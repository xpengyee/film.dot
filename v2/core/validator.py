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

from .assets import AssetRegistry
from .models import CastingResult, Line, Scene


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


__all__ = [
    "ValidationError",
    "format_errors",
    "VALID_STATES",
    "EnumValidator",
    "ReferenceValidator",
    "SchemaValidator",
    "CastingValidator",
    "Validator",
]
