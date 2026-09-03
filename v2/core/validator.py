# -*- coding: utf-8 -*-
"""
V0.2 阶段 1 —— 纯 Python QA 校验层（core/validator.py）

作用：对 V0.2 的 Scene（以及整本剧本）做"确定性校验"——
      不调用任何 LLM，只按明确的规则返回结构化的错误清单。

三个校验器各管一件事：
    EnumValidator      值是否在合法集合里（地点/动作/镜头/站位点/状态）
    ReferenceValidator 引用关系对不对（角色存不存在、同一位置是否被两人占用）
    SchemaValidator    结构全不全、类型对不对（该有的字段有没有、是不是 list/dict/str）

校验结果不是 True/False，而是一份 ValidationError 列表：
    []           表示没问题
    [错误, ...]  表示有哪些问题（以后 Workflow 阶段可以原样反馈给 Agent）

重要设计（阶段 1 指令明确要求）：
    1. 绝不使用 return_most_similar() 做核心校验。
    2. 绝不使用 assert 代替业务校验。
    3. 非法地点必须报 INVALID_LOCATION，绝不能冒 FileNotFoundError。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Set, Union

from .assets import AssetRegistry, base_name
from .models import CastingResult, Line, Scene
from .shot import (
    ASSET_KINDS, CAMERA_ANGLES, CAMERA_MOVEMENTS, COMPOSITIONS,
    SHOT_KINDS, SHOT_SIZES, SHOT_STATUSES,
    Beat, Shot, ShotAsset, Storyboard,
)


# ---------------------------------------------------------------------------
# 结构化错误
# ---------------------------------------------------------------------------
@dataclass
class ValidationError:
    """一条结构化校验错误。"""
    validator: str        # 哪个校验器：EnumValidator / ReferenceValidator / SchemaValidator
    code: str             # 错误码：INVALID_LOCATION / INVALID_ACTION / ...
    path: str             # 出错位置，如 scene[1].lines[3].actions[0].action
    message: str          # 人类可读说明
    actual: Any = None    # 实际值
    expected: Any = None  # 期望值（可能是一组合法值）

    def __str__(self) -> str:
        lines = [f"[{self.validator}] {self.code} at {self.path}"]
        lines.append(f"  {self.message}")
        if self.actual is not None and self.actual != "":
            lines.append(f"  actual  : {self.actual!r}")
        if self.expected is not None:
            exp = self.expected
            if isinstance(exp, (list, set, tuple)) and len(exp) > 8:
                exp = list(exp)[:8] + ["..."]
            lines.append(f"  expected: {exp}")
        return "\n".join(lines)


def format_errors(errors: Iterable[ValidationError]) -> str:
    """把错误列表打印成人类可读文本（空列表返回 'OK'）。"""
    errors = list(errors)
    if not errors:
        return "OK (0 errors)"
    return "\n\n".join(str(e) for e in errors)


# ---------------------------------------------------------------------------
# 枚举/合法值校验
# ---------------------------------------------------------------------------
# 根据 V0.1 真实数据建立的合法状态集合（实测 script/0.json 与 scenes_8.json
# 里只出现了 "standing"；"sitting" 是坐姿动作对应的状态，V0.1 支持但本集没用到）。
# 不要擅自扩展大量状态（指令 §11.6）。
VALID_STATES: frozenset = frozenset({"standing", "sitting"})


class EnumValidator:
    """检查值是否在合法集合里：地点 / 动作 / 镜头 / 站位点 / 状态。"""

    def __init__(self, registry: AssetRegistry, valid_states: Optional[Set[str]] = None):
        self.registry = registry
        self.valid_states: Set[str] = set(valid_states) if valid_states else set(VALID_STATES)

    def validate(self, scene: Scene) -> List[ValidationError]:
        errors: List[ValidationError] = []
        where = scene.info.where
        scene_path = f"scene[{scene.id}]"

        # ---- 1. 地点 ----
        if not where:
            errors.append(ValidationError(
                validator="EnumValidator", code="MISSING_LOCATION",
                path=f"{scene_path}.info.where",
                message="Scene has no location (where is empty).",
                actual=where,
            ))
            return errors  # 没有地点，后面的位置校验无从谈起，先停下
        if not self.registry.has_location(where):
            errors.append(ValidationError(
                validator="EnumValidator", code="INVALID_LOCATION",
                path=f"{scene_path}.info.where",
                message="Location is not registered.",
                actual=where,
                expected=self.registry.location_names(),
            ))
            # 地点非法 → 本场景内按地点判断的位置/站位点不再继续查，
            # 避免一连串"因为地点错了所以位置也错"的噪音错误。
            return errors

        # ---- 2. 每行：动作 / 镜头 / 当前位置 ----
        for i, line in enumerate(line for line in scene.lines):
            line_path = f"{scene_path}.lines[{i}]"
            # 动作
            for j, action in enumerate(line.actions):
                if not self.registry.has_action(action.action):
                    errors.append(ValidationError(
                        validator="EnumValidator", code="INVALID_ACTION",
                        path=f"{line_path}.actions[{j}].action",
                        message="Action is not registered.",
                        actual=action.action,
                        expected=self.registry.action_names(),
                    ))
                if action.state not in self.valid_states:
                    errors.append(ValidationError(
                        validator="EnumValidator", code="INVALID_STATE",
                        path=f"{line_path}.actions[{j}].state",
                        message="Action state is not a known state.",
                        actual=action.state,
                        expected=sorted(self.valid_states),
                    ))
            # 镜头
            if line.shot and not self.registry.has_shot(line.shot):
                errors.append(ValidationError(
                    validator="EnumValidator", code="INVALID_SHOT",
                    path=f"{line_path}.shot",
                    message="Shot is not registered.",
                    actual=line.shot,
                    expected=self.registry.shot_names(),
                ))
            # 当前位置（current position）里的每个站位点
            for k, pos in enumerate(line.positions):
                if not self.registry.has_position(where, pos.position):
                    errors.append(ValidationError(
                        validator="EnumValidator", code="INVALID_POSITION",
                        path=f"{line_path}.positions[{k}].position",
                        message=f"Position does not exist in location '{where}'.",
                        actual=pos.position,
                        expected=[p.id for p in self.registry.get_positions(where)],
                    ))
            # 走位行的目标位置
            if line.move is not None:
                if not self.registry.has_position(where, line.move.destination):
                    errors.append(ValidationError(
                        validator="EnumValidator", code="INVALID_POSITION",
                        path=f"{line_path}.move.destination",
                        message=f"Move destination does not exist in location '{where}'.",
                        actual=line.move.destination,
                        expected=[p.id for p in self.registry.get_positions(where)],
                    ))

        # ---- 3. 初始站位（initial position） ----
        for k, pos in enumerate(scene.initial_positions):
            if not self.registry.has_position(where, pos.position):
                errors.append(ValidationError(
                    validator="EnumValidator", code="INVALID_POSITION",
                    path=f"{scene_path}.initial_positions[{k}].position",
                    message=f"Position does not exist in location '{where}'.",
                    actual=pos.position,
                    expected=[p.id for p in self.registry.get_positions(where)],
                ))

        return errors


# ---------------------------------------------------------------------------
# 引用校验
# ---------------------------------------------------------------------------
class ReferenceValidator:
    """检查引用关系：角色必须在角色清单里；同一位置不能被两人同时占用。"""

    def __init__(self, character_names: Iterable[str]):
        self.characters: Set[str] = set(character_names)

    def validate(self, scene: Scene) -> List[ValidationError]:
        errors: List[ValidationError] = []
        scene_path = f"scene[{scene.id}]"

        def check_character(name: str, path: str) -> None:
            if name and name not in self.characters:
                errors.append(ValidationError(
                    validator="ReferenceValidator", code="UNKNOWN_CHARACTER",
                    path=path,
                    message="Character is not in the project character list.",
                    actual=name,
                    expected=sorted(self.characters),
                ))

        # ---- who ----
        for k, name in enumerate(scene.info.who):
            check_character(name, f"{scene_path}.info.who[{k}]")

        # ---- 每行 ----
        for i, line in enumerate(scene.lines):
            line_path = f"{scene_path}.lines[{i}]"
            if line.type == "dialogue":
                check_character(line.speaker, f"{line_path}.speaker")
            for j, action in enumerate(line.actions):
                check_character(action.character, f"{line_path}.actions[{j}].character")
            for k, pos in enumerate(line.positions):
                check_character(pos.character, f"{line_path}.positions[{k}].character")
            if line.move is not None:
                check_character(line.move.character, f"{line_path}.move.character")

        # ---- 初始站位 ----
        for k, pos in enumerate(scene.initial_positions):
            check_character(pos.character, f"{scene_path}.initial_positions[{k}].character")

        # ---- 位置冲突：同一时刻同一位置不能被两个角色同时占用 ----
        self._check_conflicts(scene.initial_positions, f"{scene_path}.initial_positions", errors)
        for i, line in enumerate(scene.lines):
            self._check_conflicts(line.positions, f"{scene_path}.lines[{i}].positions", errors)

        return errors

    def _check_conflicts(self, refs: List, path: str, errors: List[ValidationError]) -> None:
        """在一个"快照"（初始站位 或 某一行当前位置）里，同一位置出现两次即冲突。"""
        seen: dict = {}
        for k, ref in enumerate(refs):
            if ref.position in seen:
                errors.append(ValidationError(
                    validator="ReferenceValidator", code="POSITION_CONFLICT",
                    path=f"{path}[{k}].position",
                    message="Two characters occupy the same position at the same time.",
                    actual=f"{ref.character} at {ref.position} (already {seen[ref.position]})",
                    expected="each position occupied by at most one character",
                ))
            else:
                seen[ref.position] = ref.character


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------
class SchemaValidator:
    """检查结构是否完整、字段类型是否正确（不评价内容好坏）。"""

    def validate(self, scene: Scene) -> List[ValidationError]:
        errors: List[ValidationError] = []
        scene_path = f"scene[{scene.id}]"

        # ---- scene 顶层 ----
        if not isinstance(scene.id, int):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.id",
                message="Scene id must be an int.", actual=scene.id, expected="int",
            ))
        if not isinstance(scene.info.who, list):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.info.who",
                message="Scene info 'who' must be a list.", actual=scene.info.who, expected="list",
            ))
        if not isinstance(scene.info.where, str):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.info.where",
                message="Scene info 'where' must be a str.", actual=scene.info.where, expected="str",
            ))
        if not isinstance(scene.info.what, str):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.info.what",
                message="Scene info 'what' must be a str.", actual=scene.info.what, expected="str",
            ))
        if not isinstance(scene.initial_positions, list):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.initial_positions",
                message="initial_positions must be a list.",
                actual=scene.initial_positions, expected="list",
            ))
        if not isinstance(scene.lines, list):
            errors.append(ValidationError(
                validator="SchemaValidator", code="WRONG_TYPE",
                path=f"{scene_path}.lines",
                message="lines must be a list.", actual=scene.lines, expected="list",
            ))
            return errors

        # ---- 每一行 ----
        for i, line in enumerate(scene.lines):
            lp = f"{scene_path}.lines[{i}]"
            if not isinstance(line, Line):
                errors.append(ValidationError(
                    validator="SchemaValidator", code="WRONG_TYPE",
                    path=lp, message="Line must be a Line object.", actual=type(line).__name__, expected="Line",
                ))
                continue
            if line.type not in ("dialogue", "move"):
                errors.append(ValidationError(
                    validator="SchemaValidator", code="INVALID_STRUCTURE",
                    path=f"{lp}.type",
                    message="Line type must be 'dialogue' or 'move'.",
                    actual=line.type, expected=["dialogue", "move"],
                ))

            if line.type == "dialogue":
                if not isinstance(line.speaker, str) or not line.speaker:
                    errors.append(ValidationError(
                        validator="SchemaValidator", code="MISSING_FIELD",
                        path=f"{lp}.speaker",
                        message="Dialogue line must have a non-empty speaker.",
                        actual=line.speaker, expected="non-empty str",
                    ))
                if not isinstance(line.content, str):
                    errors.append(ValidationError(
                        validator="SchemaValidator", code="WRONG_TYPE",
                        path=f"{lp}.content",
                        message="Dialogue line 'content' must be a str.",
                        actual=line.content, expected="str",
                    ))
                if not isinstance(line.actions, list):
                    errors.append(ValidationError(
                        validator="SchemaValidator", code="WRONG_TYPE",
                        path=f"{lp}.actions",
                        message="Dialogue line 'actions' must be a list.",
                        actual=line.actions, expected="list",
                    ))
                if not isinstance(line.shot, str):
                    errors.append(ValidationError(
                        validator="SchemaValidator", code="WRONG_TYPE",
                        path=f"{lp}.shot",
                        message="Dialogue line 'shot' must be a str.",
                        actual=line.shot, expected="str",
                    ))
            elif line.type == "move":
                if line.move is None:
                    errors.append(ValidationError(
                        validator="SchemaValidator", code="MISSING_FIELD",
                        path=f"{lp}.move",
                        message="Move line must have a move object.",
                        actual=None, expected="Move",
                    ))
                else:
                    if not isinstance(line.move.character, str) or not line.move.character:
                        errors.append(ValidationError(
                            validator="SchemaValidator", code="MISSING_FIELD",
                            path=f"{lp}.move.character",
                            message="Move must have a non-empty character.",
                            actual=line.move.character, expected="non-empty str",
                        ))
                    if not isinstance(line.move.destination, str) or not line.move.destination:
                        errors.append(ValidationError(
                            validator="SchemaValidator", code="MISSING_FIELD",
                            path=f"{lp}.move.destination",
                            message="Move must have a non-empty destination.",
                            actual=line.move.destination, expected="non-empty str",
                        ))
            if not isinstance(line.positions, list):
                errors.append(ValidationError(
                    validator="SchemaValidator", code="WRONG_TYPE",
                    path=f"{lp}.positions",
                    message="Line 'positions' must be a list.",
                    actual=line.positions, expected="list",
                ))

        return errors


# ---------------------------------------------------------------------------
# 组合校验器
# ---------------------------------------------------------------------------
class Validator:
    """一个入口跑完三个校验器，返回全部错误。"""

    def __init__(self, registry: AssetRegistry, character_names: Iterable[str],
                 valid_states: Optional[Set[str]] = None):
        self.registry = registry
        self.character_names: Set[str] = set(character_names)
        self.enum = EnumValidator(registry, valid_states)
        self.reference = ReferenceValidator(character_names)
        self.schema = SchemaValidator()

    def validate(self, scene: Scene) -> List[ValidationError]:
        return (
            self.enum.validate(scene)
            + self.reference.validate(scene)
            + self.schema.validate(scene)
        )

    def validate_many(self, scenes: Iterable[Scene]) -> List[ValidationError]:
        errors: List[ValidationError] = []
        for scene in scenes:
            errors.extend(self.validate(scene))
        return errors


# ---------------------------------------------------------------------------
# Casting 校验器（阶段 6：Casting Agent 的结构化产出校验）
# ---------------------------------------------------------------------------
# 复用 ValidationError / format_errors 这套结构化错误机制，
# 不另起一套孤立错误类型（指令 §十九：复用现有机制）。
# 现有 Enum/Reference/Schema 强耦合 Scene 结构，与 Casting 产出不同，
# 因此新增独立 CastingValidator，但错误格式与全项目一致。
class CastingValidator:
    """Casting 产出的确定性校验（纯规则，不调 LLM）。

    检查项（阶段 6 指令 §九）：
        1. 数据结构是否合法（episode 是 int、entries 是非空 list）
        2. 必要字段是否缺失（角色名非空；新角色档案字段齐全）
        3. 角色字段是否合法（status 只允许 existing/new）
        4. 角色 ID 是否重复（同一份 Casting 里名字不重复）
        5. 已有角色引用是否合法（existing 的角色必须真的存在于项目已有角色）
        6. 项目级角色注册是否存在冲突（new 角色名不得与已有角色重名——
           重名意味着"该复用"，而不是重复建档）
    """

    CASTING_STATUSES = ("existing", "new")
    REQUIRED_FIELDS = ("name", "age", "gender", "occupation",
                       "personality traits", "speaking style")

    def __init__(self, known_characters: Iterable[str]):
        """known_characters：项目 Memory 中已有的角色名（现有角色引用 / 冲突判定用）。"""
        self.known: Set[str] = set(known_characters or ())

    def validate(self, result: CastingResult) -> List[ValidationError]:
        """校验一个 CastingResult 对象（等价于 validate_dict(result.to_dict())）。"""
        return self.validate_dict(result.to_dict())

    def validate_dict(self, data: dict) -> List[ValidationError]:
        """校验 Casting 落盘用的 dict（Step.validate 直接调它）。"""
        errors: List[ValidationError] = []

        # ---- 1. 顶层结构 ----
        if not isinstance(data.get("episode"), int):
            errors.append(ValidationError(
                validator="CastingValidator", code="WRONG_TYPE",
                path="episode", message="episode 必须是 int。",
                actual=data.get("episode"), expected="int",
            ))
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            errors.append(ValidationError(
                validator="CastingValidator", code="MISSING_FIELD",
                path="entries", message="entries 必须是非空列表（一次 Casting 至少 1 个角色）。",
                actual=entries, expected="non-empty list",
            ))
            return errors  # 没有条目，逐条检查无从谈起

        # ---- 2. 逐条检查 ----
        seen: Set[str] = set()
        for i, e in enumerate(entries):
            p = f"entries[{i}]"
            if not isinstance(e, dict):
                errors.append(ValidationError(
                    validator="CastingValidator", code="WRONG_TYPE",
                    path=p, message="每个 entry 必须是对象。",
                    actual=type(e).__name__, expected="dict",
                ))
                continue

            name = str(e.get("name", "")).strip()
            if not name:
                errors.append(ValidationError(
                    validator="CastingValidator", code="MISSING_FIELD",
                    path=f"{p}.name", message="角色名不能为空。",
                    actual=e.get("name"), expected="non-empty str",
                ))
                continue
            if name in seen:
                errors.append(ValidationError(
                    validator="CastingValidator", code="DUPLICATE_ROLE",
                    path=f"{p}.name", message="同一份 Casting 里角色名重复。",
                    actual=name, expected="unique names",
                ))
            seen.add(name)

            status = str(e.get("status", "")).strip()
            if status not in self.CASTING_STATUSES:
                errors.append(ValidationError(
                    validator="CastingValidator", code="INVALID_STATUS",
                    path=f"{p}.status",
                    message="status 只允许 existing / new。",
                    actual=status, expected=list(self.CASTING_STATUSES),
                ))

            if status == "existing":
                if name not in self.known:
                    errors.append(ValidationError(
                        validator="CastingValidator", code="UNKNOWN_CHARACTER",
                        path=f"{p}.name",
                        message="标记为 existing 的角色必须真的存在于项目已有角色。",
                        actual=name,
                        expected=sorted(self.known) if self.known else "（项目还没有任何角色）",
                    ))
            elif status == "new":
                if name in self.known:
                    errors.append(ValidationError(
                        validator="CastingValidator", code="DUPLICATE_ROLE",
                        path=f"{p}.name",
                        message="新角色名与项目已有角色冲突：应复用已有角色，而非重复建档。",
                        actual=name, expected="new role name not in known characters",
                    ))
                char = e.get("character")
                if not isinstance(char, dict):
                    errors.append(ValidationError(
                        validator="CastingValidator", code="MISSING_FIELD",
                        path=f"{p}.character",
                        message="新角色必须带完整 character 档案。",
                        actual=char, expected="dict",
                    ))
                else:
                    for fld in self.REQUIRED_FIELDS:
                        v = str(char.get(fld, "")).strip()
                        if not v:
                            errors.append(ValidationError(
                                validator="CastingValidator", code="MISSING_FIELD",
                                path=f"{p}.character.{fld}",
                                message=f"新角色档案缺必要字段 {fld}。",
                                actual=char.get(fld), expected="non-empty str",
                            ))
        return errors


# ---------------------------------------------------------------------------
# Canonical 镜头层校验器（阶段 8：Beat/Shot/Storyboard/Asset）
# ---------------------------------------------------------------------------
# 说明：
#   - 与 CastingValidator 一样复用 ValidationError / format_errors，不另起错误类型。
#   - 全部是确定性纯规则（不调 LLM），错误码可原样反馈给 Agent / 审核人。
#   - registry（AssetRegistry）与 known_characters 都允许不传：
#     * 不传 registry  -> 不查地点/动作/镜头/站位是否注册（纯结构校验）
#     * 不传角色清单   -> 不查角色引用
#     传了才查"资产/角色是否真的存在"，缺省构建时给 None 即可。
#   - registry 的地点注册名来自 v2/assets/locations/<目录名>，与 V0.1 完全一致。
class AssetValidator:
    """镜头内资产引用（ShotAsset）的确定性校验。

    检查（开工指令 §十四）：
        1. 类型是否合法（kind ∈ ASSET_KINDS，统一抽象中的九类）
        2. 必要字段是否缺失（ref 非空）
        3. 引用的资产是否存在（character→角色清单 / location→注册地点 /
           action→注册动作；给了注册表/角色清单才查，其他类型留未来验证）
    """

    def __init__(self, registry: Optional[AssetRegistry] = None,
                 characters: Optional[Iterable[str]] = None):
        self.registry = registry
        self.characters: Set[str] = set(characters or ())

    def validate(self, assets: List[ShotAsset], *, path: str = "assets") -> List[ValidationError]:
        errors: List[ValidationError] = []
        if not isinstance(assets, list):
            errors.append(ValidationError(
                validator="AssetValidator", code="WRONG_TYPE",
                path=path, message="assets 必须是列表。",
                actual=type(assets).__name__, expected="list",
            ))
            return errors
        for i, a in enumerate(assets):
            p = f"{path}[{i}]"
            if not isinstance(a, ShotAsset):
                errors.append(ValidationError(
                    validator="AssetValidator", code="WRONG_TYPE",
                    path=p, message="资产条目必须是 ShotAsset。",
                    actual=type(a).__name__, expected="ShotAsset",
                ))
                continue
            if a.kind not in ASSET_KINDS:
                errors.append(ValidationError(
                    validator="AssetValidator", code="INVALID_ASSET_KIND",
                    path=f"{p}.kind", message="资产类型不在统一抽象集合里。",
                    actual=a.kind, expected=list(ASSET_KINDS),
                ))
            if not str(a.ref).strip():
                errors.append(ValidationError(
                    validator="AssetValidator", code="MISSING_FIELD",
                    path=f"{p}.ref", message="资产引用 ref 不能为空。",
                    actual=a.ref, expected="non-empty str",
                ))
                continue
            # 引用存在性：只有对应注册源存在时才查，避免无意义误报
            if a.kind == "character" and self.characters and a.ref not in self.characters:
                errors.append(ValidationError(
                    validator="AssetValidator", code="UNKNOWN_CHARACTER",
                    path=f"{p}.ref",
                    message="引用的角色不在项目角色清单里。",
                    actual=a.ref, expected=sorted(self.characters),
                ))
            elif a.kind == "location" and self.registry and not self.registry.has_location(a.ref):
                errors.append(ValidationError(
                    validator="AssetValidator", code="INVALID_LOCATION",
                    path=f"{p}.ref", message="引用的地点未注册。",
                    actual=a.ref, expected=self.registry.location_names(),
                ))
            elif a.kind == "action" and self.registry and not self.registry.has_action(a.ref):
                errors.append(ValidationError(
                    validator="AssetValidator", code="INVALID_ACTION",
                    path=f"{p}.ref", message="引用的动作未注册（自动剥编号变体）。",
                    actual=a.ref, expected=self.registry.action_names(),
                ))
        return errors


class ShotValidator:
    """Canonical Shot 的确定性校验（不含 Beat/Scene 容器关系，那是 Beat/Storyboard 的职责）。

    检查（开工指令 §十四，逐镜）：
        必要字段是否缺失 / kind / status / 镜头语言（景别·角度·运镜·构图受控词表）/
        镜头名是否注册 / 地点是否注册 / 角色是否在清单里 / 动作·状态是否合法 /
        站位点是否存在且不冲突 / 走位目的地是否存在 / 资产引用是否存在
    """

    SHOT_KINDS = SHOT_KINDS
    SHOT_STATUSES = SHOT_STATUSES

    def __init__(self, registry: Optional[AssetRegistry] = None,
                 known_characters: Optional[Iterable[str]] = None,
                 valid_states: Optional[Set[str]] = None):
        self.registry = registry
        self.characters: Set[str] = set(known_characters or ())
        self.valid_states: Set[str] = set(valid_states) if valid_states else set(VALID_STATES)
        self.asset_validator = AssetValidator(registry, known_characters)

    def validate(self, shot: Shot, *, scene_location: str = "",
                 path_prefix: str = "shot") -> List[ValidationError]:
        errors: List[ValidationError] = []
        sp = path_prefix
        eff_loc = str(shot.location or "").strip() or str(scene_location or "").strip()

        # ---- 1. 结构：ID 类型 ----
        for fld in ("shot_id", "scene_id", "beat_id"):
            if not isinstance(getattr(shot, fld), int):
                errors.append(ValidationError(
                    validator="ShotValidator", code="WRONG_TYPE",
                    path=f"{sp}.{fld}", message=f"{fld} 必须是 int。",
                    actual=getattr(shot, fld), expected="int",
                ))
        # ---- 2. kind / status ----
        if shot.kind not in self.SHOT_KINDS:
            errors.append(ValidationError(
                validator="ShotValidator", code="INVALID_STRUCTURE",
                path=f"{sp}.kind", message="kind 只允许 dialogue / move / general。",
                actual=shot.kind, expected=list(self.SHOT_KINDS),
            ))
        if shot.status and shot.status not in self.SHOT_STATUSES:
            errors.append(ValidationError(
                validator="ShotValidator", code="INVALID_STATUS",
                path=f"{sp}.status", message="status 不在合法集合里。",
                actual=shot.status, expected=list(self.SHOT_STATUSES),
            ))
        # ---- 3. 必要字段（dialogue 必须有 speaker+dialogue；move 必须有 move 对象）----
        if shot.kind == "dialogue":
            if not str(shot.speaker).strip():
                errors.append(ValidationError(
                    validator="ShotValidator", code="MISSING_FIELD",
                    path=f"{sp}.speaker", message="dialogue 镜头必须有非空 speaker。",
                    actual=shot.speaker, expected="non-empty str",
                ))
            if not str(shot.dialogue).strip():
                errors.append(ValidationError(
                    validator="ShotValidator", code="MISSING_FIELD",
                    path=f"{sp}.dialogue", message="dialogue 镜头必须有非空台词。",
                    actual=shot.dialogue, expected="non-empty str",
                ))
        elif shot.kind == "move":
            if shot.move is None:
                errors.append(ValidationError(
                    validator="ShotValidator", code="MISSING_FIELD",
                    path=f"{sp}.move", message="move 镜头必须带 move 对象。",
                    actual=None, expected="Move",
                ))
            else:
                if not str(shot.move.character).strip():
                    errors.append(ValidationError(
                        validator="ShotValidator", code="MISSING_FIELD",
                        path=f"{sp}.move.character", message="move 必须有非空 character。",
                        actual=shot.move.character, expected="non-empty str",
                    ))
                if not str(shot.move.destination).strip():
                    errors.append(ValidationError(
                        validator="ShotValidator", code="MISSING_FIELD",
                        path=f"{sp}.move.destination", message="move 必须有非空 destination。",
                        actual=shot.move.destination, expected="non-empty str",
                    ))
        # ---- 4. 镜头语言（受控词表）----
        if shot.camera is not None:
            cam = shot.camera
            if cam.shot_size and cam.shot_size not in SHOT_SIZES:
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_SHOT_SIZE",
                    path=f"{sp}.camera.shot_size",
                    message="景别不在受控词表里。",
                    actual=cam.shot_size, expected=list(SHOT_SIZES),
                ))
            if cam.camera_angle and cam.camera_angle not in CAMERA_ANGLES:
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_CAMERA_ANGLE",
                    path=f"{sp}.camera.camera_angle",
                    message="机位角度不在受控词表里。",
                    actual=cam.camera_angle, expected=list(CAMERA_ANGLES),
                ))
            if cam.camera_movement and cam.camera_movement not in CAMERA_MOVEMENTS:
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_CAMERA_MOVEMENT",
                    path=f"{sp}.camera.camera_movement",
                    message="运镜不在受控词表里。",
                    actual=cam.camera_movement, expected=list(CAMERA_MOVEMENTS),
                ))
        if shot.composition and shot.composition not in COMPOSITIONS:
            errors.append(ValidationError(
                validator="ShotValidator", code="INVALID_COMPOSITION",
                path=f"{sp}.composition",
                message="构图不在受控词表里。",
                actual=shot.composition, expected=list(COMPOSITIONS),
            ))
        # 镜头名（shot_type）：有注册表时，非空则必须是已注册镜头 或 受控景别
        if self.registry is not None and shot.shot_type:
            if base_name(shot.shot_type) not in self.registry.shot_names() and shot.shot_type not in SHOT_SIZES:
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_SHOT_TYPE",
                    path=f"{sp}.shot_type",
                    message="镜头名不在 V0.1 注册镜头里，也不是受控景别。",
                    actual=shot.shot_type, expected=self.registry.shot_names(),
                ))
        # ---- 5. 地点 ----
        if self.registry is not None and eff_loc and not self.registry.has_location(eff_loc):
            errors.append(ValidationError(
                validator="ShotValidator", code="INVALID_LOCATION",
                path=f"{sp}.location",
                message="镜头地点未注册（空则继承 Scene 地点）。",
                actual=eff_loc, expected=self.registry.location_names(),
            ))
        # ---- 6. 角色 / 动作 / 站位 / 走位 ----
        known = self.characters
        for j, name in enumerate(shot.characters):
            if known and name and name not in known:
                errors.append(ValidationError(
                    validator="ShotValidator", code="UNKNOWN_CHARACTER",
                    path=f"{sp}.characters[{j}]",
                    message="镜头里出现的角色不在项目角色清单里。",
                    actual=name, expected=sorted(known),
                ))
        for j, a in enumerate(shot.actions):
            if known and a.character not in known:
                errors.append(ValidationError(
                    validator="ShotValidator", code="UNKNOWN_CHARACTER",
                    path=f"{sp}.actions[{j}].character",
                    message="动作角色不在项目角色清单里。",
                    actual=a.character, expected=sorted(known),
                ))
            if self.registry is not None and a.action and not self.registry.has_action(a.action):
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_ACTION",
                    path=f"{sp}.actions[{j}].action",
                    message="动作未注册。",
                    actual=a.action, expected=self.registry.action_names(),
                ))
            if a.state not in self.valid_states:
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_STATE",
                    path=f"{sp}.actions[{j}].state",
                    message="动作状态不在合法状态集合里。",
                    actual=a.state, expected=sorted(self.valid_states),
                ))
        if self.registry is not None and eff_loc:
            for k, ref in enumerate(shot.positioning):
                if not self.registry.has_position(eff_loc, ref.position):
                    errors.append(ValidationError(
                        validator="ShotValidator", code="INVALID_POSITION",
                        path=f"{sp}.positioning[{k}].position",
                        message=f"站位点不存在于地点 '{eff_loc}'。",
                        actual=ref.position,
                        expected=[p.id for p in self.registry.get_positions(eff_loc)],
                    ))
            if shot.move is not None and not self.registry.has_position(eff_loc, shot.move.destination):
                errors.append(ValidationError(
                    validator="ShotValidator", code="INVALID_POSITION",
                    path=f"{sp}.move.destination",
                    message=f"走位目的地不存在于地点 '{eff_loc}'。",
                    actual=shot.move.destination,
                    expected=[p.id for p in self.registry.get_positions(eff_loc)],
                ))
        # ---- 7. 资产引用 ----
        errors.extend(self.asset_validator.validate(shot.assets, path=f"{sp}.assets"))
        return errors


class BeatValidator:
    """Canonical Beat 的确定性校验：Beat 自身 + 内部每个 Shot 的归属与唯一性。

    检查（开工指令 §十四）：
        Beat ID 唯一（由 StoryboardValidator 做全局唯一，本类做 beat 内部完整性）
        Shot 是否属于合法 Beat（shot.beat_id == 所在 Beat.beat_id）
        Beat 内部 Shot ID 不重复
        每个 Shot 交给 ShotValidator 校验
    """

    def __init__(self, shot_validator: Optional[ShotValidator] = None,
                 registry: Optional[AssetRegistry] = None,
                 known_characters: Optional[Iterable[str]] = None,
                 valid_states: Optional[Set[str]] = None):
        self.shot_validator = shot_validator or ShotValidator(registry, known_characters, valid_states)

    def validate(self, beat: Beat, *, scene_id: Optional[int] = None,
                 scene_location: str = "", path_prefix: str = "beat") -> List[ValidationError]:
        errors: List[ValidationError] = []
        if not isinstance(beat.beat_id, int):
            errors.append(ValidationError(
                validator="BeatValidator", code="WRONG_TYPE",
                path=f"{path_prefix}.beat_id", message="beat_id 必须是 int。",
                actual=beat.beat_id, expected="int",
            ))
        if not isinstance(beat.shots, list):
            errors.append(ValidationError(
                validator="BeatValidator", code="WRONG_TYPE",
                path=f"{path_prefix}.shots", message="shots 必须是列表。",
                actual=type(beat.shots).__name__, expected="list",
            ))
            return errors
        seen: Set[int] = set()
        for j, s in enumerate(beat.shots):
            sp = f"{path_prefix}.shots[{j}]"
            if not isinstance(s, Shot):
                errors.append(ValidationError(
                    validator="BeatValidator", code="WRONG_TYPE",
                    path=sp, message="Beat 里必须是 Shot。",
                    actual=type(s).__name__, expected="Shot",
                ))
                continue
            if scene_id is not None and s.scene_id != scene_id:
                errors.append(ValidationError(
                    validator="BeatValidator", code="INVALID_STRUCTURE",
                    path=f"{sp}.scene_id",
                    message=f"Shot 属于 Scene {s.scene_id}，不属于所在 Scene {scene_id}。",
                    actual=s.scene_id, expected=scene_id,
                ))
            if s.beat_id != beat.beat_id:
                errors.append(ValidationError(
                    validator="BeatValidator", code="INVALID_STRUCTURE",
                    path=f"{sp}.beat_id",
                    message=f"Shot 声称属于 Beat {s.beat_id}，与所在 Beat {beat.beat_id} 不一致。",
                    actual=s.beat_id, expected=beat.beat_id,
                ))
            if s.shot_id in seen:
                errors.append(ValidationError(
                    validator="BeatValidator", code="DUPLICATE_SHOT_ID",
                    path=f"{sp}.shot_id",
                    message=f"Beat {beat.beat_id} 内 Shot ID 重复：{s.shot_id}。",
                    actual=s.shot_id, expected="beat 内唯一",
                ))
            seen.add(s.shot_id)
            errors.extend(self.shot_validator.validate(s, scene_location=scene_location, path_prefix=sp))
        return errors


class StoryboardValidator:
    """Canonical Storyboard 的确定性校验：Scene 级 + 各 Beat + 全局唯一性。

    检查（开工指令 §十四）：
        Shot 是否属于合法 Scene（shot.scene_id == storyboard.scene_id）
        Shot 是否属于合法 Beat（交给 BeatValidator）
        角色 / 地点 / 动作 / 镜头语言 / 站位是否合法（交给 ShotValidator）
        Shot ID 全局唯一 / Beat ID 唯一
        引用的 Asset 是否存在（交给 ShotValidator 内嵌的 AssetValidator）
        必要字段是否缺失
    此外支持 validate_dict()：直接校验落盘 / LLM 产出的 dict（Step.validate 用）。
    """

    def __init__(self, registry: Optional[AssetRegistry] = None,
                 known_characters: Optional[Iterable[str]] = None,
                 valid_states: Optional[Set[str]] = None):
        self.registry = registry
        self.characters: Set[str] = set(known_characters or ())
        self.shot_validator = ShotValidator(registry, known_characters, valid_states)
        self.beat_validator = BeatValidator(self.shot_validator)
        self.asset_validator = AssetValidator(registry, known_characters)

    def validate(self, storyboard: Storyboard, *, path_prefix: str = "storyboard") -> List[ValidationError]:
        errors: List[ValidationError] = []
        sp = path_prefix

        # ---- 1. Scene 级结构 ----
        if not isinstance(storyboard.scene_id, int):
            errors.append(ValidationError(
                validator="StoryboardValidator", code="WRONG_TYPE",
                path=f"{sp}.scene_id", message="scene_id 必须是 int。",
                actual=storyboard.scene_id, expected="int",
            ))
        if not isinstance(storyboard.beats, list):
            errors.append(ValidationError(
                validator="StoryboardValidator", code="WRONG_TYPE",
                path=f"{sp}.beats", message="beats 必须是列表。",
                actual=type(storyboard.beats).__name__, expected="list",
            ))
            return errors

        # 地点是否注册（决定后面站位点能不能查；非法地点就不往下查站位，避免噪音）
        location_registered = bool(
            storyboard.location and self.registry is not None and self.registry.has_location(storyboard.location)
        )
        if self.registry is not None and storyboard.location and not location_registered:
            errors.append(ValidationError(
                validator="StoryboardValidator", code="INVALID_LOCATION",
                path=f"{sp}.location", message="Storyboard 地点未注册。",
                actual=storyboard.location, expected=self.registry.location_names(),
            ))
        shot_loc = storyboard.location if location_registered else ""

        # ---- 2. Scene 级角色 / 初始站位 ----
        known = self.characters
        for i, name in enumerate(storyboard.characters):
            if known and name not in known:
                errors.append(ValidationError(
                    validator="StoryboardValidator", code="UNKNOWN_CHARACTER",
                    path=f"{sp}.characters[{i}]",
                    message="Storyboard 角色不在项目角色清单里。",
                    actual=name, expected=sorted(known),
                ))
        if location_registered and self.registry is not None:
            for i, ref in enumerate(storyboard.initial_positions):
                if known and ref.character not in known:
                    errors.append(ValidationError(
                        validator="StoryboardValidator", code="UNKNOWN_CHARACTER",
                        path=f"{sp}.initial_positions[{i}].character",
                        message="初始站位角色不在项目角色清单里。",
                        actual=ref.character, expected=sorted(known),
                    ))
                if not self.registry.has_position(storyboard.location, ref.position):
                    errors.append(ValidationError(
                        validator="StoryboardValidator", code="INVALID_POSITION",
                        path=f"{sp}.initial_positions[{i}].position",
                        message=f"初始站位点不存在于地点 '{storyboard.location}'。",
                        actual=ref.position,
                        expected=[p.id for p in self.registry.get_positions(storyboard.location)],
                    ))
        # ---- 3. 逐 Beat 校验 + Beat ID / 全局 Shot ID 唯一性 ----
        beat_seen: Set[int] = set()
        all_shot_ids: Set[int] = set()
        for i, beat in enumerate(storyboard.beats):
            bp = f"{sp}.beats[{i}]"
            if not isinstance(beat, Beat):
                errors.append(ValidationError(
                    validator="StoryboardValidator", code="WRONG_TYPE",
                    path=bp, message="beats 里必须是 Beat。",
                    actual=type(beat).__name__, expected="Beat",
                ))
                continue
            if beat.beat_id in beat_seen:
                errors.append(ValidationError(
                    validator="StoryboardValidator", code="DUPLICATE_BEAT_ID",
                    path=f"{bp}.beat_id",
                    message=f"Beat ID 重复：{beat.beat_id}。",
                    actual=beat.beat_id, expected="storyboard 内唯一",
                ))
            beat_seen.add(beat.beat_id)
            for j, s in enumerate(beat.shots):
                if isinstance(s, Shot) and s.scene_id != storyboard.scene_id:
                    errors.append(ValidationError(
                        validator="StoryboardValidator", code="INVALID_STRUCTURE",
                        path=f"{bp}.shots[{j}].scene_id",
                        message=f"Shot 属于 Scene {s.scene_id}，不属于本 Storyboard 的 Scene {storyboard.scene_id}。",
                        actual=s.scene_id, expected=storyboard.scene_id,
                    ))
                if isinstance(s, Shot):
                    if s.shot_id in all_shot_ids:
                        errors.append(ValidationError(
                            validator="StoryboardValidator", code="DUPLICATE_SHOT_ID",
                            path=f"{bp}.shots[{j}].shot_id",
                            message=f"整份 Storyboard 内 Shot ID 重复：{s.shot_id}。",
                            actual=s.shot_id, expected="storyboard 内唯一",
                        ))
                    all_shot_ids.add(s.shot_id)
            errors.extend(
                self.beat_validator.validate(beat, scene_id=storyboard.scene_id,
                                             scene_location=shot_loc, path_prefix=bp)
            )
        return errors

    def validate_dict(self, data: dict) -> List[ValidationError]:
        """直接校验落盘 / LLM 产出的 Storyboard dict（Step.validate 用）。"""
        try:
            sb = Storyboard.from_dict(data)
        except Exception as exc:
            return [ValidationError(
                validator="StoryboardValidator", code="WRONG_TYPE",
                path="storyboard", message=f"无法把 dict 解析成 Storyboard: {exc}",
                actual=type(data).__name__, expected="Storyboard dict",
            )]
        if sb is None:
            return [ValidationError(
                validator="StoryboardValidator", code="MISSING_FIELD",
                path="storyboard", message="产出是空的，缺少 Storyboard 内容。",
                actual=data, expected="Storyboard dict",
            )]
        return self.validate(sb)


__all__ = [
    "ValidationError",
    "format_errors",
    "VALID_STATES",
    "EnumValidator",
    "ReferenceValidator",
    "SchemaValidator",
    "CastingValidator",
    "Validator",
    # 阶段 8 Canonical 镜头层
    "AssetValidator",
    "ShotValidator",
    "BeatValidator",
    "StoryboardValidator",
]
