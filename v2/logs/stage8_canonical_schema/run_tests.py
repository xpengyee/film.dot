# -*- coding: utf-8 -*-
"""
V0.2 阶段 8 —— Canonical Schema 测试（T01~T21 + 阶段 1/3/4/6/7 回归）

覆盖（对应 v0.2 开工指令 7 §九~§二十二）：
    A. 受控词表 + Canonical 模型序列化
        T01  受控词表与 §十三 完全一致（shot_size / camera_angle / camera_movement /
             composition / kind / status / asset_kind）
        T02  Canonical 是唯一内部标准：序列化结果不含任何 V0.1 容器键
             （scene information / current position / initial position …）
        T03  CameraPlan / ShotAsset 默认值与 dict 往返
        T04  Shot 全字段 dict 往返（serialize→deserialize→serialize 稳定）
        T05  Beat dict 往返（shots 保序）
        T06  Storyboard dict 往返 + flatten_shots 保序
    B. 真实 fixture + 全链路
        T07  §十九 硬性 fixture：Scene→Beat01(Shot01/02)→Beat02(Shot03/04)
             含 角色/地点/动作/台词/镜头/站位/Asset reference，往返完全一致 + 0 错误
        T08  Project→Scene→Beat→Shot→Asset 全链路构造 + StoryboardValidator 0 错误
             （image/video/audio 未来媒体资产 Schema 可表达）
    C. Validator 确定性（纯代码，不依赖 LLM）
        T09  合法 Storyboard 0 错误（对象 + validate_dict 两条路径）
        T10  ShotValidator 拒绝非法词表 / 未注册镜头名 / 非法地点 / 未知角色 /
             非法动作 / 非法状态 / 缺必要字段
        T11  BeatValidator 拒绝 Beat 内重复 shot_id + shot.beat_id 错配
        T12  StoryboardValidator 拒绝全局重复 shot_id / beat_id / Scene 错配 /
             非 Beat 对象
        T13  AssetValidator 拒绝非法 kind / 空 ref / 未知角色·地点·动作引用
        T13b validate_dict 拒绝结构损坏 / 非法地点的落盘 dict
    D. Adapter + V0.1 往返（§十五 硬性验收）
        T14  真实 script/0.json → Canonical → V0.1-compatible 完全无损（深等）
        T15  真实剧本逐行 → Shot → 行，模型级无损（dialogue / move 全对）
        T16  general 视觉镜头不能静默转回 V0.1（明确报错）；
             shot_label_from_camera 反向映射正确
    E. Agent 公共骨架（§十六，只抽取不新增业务 Agent）
        T17  两个业务 Agent / Step 正确继承 LLMAgent / LLMStep，needs 精确、
             needs_review=True、llm_client=None 可构造
        T18  LLMAgent._parse_json 剥围栏 / 非法 JSON 抛 StepError / chat_json 端到端
        T19  LLMStep 接线：缺 agent 报错 / assembled_context 缺失报错 / make_output 产出
    F. 收尾 + 回归
        T20  V0.1 完整性（v0.1 commit 未动 + FilmAgent/ 0 diff + baseline tag 存在）
        T21  阶段 8 无真实 LLM / 无网络依赖（源码扫描 + mock 调用计数=0）
        R1~R5 阶段 1(19/19) / 3(38/38) / 4(25/25) / 6(24/24) / 7(29/29) 回归

真实 LLM 调用 = 0。本文件只读 V0.1（FilmAgent/），绝不写它。
"""
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]       # v2/
TEST_DIR = Path(__file__).resolve().parent          # 本目录
V01_ROOT = V2_ROOT.parent / "FilmAgent"             # V0.1（只读）
V01_SCRIPT = V01_ROOT / "Script" / "script" / "0.json"
V01_ACTORS = V01_ROOT / "Script" / "actors_profile.json"

sys.path.insert(0, str(V2_ROOT))
from types import SimpleNamespace                      # noqa: E402

from core.io import read_json, write_json              # noqa: E402
from core.models import (                              # noqa: E402
    Scene, SceneInfo, Line, Move, PositionRef,
    from_v01_actors, from_v01_script, to_v01_script,
)
from core.assets import AssetRegistry, base_name       # noqa: E402
from core.shot import (                                # noqa: E402
    SHOT_SIZES, CAMERA_ANGLES, CAMERA_MOVEMENTS, COMPOSITIONS,
    SHOT_KINDS, SHOT_STATUSES, ASSET_KINDS, V01_SHOT_SIZE_MAP,
    CameraPlan, ShotAsset, Shot, Beat, Storyboard,
    infer_camera_from_shot_label, shot_label_from_camera,
)
from core.adapter import (                             # noqa: E402
    line_to_shot, shot_to_line, scene_to_storyboard, storyboard_to_scene,
    v01_script_to_storyboards, storyboards_to_v01_script,
)
from core.validator import (                           # noqa: E402
    ValidationError, format_errors, VALID_STATES,
    AssetValidator, ShotValidator, BeatValidator, StoryboardValidator,
)
from agents.base import LLMAgent, LLMStep              # noqa: E402
from agents.casting import CastingAgent, CastingStep   # noqa: E402
from agents.scene_generation import (                  # noqa: E402
    SceneGenerationAgent, SceneGenerationStep,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_results = []
_log_lines = []


def log(msg=""):
    _log_lines.append(str(msg))


def check(name, ok, detail=""):
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 只读真实数据（FilmAgent/ 绝不修改）
# ---------------------------------------------------------------------------
REGISTRY = AssetRegistry.load(V2_ROOT / "assets" / "locations")
CHARACTERS = from_v01_actors(read_json(V01_ACTORS))
CHAR_NAMES = [c.name for c in CHARACTERS]
REAL_SCRIPT = read_json(V01_SCRIPT)
LIVING_ROOM = "Apartment living room"                 # 真实地点
LR_POSITIONS = [p.id for p in REGISTRY.get_positions(LIVING_ROOM)]


def _fmt_errs(errs):
    return "; ".join(str(e) for e in errs) if errs else "(无)"


def _all_keys(obj, acc=None):
    """递归收集 dict 的所有 key（用于 T02：断言 Canonical 序列化不残留 V0.1 容器键）。"""
    if acc is None:
        acc = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.append(str(k))
            _all_keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, acc)
    return acc


