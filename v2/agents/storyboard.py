# -*- coding: utf-8 -*-
"""
V0.2 Stage 11 —— Storyboard Agent（v2/agents/storyboard.py）

职责（Stage 11 开工指令 §四 / §六 / §七）：
    把 Stage 7 产出的、已批准的场景剧本（Scene）确定性地规划为
    Storyboard → Beat → Shot 结构：

        Scene（有台词/走位/场景信息）
              ↓  StoryboardAgent（本文件）
        Storyboard（beats[].shots[]）—— Shot 落在 Stage 8 受控词表内，
                                        只存「名字级引用 + 本镜头视觉增量」

    这是 V0.2 接入的第三个真实业务 Agent（前两个：Casting / SceneGeneration）。

边界（AGENTS.md / §十 铁律 5，与原有两个 Agent 完全一致）：
    - 输入只来自 AssembledContext（needs 声明制）与 WorkflowContext.inputs；
    - **Agent 绝不自己 open() 读磁盘**：Scene 由调度层放进 inputs["scene"]；
      缺 scene → 抛 StepError（可重试），绝不静默产出空 Storyboard；
    - LLM 只走 LLMClient（统一错误 / 重试 / token 日志 / 脱敏）；
    - 产出只交 StepOutput(artifact_name=, data=)，由引擎落盘（版本化不覆盖）；
    - 不写 Memory、不找 API key、不决定 Workflow 下一步。

复用原则（§五，禁止重造）：
    - 输出直接复用 Stage 8 的 Canonical Schema（Shot / Beat / Storyboard /
      CameraPlan / ShotAsset / 受控词表），**0 改**；
    - 校验直接复用 StoryboardValidator（validate_dict），**0 改**；
    - Agent / Step 骨架继承 agents/base.py 的 LLMAgent / LLMStep，**0 改**。

Token / Context 纪律（§十一，本阶段验收硬点）：
    Shot 只存「名字级引用（characters / location / assets[].ref）+ 本镜头视觉增量
    （visual_description）」，**绝不**把角色外貌 / 地点环境写进 Shot —— 那些活在
    Stage 10 的 VisualAssetRegistry，由 VisualComposer 在调用模型前一次性展开。
    硬证据：注册视觉资产前后 Storyboard.to_dict() 逐字节不变。

结构：
    StoryboardAgent  业务核心（继承 LLMAgent）：Scene → Storyboard
    StoryboardStep   Workflow Step 壳（继承 LLMStep）：声明 needs / needs_review
    normalize_storyboard(...)  确定性规范化（§7.2 全部 8 条，纯函数，可单测）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.assets import base_name
from core.models import Action, Move, PositionRef, Scene
from core.shot import (
    ASSET_KINDS,
    CAMERA_ANGLES,
    CAMERA_MOVEMENTS,
    COMPOSITIONS,
    SHOT_KINDS,
    SHOT_SIZES,
    Beat,
    CameraPlan,
    Shot,
    ShotAsset,
    Storyboard,
)
from core.validator import VALID_STATES, StoryboardValidator
from workflow.errors import StepError

from agents.base import LLMAgent, LLMStep


# ---------------------------------------------------------------------------
# 受控词表（唯一事实来源 = core.shot；本文件只读不写，绝不新增取值）
# ---------------------------------------------------------------------------
VOCAB_CONSTANTS: Tuple[Tuple[str, tuple], ...] = (
    ("SHOT_SIZES", SHOT_SIZES),
    ("CAMERA_ANGLES", CAMERA_ANGLES),
    ("CAMERA_MOVEMENTS", CAMERA_MOVEMENTS),
    ("COMPOSITIONS", COMPOSITIONS),
    ("SHOT_KINDS", SHOT_KINDS),
)
"""受控词表常量清单（名称 → 取值），供 user payload 渲染与 prompt 一致性自检复用。"""

# 越界值的安全兜底（§7.2 第 1 条；经总控裁决 3：取有值默认而非空串，且必须记 warning）
SAFE_DEFAULTS: Dict[str, str] = {
    "shot_size": "MS",
    "camera_angle": "eye_level",
    "camera_movement": "static",
    "composition": "single",
    "kind": "general",
    "action_state": "standing",
}

# 时长兜底（§7.2 第 4 条）
DEFAULT_DURATIONS: Dict[str, float] = {
    "dialogue": 3.0,
    "move": 2.5,
    "general": 2.0,
}

# subject 约束为短名词短语（Stage 11-A §1.5：它会进 VisualComposer，最终进图像 prompt）
SUBJECT_MAX_LEN = 40


def canonical_vocab_line(name: str, values: tuple) -> str:
    """把一组受控词表渲染成唯一规范文本（供 payload 渲染与 prompt 自检共用）。"""
    return f"{name} = " + ", ".join(values)


def render_vocab_block() -> str:
    """从 core.shot 常量渲染受控词表文本块（进 user payload，绝不手抄进 prompt 文件）。"""
    lines = [canonical_vocab_line(name, values) for name, values in VOCAB_CONSTANTS]
    return "\n".join(lines)


def check_prompt_vocab_consistency(prompt_text: str) -> None:
    """运行时自检：prompt 文件若出现受控词表文字，必须与 core.shot 常量逐字一致。

    本阶段受控词表**走 user payload（f-string 渲染常量）**，prompt 文件只放角色设定
    与 JSON 契约，因此正常情况本函数无事可做。它是防漂移护栏：一旦有人后来手工把
    词表抄进 prompt 文件、且抄错（值缺失/改名/新增），这里立刻抛 StepError 拦住，
    而不是让 LLM 拿到一份与校验器不一致的词表。

    :raises StepError: prompt 内出现的词表行与 core.shot 常量不一致。
    """
    for name, values in VOCAB_CONSTANTS:
        expected = canonical_vocab_line(name, values)
        for raw_line in (prompt_text or "").splitlines():
            s = raw_line.strip()
            if s.startswith(name + " =") or s.startswith(name + "="):
                if s != expected:
                    raise StepError(
                        f"prompt 内受控词表 {name} 与 core.shot 常量不一致："
                        f"prompt='{s}' / 期望='{expected}'（词表唯一事实来源是 core.shot）"
                    )


# ---------------------------------------------------------------------------
# 规范化内部工具（纯函数，不调 LLM、不碰磁盘）
# ---------------------------------------------------------------------------
def _as_list_warn(value: Any, label: str, where: str, warnings: List[str]) -> List[Any]:
    """列表字段读取：非 None 且非 list → 记 warning 后按空处理（不静默丢弃信息）。

    LLM 偶尔把数组字段写成字符串/对象（契约违规）。静默当空会让"本该出现的信息"
    无声消失，因此这里显式记 warning，让降级可追溯。
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    warnings.append(
        f"{where}: {label} 应为列表，实际为 {type(value).__name__}，已按空列表处理"
    )
    return []


