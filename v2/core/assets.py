# -*- coding: utf-8 -*-
"""
V0.2 阶段 1 —— 最小 Asset 注册表（core/assets.py）

作用：把 V0.1 已存在的资产（地点、动作、镜头、站位点）读进内存，
     统一由一个"注册表"对外提供，供 Validator 使用。
     Validator 不应该自己到处读文件，而是从注册表拿"合法资产清单"。
     这样以后接入 Project / World 时，可以替换数据来源（例如从内存字典注入）。

数据来源（只读，绝不修改）：
    v2/assets/locations/  (阶段 0 从 V0.1 Locations/ 逐字节迁移过来)
        - actions.json       全局动作清单（键是基础名）
        - shots.json         全局镜头清单（键是基础名）
        - rotateandtrack.json 各地点旋转/跟踪/长镜头配置（本阶段仅读入保留）
        - <地点名>/position.json 每个地点的站位点

关键点：动作和镜头在最终剧本里常带编号后缀。
    actions.json 里是 "Standing Talking"（基础名）
    但 script/0.json 里是 "Standing Talking 5"（编号变体）
所以 has_action / has_shot 必须先把 "Standing Talking 5" 里的数字后缀剥掉，
再和基础名比较。这是 V0.1 容错 return_most_similar() 之外、明确的转换方式。

本阶段只处理 LocationAsset / ActionAsset / ShotAsset。
VoiceAsset / StyleAsset 暂不实现，但通过 Asset 基类和预留目录留了扩展空间。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .io import read_json
from .models import Asset, Position


# ---------------------------------------------------------------------------
# 具体资产类型
# ---------------------------------------------------------------------------
class LocationAsset(Asset):
    """一个地点：有名字 + 一组站位点 Position。"""
    kind = "location"

    def __init__(self, name: str, positions: Optional[List[Position]] = None):
        super().__init__(name=name, kind=self.kind)
        self.positions: List[Position] = positions or []


class ActionAsset(Asset):
    """一个基础动作（如 "Standing Talking"）。"""
    kind = "action"

    def __init__(self, name: str):
        super().__init__(name=name, kind=self.kind)


class ShotAsset(Asset):
    """一个基础镜头（如 "Close Shot"）。"""
    kind = "shot"

    def __init__(self, name: str):
        super().__init__(name=name, kind=self.kind)


# ---------------------------------------------------------------------------
# 编号变体剥离
# ---------------------------------------------------------------------------
_NUMBER_SUFFIX_RE = re.compile(r"\s+\d+\s*$")


def base_name(name: str) -> str:
    """把带编号变体的名字剥成基础名。

    "Standing Talking 5" -> "Standing Talking"
    "Long Shot 2"        -> "Long Shot"
    "Standing Agree 1"   -> "Standing Agree"
    基础名本身（无编号）原样返回。
    """
    return _NUMBER_SUFFIX_RE.sub("", str(name)).strip()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
class AssetRegistry:
    """资产注册表：统一提供地点/动作/镜头/站位点的合法性判断。"""

    def __init__(
        self,
        locations: Optional[Dict[str, LocationAsset]] = None,
        actions: Optional[Set[str]] = None,
        shots: Optional[Set[str]] = None,
        rotate_track: Optional[Dict[str, dict]] = None,
    ):
        # locations: {地点名: LocationAsset}
        self._locations: Dict[str, LocationAsset] = locations or {}
        # actions/shots: 基础名集合
        self._actions: Set[str] = set(actions or {})
        self._shots: Set[str] = set(shots or {})
        # rotate_track: {地点名: {rotate, track, long}}，本阶段仅保留
        self._rotate_track: Dict[str, dict] = rotate_track or {}

    # ---------------- 构造 ----------------
    @classmethod
    def load(cls, root: Path) -> "AssetRegistry":
        """从 assets 根目录读取真实资产。

        root 通常是 v2/assets/locations/
        """
        root = Path(root)
        locations: Dict[str, LocationAsset] = {}
        for child in sorted(root.iterdir()):
            if child.is_dir():
                pos_file = child / "position.json"
                if pos_file.exists():
                    positions = [Position.from_v01(p) for p in read_json(pos_file)]
                    locations[child.name] = LocationAsset(name=child.name, positions=positions)

        actions_data = read_json(root / "actions.json") if (root / "actions.json").exists() else {}
        shots_data = read_json(root / "shots.json") if (root / "shots.json").exists() else {}
        rot_data = read_json(root / "rotateandtrack.json") if (root / "rotateandtrack.json").exists() else {}

        actions = set(actions_data.keys()) if isinstance(actions_data, dict) else set()
        shots = set(shots_data.keys()) if isinstance(shots_data, dict) else set()

        return cls(locations=locations, actions=actions, shots=shots,
                   rotate_track=rot_data if isinstance(rot_data, dict) else {})

    @classmethod
    def from_dicts(cls, locations, actions, shots, rotate_track=None) -> "AssetRegistry":
        """从内存字典构造（测试 / 未来 Project/World 注入用）。"""
        return cls(locations=locations, actions=set(actions), shots=set(shots),
                   rotate_track=rotate_track)

    # ---------------- 地点 ----------------
    def location_names(self) -> List[str]:
        return sorted(self._locations.keys())

    def has_location(self, name: str) -> bool:
        return name in self._locations

    def get_positions(self, name: str) -> List[Position]:
        """返回某地点的站位点列表；地点不存在时给出明确报错（不是 FileNotFoundError）。"""
        if name not in self._locations:
            raise KeyError(
                f"Location is not registered: '{name}'. "
                f"Known locations: {sorted(self._locations.keys())}"
            )
        return list(self._locations[name].positions)

    def has_position(self, location: str, position_id: str) -> bool:
        if location not in self._locations:
            return False
        return any(p.id == position_id for p in self._locations[location].positions)

    # ---------------- 动作 ----------------
    def action_names(self) -> List[str]:
        return sorted(self._actions)

    def has_action(self, name: str) -> bool:
        """判断动作是否合法；自动剥离编号变体后缀。"""
        return base_name(name) in self._actions

    # ---------------- 镜头 ----------------
    def shot_names(self) -> List[str]:
        return sorted(self._shots)

    def has_shot(self, name: str) -> bool:
        """判断镜头是否合法；自动剥离编号变体后缀。"""
        return base_name(name) in self._shots

    # ---------------- 其它 ----------------
    def rotate_track_config(self, location: str) -> Optional[dict]:
        """某地点的 rotate/track/long 配置；没有则返回 None。"""
        return self._rotate_track.get(location)

    def __repr__(self) -> str:  # pragma: no cover - 仅方便调试
        return (f"AssetRegistry(locations={len(self._locations)}, "
                f"actions={len(self._actions)}, shots={len(self._shots)})")


__all__ = [
    "Asset",
    "LocationAsset",
    "ActionAsset",
    "ShotAsset",
    "AssetRegistry",
    "base_name",
]
