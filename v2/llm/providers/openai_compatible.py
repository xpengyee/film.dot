# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— OpenAI 兼容文本 Provider（v2/llm/providers/openai_compatible.py）

作用（stage9_plan §6.1 / §15）：任何"兼容 OpenAI /chat/completions 接口"的文本模型
（DeepSeek、百炼 DashScope 兼容模式、未来本地 vLLM / Ollama / llama.cpp……）都可以
通过这一实现接入——厂商差异收敛成 Config（base_url / model / api_key / pricing），
而不是"每接一家就新写一个类"。

实现策略：直接复用 Stage2 的 LLMClient 作为执行器（LLMClient 本身就是
"OpenAI 兼容文本执行器"，其能力是本接口的超集）。本类只做两件事：
    1. 补齐 Provider 元信息（kind/vendor/model）与"是 TextProvider"的类型身份；
    2. chat() 增补 structured 透传位（默认不送后端，见下），并把 LLMResponse
       统一包成 ProviderResult 返回。
不重写第二遍 retry / token 日志 / 错误翻译——全部走 LLMClient 现成机制。

structured 说明（stage9_plan §6.1 / §20 约定）：
    - 契约上 Provider 尽量把 structured 透传给后端 json 模式；
    - 但"后端到底支不支持"只有真实联调才知道，为避免给不支持的后端送一个
      它不认识的字段导致请求失败，本阶段默认 structured_supported=False，
      即：接受 structured 参数但不发送（后端未接入时静默忽略）。
    - 未来真实联调（9-C）确认某个后端支持后，用 structured_supported=True
      显式打开透传即可，业务代码零改动。
"""
from __future__ import annotations

from typing import List, Optional

from llm.client import LLMClient
from llm.providers.base import ProviderResult, TextProvider


class OpenAICompatibleTextProvider(LLMClient, TextProvider):
    """OpenAI 兼容文本执行器 + TextProvider 抽象身份。

    构造参数与 LLMClient 完全一致（向后兼容：存量接线代码拿它替换 LLMClient
    即可）；额外可选 vendor / structured_supported / temperature / max_tokens。
    业务 Agent 只需要它"是个能 chat 的 TextProvider"，不需要知道厂商细节。
    """

    vendor = "openai-compatible"     # 子类/工厂会覆盖为具体厂商（deepseek/bailian/local…）
    kind = "text"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "deepseek-chat",
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        logger: Optional[object] = None,
        transport=None,
        vendor: Optional[str] = None,
        structured_supported: bool = False,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            logger=logger,
            transport=transport,
        )
        if vendor is not None:
            self.vendor = vendor
        self.provider_name = f"{self.vendor}_{self.kind}"
        # 结构化透传开关：默认关闭（不向后端送未经验证的字段），见模块 docstring。
        self.structured_supported = bool(structured_supported)

    # ---------------- 文本对话 ----------------
    def chat(
        self,
        messages: List[dict],
        *,
        step: str = "",
        agent: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        structured: Optional[dict] = None,
    ) -> ProviderResult:
        """OpenAI 兼容文本对话。返回值统一包成 ProviderResult（可再转 LLMResponse）。"""
        resp = LLMClient.chat(
            self, messages,
            step=step, agent=agent,
            temperature=temperature, max_tokens=max_tokens,
            structured=structured,
        )
        return ProviderResult.from_llm_response(resp)


__all__ = ["OpenAICompatibleTextProvider"]
