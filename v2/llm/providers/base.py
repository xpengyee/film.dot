# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— Provider 接口族与公共元信息（v2/llm/providers/base.py）

作用（stage9_plan §5）：定义统一的 Provider 抽象接口族，让业务 Agent 只认识
"抽象 Provider"，不认识任何一家厂商的 SDK / endpoint / 具体模型名。

架构分工（stage9_plan §4）：
    Agent     说"要什么"（把 context 拼成 prompt、声明期望、解析规范化、业务校验）
    Router    说"用哪家哪个模型"（返回一份路由描述，不亲自发请求）
    Provider  说"这次调用结果怎么样"（封装厂商协议、瞬时错误重试、上报 usage/cost）
    Model     是数据不是逻辑，只存在于 Config / 路由结果里

本文件只放"接口 + 元信息 + 统一返回值"，不含任何真实厂商调用。
真实实现（OpenAI 兼容文本 / Bailian）在 openai_compatible.py / bailian.py；
测试用 Mock 在 mock.py；Config→实例的统一入口在 registry.py。

依赖方向：providers/base 依赖 core.token_log（usage / 日志记录对象），
不反向依赖 agents / workflow。复用 llm/client.py 的 LLMError 家族与结果字段，
不另起第二套错误 / usage 体系。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.token_log import TokenUsage, LLMCallRecord, new_request_id, now_iso


# ---------------------------------------------------------------------------
# Provider 元信息基类
# ---------------------------------------------------------------------------
class Provider:
    """所有 Provider 的公共基类：只放元信息与日志小工具，不定义业务。

    三个只读标签决定"怎么路由/怎么选实例"（stage9_plan §5.3）：
        kind    "text" | "vision" | "multimodal" | "embedding"
                | "image" | "video" | "audio"   —— 模态 + 协议标签
        vendor  "mock" | "bailian" | "deepseek" | "local" | ... —— 供应商标签
        model   当前生效模型名（来自 Config，绝不硬编码进业务）
    """

    kind: str = ""
    vendor: str = ""
    model: str = ""
    # 可选 TokenLogger：Mock 用它上报 usage（cost 恒 None / 0，绝不产生真实账目）。
    # 真实 Provider（OpenAI 兼容文本）自己会记日志，不需要这里再记。
    logger: Optional[Any] = None

    def __repr__(self) -> str:  # pragma: no cover - 仅调试友好
        return (f"{type(self).__name__}(kind={self.kind!r}, vendor={self.vendor!r}, "
                f"model={self.model!r})")

    # ---------------- Mock 共用的日志小工具 ----------------
    def _emit_log(self, *, step: str, agent: str, model: Optional[str],
                  usage: TokenUsage, duration_ms: int = 1,
                  success: bool = True, error: Optional[str] = None,
                  retry_count: int = 0, prompt_hash: str = "",
                  prompt_length: int = 0, message_count: int = 0) -> None:
        """把一次（Mock）调用记进 TokenLogger（同一 JSONL 体系）。

        规则：Mock 不联网、不花钱，因此 estimated_cost 恒为 None（不进真实账目），
        currency 留空。这样"Mock 跑 workflow"也能验证 usage/token 走的是
        stage2 既有的记录管线，只是账目为 0/null。
        """
        if self.logger is None:
            return
        rec = LLMCallRecord(
            timestamp=now_iso(),
            request_id=new_request_id(),
            step=step,
            agent=agent,
            model=model or self.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=None,
            currency="",
            duration_ms=int(duration_ms),
            success=success,
            retry_count=retry_count,
            error=error,
            prompt_hash=prompt_hash,
            prompt_length=int(prompt_length),
            message_count=int(message_count),
        )
        self.logger.record(rec)


# ---------------------------------------------------------------------------
# 统一"调用结果"
# ---------------------------------------------------------------------------
@dataclass
class ProviderResult:
    """文本类调用的统一返回（stage9_plan §5.2）。

    字段刻意与 llm/client.py 的 LLMResponse 对齐
    （text / model / usage / request_id / duration_ms / retry_count），
    使 Mock 与真实实现可以互相替换而不改上层调用方。
    """

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    request_id: str = ""
    duration_ms: int = 0
    retry_count: int = 0

    @classmethod
    def from_llm_response(cls, resp: Any) -> "ProviderResult":
        """把 Stage2 的 LLMResponse 转成 ProviderResult（OpenAI 兼容执行器用）。"""
        return cls(
            text=resp.text,
            model=resp.model,
            usage=resp.usage,
            request_id=resp.request_id,
            duration_ms=resp.duration_ms,
            retry_count=getattr(resp, "retry_count", 0),
        )

    def to_llm_response(self) -> Any:
        """反向转回 LLMResponse（存量代码迁移 / 对比用）。延迟导入避免循环。"""
        from llm.client import LLMResponse
        return LLMResponse(
            text=self.text,
            model=self.model,
            usage=self.usage,
            request_id=self.request_id,
            duration_ms=self.duration_ms,
            retry_count=self.retry_count,
        )


