# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— Mock Provider 四件套（v2/llm/providers/mock.py）

作用（stage9_plan §8）：把"测试里手写 transport=fake"的临时技巧，升级成
Provider 层的一等公民——单元 / 集成 / workflow 测试全部 0 API 调用、0 成本、可重复。

四个实现：
    MockTextProvider        可编程返回（支持按 step 脚本 / 按调用次数出队 / 注入抛错）
    MockVisionProvider      固定返回预设理解结果，入参即校验（保证喂了图/prompt）
    MockMultimodalProvider  固定返回图文对话结果，同上
    MockEmbeddingProvider   确定性伪向量：同输入必同输出（未来检索测试可稳定断言）

所有 Mock：
    - 不访问网络、不访问真实 API；
    - 打 vendor="mock"、kind 对应各自模态；
    - 可选注入 TokenLogger：同一 JSONL 体系记 usage，但 estimated_cost 恒为 None
      （Mock 免费，绝不产生真实账目）。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from core.token_log import TokenUsage, new_request_id, compute_prompt_hash
from llm.client import LLMError

from llm.providers.base import (
    ProviderResult, EmbeddingResult,
    TextProvider, VisionProvider, MultimodalProvider, EmbeddingProvider,
)


# Mock 默认账目：一套"确定性但非零"的用量（用于验证 token 管线确实在记）
_DEFAULT_USAGE = TokenUsage(input_tokens=25, output_tokens=15, total_tokens=40,
                            cache_miss_tokens=25)


def _usage_or(override: Optional[TokenUsage]) -> TokenUsage:
    return override if override is not None else _DEFAULT_USAGE


# ---------------------------------------------------------------------------
# MockTextProvider
# ---------------------------------------------------------------------------
class MockTextProvider(TextProvider):
    """可编程的文本 Mock。

    响应来源（按优先级）：
        1. script: {step: 响应}        —— 按 step 名精确返回（同一步多次调用取同值）
        2. responses: [...]            —— 按调用顺序出队（每次弹一条）
        3. default_text: str           —— 兜底（脚本用尽 / 未配脚本时）
    其中每个"响应"可以是：
        - str                          -> 作为返回文本
        - dict                         -> 形如 {"text": str, "usage": TokenUsage} 细粒度控制
        - LLMError 实例                -> 原样抛出（测"错误该在哪层被处理"）
        - Exception 实例               -> 原样抛出
    """

    vendor = "mock"

    def __init__(
        self,
        responses: Optional[List[Any]] = None,
        *,
        script: Optional[Dict[str, Any]] = None,
        default_text: str = "{}",
        model: str = "mock-text",
        default_usage: Optional[TokenUsage] = None,
        logger: Optional[Any] = None,
    ):
        self.responses = list(responses or [])
        self.script = dict(script or {})
        self.default_text = default_text
        self.model = model
        self.default_usage = _usage_or(default_usage)
        self.logger = logger
        # 观测位：测试可检查"这次调用带了什么"
        self.calls: List[dict] = []
        self.last_structured: Optional[dict] = None

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
        self.last_structured = structured
        self.calls.append({
            "step": step, "agent": agent,
            "structured": structured, "messages": list(messages),
        })
        request_id = new_request_id()
        usage = self.default_usage

        response = self._pick(step)
        if isinstance(response, BaseException):
            self._emit_log(step=step, agent=agent, model=self.model, usage=usage,
                           success=False, error=f"{type(response).__name__}: {response}",
                           prompt_hash=_messages_hash(messages),
                           prompt_length=_prompt_len(messages), message_count=len(messages))
            raise response
        if isinstance(response, dict):
            text = str(response.get("text", ""))
            usage = response.get("usage", usage)
        else:
            text = str(response) if response is not None else self.default_text

        self._emit_log(step=step, agent=agent, model=self.model, usage=usage,
                       success=True, prompt_hash=_messages_hash(messages),
                       prompt_length=_prompt_len(messages),
                       message_count=len(messages))
        return ProviderResult(text=text, model=self.model, usage=usage,
                              request_id=request_id, duration_ms=1, retry_count=0)

    def _pick(self, step: str):
        """按优先级挑一条响应。"""
        if step in self.script:
            return self.script[step]
        if self.responses:
            return self.responses.pop(0)
        return self.default_text


# ---------------------------------------------------------------------------
# MockVisionProvider
# ---------------------------------------------------------------------------
class MockVisionProvider(VisionProvider):
    """视觉理解 Mock：固定返回 reply；要求必须传了图引用与 prompt。"""

    vendor = "mock"

    def __init__(self, *, reply: str = "[mock-vision] 图像理解结果",
                 model: str = "mock-vision",
                 default_usage: Optional[TokenUsage] = None,
                 logger: Optional[Any] = None):
        self.reply = reply
        self.model = model
        self.default_usage = _usage_or(default_usage)
        self.logger = logger
        self.calls: List[dict] = []

    def understand(self, image: Any, prompt: str, *,
                   step: str = "", agent: str = "") -> ProviderResult:
        if image is None or prompt is None or not str(prompt).strip():
            raise ValueError("MockVisionProvider: 必须同时提供 image 与 prompt（入参校验）")
        self.calls.append({"step": step, "agent": agent, "prompt": prompt})
        request_id = new_request_id()
        usage = self.default_usage
        self._emit_log(step=step, agent=agent, model=self.model, usage=usage,
                       success=True,
                       prompt_hash=_messages_hash([{"role": "user", "content": str(prompt)}]),
                       prompt_length=len(str(prompt)),
                       message_count=1)
        return ProviderResult(text=self.reply, model=self.model, usage=usage,
                              request_id=request_id, duration_ms=1, retry_count=0)


