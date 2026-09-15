# -*- coding: utf-8 -*-
"""
V0.2 Stage 12-B —— 编排层前置校验门（v2/orchestration/preflight.py）

作用（开工指令12 §7.2 + stage12_plan §1.4 / §2.1）：

    Stage 11 的 StoryboardAgent 对"坏 Scene"是 **fail-loud** 的：
        Scene.info.who 含未登记角色 → StoryboardValidator 报 UNKNOWN_CHARACTER
        → 引擎按 retryable 重试 3 次 → 重试耗尽 → 永久 FAILED。
    重试救不了它（不是瞬时错误，是数据本身坏了）。

    所以编排层在**把 Scene 喂给 Stage 11 之前**先做一次"调度前置条件"检查：
        校验通过 → 正常喂给 Stage 11；
        校验失败 → 编排层明确报错并列出缺口（OrchestrationError），
                   **绝不进入 Stage 11 的重试风暴**。

本模块是**纯函数**：不调 LLM、不碰磁盘、不抛异常（返回缺口清单）。

五条规则（判据与 core/validator.py 的 EnumValidator / ReferenceValidator **同源**，
本模块只是把同一套判据"前移到调度层"，绝不另立一套会漂移的规则）：

    规则 1  角色引用白名单：scene.info.who / dialogue 行 speaker /
            move 行 character / positions 行 character / actions 行 character /
            initial_positions 行 character 都必须 ∈ known_characters
            （对应 ReferenceValidator.check_character）
    规则 2  scene.info.where ∈ registry.location_names()（对应 INVALID_LOCATION /
            MISSING_LOCATION）
    规则 3  dialogue 行 speaker ∈ known_characters（并入规则 1 的统一角色引用检查）
    规则 4  scene.initial_positions / lines[].positions 的 position
            ∈ 该地点的站位点（对应 INVALID_POSITION）
    规则 5  move 行 destination ∈ 该地点的站位点（对应 INVALID_POSITION）

★ 关键陷阱（R3，A 会话实测 P12）：`Line.type` 是 V0.2 内部字段，**不写进 to_v01()**，
  V0.1 侧靠"有没有 move 键"判断类型。因此本函数**必须先把落盘 dict 经
  `Scene.from_v01(data, id=...)` 还原**，否则直接在落盘 dict 上判 `line["type"]`
  会让 move 行**完全漏检** → 漏到 Stage 11 → 重试风暴。

边界（指令 §7.2）：该门是**编排层职责**，**不得**加进 `v2/core/validator.py`
（那里是"数据合法性"，这里是"调度前置条件"，两者不同层）。
"""
from __future__ import annotations

from typing import Any, Iterable, List, Optional

from core.models import Scene


def _as_name_set(known_characters: Optional[Iterable[Any]]) -> set:
    """把角色清单（[{"name": ...}] / ["A", "B"] / None）统一成名字集合。"""
    out: set = set()
    for c in (known_characters or []):
        if isinstance(c, dict):
            name = str(c.get("name", "") or "").strip()
        else:
            name = str(c or "").strip()
        if name:
            out.add(name)
    return out


def _normalize_scene(scene: Any) -> "tuple[Optional[Scene], Optional[str]]":
    """把入参归一化成 Scene（落盘 dict → Scene.from_v01，还原 Line.type）。

    :return: (scene, error)；error 非 None 时表示无法解析（调用方转成缺口清单）。
    """
    if scene is None:
        return None, "scene 缺失：无法执行前置校验（scene is None）"
    if isinstance(scene, dict):
        try:
            # ★ 必须走 from_v01：落盘 dict 没有 Line.type，move 行会漏检
            return Scene.from_v01(scene, id=int(scene.get("id") or 0)), None
        except Exception as exc:  # 解析失败 → 缺口（不抛异常）
            return None, f"scene 无法解析成 Scene：{type(exc).__name__}: {exc}"
    if not isinstance(scene, Scene):
        return None, (
            f"scene 类型非法：{type(scene).__name__}"
            f"（需 core.models.Scene 对象或其 to_v01() dict）"
        )
    return scene, None


