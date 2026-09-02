# -*- coding: utf-8 -*-
"""
V0.2 阶段 3/4 —— Workflow 线性执行引擎（v2/workflow/engine.py）

作用：把一组 Step 按顺序执行，负责：
    执行 / 记录 / 错误处理 / 状态更新 / 重试 / 轨迹保存（开工指令 §七）。

阶段 4 扩展（指令 §十四 / §十七）：
    Step 声明 needs_review=True 且 run+validate 通过后，进入人工审核节点：
        approved            -> APPROVED，run 继续
        approved_with_edit  -> 写"编辑后的新版本"产物（原文件保留），APPROVED_WITH_EDIT
        rejected            -> REJECTED，run 失败即停
    审核处理器 review_handler 由调用方注入（ReviewService / 测试 handler）。
    原有状态与测试（阶段 3 38/38）不受影响：is_success 扩展覆盖 APPROVED 家族状态。


引擎不知道任何业务。它只关心：
    Step 现在是什么状态？这一步要不要重试？产物写到哪？历史版本保没保？
    具体"写什么剧本、怎么生成"是 Step 自己的事（未来 Agent）。

一次 run 的规则（架构报告 §10.1 线性执行器）：
    按顺序执行 Step；某个 Step 失败（重试耗尽）则整个 run 立即失败停止，
    不再执行后续 Step（因为后续依赖它的产出）。

每次尝试（attempt）的流程：
    run() 产出 -> 写版本化 artifact（无论质量如何都先落盘保留）
    -> validate() 校验
        - 无错误 -> 本次尝试 SUCCESS，approved 该版本
        - 有错误 / 抛异常 -> 本次尝试 FAILED（文件保留）
            - 错误可重试且没到上限 -> RETRYING，等退避，进入下一次尝试
            - 否则 -> Step FAILED，run 失败

绝对不做（开工指令 §十三）：
    while retry_count < 3:
        try: step.run(); break
        except: retry_count += 1
    那种写法无法回答"这次尝试的文件是哪一版？历史还在吗？最终用了哪一版？"
    本引擎把每次尝试都记成 AttemptResult + Artifact，轨迹全程可查。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

from workflow.artifact import Artifact, ArtifactStore
from workflow.errors import WorkflowError, StepError, RetryError
from workflow.models import (
    StepStatus,
    ReviewDecision,
    StepOutput,
    AttemptResult,
    StepResult,
    WorkflowContext,
    WorkflowRun,
    now_iso,
    new_run_id,
)
from workflow.step import Step


class WorkflowEngine:
    """线性 Workflow 执行引擎。

    :param steps:            按顺序执行的 Step 列表
    :param artifacts_dir:    Step 产出文件（artifact）的根目录
    :param run_log_dir:      可选；若指定，每次 run 结束会保存一份
                             <run_id>.json 运行记录（WorkflowRun.to_dict）
    :param retry_backoff_seconds: 重试间隔秒数（测试设 0，生产可设 1~2）
    """

    def __init__(
        self,
        steps: Iterable[Step],
        *,
        artifacts_dir: Path,
        run_log_dir: Optional[Path] = None,
        retry_backoff_seconds: float = 0.0,
        review_handler: Optional[Callable[[Artifact, Any], Any]] = None,
    ):
        self.steps: List[Step] = list(steps)
        self.artifacts_dir = Path(artifacts_dir)
        self.store = ArtifactStore(self.artifacts_dir)
        self.run_log_dir = Path(run_log_dir) if run_log_dir else None
        self.retry_backoff_seconds = retry_backoff_seconds
        # 阶段 4：人工审核处理器。签名 review_handler(artifact, context) -> ReviewResult。
        # 返回的对象需要 .decision（ReviewDecision 之一）与 .review_id；
        # approved_with_edit 时还需 .edited_content。不配置时，任何 needs_review Step 会报错。
        self.review_handler = review_handler

    # ---------------- 对外主接口 ----------------
    def run(
        self,
        *,
        project_id: str,
        workflow_id: str,
        inputs: Optional[dict] = None,
        max_attempts: int = 3,
        resume_from: Optional[WorkflowRun] = None,
    ) -> WorkflowRun:
        """执行一次 Workflow。

        :param max_attempts:  每个 Step 最多尝试几次（含首次）。默认 3。
        :param resume_from:   上一次的 WorkflowRun；提供后，其中已 SUCCESS 的 Step
                              本次跳过（SKIPPED），只重新执行未成功 / 未执行的部分。
        """
        context = WorkflowContext(
            project_id=project_id,
            workflow_id=workflow_id,
            run_id=new_run_id(),
            status="running",
            inputs=dict(inputs or {}),
            created_at=now_iso(),
        )
        run = WorkflowRun(
            run_id=context.run_id,
            project_id=project_id,
            workflow_id=workflow_id,
            status="running",
            started_at=now_iso(),
        )

        for step in self.steps:
            if not step.step_id:
                step.step_id = step.name or type(step).__name__

            # —— resume：上游已成功的 Step 不再重跑 ——
            if resume_from is not None:
                prev = resume_from.step_result(step.step_id)
                if prev is not None and prev.is_success:
                    run.steps[step.step_id] = StepResult(
                        step_id=step.step_id,
                        name=step.name or step.step_id,
                        status=StepStatus.SKIPPED,
                        attempts=[],
                        approved_artifact=prev.approved_artifact,
                    )
                    continue

            result = self._run_step(context, step, max_attempts)
            run.steps[step.step_id] = result
            if not result.is_success:  # 含 FAILED / REJECTED：失败即停
                run.status = "failed"
                context.status = "failed"
                break
        else:
            run.status = "success"
            context.status = "success"

        run.ended_at = now_iso()
        context.updated_at = now_iso()

        # —— 可选：保存 run 记录（历史可恢复 / 可读取） ——
        if self.run_log_dir is not None:
            path = self.run_log_dir / f"{run.run_id}.json"
            self.run_log_dir.mkdir(parents=True, exist_ok=True)
            from core.io import write_json
            write_json(path, run.to_dict())

        return run

    # ---------------- 单 Step 执行（含重试） ----------------
    def _run_step(self, context: WorkflowContext, step: Step, max_attempts: int) -> StepResult:
        result = StepResult(
            step_id=step.step_id,
            name=step.name or step.step_id,
            status=StepStatus.RUNNING,
            started_at=now_iso(),
        )
        last_error: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            att = AttemptResult(attempt=attempt, status=StepStatus.FAILED, started_at=now_iso())
            retryable = step.retryable

            try:
                output = step.run(context)
                if output is None:
                    output = StepOutput(data=None, artifact_name=step.step_id)
                if not output.artifact_name:
                    output.artifact_name = step.step_id

                # 1) 先落盘（无论质量如何，版本文件都保留 —— 轨迹不可丢）
                artifact = self.store.save(
                    step_id=step.step_id,
                    attempt=attempt,
                    name=output.artifact_name,
                    data=output.data,
                    type=output.artifact_type,
                    metadata=output.metadata,
                )
                att.artifact = artifact

                # 2) 再校验质量
                errors = list(step.validate(output.data, context) or [])
                if not errors:
                    # 2a) Step 声明需要人工审核 -> 走审核节点（阶段 4）
                    if step.needs_review:
                        return self._handle_review(context, step, output, artifact, att, result)
                    # 2b) 不需要审核 -> 直接成功
                    att.status = StepStatus.SUCCESS
                    result.attempts.append(self._finish_attempt(att))
                    result.status = StepStatus.SUCCESS
                    result.approved_artifact = artifact
                    artifact.approved = True
                    context.set_output(step.step_id, output.data, artifact)
                    return self._finish_step(result)

                # 质量不合格：文件已保留，是否值得再试
                att.errors = errors
                last_error = "; ".join(errors)

            except RetryError:
                raise  # 重试机制自身错误（如审核未配置 handler）：直接向调用方报告，不当普通失败吞掉
            except WorkflowError as exc:
                att.error = f"{type(exc).__name__}: {exc}"
                last_error = att.error
                retryable = bool(exc.retryable) and step.retryable
            except Exception as exc:  # 未知异常：包成 StepError 再记录，不吞掉
                wrapped = StepError(f"step {step.step_id} 抛出未预期异常: {exc!r}")
                att.error = f"{type(exc).__name__}: {exc}"
                last_error = att.error
                retryable = step.retryable

            result.attempts.append(self._finish_attempt(att))
            result.status = StepStatus.RETRYING

            if attempt >= max_attempts or not retryable:
                # 重试耗尽 / 不可重试 -> FAILED
                result.status = StepStatus.FAILED
                result.error = last_error
                return self._finish_step(result)

            if self.retry_backoff_seconds > 0:
                time.sleep(self.retry_backoff_seconds)

        # 防御性兜底（正常情况下到不了这里）
        result.status = StepStatus.FAILED
        result.error = last_error or "重试次数耗尽"
        return self._finish_step(result)

    # ---------------- 人工审核节点（阶段 4） ----------------
    def _handle_review(
        self,
        context: WorkflowContext,
        step: Step,
        output: StepOutput,
        artifact: Artifact,
        att: AttemptResult,
        result: StepResult,
    ) -> StepResult:
        """Step 成功但声明 needs_review=True：交给 review_handler 走人工审核。

        审核决策（review.decision，与 review.models.ReviewDecision 对齐）：
            approved            -> 采用原始产物，Step 状态 APPROVED，run 继续
            approved_with_edit  -> 用"编辑后的新内容"写一个新版本产物（原文件保留），
                                   采用新版本，状态 APPROVED_WITH_EDIT，run 继续
            rejected            -> Step 状态 REJECTED，run 失败即停
        """
        if self.review_handler is None:
            raise RetryError(
                f"Step {step.step_id} 声明 needs_review=True，但引擎未配置 review_handler"
            )

        result.status = StepStatus.NEEDS_REVIEW
        review = self.review_handler(artifact, context)
        decision = str(getattr(review, "decision", ""))
        review_id = str(getattr(review, "review_id", ""))
        result.review_id = review_id or None

        if decision == ReviewDecision.APPROVED:
            # 原始产出直接通过
            att.status = StepStatus.SUCCESS
            result.attempts.append(self._finish_attempt(att))
            result.approved_artifact = artifact
            artifact.approved = True
            result.status = StepStatus.APPROVED
            context.set_output(step.step_id, output.data, artifact)
            return self._finish_step(result)

        if decision == ReviewDecision.APPROVED_WITH_EDIT:
            # 人工修改后通过：原始文件绝不覆盖（ArtifactStore 版本化保证）
            edited = getattr(review, "edited_content", None)
            if edited is None:
                raise RetryError(
                    f"approved_with_edit 但 edited_content 为空（review={review_id}）"
                )
            edited_art_dict = getattr(review, "edited_artifact", None)
            if edited_art_dict is not None:
                # 审核层（ReviewService）已把编辑产物写盘：直接采用，绝不重复写盘
                new_artifact = Artifact.from_dict(edited_art_dict)
            else:
                # 兜底：自定义 handler 只给内容、没写盘时，由引擎写"编辑后的新版本"
                new_artifact = self.store.save(
                    step_id=step.step_id,
                    attempt=0,  # 0 = 非引擎尝试（人工编辑产物）
                    name=output.artifact_name,
                    data=edited,
                    type=output.artifact_type,
                    metadata={**(output.metadata or {}), "review_id": review_id, "edited": True},
                )
            att.status = StepStatus.SUCCESS
            result.attempts.append(self._finish_attempt(att))
            result.approved_artifact = new_artifact
            result.status = StepStatus.APPROVED_WITH_EDIT
            context.set_output(step.step_id, edited, new_artifact)
            return self._finish_step(result)

        if decision == ReviewDecision.REJECTED:
            # 当前产出不能采用：流程停止
            reason = getattr(review, "comment", "") or getattr(review, "reason", "")
            att.status = StepStatus.FAILED
            att.error = f"人工审核拒绝（review={review_id}）: {reason}"
            result.attempts.append(self._finish_attempt(att))
            result.status = StepStatus.REJECTED
            result.error = att.error
            return self._finish_step(result)

        raise RetryError(f"未知审核决策: {decision!r}")

    # ---------------- 小工具 ----------------
    @staticmethod
    def _finish_attempt(att: AttemptResult) -> AttemptResult:
        att.ended_at = now_iso()
        return att

    @staticmethod
    def _finish_step(result: StepResult) -> StepResult:
        result.ended_at = now_iso()
        return result


__all__ = ["WorkflowEngine"]
