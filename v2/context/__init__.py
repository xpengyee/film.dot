# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Context 模块（v2/context/）

职责边界（指令 §七 / §八 / §九 / §十）：
    ContextAssembler 是"资料管理员"：根据 Workflow Step 声明的 needs，
    从 Memory / Assets / 项目状态 / 已有产出中，组装这个 Step 真正需要的 Context。

    它承担"上下文组织 + 上下文裁剪"：
        - 只装 Step 需要的内容（needs 声明制，杜绝 if step_name 硬编码）；
        - 每条内容带来源信息（source/source_type/source_id/version），可追踪。

本阶段实现：
    models.py    ContextSource / ContextItem / AssembledContext（可追踪的数据形态）
    assembler.py ContextAssembler（声明式 needs + provider 注册表）

本阶段不实现：具体业务 Agent（casting / lines_generate 等）——那是后续阶段的事。
"""
from context.models import ContextSource, ContextItem, AssembledContext, now_iso, new_context_id
from context.assembler import ContextAssembler

__all__ = [
    "ContextSource",
    "ContextItem",
    "AssembledContext",
    "now_iso",
    "new_context_id",
    "ContextAssembler",
]
