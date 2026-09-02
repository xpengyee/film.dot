# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— 人工审核服务（v2/review/service.py）

作用：实现"提交审核 -> 人工决定 -> 记录结果"的最小可用闭环（指令 §十六）。
    submit_for_review(artifact)
    approve(review_id, comment)
    approve_with_edit(review_id, edited_content, comment)
    reject(review_id, reason)

设计要点（指令 §十二 / §十三 / §十六）：
    1. 审核复用阶段 3 的 Artifact 版本机制，不重新造一套版本系统。
    2. approve_with_edit 必须真正产生"修改后的新产物"：
       用 ArtifactStore.save() 写一个更新的版本文件（scenes_5_v4.json），
       原始文件（scenes_5_v3.json）原封不动。绝不"打开原文件直接覆盖"。
    3. 每条审核记录独立持久化到 v2/projects/<project_id>/reviews/<review_id>.json，
       与原始/修改产物都能对上，可回答"谁/何时/改成什么/采用哪版"。

本阶段人工审核是明确的 Python/API 层操作，绝不调用 LLM 冒充审核。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from config import V2_ROOT
from core.io import read_json, write_json
from workflow.artifact import Artifact, ArtifactStore
from review.models import (
    Review,
    ReviewStatus,
    ReviewDecision,
    ReviewResult,
    now_iso,
    new_review_id,
)


class ReviewService:
    """一个项目的审核服务：提交 / 批准 / 批准并修改 / 拒绝，全部持久化。"""

    def __init__(self, project_id: str, store: ArtifactStore, projects_root: Optional[Path] = None):
        self.project_id = project_id
        self.store = store
        self.projects_root = Path(projects_root) if projects_root else V2_ROOT / "projects"
        self.reviews_dir = self.projects_root / project_id / "reviews"
        self._reviews: Dict[str, Review] = {}
        self.load()

    # ---------------- 持久化 ----------------
    def load(self) -> None:
        """从磁盘读回已有审核记录。"""
        if self.reviews_dir.exists():
            for path in sorted(self.reviews_dir.glob("review-*.json")):
                review = Review.from_dict(read_json(path))
                self._reviews[review.review_id] = review

    def save(self, review: Review) -> None:
        """把一条审核记录写盘。"""
        write_json(self.reviews_dir / f"{review.review_id}.json", review.to_dict())

    # ---------------- 审核 API ----------------
    def submit_for_review(
        self,
        artifact: Artifact,
        *,
        reviewer: str = "human",
        comment: str = "",
    ) -> Review:
        """把一份产出提交人工审核（状态 PENDING）。"""
        review = Review(
            review_id=new_review_id(),
            artifact_id=artifact.artifact_id,
            original_artifact=artifact.to_dict(),
            reviewer=reviewer,
            status=ReviewStatus.PENDING,
            submitted_at=now_iso(),
            comment=comment,
        )
        self._reviews[review.review_id] = review
        self.save(review)
        return review

    def approve(self, review_id: str, *, comment: str = "") -> Review:
        """人工确认通过：原始产出直接采用。"""
        review = self._pending(review_id)
        review.status = ReviewStatus.APPROVED
        review.comment = comment or review.comment
        review.reviewed_at = now_iso()
        self.save(review)
        return review

    def approve_with_edit(self, review_id: str, edited_content: Any, *, comment: str = "") -> Review:
        """人工修改后通过：真正写一个"编辑后的新版本"文件，原始文件保留。

        新版本文件用 ArtifactStore 的版本化命名（自动排下一个版本号），
        因此绝不会覆盖原始产物（指令 §十三）。
        """
        review = self._pending(review_id)
        orig = review.original_artifact
        new_artifact = self.store.save(
            step_id=str(orig.get("step_id", "")),
            attempt=0,  # 0 = 非引擎尝试（人工编辑产物）
            name=str(orig.get("name", "")),
            data=edited_content,
            type=str(orig.get("type", "json")),
            metadata={**dict(orig.get("metadata") or {}),
                      "review_id": review_id, "edited": True},
        )
        review.edited_artifact = new_artifact.to_dict()
        review.status = ReviewStatus.APPROVED_WITH_EDIT
        review.comment = comment or review.comment
        review.reviewed_at = now_iso()
        self.save(review)
        return review

    def reject(self, review_id: str, *, reason: str) -> Review:
        """人工拒绝：当前产出不能采用（流程停止）。"""
        review = self._pending(review_id)
        review.status = ReviewStatus.REJECTED
        review.reason = reason
        review.reviewed_at = now_iso()
        self.save(review)
        return review

    def _pending(self, review_id: str) -> Review:
        review = self._reviews.get(review_id)
        if review is None:
            raise KeyError(f"review 不存在: {review_id}")
        if review.status != ReviewStatus.PENDING:
            raise ValueError(f"review {review_id} 已审核（{review.status}），不能重复操作")
        return review

    # ---------------- 查询 ----------------
    def get_review(self, review_id: str) -> Optional[Review]:
        return self._reviews.get(review_id)

    def list_reviews(self) -> List[Review]:
        return sorted(self._reviews.values(), key=lambda r: r.review_id)

    # ---------------- 转给 Engine 的决策 ----------------
    def to_review_result(self, review: Review, edited_content: Any = None) -> ReviewResult:
        """把一条审核记录转成 Workflow Engine 需要的 ReviewResult。

        :param edited_content: approved_with_edit 时人工修改后的内容（由调用方一并给出）。
        """
        decision = {
            ReviewStatus.APPROVED: ReviewDecision.APPROVED,
            ReviewStatus.APPROVED_WITH_EDIT: ReviewDecision.APPROVED_WITH_EDIT,
            ReviewStatus.REJECTED: ReviewDecision.REJECTED,
        }.get(review.status)
        if decision is None:
            raise ValueError(f"审核未完成（当前 {review.status}），无法转成决策")
        return ReviewResult(
            decision=decision,
            review_id=review.review_id,
            edited_content=edited_content,
            edited_artifact=review.edited_artifact,
            reviewer=review.reviewer,
            comment=review.comment or review.reason,
        )


__all__ = ["ReviewService"]
