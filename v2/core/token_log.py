# -*- coding: utf-8 -*-
"""
V0.2 阶段 2 —— Token / Cost 记录模块（core/token_log.py）

作用：记录"每一次 LLM 调用"的账目，并做成本估算。

核心原则（开工指令 §六、§九）：
    每一次 LLM 调用都是一笔账。
    token（使用量）和 cost（金钱）必须分开，绝不能混成一个字段——
    同样 10,000 tokens，不同模型成本可能完全不同。

两个重要工具：
    1. TokenUsage：把各家 API 返回的 usage 统一成一个内部格式，
       业务代码不用关心 DeepSeek/OpenAI 的具体字段名。
       （DeepSeek 返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens，
       标准 OpenAI 返回 prompt_tokens / completion_tokens / total_tokens，
       这里都能兼容。）
    2. TokenLogger：往 v2/logs/llm_calls.jsonl 追加一行 JSON 日志。

日志安全（开工指令 §十五/§十六/§九）：
    - 绝不记录完整 API Key。
    - 不记录 prompt 正文，只记 sha256 哈希、长度、消息数，避免泄露。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Token 统计：统一内部格式
# ---------------------------------------------------------------------------
@dataclass
class TokenUsage:
    """统一 token 使用量格式。

    input_tokens / output_tokens / total_tokens 三兄弟是通用字段。
    cache_hit_tokens / cache_miss_tokens 是 DeepSeek 特有的缓存拆分
    （命中部分和未命中部分价格不同），没有则记 0。
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @classmethod
    def from_openai_usage(cls, usage: Any) -> "TokenUsage":
        """把 API 返回的 usage（对象或 dict）转成统一格式。

        兼容：
            usage.prompt_tokens / usage.completion_tokens / usage.total_tokens
            usage.prompt_cache_hit_tokens / usage.prompt_cache_miss_tokens   (DeepSeek)
        """
        if usage is None:
            return cls()
        get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))

        input_tokens = get("prompt_tokens") or 0
        output_tokens = get("completion_tokens") or 0
        total_tokens = get("total_tokens")
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens
        return cls(
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            total_tokens=int(total_tokens),
            cache_hit_tokens=int(get("prompt_cache_hit_tokens") or 0),
            cache_miss_tokens=int(get("prompt_cache_miss_tokens") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
        }


# ---------------------------------------------------------------------------
# 成本估算
# ---------------------------------------------------------------------------
def estimate_cost(usage: TokenUsage, pricing: Optional[dict]) -> Optional[float]:
    """按配置价格估算本次调用的成本（单位随 pricing.currency）。

    pricing 结构（每 1M tokens 的价格）：
        {
            "input_cache_hit": 0.10,   # 输入缓存命中
            "input_cache_miss": 3.00,  # 输入缓存未命中
            "output": 9.00,            # 输出
            "currency": "CNY",
        }

    规则：
        - pricing 缺失或价格字段不全 → 返回 None（无法估算，绝不瞎编）。
        - 有 DeepSeek 缓存拆分 → 命中/未命中分别按各自价格算。
        - 只有 input/output 总数 → 输入部分全部按"未命中"价（保守）。
    """
    if not pricing:
        return None
    hit_price = pricing.get("input_cache_hit")
    miss_price = pricing.get("input_cache_miss")
    out_price = pricing.get("output")
    if hit_price is None or miss_price is None or out_price is None:
        return None

    hit = usage.cache_hit_tokens or 0
    miss = usage.cache_miss_tokens or 0
    if hit + miss == 0:
        # 没有缓存拆分信息 → 输入全部按未命中价（保守）
        miss = usage.input_tokens
    cost = (hit / 1e6) * hit_price + (miss / 1e6) * miss_price + (usage.output_tokens / 1e6) * out_price
    return round(cost, 8)


# ---------------------------------------------------------------------------
# 一条调用日志
# ---------------------------------------------------------------------------
@dataclass
class LLMCallRecord:
    """一条 LLM 调用记录（写进 JSONL 的一行）。"""
    timestamp: str = ""
    request_id: str = ""
    step: str = ""
    agent: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: Optional[float] = None
    currency: str = ""
    duration_ms: int = 0
    success: bool = False
    retry_count: int = 0
    error: Optional[str] = None
    prompt_hash: str = ""
    prompt_length: int = 0
    message_count: int = 0

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "step": self.step,
            "agent": self.agent,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost": self.estimated_cost,
            "currency": self.currency,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "retry_count": self.retry_count,
            "error": self.error,
            "prompt_hash": self.prompt_hash,
            "prompt_length": self.prompt_length,
            "message_count": self.message_count,
        }


def compute_prompt_hash(messages: List[dict]) -> str:
    """对整个消息列表做 sha256 哈希，用于日志（不记录 prompt 正文）。"""
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def new_request_id() -> str:
    """生成唯一请求 ID（UUID）。"""
    return uuid.uuid4().hex


def now_iso() -> str:
    """当前时间的 ISO 格式字符串（本地时区）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------------------
# 日志写入
# ---------------------------------------------------------------------------
class TokenLogger:
    """把每次调用追加进 JSONL 日志文件。每行一条完整记录。"""

    def __init__(self, log_dir: Path, filename: str = "llm_calls.jsonl"):
        self.log_path = Path(log_dir) / filename
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, rec: LLMCallRecord) -> None:
        """追加一行 JSON（模式 'a' 追加，绝不覆盖历史）。"""
        line = json.dumps(rec.to_dict(), ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_all(self) -> List[dict]:
        """读回全部日志（供测试/报告用）。"""
        if not self.log_path.exists():
            return []
        records: List[dict] = []
        with self.log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records


__all__ = [
    "TokenUsage",
    "estimate_cost",
    "LLMCallRecord",
    "compute_prompt_hash",
    "new_request_id",
    "now_iso",
    "TokenLogger",
]
