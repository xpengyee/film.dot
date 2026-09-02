# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Workflow 运行时模型（v2/workflow/models.py）

作用：定义 Workflow 引擎运行时的数据形态（区别于阶段 1 core/models.py 的"数据模型"）。

分工：
    core/models.py 的 WorkflowStepState / WorkflowState 是"每集流水线步骤的持久化状态"
    （数据模型，阶段 1 已定，只记录 文件名 / approved_output）。
    本文件的 StepResult / WorkflowRun / WorkflowContext 是"一次运行的过程记录"
    （行为模型，记录 每次 attempt / 状态流转 / artifact 引用 / 错误）。

    两者通过本文件的 to_core_workflow_state() 桥接：
    引擎跑完一轮 run 后，可折叠成 core.WorkflowState，供后续阶段（Project 层）持久化。

状态机（StepStatus，对应架构报告 §10.2 + 开工指令 §七）：
    PENDING    还没轮到执行
    RUNNING    正在执行
    SUCCESS    成功（approved 了一个 artifact）
    FAILED     失败（不可重试 / 重试耗尽）
    RETRYING   失败后等待下一次重试
    SKIPPED    本次 run 被跳过（例如 resume 模式下上游已成功）
    NEEDS_REVIEW / APPROVED  架构报告 10.2 定义的人工审核状态。
    APPROVED_WITH_EDIT  人工修改后批准（阶段 4 新增；产物换成编辑后的新版本）
    REJECTED            人工拒绝（阶段 4 新增；流程停止）

artifact 版本命名（对应开工指令 §十 / §十一，第四十七章问题三方案 A）：
    同一个 Step 的第 1 次尝试   -> scenes_5.json
    第 2 次尝试                 -> scenes_5_v2.json
    第 3 次尝试                 -> scenes_5_v3.json
    历史版本全部保留，绝不覆盖。
    version 由 ArtifactStore 按磁盘上已存在的版本数 +1 决定（见 artifact.py），
    因此"单次 run 内重试"与"整个 workflow 重新运行"都能保持不覆盖。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from core.models import WorkflowState, WorkflowStepState


def now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（毫秒精度），用于记录时间戳。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_run_id() -> str:
    """生成一个短 run 标识（12 位 hex）。"""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Step 状态常量