V01_CONTAINER_KEYS = {
    # V0.1 的"容器/整行结构"键。Canonical 序列化不允许出现它们；
    # 但 speaker / dialogue 是 Canonical Shot 自己的字段名，不算 V0.1 容器键。
    "scene information", "scene_information", "sceneinformation",
    "scene", "dialogues", "content",
    "current position", "current_position", "positions",
    "initial position", "initial_position",
    "selected shot", "selected-location", "selected-characters",
    "story-plot",
}


# ---------------------------------------------------------------------------
# A. 词表 + 模型序列化
# ---------------------------------------------------------------------------
def test_vocab():
    ok = (SHOT_SIZES == ("WS", "MS", "MCU", "CU", "ECU")
          and CAMERA_ANGLES == ("eye_level", "high_angle", "low_angle", "overhead")
          and CAMERA_MOVEMENTS == ("static", "pan", "tilt", "dolly", "tracking",
                                   "zoom", "handheld")
          and COMPOSITIONS == ("single", "two_shot", "over_the_shoulder", "group")
          and SHOT_KINDS == ("dialogue", "move", "general")
          and SHOT_STATUSES == ("planned", "approved", "rendered")
          and set(ASSET_KINDS) == {"character", "location", "prop", "action",
                                   "camera", "image", "video", "audio", "reference"}
          and V01_SHOT_SIZE_MAP == {"Long Shot": "WS", "Mid Shot": "MS",
                                    "Close Shot": "CU"})
    check("T01 受控词表与 §十三 完全一致", ok,
          f"sizes={list(SHOT_SIZES)}; angles={list(CAMERA_ANGLES)}; "
          f"movements={list(CAMERA_MOVEMENTS)}; comps={list(COMPOSITIONS)}; "
          f"kinds={list(SHOT_KINDS)}; statuses={list(SHOT_STATUSES)}; "
          f"assets={len(ASSET_KINDS)}类")


def test_canonical_only():
    sb = v01_script_to_storyboards(REAL_SCRIPT)[0]
    keys = set(_all_keys(sb.to_dict()))
    leaks = sorted(V01_CONTAINER_KEYS & keys)
    ok = not leaks
    # 正向：Canonical Shot 用自己的键，而不是 V0.1 行键
    sh_keys = set(sb.flatten_shots()[0].to_dict().keys())
    ok = ok and "shot_type" in sh_keys and "dialogue" in sh_keys and "scene information" not in sh_keys
    check("T02 Canonical 序列化不残留 V0.1 容器键", ok,
          f"泄露键={leaks or '无'}; shot 键含 shot_type/dialogue={('shot_type' in sh_keys and 'dialogue' in sh_keys)}")


def test_cameraplan_asset_roundtrip():
    cam = CameraPlan(shot_size="CU", camera_angle="low_angle", camera_movement="dolly")
    ok1 = CameraPlan.from_dict(cam.to_dict()).to_dict() == cam.to_dict()
    ok2 = CameraPlan.from_dict(None) is None
    ok3 = CameraPlan().shot_size == ""          # 未指定默认空串，不编造
    a = ShotAsset(kind="image", ref="shot_01.png", detail="渲染预览")
    ok4 = ShotAsset.from_dict(a.to_dict()).to_dict() == a.to_dict()
    ok5 = ShotAsset.from_dict(None) is None
    ok6 = ShotAsset().kind == ""
    check("T03 CameraPlan / ShotAsset 默认值与 dict 往返", ok1 and ok2 and ok3 and ok4 and ok5 and ok6,
          f"cam往返={ok1}; cam空dict→None={ok2}; 默认空串={ok3}; asset往返={ok4}; asset空→None={ok5}; 默认空串={ok6}")


def test_shot_roundtrip():
    shot = Shot(
        shot_id=7, scene_id=101, beat_id=2,
        kind="dialogue", shot_type="Close Shot",
        camera=CameraPlan(shot_size="CU", camera_angle="eye_level", camera_movement="static"),
        composition="two_shot",
        subject="Caleb 对 Ivy 说出和解的话",
        speaker="Caleb", dialogue="I missed this, Ivy.",
        location=LIVING_ROOM,
        characters=["Caleb", "Ivy"],
        positioning=[PositionRef(character="Caleb", position="Position 1"),
                     PositionRef(character="Ivy", position="Position 2")],
        reason="", insertion_reason="", insertion_position="",
        visual_description="暖光下 Caleb 靠近沙发", continuity="承接上一镜", duration=3.5,
        assets=[ShotAsset(kind="character", ref="Caleb"),
                ShotAsset(kind="image", ref="shot_07.png", detail="预览")],
        status="planned",
        metadata={"source": "stage8-fixture", "episode": 1},
    )
    # 用合法动作补一条，验证 actions 多对象往返
    shot.actions = [
        _mk_action("Caleb", "sitting", "Sitting Talking"),
        _mk_action("Ivy", "standing", "Standing Talking"),
    ]
    d1 = shot.to_dict()
    d2 = Shot.from_dict(d1).to_dict()
    obj2 = Shot.from_dict(d1)
    ok = (d1 == d2
          and obj2.shot_id == 7 and obj2.scene_id == 101 and obj2.beat_id == 2
          and len(obj2.actions) == 2 and len(obj2.positioning) == 2
          and len(obj2.assets) == 2 and obj2.duration == 3.5
          and obj2.metadata["source"] == "stage8-fixture"
          and obj2.has_content)                     # dialogue 属于内容镜头
    check("T04 Shot 全字段 dict 往返稳定", ok,
          f"serialize→deserialize→serialize 稳定={d1 == d2}; "
          f"actions={len(obj2.actions)}; positioning={len(obj2.positioning)}; "
          f"assets={len(obj2.assets)}; duration={obj2.duration}; has_content={obj2.has_content}")


