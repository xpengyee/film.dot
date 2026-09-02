# -*- coding: utf-8 -*-
"""
V0.2 阶段 6 —— 业务 Agent 层（v2/agents/）

职责边界（AGENTS.md / DEVELOPMENT.md）：
    Agent 是"完成一个明确业务任务的执行者"，处于依赖链最顶层：

        core → (llm | memory | context) → workflow → review → agents

    Agent 只做业务，不碰基础设施：
        不读文件 / 不找 API key / 不调 requests / 不生成文件名 /
        不改 Memory / 不决定 Workflow 下一步。
    输入来自 ContextAssembler（needs 声明制），LLM 走 LLMClient，
    产出交给 Workflow（Artifact 版本化 + Review 人工审核）。

本阶段实现：
    casting.py             CastingAgent（业务核心）+ CastingStep（Workflow Step 壳）
    scene_generation.py    SceneGenerationAgent（业务核心）+ SceneGenerationStep（Workflow Step 壳）
"""
from agents.casting import CastingAgent, CastingStep
from agents.scene_generation import SceneGenerationAgent, SceneGenerationStep

__all__ = [
    "CastingAgent",
    "CastingStep",
    "SceneGenerationAgent",
    "SceneGenerationStep",
]
