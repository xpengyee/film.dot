# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— Provider 层统一出口（v2/llm/providers/__init__.py）

对外只暴露抽象接口 / 结果 / Mock / 真实实现 / 工厂入口，
业务代码 import 从本包拿，不直接钻到子模块。
"""
from llm.providers.base import (
    Provider,
    ProviderResult,
    EmbeddingResult,
    TextProvider,
    VisionProvider,
    MultimodalProvider,
    EmbeddingProvider,
    ImageGenerationProvider,
    VideoGenerationProvider,
    AudioProvider,
    GENERATION_KINDS,
)
from llm.providers.mock import (
    MockTextProvider,
    MockVisionProvider,
    MockMultimodalProvider,
    MockEmbeddingProvider,
)
from llm.providers.openai_compatible import OpenAICompatibleTextProvider
from llm.providers.openai_compatible_vision import (
    OpenAICompatibleVisionProvider,
    OpenAICompatibleMultimodalProvider,
)
from llm.providers.bailian import BailianTextProvider
from llm.providers.registry import (
    create_provider,
    get_default_provider,
    provider_kind_vendor,
)

__all__ = [
    "Provider",
    "ProviderResult",
    "EmbeddingResult",
    "TextProvider",
    "VisionProvider",
    "MultimodalProvider",
    "EmbeddingProvider",
    "ImageGenerationProvider",
    "VideoGenerationProvider",
    "AudioProvider",
    "GENERATION_KINDS",
    "MockTextProvider",
    "MockVisionProvider",
    "MockMultimodalProvider",
    "MockEmbeddingProvider",
    "OpenAICompatibleTextProvider",
    "OpenAICompatibleVisionProvider",
    "OpenAICompatibleMultimodalProvider",
    "BailianTextProvider",
    "create_provider",
    "get_default_provider",
    "provider_kind_vendor",
]

