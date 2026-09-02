# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— 人工审核模块（v2/review/）

职责边界（指令 §十一 ~ §十六）：
    实现关键节点的人工审核：approved / approved_with_edit / rejected。

    - approved            原始产出直接通过
    - approved_with_edit  人工修改后通过（真正写新版本产物，原始文件保留）
    - rejected            当前产出不能采用（流程停止）

    审核复用阶段 3 的 Artifact 版本机制（不重造版本系统）；
    审核记录持久化，可回答"谁审核 / 何时 / 改成什么 / 采用哪版"。

    本阶段人工审核是明确的操作层（Python/API），绝不调用 LLM 冒充审核。

本阶段实现：
    models.py  ReviewStatus / ReviewDecision / Review / ReviewResult
    service.py ReviewService（submit/approve/approve_with_edit/reject + 持久化）
"""
from review.models import ReviewStatus, ReviewDecision, Review, ReviewResult, now_iso, new_review_id
from review.service import ReviewService

__all__ = [
    "ReviewStatus",
    "ReviewDecision",
    "Review",
    "ReviewResult",
    "now_iso",
    "new_review_id",
    "ReviewService",
]
