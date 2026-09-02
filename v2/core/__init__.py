# -*- coding: utf-8 -*-
"""
V0.2 —— core 基础层（v2/core/__init__.py）

core 是最底层，不依赖任何上层模块；其他所有层（llm / memory / context / workflow /
review）都只向上依赖 core 或同级。包含五件事：

    io         JSON 读写（保留 V0.1 容错行为）
    models     统一数据模型（Scene / Character / Line / World / Project / WorkflowState …）
    assets     资产注册表（地点 / 动作 / 镜头 / 站位点）
    validator  三个校验器（Enum / Reference / Schema）
    token_log  Token / Cost 记录（JSONL，不记 key、不记 prompt 正文）

阶段 5 清理：补齐本 __init__.py，与 memory / context / review / workflow 的入口风格一致。
"""
from core.io import read_json, write_json, ensure_dir
from core.models import (
    Asset, Position, PositionRef, Character, CharacterMemory, SceneInfo,
    CastingEntry, CastingResult, Action, Move, Line, Scene, Episode, World, Project,
    WorkflowStepState, WorkflowState,
    from_v01_script, to_v01_script, from_v01_actors, to_v01_actors,
)
from core.assets import LocationAsset, ActionAsset, ShotAsset, AssetRegistry, base_name
from core.validator import (
    ValidationError, format_errors, VALID_STATES,
    EnumValidator, ReferenceValidator, SchemaValidator, CastingValidator, Validator,
)
from core.token_log import (
    TokenUsage, estimate_cost, LLMCallRecord, compute_prompt_hash,
    new_request_id, now_iso, TokenLogger,
)

__all__ = [
    # io
    "read_json", "write_json", "ensure_dir",
    # models
    "Asset", "Position", "PositionRef", "Character", "CharacterMemory", "SceneInfo",
    "CastingEntry", "CastingResult", "Action", "Move", "Line", "Scene", "Episode", "World", "Project",
    "WorkflowStepState", "WorkflowState",
    "from_v01_script", "to_v01_script", "from_v01_actors", "to_v01_actors",
    # assets
    "LocationAsset", "ActionAsset", "ShotAsset", "AssetRegistry", "base_name",
    # validator
    "ValidationError", "format_errors", "VALID_STATES",
    "EnumValidator", "ReferenceValidator", "SchemaValidator", "CastingValidator", "Validator",
    # token_log
    "TokenUsage", "estimate_cost", "LLMCallRecord", "compute_prompt_hash",
    "new_request_id", "now_iso", "TokenLogger",
]
