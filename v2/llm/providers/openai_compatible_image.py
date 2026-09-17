# -*- coding: utf-8 -*-
"""
V0.2 Stage 13 —— OpenAI 兼容文生图 Provider（v2/llm/providers/openai_compatible_image.py）

作用（开工指令13 §7.2）：第一个**真实图像生成**实现类。任何兼容
OpenAI `POST /images/generations` 协议的文生图端点（百炼 DashScope 兼容模式、
其他云厂商、本地 vLLM/ComfyUI 兼容网关……）都是同一实现类的不同 Config 档——
**厂商差异全部收敛成 Config，绝不写死任何模型名**（§7.2 硬要求）。

实现策略（复用既有模式，不另造第二套）：
    1. 错误翻译 / 瞬时重试 / 传输注入 —— 沿用 Stage 2 `LLMClient` 的既有模式
       （错误家族直接复用 `llm.client` 的 LLMError 子类；重试只重试 retryable，
       指数退避；transport 可注入 → 单测 0 网络）。本类**不继承** LLMClient：
       `LLMClient.chat()` 的传输入参形状是 chat 专用的，图像协议是另一个端点。
    2. **真实请求计数点**：`_invoke()` 是每个 HTTP attempt 的唯一入口，
       原子完成"检查预算 → 计数 +1 → 真正发送"（与 vision Provider 同一纪律）。
       retry 一样计数（按实际 HTTP 请求次数消耗预算，不按业务任务计）。
       本地校验失败（prompt 为空 / 未配置模型）发生在计数点之前，不消耗预算。
    3. 返回形态 = **引用串**（`ProviderResult.text`）：
           data:image/...;base64,...   后端直接给 base64（优先，无二次网络）
           http(s)://...               后端给 URL（由编排层下载为二进制）
       两种都处理；两者皆无 → `ImageGenerationError`（明确报错，绝不静默留空文件）。
    4. `size` 归一化走 Config：内部/canonical 记法用 `1024*1024`（§6.1/§6.2），
       线上协议多为 `1024x1024` → 由 Config 的 `size_separator` 决定，不硬编码。

reference 语义（诚实边界）：
    `generate_image(..., reference=...)` 的 reference 是**溯源/一致性提示串**
    （参考图 image_id + sha256 组合，见 §7.4），不是图生图输入——图生图/局部重绘
    未立项（§4.4）。本类把它透传给 transport（厂商专属 transport 可用它），
    默认的 OpenAI 兼容 transport **不发送**（该协议没有参考图槽位）；
    编排层会把它完整记进 manifest，保证信息不丢。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional, Tuple

from core.token_log import TokenUsage, new_request_id
from llm.client import (
    LLMError,
    LLMTimeoutError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMInternalError,
    LLMRequestError,
    LLMAuthenticationError,
)
from llm.providers.base import ImageGenerationProvider, ProviderResult
from llm.request_counter import APIRequestCounter, get_api_counter

#: 图像 transport 契约：入参 (prompt, model, size, reference, timeout)
#:                  出参 (引用串, usage)
ImageTransportFn = Callable[
    [str, str, Optional[str], Optional[str], float], Tuple[str, TokenUsage]
]


class ImageGenerationError(LLMError):
    """图像生成专属错误（铁律 9：新增可区分来源的错误类型）。

    用于"协议层拿到了响应，但响应里没有可用的图片载体（既无 b64_json 也无 url）"
    这类**确定性失败**：不可重试（重试同样的请求只会得到同样的空响应）。
    其余失败一律沿用 `llm.client` 既有错误家族，原样透传、不吞不改写。
    """
    retryable = False


class OpenAICompatibleImageProvider(ImageGenerationProvider):
    """OpenAI 兼容文生图执行器 + ImageGenerationProvider 抽象身份。

    构造不联网；网络调用只发生在 `generate_image()` 被调用时，
    且每个真实 HTTP 请求都先经预算检查并计数（含 retry）。
    """

    kind = "image"
    vendor = "openai-compatible"       # 工厂按 Config 覆盖为具体厂商（bailian/...）
    budget_kind = "image"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        transport: Optional[ImageTransportFn] = None,
        vendor: Optional[str] = None,
        counter: Optional[APIRequestCounter] = None,
        size_separator: str = "x",
    ):
        if not str(model or "").strip():
            # 配置护栏（沿用 Stage 10 范式）：不硬编码模型名，也不静默退回 mock
            raise LLMRequestError(
                "OpenAICompatibleImageProvider: model 为空。真实出图前请在 Config 的 "
                "PROVIDERS['<vendor>_image']['default_model'] 填入具体图像模型名。"
            )
        if not str(base_url or "").strip():
            raise LLMRequestError("OpenAICompatibleImageProvider: base_url 为空。")
        self.api_key = str(api_key or "")
        self.base_url = str(base_url)
        self.model = str(model)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._transport: ImageTransportFn = transport or self._default_transport
        self._uses_default_transport = transport is None
        if vendor is not None:
            self.vendor = str(vendor)
        self.size_separator = str(size_separator or "x")
        self.counter = counter or get_api_counter()
        # 业务调用内的 attempt 跟踪（每次 generate_image 入口清零）
        self._attempt = 0
        self._business_request_id = ""

    # ------------------------------------------------------------------
    # 业务入口
    # ------------------------------------------------------------------
    def generate_image(self, prompt: str, *, size: Optional[str] = None,
                       reference: Optional[str] = None) -> ProviderResult:
        """文生图：一次业务调用 → 0..max_retries 次真实 HTTP 请求。

        本地校验（prompt 为空 / 模型未配置）在计数点之前完成，不消耗预算。
        失败一律抛 `llm.client` 的错误家族（或 `ImageGenerationError`）。
        """
        text_prompt = str(prompt or "")
        if not text_prompt.strip():
            raise LLMRequestError(
                "generate_image(): prompt 不能为空（出图 prompt 应来自 "
                "ComposedVisualInput.render_prompt()）。"
            )
        wire_size = self.normalize_size(size)

        self._attempt = 0
        self._business_request_id = new_request_id()
        started = time.monotonic()
        attempts = 0
        max_attempts = self.max_retries + 1
        last_error: Optional[LLMError] = None

        while attempts < max_attempts:
            try:
                ref, usage = self._invoke(text_prompt, self.model, wire_size,
                                          reference, self.timeout)
                return ProviderResult(
                    text=ref,
                    model=self.model,
                    usage=usage,
                    request_id=self._business_request_id,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    retry_count=attempts,
                )
            except LLMError as exc:
                last_error = exc
                attempts += 1
                if not exc.retryable or attempts >= max_attempts:
                    break
                backoff = self.retry_backoff_seconds * (2 ** (attempts - 1))
                if backoff > 0:
                    time.sleep(backoff)

        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------
    # 真实请求计数点（每个 HTTP attempt 恰好经过一次）
    # ------------------------------------------------------------------
    def _invoke(self, prompt: str, model: str, size: Optional[str],
                reference: Optional[str], timeout: float) -> Tuple[str, TokenUsage]:
        """检查预算 → 计数 +1 → 真正发送。retry 一样计数（预算按 HTTP 请求计）。"""
        self._attempt += 1
        self.counter.consume_real_request(
            self.budget_kind,
            provider=self.vendor,
            model=model,
            request_id=self._business_request_id,
            attempt=self._attempt,
            note=f"{type(self).__name__}.attempt",
        )
        return self._transport(prompt, model, size, reference, timeout)

    # ------------------------------------------------------------------
    # size 归一化（厂商差异 = Config）
    # ------------------------------------------------------------------
    def normalize_size(self, size: Optional[str]) -> Optional[str]:
        """canonical `1024*1024` → 线上协议记法（默认 `1024x1024`）。

        空值原样返回（不编造默认尺寸——默认尺寸属 Config，不属 Provider）。
        """
        s = str(size or "").strip()
        if not s:
            return None
        return s.replace("*", self.size_separator)

    # ------------------------------------------------------------------
    # 真实 transport（POST {base_url}/images/generations）
    # ------------------------------------------------------------------
    def _default_transport(self, prompt: str, model: str, size: Optional[str],
                           reference: Optional[str], timeout: float) -> Tuple[str, TokenUsage]:
        """真正调用 OpenAI 兼容图像端点。

        `openai` 库延迟导入（只有真实调用才需要它）。SDK 异常统一翻译成
        V0.2 的 LLMError 子类，绝不让 SDK 异常泄漏给上层。
        """
        import openai  # 延迟导入

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)
        kwargs: dict = {"model": model, "prompt": prompt}
        if size:
            kwargs["size"] = size
        try:
            resp = client.images.generate(**kwargs)
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(f"Image request timed out after {timeout}s.") from e
        except openai.APIConnectionError as e:
            raise LLMConnectionError(f"Failed to connect to {self.base_url}.") from e
        except openai.RateLimitError as e:
            raise LLMRateLimitError(f"Rate limit exceeded: {e}") from e
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            raise LLMAuthenticationError("Authentication failed (check API key).") from e
        except openai.BadRequestError as e:
            raise LLMRequestError(f"Invalid image request: {e}") from e
        except openai.APIStatusError as e:
            if 500 <= (e.status_code or 0) < 600:
                raise LLMInternalError(f"Server error (HTTP {e.status_code}).") from e
            raise LLMRequestError(f"Image API error (HTTP {e.status_code}): {e}") from e

        return _reference_from_images_response(resp)

    def __repr__(self) -> str:  # pragma: no cover - 仅调试友好
        return (f"{type(self).__name__}(kind={self.kind!r}, vendor={self.vendor!r}, "
                f"model={self.model!r})")


def _reference_from_images_response(resp: Any) -> Tuple[str, TokenUsage]:
    """OpenAI 兼容图像响应 → (引用串, usage)。

    优先级：`data[0].b64_json`（内嵌，无二次网络）> `data[0].url`（远端，需下载）。
    两者皆无 → `ImageGenerationError`（明确报错，绝不静默返回空引用）。
    """
    items = getattr(resp, "data", None)
    if not items:
        raise ImageGenerationError("图像响应中没有 data 数组（无可取用的图片载体）。")
    item = items[0]
    b64 = getattr(item, "b64_json", None)
    if not b64 and isinstance(item, dict):
        b64 = item.get("b64_json")
    if b64:
        return "data:image/png;base64," + str(b64), TokenUsage()
    url = getattr(item, "url", None)
    if not url and isinstance(item, dict):
        url = item.get("url")
    if url:
        return str(url), TokenUsage()
    raise ImageGenerationError(
        "图像响应的 data[0] 既没有 b64_json 也没有 url（无法取得图片载体）。")


__all__ = [
    "OpenAICompatibleImageProvider",
    "ImageGenerationError",
    "ImageTransportFn",
]
