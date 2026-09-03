# -*- coding: utf-8 -*-
"""
V0.2 阶段 8 —— V0.1 ↔ Canonical Storyboard 双向转换（core/adapter.py）

边界层（§九原则 1 + DEVELOPMENT.md：V0.1 兼容逻辑只允许在边界层）：
    - V0.1 三种历史字段写法（scene_information / sceneinformation /
      scene information）在 core/models.py 消化一次变成统一 Scene；
    - 本文件再把统一 Scene 无损映射成 Canonical 镜头层（Storyboard→Beat→Shot），
      并支持无损映射回来。Canonical 内部不再出现任何 V0.1 历史写法。
    - 本文件不调 LLM、不读写磁盘，是纯内存转换。

无损保证（§十五 硬性验收）：
    每一个 V0.1 剧本行（台词行 / 走位行）变成一个 Canonical Shot：
        台词行  ->  kind="dialogue"：speaker / dialogue(=content) / actions / shot_type(=原 shot)
        走位行  ->  kind="move"：move / shot_type(=原 shot) / reason / insertion_*
    原始镜头名原样保留在 shot.shot_type（例如 "Close Shot" / "Long Shot 2"），
    保证"V0.1 Script → Canonical → V0.1-compatible"角色/场景/台词/动作/镜头/站位
    全部不丢。V0.1 没有 Beat 概念，因此自动派生时整个 Scene 放一个 Beat（beat_id=1），
    全部行按原顺序变成其中的 Shot；多 Beat 结构留给未来 StoryboardAgent 人工/智能规划。

本文件输出两种 V0.1 表示：
    - Scene 对象（storyboard_to_scene），可继续走阶段 1 的 to_v01_script；
    - 直接输出 V0.1 兼容 dict（storyboards_to_v01_script），逐键对齐 script/0.json。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from .models import (
    Line, Move, Scene, SceneInfo,
    from_v01_script, to_v01_script,
)
from .shot import Beat, Shot, Storyboard, infer_camera_from_shot_label


# ---------------------------------------------------------------------------
# 行 ↔ Shot
# ---------------------------------------------------------------------------
def line_to_shot(
    line: Line,
    *,
    scene_id: int,
    beat_id: int = 1,
    shot_id: int = 1,
    characters: Optional[List[str]] = None,
) -> Shot:
    """把一条 V0.1 行（台词/走位）无损映射成一个 Canonical Shot。

    原始镜头名保留在 shot.shot_type；camera 用 infer_camera_from_shot_label
    尽力推断（推断不出就空串，绝不编造）。characters 传 Scene.info.who
    （V0.1 每一行的 current position 就是全部在场角色）。
    """
    camera = infer_camera_from_shot_label(line.shot) if line.shot else None
    who = list(characters or ())
    if line.type == "move":
        return Shot(
            shot_id=shot_id, scene_id=scene_id, beat_id=beat_id,
            kind="move", shot_type=line.shot, camera=camera,
            characters=who, positioning=list(line.positions),
            move=line.move,
            reason=line.reason,
            insertion_reason=line.insertion_reason,
            insertion_position=line.insertion_position,
            status="planned",
        )
    return Shot(
        shot_id=shot_id, scene_id=scene_id, beat_id=beat_id,
        kind="dialogue", shot_type=line.shot, camera=camera,
        speaker=line.speaker, dialogue=line.content,
        actions=list(line.actions), characters=who,
        positioning=list(line.positions),
        status="planned",
    )


def shot_to_line(shot: Shot) -> Line:
    """把一个 Canonical Shot 无损还原成一条 V0.1 行（shot_to_line 的逆操作）。

    只支持 dialogue / move 两类内容镜头；纯视觉 general 镜头在 V0.1 的
    script 行格式里没有对应物，转换会明确报错（不静默丢内容）。
    """
    if shot.kind == "move" or shot.move is not None:
        return Line(
            type="move",
            move=shot.move or Move(character="", destination=""),
            shot=shot.shot_type,
            positions=list(shot.positioning),
            reason=shot.reason,
            insertion_reason=shot.insertion_reason,
            insertion_position=shot.insertion_position,
        )
    if shot.kind == "dialogue":
        return Line(
            type="dialogue",
            speaker=shot.speaker,
            content=shot.dialogue,
            actions=list(shot.actions),
            shot=shot.shot_type,
            positions=list(shot.positioning),
        )
    raise ValueError(
        f"general 镜头（shot_id={shot.shot_id}，visual_description 视觉镜头）没有 "
        "对应的 V0.1 script 行格式，无法转回 V0.1 表示。"
    )


# ---------------------------------------------------------------------------
# Scene ↔ Storyboard
# ---------------------------------------------------------------------------
def scene_to_storyboard(scene: Scene) -> Storyboard:
    """把一个统一 Scene 无损映射成一个 Canonical Storyboard。

    V0.1 没有 Beat 概念：整个 Scene 放一个自动派生 Beat（beat_id=1），
    每行按原顺序变成一个 Shot。Scene 的 who/where/what/initial position
    分别落到 Storyboard 的 characters / location / synopsis / initial_positions，
    保证能无损还原。
    """
    who = list(scene.info.who)
    shots = [
        line_to_shot(line, scene_id=scene.id, beat_id=1, shot_id=i + 1, characters=who)
        for i, line in enumerate(scene.lines)
    ]
    beats = [Beat(beat_id=1, purpose=f"Scene {scene.id}（V0.1 自动派生，无 Beat 信息）", shots=shots)]
    return Storyboard(
        scene_id=scene.id,
        location=scene.info.where,
        characters=who,
        synopsis=scene.info.what,
        initial_positions=list(scene.initial_positions),
        beats=beats,
    )


def storyboard_to_scene(storyboard: Storyboard) -> Scene:
    """把一个 Canonical Storyboard 无损还原成一个统一 Scene。

    lines 由各 Beat 的 Shot 保序还原（shot_to_line 保证逐行无损）。
    """
    lines = [shot_to_line(s) for beat in storyboard.beats for s in beat.shots]
    return Scene(
        id=storyboard.scene_id,
        info=SceneInfo(
            who=list(storyboard.characters),
            where=storyboard.location,
            what=storyboard.synopsis,
        ),
        lines=lines,
        initial_positions=list(storyboard.initial_positions),
    )


# ---------------------------------------------------------------------------
# 整本剧本级便捷函数
# ---------------------------------------------------------------------------
def scenes_to_storyboards(scenes: Iterable[Scene]) -> List[Storyboard]:
    """统一 Scene 列表 → Storyboard 列表。"""
    return [scene_to_storyboard(s) for s in scenes]


def storyboards_to_scenes(storyboards: Iterable[Storyboard]) -> List[Scene]:
    """Storyboard 列表 → 统一 Scene 列表。"""
    return [storyboard_to_scene(sb) for sb in storyboards]


def v01_script_to_storyboards(script_dicts: List[dict]) -> List[Storyboard]:
    """V0.1 最终剧本（script/0.json 的 list）→ Storyboard 列表。

    经 core.models 的 from_v01_script 先消化掉三种历史字段写法，
    再无损映射成 Canonical 镜头层。
    """
    scenes = from_v01_script(script_dicts)
    return scenes_to_storyboards(scenes)


def storyboards_to_v01_script(storyboards: Iterable[Storyboard]) -> List[dict]:
    """Storyboard 列表 → V0.1 兼容剧本 dict（script/0.json 结构）。

    先还原成统一 Scene，再走 to_v01_script（阶段 1 已证明对真实剧本逐字节无损）。
    """
    scenes = storyboards_to_scenes(storyboards)
    return to_v01_script(scenes)


__all__ = [
    "line_to_shot", "shot_to_line",
    "scene_to_storyboard", "storyboard_to_scene",
    "scenes_to_storyboards", "storyboards_to_scenes",
    "v01_script_to_storyboards", "storyboards_to_v01_script",
]
