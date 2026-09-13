# -*- coding: utf-8 -*-
"""
V0.2 —— core 基础层（v2/core/__init__.py）

core 是最底层，不依赖任何上层模块；其他所有层（llm / memory / context / workflow /
review）都只向上依赖 core 或同级。包含七件事：

    io         JSON 读写（保留 V0.1 容错行为）
    models     统一数据模型（Scene / Character / Line / World / Project / WorkflowState …）
    assets     资产注册表（地点 / 动作 / 镜头 / 站位点）
    shot       Canonical 镜头层 Schema（阶段 8：受控词表 + Shot / Beat / Storyboard / ShotAsset）
    adapter    V0.1 ↔ Canonical 边界转换器（阶段 8：script 往返无损）
    validator  校验器（Enum / Reference / Schema / Casting / 阶段8 Shot·Beat·Storyboard·Asset / 阶段10 VisualAsset）
    token_log  Token / Cost 记录（JSONL，不记 key、不记 prompt 正文）
    visual     视觉资产数据模型（阶段 10：Profile / VisualAsset / Version / ReferenceImage）
    visual_registry   视觉资产注册表（身份/版本/生命周期派生/持久化）
    visual_compose    VisualComposer（Shot → 组装输入，Token 控制核心）

阶段 5 清理：补齐本 __init__.py，与 memory / context / review / workflow 的入口风格一致。
阶段 8：新增 core.shot（Canonical 镜头层）与 core.adapter（V0.1 边界转换），
本 __init__ 一并导出，方便从 core 顶层取用。
"""
from core.io import read_json, write_json, ensure_dir
from core.models import (
    Asset, Position, PositionRef, Character, CharacterMemory, SceneInfo,
    CastingEntry, CastingResult, Action, Move, Line, Scene, Episode, World, Project,
    WorkflowStepState, WorkflowState,
    from_v01_script, to_v01_script, from_v01_actors, to_v01_actors,
)
from core.assets import LocationAsset, ActionAsset, ShotAsset, AssetRegistry, base_name
from core.shot import (
    SHOT_SIZES, CAMERA_ANGLES, CAMERA_MOVEMENTS, COMPOSITIONS,
    SHOT_KINDS, SHOT_STATUSES, ASSET_KINDS, V01_SHOT_SIZE_MAP,
    CameraPlan, ShotAsset as CanonicalShotAsset, Shot, Beat, Storyboard,
    infer_camera_from_shot_label, shot_label_from_camera,
)
from core.adapter import (
    line_to_shot, shot_to_line, scene_to_storyboard, storyboard_to_scene,
    scenes_to_storyboards, storyboards_to_scenes,
    v01_script_to_storyboards, storyboards_to_v01_script,
)
from core.validator import (
    ValidationError, format_errors, VALID_STATES,
    EnumValidator, ReferenceValidator, SchemaValidator, CastingValidator, Validator,
    AssetValidator, ShotValidator, BeatValidator, StoryboardValidator,
    VisualAssetValidator,
)
from core.token_log import (
    TokenUsage, estimate_cost, LLMCallRecord, compute_prompt_hash,
    new_request_id, now_iso, TokenLogger,
)
from core.visual import (
    VISUAL_ASSET_KINDS, VISUAL_VERSION_STATUSES, REFERENCE_IMAGE_KINDS,
    REFERENCE_PURPOSES, VISUAL_SOURCES, ASSET_ID_PREFIX, LEGAL_STATUS_TRANSITIONS,
    asset_id_for, is_valid_asset_id, is_valid_version, version_number,
    is_valid_checksum,
    CharacterVisualProfile, LocationVisualProfile, PropVisualProfile, StyleProfile,
    PROFILE_CLASSES, new_profile, normalize_profile_fields,
    ReferenceImage, VisualAssetVersion, VisualAsset,
)
from core.visual_registry import (
    DECISION_APPROVED, DECISION_APPROVED_WITH_EDIT, DECISION_REJECTED,
    VisualRegistryError, sha256_of_file, VisualAssetRegistry,
)
from core.visual_compose import VisualComposer, ComposedVisualInput, VisualComposeError

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
    # shot（Canonical 镜头层，阶段 8）
    "SHOT_SIZES", "CAMERA_ANGLES", "CAMERA_MOVEMENTS", "COMPOSITIONS",
    "SHOT_KINDS", "SHOT_STATUSES", "ASSET_KINDS", "V01_SHOT_SIZE_MAP",
    "CameraPlan", "CanonicalShotAsset", "Shot", "Beat", "Storyboard",
    "infer_camera_from_shot_label", "shot_label_from_camera",
    # adapter（V0.1 ↔ Canonical，阶段 8）
    "line_to_shot", "shot_to_line", "scene_to_storyboard", "storyboard_to_scene",
    "scenes_to_storyboards", "storyboards_to_scenes",
    "v01_script_to_storyboards", "storyboards_to_v01_script",
    # validator
    "ValidationError", "format_errors", "VALID_STATES",
    "EnumValidator", "ReferenceValidator", "SchemaValidator", "CastingValidator", "Validator",
    "AssetValidator", "ShotValidator", "BeatValidator", "StoryboardValidator",
    "VisualAssetValidator",
    # visual（Stage 10 视觉资产层）
    "VISUAL_ASSET_KINDS", "VISUAL_VERSION_STATUSES", "REFERENCE_IMAGE_KINDS",
    "REFERENCE_PURPOSES", "VISUAL_SOURCES", "ASSET_ID_PREFIX", "LEGAL_STATUS_TRANSITIONS",
    "asset_id_for", "is_valid_asset_id", "is_valid_version", "version_number",
    "is_valid_checksum",
    "CharacterVisualProfile", "LocationVisualProfile", "PropVisualProfile", "StyleProfile",
    "PROFILE_CLASSES", "new_profile", "normalize_profile_fields",
    "ReferenceImage", "VisualAssetVersion", "VisualAsset",
    "DECISION_APPROVED", "DECISION_APPROVED_WITH_EDIT", "DECISION_REJECTED",
    "VisualRegistryError", "sha256_of_file", "VisualAssetRegistry",
    "VisualComposer", "ComposedVisualInput", "VisualComposeError",
    # token_log
    "TokenUsage", "estimate_cost", "LLMCallRecord", "compute_prompt_hash",
    "new_request_id", "now_iso", "TokenLogger",
]
