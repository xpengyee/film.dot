# -*- coding: utf-8 -*-
"""
V0.2 Stage 10 —— Visual Asset 数据模型（core/visual.py）

作用（stage10_plan §3/§4~§9/§22）：给"被镜头引用的资产"补上**视觉身份与版本**层：
    VisualAsset（稳定身份，asset_id 一经分配永不变更）
      └── VisualAssetVersion（视觉版本，单调递增、绝不覆盖）
            ├── profile（按 kind 的结构化视觉档案）
            ├── reference_images（参考图，挂版本上——换参考图 = 新版本）
            └── visual_prompt（该版本视觉身份的一句话浓缩）

语义与视觉分离（指令 §八）：
    - Character / Location 语义模型（core/models.py）0 改，不加任何视觉字段；
    - 本文件的视觉档案只留 name 锚点，不复制语义档案；
    - 连接键 = 名字（(kind, name) 在项目内唯一，由 CastingValidator /
      AssetRegistry 的既有唯一性保证）。

本文件只定义受控词表 + 数据结构 + dict 序列化（serialize → deserialize →
serialize 一致）+ 确定性规范化工具，不调 LLM、不读写磁盘（持久化在
core/visual_registry.py，校验在 core/validator.py 的 VisualAssetValidator）。

为什么不用 ShotAsset 的 ASSET_KINDS（重要决策记录，stage10_plan §7）：
    stage8 封存测试对 ASSET_KINDS 做精确集合断言，加值必破封存。视觉资产的
    五类词表（character/location/prop/style/reference）是 registry 层自己的
    词表，与镜头引用层（ASSET_KINDS）各自独立、靠名字连接，互不渗透。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 受控词表（Visual Asset 层自有词表；与 core/shot.py 的 ASSET_KINDS 互不影响）
# ---------------------------------------------------------------------------
VISUAL_ASSET_KINDS: tuple = ("character", "location", "prop", "style", "reference")
"""视觉资产五类：角色 / 地点 / 道具 / 风格 / 独立参考束。"""

VISUAL_VERSION_STATUSES: tuple = (
    "draft", "generated", "reviewing", "approved", "rejected", "superseded", "archived",
)
"""版本生命周期七态（stage10_plan §10.1）。状态机只有一套驱动源：Registry 对
既有 Review 结果做确定性派生（visual_registry.py），不建第三套审核服务。"""

REFERENCE_IMAGE_KINDS: tuple = ("character", "location", "prop", "style", "pose", "storyboard", "other")
"""参考图 kind（挂版本上的 ReferenceImage 用）。"""

REFERENCE_PURPOSES: tuple = ("pose", "storyboard", "other")
"""独立参考束（kind=reference 的资产）的用途。
未来预留 video / audio（stage10_plan §8 指令 §十二）：只在此注释占位，当前不实现。"""

VISUAL_SOURCES: tuple = ("manual", "text_planned", "vision_extracted", "review_edited")
"""版本来源：人工 / 文本规划 / 视觉提取 / 审核编辑。"""

ASSET_ID_PREFIX: Dict[str, str] = {
    "character": "CHAR", "location": "LOC", "prop": "PROP", "style": "STYLE", "reference": "REF",
}
_PREFIX_TO_KIND: Dict[str, str] = {v: k for k, v in ASSET_ID_PREFIX.items()}

_ASSET_ID_RE = re.compile(r"^(CHAR|LOC|PROP|STYLE|REF)_(\d{3,})$")
_VERSION_RE = re.compile(r"^v(\d+)$")
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")

# 版本状态合法转移（Registry 在变更时强制；Validator 校验记录一致性）
LEGAL_STATUS_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "draft": ("reviewing",),
    "generated": ("reviewing",),          # 预留：未来生成媒体资产走此入口
    "reviewing": ("approved", "rejected"),
    "approved": ("superseded", "archived"),
    "rejected": (),                        # 保留记录；重审 = 产生新版本
    "superseded": ("archived",),
    "archived": (),
}


# ---------------------------------------------------------------------------
# ID / 版本格式工具（纯函数）
# ---------------------------------------------------------------------------
def asset_id_for(kind: str, number: int) -> str:
    """按 kind 生成资产 ID：CHAR_001 / LOC_012 …（超过 999 自然增长到 4 位）。"""
    prefix = ASSET_ID_PREFIX.get(kind)
    if not prefix:
        raise ValueError(f"未知视觉资产 kind: {kind!r}（支持 {list(VISUAL_ASSET_KINDS)}）")
    return f"{prefix}_{number:03d}"


def is_valid_asset_id(asset_id: str, kind: Optional[str] = None) -> bool:
    """ID 格式 ^PREFIX_\\d{3,}$；给了 kind 时前缀必须与 kind 匹配。"""
    m = _ASSET_ID_RE.match(str(asset_id or ""))
    if not m:
        return False
    if kind is not None and _PREFIX_TO_KIND.get(m.group(1)) != kind:
        return False
    return True


def is_valid_version(version: str) -> bool:
    """版本格式 ^v\\d+$（v1 / v2 / v10 …；绝不允许 v0 / v01）。"""
    m = _VERSION_RE.match(str(version or ""))
    return bool(m) and int(m.group(1)) >= 1


def version_number(version: str) -> int:
    """"v3" -> 3（格式不合法抛 ValueError）。"""
    m = _VERSION_RE.match(str(version or ""))
    if not m:
        raise ValueError(f"非法版本号: {version!r}（应为 v1/v2/…）")
    return int(m.group(1))


def is_valid_checksum(value: str) -> bool:
    return bool(_CHECKSUM_RE.match(str(value or "").lower()))


# ---------------------------------------------------------------------------
# 视觉档案（per-kind Profile，字段清单见 stage10_plan §4~§7，最小集）
# 每个字段必须服务三件事之一：视觉一致性 / prompt 组装 / VLM QA ground truth。
# 全部 str、缺省空串（空 = 未指定，不编造）；顺序即紧凑渲染顺序（定长）。
# ---------------------------------------------------------------------------
class _ProfileBase:
    """四个 Profile 的公共行为：紧凑渲染 + dict 往返。字段由子类 FIELD_ORDER 声明。"""

    FIELD_ORDER: Tuple[Tuple[str, bool], ...] = ()
    """((字段名, 是否必填), ...)；顺序 = render_lines() 输出顺序。"""

    def to_dict(self) -> Dict[str, str]:
        return {name: str(getattr(self, name, "") or "") for name, _ in self.FIELD_ORDER}

    @classmethod
    def from_dict(cls, data: Optional[dict]):
        data = dict(data or {})
        obj = cls()
        for name, _ in cls.FIELD_ORDER:
            setattr(obj, name, str(data.get(name, "") or ""))
        return obj

    def render_lines(self) -> List[str]:
        """紧凑序列化：非空字段的 'key: value' 行（Composer 的 profile 文本）。"""
        out = []
        for name, _ in self.FIELD_ORDER:
            v = str(getattr(self, name, "") or "").strip()
            if v:
                out.append(f"{name}: {v}")
        return out

    def missing_required(self) -> List[str]:
        """必填但为空的字段清单（审批门槛用；draft 允许不完整）。"""
        return [name for name, req in self.FIELD_ORDER
                if req and not str(getattr(self, name, "") or "").strip()]

    def get_negative_cues(self) -> str:
        return str(getattr(self, "negative_cues", "") or "")


@dataclass
class CharacterVisualProfile(_ProfileBase):
    """角色视觉档案（stage10_plan §4）。必填 4 项：appearance/face/hair/clothing。"""
    appearance: str = ""
    face: str = ""
    hair: str = ""
    clothing: str = ""
    body: str = ""
    color_palette: str = ""
    distinguishing_features: str = ""
    visual_style: str = ""
    negative_cues: str = ""

    FIELD_ORDER = (
        ("appearance", True), ("face", True), ("hair", True), ("clothing", True),
        ("body", False), ("color_palette", False), ("distinguishing_features", False),
        ("visual_style", False), ("negative_cues", False),
    )


@dataclass
class LocationVisualProfile(_ProfileBase):
    """地点视觉档案（stage10_plan §5）。必填 3 项 + 强烈建议 distinguishing_features。"""
    environment: str = ""
    lighting: str = ""
    palette: str = ""
    architecture: str = ""
    time_of_day: str = ""
    distinguishing_features: str = ""
    visual_style: str = ""
    negative_cues: str = ""

    FIELD_ORDER = (
        ("environment", True), ("lighting", True), ("palette", True),
        ("architecture", False), ("time_of_day", False),
        ("distinguishing_features", False), ("visual_style", False), ("negative_cues", False),
    )


@dataclass
class PropVisualProfile(_ProfileBase):
    """道具视觉档案（stage10_plan §6）。必填 2 项：form/color。"""
    form: str = ""
    color: str = ""
    material: str = ""
    size: str = ""
    condition: str = ""
    distinguishing_features: str = ""
    negative_cues: str = ""

    FIELD_ORDER = (
        ("form", True), ("color", True), ("material", False), ("size", False),
        ("condition", False), ("distinguishing_features", False), ("negative_cues", False),
    )


@dataclass
class StyleProfile(_ProfileBase):
    """风格档案（stage10_plan §7，项目级默认风格）。必填 2 项：medium/palette。"""
    medium: str = ""
    palette: str = ""
    rendering: str = ""
    lighting_style: str = ""
    camera_style: str = ""
    negative_cues: str = ""
    notes: str = ""

    FIELD_ORDER = (
        ("medium", True), ("palette", True), ("rendering", False),
        ("lighting_style", False), ("camera_style", False),
        ("negative_cues", False), ("notes", False),
    )


PROFILE_CLASSES: Dict[str, type] = {
    "character": CharacterVisualProfile,
    "location": LocationVisualProfile,
    "prop": PropVisualProfile,
    "style": StyleProfile,
    # reference 是参考束的集合，本身没有 profile 结构（stage10_plan §8）
}


def new_profile(kind: str, data: Optional[dict] = None):
    """按 kind 造一个 Profile 实例（from_dict；reference 无 profile 返回 None）。"""
    cls = PROFILE_CLASSES.get(kind)
    if cls is None:
        return None
    return cls.from_dict(data)


def normalize_profile_fields(kind: str, raw: Any) -> Dict[str, str]:
    """确定性规范化：LLM/人工原始 dict → 该 kind 的合法字段表。

    规则（不调任何模型，Stage 10-B 全部规范化走这里）：
        - 只保留该 kind 的已知字段（LLM 多说的键直接丢弃，不进档案）；
        - 值统一 str + strip；None/缺失 → 空串（空 = 未指定，不编造）；
        - kind 非法 → ValueError；reference 无 profile → 返回 {}。
    """
    if kind not in VISUAL_ASSET_KINDS:
        raise ValueError(f"未知视觉资产 kind: {kind!r}（支持 {list(VISUAL_ASSET_KINDS)}）")
    cls = PROFILE_CLASSES.get(kind)
    if cls is None:
        return {}
    raw = raw if isinstance(raw, dict) else {}
    return {name: str(raw.get(name, "") or "").strip() for name, _ in cls.FIELD_ORDER}


# ---------------------------------------------------------------------------
# 参考图（挂版本上）
# ---------------------------------------------------------------------------
@dataclass
class ReferenceImage:
    """一张参考图（stage10_plan §3.3）。checksum（sha256）= "同一张图"判定 +
    未来 VLM QA 比对锚点。checksum 由 Registry 落盘时计算（本文件不碰磁盘）。"""
    image_id: str = ""          # IMG_001 …（Registry 分配）
    path: str = ""              # 项目内相对路径（v2/projects/<pid>/visual_assets/images/…）
    kind: str = ""              # ∈ REFERENCE_IMAGE_KINDS
    note: str = ""              # 来源/用途说明
    checksum: str = ""          # 文件 sha256（64 hex）
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "image_id": self.image_id,
            "path": self.path,
            "kind": self.kind,
            "note": self.note,
            "checksum": self.checksum,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["ReferenceImage"]:
        if not isinstance(data, dict):
            return None
        return cls(
            image_id=str(data.get("image_id", "") or ""),
            path=str(data.get("path", "") or ""),
            kind=str(data.get("kind", "") or ""),
            note=str(data.get("note", "") or ""),
            checksum=str(data.get("checksum", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
        )


# ---------------------------------------------------------------------------
# 版本
# ---------------------------------------------------------------------------
@dataclass
class VisualAssetVersion:
    """一个视觉版本（stage10_plan §3.3/§9）。单调递增、绝不复用号、绝不覆盖内容。"""
    version: str = "v1"                       # ^v\d+$
    status: str = "draft"                     # ∈ VISUAL_VERSION_STATUSES
    profile: Optional[_ProfileBase] = None    # 按 kind；reference 为 None
    reference_images: List[ReferenceImage] = field(default_factory=list)
    visual_prompt: str = ""                   # 该版本视觉身份的一句话浓缩
    source: str = "text_planned"              # ∈ VISUAL_SOURCES
    notes: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "status": self.status,
            "profile": self.profile.to_dict() if self.profile is not None else None,
            "reference_images": [r.to_dict() for r in self.reference_images],
            "visual_prompt": self.visual_prompt,
            "source": self.source,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict], *, kind: str = "") -> Optional["VisualAssetVersion"]:
        if not isinstance(data, dict):
            return None
        return cls(
            version=str(data.get("version", "") or ""),
            status=str(data.get("status", "") or ""),
            profile=new_profile(kind, data.get("profile")),
            reference_images=[r for r in (ReferenceImage.from_dict(d)
                                          for d in data.get("reference_images", [])) if r],
            visual_prompt=str(data.get("visual_prompt", "") or ""),
            source=str(data.get("source", "") or ""),
            notes=str(data.get("notes", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
        )


# ---------------------------------------------------------------------------
# 资产（稳定身份）
# ---------------------------------------------------------------------------
@dataclass
class VisualAsset:
    """一个视觉资产（stage10_plan §3.2/§3.3）。

    asset_id 一经分配永不变更，与 (kind, name) 永久绑定；名字 = 角色名/地点名/
    道具名/风格名/参考名（系统内唯一且不可变）。换造型/换参考图 = 新版本，
    旧 approved 版本转 superseded，内容与审核记录原样保留。
    """
    asset_id: str = ""                        # CHAR_001 …
    kind: str = ""                            # ∈ VISUAL_ASSET_KINDS
    name: str = ""                            # 语义锚点（= 角色名/地点名/…）
    purpose: str = ""                         # 仅 kind=reference 用（∈ REFERENCE_PURPOSES）
    current_version: str = ""                 # 指针：最新 approved；无 approved 时最新 draft（未生效）
    versions: List[VisualAssetVersion] = field(default_factory=list)
    archived: bool = False                    # 资产级退役（作用于全部版本）
    created_at: str = ""
    updated_at: str = ""

    # ---------------- 查询 ----------------
    def get_version(self, version: str) -> Optional[VisualAssetVersion]:
        for v in self.versions:
            if v.version == version:
                return v
        return None

    def latest_version(self) -> Optional[VisualAssetVersion]:
        """版本号最大的版本（versions 只追加不删除，取 max 即最新）。"""
        if not self.versions:
            return None
        return max(self.versions, key=lambda v: version_number(v.version))

    def latest_approved(self) -> Optional[VisualAssetVersion]:
        """最新 approved 版本（没有则 None——"当前生效视觉身份"不存在）。"""
        approved = [v for v in self.versions if v.status == "approved"]
        if not approved:
            return None
        return max(approved, key=lambda v: version_number(v.version))

    def next_version(self) -> str:
        """下一个版本号（永不复用）：现存最大号 + 1。"""
        latest = self.latest_version()
        n = version_number(latest.version) + 1 if latest else 1
        return f"v{n}"

    def to_dict(self) -> dict:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "name": self.name,
            "purpose": self.purpose,
            "current_version": self.current_version,
            "versions": [v.to_dict() for v in self.versions],
            "archived": self.archived,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["VisualAsset"]:
        if not isinstance(data, dict):
            return None
        kind = str(data.get("kind", "") or "")
        return cls(
            asset_id=str(data.get("asset_id", "") or ""),
            kind=kind,
            name=str(data.get("name", "") or ""),
            purpose=str(data.get("purpose", "") or ""),
            current_version=str(data.get("current_version", "") or ""),
            versions=[v for v in (VisualAssetVersion.from_dict(d, kind=kind)
                                  for d in data.get("versions", [])) if v],
            archived=bool(data.get("archived", False)),
            created_at=str(data.get("created_at", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
        )


__all__ = [
    # 词表
    "VISUAL_ASSET_KINDS", "VISUAL_VERSION_STATUSES", "REFERENCE_IMAGE_KINDS",
    "REFERENCE_PURPOSES", "VISUAL_SOURCES", "ASSET_ID_PREFIX",
    "LEGAL_STATUS_TRANSITIONS",
    # 格式工具
    "asset_id_for", "is_valid_asset_id", "is_valid_version", "version_number",
    "is_valid_checksum",
    # Profile
    "CharacterVisualProfile", "LocationVisualProfile", "PropVisualProfile",
    "StyleProfile", "PROFILE_CLASSES", "new_profile", "normalize_profile_fields",
    # 结构
    "ReferenceImage", "VisualAssetVersion", "VisualAsset",
]
