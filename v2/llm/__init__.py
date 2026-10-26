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
from llm.request_counter import (
    MAX_REAL_VISION_REQUESTS, MAX_REAL_IMAGE_REQUESTS,
    MAX_REAL_VIDEO_SUBMISSIONS, APIBudgetExceededError,
    APIRequestCounter, CountingProviderWrapper,
    get_api_counter, set_api_counter, reset_api_counter,
)
from llm.providers import (
    Provider, TextProvider, VisionProvider, MultimodalProvider, EmbeddingProvider,
    ImageGenerationProvider, VideoGenerationProvider, AudioProvider,
    ProviderResult, VideoTaskResult, EmbeddingResult,
    MockTextProvider, MockVisionProvider, MockMultimodalProvider, MockEmbeddingProvider,
    OpenAICompatibleTextProvider, BailianTextProvider,
    OpenAICompatibleVisionProvider, OpenAICompatibleMultimodalProvider,
    MockImageGenerationProvider, OpenAICompatibleImageProvider, ImageGenerationError,
    MockVideoProvider, BailianVideoProvider, VideoGenerationError,
    create_provider, get_default_provider,
)

__all__ = [
    "LLMError", "LLMTimeoutError", "LLMConnectionError", "LLMRateLimitError",
    "LLMInternalError", "LLMRequestError", "LLMAuthenticationError",
    "LLMResponse", "LLMClient", "Router", "ProviderRoute",
    # ---- API 请求计数 / 预算硬上限（Stage 10-B / Stage 13 / Stage 15） ----
    "MAX_REAL_VISION_REQUESTS", "MAX_REAL_IMAGE_REQUESTS",
    "MAX_REAL_VIDEO_SUBMISSIONS", "APIBudgetExceededError",
    "APIRequestCounter", "CountingProviderWrapper",
    "get_api_counter", "set_api_counter", "reset_api_counter",
    # ---- Provider 抽象与实现（Stage 9 / 10-B / 13 / 15） ----
    "Provider", "TextProvider", "VisionProvider", "MultimodalProvider",
    "EmbeddingProvider",
    "ImageGenerationProvider", "VideoGenerationProvider", "AudioProvider",
    "ProviderResult", "VideoTaskResult", "EmbeddingResult",
    "MockTextProvider", "MockVisionProvider", "MockMultimodalProvider",
    "MockEmbeddingProvider",
    "OpenAICompatibleTextProvider", "BailianTextProvider",
    "OpenAICompatibleVisionProvider", "OpenAICompatibleMultimodalProvider",
    "MockImageGenerationProvider", "OpenAICompatibleImageProvider",
    "ImageGenerationError",
    "MockVideoProvider", "BailianVideoProvider", "VideoGenerationError",
    "create_provider", "get_default_provider",
]
