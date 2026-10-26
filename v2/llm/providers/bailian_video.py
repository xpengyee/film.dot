# -*- coding: utf-8 -*-
"""
V0.2 Stage 15 —— 阿里云百炼 DashScope **原生异步**图生视频 Provider
（v2/llm/providers/bailian_video.py）

协议依据（Stage 15 §7，官方文档 2026-10-02 核实）：
    - HTTP 为**异步流程**：创建任务 → 用 task_id 轮询；
    - 创建请求需要 header `X-DashScope-Async: enable`；
    - 新版图生视频 payload 使用 `input.media`，首帧类型为 `first_frame`；
    - 时长（duration）、分辨率（resolution）直接影响费用；
    - 成功结果 `video_url` 有效期有限，必须及时下载；
    - 模型、endpoint 和 API key 必须属于同一地域。

本 Provider 是**纯新增**：不替换任何既有 Provider；与 bailian_image.py 一样走
DashScope 原生端点，但采用**显式 submit/poll 分离**（D15-03）——这是与
图像 Provider 的"单次业务调用内自轮询"最大的语义差异，因为视频昂贵、可计费，
task_id 必须在提交后立即由编排层原子落盘，进程中断后才能安全 resume。

关键纪律（§15-B3）：
    - Config 驱动 base URL / paths / model / key env / capabilities；
    - 通过 `load_provider_api_key()` 取 key（在 registry 完成，本类只收 api_key 参数），
      **不在类中硬编码环境变量名**；
    - submit 使用 `X-DashScope-Async: enable`；
    - 本地图片转 data URL 只存在于请求内存，不写日志；
    - **POST 不自动重试**（响应丢失时可能重复创建任务 = 重复扣费）；
    - poll 解析全部终态，非法/未知 Schema fail-loud；
    - transport / sleep 可注入；测试不得联网；
    - 不持久化或日志输出签名 URL；
    - URL 组合保证 `/api/v1` 恰好一次（公共域与业务空间域共用语义）。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from core.token_log import new_request_id
from llm.client import (
    LLMError,
    LLMTimeoutError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMInternalError,
    LLMRequestError,
    LLMAuthenticationError,
)
from llm.providers.base import VideoGenerationProvider, VideoTaskResult
from llm.request_counter import APIRequestCounter, get_api_counter


#: 厂商层错误（视频专用子类，仍属 LLMError 家族 —— §15-B1）
class VideoGenerationError(LLMError):
    """图生视频厂商响应/协议错误。不可重试（重试可能重复创建任务）。"""
    retryable = False


#: DashScope 原生图生视频端点（Config 可覆盖）
DEFAULT_VIDEO_CREATE_PATH = "/services/aigc/video-generation/video-synthesis"
DEFAULT_VIDEO_TASK_PATH = "/tasks/{task_id}"

#: 异步任务终态（非 SUCCEEDED 的终态一律判失败，不无限轮询）
_TERMINAL_TASK_STATUSES = {
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELED": "canceled",
    "CANCELLED": "canceled",
    "UNKNOWN": "unknown",
}
_PENDING_TASK_STATUSES = ("PENDING", "RUNNING", "SUBMITTED", "IN_PROGRESS")

#: 提交响应缺失 task_id 时视为 ambiguous（无法确认是否成功创建，禁止自动重提）
_AMBIGUOUS_SUBMIT = "ambiguous"


#: transport 契约（测试可注入 fake，全程 0 网络）
#:   提交: ("submit", method, url, headers, body_dict, timeout) -> (status_code, payload_dict)
#:   轮询: ("poll",   method, url, headers, None,     timeout) -> (status_code, payload_dict)
VideoTransportFn = Callable[[str, str, str, Dict[str, str], Optional[Dict[str, Any]], float],
                            Tuple[int, Dict[str, Any]]]


def _data_url_for_path(path: str) -> str:
    """本地图片 → data URL（仅请求内存；绝不写日志/落盘）。

    未知 MIME 回退 image/png（下游容错读取）；路径不存在/空文件由调用方校验。
    """
    p = Path(path)
    data = p.read_bytes()
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    if mime == "image/jpg":
        mime = "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


class BailianVideoProvider(VideoGenerationProvider):
    """百炼 DashScope 原生异步图生视频 Provider + VideoGenerationProvider 抽象身份。

    构造不联网；网络调用只发生在 submit_video()/get_video_task() 被调用时。
    submit 是唯一"真实提交"计数点（budget_kind='video'，硬上限 1）；poll/download
    使用不同 kind（video_poll / video_download），不占提交数但进入总审计。
    """

    kind = "video"
    vendor = "bailian_native"           # 工厂按 Config 覆盖
    budget_kind = "video"
    poll_budget_kind = "video_poll"
    download_budget_kind = "video_download"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        poll_interval: float = 3.0,
        create_path: Optional[str] = None,
        task_path: Optional[str] = None,
        transport: Optional[VideoTransportFn] = None,
        sleep: Optional[Callable[[float], None]] = None,
        vendor: Optional[str] = None,
        counter: Optional[APIRequestCounter] = None,
    ):
        if not str(model or "").strip():
            raise LLMRequestError(
                "BailianVideoProvider: model 为空。真实生成前请在 Config 的 "
                "PROVIDERS['<vendor>_video']['default_model'] 填入具体视频模型名。"
            )
        if not str(base_url or "").strip():
            raise LLMRequestError("BailianVideoProvider: base_url 为空。")
        if not str(api_key or ""):
            raise LLMRequestError("BailianVideoProvider: api_key 为空。")
        self.api_key = str(api_key)
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.timeout = float(timeout)
        self.poll_interval = max(0.0, float(poll_interval))
        self.create_path = str(create_path or DEFAULT_VIDEO_CREATE_PATH)
        self.task_path = str(task_path or DEFAULT_VIDEO_TASK_PATH)
        self._transport: VideoTransportFn = transport or self._default_transport
        self._uses_default_transport = transport is None
        self._sleep: Callable[[float], None] = sleep or time.sleep
        if vendor is not None:
            self.vendor = str(vendor)
        self.counter = counter or get_api_counter()
        self._submit_count = 0

    # ------------------------------------------------------------------
    # 提交（可能计费；不自动重试）
    # ------------------------------------------------------------------
    def submit_video(
        self,
        prompt: str,
        *,
        media: Any,
        duration: float,
        resolution: str,
        negative_prompt: str = "",
        prompt_extend: bool = False,
        watermark: bool = False,
        seed: Optional[int] = None,
    ) -> VideoTaskResult:
        text_prompt = str(prompt or "")
        if not text_prompt.strip():
            raise LLMRequestError("BailianVideoProvider.submit_video(): prompt 为空。")
        media_str = str(media or "")
        if not media_str:
            raise LLMRequestError("BailianVideoProvider.submit_video(): 首帧 media 为空。")

        self._submit_count += 1
        request_id = new_request_id()
        # ★ 真实提交计数点（硬上限 1，见 D15-04）；发送前检查预算
        self.counter.consume_real_request(
            self.budget_kind,
            provider=self.vendor,
            model=self.model,
            request_id=request_id,
            attempt=self._submit_count,
            note=f"{type(self).__name__}.submit_video",
        )

        started = time.monotonic()
        body = self._build_submit_body(
            text_prompt, media_str, duration, resolution,
            negative_prompt, prompt_extend, watermark, seed)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable",
        }
        try:
            status_code, payload = self._transport(
                "submit", "POST", self.base_url + self.create_path,
                headers, body, self.timeout)
        except LLMError as exc:
            # 提交 HTTP 失败：不自动重试；是否创建任务不明 → 由编排层判 ambiguous
            raise VideoGenerationError(
                f"百炼视频任务提交失败（{type(exc).__name__}，未自动重试）："
                f"{_safe(exc)}") from exc

        task_id = _extract_task_id(payload)
        duration_ms = int((time.monotonic() - started) * 1000)
        if not task_id:
            # 提交成功但未返回 task_id：ambiguous，编排层不得自动重提
            return VideoTaskResult(
                task_id="",
                status=_AMBIGUOUS_SUBMIT,
                model=self.model,
                request_id=request_id,
                duration_ms=duration_ms,
                retry_count=0,
                error_code="AMBIGUOUS_SUBMIT",
                error_message="提交未返回 task_id，无法确认任务是否创建（禁止自动重提）",
                usage=_usage_from_payload(payload),
            )
        # 解析提交后的初始状态（通常 PENDING/RUNNING）
        status = _map_task_status(_extract_task_status(payload), default="submitted")
        return VideoTaskResult(
            task_id=task_id,
            status=status,
            model=self.model,
            request_id=request_id,
            duration_ms=duration_ms,
            retry_count=0,
            error_code="",
            error_message="",
            usage=_usage_from_payload(payload),
        )

    # ------------------------------------------------------------------
    # 轮询（有限；可对确定瞬时错误有界重试，但受最大 poll 次数约束在编排层）
    # ------------------------------------------------------------------
    def get_video_task(self, task_id: str) -> VideoTaskResult:
        task_id = str(task_id or "")
        if not task_id:
            raise LLMRequestError("BailianVideoProvider.get_video_task(): task_id 为空。")

        request_id = new_request_id()
        # ★ 轮询计数点（video_poll，独立于提交）
        self.counter.consume_real_request(
            self.poll_budget_kind,
            provider=self.vendor,
            model=self.model,
            request_id=request_id,
            attempt=1,
            note=f"{type(self).__name__}.get_video_task",
        )

        started = time.monotonic()
        url = self.base_url + self.task_path.format(task_id=task_id)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            status_code, payload = self._transport(
                "poll", "GET", url, headers, None, self.timeout)
        except LLMError as exc:
            raise VideoGenerationError(
                f"百炼视频任务轮询失败（{type(exc).__name__}）：{_safe(exc)}") from exc

        raw_status = _extract_task_status(payload)
        mapped = _map_task_status(raw_status, default="unknown")
        duration_ms = int((time.monotonic() - started) * 1000)

        result = VideoTaskResult(
            task_id=task_id,
            status=mapped,
            model=self.model,
            request_id=request_id,
            duration_ms=duration_ms,
            retry_count=0,
            usage=_usage_from_payload(payload),
        )
        if mapped == "succeeded":
            result.video_url = _extract_video_url(payload)
            if not result.video_url:
                # SUCCEEDED 但无 video_url：Schema 非法，fail-loud（§15-B3）
                result.status = "unknown"
                result.error_code = "MISSING_VIDEO_URL"
                result.error_message = "任务 SUCCEEDED 但未返回 video_url（Schema 非法）"
        elif mapped in ("failed", "canceled", "unknown"):
            result.error_code = str(_extract_error_code(payload) or raw_status or mapped)
            result.error_message = _safe(_extract_error_message(payload))
        return result

    # ------------------------------------------------------------------
    # 请求体构造（形状照官方文档：input.media / first_frame）
    # ------------------------------------------------------------------
    def _build_submit_body(
        self,
        prompt: str,
        media: str,
        duration: float,
        resolution: str,
        negative_prompt: str,
        prompt_extend: bool,
        watermark: bool,
        seed: Optional[int],
    ) -> Dict[str, Any]:
        # 首帧：本地路径 → data URL（仅内存）；已是 data/http 则原样
        if media.startswith("data:"):
            first_frame = media
        elif media.startswith(("http://", "https://")):
            first_frame = media
        else:
            first_frame = _data_url_for_path(media)

        params: Dict[str, Any] = {
            "duration": int(round(duration)),      # 已由编排层规范化
            "resolution": str(resolution),
            "prompt_extend": bool(prompt_extend),
            "watermark": bool(watermark),
        }
        if seed is not None:
            params["seed"] = int(seed)
        return {
            "model": self.model,
            "input": {
                "prompt": prompt,
                "media": [
                    {"type": "first_frame", "data": first_frame},
                ],
                "negative_prompt": str(negative_prompt or ""),
            },
            "parameters": params,
        }

    # ------------------------------------------------------------------
    # 真实 transport（stdlib urllib；错误一律翻译成 LLMError 家族）
    # ------------------------------------------------------------------
    def _default_transport(
        self,
        phase: str,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: Optional[Dict[str, Any]],
        timeout: float,
    ) -> Tuple[int, Dict[str, Any]]:
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                status_code = int(getattr(resp, "status", 200) or 200)
        except urllib.error.HTTPError as e:
            raise _translate_http_error(e, url) from e
        except socket.timeout as e:
            raise LLMTimeoutError(f"百炼视频请求超时（{timeout}s，{url}）。") from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), socket.timeout):
                raise LLMTimeoutError(f"百炼视频请求超时（{timeout}s，{url}）。") from e
            raise LLMConnectionError(f"无法连接百炼视频端点：{url}（{e.reason}）。") from e
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise VideoGenerationError(
                f"百炼视频响应不是合法 JSON（HTTP {status_code}，{url}）。") from e
        if not isinstance(payload, dict):
            raise VideoGenerationError(f"百炼视频响应不是 JSON 对象（{url}）。")
        # 业务层错误码（HTTP 200 但 code 非空 / status_code >= 400）
        code = str(payload.get("code") or "")
        sc = payload.get("status_code")
        if code or (isinstance(sc, int) and sc >= 400):
            msg = str(payload.get("message") or code or sc)
            if isinstance(sc, int) and 500 <= sc < 600:
                raise LLMInternalError(f"百炼服务端错误（{sc}）：{msg}")
            raise LLMRequestError(f"百炼视频请求被拒绝（{code or sc}）：{msg}")
        return status_code, payload

    def __repr__(self) -> str:  # pragma: no cover - 仅调试友好
        return (f"{type(self).__name__}(kind={self.kind!r}, vendor={self.vendor!r}, "
                f"model={self.model!r})")


# ---------------------------------------------------------------------------
# 响应解析小工具（严格 Schema 解析；非法 fail-loud 由调用方记 unknown）
# ---------------------------------------------------------------------------
def _extract_task_id(payload: Dict[str, Any]) -> str:
    out = payload.get("output") or {}
    if isinstance(out, dict):
        return str(out.get("task_id") or "")
    return str(payload.get("task_id") or "")


def _extract_task_status(payload: Dict[str, Any]) -> str:
    out = payload.get("output") or {}
    if isinstance(out, dict):
        return str(out.get("task_status") or out.get("status") or "")
    return str(payload.get("task_status") or payload.get("status") or "")


def _map_task_status(raw: str, *, default: str) -> str:
    s = str(raw or "").upper()
    if s in _TERMINAL_TASK_STATUSES:
        return _TERMINAL_TASK_STATUSES[s]
    if s in _PENDING_TASK_STATUSES:
        return "running"
    if s in ("SUBMITTED",):
        return "submitted"
    if not s:
        return default
    return "unknown"


def _extract_video_url(payload: Dict[str, Any]) -> str:
    out = payload.get("output") or {}
    if isinstance(out, dict):
        for key in ("video_url", "url"):
            if out.get(key):
                return str(out[key])
        # 部分厂商用 results[0].url / output.video.url
        results = out.get("results")
        if isinstance(results, list) and results:
            r = results[0]
            if isinstance(r, dict) and r.get("url"):
                return str(r["url"])
    for key in ("video_url", "url"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _extract_error_code(payload: Dict[str, Any]) -> str:
    out = payload.get("output") or {}
    if isinstance(out, dict) and out.get("code"):
        return str(out["code"])
    return str(payload.get("code") or "")


def _extract_error_message(payload: Dict[str, Any]) -> str:
    out = payload.get("output") or {}
    if isinstance(out, dict):
        return str(out.get("message") or "")
    return str(payload.get("message") or "")


def _usage_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    u = payload.get("usage")
    if not isinstance(u, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "video_seconds"):
        if key in u:
            try:
                out[key] = int(u[key])
            except (TypeError, ValueError):
                out[key] = u[key]
    return out


def _safe(exc: BaseException) -> str:
    """脱敏错误文本：只保留异常类名与已脱敏消息；绝不拼 URL / key / 正文。"""
    msg = str(exc)
    # 拦截可能泄漏 key 的形态（Authorization 头 / sk- / Bearer）
    for secret in ("Authorization", "Bearer ", "sk-", "api_key"):
        if secret in msg:
            return type(exc).__name__
    # 截断过长消息，避免把响应正文（可能含签名 URL）带进日志
    if len(msg) > 200:
        return type(exc).__name__
    return f"{type(exc).__name__}: {msg}"


def _translate_http_error(e: urllib.error.HTTPError, url: str) -> LLMError:
    """HTTP 状态码 → 既有 LLMError 家族。detail 不含签名 URL / key。"""
    code = int(getattr(e, "code", 0) or 0)
    if code in (401, 403):
        return LLMAuthenticationError("百炼视频鉴权失败（HTTP %d）。" % code)
    if code == 429:
        return LLMRateLimitError("百炼视频限流（HTTP 429）。")
    if 500 <= code < 600:
        return LLMInternalError("百炼视频服务端错误（HTTP %d）。" % code)
    return LLMRequestError("百炼视频请求错误（HTTP %d）。" % code)


__all__ = [
    "BailianVideoProvider",
    "VideoGenerationError",
    "VideoTransportFn",
    "DEFAULT_VIDEO_CREATE_PATH",
    "DEFAULT_VIDEO_TASK_PATH",
]