def _vocab(value: Any, vocab: tuple, default: str, label: str,
           where: str, warnings: List[str]) -> str:
    """受控词表兜底（§7.2 第 1 条）：越界/空值 → 安全默认 + warning（不抛异常）。"""
    v = str(value).strip() if value is not None else ""
    if v in vocab:
        return v
    warnings.append(
        f"{where}: {label} 取值 '{v}' 不在受控词表 {list(vocab)}，"
        f"兜底为 '{default}'（兜底值，非 LLM 判断）"
    )
    return default


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _anchor_characters(names: List[Any], allowed: set, where: str,
                       warnings: List[str]) -> List[str]:
    """名字回锚（§7.2 第 2 条）：不在允许集合里的角色名剔除 + warning。"""
    kept: List[str] = []
    for name in names:
        n = str(name).strip()
        if not n:
            continue
        if allowed and n not in allowed:
            warnings.append(f"{where}: 角色 '{n}' 不在 Scene.who / 已存在角色清单中，已剔除")
            continue
        kept.append(n)
    return kept


def _anchor_positioning(raw_positions: List[Any], *, eff_loc: str, registry,
                        allowed_chars: set, where: str,
                        warnings: List[str]) -> List[PositionRef]:
    """站位引用回锚（T05b / R6）：不存在的站位点 / 编造角色 → 剔除 + warning。"""
    valid_pos: Optional[set] = None
    if registry is not None and eff_loc:
        valid_pos = {p.id for p in registry.get_positions(eff_loc)}
    kept: List[PositionRef] = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        ch = str(item.get("character", "")).strip()
        pos = str(item.get("position", "")).strip()
        if ch and allowed_chars and ch not in allowed_chars:
            warnings.append(f"{where}: 站位角色 '{ch}' 不在角色清单中，已剔除")
            continue
        if valid_pos is not None and pos and pos not in valid_pos:
            warnings.append(f"{where}: 站位点 '{pos}' 不存在于地点 '{eff_loc}'，已剔除")
            continue
        if not ch and not pos:
            continue
        kept.append(PositionRef(character=ch, position=pos))
    return kept