def _mk_action(character, state, action):
    from core.models import Action
    return Action(character=character, state=state, action=action)


def test_beat_roundtrip():
    beat = Beat(
        beat_id=2, purpose="两人开始和解对话",
        shots=[
            Shot(shot_id=3, scene_id=101, beat_id=2, kind="dialogue",
                 speaker="Caleb", dialogue="你好",
                 shot_type="Close Shot", location=LIVING_ROOM),
            Shot(shot_id=4, scene_id=101, beat_id=2, kind="dialogue",
                 speaker="Ivy", dialogue="好久不见",
                 shot_type="Mid Shot", location=LIVING_ROOM),
        ],
        metadata={"pacing": "slow"},
    )
    d1 = beat.to_dict()
    d2 = Beat.from_dict(d1).to_dict()
    obj2 = Beat.from_dict(d1)
    ok = (d1 == d2 and [s.shot_id for s in obj2.shots] == [3, 4]
          and obj2.purpose == beat.purpose and obj2.metadata["pacing"] == "slow")
    check("T05 Beat dict 往返稳定 + shots 保序", ok,
          f"稳定={d1 == d2}; shot顺序={[s['shot_id'] for s in d1['shots']]}")


def test_storyboard_roundtrip():
    sb = Storyboard(
        scene_id=101, location=LIVING_ROOM,
        characters=["Caleb", "Ivy"], synopsis="重逢后的和解",
        initial_positions=[PositionRef(character="Caleb", position="Position 1")],
        beats=[
            Beat(beat_id=1, purpose="进入房间", shots=[Shot(shot_id=1, scene_id=101, beat_id=1,
                                                            kind="dialogue", speaker="Caleb",
                                                            dialogue="你来了",
                                                            shot_type="Close Shot", location=LIVING_ROOM)]),
            Beat(beat_id=2, purpose="对话", shots=[Shot(shot_id=2, scene_id=101, beat_id=2,
                                                        kind="move",
                                                        move=Move(character="Ivy", destination="Position 2"),
                                                        shot_type="Follow Shot", location=LIVING_ROOM)]),
        ],
        metadata={"episode": 1},
    )
    d1 = sb.to_dict()
    d2 = Storyboard.from_dict(d1).to_dict()
    flat = sb.flatten_shots()
    ok = (d1 == d2
          and [s.shot_id for s in flat] == [1, 2]
          and [s.beat_id for s in flat] == [1, 2]
          and flat[1].kind == "move" and flat[1].move.destination == "Position 2")
    check("T06 Storyboard dict 往返稳定 + flatten_shots 保序", ok,
          f"稳定={d1 == d2}; flatten shot_id顺序={[s.shot_id for s in flat]}; beat顺序={[s.beat_id for s in flat]}")


# ---------------------------------------------------------------------------
# B. 真实 fixture + 全链路
# ---------------------------------------------------------------------------
def _valid_dialogue(shot_id, beat_id, speaker, dialogue, shot_type="Close Shot"):
    """一个能通过 StoryboardValidator 的最小合法台词 Shot（真实词表）。"""
    return Shot(
        shot_id=shot_id, scene_id=101, beat_id=beat_id,
        kind="dialogue", shot_type=shot_type,
        camera=CameraPlan(shot_size=V01_SHOT_SIZE_MAP.get(base_name(shot_type), "CU"),
                          camera_angle="eye_level", camera_movement="static"),
        composition="two_shot",
        speaker=speaker, dialogue=dialogue,
        location=LIVING_ROOM,
        characters=["Caleb", "Ivy"],
        positioning=[PositionRef(character=speaker, position="Position 1")],
        status="planned",
    )


