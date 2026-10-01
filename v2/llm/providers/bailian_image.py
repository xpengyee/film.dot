# -*- coding: utf-8 -*-
"""
V0.2 Stage 13-P —— 阿里云百炼 DashScope **原生**文生图 Provider
（v2/llm/providers/bailian_image.py）

为什么需要它（T-9 核实结论，2026-09-18）：
    百炼的图像生成**不在** OpenAI 兼容模式里（`/compatible-mode/v1` 未覆盖图像生成），
    其图像能力走 DashScope **原生**端点，形状与 OpenAI `/images/generations` 完全不同：

        同步（千问系）
            POST {base}/services/aigc/multimodal-generation/generation
        异步（万相系）
            POST {base}/services/aigc/image-generation/generation
                 header: X-DashScope-Async: enable   → 返回 output.task_id
            GET  {base}/tasks/{task_id}              → 轮询至 output.task_status == SUCCEEDED
        请求体（同步/异步同形）
            {"model": …, "input": {"messages": [{"role": "user",
                                                "content": [{"text": <prompt>}]}]},
             "parameters": {…}}
        响应（同步/异步同形，★图片 URL 路径一致）
            output.choices[0].message.content[0].image  →  https://…png?Expires=…
        size 记法：`parameters.size` = `"宽*高"`（如 "1024*1024"）—— 与本项目 canonical
                   记法**完全一致**，故本档 `size_separator` 用 `"*"`（不是 "x"）。

★ 本文件是**纯新增**：不改动、不替换任何既有 Provider。
  OpenAI 兼容档（火山方舟 / OpenAI / 硅基流动 / …）仍由
  `openai_compatible_image.OpenAICompatibleImageProvider` 承载；两档由 Config 的
  `protocol` 字段选择（见 `registry._create_image_provider`），**互不影响、可随时切换**。

预算语义（★重要，与异步轮询的关系）：
    唯一计数点仍是 `_invoke()`，它**包住整次业务尝试**（含内部的 1 次创建 + N 次轮询）。
    因此 **一次业务调用 = 消耗 1 次 image 预算**，轮询**不额外消耗预算** —— 这正是
    §7.3 想要的"每张图 = 1 次计费调用"语义（否则 1 张图就会耗尽 3 次配额）。
    代价：轮询请求**不进入** `APIRequestCounter` 审计（它只记计费口径的请求）；
    单张图的完整信息记在编排层的图像账 `image_generation_calls.jsonl`。

诚实边界：
    - 图片以 **URL** 返回（有效期约 24h），由编排层 `resolve_image_bytes()` 下载为二进制；
      本 Provider **不落盘、不决定文件名与版本**（那是 Artifact 层的铁律）。
    - 本契约一次只承载**一张**图。若响应里出现多张（`parameters.n > 1` 或组图），
      **明确报错**而不静默丢图（避免"多出来的图被无声丢弃"）。
    - `reference`（参考图 image_id+sha256 溯源串）本档**不发送**：DashScope 文生图
      无对应槽位（图生图未立项）；编排层会把它完整记进 manifest，信息不丢。
    - `parameters.watermark` 官方默认 `false`（无角标）；本档显式下发，避免厂商改默认。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

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
from llm.providers.openai_compatible_image import ImageGenerationError
from llm.request_counter import APIRequestCounter, get_api_counter

#: transport 契约（与 OpenAI 兼容档**同形**，便于上层与测试复用）
#:   入参 (prompt, model, size, reference, timeout)
#:   出参 (引用串, usage)
ImageTransportFn = Callable[
    [str, str, Optional[str], Optional[str], float], Tuple[str, TokenUsage]
]

#: DashScope 原生端点（★Config 可覆盖 —— 厂商路径漂移全部走 Config，不写死进调用逻辑）
#: URL 语义是唯一的：base_url 已包含 ``/api/v1``，path 只是该 base 下的绝对路径。
#: 因此默认 path 不得再重复 ``/api/v1``；公共域与业务空间域共用此语义。
DEFAULT_SYNC_PATH = "/services/aigc/multimodal-generation/generation"
DEFAULT_CREATE_PATH = "/services/aigc/image-generation/generation"
DEFAULT_TASK_PATH = "/tasks/{task_id}"

#: 异步任务终态（非 SUCCEEDED 的终态一律判失败，不无限轮询）
_FAILED_TASK_STATUSES = ("FAILED", "CANCELED", "CANCELLED", "UNKNOWN")


class BailianImageProvider(ImageGenerationProvider):
    """百炼 DashScope 原生文生图执行器 + ImageGenerationProvider 抽象身份。

    构造不联网；网络调用只发生在 `generate_image()` 被调用时，
    且**每次业务尝试**先经预算检查并计数（retry 会再计一次；轮询不计）。
    """

    kind = "image"
    vendor = "bailian_native"          # 工厂按 Config 覆盖
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
        size_separator: str = "*",     # ★ DashScope 用 "宽*高"，与 canonical 一致
        async_mode: bool = False,
        sync_path: Optional[str] = None,
        create_path: Optional[str] = None,
        task_path: Optional[str] = None,
        poll_interval: float = 3.0,
        poll_timeout: float = 180.0,
        extra_parameters: Optional[Dict[str, Any]] = None,
        send_negative_prompt: bool = True,
    ):
        if not str(model or "").strip():
            raise LLMRequestError(
                "BailianImageProvider: model 为空。真实出图前请在 Config 的 "
                "PROVIDERS['<vendor>_image']['default_model'] 填入具体图像模型名。"
            )
        if not str(base_url or "").strip():
            raise LLMRequestError("BailianImageProvider: base_url 为空。")
        self.api_key = str(api_key or "")
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.size_separator = str(size_separator or "*")
        self.async_mode = bool(async_mode)
        self.sync_path = str(sync_path or DEFAULT_SYNC_PATH)
        self.create_path = str(create_path or DEFAULT_CREATE_PATH)
        self.task_path = str(task_path or DEFAULT_TASK_PATH)
        self.poll_interval = max(0.0, float(poll_interval))
        self.poll_timeout = max(0.0, float(poll_timeout))
        self.extra_parameters: Dict[str, Any] = dict(extra_parameters or {})
        self.send_negative_prompt = bool(send_negative_prompt)
        self._transport: ImageTransportFn = transport or self._default_transport
        self._uses_default_transport = transport is None
        if vendor is not None:
            self.vendor = str(vendor)
        self.counter = counter or get_api_counter()
        self._attempt = 0
        self._business_request_id = ""

    # ------------------------------------------------------------------
    # 业务入口（与 OpenAI 兼容档同形：retry 只重试 retryable）
    # ------------------------------------------------------------------
    def generate_image(self, prompt: str, *, size: Optional[str] = None,
                       reference: Optional[str] = None) -> ProviderResult:
        text = str(prompt or "")
        if not text.strip():
            # 本地校验：不消耗预算（没发 HTTP）
            raise LLMRequestError("BailianImageProvider: prompt 为空，拒绝发起真实请求。")

        self._attempt = 0
        self._business_request_id = new_request_id()
        started = time.monotonic()
        max_attempts = max(1, self.max_retries + 1)
        attempts = 0
        last_error: Optional[LLMError] = None

        while attempts < max_attempts:
            try:
                ref, usage = self._invoke(
                    text, self.model, self.normalize_size(size), reference, self.timeout)
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
    # 真实请求计数点（★每个业务尝试恰好经过一次；轮询在其内部，不另计）
    # ------------------------------------------------------------------
    def _invoke(self, prompt: str, model: str, size: Optional[str],
                reference: Optional[str], timeout: float) -> Tuple[str, TokenUsage]:
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
        """canonical `1024*1024` → 线上记法（本档默认 `*`，即原样）。

        空值原样返回（不编造默认尺寸 —— 默认尺寸属 Config，不属 Provider）。
        """
        s = str(size or "").strip()
        if not s:
            return None
        return s.replace("*", self.size_separator)

    # ------------------------------------------------------------------
    # 请求体构造（形状照官方文档，不做任何"发明"）
    # ------------------------------------------------------------------
    def build_body(self, prompt: str, size: Optional[str],
                   negative_prompt: str = "") -> Dict[str, Any]:
        params: Dict[str, Any] = {"n": 1}
        if size:
            params["size"] = size
        # 官方默认 watermark=false；显式下发，避免厂商改默认后交付物多出角标
        params["watermark"] = False
        params.update(self.extra_parameters)
        body: Dict[str, Any] = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": params,
        }
        neg = str(negative_prompt or "").strip()
        if neg and self.send_negative_prompt:
            # 顶层字段（与 input 同级），照官方文档
            body["negative_prompt"] = neg
        return body

    # ------------------------------------------------------------------
    # 真实 transport（stdlib urllib，不引入新依赖）
    # ------------------------------------------------------------------
    def _default_transport(self, prompt: str, model: str, size: Optional[str],
                           reference: Optional[str], timeout: float) -> Tuple[str, TokenUsage]:
        body = self.build_body(prompt, size)
        if self.async_mode:
            payload = self._post_json(self.create_path, body, timeout, async_submit=True)
            task_id = str((payload.get("output") or {}).get("task_id") or "")
            if not task_id:
                raise ImageGenerationError(
                    "百炼异步创建任务未返回 output.task_id（无法轮询结果）。")
            payload = self._poll_task(task_id, timeout)
        else:
            payload = self._post_json(self.sync_path, body, timeout, async_submit=False)
        return _extract_reference(payload)

    def _poll_task(self, task_id: str, timeout: float) -> Dict[str, Any]:
        """轮询异步任务直到终态。★轮询请求**不消耗** image 预算（见模块文档）。"""
        url = self.base_url + self.task_path.format(task_id=task_id)
        deadline = time.monotonic() + self.poll_timeout
        while True:
            payload = self._get_json(url, timeout)
            out = payload.get("output") or {}
            status = str(out.get("task_status") or "").upper()
            if status == "SUCCEEDED":
                return payload
            if status in _FAILED_TASK_STATUSES:
                raise ImageGenerationError(
                    f"百炼异步图像任务未成功：task_id={task_id} task_status={status} "
                    f"message={payload.get('message') or out.get('message') or ''}")
            if time.monotonic() >= deadline:
                raise LLMTimeoutError(
                    f"百炼异步图像任务轮询超时（{self.poll_timeout}s，task_id={task_id}，"
                    f"最后状态={status or 'UNKNOWN'}）。")
            if self.poll_interval > 0:
                time.sleep(self.poll_interval)

    # ---- HTTP 原语（错误一律翻译成既有 LLMError 家族，绝不让 urllib 异常上浮）----
    def _post_json(self, path: str, body: Dict[str, Any], timeout: float,
                   *, async_submit: bool) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if async_submit:
            headers["X-DashScope-Async"] = "enable"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return self._request("POST", self.base_url + path, data, headers, timeout)

    def _get_json(self, url: str, timeout: float) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        return self._request("GET", url, None, headers, timeout)

    def _request(self, method: str, url: str, data: Optional[bytes],
                 headers: Dict[str, str], timeout: float) -> Dict[str, Any]:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            raise _translate_http_error(e, url) from e
        except socket.timeout as e:
            raise LLMTimeoutError(f"百炼图像请求超时（{timeout}s，{url}）。") from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), socket.timeout):
                raise LLMTimeoutError(f"百炼图像请求超时（{timeout}s，{url}）。") from e
            raise LLMConnectionError(f"无法连接百炼图像端点：{url}（{e.reason}）。") from e
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ImageGenerationError(
                f"百炼图像响应不是合法 JSON（HTTP 200 但无法解析，{url}）。") from e
        if not isinstance(payload, dict):
            raise ImageGenerationError(f"百炼图像响应不是 JSON 对象（{url}）。")
        # 业务层错误码（HTTP 200 但 status_code != 200 / code 非空）
        code = str(payload.get("code") or "")
        status_code = payload.get("status_code")
        if code or (isinstance(status_code, int) and status_code >= 400):
            msg = str(payload.get("message") or code or status_code)
            if isinstance(status_code, int) and 500 <= status_code < 600:
                raise LLMInternalError(f"百炼服务端错误（{status_code}）：{msg}")
            raise LLMRequestError(f"百炼图像请求被拒绝（{code or status_code}）：{msg}")
        return payload

    def __repr__(self) -> str:  # pragma: no cover - 仅调试友好
        return (f"{type(self).__name__}(kind={self.kind!r}, vendor={self.vendor!r}, "
                f"model={self.model!r}, async_mode={self.async_mode!r})")


def _translate_http_error(e: urllib.error.HTTPError, url: str) -> LLMError:
    """HTTP 状态码 → 既有 LLMError 家族（可区分来源、retryable 语义沿用既有类）。"""
    code = int(getattr(e, "code", 0) or 0)
    detail = ""
    try:
        detail = (e.read() or b"").decode("utf-8", errors="replace")[:300]
    except Exception:  # pragma: no cover - 读取失败不影响判定
        detail = ""
    if code in (401, 403):
        return LLMAuthenticationError(f"百炼鉴权失败（HTTP {code}）：请检查 BAILIAN_API_KEY。")
    if code == 429:
        return LLMRateLimitError(f"百炼限流（HTTP 429）：{detail}")
    if 500 <= code < 600:
        return LLMInternalError(f"百炼服务端错误（HTTP {code}）：{detail}")
    return LLMRequestError(f"百炼图像请求错误（HTTP {code}，{url}）：{detail}")


def _extract_reference(payload: Dict[str, Any]) -> Tuple[str, TokenUsage]:
    """百炼响应 → (引用串, usage)。

    主路径（官方文档，同步/异步一致）：
        output.choices[0].message.content[0].image  → URL
    兼容兜底（厂商演进 / 组图形态）：output.results[0].url、output.url、output.image
    ★ 出现**多张**图 → 明确报错，绝不静默丢图（本契约一次只承载一张）。
    """
    out = payload.get("output")
    if not isinstance(out, dict):
        raise ImageGenerationError("百炼图像响应缺少 output 字段。")

    urls = _collect_image_urls(out)
    if not urls:
        raise ImageGenerationError(
            "百炼图像响应里没有可用的图片 URL"
            "（期望 output.choices[0].message.content[0].image）。")
    if len(urls) > 1:
        raise ImageGenerationError(
            f"百炼图像响应返回了 {len(urls)} 张图，但本契约一次只承载一张："
            f"请把 parameters.n 设为 1（当前实现固定 n=1，若厂商默认返回多张请显式指定）。")
    return urls[0], _usage_from_payload(payload)


def _collect_image_urls(out: Dict[str, Any]) -> list:
    """从 output 里收集全部图片 URL（顺序：官方主路径 → 兜底路径）。"""
    urls: list = []
    choices = out.get("choices")
    if isinstance(choices, list):
        for ch in choices:
            msg = (ch or {}).get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("image"):
                        urls.append(str(part["image"]))
            elif isinstance(content, str) and content.startswith("http"):
                urls.append(content)
    results = out.get("results")
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("url"):
                urls.append(str(r["url"]))
    for key in ("url", "image"):
        if out.get(key):
            urls.append(str(out[key]))
    # 去重但保序（同一 URL 重复出现不算"多张"）
    seen, uniq = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def _usage_from_payload(payload: Dict[str, Any]) -> TokenUsage:
    """百炼 usage → TokenUsage（拿得到就记，拿不到记 0；不编造）。"""
    u = payload.get("usage")
    if not isinstance(u, dict):
        return TokenUsage()
    def _int(v: Any) -> int:
        try:
            return int(v or 0)
        except (TypeError, ValueError):
            return 0
    return TokenUsage(
        input_tokens=_int(u.get("input_tokens")),
        output_tokens=_int(u.get("output_tokens")),
        total_tokens=_int(u.get("total_tokens")),
    )


__all__ = [
    "BailianImageProvider",
    "ImageTransportFn",
    "DEFAULT_SYNC_PATH",
    "DEFAULT_CREATE_PATH",
    "DEFAULT_TASK_PATH",
]
