# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Memory 模块（v2/memory/）

职责边界（指令 §三 / §四 / §五 / §六）：
    Memory 是"项目的事实层"：known_facts（结构化事实）+ 角色 + 世界观 + 历史产出引用。
    事实必须以 candidate 身份进入，经人工 approve 才能成为 approved（绝不自升）。
    不同 project 的 Memory 物理隔离（目录不同，互不污染）。

    Memory 不是把全部 JSON 塞进来；它是明确的数据层，
    只保存"后续 Step / ContextAssembler 真正需要的高可信事实"。

本阶段实现：
    models.py  FactStatus / Fact（candidate/approved/rejected 三态）
    store.py   MemoryStore（JSON 持久化 + 项目隔离 + revision 追踪）

本阶段不实现：具体 memory_writer Agent（那是"产生候选事实"的业务，后续阶段接）。
"""
from memory.models import FactStatus, Fact, now_iso, new_fact_id
from memory.store import MemoryStore

__all__ = [
    "FactStatus",
    "Fact",
    "now_iso",
    "new_fact_id",
    "MemoryStore",
]