def _normalize_actions(raw_actions: List[Any], *, registry, allowed_chars: set,
                       where: str, warnings: List[str]) -> List[Action]:
    """动作回锚（防御性补充，超出 §7.2 的 8 条，见模块报告）：
    state 越界 → 兜底 standing；角色未登记 / 动作未注册 → 剔除整条 + warning。

    依据：ShotValidator 对 `actions[i].state ∉ VALID_STATES` 报 INVALID_STATE、
    对未登记角色报 UNKNOWN_CHARACTER、对未注册动作报 INVALID_ACTION —— 任一都会
    让整步硬失败（重试耗尽 → 永久 FAILED），与 §7.2 的兜底精神一致。
    """
    kept: List[Action] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        ch = str(item.get("character", "")).strip()
        action = str(item.get("action", "")).strip()
        state = str(item.get("state", "")).strip()
        reason = str(item.get("reason", "") or "")
        if not ch or not action:
            warnings.append(f"{where}: 动作缺少 character/action，已剔除")
            continue
        if allowed_chars and ch not in allowed_chars:
            warnings.append(f"{where}: 动作角色 '{ch}' 不在角色清单中，已剔除该动作")
            continue
        if registry is not None and not registry.has_action(action):
            warnings.append(f"{where}: 动作 '{action}' 未注册，已剔除该动作")
            continue
        if state not in VALID_STATES:
            warnings.append(
                f"{where}: 动作状态 '{state}' 不在 {sorted(VALID_STATES)}，兜底为 "
                f"'{SAFE_DEFAULTS['action_state']}'（兜底值，非 LLM 判断）"
            )
            state = SAFE_DEFAULTS["action_state"]
        kept.append(Action(character=ch, state=state, action=action, reason=reason))
    return kept


def _normalize_assets(raw_assets: List[Any], *, registry, allowed_chars: set,
                      where: str, warnings: List[str]) -> List[ShotAsset]:
    """资产引用回锚（防御性补充，超出 §7.2 的 8 条）：
    kind 越界 / ref 为空 → 剔除；character/location/action 类 ref 未登记 → 剔除 + warning。
    """
    kept: List[ShotAsset] = []
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        ref = str(item.get("ref", "")).strip()
        detail = str(item.get("detail", "") or "")
        if kind not in ASSET_KINDS or not ref:
            warnings.append(f"{where}: 资产引用 kind='{kind}' ref='{ref}' 非法，已剔除")
            continue
        if kind == "character" and allowed_chars and ref not in allowed_chars:
            warnings.append(f"{where}: 资产角色引用 '{ref}' 不在角色清单中，已剔除")
            continue
        if kind == "location" and registry is not None and not registry.has_location(ref):
            warnings.append(f"{where}: 资产地点引用 '{ref}' 未注册，已剔除")
            continue
        if kind == "action" and registry is not None and not registry.has_action(ref):
            warnings.append(f"{where}: 资产动作引用 '{ref}' 未注册，已剔除")
            continue
        kept.append(ShotAsset(kind=kind, ref=ref, detail=detail))
    return kept