# ---------------------------------------------------------------------------
class StepStatus:
    """Step 生命周期状态（字符串常量，便于序列化 / 比对）。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    SKIPPED = "SKIPPED"
    NEEDS_REVIEW = "NEEDS_REVIEW"   # 成功但需要人工审核
    APPROVED = "APPROVED"           # 人工审核通过（采用原始产出）
    APPROVED_WITH_EDIT = "APPROVED_WITH_EDIT"  # 人工修改后通过（采用编辑后的新产物）
    REJECTED = "REJECTED"           # 人工拒绝（流程停止）


# ---------------------------------------------------------------------------
# 人工审核决策常量（阶段 4）
# ---------------------------------------------------------------------------
class ReviewDecision:
    """人工审核决策（Workflow 引擎按它决定"继续 / 停止"）。

    定义在 workflow 层（而非 review 层），保证模块边界是
        core -> ... -> workflow -> review
    即 review 依赖 workflow、workflow 不依赖 review（阶段 5 清理确认）。
    review/models.py 通过 `from workflow.models import ReviewDecision` 复用同一常量。
    """

    APPROVED = "approved"                   # 原始产出直接通过，run 继续
    APPROVED_WITH_EDIT = "approved_with_edit"  # 人工修改后通过，run 继续
    REJECTED = "rejected"                   # 人工拒绝，run 失败即停


# ---------------------------------------------------------------------------
# Step.run() 的返回值
# ---------------------------------------------------------------------------
@dataclass
class StepOutput:
    """Step.run() 返回给引擎的产出。引擎负责把它写成版本化 artifact。

    artifact_name：产出文件名基础名，例如 "scenes_5"。
                   引擎会自动生成 scenes_5.json / scenes_5_v2.json / ...
    """
    data: Any = None
    artifact_name: str = ""
    artifact_type: str = "json"     # "json" | "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 一次尝试的结果
# ---------------------------------------------------------------------------
@dataclass
class AttemptResult:
    """一个 Step 的某一次尝试（attempt）。

    attempt   本次 run 内的第几次尝试（从 1 开始）。
    status    SUCCESS / FAILED。
    artifact  本次尝试落盘的文件（失败的那次也保留文件，轨迹不丢）。
    errors    validate() 返回的质量错误列表（有内容 = 质量不合格）。
    error     异常型失败的错误消息（run() 抛异常时）。
    """
    attempt: int
    status: str
    artifact: Optional["Artifact"] = None
    errors: List[str] = field(default_factory=list)
    error: Optional[str] = None
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0

    @property
    def is_success(self) -> bool:
        return self.status == StepStatus.SUCCESS

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "artifact": self.artifact.to_dict() if self.artifact else None,
            "errors": list(self.errors),
            "error": self.error,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AttemptResult":
        from workflow.artifact import Artifact  # 延迟导入避免循环
        art = data.get("artifact")
        return cls(
            attempt=int(data["attempt"]),
            status=str(data["status"]),
            artifact=Artifact.from_dict(art) if art else None,
            errors=list(data.get("errors") or []),
            error=data.get("error"),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            duration_ms=int(data.get("duration_ms", 0)),
        )


# ---------------------------------------------------------------------------
# 一个 Step 的最终结果
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    """一个 Step 在本次 run 中的完整过程与结论。"""

    step_id: str
    name: str = ""
    status: str = StepStatus.PENDING
    attempts: List[AttemptResult] = field(default_factory=list)
    approved_artifact: Optional["Artifact"] = None
    error: Optional[str] = None
    review_id: Optional[str] = None   # 阶段 4：经人工审核的 Step 记录审核 ID（可追踪）
    started_at: str = ""
    ended_at: str = ""
    duration_ms: int = 0

    # 视为"成功"的状态：直接成功，或审核通过（含修改后通过）
    _SUCCEEDED_STATES = (StepStatus.SUCCESS, StepStatus.APPROVED, StepStatus.APPROVED_WITH_EDIT)

    @property
    def is_success(self) -> bool:
        return self.status in self._SUCCEEDED_STATES

    def attempt(self, n: int) -> Optional[AttemptResult]:
        """返回第 n 次尝试的结果；不存在返回 None。"""
        for a in self.attempts:
            if a.attempt == n:
                return a
        return None

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "name": self.name,
            "status": self.status,
            "attempts": [a.to_dict() for a in self.attempts],
            "approved_artifact": self.approved_artifact.to_dict() if self.approved_artifact else None,
            "error": self.error,
            "review_id": self.review_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepResult":
        from workflow.artifact import Artifact  # 延迟导入避免循环
        aa = data.get("approved_artifact")
        return cls(
            step_id=str(data["step_id"]),
            name=str(data.get("name", "")),
            status=str(data.get("status", StepStatus.PENDING)),
            attempts=[AttemptResult.from_dict(a) for a in data.get("attempts", [])],
            approved_artifact=Artifact.from_dict(aa) if aa else None,
            error=data.get("error"),
            review_id=data.get("review_id"),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            duration_ms=int(data.get("duration_ms", 0)),
        )


# ---------------------------------------------------------------------------
# 上下文：Step 之间传信息
# ---------------------------------------------------------------------------
@dataclass
class WorkflowContext:
    """Step 之间共享的上下文（开工指令 §八）。

    只保存"运行需要的最小信息"：项目/工作流/run 标识、输入、已成功 Step 的输出与
    artifact 引用、运行元数据。不硬编码整个项目的数据（角色/场景等业务数据放
    step_outputs / inputs，由 Step 自己决定存取）。
    """
    project_id: str
    workflow_id: str
    run_id: str
    status: str = "pending"                     # pending | running | success | failed
    inputs: Dict[str, Any] = field(default_factory=dict)
    step_outputs: Dict[str, Any] = field(default_factory=dict)    # step_id -> 已批准的产出数据
    artifacts: Dict[str, Any] = field(default_factory=dict)       # step_id -> artifact(dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def get_output(self, step_id: str, default: Any = None) -> Any:
        """取上游某 Step 已批准的产出；没有则返回 default。"""
        return self.step_outputs.get(step_id, default)

    def set_output(self, step_id: str, data: Any, artifact: Optional["Artifact"] = None) -> None:
        """记录某 Step 的产出（成功时由引擎调用）。"""
        self.step_outputs[step_id] = data
        if artifact is not None:
            self.artifacts[step_id] = artifact.to_dict()

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "inputs": self.inputs,
            "step_outputs": self.step_outputs,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowContext":
        return cls(
            project_id=str(data["project_id"]),
            workflow_id=str(data["workflow_id"]),
            run_id=str(data["run_id"]),
            status=str(data.get("status", "pending")),
            inputs=dict(data.get("inputs") or {}),
            step_outputs=dict(data.get("step_outputs") or {}),
            artifacts=dict(data.get("artifacts") or {}),
            metadata=dict(data.get("metadata") or {}),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


# ---------------------------------------------------------------------------
# 一次 Workflow 运行
# ---------------------------------------------------------------------------
@dataclass
class WorkflowRun:
    """一次完整 run 的结论：每个 step 的 StepResult + 整体状态。"""

    run_id: str
    project_id: str
    workflow_id: str
    status: str = "running"                     # running | success | failed
    steps: Dict[str, StepResult] = field(default_factory=dict)   # step_id -> StepResult
    started_at: str = ""
    ended_at: str = ""

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    def step_result(self, step_id: str) -> Optional[StepResult]:
        return self.steps.get(step_id)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "project_id": self.project_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "steps": {sid: res.to_dict() for sid, res in self.steps.items()},
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowRun":
        return cls(
            run_id=str(data["run_id"]),
            project_id=str(data["project_id"]),
            workflow_id=str(data["workflow_id"]),
            status=str(data.get("status", "running")),
            steps={sid: StepResult.from_dict(res) for sid, res in data.get("steps", {}).items()},
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
        )


# ---------------------------------------------------------------------------
# 桥接：WorkflowRun -> core.WorkflowState（阶段 1 的持久化数据模型）
# ---------------------------------------------------------------------------
def to_core_workflow_state(run: WorkflowRun) -> WorkflowState:
    """把一次 run 的结论折叠成 core.models.WorkflowState。

    映射关系：
        SUCCESS / APPROVED / APPROVED_WITH_EDIT  -> status="passed"
                                                      approved_output = 采用版本的文件名
                                                      （审核后修改通过的，就是编辑后的新版本）
        FAILED / REJECTED                        -> status="failed"
        SKIPPED / NEEDS_REVIEW / PENDING         -> status="pending"（本次未通过，不算完成）
        attempts -> 每个有 artifact 的尝试都 record_attempt（历史文件名全保留）
    """
    state = WorkflowState(version="1")
    for step_id in run.steps:
        res = run.steps[step_id]
        ws = WorkflowStepState(step=step_id)
        if res.status in (StepStatus.SUCCESS, StepStatus.APPROVED, StepStatus.APPROVED_WITH_EDIT):
            ws.status = "passed"
        elif res.status in (StepStatus.FAILED, StepStatus.REJECTED):
            ws.status = "failed"
        else:
            ws.status = "pending"  # SKIPPED / NEEDS_REVIEW / PENDING 视为未完成
        for att in res.attempts:
            if att.artifact is not None:
                ws.record_attempt(att.artifact.filename)
        if res.approved_artifact is not None:
            ws.approve(res.approved_artifact.filename)
        state.steps.append(ws)
    return state


__all__ = [
    "now_iso",
    "new_run_id",
    "StepStatus",
    "ReviewDecision",
    "StepOutput",
    "AttemptResult",
    "StepResult",
    "WorkflowContext",
    "WorkflowRun",
    "to_core_workflow_state",
]
