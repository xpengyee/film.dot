# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Workflow 流水线执行引擎（v2/workflow/）

职责边界（开工指令 §六 / §十五）：
    Workflow 是 V0.2 的"骨架"。它只负责 Step 的执行、状态记录、错误处理、
    重试与轨迹保存。它不知道任何业务（不知道剧本 / 角色 / LLM 是什么）。

    具体的业务 Agent（casting、lines_generate、verify…）在后续阶段以
    Step 子类的形式接入引擎。本阶段不实现任何 Agent。

与阶段 1 core/models.py 的关系：
    core 的 WorkflowStepState / WorkflowState 是"每集流水线步骤的持久化状态"
    （数据模型，只记文件名与 approved_output）；
    本模块是"一次运行的过程记录"（行为模型，记每次 attempt 与 artifact）。
    两者通过 models.to_core_workflow_state() 桥接，供后续阶段持久化。

与阶段 2 LLM 层的关系：
    Workflow 不统计 token / cost（那是 LLMClient 的职责，见 token_log.py）。
    本阶段测试全部用纯本地 FakeStep，真实 LLM 调用 0 次。

阶段 4 扩展：
    StepStatus 增加 APPROVED_WITH_EDIT / REJECTED；
    Step 增加 needs（声明式需求，交 ContextAssembler）与 needs_review（人工审核）；
    WorkflowEngine 增加 review_handler（人工审核节点，见 engine.py）。
    人工审核实现见 v2/review/，Memory / ContextAssembler 见 v2/memory/、v2/context/。
"""
from workflow.errors import WorkflowError, StepError, ArtifactError, RetryError
from workflow.models import (
    now_iso,
    new_run_id,
    StepStatus,
    ReviewDecision,
    StepOutput,
    AttemptResult,
    StepResult,
    WorkflowContext,
    WorkflowRun,
    to_core_workflow_state,
)
from workflow.step import Step
from workflow.artifact import Artifact, ArtifactStore
from workflow.engine import WorkflowEngine

__all__ = [
    "WorkflowError",
    "StepError",
    "ArtifactError",
    "RetryError",
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
    "Step",
    "Artifact",
    "ArtifactStore",
    "WorkflowEngine",
]