def _sanitize_shot_type(shot_type: str, *, registry, where: str,
                        warnings: List[str]) -> str:
    """shot_type 安全化（§7.2 第 7 条 / 总控裁决 2）。

    registry 非空时，非空 shot_type 必须命中 registry.shot_names() 或 SHOT_SIZES，
    否则 ShotValidator 报 INVALID_SHOT_TYPE（validator.py L715-L727）。
    → 非空但既不在 shot_names() 也不在 SHOT_SIZES → 清空 + warning。
    registry 为空时 validator 不校验该字段，原样保留（不制造无谓 warning）。
    """
    st = str(shot_type or "").strip()
    if not st:
        return ""
    if registry is None:
        return st
    if base_name(st) in registry.shot_names() or st in SHOT_SIZES:
        return st
    warnings.append(
        f"{where}: shot_type '{st}' 既不是已注册镜头名也不是受控景别，"
        f"已清空（避免 INVALID_SHOT_TYPE）"
    )
    return ""


# ---------------------------------------------------------------------------
# 确定性规范化（§7.2 全部 8 条）—— 纯函数，可单独单测
# ---------------------------------------------------------------------------
def normalize_storyboard(
    raw: dict,
    scene: Scene,
    *,
    scene_id: int,
    registry=None,
    known_characters: Optional[List[str]] = None,
    episode: int = 1,
) -> Tuple[Storyboard, List[str]]:
    """把 LLM 原始 dict 规范化成一份 Canonical Storyboard（确定性，不调 LLM/磁盘）。

    §7.2 八条规则（顺序即实现顺序）：
        1. 受控词表兜底（shot_size / camera_angle / camera_movement / composition / kind）
        2. 名字回锚（characters / location，另含 positioning / actions / assets 的引用）
        3. id 重排（beat_id 按出现序 1..n；shot_id 全局 1..N）
        4. 时长兜底（duration <= 0 → 按 kind 默认值）
        5. 状态强制（所有 Shot.status = "planned"）
        6. speaker/dialogue 回锚（dialogue 镜头按序对齐 Scene.lines）
        7. shot_type 安全化（非空但不在注册镜头名/SHOT_SIZES → 清空 + warning）
        8. dialogue / move 降级（台词补不齐或 move 不完整 → kind="general" + warning）

    :return: (Storyboard, warnings)；warnings 由调用方写入
             `Storyboard.metadata["normalize_warnings"]`。
    :raises StepError: 结构无法解析（beats 非列表 / 无任何有效 Beat）——可重试。
    """
    warnings: List[str] = []
    raw = raw if isinstance(raw, dict) else {}

    allowed_chars = set(str(x) for x in (scene.info.who or [])) | \
        set(str(x) for x in (known_characters or []))
    allowed_loc = set()
    if scene.info.where:
        allowed_loc.add(str(scene.info.where))
    if registry is not None:
        allowed_loc |= set(registry.location_names())

    # Scene 级台词行（规则 6 回锚用），保序
    dialogue_lines = [ln for ln in scene.lines if ln.type == "dialogue"]

    raw_beats = raw.get("beats")
    if not isinstance(raw_beats, list):
        raise StepError("Storyboard LLM 输出缺少合法的 beats 列表（必须是 list）")

    beats: List[Beat] = []
    next_shot_id = 1
    dialogue_cursor = 0

    for bi, raw_beat in enumerate(raw_beats):
        if not isinstance(raw_beat, dict):
            continue
        raw_shots = _as_list_warn(raw_beat.get("shots"), "shots", f"beat[{bi}]", warnings)
        shots: List[Shot] = []
        for si, raw_shot in enumerate(raw_shots):
            if not isinstance(raw_shot, dict):
                continue
            where = f"beat[{bi}].shot[{si}]"

            # ---- 规则 1：受控词表兜底 ----
            kind = _vocab(raw_shot.get("kind"), SHOT_KINDS, SAFE_DEFAULTS["kind"],
                          "kind", where, warnings)
            cam_raw = raw_shot.get("camera") if isinstance(raw_shot.get("camera"), dict) else {}
            shot_size = _vocab(cam_raw.get("shot_size"), SHOT_SIZES,
                               SAFE_DEFAULTS["shot_size"], "shot_size", where, warnings)
            camera_angle = _vocab(cam_raw.get("camera_angle"), CAMERA_ANGLES,
                                  SAFE_DEFAULTS["camera_angle"], "camera_angle", where, warnings)
            camera_movement = _vocab(cam_raw.get("camera_movement"), CAMERA_MOVEMENTS,
                                     SAFE_DEFAULTS["camera_movement"], "camera_movement",
                                     where, warnings)
            composition = _vocab(raw_shot.get("composition"), COMPOSITIONS,
                                 SAFE_DEFAULTS["composition"], "composition", where, warnings)

            # ---- 规则 2：名字回锚 ----
            characters = _anchor_characters(
                _as_list_warn(raw_shot.get("characters"), "characters", where, warnings),
                allowed_chars, where, warnings)
            loc = str(raw_shot.get("location", "") or "").strip()
            if loc and allowed_loc and loc not in allowed_loc:
                warnings.append(
                    f"{where}: 地点 '{loc}' 不在 Scene.where / 已注册地点中，回锚为 "
                    f"'{scene.info.where}'"
                )
                loc = str(scene.info.where or "")
            eff_loc = loc or str(scene.info.where or "")

            positioning = _anchor_positioning(
                _as_list_warn(raw_shot.get("positioning"), "positioning", where, warnings),
                eff_loc=eff_loc, registry=registry, allowed_chars=allowed_chars,
                where=where, warnings=warnings)
            actions = _normalize_actions(
                _as_list_warn(raw_shot.get("actions"), "actions", where, warnings),
                registry=registry, allowed_chars=allowed_chars, where=where, warnings=warnings)
            assets = _normalize_assets(
                _as_list_warn(raw_shot.get("assets"), "assets", where, warnings),
                registry=registry, allowed_chars=allowed_chars, where=where, warnings=warnings)

            # ---- 规则 7：shot_type 安全化 ----
            shot_type = _sanitize_shot_type(raw_shot.get("shot_type"), registry=registry,
                                            where=where, warnings=warnings)

            # ---- move（走位）：先回锚，再交给规则 8 判定 ----
            move: Optional[Move] = None
            raw_move = raw_shot.get("move")
            if isinstance(raw_move, dict):
                m_ch = str(raw_move.get("character", "")).strip()
                m_dest = str(raw_move.get("destination", "")).strip()
                if allowed_chars and m_ch and m_ch not in allowed_chars:
                    warnings.append(f"{where}: 走位角色 '{m_ch}' 不在角色清单中，走位作废")
                elif registry is not None and eff_loc and m_dest and \
                        not registry.has_position(eff_loc, m_dest):
                    warnings.append(
                        f"{where}: 走位目的地 '{m_dest}' 不存在于地点 '{eff_loc}'，走位作废"
                    )
                else:
                    move = Move(character=m_ch, destination=m_dest,
                                reason=str(raw_move.get("reason", "") or ""))

            # ---- 规则 6：speaker / dialogue 回锚 ----
            speaker = str(raw_shot.get("speaker", "") or "").strip()
            dialogue = str(raw_shot.get("dialogue", "") or "").strip()
            if kind == "dialogue":
                if dialogue_cursor < len(dialogue_lines):
                    src = dialogue_lines[dialogue_cursor]
                    if not speaker:
                        speaker = str(src.speaker or "")
                        if speaker:
                            warnings.append(f"{where}: dialogue 镜头缺 speaker，已按序回锚为 '{speaker}'")
                    if not dialogue:
                        dialogue = str(src.content or "")
                        if dialogue:
                            warnings.append(f"{where}: dialogue 镜头缺台词，已按序回锚 Scene.lines")
                dialogue_cursor += 1

            # ---- 规则 4：时长兜底 ----
            duration = _as_float(raw_shot.get("duration"))
            if duration <= 0:
                duration = DEFAULT_DURATIONS.get(kind, DEFAULT_DURATIONS["general"])
                warnings.append(
                    f"{where}: duration 缺失或 <= 0，按 kind='{kind}' 兜底为 {duration}"
                )

            # ---- 规则 8：dialogue / move 降级 ----
            if kind == "dialogue" and (not speaker or not dialogue):
                warnings.append(
                    f"{where}: dialogue 镜头 speaker/台词无法补全，降级为 kind='general'"
                )
                kind = "general"
            if kind == "move" and (move is None or not move.character or not move.destination):
                warnings.append(f"{where}: move 镜头缺少完整走位，降级为 kind='general'")
                kind = "general"
                move = None

            # subject：短名词短语 + 长度截断（Stage 11-A §1.5）
            subject = str(raw_shot.get("subject", "") or "").strip()
            first_line = subject.splitlines()[0].strip() if subject else ""
            if len(first_line) > SUBJECT_MAX_LEN:
                warnings.append(
                    f"{where}: subject 超长（{len(first_line)} > {SUBJECT_MAX_LEN}），已截断"
                )
                first_line = first_line[:SUBJECT_MAX_LEN]
            subject = first_line

            shots.append(Shot(
                shot_id=next_shot_id,
                scene_id=int(scene_id),
                beat_id=len(beats) + 1,
                kind=kind,
                shot_type=shot_type,
                camera=CameraPlan(shot_size=shot_size, camera_angle=camera_angle,
                                  camera_movement=camera_movement),
                composition=composition,
                subject=subject,
                speaker=speaker,
                dialogue=dialogue,
                actions=actions,
                location=loc,
                characters=characters,
                positioning=positioning,
                move=move,
                reason=str(raw_shot.get("reason", "") or ""),
                insertion_reason=str(raw_shot.get("insertion_reason", "") or ""),
                insertion_position=str(raw_shot.get("insertion_position", "") or ""),
                visual_description=str(raw_shot.get("visual_description", "") or "").strip(),
                continuity=str(raw_shot.get("continuity", "") or "").strip(),
                duration=duration,
                assets=assets,
                status="planned",  # ---- 规则 5：状态强制 ----
            ))
            next_shot_id += 1

        if not shots:
            # 空 Beat 无意义：跳过（保持 Storyboard 结构紧凑）
            if isinstance(raw_beat, dict):
                warnings.append(f"beat[{bi}]: 无有效 Shot，已跳过该 Beat")
            continue

        beats.append(Beat(
            beat_id=len(beats) + 1,   # ---- 规则 3：beat_id 按出现序重排 ----
            purpose=str(raw_beat.get("purpose", "") or ""),
            shots=shots,
        ))

    if not beats:
        raise StepError("Storyboard LLM 输出没有任何有效 Beat/Shot（无法产出可用分镜）")

    synopsis = str(raw.get("synopsis", "") or "").strip() or str(scene.info.what or "")

    storyboard = Storyboard(
        scene_id=int(scene_id),
        location=str(scene.info.where or ""),
        characters=[str(x) for x in (scene.info.who or [])],
        synopsis=synopsis,
        initial_positions=[PositionRef(character=p.character, position=p.position)
                           for p in (scene.initial_positions or [])],
        beats=beats,
        metadata={
            "scene_id": int(scene_id),
            "episode": int(episode),
            "agent": StoryboardAgent.name,
            "normalize_warnings": warnings,
        },
    )
    return storyboard, warnings