def test_stage19_fixture():
    """§十九 硬性 fixture：Scene 101 → Beat01(Shot01,02) / Beat02(Shot03,04)。"""
    fixture = Storyboard(
        scene_id=101, location=LIVING_ROOM,
        characters=["Caleb", "Ivy"],
        synopsis="重逢和解（真实词表 fixture，覆盖 角色/地点/动作/台词/镜头/站位/Asset）",
        initial_positions=[PositionRef(character="Caleb", position="Position 1"),
                           PositionRef(character="Ivy", position="Position 2")],
        beats=[
            Beat(beat_id=1, purpose="Beat 01 —— 进入房间与寒暄",
                 shots=[
                     # Shot 01：台词 + 动作 + 站位 + 资产引用（character/location/action/image）
                     Shot(shot_id=1, scene_id=101, beat_id=1, kind="dialogue",
                          shot_type="Close Shot",
                          camera=CameraPlan(shot_size="CU", camera_angle="eye_level",
                                            camera_movement="static"),
                          composition="single",
                          speaker="Caleb",
                          dialogue="You finally made it, Ivy.",
                          actions=[_mk_action("Caleb", "standing", "Standing Talking")],
                          location=LIVING_ROOM,
                          characters=["Caleb", "Ivy"],
                          positioning=[PositionRef(character="Caleb", position="Position 1")],
                          assets=[ShotAsset(kind="character", ref="Caleb"),
                                  ShotAsset(kind="location", ref=LIVING_ROOM),
                                  ShotAsset(kind="action", ref="Standing Talking"),
                                  ShotAsset(kind="image", ref="beat1_shot1_preview.png",
                                            detail="特写预览占位")],
                          status="planned"),
                     # Shot 02：另一视角台词
                     Shot(shot_id=2, scene_id=101, beat_id=1, kind="dialogue",
                          shot_type="Mid Shot",
                          camera=CameraPlan(shot_size="MS", camera_angle="eye_level",
                                            camera_movement="static"),
                          composition="two_shot",
                          speaker="Ivy",
                          dialogue="It's good to see you too.",
                          actions=[_mk_action("Ivy", "sitting", "Sitting Talking")],
                          location=LIVING_ROOM,
                          characters=["Caleb", "Ivy"],
                          positioning=[PositionRef(character="Caleb", position="Position 1"),
                                       PositionRef(character="Ivy", position="Position 2")],
                          status="planned"),
                 ]),
            Beat(beat_id=2, purpose="Beat 02 —— 起身走位与收束",
                 shots=[
                     # Shot 03：走位（move）
                     Shot(shot_id=3, scene_id=101, beat_id=2, kind="move",
                          shot_type="Follow Shot", camera=None,
                          speaker="", dialogue="",
                          move=Move(character="Ivy", destination="Position 2"),
                          location=LIVING_ROOM,
                          characters=["Caleb", "Ivy"],
                          positioning=[PositionRef(character="Caleb", position="Position 1"),
                                       PositionRef(character="Ivy", position="Position 2")],
                          status="planned"),
                     # Shot 04：收束台词 + 音频/视频未来媒体资产引用
                     Shot(shot_id=4, scene_id=101, beat_id=2, kind="dialogue",
                          shot_type="Long Shot",
                          camera=CameraPlan(shot_size="WS", camera_angle="eye_level",
                                            camera_movement="static"),
                          composition="group",
                          speaker="Caleb",
                          dialogue="Let's sit down and talk.",
                          location=LIVING_ROOM,
                          characters=["Caleb", "Ivy"],
                          positioning=[PositionRef(character="Caleb", position="Position 1"),
                                       PositionRef(character="Ivy", position="Position 2")],
                          assets=[ShotAsset(kind="audio", ref="ambient.wav", detail="环境声"),
                                  ShotAsset(kind="video", ref="beat2_shot4_rendered.mp4",
                                            detail="未来渲染产出")],
                          status="planned"),
                 ]),
        ],
        metadata={"source": "stage8_fixture_stage19"},
    )

    # (1) serialize → deserialize → serialize 完全一致
    d1 = fixture.to_dict()
    d2 = Storyboard.from_dict(d1).to_dict()
    rt_ok = d1 == d2

    # (2) 结构：Beat01(Shot01,02) / Beat02(Shot03,04)
    flat = fixture.flatten_shots()
    struct_ok = ([len(b.shots) for b in fixture.beats] == [2, 2]
                 and [s.shot_id for s in flat] == [1, 2, 3, 4]
                 and [s.beat_id for s in flat] == [1, 1, 2, 2])

    # (3) 内容覆盖：角色/地点/动作/台词/镜头/站位/Asset reference
    kinds = {s.kind for s in flat}
    content_ok = (kinds == {"dialogue", "move"}
                  and flat[0].speaker == "Caleb" and "made it" in flat[0].dialogue
                  and len(flat[0].actions) == 1
                  and flat[0].assets
                  and {a.kind for a in flat[0].assets} == {"character", "location", "action", "image"}
                  and {a.kind for a in flat[3].assets} == {"audio", "video"}
                  and flat[0].shot_type == "Close Shot"
                  and flat[2].move.character == "Ivy"
                  and flat[0].positioning[0].position == "Position 1")

    # (4) 整个 fixture 通过 StoryboardValidator（0 错误）
    errs = StoryboardValidator(registry=REGISTRY,
                               known_characters=CHAR_NAMES).validate(fixture)
    val_ok = not errs

    check("T07 §十九 fixture 往返一致 + 内容全覆盖 + 0 错误",
          rt_ok and struct_ok and content_ok and val_ok,
          f"往返稳定={rt_ok}; beats={[len(b.shots) for b in fixture.beats]}; "
          f"shot_id={[s.shot_id for s in flat]}; beat_id={[s.beat_id for s in flat]}; "
          f"内容覆盖={content_ok}; 校验错误={len(errs)}")


def test_full_chain_project_scene_beat_shot_asset():
    """§十八：Project → Scene → Beat → Shot → Asset 完整构造与验证。"""
    # Project 数据模型承载场景（canonical 由 Storyboard 表达该场景的镜头规划）
    from core.models import World, Episode, Project
    world = World(name="Shanghai modern drama", locations=[LIVING_ROOM],
                  characters=CHARACTERS)
    project = Project(title="Reconciliation", world=world,
                      episodes=[Episode(number=1, title="重逢")])
    # Scene（V0.1 兼容数据经 models 统一后进入 canonical）
    scene = Scene(
        id=101,
        info=SceneInfo(who=["Caleb", "Ivy"], where=LIVING_ROOM,
                       what="重逢后的和解"),
        lines=[
            Line(type="dialogue", speaker="Caleb", content="你来了。",
                 actions=[_mk_action("Caleb", "standing", "Standing Talking")],
                 shot="Close Shot", positions=[PositionRef("Caleb", "Position 1")]),
            Line(type="move", move=Move(character="Ivy", destination="Position 2"),
                 shot="Follow Shot", positions=[PositionRef("Caleb", "Position 1"),
                                                PositionRef("Ivy", "Position 2")]),
        ],
        initial_positions=[PositionRef("Caleb", "Position 1"),
                           PositionRef("Ivy", "Position 2")],
    )
    # Scene → Storyboard（Beat/Shot 层），并表达 image/video/audio 未来媒体资产
    sb = scene_to_storyboard(scene)
    sb.metadata["project"] = project.title
    sb.beats[0].shots[0].assets.append(ShotAsset(kind="image", ref="render_01.png"))
    sb.beats[0].shots[1].assets.append(ShotAsset(kind="video", ref="take_01.mp4"))
    sb.beats[0].shots[1].assets.append(ShotAsset(kind="audio", ref="diag_01.wav"))
    flat = sb.flatten_shots()
    kinds = {a.kind for s in flat for a in s.assets}
    errs = StoryboardValidator(registry=REGISTRY,
                               known_characters=CHAR_NAMES).validate(sb)
    # 转回 Scene 无损
    scene2 = storyboard_to_scene(sb)
    ok = (project.episodes[0].number == 1
          and scene2 == scene
          and len(flat) == 2
          and kinds == {"image", "video", "audio"}
          and not errs)
    check("T08 Project→Scene→Beat→Shot→Asset 全链路 0 错误 + 无损回 Scene", ok,
          f"shots={len(flat)}; 媒体资产类型={sorted(kinds)}; "
          f"校验错误={len(errs)}; scene无损={scene2 == scene}")