def preflight_check(
    scene: Any,
    known_characters: Optional[Iterable[Any]] = None,
    registry: Any = None,
) -> List[str]:
    """调度前置校验门：返回**缺口清单**（空列表 = 通过）。

    :param scene:             Scene 对象（推荐）或其 to_v01() 落盘 dict。
    :param known_characters:  已批准角色清单（Memory.get_characters() / Casting 产物）。
    :param registry:          AssetRegistry（合法地点 / 站位点来源）。
    :return: 缺口字符串列表；**任何情况下都不抛异常**（scene 非法 / registry 缺失
             也会被表达成缺口条目，而不是异常）。
    """
    gaps: List[str] = []

    # ---- 0. 归一化（★先做：还原 Line.type，否则 move 行漏检） ----
    scene_obj, err = _normalize_scene(scene)
    if err is not None:
        return [err]
    assert scene_obj is not None  # 归一化成功

    # ---- 规则 1 / 3：角色引用白名单 ----
    if known_characters is None:
        gaps.append("known_characters 未提供（None）：无法校验角色引用白名单")
        known: Optional[set] = None
    else:
        known = _as_name_set(known_characters)

    def _check_character(name: Any, path: str) -> None:
        if known is None:
            return
        n = str(name or "").strip()
        if n and n not in known:
            gaps.append(
                f"UNKNOWN_CHARACTER {path}：'{n}' 不在已批准角色清单中"
                f"（当前 {len(known)} 个：{sorted(known)}）"
            )

    info = scene_obj.info
    lines = list(scene_obj.lines or [])

    for i, name in enumerate(list(info.who or [])):
        _check_character(name, f"scene.info.who[{i}]")

    for i, line in enumerate(lines):
        lp = f"scene.lines[{i}]"
        if line.type == "dialogue":
            _check_character(line.speaker, f"{lp}.speaker")
        for j, action in enumerate(list(line.actions or [])):
            _check_character(action.character, f"{lp}.actions[{j}].character")
        for k, pos in enumerate(list(line.positions or [])):
            _check_character(pos.character, f"{lp}.positions[{k}].character")
        if line.move is not None:
            _check_character(line.move.character, f"{lp}.move.character")

    for k, pos in enumerate(list(scene_obj.initial_positions or [])):
        _check_character(pos.character, f"scene.initial_positions[{k}].character")

    # ---- 规则 2：地点必须已注册（空 where = MISSING_LOCATION） ----
    where = str(info.where or "").strip()
    where_ok = False
    if not where:
        gaps.append(
            "MISSING_LOCATION scene.info.where：为空"
            "（Stage 11 无法解析站位点，必然失败）"
        )
    elif registry is None:
        # registry 缺失已单独记缺口，这里不再重复噪音
        pass
    else:
        try:
            if registry.has_location(where):
                where_ok = True
            else:
                gaps.append(
                    f"INVALID_LOCATION scene.info.where：'{where}' 不在已注册地点中"
                    f"（可用 {sorted(registry.location_names())}）"
                )
        except Exception as exc:  # registry 行为异常 → 表达成缺口，不抛
            gaps.append(f"registry.has_location 异常：{type(exc).__name__}: {exc}")

    if registry is None:
        gaps.append("registry 缺失：无法校验地点 / 站位点（AssetRegistry is None）")

    # ---- 规则 4 / 5：站位点引用（与 EnumValidator 同源：地点非法则不再查位置，避免噪音） ----
    if where_ok and registry is not None:
        def _check_position(position: Any, path: str) -> None:
            pid = str(position or "").strip()
            try:
                ok = registry.has_position(where, pid)
            except Exception as exc:  # pragma: no cover - 防御
                gaps.append(f"registry.has_position 异常：{type(exc).__name__}: {exc}")
                return
            if not ok:
                try:
                    available = sorted(p.id for p in registry.get_positions(where))
                except Exception:  # pragma: no cover - 防御
                    available = []
                gaps.append(
                    f"INVALID_POSITION {path}：站位点 '{pid}' 不存在于地点 '{where}'"
                    f"（可用 {available}）"
                )

        for k, pos in enumerate(list(scene_obj.initial_positions or [])):
            _check_position(pos.position, f"scene.initial_positions[{k}].position")

        for i, line in enumerate(lines):
            for k, pos in enumerate(list(line.positions or [])):
                _check_position(pos.position, f"scene.lines[{i}].positions[{k}].position")
            if line.move is not None:
                _check_position(line.move.destination, f"scene.lines[{i}].move.destination")

    return gaps


def format_gaps(gaps: List[str]) -> str:
    """把缺口清单格式化成单行文本（报错 / run_report 用）。"""
    return "; ".join(str(g) for g in (gaps or []))


__all__ = ["preflight_check", "format_gaps"]