# ---------------------------------------------------------------------------
# 业务核心：StoryboardAgent
# ---------------------------------------------------------------------------
class StoryboardAgent(LLMAgent):
    """Storyboard 业务核心：把一个已批准的 Scene 规划成 Storyboard → Beat → Shot。"""

    name = "storyboard_generation"
    needs = ["project_info", "approved_facts", "characters",
             "locations", "actions", "shots"]

    def __init__(
        self,
        llm_client,
        *,
        scene_id: int = 1,
        registry=None,
        known_characters: Optional[List[str]] = None,
        validator: Optional[StoryboardValidator] = None,
        prompt_path: Optional[Path] = None,
    ):
        """
        :param llm_client:       LLMClient / TextProvider（统一错误 / 重试 / token 日志）
        :param scene_id:         默认场景编号（spec 未给时兜底）
        :param registry:         AssetRegistry（合法地点/动作/镜头/站位点；校验用；调度层提供）
        :param known_characters: 项目 Memory 中已有角色名（校验用；调度层提供）
        :param validator:        StoryboardValidator（缺省按 registry + known_characters 建）
        :param prompt_path:      V0.2 Storyboard Prompt 路径
                                 （缺省 v2/prompts/v0.2/storyboard_generation.txt）
        """
        super().__init__(llm_client, prompt_name="storyboard_generation",
                         prompt_path=prompt_path)
        self.scene_id = int(scene_id)
        self.registry = registry
        self.known_characters: List[str] = [str(x) for x in (known_characters or [])]
        if validator is None:
            validator = StoryboardValidator(registry, self.known_characters)
        self.validator = validator

    # ---------------- 主入口 ----------------
    def run(self, context, *, scene=None, storyboard_spec: Optional[dict] = None) -> Storyboard:
        """根据组装好的 Context + Scene + 分镜规范，产出一份 Storyboard。

        :param context:         AssembledContext（由 ContextAssembler 按 needs 组装）
        :param scene:           Scene 对象（推荐）或其 to_v01() dict。
                                **由调度层经 inputs["scene"] 注入，Agent 绝不自己读磁盘**。
        :param storyboard_spec: 分镜规范 dict（调度层放入 inputs），可含
                                scene_id / episode / target_beat_count / max_shots_per_beat /
                                style_hint / granularity（全部软建议）。
        :return: Storyboard
        :raises StepError: 缺 scene / LLM 输出无法解析 / 结构非法（Workflow 可重试）
        """
        if scene is None:
            raise StepError(
                "StoryboardAgent 需要 scene（调度层必须把它放进 inputs['scene']，"
                "Agent 绝不自己读磁盘）"
            )
        if isinstance(scene, dict):
            scene = Scene.from_v01(scene)
        if not isinstance(scene, Scene):
            raise StepError(f"inputs['scene'] 必须是 Scene 或其 to_v01() dict，实际 {type(scene).__name__}")

        spec = dict(storyboard_spec or {})

        # 防漂移护栏：prompt 文件若出现受控词表文字，必须与 core.shot 常量一致
        check_prompt_vocab_consistency(self.system_prompt())

        topic = self._topic_from(context)
        facts = self._facts_from(context)
        characters = self._characters_from(context)
        locations = self._locations_from(context)
        actions = self._actions_from(context)
        shots = self._shots_from(context)

        raw = self.chat_json(
            step="storyboard_generation",
            user_payload=self._user_payload(
                topic, facts, characters, locations, actions, shots, scene, spec),
        )

        resolved_scene_id = int(spec.get("scene_id") or scene.id or self.scene_id)
        storyboard, _ = normalize_storyboard(
            raw, scene,
            scene_id=resolved_scene_id,
            registry=self.registry,
            known_characters=self.known_characters,
            episode=int(spec.get("episode") or 1),
        )
        return storyboard

    # ---------------- Prompt 构造 ----------------
    def _user_payload(
        self,
        topic: str,
        facts: List[dict],
        characters: List[dict],
        locations: List[dict],
        actions: List[str],
        shots: List[str],
        scene: Scene,
        spec: dict,
    ) -> str:
        facts_text = _json_dumps(facts)
        characters_text = _json_dumps(characters)
        locations_text = _json_dumps(locations)
        actions_text = ", ".join(actions) if actions else "(none)"
        shots_text = ", ".join(shots) if shots else "(none)"
        scene_text = _json_dumps(scene.to_v01())
        spec_text = _json_dumps(spec)
        return (
            f"## project topic\n{topic}\n\n"
            f"## approved_facts (ONLY these facts are confirmed)\n{facts_text}\n\n"
            f"## available characters (use ONLY these, by EXACT name)\n{characters_text}\n\n"
            f"## available locations (use ONLY these, with their positions)\n{locations_text}\n\n"
            f"## available actions (use ONLY these)\n{actions_text}\n\n"
            f"## available shots (use ONLY these)\n{shots_text}\n\n"
            f"## controlled vocabularies (use ONLY these exact values)\n"
            f"{render_vocab_block()}\n\n"
            f"## scene (approved script to storyboard)\n{scene_text}\n\n"
            f"## storyboard specification (soft guidance)\n{spec_text}"
        )

    # ---------------- 从 Context 取数据 ----------------
    @staticmethod
    def _topic_from(context) -> str:
        item = context.item("project_info")
        if item and isinstance(item.data, dict):
            return str(item.data.get("project_title", ""))
        return ""

    @staticmethod
    def _facts_from(context) -> List[dict]:
        item = context.item("approved_facts")
        if not item:
            return []
        return [dict(f) if isinstance(f, dict) else {"content": str(f)} for f in (item.data or [])]

    @staticmethod
    def _characters_from(context) -> List[dict]:
        item = context.item("characters")
        if not item:
            return []
        return [dict(c) if isinstance(c, dict) else {"name": str(c)} for c in (item.data or [])]

    @staticmethod
    def _locations_from(context) -> List[dict]:
        item = context.item("locations")
        if not item:
            return []
        return [dict(l) if isinstance(l, dict) else {"name": str(l)} for l in (item.data or [])]

    @staticmethod
    def _actions_from(context) -> List[str]:
        item = context.item("actions")
        if not item:
            return []
        return [str(a) for a in (item.data or [])]

    @staticmethod
    def _shots_from(context) -> List[str]:
        item = context.item("shots")
        if not item:
            return []
        return [str(s) for s in (item.data or [])]