# ---------------------------------------------------------------------------
# C. Validator 确定性
# ---------------------------------------------------------------------------
def test_valid_storyboard_zero_error():
    sb = StoryboardValidator(registry=REGISTRY, known_characters=CHAR_NAMES)
    fixture_sb = Storyboard(
        scene_id=101, location=LIVING_ROOM, characters=["Caleb", "Ivy"], synopsis="x",
        beats=[Beat(beat_id=1, purpose="p", shots=[_valid_dialogue(1, 1, "Caleb", "hi"),
                                                   _valid_dialogue(2, 1, "Ivy", "yo")])],
    )
    errs_obj = sb.validate(fixture_sb)
    errs_dict = sb.validate_dict(fixture_sb.to_dict())
    check("T09 合法 Storyboard 对象与 dict 两条路径均 0 错误", not errs_obj and not errs_dict,
          f"对象错误={len(errs_obj)}; validate_dict错误={len(errs_dict)}")


def test_shot_validator_reject():
    bad = Shot(
        shot_id=1, scene_id=7, beat_id=1,
        kind="dialogue", speaker="Nobody", dialogue="",       # 未知角色 + 缺台词
        shot_type="Ultra Widescreen 99",                       # 未注册镜头名
        camera=CameraPlan(shot_size="XXL", camera_angle="underwater",
                          camera_movement="warp"),
        composition="solo",
        location="Atlantis",                                   # 非法地点
        characters=["Nobody"],
        actions=[_mk_action("Nobody", "flying", "Warp Dance")],
        assets=[ShotAsset(kind="hologram", ref="x"),
                ShotAsset(kind="location", ref="Atlantis"),
                ShotAsset(kind="character", ref="Nobody")],
    )
    codes = {e.code for e in ShotValidator(registry=REGISTRY,
                                           known_characters=CHAR_NAMES).validate(bad)}
    need = {"INVALID_SHOT_SIZE", "INVALID_CAMERA_ANGLE", "INVALID_CAMERA_MOVEMENT",
            "INVALID_COMPOSITION", "INVALID_SHOT_TYPE", "INVALID_LOCATION",
            "UNKNOWN_CHARACTER", "INVALID_ACTION", "INVALID_STATE",
            "INVALID_ASSET_KIND", "MISSING_FIELD"}
    # move 镜头缺 move 对象 → MISSING_FIELD
    no_move = Shot(shot_id=2, scene_id=7, beat_id=1, kind="move", move=None,
                   shot_type="Close Shot", location=LIVING_ROOM)
    codes2 = {e.code for e in ShotValidator(registry=REGISTRY,
                                            known_characters=CHAR_NAMES).validate(no_move)}
    ok = need <= codes and "MISSING_FIELD" in codes2
    check("T10 ShotValidator 拒绝非法词表/镜头名/地点/角色/动作/状态/缺字段", ok,
          f"错误码={sorted(codes)}; move缺move→{sorted(codes2)}")


def test_beat_validator_reject():
    # 同一 Beat 里 shot_id 重复；且第二个 shot 声称属于别的 beat
    s1 = Shot(shot_id=9, scene_id=7, beat_id=1, kind="dialogue", speaker="Caleb",
              dialogue="a", shot_type="Close Shot", location=LIVING_ROOM)
    s2 = Shot(shot_id=9, scene_id=7, beat_id=2, kind="dialogue", speaker="Ivy",
              dialogue="b", shot_type="Close Shot", location=LIVING_ROOM)
    codes = {e.code for e in BeatValidator(registry=REGISTRY,
                                           known_characters=CHAR_NAMES)
             .validate(Beat(beat_id=1, shots=[s1, s2]), scene_id=7)}
    ok = {"DUPLICATE_SHOT_ID", "INVALID_STRUCTURE"} <= codes
    check("T11 BeatValidator 拒绝 Beat 内重复 shot_id + beat_id 错配", ok,
          f"错误码={sorted(codes)}")