@dataclass
class EmbeddingResult:
    """Embedding 调用的统一返回（stage9_plan §6.3）。"""

    vectors: List[List[float]]           # 文本 -> 向量；与入参 texts 一一对应
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    request_id: str = ""
    duration_ms: int = 0
    retry_count: int = 0


# ---------------------------------------------------------------------------
# 接口族（Text / Vision / Multimodal / Embedding）
# ---------------------------------------------------------------------------
class TextProvider(Provider):
    """文本对话 Provider（stage9 必须完整落地的最重要能力）。

    语义与 LLMClient.chat 对齐，便于存量业务平滑迁移：
    chat(messages, *, step="", agent="", temperature=None, max_tokens=None,
         structured=None) -> ProviderResult
    """

    kind = "text"

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
        """发起一次文本对话，返回文本 + usage。

        structured: 可选 dict，如 {"type": "json_object"} / {"json_schema": {...}}，
                    表示"尽力让后端返回结构化"；后端不支持时由实现层决定忽略，
                    不在这里做任何业务合法性校验。
        失败一律抛 LLMError 家族（由各实现负责翻译 SDK 异常）。
        """
        raise NotImplementedError(f"{type(self).__name__} 未实现 chat()")


class VisionProvider(Provider):
    """视觉理解 Provider（stage9：接口 + Mock，不接真实厂商）。

    纯视觉理解任务：给一张图 + 一个问题/提示，返回文字理解。
    image 统一以"已落盘图片引用 / content 描述"表达；不在接口层设计二进制传输。
    """

    kind = "vision"

    def understand(
        self,
        image: Any,
        prompt: str,
        *,
        step: str = "",
        agent: str = "",
    ) -> ProviderResult:
        raise NotImplementedError(f"{type(self).__name__} 未实现 understand()")


class MultimodalProvider(Provider):
    """图文混合对话 Provider（stage9：接口 + Mock，未来 VLM / QA / Review 用）。"""

    kind = "multimodal"

    def chat(
        self,
        messages: List[dict],
        *,
        images: tuple = (),
        step: str = "",
        agent: str = "",
    ) -> ProviderResult:
        raise NotImplementedError(f"{type(self).__name__} 未实现 chat(images=...)")


class EmbeddingProvider(Provider):
    """文本向量化 Provider（stage9：接口 + Mock；未来 Memory 语义检索消费）。"""

    kind = "embedding"

    def embed(
        self,
        texts: List[str],
        *,
        step: str = "",
        agent: str = "",
    ) -> EmbeddingResult:
        raise NotImplementedError(f"{type(self).__name__} 未实现 embed()")


# ---------------------------------------------------------------------------
# 未来接口预留（Image / Video / Audio）——只占位，本阶段不实现、不联调
# ---------------------------------------------------------------------------
class ImageGenerationProvider(Provider):
    """（预留）文生图 Provider。

    对应未来 Storyboard → Image 链路，与 Stage8 的 ShotAsset(kind='image') 衔接：
    先让"资产能表达"，再接"生成"。本阶段任何调用都会 NotImplementedError。
    未来实现时，结果返回一个"产物引用"（落盘路径 / asset ref），
    不要在这里决定文件命名与版本（那是 Artifact 层铁律）。
    """

    kind = "image"

    def generate_image(self, prompt: str, *, size: Optional[str] = None,
                       reference: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError(
            "ImageGenerationProvider 是 Stage 9 预留接口，未实现真实文生图"
        )


class VideoGenerationProvider(Provider):
    """（预留）文生视频 Provider。同上，本阶段不实现。"""

    kind = "video"

    def generate_video(self, prompt: str, *, shots: Optional[list] = None,
                       duration: Optional[float] = None) -> ProviderResult:
        raise NotImplementedError(
            "VideoGenerationProvider 是 Stage 9 预留接口，未实现真实文生视频"
        )


class AudioProvider(Provider):
    """（预留）TTS / 语音 Provider。同上，本阶段不实现。"""

    kind = "audio"

    def synthesize(self, text: str, *, voice: Optional[str] = None) -> ProviderResult:
        raise NotImplementedError(
            "AudioProvider 是 Stage 9 预留接口，未实现真实 TTS"
        )


# 预留三类统一出口（供 registry / 测试识别）
GENERATION_KINDS = ("image", "video", "audio")


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
]
