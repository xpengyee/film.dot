# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Memory 数据模型（v2/memory/models.py）

作用：定义 Memory 的"事实"数据结构。事实分三态：
    candidate（候选）-> approved（已确认）或 rejected（已拒绝）

设计来源（架构报告 §6.5 + 第四十七章问题一）：
    Memory 分为"事实层"与"摘要层"。
    known_facts（结构化事实）是高可信 Source of Truth，优先级高于 LLM 摘要。
    但事实必须经人工确认才能成为 approved，绝不把 candidate 直接当 approved
    （问题一结论：known_facts 每次写入都必须有人工确认或规则兜底）。

    candidate / approved / rejected 三态是分开的，不能混在一起。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（毫秒精度）。Memory 用（与 workflow 同格式）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_fact_id() -> str:
    """生成一个短事实 ID（如 fact-9f2c41d0ab），用于唯一标识一条事实。"""
    return f"fact-{uuid.uuid4().hex[:10]}"


class FactStatus:
    """事实生命周期状态（字符串常量，便于序列化 / 比对）。"""

    CANDIDATE = "candidate"   # LLM / Agent 提出，还没确认
    APPROVED = "approved"     # 人工确认，成为项目级高可信事实
    REJECTED = "rejected"     # 人工拒绝，不进入 approved 集合


@dataclass
class Fact:
    """一条结构化事实。

    例：content = "Theo left the apartment"
        category = "plot"（剧情）| "character"（角色）| "world"（世界观）| "general"
        source   = 谁提出的："memory_writer"（LLM 提取）| "human"（人工补充）
    """

    fact_id: str
    content: str
    status: str = FactStatus.CANDIDATE
    category: str = "general"
    source: str = ""
    created_at: str = ""
    reviewed_at: str = ""
    reviewer: str = ""
    comment: str = ""

    @property
    def is_approved(self) -> bool:
        return self.status == FactStatus.APPROVED

    def to_dict(self) -> dict:
        return {
            "fact_id": self.fact_id,
            "content": self.content,
            "status": self.status,
            "category": self.category,
            "source": self.source,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "comment": self.comment,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Fact":
        return cls(
            fact_id=str(data["fact_id"]),
            content=str(data.get("content", "")),
            status=str(data.get("status", FactStatus.CANDIDATE)),
            category=str(data.get("category", "general")),
            source=str(data.get("source", "")),
            created_at=str(data.get("created_at", "")),
            reviewed_at=str(data.get("reviewed_at", "")),
            reviewer=str(data.get("reviewer", "")),
            comment=str(data.get("comment", "")),
        )


__all__ = ["now_iso", "new_fact_id", "FactStatus", "Fact"]