# ---------------------------------------------------------------------------
# Workflow Step 壳：StoryboardStep
# ---------------------------------------------------------------------------
class StoryboardStep(LLMStep):
    """把 StoryboardAgent 接入 Workflow 引擎的 Step 壳。

    run()      从 context.inputs 取 AssembledContext + storyboard_spec + scene，交给 Agent，
               返回结构化产出（Storyboard.to_dict()），由引擎落盘 storyboard_{scene_id}.json。
    validate() 用 StoryboardValidator.validate_dict 做确定性校验（返回错误列表，非异常）；
               不合格 → 引擎重试（旧版本保留）。
    needs_review = True：成功后进入人工审核节点（approved / approved_with_edit / rejected）。
    """

    step_id = "storyboard_generation"
    name = "storyboard_generation"
    needs = StoryboardAgent.needs
    needs_review = True

    def __init__(
        self,
        agent: StoryboardAgent,
        *,
        scene_id: Optional[int] = None,
        step_id: Optional[str] = None,
        needs_review: Optional[bool] = None,
    ):
        self.scene_id = int(scene_id) if scene_id is not None else 1
        # step_id 带场景编号：多场景在同一 workflow 里是各自独立的 step（resume 不串场）
        super().__init__(
            agent=agent,
            step_id=step_id or f"storyboard_generation_{self.scene_id}",
            name=self.name,
            needs=list(self.needs),
            needs_review=needs_review,
        )

    def run(self, context):
        assembled = self.assembled_context(context)
        spec = dict(context.inputs.get("storyboard_spec") or {})
        scene = context.inputs.get("scene")
        if scene is None:
            raise StepError(
                "StoryboardStep 需要 context.inputs['scene']（Scene 对象或其 to_v01() dict）；"
                "调度层必须先把它放进 inputs，绝不静默产出空 Storyboard"
            )
        scene_id = int(spec.get("scene_id") or self.scene_id)
        spec["scene_id"] = scene_id          # 回注：artifact 命名与 Storyboard.scene_id 保持一致
        storyboard = self.agent.run(assembled, scene=scene, storyboard_spec=spec)
        return self.make_output(
            data=storyboard.to_dict(),
            artifact_name=f"storyboard_{scene_id}",
            metadata={
                "scene_id": scene_id,
                "episode": int(spec.get("episode") or 1),
                "agent": self.name,
            },
        )

    def validate(self, data: Any, context) -> List[str]:
        """确定性校验：直接吃落盘 dict（StoryboardValidator.validate_dict），错误转字符串列表。"""
        errors = self.agent.validator.validate_dict(data)
        return [str(e) for e in errors]


def _json_dumps(obj, *, fallback: str = "(none)") -> str:
    """把可序列化对象转成 JSON 文本；空对象返回 fallback（与既有 prompt 风格一致）。"""
    if not obj:
        return fallback
    return json.dumps(obj, ensure_ascii=False, indent=2)


__all__ = [
    "StoryboardAgent",
    "StoryboardStep",
    "normalize_storyboard",
    "render_vocab_block",
    "canonical_vocab_line",
    "check_prompt_vocab_consistency",
    "VOCAB_CONSTANTS",
    "SAFE_DEFAULTS",
    "DEFAULT_DURATIONS",
    "SUBJECT_MAX_LEN",
]
