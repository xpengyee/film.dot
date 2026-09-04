# -*- coding: utf-8 -*-
"""
V0.2 —— LLM 调用层（v2/llm/__init__.py）

llm 依赖 core（token 日志）与 config，不依赖任何业务层。

    client    LLMClient：统一调用 / 错误家族 / 重试 / token 日志
    router    任务类型 -> 模型 的路由（当前全部走默认模型）

阶段 5 清理：补齐本 __init__.py，与 memory / context / review / workflow 的入口风格一致。
"""
from llm.client import (
    LLMError, LLMTimeoutError, LLMConnectionError, LLMRateLimitError,
    LLMInternalError, LLMRequestError, LLMAuthenticationError,
    LLMResponse, LLMClient,
)
from llm.router import Router, ProviderRoute
from llm.providers import (
    Provider, TextProvider, VisionProvider, MultimodalProvider, EmbeddingProvider,
    ImageGenerationProvider, VideoGenerationProvider, AudioProvider,
    ProviderResult, EmbeddingResult,
    MockTextProvider, MockVisionProvider, MockMultimodalProvider, MockEmbeddingProvider,
    OpenAICompatibleTextProvider, BailianTextProvider,
    create_provider, get_default_provider,
)

__all__ = [
    "LLMError", "LLMTimeoutError", "LLMConnectionError", "LLMRateLimitError",
    "LLMInternalError", "LLMRequestError", "LLMAuthenticationError",
    "LLMResponse", "LLMClient", "Router", "ProviderRoute",
    # ---- Provider 抽象与实现（Stage 9） ----
    "Provider", "TextProvider", "VisionProvider", "MultimodalProvider",
    "EmbeddingProvider",
    "ImageGenerationProvider", "VideoGenerationProvider", "AudioProvider",
    "ProviderResult", "EmbeddingResult",
    "MockTextProvider", "MockVisionProvider", "MockMultimodalProvider",
    "MockEmbeddingProvider",
    "OpenAICompatibleTextProvider", "BailianTextProvider",
    "create_provider", "get_default_provider",
]
