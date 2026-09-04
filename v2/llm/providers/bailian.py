# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— 百炼（DashScope）文本 Provider（v2/llm/providers/bailian.py）

作用（stage9_plan §9）：第一个"真实厂商"接入位。DashScope 提供 OpenAI 兼容模式，
因此 BailianTextProvider 直接继承/复用 OpenAI 兼容文本实现，只补上：
    1. 固定的 vendor="bailian"（Router 识别"哪家"）；
    2. 从 config.PROVIDERS['bailian_text'] 读默认 base_url / model / api_key（不硬编码）。

Stage 9-B 原则：真实 API 调用默认 0。本文件只保证"接口落位 + 可被 factory 构造 +
可被 Router 路由到"，验证一律走 Mock。是否对百炼做最小真实冒烟（计费）由 9-C /
指令决定，且只走独立 run_real_*.py 脚本。
"""
from __future__ import annotations

from typing import Optional

from config import get_provider_config, load_provider_api_key
from llm.providers.openai_compatible import OpenAICompatibleTextProvider


class BailianTextProvider(OpenAICompatibleTextProvider):
    """百炼 DashScope 文本 Provider（OpenAI 兼容实现 + 百炼配置档）。

    真实调用前请先在 config.PROVIDERS['bailian_text'] 填 default_model
    （如 qwen-plus / qwen-max / …），并配置 BAILIAN_API_KEY 环境变量。
    本阶段构造它不会联网（网络调用只发生在 chat() 被调用时）。
    """

    vendor = "bailian"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        structured_supported: bool = False,
        **kw,
    ):
        cfg = get_provider_config("bailian_text")
        api_key = load_provider_api_key("bailian") if api_key is None else api_key
        base_url = cfg["base_url"] if base_url is None else base_url
        model = cfg["default_model"] if model is None else model
        super().__init__(
            api_key=api_key or "",
            base_url=base_url,
            model=model or "",
            vendor="bailian",
            structured_supported=structured_supported,
            **kw,
        )


__all__ = ["BailianTextProvider"]
