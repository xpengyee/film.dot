# -*- coding: utf-8 -*-
"""
V0.2 阶段 2 —— 基础模型路由器（v2/llm/router.py）

作用：按"任务类型"决定用哪个模型。当前只是接口占位。

重要（开工指令 §二十六/§二十七）：
    本阶段**不实现自动模型路由**。
    当前所有任务统一返回默认模型 deepseek-chat。
    不做 if prompt_length > xxx: use pro 之类的"智能选择"，
    也不让 LLM 自己判断该用哪个模型——
    因为目前没有实验数据支持，等后续阶段积累真实 token / quality 数据再设计。

    所以 Router 现在是"一张只回同一个答案的表"，先把接口立住。
"""
from __future__ import annotations

from typing import Optional


class Router:
    """任务类型 → 模型 的映射。目前全部任务走默认模型。"""

    def __init__(self, default_model: str = "deepseek-chat"):
        self.default_model = default_model

    def get_model(self, task_type: str) -> str:
        """按任务类型返回模型名。

        目前（接口先行、策略后置）任何 task_type 都返回 default_model。
        未来积累数据后，可以在这里按 task_type 分模型
        （如 creative → pro、simple → flash），并在 config 里维护映射表。
        """
        return self.default_model

    def model_for_step(self, step: Optional[str] = None) -> str:
        """便捷方法：按流水线步骤名取模型。同样暂不区分。"""
        return self.default_model


__all__ = ["Router"]
