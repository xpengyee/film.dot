# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Step 抽象（v2/workflow/step.py）

作用：定义"一步工作"的通用插槽。未来所有 Agent（casting、lines_generate、verify…）
都实现成 Step 的子类，塞进 WorkflowEngine 跑。

Step 不应该包含业务逻辑本身，只负责表达：
    1. 它叫什么（step_id / name）
    2. 它允不允许重试（retryable）
    3. 它怎么执行（run：产出数据）
    4. 产出合不合格（validate：返回错误列表，空列表=通过）
    5. 它需要哪些信息（needs：声明式清单，交给 ContextAssembler 照单取货）
    6. 它要不要人工审核（needs_review：成功后进入审核节点，阶段 4 启用）

使用方式（开工指令 §七）：
    class MyStep(Step):
        def run(self, context):
            # 读 context.get_output("上游step")，算点什么
            return StepOutput(data={...}, artifact_name="scenes_5")

        def validate(self, data, context):
            errors = []
            if not data:
                errors.append("产出为空")
            return errors

引擎的责任（执行 / 记录 / 重试 / 状态 / 轨迹）全部在 engine.py，不在 Step 里。

重要边界：
    - Step 不直接碰磁盘：产出交给引擎写 Artifact（版本化、不覆盖）。
    - Step 不碰 token 日志：那是 LLMClient 的职责。
    - run() 里可以调用 LLMClient（未来 Agent 会这样做），但本阶段的测试
      全部用纯本地 FakeStep，不调真实 LLM。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from workflow.models import StepOutput


class Step:
    """Step 基类。子类必须实现 run()，可选择性覆盖 validate()。"""

    # 实例属性在 __init__ 里赋值，允许每个实例有不同的 id/名称/重试策略
    step_id: str = ""
    name: str = ""
    retryable: bool = True
    needs: List[str] = []          # 声明式需求清单（阶段 4）：交给 ContextAssembler
    needs_review: bool = False     # 阶段 4：成功后进入人工审核节点

    def __init__(
        self,
        step_id: Optional[str] = None,
        name: Optional[str] = None,
        retryable: Optional[bool] = None,
        needs: Optional[List[str]] = None,
        needs_review: Optional[bool] = None,
    ):
        if step_id is not None:
            self.step_id = step_id
        if name is not None:
            self.name = name
        if retryable is not None:
            self.retryable = retryable
        if needs is not None:
            self.needs = list(needs)
        if needs_review is not None:
            self.needs_review = bool(needs_review)
        if not self.name:
            self.name = self.step_id

    # ---------------- 必须实现 ----------------
    def run(self, context) -> StepOutput:
        """执行这一步，返回要落盘的产出。

        :param context: WorkflowContext —— 可通过 context.get_output("上游step") 取上游产出
        :return: StepOutput（data + artifact_name 等）
        :raises StepError: 执行失败（引擎会按 retryable 决定是否重试）
        """
        raise NotImplementedError(f"{type(self).__name__} must implement run()")

    # ---------------- 可选覆盖 ----------------
    def validate(self, data: Any, context) -> List[str]:
        """校验本次产出是否合格。返回错误列表，空列表表示通过。

        这是"产出已写盘、但质量不合格"的失败路径：
        引擎会保留本次落盘的版本文件（轨迹），再决定是否重试。
        默认不校验（返回空列表）。
        """
        return []


__all__ = ["Step"]
