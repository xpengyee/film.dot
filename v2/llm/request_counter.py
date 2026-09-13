# -*- coding: utf-8 -*-
"""
V0.2 Stage 10-B —— 程序化 API 请求计数器 + 预算硬上限（v2/llm/request_counter.py）

作用（Stage 10-B 补充指令，硬性要求）：
    从本阶段起第一次真实调用 Vision API。所有真实 API 请求必须：
        由程序自动计数、记录、并可在测试后核对。
    严禁只依赖人工统计。在计数器 + 预算保护 + Mock 测试完成之前，
    不允许执行任何真实 Vision 请求。

核心语义（补充指令 §2/§7/§10/§11 的落地答案）：
    1. 唯一"真实请求计数点" = 真实 Provider 的传输出口（_invoke：每个 HTTP
       attempt 恰好经过一次）。计数顺序 = 检查预算 → 计数 +1 → 真正发送。
       Agent / Workflow / 测试脚本绝不计数真实请求（不猜测）。
       本地校验失败（没发 HTTP）不计入 real_api_requests。
    2. Retry 也消耗预算：一次业务调用重试 N 次 = real_api_requests +N
       （按实际 HTTP 请求次数计，不按业务任务数计）。
    3. Real 与 Mock 分开：real_api_requests（真实传输出口计）与
       mock_requests（CountingProviderWrapper 在调用边界计，Mock 没有 HTTP，
       每次调用即一次"请求"）两本账。真实请求绝不用 Wrapper 计（防重复计数）。
    4. 预算硬上限：MAX_REAL_VISION_REQUESTS = 3。check/consume 原子执行，
       达到上限立即拒绝（APIBudgetExceededError，不可重试），绝不发出第 4 次。
    5. 审计最小集：timestamp / provider / model / modality / request_id /
       attempt / is_real。绝不记录 API key、prompt 正文、图片内容。
    6. 与成本分离：request count 在这里；token/cost 仍走既有 TokenLogger
       （tokens>0 不推断请求数，请求数不推断成本）。两套口径互不掺和。
    7. 线程安全：全部计数在 threading.Lock 内完成（check+count 原子，
       无 TOCTOU）。不是裸全局 += 。
    8. 可复用：按 kind（text/vision/multimodal/embedding/...）分桶记账，
       预算按 kind 配置（vision=3，其余默认不限）。不为未来重构 Provider，
       只提供最小可复用计数能力。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from core.token_log import now_iso
from llm.client import LLMError

# ---------------------------------------------------------------------------
# 预算常量（本阶段硬上限）
# ---------------------------------------------------------------------------
MAX_REAL_VISION_REQUESTS = 3
"""Stage 10-B 真实 Vision API 请求硬上限（含 retry，按实际 HTTP 请求次数计）。"""

DEFAULT_REAL_BUDGETS: Dict[str, Optional[int]] = {"vision": MAX_REAL_VISION_REQUESTS}
"""各模态真实请求默认预算；None = 不限（未来模态按需加，不重构）。"""


class APIBudgetExceededError(LLMError):
    """真实 API 请求预算已用尽。不可重试——重试只会再次被拒。"""
    retryable = False

    def __init__(self, kind: str, max_requests: int):
        self.kind = kind
        self.max_requests = max_requests
        super().__init__(
            f"Real {kind} API request budget exceeded: max={max_requests}"
        )


# ---------------------------------------------------------------------------
# 计数器
# ---------------------------------------------------------------------------
class APIRequestCounter:
    """线程安全的 API 请求计数器（real / mock 两本账 + 预算硬上限 + 审计）。

    用法（真实 Provider 传输出口，Stage 10-B 唯一真实计数点）：
        counter.consume_real_request(kind="vision", provider=..., model=...,
                                     request_id=..., attempt=n)
        —— 原子完成"检查预算 → 计数 → 审计"；超限抛 APIBudgetExceededError。

    用法（Mock，经 CountingProviderWrapper）：
        counter.count_mock_request(kind="vision", provider="mock", model=...)
    """

    def __init__(self, *, real_budgets: Optional[Dict[str, Optional[int]]] = None,
                 audit_capacity: int = 1000):
        self._lock = threading.Lock()
        self._real_budgets: Dict[str, Optional[int]] = dict(DEFAULT_REAL_BUDGETS)
        if real_budgets:
            self._real_budgets.update(real_budgets)
        # 分桶计数：{kind: {"real": n, "mock": m, "logical": k}}
        self._by_kind: Dict[str, Dict[str, int]] = {}
        self._audit: List[dict] = []           # 最小审计记录（不含 key / prompt / 图片）
        self._audit_capacity = int(audit_capacity)
        self._budget_rejections = 0            # 被预算拒绝的次数（含第 4 次尝试）

    # ---------------- 内部 ----------------
    def _bucket(self, kind: str) -> Dict[str, int]:
        if kind not in self._by_kind:
            self._by_kind[kind] = {"real": 0, "mock": 0, "logical": 0}
        return self._by_kind[kind]

    # ---------------- 真实请求（唯一真实计数点调用） ----------------
    def consume_real_request(self, kind: str, *, provider: str = "", model: str = "",
                             request_id: str = "", attempt: int = 0,
                             note: str = "") -> int:
        """真实请求计数点：检查预算 → 计数 +1 → 写审计（原子）。

        必须在"真正发起 HTTP 请求"之前、且紧贴发送处调用；本地校验失败
        （没发 HTTP）不得调用本方法。attempt 从 1 计（1 = 首次请求，
        2..n = retry）；attempt==1 同时计一次 logical_call（业务调用）。

        :returns: 该 kind 的 real 计数（计数后）
        :raises APIBudgetExceededError: 该 kind 真实请求已达预算上限。
        """
        with self._lock:
            kind = str(kind or "unknown")
            budget = self._real_budgets.get(kind)
            bucket = self._bucket(kind)
            if budget is not None and bucket["real"] >= int(budget):
                self._budget_rejections += 1
                raise APIBudgetExceededError(kind, int(budget))
            bucket["real"] += 1
            if int(attempt) <= 1:
                bucket["logical"] += 1
            self._audit.append({
                "timestamp": now_iso(),
                "provider": str(provider),
                "model": str(model),
                "modality": kind,
                "request_id": str(request_id),
                "attempt": int(attempt),
                "is_real": True,
                "note": str(note or ""),
            })
            self._trim_audit_locked()
            return bucket["real"]

    # ---------------- Mock 请求（Wrapper 调用边界计） ----------------
    def count_mock_request(self, kind: str, *, provider: str = "", model: str = "",
                           request_id: str = "", note: str = "") -> int:
        """Mock 请求计数：Mock 没有 HTTP，一次 Provider 调用即一次"请求"。

        只允许 Wrapper 对 mock（is_real=False）计数；真实请求绝不走这里
        （真实唯一计数点在真实 Provider 传输出口，防重复计数）。
        """
        with self._lock:
            kind = str(kind or "unknown")
            bucket = self._bucket(kind)
            bucket["mock"] += 1
            bucket["logical"] += 1
            self._audit.append({
                "timestamp": now_iso(),
                "provider": str(provider),
                "model": str(model),
                "modality": kind,
                "request_id": str(request_id),
                "attempt": 1,
                "is_real": False,
                "note": str(note or ""),
            })
            self._trim_audit_locked()
            return bucket["mock"]

    def _trim_audit_locked(self) -> None:
        if len(self._audit) > self._audit_capacity:
            del self._audit[: len(self._audit) - self._audit_capacity]

    # ---------------- 查询 / 快照（测试与报告用） ----------------
    def real_requests(self, kind: Optional[str] = None) -> int:
        with self._lock:
            if kind is None:
                return sum(b["real"] for b in self._by_kind.values())
            return self._by_kind.get(kind, {}).get("real", 0)

    def mock_requests(self, kind: Optional[str] = None) -> int:
        with self._lock:
            if kind is None:
                return sum(b["mock"] for b in self._by_kind.values())
            return self._by_kind.get(kind, {}).get("mock", 0)

    def remaining_budget(self, kind: str) -> Optional[int]:
        """该 kind 剩余真实请求额度；None = 不限。"""
        with self._lock:
            budget = self._real_budgets.get(kind)
            if budget is None:
                return None
            return max(0, int(budget) - self._bucket(kind)["real"])

    def check_budget(self, kind: str) -> None:
        """只检查不计数（fail-fast 用）；超限抛 APIBudgetExceededError。"""
        with self._lock:
            budget = self._real_budgets.get(kind)
            if budget is not None and self._bucket(kind)["real"] >= int(budget):
                self._budget_rejections += 1
                raise APIBudgetExceededError(kind, int(budget))

    def audit_records(self) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._audit]

    def snapshot(self) -> dict:
        """程序化账目快照（最终报告直接引用，不许人工数数）。"""
        with self._lock:
            real = sum(b["real"] for b in self._by_kind.values())
            mock = sum(b["mock"] for b in self._by_kind.values())
            logical = sum(b["logical"] for b in self._by_kind.values())
            return {
                "real_api_requests": real,
                "mock_requests": mock,
                "logical_calls": logical,
                "retries": max(0, real - logical),
                "budget_rejections": self._budget_rejections,
                "by_kind": {k: dict(v) for k, v in sorted(self._by_kind.items())},
                "budget": dict(self._real_budgets),
                "audit": [dict(r) for r in self._audit],
            }


# ---------------------------------------------------------------------------
# Mock 计数 Wrapper（Mock 的唯一计数点）
# ---------------------------------------------------------------------------
class CountingProviderWrapper:
    """给任意 Provider 包一层，在"调用边界"程序化计数（Mock 专用）。

    为什么存在：mock.py 是 Stage 9 封存实现（0 改），Mock 又没有 HTTP 可挂
    计数点；Mock 的"一次请求"就是一次 Provider 调用，因此在调用边界观察
    计数——这是观察每一次真实发生的调用，不是猜测。

    铁律：本 Wrapper 只记 is_real=False（mock 账）。真实请求唯一计数点在
    真实 Provider 的传输出口（openai_compatible_vision._invoke），两者绝不
    叠加计数。未知方法/属性全部透传给被包对象。
    """

    # 需要计数的 Provider 调用方法（与 providers/base.py 接口族一一对应）
    _COUNTED = ("chat", "understand", "embed",
                "generate_image", "generate_video", "synthesize")

    def __init__(self, inner: Any, *, counter: Optional[APIRequestCounter] = None,
                 kind: Optional[str] = None, note: str = ""):
        self._inner = inner
        self._counter = counter or get_api_counter()
        self._kind = kind or str(getattr(inner, "kind", "") or "unknown")
        self._note = note

    def __getattr__(self, name: str):  # 仅在常规属性缺失时触发，无递归风险
        return getattr(self._inner, name)

    def _count_and_call(self, method: str, args, kwargs):
        self._counter.count_mock_request(
            self._kind,
            provider=str(getattr(self._inner, "vendor", "")),
            model=str(getattr(self._inner, "model", "")),
            note=self._note or f"wrapper:{method}",
        )
        return getattr(self._inner, method)(*args, **kwargs)

    def chat(self, *args, **kwargs):
        return self._count_and_call("chat", args, kwargs)

    def understand(self, *args, **kwargs):
        return self._count_and_call("understand", args, kwargs)

    def embed(self, *args, **kwargs):
        return self._count_and_call("embed", args, kwargs)

    def generate_image(self, *args, **kwargs):
        return self._count_and_call("generate_image", args, kwargs)

    def generate_video(self, *args, **kwargs):
        return self._count_and_call("generate_video", args, kwargs)

    def synthesize(self, *args, **kwargs):
        return self._count_and_call("synthesize", args, kwargs)


# ---------------------------------------------------------------------------
# 进程级默认计数器（单例；测试/脚本可注入替换或 reset）
# ---------------------------------------------------------------------------
_counter_lock = threading.Lock()
_default_counter: Optional[APIRequestCounter] = None


def get_api_counter() -> APIRequestCounter:
    """取进程级默认计数器（首次调用惰性创建）。"""
    global _default_counter
    with _counter_lock:
        if _default_counter is None:
            _default_counter = APIRequestCounter()
        return _default_counter


def set_api_counter(counter: Optional[APIRequestCounter]) -> Optional[APIRequestCounter]:
    """替换默认计数器（测试/真实脚本注入独立账本用）。返回旧实例。"""
    global _default_counter
    with _counter_lock:
        old = _default_counter
        _default_counter = counter
        return old


def reset_api_counter(**kw) -> APIRequestCounter:
    """重建默认计数器（清零）。测试 setup / 真实脚本开头用。"""
    return set_api_counter(APIRequestCounter(**kw))


__all__ = [
    "MAX_REAL_VISION_REQUESTS",
    "DEFAULT_REAL_BUDGETS",
    "APIBudgetExceededError",
    "APIRequestCounter",
    "CountingProviderWrapper",
    "get_api_counter",
    "set_api_counter",
    "reset_api_counter",
]
