# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Context 数据模型（v2/context/models.py）

作用：定义"组装出来的上下文"的数据形态。

设计要点（指令 §八 / §九 / §十）：
    1. Context 是"按 Step 声明的 needs 取货"的产物，不是把整部剧都塞进去。
    2. Context 必须可追踪：每一条内容都带来源信息
       （source / source_type / source_id / version），
       未来回答"为什么这个 Agent 得到这些信息"。
    3. char_count 记录每条内容的字符规模——它是"上下文裁剪"的度量之一
       （不替代 LLMClient 的 token 统计；token 归 token_log 管）。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_context_id() -> str:
    """生成一个短 context 标识（一次组装一个）。"""
    return f"ctx-{uuid.uuid4().hex[:10]}"


def char_count(data: Any) -> int:
    """估算一段数据的字符规模（紧凑 JSON 长度；不可序列化时退回 str）。"""
    try:
        return len(json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(data))


@dataclass
class ContextSource:
    """一条 Context 内容的来源（指令 §十 的可追踪要求）。"""

    source: str         # "memory" | "asset" | "workflow" | "project"
    source_type: str    # "approved_fact" | "candidate_fact" | "character" | "world"
                        # "history" | "artifact" | "workflow_state" | "location" | "action" | "shot"
                        # "project_info"
    source_id: str      # 具体 ID / 文件名（如 fact_id、artifact 文件名、project_id）
    version: str        # 版本（如 "rev12"、"v3"、"1"）

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "version": self.version,
        }


@dataclass
class ContextItem:
    """组装出来的一个条目：key 是 Step 声明的 needs 名。"""

    key: str
    data: Any
    source: ContextSource
    char_count: int = 0

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "data": self.data,
            "source": self.source.to_dict(),
            "char_count": self.char_count,
        }


@dataclass
class AssembledContext:
    """一次组装的结果：给一个 Step 的完整 Context。"""

    context_id: str
    project_id: str
    step_id: str
    items: List[ContextItem] = field(default_factory=list)
    created_at: str = ""

    def item(self, key: str) -> Optional[ContextItem]:
        """按 needs 名取某一条；没有返回 None。"""
        for it in self.items:
            if it.key == key:
                return it
        return None

    def keys(self) -> List[str]:
        return [it.key for it in self.items]

    def total_chars(self) -> int:
        return sum(it.char_count for it in self.items)

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "project_id": self.project_id,
            "step_id": self.step_id,
            "items": [it.to_dict() for it in self.items],
            "total_chars": self.total_chars(),
            "created_at": self.created_at,
        }

    def to_prompt_text(self) -> str:
        """把 Context 拼成一段可读文本（未来塞进 Prompt 用；本阶段仅提供能力）。"""
        blocks = []
        for it in self.items:
            blocks.append(f"## {it.key}\n{_fmt(it.data)}")
        return "\n\n".join(blocks)


def _fmt(data: Any) -> str:
    if isinstance(data, str):
        return data
    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
        return "\n".join(f"- {x}" for x in data)
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(data)


__all__ = ["now_iso", "new_context_id", "char_count", "ContextSource", "ContextItem", "AssembledContext"]