def test_storyboard_validator_reject():
    def mk(shot_id, beat_id, scene_id=7):
        return Shot(shot_id=shot_id, scene_id=scene_id, beat_id=beat_id,
                    kind="dialogue", speaker="Caleb", dialogue="x",
                    shot_type="Close Shot", location=LIVING_ROOM)
    # 全局重复 shot_id：两个 Beat 各含 shot_id=3；Beat id 也重复；一个 shot 串 Scene
    sb = Storyboard(
        scene_id=7, location=LIVING_ROOM,
        beats=[Beat(beat_id=1, shots=[mk(3, 1)]),
               Beat(beat_id=1, shots=[mk(3, 1)]),          # beat 1 重复 + shot 3 全局重复
               Beat(beat_id=99, shots=["not a shot"]),     # 非 Beat 内容 → WRONG_TYPE
               Beat(beat_id=5, shots=[mk(4, 5, scene_id=999)])],  # 串 Scene
    )
    codes = {e.code for e in StoryboardValidator(registry=REGISTRY,
                                                 known_characters=CHAR_NAMES).validate(sb)}
    ok = {"DUPLICATE_BEAT_ID", "DUPLICATE_SHOT_ID", "INVALID_STRUCTURE", "WRONG_TYPE"} <= codes
    check("T12 StoryboardValidator 拒绝全局重复 shot/beat + Scene 错配 + 非 Beat", ok,
          f"错误码={sorted(codes)}")


def test_asset_validator_reject():
    assets = [ShotAsset(kind="hologram", ref="x"),                 # 非法 kind
              ShotAsset(kind="image", ref=""),                     # 空 ref
              ShotAsset(kind="character", ref="Nobody"),           # 未知角色
              ShotAsset(kind="location", ref="Atlantis"),          # 非法地点
              ShotAsset(kind="action", ref="Warp Dance")]          # 非法动作
    codes = {e.code for e in AssetValidator(registry=REGISTRY,
                                            characters=CHAR_NAMES).validate(assets)}
    ok = {"INVALID_ASSET_KIND", "MISSING_FIELD", "UNKNOWN_CHARACTER",
          "INVALID_LOCATION", "INVALID_ACTION"} <= codes
    check("T13 AssetValidator 拒绝非法 kind / 空 ref / 未知引用", ok,
          f"错误码={sorted(codes)}")


def test_validate_dict_bad():
    sv = StoryboardValidator(registry=REGISTRY, known_characters=CHAR_NAMES)
    # beats 非 list → 结构错误 WRONG_TYPE（校验器对结构损坏早退，不产生噪音）
    bad = sv.validate_dict({"beats": "not-a-list"})
    codes = {e.code for e in bad}
    # location 非法（beats 为合法空列表，能走到地点检查）→ INVALID_LOCATION
    bad2 = sv.validate_dict({"beats": [], "location": "Atlantis"})
    codes2 = {e.code for e in bad2}
    # 空数据 → MISSING_FIELD
    empty = sv.validate_dict([])
    empty_code = empty[0].code if empty else "?"
    ok = ("WRONG_TYPE" in codes
          and "INVALID_LOCATION" in codes2
          and empty_code == "MISSING_FIELD")
    check("T13b validate_dict 能拒绝结构损坏/非法地点的 dict", ok,
          f"beats非list→{sorted(codes)}; 非法地点→{sorted(codes2)}; 空→{empty_code}")


# ---------------------------------------------------------------------------
# D. Adapter + V0.1 往返
# ---------------------------------------------------------------------------
def test_real_script_roundtrip():
    sbs = v01_script_to_storyboards(REAL_SCRIPT)
    out = storyboards_to_v01_script(sbs)
    ok = out == REAL_SCRIPT
    n_shots = sum(len(sb.flatten_shots()) for sb in sbs)
    check("T14 真实 script/0.json → Canonical → V0.1-compatible 完全无损", ok,
          f"场景={len(REAL_SCRIPT)}; storyboard={len(sbs)}; shots(行)={n_shots}; "
          f"深等={ok}")


def test_every_line_shot_mapping():
    scenes = from_v01_script(REAL_SCRIPT)
    checked = 0
    camera_ok = True
    samples = []
    for sc in scenes:
        for line in sc.lines:
            shot = line_to_shot(line, scene_id=sc.id, characters=list(sc.info.who))
            back = shot_to_line(shot)
            checked += 1
            if back != line:
                camera_ok = False
                break
            # camera 推断：Long/Close/Mid → WS/CU/MS；Pan/Follow/Track 等不强行映射
            if shot.shot_type:
                base = base_name(shot.shot_type)
                if base in V01_SHOT_SIZE_MAP:
                    expect = V01_SHOT_SIZE_MAP[base]
                    if not shot.camera or shot.camera.shot_size != expect:
                        camera_ok = False
                else:
                    if shot.camera is not None:      # 运镜镜头不应有强行景别
                        camera_ok = False
                samples.append((line.type, shot.shot_type,
                                shot.camera.shot_size if shot.camera else None))
    # 抽 3 条样本展示映射
    show = samples[:3] if len(samples) >= 3 else samples
    check("T15 真实剧本逐行 → Shot → 行 无损（含 move 行）", checked > 0 and camera_ok,
          f"行数={checked}; 全部往返相等={camera_ok}; 样本={show}")


def test_general_shot_and_reverse_label():
    general = Shot(shot_id=1, scene_id=1, beat_id=1, kind="general",
                   visual_description="空镜：窗外的雨")
    raised = False
    try:
        shot_to_line(general)
    except ValueError:
        raised = True
    # 反向镜头名
    rev = (shot_label_from_camera(CameraPlan(shot_size="WS")) == "Long Shot"
           and shot_label_from_camera(CameraPlan(shot_size="MS")) == "Mid Shot"
           and shot_label_from_camera(CameraPlan(shot_size="CU")) == "Close Shot"
           and shot_label_from_camera(CameraPlan(shot_size="MCU")) == ""      # 无对应 V0.1 镜头
           and shot_label_from_camera(None) == "")
    inf = (infer_camera_from_shot_label("Long Shot 2").shot_size == "WS"
           and infer_camera_from_shot_label("Close Shot 3").shot_size == "CU"
           and infer_camera_from_shot_label("Follow Shot") is None
           and infer_camera_from_shot_label("") is None)
    check("T16 general 视觉镜头明确报错不静默丢内容 + 镜头名双向映射", raised and rev and inf,
          f"general→ValueError={raised}; 反向={rev}; base_name推断={inf}")


