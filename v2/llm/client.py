# -*- coding: utf-8 -*-
"""
V0.2 阶段 2 —— LLM 调用客户端（v2/llm/client.py）

作用：把 V0.1 的 LLMCaller 升级成稳定、可统计、可替换的 V0.2 LLMClient。

V0.1 LLMCaller 的问题（本阶段解决）：
    1. 只支持单条 user 消息，messages 不能成为正式接口。
    2. 没有超时，一次请求可能无限挂死。
    3. 任何异常都盲目重试（while counter<3），不区分"可重试/不可重试"。
    4. 失败返回假字符串 "api调用失败"，不抛异常，上层还以为成功了。
    5. 没有 token / 成本统计，没有调用日志。

V0.2 LLMClient 的定位（开工指令 §十二）：
    LLMClient 不应该知道业务。它只负责：调用、重试、超时、统计、日志、错误处理。
    它不知道"自己正在写什么剧本"。
    Agent / Workflow 在上层；DeepSeek API 在下层；LLMClient 只是中间的"电话总机"。

关键机制：
    - chat(messages, ...) 支持 system/user/assistant 多条消息。
    - request_id：每次调用一个 UUID，把 prompt/response/token/cost/error/retry 串起来。
    - 重试：只重试"可重试"错误（超时/连接/限流/5xx），不盲目重试。
    - transport 可注入：默认用 OpenAI 兼容库，测试可用 mock 替代（避免浪费真实 API）。
    - 每次调用写一条 JSONL 日志（含 retry_count、成功/失败、错误）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from core.token_log import (
    TokenUsage, TokenLogger, LLMCallRecord,
    estimate_cost, compute_prompt_hash, new_request_id, now_iso,
)


# ---------------------------------------------------------------------------
# 错误设计：把 SDK 异常统一转成 V0.2 自己的异常，不泄漏给上层业务
# ---------------------------------------------------------------------------
class LLMError(Exception):
    """所有 LLM 调用错误的基类。retryable=True 表示值得重试。"""
    retryable: bool = False

    def __init__(self, message: str, *, retryable: Optional[bool] = None):
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class LLMTimeoutError(LLMError):
    """请求超时。可重试。"""
    retryable = True


class LLMConnectionError(LLMError):
    """网络连接失败。可重试。"""
    retryable = True


class LLMRateLimitError(LLMError):
    """触发限流（429）。可重试（稍等再试通常有用）。"""
    retryable = True


class LLMInternalError(LLMError):
    """服务端 5xx。可重试。"""
    retryable = True


class LLMRequestError(LLMError):
    """请求参数错误（4xx，非限流/非认证）。不可重试——重试也没用。"""
    retryable = False


class LLMAuthenticationError(LLMError):
    """API key 无效 / 权限不足。不可重试。"""
    retryable = False


# ---------------------------------------------------------------------------
# 返回值设计
# ---------------------------------------------------------------------------
@dataclass
class LLMResponse:
    """统一返回值：Agent 不需要自己解析 API response。"""
    text: str
    model: str
    usage: TokenUsage
    request_id: str
    duration_ms: int
    retry_count: int = 0

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "model": self.model,
            "usage": self.usage.to_dict(),
            "request_id": self.request_id,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
        }


# transport 类型：输入参数，输出 (文本, usage对象)
TransportFn = Callable[[List[dict], str, Optional[float], Optional[int], float], tuple[str, TokenUsage]]


# ---------------------------------------------------------------------------
# LLMClient
# ---------------------------------------------------------------------------
class LLMClient:
    """统一 LLM 调用客户端。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str = "deepseek-chat",
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        logger: Optional[TokenLogger] = None,
        transport: Optional[TransportFn] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.logger = logger or TokenLogger(Path("."))
        self._transport: TransportFn = transport or self._default_transport
        # Stage 9 增补：
        self._uses_default_transport = transport is None   # 是否在用内置 openai 传输
        # 结构化输出透传开关。默认 False（"后端未确认支持时不送"），Provider 可按需打开。
        self.structured_supported = False

    # ---------------- 对外主接口 ----------------
    def chat(
        self,
        messages: List[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        step: str = "",
        agent: str = "",
        structured: Optional[dict] = None,
    ) -> LLMResponse:
        """发起一次 chat 调用。

        messages 支持：
            [{"role": "system", "content": "..."},
             {"role": "user", "content": "..."},
             {"role": "assistant", "content": "..."},
             {"role": "user", "content": "..."}]

        step / agent 只是调用上下文标签（进日志用），
        LLMClient 本身不关心业务含义。

        structured（Stage 9 增补）：可选 dict，表示"尽力让后端返回结构化"。
            默认 None，行为与之前完全一致。仅当 structured_supported=True 且用的是
            内置 openai 传输时，才会把 structured 作为 response_format 透传给后端；
            否则（未接线 / 注入自定义 transport）静默忽略、不报错（见 _invoke）。
        """
        request_id = new_request_id()
        started = time.monotonic()
        eff_model = model or self.model
        eff_timeout = timeout if timeout is not None else self.timeout
        attempts = 0
        max_attempts = self.max_retries + 1
        last_error: Optional[LLMError] = None

        while attempts < max_attempts:
            try:
                text, usage = self._invoke(messages, eff_model, temperature, max_tokens,
                                           eff_timeout, structured)
                duration_ms = int((time.monotonic() - started) * 1000)
                cost = estimate_cost(usage, _pricing_for(eff_model))
                self._log(request_id, step, agent, eff_model, usage, cost, duration_ms,
                          success=True, retry_count=attempts, error=None, messages=messages)
                return LLMResponse(text=text, model=eff_model, usage=usage,
                                   request_id=request_id, duration_ms=duration_ms,
                                   retry_count=attempts)
            except LLMError as exc:
                last_error = exc
                attempts += 1
                if not exc.retryable or attempts >= max_attempts:
                    break
                # 指数退避：0.5s, 1s, 2s ...（测试可设 0）
                backoff = self.retry_backoff_seconds * (2 ** (attempts - 1))
                if backoff > 0:
                    time.sleep(backoff)

        # ---- 最终失败 ----
        # retry_count = 实际发生的重试次数 = 总尝试次数 - 1。
        # 例如：第一次就因"参数错误"(不可重试)失败 -> 0 次重试；
        #       超时重试 2 次后仍失败 -> 2 次重试。
        assert last_error is not None
        duration_ms = int((time.monotonic() - started) * 1000)
        self._log(request_id, step, agent, eff_model, None, None, duration_ms,
                  success=False, retry_count=attempts - 1,
                  error=f"{type(last_error).__name__}: {last_error}", messages=messages)
        raise last_error

    # ---------------- 日志 ----------------
    def _log(self, request_id: str, step: str, agent: str, model: str,
             usage: Optional[TokenUsage], cost: Optional[float], duration_ms: int,
             success: bool, retry_count: int, error: Optional[str], messages: List[dict]) -> None:
        usage = usage or TokenUsage()
        pricing = _pricing_for(model)
        rec = LLMCallRecord(
            timestamp=now_iso(),
            request_id=request_id,
            step=step,
            agent=agent,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            estimated_cost=cost,
            currency=(pricing or {}).get("currency", "") if pricing else "",
            duration_ms=duration_ms,
            success=success,
            retry_count=retry_count,
            error=error,
            prompt_hash=compute_prompt_hash(messages),
            prompt_length=sum(len(m.get("content", "") or "") for m in messages),
            message_count=len(messages),
        )
        self.logger.record(rec)

    def _invoke(self, messages: List[dict], model: str,
                temperature: Optional[float], max_tokens: Optional[int],
                timeout: float, structured: Optional[dict]) -> tuple[str, TokenUsage]:
        """统一调用点（Stage 9）：决定 structured 走哪条传输路径。

        规则（stage9_plan §12 三层边界 + §6.1）：
            - structured 只在"确认后端支持（structured_supported）且在用内置 openai
              传输"时才真正透传（作为 response_format）；
            - 否则走常规 transport 调用；structured 被静默忽略、不报错
              —— 这是"后端未接入时忽略，绝不让 Provider 越权替业务校验"。
        """
        if (structured is not None and self.structured_supported
                and self._uses_default_transport):
            return self._default_transport(messages, model, temperature, max_tokens,
                                           timeout, structured=structured)
        return self._transport(messages, model, temperature, max_tokens, timeout)

    # ---------------- 真实 transport ----------------
    def _default_transport(
        self,
        messages: List[dict],
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: float,
        structured: Optional[dict] = None,
    ) -> tuple[str, TokenUsage]:
        """真正调用 DeepSeek（OpenAI 兼容接口）。

        把 openai 库的异常统一转成 V0.2 自己的 LLMError 子类，
        绝不让 SDK 异常泄漏给上层业务。

        structured（Stage 9 增补，默认 None）：非 None 时作为 response_format 透传，
        请求后端 json 模式（是否生效取决于后端实现）。
        """
        import openai  # 延迟导入：只用真实调用时才需要 openai 库

        client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)
        kwargs = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if structured is not None:
            kwargs["response_format"] = structured

        try:
            completion = client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as e:
            raise LLMTimeoutError(f"API request timed out after {timeout}s.") from e
        except openai.APIConnectionError as e:
            raise LLMConnectionError(f"Failed to connect to {self.base_url}.") from e
        except openai.RateLimitError as e:
            raise LLMRateLimitError(f"Rate limit exceeded: {e}") from e
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            raise LLMAuthenticationError(f"Authentication failed (check API key).") from e
        except openai.BadRequestError as e:
            raise LLMRequestError(f"Invalid request: {e}") from e
        except openai.APIStatusError as e:
            if 500 <= (e.status_code or 0) < 600:
                raise LLMInternalError(f"Server error (HTTP {e.status_code}).") from e
            raise LLMRequestError(f"API error (HTTP {e.status_code}): {e}") from e

        text = (completion.choices[0].message.content) or ""
        usage = TokenUsage.from_openai_usage(completion.usage)
        return text, usage


# ---------------------------------------------------------------------------
# 内部小工具：查价格配置（从 config 懒加载，避免循环导入）
# ---------------------------------------------------------------------------
def _pricing_for(model: str) -> Optional[dict]:
    try:
        from config import get_pricing
        return get_pricing(model)
    except ImportError:
        return None


__all__ = [
    "LLMError",
    "LLMTimeoutError",
    "LLMConnectionError",
    "LLMRateLimitError",
    "LLMInternalError",
    "LLMRequestError",
    "LLMAuthenticationError",
    "LLMResponse",
    "LLMClient",
]
