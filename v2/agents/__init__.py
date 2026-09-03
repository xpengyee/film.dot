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

现有实现：
    base.py                LLMAgent / LLMStep 公共骨架（阶段 8 §十六）
    casting.py             CastingAgent（继承 LLMAgent）+ CastingStep（继承 LLMStep）
    scene_generation.py    SceneGenerationAgent（继承 LLMAgent）+ SceneGenerationStep（继承 LLMStep）
未来 StoryboardAgent / ImageAgent / VideoAgent 继承 base 即可。
"""
from agents.base import LLMAgent, LLMStep
from agents.casting import CastingAgent, CastingStep
from agents.scene_generation import SceneGenerationAgent, SceneGenerationStep

__all__ = [
    "LLMAgent",
    "LLMStep",
    "CastingAgent",
    "CastingStep",
    "SceneGenerationAgent",
    "SceneGenerationStep",
]
