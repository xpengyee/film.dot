# -*- coding: utf-8 -*-
"""
V0.2 阶段 8 —— Canonical Shot-level Schema（core/shot.py）

为什么需要这一层（开工指令 7 §九 / §十 / §十一 / §十二）：
    阶段 1~7 已经能可靠生成 Scene / Script（每行 = speaker/content/shot/站位）。
    但影视生产真正要控制的粒度是"镜头（Shot）"：一个画面里谁在、什么景别、
    机位怎么动、怎么构图、后面能不能直接出图/出视频。所以要把数据结构从

        Scene ──► Script

    升级为

        Scene ──► Beat ──► Shot ──► Asset

    本文件就是这套"Canonical（唯一内部标准）镜头语言"的数据定义。设计原则：

    1. Canonical 是 V0.2 内部唯一标准。V0.1 的三种字段写法
       （scene_information / sceneinformation / scene information）只允许在
       core/adapter.py（边界层）被消化，绝不允许继续往 V0.2 内部扩散。
    2. Shot 是影视生成的最小核心单位（渲染原子单元）：一段画面 + 表演内容 + 镜头语言。
    3. Beat 是"具有明确叙事/动作目的的连续单元"（一个 Scene 下按剧情切成几段），
       每段 Beat 里放若干个 Shot。
    4. Storyboard 是"Scene → Beat → Shot"的结构化视觉规划结果，是一个独立 artifact
       （不等于最终图片/视频；以后由它往下接 Image/Video Generation）。
    5. 受控词表（本文件下半部分）只借鉴成熟做法，绝不照搬 take 代码：
       shot_size / camera_angle / camera_movement / composition 四轴全用受控枚举。
       V0.1 已有镜头资产优先兼容（见 V01_SHOT_SIZE_MAP）。

本文件只定义数据结构 + dict 序列化（to_dict / from_dict，保证
serialize → deserialize → serialize 完全一致），不调 LLM、不读写磁盘。
校验在 core/validator.py（BeatValidator / ShotValidator / StoryboardValidator /
AssetValidator）；V0.1 ↔ Canonical 的双向转换在 core/adapter.py。

关于 Asset 命名的说明：
    core/models.py 里已经有一个"Asset"（阶段 1 的资产基类，LocationAsset /
    ActionAsset / ShotAsset 都继承它，A-keep 不能动）。本文件实现的 §十二"统一
    Asset 抽象"（character/location/prop/action/camera/image/video/audio/reference）
    是"镜头里引用了哪些资产/未来要产出哪些媒体资产"的引用层，为了避免和阶段 1
    的目录资产类重名搞混，命名为 ShotAsset。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .assets import base_name
from .models import Action, Move, PositionRef


# ---------------------------------------------------------------------------
# Canonical 受控镜头词表（唯一内部标准；validator 按它校验）
# ---------------------------------------------------------------------------
SHOT_SIZES: tuple = ("WS", "MS", "MCU", "CU", "ECU")
"""景别：WS 全景 / MS 中景 / MCU 中近景 / CU 特写 / ECU 大特写。"""

CAMERA_ANGLES: tuple = ("eye_level", "high_angle", "low_angle", "overhead")
"""机位高度/角度：平视 / 俯拍 / 仰拍 / 顶拍。"""

CAMERA_MOVEMENTS: tuple = ("static", "pan", "tilt", "dolly", "tracking", "zoom", "handheld")
"""运镜：固定 / 横摇 / 纵摇 / 推拉 / 跟随 / 变焦 / 手持。"""

COMPOSITIONS: tuple = ("single", "two_shot", "over_the_shoulder", "group")
"""构图：单人 / 双人 / 过肩 / 群体。"""

SHOT_KINDS: tuple = ("dialogue", "move", "general")
"""Shot 的内容类型：台词 / 走位 / 纯动作视觉（无台词无走位）。"""

SHOT_STATUSES: tuple = ("planned", "approved", "rendered")
"""Shot 生命周期状态：已规划 / 已审核通过 / 已渲染产出。"""

ASSET_KINDS: tuple = (
    "character", "location", "prop", "action",
    "camera", "image", "video", "audio", "reference",
)
"""ShotAsset 统一资产类型（开工指令 §十二）。前四种引用 V0.1 已有目录资产；
camera 引用机位/镜头设备配置；image/video/audio 是未来生产链产出的媒体资产；
reference 是参考素材（参考图等）。阶段 8 只让 Schema 能表达，不真生成。"""

# V0.1 镜头资产 → Canonical 景别 的"尽力而为"映射（§十三：优先兼容已有资产）。
# 只映射含义清晰的镜头；Pan/Track/Follow/Zoom/360/Curve Surround 等是运镜，
# 没有单一景别含义，不做强行映射（原始镜头名保留在 Shot.shot_type，保证不丢信息）。
V01_SHOT_SIZE_MAP: Dict[str, str] = {
    "Long Shot": "WS",   # 展示整个场景 → 全景
    "Mid Shot": "MS",    # 全身 → 中景
    "Close Shot": "CU",  # 上半身 → 特写
}


# ---------------------------------------------------------------------------
# 镜头参数（受控词表组装成的小对象）
# ---------------------------------------------------------------------------
@dataclass
class CameraPlan:
    """一个镜头的相机参数：景别 / 角度 / 运镜（全部受控枚举，空串 = 未指定）。"""
    shot_size: str = ""
    camera_angle: str = "eye_level"
    camera_movement: str = "static"

    def to_dict(self) -> dict:
        return {
            "shot_size": self.shot_size,
            "camera_angle": self.camera_angle,
            "camera_movement": self.camera_movement,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["CameraPlan"]:
        if not data:
            return None
        return cls(
            shot_size=str(data.get("shot_size", "")),
            camera_angle=str(data.get("camera_angle", "eye_level")),
            camera_movement=str(data.get("camera_movement", "static")),
        )


# ---------------------------------------------------------------------------
# 镜头内的资产引用（§十二 统一 Asset 抽象；见模块 docstring 命名说明）
# ---------------------------------------------------------------------------
@dataclass
class ShotAsset:
    """一个镜头里引用的资产 / 未来要产出的媒体资产。

    kind   ∈ ASSET_KINDS
    ref    资产标识：character→角色名 / location→地点名 / action→动作基础名 /
           camera→机位配置名 / prop→道具名 / reference→参考名；
           image/video/audio→未来产出的媒体文件（路径/名）。
    detail 补充说明（可选）。
    """
    kind: str = ""
    ref: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        out: Dict[str, Any] = {"kind": self.kind, "ref": self.ref}
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["ShotAsset"]:
        if not data:
            return None
        return cls(
            kind=str(data.get("kind", "")),
            ref=str(data.get("ref", "")),
            detail=str(data.get("detail", "")),
        )


# ---------------------------------------------------------------------------
# Shot：最小渲染单元（核心模型）
# ---------------------------------------------------------------------------
@dataclass
class Shot:
    """一个镜头：一段连续画面（Canonical 的最小核心单位）。

    字段分四组：
        - 定位：shot_id（镜头全局号）/ beat_id（所属 Beat）/ scene_id（所属 Scene）
        - 表演内容：kind、speaker、dialogue、actions、subject、move（走位）
        - 空间与表演：location、characters（画面里出现谁）、positioning（各人站位）
        - 镜头语言与产出：shot_type（镜头标签）、camera（受控）、composition（受控）、
          visual_description / continuity / duration / assets / status / metadata

    V0.1 兼容（§十五）：从 V0.1 script 的一行转换来的镜头，原始镜头名放在
    shot_type 原样保留（例如 "Close Shot" / "Long Shot 2"），保证往返不丢信息；
    camera 字段尽量从原始镜头名推断（见 infer_camera_from_shot_label），
    推断不出来就留空串，绝不编造。
    """
    shot_id: int = 0
    scene_id: int = 0
    beat_id: int = 1
    kind: str = "general"                     # dialogue | move | general
    shot_type: str = ""                       # 镜头标签（V0.1 注册镜头名 / 简短类型名）
    camera: Optional[CameraPlan] = None       # 受控镜头参数（可为空）
    composition: str = ""                     # 受控构图（可为空）
    subject: str = ""                         # 这一镜在拍什么（一句话内容要点）
    speaker: str = ""                         # kind=="dialogue" 时的说话人
    dialogue: str = ""                        # kind=="dialogue" 时的台词内容
    actions: List[Action] = field(default_factory=list)   # 镜内动作（可多个）
    location: str = ""                        # 可为空（空 = 继承 Scene 地点）
    characters: List[str] = field(default_factory=list)   # 画面里出现的角色
    positioning: List[PositionRef] = field(default_factory=list)  # 各角色站位
    move: Optional[Move] = None               # kind=="move" 时的走位
    reason: str = ""                          # move 行顶层 reason（scenes_8 透传保留）
    insertion_reason: str = ""                # move 行 insertion.reason（透传保留）
    insertion_position: str = ""              # move 行 insertion.position（透传保留）
    visual_description: str = ""              # 视觉描述（为未来图像生成准备的文本提示基础）
    continuity: str = ""                      # 与前镜的连贯性说明（可选）
    duration: float = 0.0                     # 预估时长（秒）
    assets: List[ShotAsset] = field(default_factory=list)   # 引用的资产
    status: str = "planned"                   # planned | approved | rendered
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_content(self) -> bool:
        """这个镜头是否承载了台词/走位内容（区别于纯视觉空镜）。"""
        return self.kind in ("dialogue", "move")

    def to_dict(self) -> dict:
        return {
            "shot_id": self.shot_id,
            "scene_id": self.scene_id,
            "beat_id": self.beat_id,
            "kind": self.kind,
            "shot_type": self.shot_type,
            "camera": self.camera.to_dict() if self.camera else None,
            "composition": self.composition,
            "subject": self.subject,
            "speaker": self.speaker,
            "dialogue": self.dialogue,
            "actions": [a.to_v01() for a in self.actions],
            "location": self.location,
            "characters": list(self.characters),
            "positioning": [p.to_v01() for p in self.positioning],
            "move": self.move.to_v01() if self.move else None,
            "reason": self.reason,
            "insertion_reason": self.insertion_reason,
            "insertion_position": self.insertion_position,
            "visual_description": self.visual_description,
            "continuity": self.continuity,
            "duration": self.duration,
            "assets": [a.to_dict() for a in self.assets],
            "status": self.status,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Shot"]:
        if not data:
            return None
        return cls(
            shot_id=int(data.get("shot_id", 0)),
            scene_id=int(data.get("scene_id", 0)),
            beat_id=int(data.get("beat_id", 1)),
            kind=str(data.get("kind", "general")),
            shot_type=str(data.get("shot_type", "")),
            camera=CameraPlan.from_dict(data.get("camera")),
            composition=str(data.get("composition", "")),
            subject=str(data.get("subject", "")),
            speaker=str(data.get("speaker", "")),
            dialogue=str(data.get("dialogue", "")),
            actions=[Action.from_v01(a) for a in data.get("actions", []) if isinstance(a, dict)],
            location=str(data.get("location", "")),
            characters=[str(c) for c in data.get("characters", [])],
            positioning=[PositionRef.from_v01(p) for p in data.get("positioning", []) if isinstance(p, dict)],
            move=Move.from_v01(data["move"]) if isinstance(data.get("move"), dict) else None,
            reason=str(data.get("reason", "")),
            insertion_reason=str(data.get("insertion_reason", "")),
            insertion_position=str(data.get("insertion_position", "")),
            visual_description=str(data.get("visual_description", "")),
            continuity=str(data.get("continuity", "")),
            duration=float(data.get("duration", 0.0) or 0.0),
            assets=[a for a in (ShotAsset.from_dict(a) for a in data.get("assets", [])) if a],
            status=str(data.get("status", "planned")),
            metadata=dict(data.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# Beat：一个具有明确叙事/动作目的的连续单元
# ---------------------------------------------------------------------------
@dataclass
class Beat:
    """一个 Scene 下的叙事/动作单元（开工指令 §十）。

    例：Scene 01 ── Beat 01（人物进房间）── Beat 02（人物开始对话）── Beat 03（人物离开）
    每个 Beat 下面挂 1..n 个 Shot。
    """
    beat_id: int = 1
    purpose: str = ""                         # 这一 Beat 在剧情上要完成什么
    shots: List[Shot] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "beat_id": self.beat_id,
            "purpose": self.purpose,
            "shots": [s.to_dict() for s in self.shots],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Beat"]:
        if not data:
            return None
        return cls(
            beat_id=int(data.get("beat_id", 1)),
            purpose=str(data.get("purpose", "")),
            shots=[s for s in (Shot.from_dict(d) for d in data.get("shots", [])) if s],
            metadata=dict(data.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# Storyboard：一个 Scene 的结构化视觉规划（独立 artifact）
# ---------------------------------------------------------------------------
@dataclass
class Storyboard:
    """一个 Scene 的 Scene→Beat→Shot 结构化视觉规划（开工指令 §十一）。

    Storyboard 不等于最终图片/视频，而是未来
        Storyboard → Image Generation → Image Review → Video Generation
    的输入计划。阶段 8 只定义数据结构，不调图像/视频模型。

    承载 Scene 级信息（location / characters(=who) / synopsis(=what) /
    initial_positions），使 Storyboard 可以脱离 Scene 对象自包含，也能被
    core/adapter.py 用来和 V0.1 Scene 双向无损转换。
    """
    scene_id: int = 0
    location: str = ""
    characters: List[str] = field(default_factory=list)   # = Scene.info.who
    synopsis: str = ""                                    # = Scene.info.what
    initial_positions: List[PositionRef] = field(default_factory=list)
    beats: List[Beat] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def flatten_shots(self) -> List[Shot]:
        """按 Beat 顺序展平所有 Shot（保序）。"""
        out: List[Shot] = []
        for beat in self.beats:
            out.extend(beat.shots)
        return out

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "location": self.location,
            "characters": list(self.characters),
            "synopsis": self.synopsis,
            "initial_positions": [p.to_v01() for p in self.initial_positions],
            "beats": [b.to_dict() for b in self.beats],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["Storyboard"]:
        if not data:
            return None
        return cls(
            scene_id=int(data.get("scene_id", 0)),
            location=str(data.get("location", "")),
            characters=[str(c) for c in data.get("characters", [])],
            synopsis=str(data.get("synopsis", "")),
            initial_positions=[PositionRef.from_v01(p) for p in data.get("initial_positions", []) if isinstance(p, dict)],
            beats=[b for b in (Beat.from_dict(d) for d in data.get("beats", [])) if b],
            metadata=dict(data.get("metadata") or {}),
        )


# ---------------------------------------------------------------------------
# V0.1 镜头名 ↔ Canonical 镜头参数（边界小工具）
# ---------------------------------------------------------------------------
def infer_camera_from_shot_label(shot_name: str) -> Optional[CameraPlan]:
    """从一个镜头标签尽力推断 Canonical 镜头参数（V0.1 兼容）。

    只映射含义清晰的 V0.1 注册镜头（Long Shot → WS / Mid Shot → MS /
    Close Shot → CU）；Pan/Track/Follow/Zoom/360/Curve 等运镜镜头没有单一景别，
    返回 None（原始镜头名留在 Shot.shot_type，信息不丢）。绝不编造。
    """
    if not shot_name:
        return None
    size = V01_SHOT_SIZE_MAP.get(base_name(str(shot_name)))
    if not size:
        return None
    return CameraPlan(shot_size=size, camera_angle="eye_level", camera_movement="static")


_REVERSE_SIZE_SHOT: Dict[str, str] = {v: k for k, v in V01_SHOT_SIZE_MAP.items()}


def shot_label_from_camera(camera: Optional[CameraPlan]) -> str:
    """从 Canonical 镜头参数反推一个兼容 V0.1 的镜头名（反向尽力而为）。

    只在 shot_type 为空、且 camera.shot_size 能对应一个 V0.1 镜头时使用；
    其他情况返回空串（不编造）。
    """
    if not camera:
        return ""
    return _REVERSE_SIZE_SHOT.get(camera.shot_size, "")


__all__ = [
    # 受控词表
    "SHOT_SIZES", "CAMERA_ANGLES", "CAMERA_MOVEMENTS", "COMPOSITIONS",
    "SHOT_KINDS", "SHOT_STATUSES", "ASSET_KINDS", "V01_SHOT_SIZE_MAP",
    # 模型
    "CameraPlan", "ShotAsset", "Shot", "Beat", "Storyboard",
    # V0.1 镜头名 ↔ Canonical
    "infer_camera_from_shot_label", "shot_label_from_camera",
]