# ---------------------------------------------------------------------------
# E. Agent 公共骨架
# ---------------------------------------------------------------------------
def test_agent_inheritance_and_needs():
    ok = (issubclass(CastingAgent, LLMAgent)
          and issubclass(SceneGenerationAgent, LLMAgent)
          and issubclass(CastingStep, LLMStep)
          and issubclass(SceneGenerationStep, LLMStep)
          and CastingAgent.needs == ["project_info", "approved_facts", "characters", "world"]
          and SceneGenerationAgent.needs == ["project_info", "approved_facts", "characters",
                                             "locations", "actions", "shots"]
          and CastingStep.needs_review is True
          and SceneGenerationStep.needs_review is True)
    # llm_client=None 可构造（测试探 needs 用）
    a = CastingAgent(llm_client=None, known_characters=CHAR_NAMES)
    ok = ok and a.name == "casting" and a.prompt_path.exists()
    b = SceneGenerationAgent(llm_client=None, assets=REGISTRY,
                             known_characters=CHAR_NAMES)
    ok = ok and b.name == "scene_generation" and b.prompt_path.exists()
    check("T17 业务 Agent/Step 正确继承公共骨架 + needs 精确 + 空 llm_client 可构造", ok,
          f"CastingAgent.needs={CastingAgent.needs}; "
          f"SceneGenerationAgent.needs={SceneGenerationAgent.needs}; "
          f"prompt存在={a.prompt_path.exists() and b.prompt_path.exists()}")


class _FakeLLM:
    """极简 mock：只实现 base.chat_json 用到的 chat()，返回预设文本。"""

    def __init__(self, text):
        self._text = text
        self.calls = 0

    def chat(self, messages, step="", agent=""):
        self.calls += 1
        return SimpleNamespace(text=self._text)


def test_parse_json_and_chat():
    a = CastingAgent(llm_client=None, known_characters=CHAR_NAMES)
    # 剥围栏
    ok1 = a._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    ok1 = ok1 and a._parse_json('{"a": 2}') == {"a": 2}
    # 非法 JSON → StepError（可重试）
    try:
        a._parse_json("not json")
        ok1 = False
    except Exception as exc:
        ok1 = ok1 and type(exc).__name__ == "StepError"
    # chat_json 端到端（mock 0 真实调用）
    fake = _FakeLLM('{"episode": 3, "entries": []}')
    b = CastingAgent(llm_client=fake, known_characters=CHAR_NAMES)
    got = b.chat_json(step="casting", user_payload="hello")
    ok2 = got == {"episode": 3, "entries": []} and fake.calls == 1
    # 非 dict 输出 → StepError
    fake2 = _FakeLLM("[1, 2, 3]")
    c = CastingAgent(llm_client=fake2, known_characters=CHAR_NAMES)
    try:
        c.chat_json(step="casting", user_payload="x")
        ok3 = False
    except Exception as exc:
        ok3 = type(exc).__name__ == "StepError"
    check("T18 _parse_json 剥围栏/非法JSON报错 + chat_json 端到端(mock)", ok1 and ok2 and ok3,
          f"解析围栏={ok1}; chat→dict={ok2}; mock调用={fake.calls}; 非dict→StepError={ok3}")


def test_llmstep_wiring():
    from workflow.models import StepOutput
    # 缺 agent → 明确报错
    try:
        CastingStep()
        no_agent = False
    except Exception as exc:
        no_agent = type(exc).__name__ == "ValueError"
    step = CastingStep(agent=CastingAgent(llm_client=None, known_characters=CHAR_NAMES))
    # assembled_context 缺失 → StepError
    class Ctx:
        inputs = {}
    try:
        step.assembled_context(Ctx())
        missing = False
    except Exception as exc:
        missing = type(exc).__name__ == "StepError"
    # make_output → StepOutput
    out = step.make_output({"x": 1}, artifact_name="casting",
                           metadata={"episode": 1})
    ok = no_agent and missing and isinstance(out, StepOutput) \
        and out.data == {"x": 1} and out.artifact_name == "casting" \
        and out.metadata == {"episode": 1} and step.needs_review is True
    check("T19 LLMStep 接线：缺 agent 报错 / context 缺失报错 / make_output 产出", ok,
          f"缺agent→ValueError={no_agent}; 缺assembled→StepError={missing}; "
          f"StepOutput={isinstance(out, StepOutput)}")


def test_no_llm_and_no_network():
    """阶段 8 新增代码不得引入 LLM/网络依赖（确定性纯代码，validator 不调 LLM）。"""
    files = [V2_ROOT / "core" / "shot.py", V2_ROOT / "core" / "adapter.py",
             V2_ROOT / "core" / "validator.py", V2_ROOT / "core" / "__init__.py",
             V2_ROOT / "agents" / "base.py", V2_ROOT / "agents" / "casting.py",
             V2_ROOT / "agents" / "scene_generation.py"]
    bad = []
    for f in files:
        src = f.read_text(encoding="utf-8")
        for line in src.splitlines():
            ls = line.strip()
            if ls.startswith(("import ", "from ")):
                for token in ("openai", "requests", "core.llm", "llm.", " from llm"):
                    if token in ls and not ls.startswith("from ."):
                        bad.append(f"{f.name}: {ls}")
    # casting/scene_generation 允许从 agents.base 引入；'llm' 字样只出现在注释/docstring
    bad = [b for b in bad if "agents.base" not in b and "from agents" not in b]
    check("T21 阶段 8 源码无 LLM/网络 import + 测试无真实调用", not bad,
          f"可疑import={bad or '无'}; 本次真实 LLM 调用=0（全程 mock / fixture）")