# ---------------------------------------------------------------------------
# MockMultimodalProvider
# ---------------------------------------------------------------------------
class MockMultimodalProvider(MultimodalProvider):
    """图文对话 Mock：固定返回 reply；要求 messages 非空且 images 非空。"""

    vendor = "mock"

    def __init__(self, *, reply: str = "[mock-multimodal] 图文对话结果",
                 model: str = "mock-multimodal",
                 default_usage: Optional[TokenUsage] = None,
                 logger: Optional[Any] = None):
        self.reply = reply
        self.model = model
        self.default_usage = _usage_or(default_usage)
        self.logger = logger
        self.calls: List[dict] = []

    def chat(self, messages: List[dict], *, images: tuple = (),
             step: str = "", agent: str = "") -> ProviderResult:
        if not messages or not images:
            raise ValueError("MockMultimodalProvider: 必须同时提供 messages 与 images（入参校验）")
        self.calls.append({"step": step, "agent": agent,
                           "images": len(images), "messages": list(messages)})
        request_id = new_request_id()
        usage = self.default_usage
        self._emit_log(step=step, agent=agent, model=self.model, usage=usage,
                       success=True, prompt_hash=_messages_hash(messages),
                       prompt_length=_prompt_len(messages),
                       message_count=len(messages) + len(images))
        return ProviderResult(text=self.reply, model=self.model, usage=usage,
                              request_id=request_id, duration_ms=1, retry_count=0)


# ---------------------------------------------------------------------------
# MockEmbeddingProvider
# ---------------------------------------------------------------------------
class MockEmbeddingProvider(EmbeddingProvider):
    """文本向量 Mock：确定性伪向量（同输入必同输出），供未来检索单测稳定断言。

    规则：每个文本生成固定 dim 维向量；第 i 个元素 = md5(f"{text}#{i}") 的
    0..1 伪随机值。向量只与文本内容有关，与它出现在列表第几位无关。
    """

    vendor = "mock"

    def __init__(self, *, dim: int = 8, model: str = "mock-embedding",
                 default_usage: Optional[TokenUsage] = None,
                 logger: Optional[Any] = None):
        self.dim = int(dim)
        self.model = model
        self.default_usage = _usage_or(default_usage)
        self.logger = logger
        self.calls: List[dict] = []

    def embed(self, texts: List[str], *, step: str = "", agent: str = "") -> EmbeddingResult:
        texts = [str(t) for t in (texts or [])]
        self.calls.append({"step": step, "agent": agent, "count": len(texts)})
        request_id = new_request_id()
        vectors = [self._vector_for(t) for t in texts]
        usage = TokenUsage(
            input_tokens=sum(max(1, len(t.split())) for t in texts),
            output_tokens=0,
            total_tokens=sum(max(1, len(t.split())) for t in texts),
        )
        self._emit_log(step=step, agent=agent, model=self.model, usage=usage,
                       success=True,
                       prompt_hash=_messages_hash(
                           [{"role": "user", "content": t} for t in texts]),
                       prompt_length=sum(len(t) for t in texts),
                       message_count=len(texts))
        return EmbeddingResult(vectors=vectors, model=self.model, usage=usage,
                               request_id=request_id, duration_ms=1, retry_count=0)

    def _vector_for(self, text: str) -> List[float]:
        out = []
        for i in range(self.dim):
            digest = hashlib.md5(f"{text}#{i}".encode("utf-8")).hexdigest()
            out.append(int(digest[:8], 16) / 0xFFFFFFFF)
        return out


def _prompt_len(messages: List[dict]) -> int:
    return sum(len(str(m.get("content", "") or "")) for m in (messages or []))


def _messages_hash(messages: List[dict]) -> str:
    """给 mock 输入算 prompt_hash，与真实执行器（LLMClient._log）同源。

    Mock 也走同一 TokenLogger JSONL 体系，因此应同样满足"不记 prompt 正文、
    只记 sha256 哈希"（Stage2 语义，第十轮排查项）。这样 Mock 与真实执行器
    写出的每条记录字段口径一致（request_id / prompt_hash / token …）。
    """
    return compute_prompt_hash(list(messages or []))


__all__ = [
    "MockTextProvider",
    "MockVisionProvider",
    "MockMultimodalProvider",
    "MockEmbeddingProvider",
]
