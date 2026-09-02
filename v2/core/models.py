# -*- coding: utf-8 -*-
"""
V0.2 阶段 1 —— 统一数据模型（core/models.py）

作用：把 V0.1 散落各处、字段名还不统一的数据，统一成一套 V0.2 的规范结构。

V0.1 的真实字段名至少有三种写法，例如场景信息字段：
    scenes_2.json  -> "scene_information"（下划线）
    scenes_8.json  -> "sceneinformation"（连写）
    script/0.json  -> "scene information"（空格）
行列表字段有 "dialogues" / "scene" 两种；镜头字段有 "selected shot" / "shot" 两种。
所以本文件规定：
    - V0.2 内部只用一套"规范字段名"（见各 dataclass）。
    - from_v01() 负责"宽容读入"：认识所有旧写法。
    - to_v01()   负责"按最终剧本格式输出"：统一输出成 script/0.json 那种格式
      （scene information / scene / shot / current position / initial position）。
      因为最终剧本格式是整条流水线的终点，也是最完整的格式。

不改变 V0.1 任何业务逻辑，也不删除 V0.1 已有的信息。
scenes_8 里动作带 reason、move 行带顶层 reason 和 insertion 的写法，
读进来后也会原样保存（reason / insertion_reason / insertion_position），
只有输出最终剧本时才按最终格式省略（因为最终剧本本来就没有这些）。

已锁定的两个架构决策（阶段 1 指令第 19/20 节，这里先记录，不实现）：
    ① 跨集 Casting = 已有角色复用 + 增量新增（见 World/Project 的注释）。
    ② 重试不覆盖轨迹 = 版本化产出文件名（见 WorkflowStepState 的注释）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 工具函数：在字典里按多个候选键取第一个存在的值
# ---------------------------------------------------------------------------
def _first_key(d: dict, *names: str) -> Any:
    """返回 d 中第一个存在的键对应的值；都不存在则返回 None。

    例：_first_key(scene, "scene information", "scene_information", "sceneinformation")
    这就是 V0.2 解决 V0.1"字段名不统一"问题的明确转换方式——
    不再使用 return_most_similar() 这种模糊匹配。
    """
    for name in names:
        if name in d:
            return d[name]
    return None


# ---------------------------------------------------------------------------
# 基础 Asset（资产基类，仅占位；具体资产见 core/assets.py）
# ---------------------------------------------------------------------------
@dataclass
class Asset:
    """资产基类：所有资产都有一个名字和一种类型。"""
    name: str
    kind: str = ""

    def to_v01(self) -> dict:
        return {"name": self.name, "kind": self.kind}


# ---------------------------------------------------------------------------
# 位置相关
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """场地里一个"站位点"的定义（对应 V0.1 Locations/<地点>/position.json）。

    注意：Position 是"资产定义"（这个场地有哪几个点、能不能坐），
    和场景里"谁站在哪个点"的 PositionRef 是两回事。
    """
    id: str
    description: str = ""
    sittable: bool = False
    fixed_angle: bool = False

    @classmethod
    def from_v01(cls, data: dict) -> "Position":
        return cls(
            id=str(data.get("id", "")),
            description=str(data.get("description", "")),
            sittable=bool(data.get("sittable", False)),
            fixed_angle=bool(data.get("fixed_angle", False)),
        )

    def to_v01(self) -> dict:
        out: Dict[str, Any] = {"id": self.id}
        if self.description:
            out["description"] = self.description
        out["sittable"] = self.sittable
        out["fixed_angle"] = self.fixed_angle
        return out


@dataclass
class PositionRef:
    """场景里"某个角色在某位置"（对应 V0.1 里的 current position / initial position 条目）。"""
    character: str
    position: str

    @classmethod
    def from_v01(cls, data: dict) -> "PositionRef":
        return cls(
            character=str(data.get("character", "")),
            position=str(data.get("position", "")),
        )

    def to_v01(self) -> dict:
        return {"character": self.character, "position": self.position}


# ---------------------------------------------------------------------------
# 角色
# ---------------------------------------------------------------------------
@dataclass
class Character:
    """角色（对应 V0.1 Script/actors_profile.json 里的一个人）。"""
    name: str
    age: Any = None                  # V0.1 里是字符串（如 "34"），原样保留
    gender: str = ""
    occupation: str = ""
    personality_traits: str = ""     # 对应 V0.1 字段 "personality traits"
    speaking_style: str = ""         # 对应 V0.1 字段 "speaking style"

    @classmethod
    def from_v01(cls, data: dict) -> "Character":
        return cls(
            name=str(data.get("name", "")),
            age=data.get("age"),
            gender=str(data.get("gender", "")),
            occupation=str(data.get("occupation", "")),
            personality_traits=str(data.get("personality traits", "")),
            speaking_style=str(data.get("speaking style", "")),
        )

    def to_v01(self) -> dict:
        return {
            "name": self.name,
            "age": self.age,
            "gender": self.gender,
            "occupation": self.occupation,
            "personality traits": self.personality_traits,
            "speaking style": self.speaking_style,
        }


@dataclass
class CharacterMemory:
    """角色记忆（阶段 1 只定义数据结构，不实现写入/生成——那是阶段 4 的事）。"""
    character: str
    facts: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Casting（阶段 6：第一个真实业务 Agent 的结构化产出）
# ---------------------------------------------------------------------------
@dataclass
class CastingEntry:
    """Casting 结果里的一条：一个角色及其"复用 / 新建"状态。

    跨集决策①（阶段 1 锁定，指令 §七）：老角色直接复用，只给新角色建档。
    status：
        "existing"  项目已有角色，本次直接复用（引用名字，不新建档案）
        "new"       本集 / 本项目新角色，需带完整档案（Character）
    role：本集定位（一句话，可选）。
    """
    name: str
    status: str = "existing"               # "existing" | "new"
    role: str = ""
    character: Optional["Character"] = None   # 仅 status=="new" 时带完整档案

    def to_dict(self) -> dict:
        out: Dict[str, Any] = {"name": self.name, "status": self.status}
        if self.role:
            out["role"] = self.role
        if self.character is not None:
            out["character"] = self.character.to_v01()
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "CastingEntry":
        char = data.get("character")
        return cls(
            name=str(data.get("name", "")),
            status=str(data.get("status", "existing")),
            role=str(data.get("role", "")),
            character=Character.from_v01(char) if isinstance(char, dict) else None,
        )


@dataclass
class CastingResult:
    """一次 Casting 的结构化结果（作为 artifact `casting.json` 落盘）。

    字段对应阶段 6 指令 §五 的要求：
        本次 Casting 用了哪些角色（entries[].name）
        是否已有项目角色 / 是否产生新角色（entries[].status）
        角色基本信息（status=="new" 的完整档案）
        Casting 与当前项目 / 当前集的关系（project_id / episode / topic）
    """
    project_id: str
    episode: int
    topic: str = ""
    entries: List[CastingEntry] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "episode": self.episode,
            "topic": self.topic,
            "entries": [e.to_dict() for e in self.entries],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CastingResult":
        return cls(
            project_id=str(data.get("project_id", "")),
            episode=int(data.get("episode", 1)),
            topic=str(data.get("topic", "")),
            entries=[CastingEntry.from_dict(e) for e in data.get("entries", [])],
            created_at=str(data.get("created_at", "")),
        )


# ---------------------------------------------------------------------------
# 场景与台词
# ---------------------------------------------------------------------------
@dataclass
class SceneInfo:
    """场景信息（who 谁在场 / where 在哪 / what 发生了什么）。"""
    who: List[str] = field(default_factory=list)
    where: str = ""
    what: str = ""

    @classmethod
    def from_v01(cls, data: dict) -> "SceneInfo":
        who = _first_key(data, "who", "selected-characters")
        where = _first_key(data, "where", "selected-location")
        what = _first_key(data, "what", "story-plot")
        return cls(
            who=list(who) if who else [],
            where=str(where) if where else "",
            what=str(what) if what else "",
        )

    def to_v01(self) -> dict:
        return {"who": self.who, "where": self.where, "what": self.what}


@dataclass
class Action:
    """一条动作标注（角色做了什么动作、什么状态）。"""
    character: str
    state: str
    action: str
    reason: str = ""                 # V0.1 scenes_8 的动作带 reason，最终剧本不带

    @classmethod
    def from_v01(cls, data: dict) -> "Action":
        return cls(
            character=str(data.get("character", "")),
            state=str(data.get("state", "")),
            action=str(data.get("action", "")),
            reason=str(data.get("reason", "")),
        )

    def to_v01(self) -> dict:
        out: Dict[str, Any] = {"character": self.character, "state": self.state, "action": self.action}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class Move:
    """一条走位（角色从当前位置移动到 destination）。"""
    character: str
    destination: str
    reason: str = ""                 # scenes_8 里 move 内部也带 reason

    @classmethod
    def from_v01(cls, data: dict) -> "Move":
        return cls(
            character=str(data.get("character", "")),
            destination=str(data.get("destination", "")),
            reason=str(data.get("reason", "")),
        )

    def to_v01(self) -> dict:
        out: Dict[str, Any] = {"character": self.character, "destination": self.destination}
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class Line:
    """一行镜头指令。

    type 有两种：
        - "dialogue"：台词行，有 speaker/content/actions
        - "move"    ：走位行，有 move（走位本身）、shot、positions

    V0.1 数据里没有 type 字段，是靠"有没有 move 键"来判断的。
    type 是 V0.2 内部字段，to_v01() 不会把它写出去（最终剧本里没有）。
    """
    type: str = "dialogue"
    speaker: str = ""
    content: str = ""
    actions: List[Action] = field(default_factory=list)
    shot: str = ""
    positions: List[PositionRef] = field(default_factory=list)   # = V0.1 的 "current position"
    move: Optional[Move] = None
    # ---- 以下是 scenes_8 才有的附加信息，读到就保存，输出最终剧本时省略 ----
    reason: str = ""                    # move 行顶层的 reason
    insertion_reason: str = ""          # move 行的 insertion.reason
    insertion_position: str = ""        # move 行的 insertion.position

    @classmethod
    def from_v01(cls, data: dict) -> "Line":
        if "move" in data:
            # —— 走位行 ——
            move_data = data["move"] or {}
            shot = _first_key(data, "shot", "selected shot")
            positions_data = _first_key(data, "current position", "current_position", "positions")
            insertion = data.get("insertion") or {}
            return cls(
                type="move",
                move=Move.from_v01(move_data),
                shot=str(shot) if shot else "",
                positions=[PositionRef.from_v01(p) for p in positions_data] if positions_data else [],
                reason=str(data.get("reason", "")),
                insertion_reason=str(insertion.get("insertion reason", insertion.get("insertion_reason", ""))),
                insertion_position=str(insertion.get("insertion position", insertion.get("insertion_position", ""))),
            )
        # —— 台词行 ——
        shot = _first_key(data, "shot", "selected shot")
        positions_data = _first_key(data, "current position", "current_position", "positions")
        actions_data = data.get("actions") or []
        return cls(
            type="dialogue",
            speaker=str(data.get("speaker", "")),
            content=str(data.get("content", "")),
            actions=[Action.from_v01(a) for a in actions_data],
            shot=str(shot) if shot else "",
            positions=[PositionRef.from_v01(p) for p in positions_data] if positions_data else [],
        )

    def to_v01(self) -> dict:
        if self.type == "move":
            move_dict: Dict[str, Any] = {
                "character": self.move.character,
                "destination": self.move.destination,
            } if self.move else {}
            if self.move and self.move.reason:
                move_dict["reason"] = self.move.reason
            out: Dict[str, Any] = {"move": move_dict}
            if self.reason:
                out["reason"] = self.reason
            if self.insertion_reason or self.insertion_position:
                insertion: Dict[str, str] = {}
                if self.insertion_reason:
                    insertion["insertion reason"] = self.insertion_reason
                if self.insertion_position:
                    insertion["insertion position"] = self.insertion_position
                out["insertion"] = insertion
            if self.shot:
                out["shot"] = self.shot
            if self.positions:
                out["current position"] = [p.to_v01() for p in self.positions]
            return out

        out = {"speaker": self.speaker, "content": self.content}
        if self.actions:
            out["actions"] = [a.to_v01() for a in self.actions]
        if self.shot:
            out["shot"] = self.shot
        if self.positions:
            out["current position"] = [p.to_v01() for p in self.positions]
        return out


@dataclass
class Scene:
    """一个场景（对应 V0.1 script/0.json 里的一个对象）。"""
    id: int
    info: SceneInfo
    lines: List[Line] = field(default_factory=list)
    initial_positions: List[PositionRef] = field(default_factory=list)

    @classmethod
    def from_v01(cls, data: dict, id: Optional[int] = None) -> "Scene":
        info_data = _first_key(data, "scene information", "scene_information", "sceneinformation")
        lines_data = _first_key(data, "scene", "dialogues", "dialogue")
        init_data = _first_key(data, "initial position", "initial_position")
        return cls(
            id=id if id is not None else 0,
            info=SceneInfo.from_v01(info_data or {}),
            lines=[Line.from_v01(l) for l in lines_data] if lines_data else [],
            initial_positions=[PositionRef.from_v01(p) for p in init_data] if init_data else [],
        )

    def to_v01(self) -> dict:
        out: Dict[str, Any] = {
            "scene information": self.info.to_v01(),
            "scene": [l.to_v01() for l in self.lines],
        }
        if self.initial_positions:
            out["initial position"] = [p.to_v01() for p in self.initial_positions]
        return out


# ---------------------------------------------------------------------------
# 集 / 世界 / 项目（跨集能力的数据结构骨架，阶段 1 只定义，不实现）
# ---------------------------------------------------------------------------
@dataclass
class Episode:
    """一集：由若干场景组成。summary 留给阶段 4 的 Memory 生成。"""
    number: int
    title: str = ""
    summary: str = ""
    scenes: List[Scene] = field(default_factory=list)


@dataclass
class World:
    """世界观。

    locations 只存 LocationAsset 的 ID（字符串），不嵌套整个资产对象。
    known_facts 是"结构化事实"，未来优先级高于 LLM 生成的 summary
    （这是第四十七章待决问题里已拍板的取舍，先记录在这里）。
    """
    name: str
    locations: List[str] = field(default_factory=list)
    characters: List[Character] = field(default_factory=list)
    known_facts: List[str] = field(default_factory=list)


@dataclass
class Project:
    """一个项目（一部剧）。

    决策① 跨集 Casting（已锁定，阶段 3 才实现）：
        每集做 casting 时，
        - 已有角色（World.characters 里已存在）→ 直接复用；
        - 本集剧本需要的新角色 → 增量创建；
        - 最后把结果合并回 World.characters。
        绝不允许每集"重新创建一套新角色"，也绝不跳过新角色创建。
    """
    title: str
    world: World
    episodes: List[Episode] = field(default_factory=list)
    current_episode: int = 1
    workflow_state: Optional["WorkflowState"] = None


# ---------------------------------------------------------------------------
# 工作流状态（只定义数据结构，不实现 Workflow —— 阶段 3 的事）
# ---------------------------------------------------------------------------
@dataclass
class WorkflowStepState:
    """某个流水线步骤（如 lines_generate）的运行状态。

    决策② 重试不覆盖轨迹（已锁定）：
        每次重试都写一个新版本的文件（scenes_5.json -> scenes_5_v2.json -> scenes_5_v3.json），
        旧版本一律保留，绝不覆盖。attempts 记录所有版本，approved_output 指向最终通过的那一版。
    """
    step: str
    status: str = "pending"                       # pending | running | passed | failed
    attempts: List[str] = field(default_factory=list)   # 版本化产出文件名，例如 ["scenes_5.json", "scenes_5_v2.json"]
    approved_output: Optional[str] = None         # 最终采用的文件名

    def record_attempt(self, filename: str) -> None:
        """记录一次产出版本（追加，不覆盖历史）。"""
        self.attempts.append(filename)

    def approve(self, filename: str) -> None:
        """标记最终通过的版本；status 同步为 passed。"""
        self.approved_output = filename
        self.status = "passed"


@dataclass
class WorkflowState:
    """整条流水线的状态快照：按步骤顺序记录每个 step 的状态。"""
    version: str = "1"
    steps: List[WorkflowStepState] = field(default_factory=list)

    def step(self, name: str) -> Optional[WorkflowStepState]:
        for s in self.steps:
            if s.step == name:
                return s
        return None


# ---------------------------------------------------------------------------
# 顶层便捷函数：V0.1 剧本/角色清单 的双向转换
# ---------------------------------------------------------------------------
def from_v01_script(data: List[dict]) -> List[Scene]:
    """把 V0.1 最终剧本（script/0.json 的 list）转成 V0.2 Scene 列表。"""
    return [Scene.from_v01(d, id=i) for i, d in enumerate(data)]


def to_v01_script(scenes: List[Scene]) -> List[dict]:
    """把 V0.2 Scene 列表转回 V0.1 最终剧本格式（script/0.json 结构）。"""
    return [s.to_v01() for s in scenes]


def from_v01_actors(data: List[dict]) -> List[Character]:
    """把 V0.1 actors_profile.json 的 list 转成 V0.2 Character 列表。"""
    return [Character.from_v01(d) for d in data]


def to_v01_actors(characters: List[Character]) -> List[dict]:
    """把 V0.2 Character 列表转回 V0.1 actors_profile.json 格式。"""
    return [c.to_v01() for c in characters]


__all__ = [
    "Asset",
    "Position",
    "PositionRef",
    "Character",
    "CharacterMemory",
    "CastingEntry",
    "CastingResult",
    "SceneInfo",
    "Action",
    "Move",
    "Line",
    "Scene",
    "Episode",
    "World",
    "Project",
    "WorkflowStepState",
    "WorkflowState",
    "from_v01_script",
    "to_v01_script",
    "from_v01_actors",
    "to_v01_actors",
]
