# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— 人工审核数据模型（v2/review/models.py）

作用：定义"人工审核"的数据形态。

设计来源（架构报告 §二十一 + 第四十七章问题四 + 指令 §十一 / §十二 / §十三）：
    审核至少支持三种结果：
        approved            原始产出直接通过
        approved_with_edit  人工修改后通过（必须真正产生修改后的新产物，不覆盖原始）
        rejected            当前产出不能采用

    审核记录必须能回答：
        原始版本是什么？谁审核的？什么时候审核？改成了什么？最终采用哪一版？
    （即 original_artifact / reviewer / reviewed_at / edited_artifact / comment）

ReviewResult 是给 Workflow Engine 的"审核决策"（decision + 编辑内容），
Review 是完整持久化记录（含原始/修改产物引用）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from workflow.models import ReviewDecision  # 审核决策常量在 workflow 层定义（review→workflow 是允许的依赖方向）


def now_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（毫秒精度）。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_review_id() -> str:
    """生成一个短审核标识（如 review-7c1a9032be）。"""
    return f"review-{uuid.uuid4().hex[:10]}"


class ReviewStatus:
    """审核记录的状态（字符串常量）。"""

    PENDING = "PENDING"                   # 已提交，等待人工
    APPROVED = "APPROVED"                 # 人工确认通过
    APPROVED_WITH_EDIT = "APPROVED_WITH_EDIT"  # 人工修改后通过
    REJECTED = "REJECTED"                 # 人工拒绝


@dataclass
class Review:
    """一条审核记录。"""

    review_id: str
    artifact_id: str                    # 被审核的原始 Artifact 的 artifact_id
    original_artifact: dict             # 原始 Artifact.to_dict()（不覆盖，原样保留）
    reviewer: str = "human"
    status: str = ReviewStatus.PENDING
    submitted_at: str = ""
    reviewed_at: str = ""
    edited_artifact: Optional[dict] = None  # approved_with_edit 产生的新 Artifact.to_dict()
    comment: str = ""                       # 审核备注
    reason: str = ""                        # rejected 时的原因

    def to_dict(self) -> dict:
        return {
            "review_id": self.review_id,
            "artifact_id": self.artifact_id,
            "original_artifact": self.original_artifact,
            "reviewer": self.reviewer,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "reviewed_at": self.reviewed_at,
            "edited_artifact": self.edited_artifact,
            "comment": self.comment,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Review":
        return cls(
            review_id=str(data["review_id"]),
            artifact_id=str(data.get("artifact_id", "")),
            original_artifact=dict(data.get("original_artifact") or {}),
            reviewer=str(data.get("reviewer", "human")),
            status=str(data.get("status", ReviewStatus.PENDING)),
            submitted_at=str(data.get("submitted_at", "")),
            reviewed_at=str(data.get("reviewed_at", "")),
            edited_artifact=dict(data["edited_artifact"]) if data.get("edited_artifact") else None,
            comment=str(data.get("comment", "")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class ReviewResult:
    """审核决策（给 Workflow Engine 用，同步返回继续/停止）。"""

    decision: str                       # ReviewDecision 之一
    review_id: str
    edited_content: Any = None          # approved_with_edit 时人工修改后的内容
    edited_artifact: Optional[dict] = None  # approved_with_edit 时已写盘的编辑产物
                                            # (Artifact.to_dict())；Engine 直接采用，避免重复写盘
    reviewer: str = "human"
    comment: str = ""


__all__ = [
    "now_iso",
    "new_review_id",
    "ReviewStatus",
    "ReviewDecision",
    "Review",
    "ReviewResult",
]