# ---------------------------------------------------------------------------
# F. V0.1 完整性 + 回归
# ---------------------------------------------------------------------------
def test_v01_unchanged():
    # 阶段 7 基线封存后 HEAD 已从 v0.1 演进到 v0.2-stage7，工作区改动 v2/ 是正常阶段
    # 工作状态。"V0.1 冻结"的真实不变量 = v0.1 commit 未动 + FilmAgent/ 无改动。
    git_checks = [
        ["git", "rev-parse", "v0.1^{commit}"],
        ["git", "rev-parse", "v0.2-stage7^{commit}"],
        ["git", "diff", "--", "FilmAgent/"],
        ["git", "status", "--short"],
    ]
    v01 = subprocess.run(git_checks[0], capture_output=True, text=True,
                         cwd=str(V2_ROOT.parent)).stdout.strip()
    tag = subprocess.run(git_checks[1], capture_output=True, text=True,
                         cwd=str(V2_ROOT.parent)).stdout.strip()
    diff = subprocess.run(git_checks[2], capture_output=True, text=True,
                          cwd=str(V2_ROOT.parent)).stdout.strip()
    status = subprocess.run(git_checks[3], capture_output=True, text=True,
                            cwd=str(V2_ROOT.parent)).stdout.strip()
    dirty_v01 = [
        ln for ln in status.splitlines()
        if ln.strip().startswith(("M ", "D ", "R ", "A ")) and "FilmAgent/" in ln
    ]
    ok = v01 == "344c0c6b7c48497be934957c29fcae029cca6752" \
        and bool(tag) \
        and diff == "" \
        and not dirty_v01
    check("T20 V0.1 完整性检查通过", ok,
          f"v0.1={v01[:7]}; baseline-tag={tag[:7] or '缺失'}; "
          f"diff空={diff == ''}; FilmAgent dirty={dirty_v01 or '无'}")


def _run_python(path):
    proc = subprocess.run([sys.executable, str(path)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(V2_ROOT.parent))
    return proc.returncode, proc.stdout


def regression_stage1():
    rc, out = _run_python(V2_ROOT / "logs" / "stage1_validation_test" / "run_tests.py")
    ok = rc == 0 and "19/19 通过" in out
    check("R1 阶段 1 回归（19/19）", ok,
          f"returncode={rc}; 输出含 '19/19 通过'={('19/19 通过' in out)}")


def regression_stage3():
    rc, out = _run_python(V2_ROOT / "logs" / "stage3_workflow_test" / "run_tests.py")
    ok = rc == 0 and "38/38 通过" in out
    check("R2 阶段 3 回归（38/38）", ok,
          f"returncode={rc}; 输出含 '38/38 通过'={('38/38 通过' in out)}")


def regression_stage4():
    rc, out = _run_python(V2_ROOT / "logs" / "stage4_memory_context_review_test" / "run_tests.py")
    ok = rc == 0 and "25/25 通过" in out
    check("R3 阶段 4 回归（25/25，含阶段2 mock 复验）", ok,
          f"returncode={rc}; 输出含 '25/25 通过'={('25/25 通过' in out)}")


def regression_stage6():
    rc, out = _run_python(V2_ROOT / "logs" / "stage6_casting" / "run_tests.py")
    ok = rc == 0 and "24/24 通过" in out
    check("R4 阶段 6 回归（24/24，mock）", ok,
          f"returncode={rc}; 输出含 '24/24 通过'={('24/24 通过' in out)}")


def regression_stage7():
    rc, out = _run_python(V2_ROOT / "logs" / "stage7_scene_generation" / "run_tests.py")
    ok = rc == 0 and "29/29 通过" in out
    check("R5 阶段 7 回归（29/29，mock）", ok,
          f"returncode={rc}; 输出含 '29/29 通过'={('29/29 通过' in out)}")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    log("=" * 68)
    log("V0.2 阶段 8 —— Canonical Schema 测试（T01~T21 + 阶段 1/3/4/6/7 回归）")
    log("=" * 68)

    tests = [
        ("A. 词表 + Canonical 模型序列化",
         [test_vocab, test_canonical_only, test_cameraplan_asset_roundtrip,
          test_shot_roundtrip, test_beat_roundtrip, test_storyboard_roundtrip]),
        ("B. 真实 fixture + 全链路",
         [test_stage19_fixture, test_full_chain_project_scene_beat_shot_asset]),
        ("C. Validator 确定性",
         [test_valid_storyboard_zero_error, test_shot_validator_reject,
          test_beat_validator_reject, test_storyboard_validator_reject,
          test_asset_validator_reject, test_validate_dict_bad]),
        ("D. Adapter + V0.1 往返",
         [test_real_script_roundtrip, test_every_line_shot_mapping,
          test_general_shot_and_reverse_label]),
        ("E. Agent 公共骨架",
         [test_agent_inheritance_and_needs, test_parse_json_and_chat,
          test_llmstep_wiring]),
        ("F. V0.1 完整性 / 无真实调用",
         [test_v01_unchanged, test_no_llm_and_no_network]),
        ("回归 R1~R5（阶段 1 / 3 / 4 / 6 / 7）",
         [regression_stage1, regression_stage3, regression_stage4,
          regression_stage6, regression_stage7]),
    ]

    for title, fns in tests:
        log(f"--- {title} ---")
        for fn in fns:
            try:
                fn()
            except Exception as exc:
                check(f"{fn.__name__} 抛异常", False,
                      f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 68)
    log(f"测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")
    log("真实 LLM 调用 = 0 次（本文件全部用 mock / fixture）")

    report = {
        "stage": "stage8_canonical_schema",
        "summary": f"{passed}/{total} 通过",
        "real_llm_calls": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "results": _results,
    }
    write_json(TEST_DIR / "stage8_report.json", report)
    with (TEST_DIR / "console.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")

    print("\n".join(_log_lines))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
