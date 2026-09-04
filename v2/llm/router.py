# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— 基础模型路由器（v2/llm/router.py）

作用（stage9_plan §4 / §15）：Router 从"纯占位"变成"真入口"，进入数据流：
    Agent
      ↓ 提出业务请求（不选厂商/模型）
    Router
      ↓ 决策：这个 task_type/step 走哪个 Provider + 哪个模型（返回路由描述）
    Factory / Registry（create_provider）
      ↓
    Provider（真正执行调用）

职责边界（stage9_plan §4，一行话）：
    Router 说"用哪家哪个模型"，但**不亲自发网络请求**；
    Provider 才真正执行调用。两者不在同一层混业务。

策略立场（沿用 stage2 router.py 现注释 + stage9_plan §17 风险低）：
    本阶段**不做"按 task_type 智能选模型"**——没有实验数据支撑，不搞
    "if prompt 太长用 pro" 之类的聪明选择。Router 只做"架构入口 + 默认档"：
    一切 task_type/step 都落到"当前默认 Provider（环境开关 FILMAGENT_PROVIDER，
    缺省 mock -> 0 API）"。策略后置，等真实 token/quality 数据积累再演进。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from config import default_text_vendor, get_provider_config
from llm.providers.base import Provider


@dataclass
class ProviderRoute:
    """一份"路由描述"（stage9_plan §4）：决策结果，不携带 Provider 实例。

    Router 负责算出它，Consumer（Agent 接线脚本 / 测试）拿着它去
    Router.get_provider() 或 create_provider() 拿真正的 Provider 实例。
    """

    kind: str                 # 模态+协议标签（text/vision/...）
    vendor: str               # 供应商标签（mock/deepseek/bailian/local）
    provider_name: str        # 配置档名（如 deepseek_text / bailian_text）；mock 用 mock_text
    model: str                # 生效模型名（来自 Config；mock 时为 "mock"）


class Router:
    """task_type/step → (Provider, model) 的决策器。

    目前所有任务走默认档（架构入口先行，策略后置）。
    """

    def __init__(
        self,
        *,
        kind: str = "text",
        vendor: Optional[str] = None,
        create: Optional[Callable[..., Provider]] = None,
    ):
        """
        :param kind:    默认模态（当前业务全是 text）
        :param vendor:  默认供应商；None -> 环境开关（缺省 mock）
        :param create:  工厂函数（默认可覆盖，测试可注入 spy）
        """
        self.kind = kind or "text"
        self.vendor = vendor or default_text_vendor()
        if create is None:
            from llm.providers.registry import create_provider
            create = create_provider
        self._create = create

    # ---------------- 路由决策（不发起任何请求） ----------------
    def get_route(self, task_type: str = "") -> ProviderRoute:
        """算出一条路由描述：当前 task_type/step 该走哪个 Provider + 哪个模型。

        策略后置：本阶段任何 task_type 都落到"默认文本档"。
        未来积累数据后，可在这里按 task_type 分档
        （如 creative → 某模型、simple → 本地），并在 config 维护映射表。
        """
        if self.vendor == "mock":
            return ProviderRoute(kind=self.kind, vendor="mock",
                                 provider_name="mock_text", model="mock")
        conf = get_provider_config(f"{self.vendor}_{self.kind}")
        return ProviderRoute(
            kind=self.kind,
            vendor=self.vendor,
            provider_name=f"{self.vendor}_{self.kind}",
            model=conf.get("default_model") or "",
        )

    # ---------------- 取 Provider 实例 ----------------
    def get_provider(self, task_type: str = "", *, vendor: Optional[str] = None) -> Provider:
        """用路由决策（默认档）产出一个真实可调用的 Provider 实例。

        Provider 内部才真正执行模型调用；Router 在这里只负责"用工厂造谁"。
        """
        return self._create(self.kind, vendor=vendor or self.vendor)

    # ---------------- 向后兼容的旧接口 ----------------
    def get_model(self, task_type: str = "") -> str:
        """按任务类型返回模型名（保留旧语义：先路由再读档；mock 档返回 "mock"）。"""
        return self.get_route(task_type).model

    def model_for_step(self, step: Optional[str] = None) -> str:
        """便捷方法：按流水线步骤名取模型。同样暂不区分。"""
        return self.get_model(step or "")


__all__ = ["Router", "ProviderRoute"]
