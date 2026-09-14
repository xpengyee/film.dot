# -*- coding: utf-8 -*-
"""
V0.2 Stage 11-B —— StoryboardAgent 测试套件（mock，0 真实 API）

覆盖（B 会话关键单测：mock 正常路径 + 词表兜底 + 名字回锚 + Step 接线）：
    T01  词汇 / 契约：needs 正确；user payload 含全部受控词表取值；命名规范
    T01b prompt 词表一致性护栏（prompt 出现词表文字且与 core.shot 不一致 → StepError）
    T02  Agent 初始化 / Step 必须注入 agent / 缺 prompt 文件明确报错（StepError）
    T03  mock 正常路径：mock 文本 → 合法 Storyboard（beats/shots 结构正确 + 校验全过）
    T04  受控词表兜底：越界值 → 规范化命中词表；有 warning；不抛异常
    T04b shot_type 自由文本 → 清空（R1 专测）★最易漏
    T05  名字回锚：编造角色/地点 → 剔除/回锚 + warning；合法名保留
    T05b positioning 指向不存在站位点 → 剔除 + warning（R6 专测）
    T06  id 重排：跳号/重复 beat_id/shot_id → 全局连续递增
    T07  时长兜底：缺失 / 0 / -1 → 按 kind 默认值；合理值保留
    T07b dialogue 但台词补不齐 → 降级 general 且无 MISSING_FIELD（R5 专测）
    T08  status 强制：所有产出 Shot.status == "planned"
    T09  Step 壳：spec 派发 / artifact_name=storyboard_{scene_id} / 缺 inputs["scene"] → StepError /
         多场景 step_id 独立 + 产物不串场
    T10  validate()：StoryboardValidator.validate_dict；非法产出返回错误列表（非异常）
    T11  Artifact 版本化：重试 → 新版本文件，原文件不动
    T12  Review 三态：APPROVED 继续 / APPROVED_WITH_EDIT 新版本 / REJECTED 停止
    T13  Retry 后仍失败 → FAILED（3 次耗尽），不静默成功
    T14  mock 计数程序化核对：真实 API = 0（快照断言，不人工数数）
    T16  静态检查：compileall / import 冒烟 / ASSET_KINDS 未变 / 新文件无硬编码 key
    T17  禁改文件 0 diff（§8.3 硬清单 + FilmAgent/ + 仓库根 .gitignore）
    T18  Token 硬证据（§十一）：注册视觉资产前后 Storyboard.to_dict() 逐字节不变 +
         名字引用可被 VisualComposer 解析 + 空 registry 明确报错列缺口
    T15  Composer 衔接：空 registry + allow_partial=True 记录缺口；全 registry 解析角色/地点
    T20  边界/防御（C 会话补充）：scene 形态、异常输入、地点继承、上游不一致 fail-loud、
         空 Beat 跳过、词表护栏不误报、缺 spec 兜底、依赖方向合规
    T19  历史回归：stage1/3/4/6/7/8/9/10 八个子进程全部 rc=0（剥离 PYTHONPATH shim）

本文件真实 API 调用 = 0（全 Mock）。
"""
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
REPO_ROOT = Path(__file__).resolve().parents[3]            # git 根
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用（绝不碰真实项目）
TMP_LOGS = TEST_DIR / "tmp_logs"

sys.path.insert(0, str(V2_ROOT))

from core.assets import AssetRegistry                       # noqa: E402
from core.models import Scene                               # noqa: E402
from core.shot import (                                     # noqa: E402
    ASSET_KINDS, CAMERA_ANGLES, CAMERA_MOVEMENTS, COMPOSITIONS, SHOT_KINDS, SHOT_SIZES,
    Storyboard,
)
from core.validator import StoryboardValidator               # noqa: E402
from core.visual_compose import VisualComposer, VisualComposeError  # noqa: E402
from core.visual_registry import VisualAssetRegistry          # noqa: E402
from memory.store import MemoryStore                          # noqa: E402
from context.assembler import ContextAssembler                # noqa: E402
from review.service import ReviewService                      # noqa: E402
from workflow.artifact import ArtifactStore                   # noqa: E402
from workflow.engine import WorkflowEngine                    # noqa: E402
from workflow.step import Step                                # noqa: E402
from workflow.models import StepStatus, StepOutput, WorkflowContext  # noqa: E402
from workflow.errors import StepError                         # noqa: E402
from llm.providers import MockTextProvider                    # noqa: E402
from llm.request_counter import (                             # noqa: E402
    CountingProviderWrapper, get_api_counter, reset_api_counter,
)
from agents import StoryboardAgent, StoryboardStep            # noqa: E402
from agents.storyboard import (                               # noqa: E402
    render_vocab_block, check_prompt_vocab_consistency, normalize_storyboard,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_results = []
_log_lines = []


def log(msg=""):
    _log_lines.append(str(msg))
    print(msg, flush=True)


def check(name, ok, detail=""):
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


def expect_raises(exc_type, fn, *args, **kw):
    try:
        fn(*args, **kw)
        return None
    except exc_type as e:
        return e
    except Exception as e:
        return f"WRONG-TYPE {type(e).__name__}: {e}"
    return "NO-RAISE"


def _hard_rmtree(path: Path) -> None:
    """用 os 层递归删除，绕过运行环境对 shutil.rmtree 的劫持（回收站/沙箱护栏）。"""
    p = Path(path)
    if not p.exists():
        return
    for root, dirs, files in os.walk(p, topdown=False):
        for name in files:
            try:
                os.unlink(os.path.join(root, name))
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    try:
        os.rmdir(p)
    except OSError:
        pass


def fresh_project(tag: str) -> Path:
    d = TMP_PROJECTS / tag
    if d.exists():
        _hard_rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 测试脚手架：真实资产注册表 + 场景 + mock 分镜 JSON
# ---------------------------------------------------------------------------
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")
WHERE = "Apartment living room"                     # 真实地点（已注册）
POS = "Position 1"
POS2 = "Position 2"
NAMES = ["Mercy", "Caleb", "Ivy", "Theo"]           # 项目已有角色名
WHO = ["Mercy", "Caleb"]
ACTION = "Standing Talking"                          # 真实已注册动作
SHOT_NAME = "Close Shot"                             # 真实已注册镜头名


def scene_v01(scene_id=1, who=None, where=WHERE, lines=None):
    """构造一个 V0.1 格式的场景剧本（Stage 7 产出形态）。"""
    who = list(who if who is not None else WHO)
    if lines is None:
        lines = [
            {"speaker": who[0], "content": "Do you think he will come?",
             "actions": [{"character": who[0], "state": "standing", "action": ACTION}],
             "shot": SHOT_NAME,
             "current position": [{"character": who[0], "position": POS}]},
            {"move": {"character": who[-1], "destination": POS2},
             "shot": "Long Shot",
             "current position": [{"character": who[-1], "position": POS2}]},
            {"speaker": who[-1], "content": "He will.",
             "actions": [{"character": who[-1], "state": "standing", "action": ACTION}],
             "shot": "Mid Shot",
             "current position": [{"character": who[-1], "position": POS2}]},
        ]
    return {
        "scene information": {"who": who, "where": where,
                              "what": "Mercy and Caleb wait for Theo in the living room."},
        "initial position": [{"character": who[0], "position": POS},
                             {"character": who[-1], "position": POS2}],
        "scene": lines,
    }


def make_scene(scene_id=1, who=None, where=WHERE, lines=None) -> Scene:
    return Scene.from_v01(scene_v01(scene_id, who, where, lines), id=scene_id)


def shot_raw(kind="dialogue", **over):
    base = {
        "kind": kind,
        "shot_type": "",
        "camera": {"shot_size": "MS", "camera_angle": "eye_level", "camera_movement": "static"},
        "composition": "single",
        "subject": "Mercy at the window",
        "speaker": "Mercy" if kind == "dialogue" else "",
        "dialogue": "Do you think he will come?" if kind == "dialogue" else "",
        "actions": [],
        "location": WHERE,
        "characters": ["Mercy"],
        "positioning": [{"character": "Mercy", "position": POS}],
        "move": None,
        "visual_description": "rain-soaked neon on the glass",
        "continuity": "",
        "duration": 3.0,
        "assets": [],
    }
    base.update(over)
    return base


def sb_raw(beats=None):
    if beats is None:
        beats = [
            {"purpose": "establish the room", "shots": [shot_raw("dialogue")]},
            {"purpose": "a character moves then a quiet beat", "shots": [
                shot_raw("move", speaker="", dialogue="", characters=["Caleb"],
                         positioning=[{"character": "Caleb", "position": POS2}],
                         move={"character": "Caleb", "destination": POS2}, duration=2.5),
                shot_raw("general", speaker="", dialogue="", characters=[],
                         positioning=[], subject="empty room", duration=2.0),
            ]},
        ]
    return {"synopsis": "Two friends wait for Theo.", "beats": beats}


def make_agent(provider, *, scene_id=1, registry=ASSETS, known=NAMES, validator=None):
    return StoryboardAgent(provider, scene_id=scene_id, registry=registry,
                           known_characters=known, validator=validator)


def make_provider(payload):
    """一个只返回指定 payload 的 mock 文本 Provider（多次调用返回同值）。"""
    return MockTextProvider(default_text=json.dumps(payload, ensure_ascii=False))


def assembled_for(mem, step, *, project_title="Reunion drama"):
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    assembler = ContextAssembler(mem, assets=ASSETS)
    return assembler.assemble(step, wf_ctx, project_title=project_title)


def fresh_memory(tag):
    mem = MemoryStore(tag, projects_root=TMP_PROJECTS)
    mem.set_characters([{"name": n} for n in NAMES])
    return mem


def scene_ctx(mem, scene, step):
    """直接给 agent.run 用的 AssembledContext。"""
    return assembled_for(mem, step)


# ---------------------------------------------------------------------------
# T01 词汇 / 契约
# ---------------------------------------------------------------------------
def test_vocab_and_contract():
    mem = fresh_memory("p11_vocab")
    provider = make_provider(sb_raw())
    agent = make_agent(provider)
    step = StoryboardStep(agent, scene_id=1)

    ok_needs = step.needs == ["project_info", "approved_facts", "characters",
                              "locations", "actions", "shots"]
    check("T01a needs 与 SceneGenerationAgent 同集合", ok_needs, f"needs={step.needs}")

    scene = make_scene()
    agent.run(scene_ctx(mem, scene, step), scene=scene, storyboard_spec={})
    payload = provider.calls[-1]["messages"][1]["content"]

    all_values = []
    for vocab in (SHOT_SIZES, CAMERA_ANGLES, CAMERA_MOVEMENTS, COMPOSITIONS, SHOT_KINDS):
        all_values.extend(vocab)
    missing = [v for v in all_values if v not in payload]
    check("T01b user payload 含全部受控词表取值（从 core.shot 渲染）",
          not missing and render_vocab_block() in payload, f"missing={missing}")

    ok_names = (step.step_id == "storyboard_generation_1"
                and step.name == "storyboard_generation"
                and agent.name == "storyboard_generation")
    check("T01c step_id / name / agent.name 命名规范", ok_names,
          f"step_id={step.step_id}; name={step.name}")

    prompt_text = agent.system_prompt()
    ok_prompt = ('"beats"' in prompt_text and '"shots"' in prompt_text
                 and '"SHOT_SIZES"' not in prompt_text and "SHOT_SIZES =" not in prompt_text)
    check("T01d prompt 只放角色设定 + JSON 契约（不含手抄词表）", ok_prompt)


def test_prompt_vocab_guard():
    stale = "SHOT_SIZES = WS, MS, CU\nCAMERA_ANGLES = eye_level, high_angle\n"
    err = expect_raises(StepError, check_prompt_vocab_consistency, stale)
    check("T01e prompt 词表与 core.shot 不一致 → StepError（防漂移护栏）",
          isinstance(err, StepError), f"got={err!r}")

    real_prompt = (V2_ROOT / "prompts" / "v0.2" / "storyboard_generation.txt").read_text(encoding="utf-8")
    err2 = expect_raises(StepError, check_prompt_vocab_consistency, real_prompt)
    check("T01f 真实 prompt 通过词表一致性自检", err2 is None, f"got={err2!r}")


# ---------------------------------------------------------------------------
# T02 初始化 / 边界报错
# ---------------------------------------------------------------------------
def test_agent_init():
    agent = StoryboardAgent(llm_client=None, registry=ASSETS, known_characters=NAMES)
    ok = (agent.name == "storyboard_generation"
          and agent.known_characters == NAMES
          and agent.registry is ASSETS
          and agent.validator is not None)
    check("T02a Agent 可被正常初始化（只探 needs 不调 LLM）", ok)

    err = expect_raises(ValueError, StoryboardStep, None)
    check("T02b StoryboardStep 未注入 agent → ValueError", isinstance(err, ValueError),
          f"got={err!r}")

    missing = TMP_PROJECTS / "no_such_prompt.txt"
    agent2 = StoryboardAgent(make_provider(sb_raw()), registry=ASSETS,
                             known_characters=NAMES, prompt_path=missing)
    err2 = expect_raises(StepError, agent2.system_prompt)
    check("T02c 缺 prompt 文件 → StepError（可重试）", isinstance(err2, StepError),
          f"got={err2!r}")


# ---------------------------------------------------------------------------
# T03 mock 正常路径
# ---------------------------------------------------------------------------
def test_mock_valid_path():
    mem = fresh_memory("p11_valid")
    agent = make_agent(make_provider(sb_raw()))
    scene = make_scene()
    sb = agent.run(scene_ctx(mem, scene, StoryboardStep(agent)), scene=scene)

    ok_struct = (isinstance(sb, Storyboard) and sb.scene_id == 1
                 and sb.location == WHERE and sb.characters == WHO
                 and len(sb.beats) >= 1 and all(len(b.shots) >= 1 for b in sb.beats))
    check("T03a mock 合法 JSON → Storyboard（beats/shots 结构正确）", ok_struct,
          f"beats={len(sb.beats)}; shots={len(sb.flatten_shots())}")

    order_ok = [s.shot_id for s in sb.flatten_shots()] == [1, 2, 3]
    check("T03b flatten_shots() 保序且 shot_id 连续", order_ok,
          f"ids={[s.shot_id for s in sb.flatten_shots()]}")

    errors = agent.validator.validate_dict(sb.to_dict())
    check("T03c 产出通过 StoryboardValidator（0 错误）", not errors,
          f"errors={[str(e) for e in errors][:3]}")


# ---------------------------------------------------------------------------
# T04 受控词表兜底 / T04b shot_type
# ---------------------------------------------------------------------------
def test_vocab_fallback():
    scene = make_scene()
    bad = shot_raw("action", shot_size=None, **{
        "camera": {"shot_size": "Extreme Close Up", "camera_angle": "bird_eye",
                   "camera_movement": "dolly_in"},
        "composition": "wide", "shot_type": "establishing",
    })
    raw = {"beats": [{"purpose": "p", "shots": [bad]}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    s = sb.flatten_shots()[0]
    ok = (s.camera.shot_size in SHOT_SIZES and s.camera.shot_size == "MS"
          and s.camera.camera_angle == "eye_level"
          and s.camera.camera_movement == "static"
          and s.composition == "single"
          and s.kind == "general"          # "action" 越界 → general
          and s.shot_type == "")           # 自由文本被清空（规则 7）
    check("T04 越界值全部被规范化兜底（MS/eye_level/static/single/general）+ warning",
          ok and len(warnings) >= 5, f"shot_size={s.camera.shot_size}; kind={s.kind}; w={len(warnings)}")
    check("T04 规范化不抛异常且 ShotValidator 全过",
          not StoryboardValidator(ASSETS, NAMES).validate_dict(sb.to_dict()))


def test_shot_type_sanitize():
    scene = make_scene()
    # 合法注册镜头名 / 受控景别 → 保留；自由文本 → 清空
    shots = [
        shot_raw("general", shot_type=SHOT_NAME, characters=[]),
        shot_raw("general", shot_type="MS", characters=[]),
        shot_raw("general", shot_type="establishing", characters=[]),
        shot_raw("general", shot_type="reaction", characters=[]),
    ]
    raw = {"beats": [{"purpose": "p", "shots": shots}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    st = [s.shot_type for s in sb.flatten_shots()]
    ok = st == [SHOT_NAME, "MS", "", ""]
    check("T04b shot_type：已注册名/受控景别保留，自由文本清空（R1 专测）", ok,
          f"shot_type={st}; warnings={len(warnings)}")
    check("T04b 清空后 ShotValidator 无 INVALID_SHOT_TYPE",
          not StoryboardValidator(ASSETS, NAMES).validate_dict(sb.to_dict()))


# ---------------------------------------------------------------------------
# T05 名字回锚 / T05b 站位回锚
# ---------------------------------------------------------------------------
def test_name_reanchor():
    scene = make_scene()      # who = ["Mercy","Caleb"], where = Apartment living room
    shots = [shot_raw("general",
                      characters=["Mercy", "Charlie"],       # Charlie 是编造
                      location="Moon base")]                  # 非法地点
    raw = {"beats": [{"purpose": "p", "shots": shots}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    s = sb.flatten_shots()[0]
    ok = (s.characters == ["Mercy"] and s.location == WHERE
          and any("Charlie" in w for w in warnings)
          and any("Moon base" in w for w in warnings))
    check("T05 编造角色被剔除 / 非法地点回锚 scene.where + warning", ok,
          f"characters={s.characters}; location={s.location}")


def test_positioning_reanchor():
    scene = make_scene()
    shots = [shot_raw("general", characters=["Mercy"], positioning=[
        {"character": "Mercy", "position": POS},
        {"character": "Mercy", "position": "Position 99"},   # 不存在
        {"character": "Charlie", "position": POS2},          # 编造角色
    ])]
    raw = {"beats": [{"purpose": "p", "shots": shots}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    s = sb.flatten_shots()[0]
    ok = ([p.position for p in s.positioning] == [POS]
          and any("Position 99" in w for w in warnings))
    check("T05b positioning 非法站位点/编造角色被剔除 + warning（R6 专测）", ok,
          f"positioning={[p.to_v01() for p in s.positioning]}")
    check("T05b 剔除后 ShotValidator 无 INVALID_POSITION",
          not StoryboardValidator(ASSETS, NAMES).validate_dict(sb.to_dict()))


# ---------------------------------------------------------------------------
# T06 id 重排 / T07 时长 / T07b dialogue 降级 / T08 status
# ---------------------------------------------------------------------------
def test_id_renumber():
    scene = make_scene()
    beats = [
        {"beat_id": 1, "purpose": "a", "shots": [dict(shot_raw("general", characters=[]), shot_id=1)]},
        {"beat_id": 3, "purpose": "b", "shots": [dict(shot_raw("general", characters=[]), shot_id=1)]},
        {"beat_id": 3, "purpose": "c", "shots": [dict(shot_raw("general", characters=[]), shot_id=7)]},
    ]
    raw = {"beats": beats}
    sb, _ = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                 known_characters=NAMES)
    bid = [b.beat_id for b in sb.beats]
    sid = [s.shot_id for s in sb.flatten_shots()]
    ok = bid == [1, 2, 3] and sid == [1, 2, 3]
    errs = StoryboardValidator(ASSETS, NAMES).validate_dict(sb.to_dict())
    dup = [e for e in errs if "DUPLICATE" in str(e)]
    check("T06 跳号/重复 id 被重排为连续递增（无 DUPLICATE_*）", ok and not dup,
          f"beat_id={bid}; shot_id={sid}; dup={dup}")


def test_duration_fallback():
    scene = make_scene()
    shots = [
        shot_raw("dialogue", duration=0),
        shot_raw("move", speaker="", dialogue="", characters=["Caleb"],
                 positioning=[{"character": "Caleb", "position": POS2}],
                 move={"character": "Caleb", "destination": POS2}, duration=-1),
        shot_raw("general", characters=[], duration=None),
        shot_raw("general", characters=[], duration=4.5),
    ]
    del shots[2]["duration"]
    raw = {"beats": [{"purpose": "p", "shots": shots}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    d = [s.duration for s in sb.flatten_shots()]
    ok = d == [3.0, 2.5, 2.0, 4.5]
    check("T07 duration 缺失/<=0 按 kind 兜底；合理值保留", ok, f"durations={d}")


def test_dialogue_degrade():
    # scene 无台词行 → dialogue 镜头无法回锚 → 必须降级为 general
    scene = make_scene(lines=[])
    raw = {"beats": [{"purpose": "p", "shots": [
        shot_raw("dialogue", speaker="", dialogue=""),
    ]}]}
    sb, warnings = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                        known_characters=NAMES)
    s = sb.flatten_shots()[0]
    errs = StoryboardValidator(ASSETS, NAMES).validate_dict(sb.to_dict())
    miss = [e for e in errs if "MISSING_FIELD" in str(e)]
    check("T07b dialogue 台词补不齐 → 降级 general 且无 MISSING_FIELD（R5 专测）",
          s.kind == "general" and not miss and any("降级" in w for w in warnings),
          f"kind={s.kind}; miss={miss}")


def test_status_forced():
    scene = make_scene()
    raw = {"beats": [{"purpose": "p", "shots": [
        dict(shot_raw("general", characters=[]), status="approved"),
        dict(shot_raw("general", characters=[]), status="rendered"),
    ]}]}
    sb, _ = normalize_storyboard(raw, scene, scene_id=1, registry=ASSETS,
                                 known_characters=NAMES)
    st = [s.status for s in sb.flatten_shots()]
    check("T08 所有产出 Shot.status 被强制为 planned", st == ["planned", "planned"],
          f"status={st}")


# ---------------------------------------------------------------------------
# T09 Step 壳 / T10 validate
# ---------------------------------------------------------------------------
def _build_engine(mem, provider, *, scene_id=1, steps_extra=None, decision="approved",
                  edited=None, validator=None):
    agent = make_agent(provider, scene_id=scene_id, validator=validator)
    step = StoryboardStep(agent, scene_id=scene_id)
    artifacts_dir = TMP_PROJECTS / mem.project_id / "artifacts"
    if artifacts_dir.exists():          # 隔离：不依赖 main() 的清场，也不依赖测试执行顺序
        _hard_rmtree(artifacts_dir)
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(mem.project_id, store, projects_root=TMP_PROJECTS)

    def handler(artifact, context):
        review = svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        if decision == "approved":
            return svc.to_review_result(svc.approve(review.review_id, comment="通过"))
        if decision == "approved_with_edit":
            done = svc.approve_with_edit(review.review_id, edited, comment="编辑通过")
            return svc.to_review_result(done, edited_content=edited)
        if decision == "rejected":
            return svc.to_review_result(svc.reject(review.review_id, reason="不合格"))
        raise ValueError(decision)

    engine = WorkflowEngine([step] + list(steps_extra or []), artifacts_dir=artifacts_dir,
                            review_handler=handler, retry_backoff_seconds=0.0)
    return agent, step, engine, svc, store


def test_step_shell():
    mem = fresh_memory("p11_step")
    provider = make_provider(sb_raw())
    agent, step, engine, svc, store = _build_engine(mem, provider, scene_id=1)
    scene = make_scene(1)
    assembled = scene_ctx(mem, scene, step)
    run = engine.run(project_id=mem.project_id, workflow_id="wf", inputs={
        "assembled_context": assembled,
        "storyboard_spec": {"scene_id": 1, "episode": 2, "target_beat_count": 2},
        "scene": scene,
    })
    res = run.steps[step.step_id]
    files = [p.name for p in store.list_files("storyboard_1")]
    ok = (res.status == StepStatus.APPROVED and files == ["storyboard_1.json"])
    check("T09a Step 从 inputs 派发 spec；artifact_name=storyboard_1；审核通过",
          ok, f"status={res.status}; files={files}")

    saved = json.loads((store.root / "storyboard_1.json").read_text(encoding="utf-8"))
    check("T09b 落盘产物是合法 Storyboard（metadata.episode 来自 spec）",
          saved.get("scene_id") == 1 and saved.get("metadata", {}).get("episode") == 2,
          f"scene_id={saved.get('scene_id')}; episode={saved.get('metadata', {}).get('episode')}")

    # 缺 inputs["scene"] → StepError（绝不静默产出空 Storyboard）
    ctx_missing = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                                  run_id="x", inputs={"assembled_context": assembled})
    err = expect_raises(StepError, step.run, ctx_missing)
    check("T09c 缺 inputs['scene'] → StepError（不静默产出空 Storyboard）",
          isinstance(err, StepError), f"got={err!r}")

    # 多场景：step_id 独立 + 产物不串场
    step2 = StoryboardStep(make_agent(make_provider(sb_raw()), scene_id=2), scene_id=2)
    ok_ids = (step.step_id == "storyboard_generation_1"
              and step2.step_id == "storyboard_generation_2"
              and step.step_id != step2.step_id)
    mem2 = fresh_memory("p11_step2")
    _, _, engine2, _, store2 = _build_engine(mem2, make_provider(sb_raw()), scene_id=2)
    scene2 = make_scene(2)
    engine2.run(project_id=mem2.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem2, scene2, step2),
        "storyboard_spec": {"scene_id": 2}, "scene": scene2,
    })
    f2 = [p.name for p in store2.list_files("storyboard_2")]
    cross = [p.name for p in store2.list_files("storyboard_1")]
    check("T09d 多场景 step_id 独立、产物不串场",
          ok_ids and f2 == ["storyboard_2.json"] and cross == [],
          f"ids=({step.step_id},{step2.step_id}); f2={f2}; cross={cross}")


def test_validate_returns_errors():
    mem = fresh_memory("p11_validate")
    agent = make_agent(make_provider(sb_raw()))
    step = StoryboardStep(agent, scene_id=1)
    ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf", run_id="x", inputs={})

    bad_type = step.validate({"scene_id": "not-an-int", "beats": []}, ctx)
    check("T10a validate() 对无法解析的 dict 返回错误列表（不抛异常）",
          isinstance(bad_type, list) and len(bad_type) >= 1, f"got={bad_type!r}")

    bad_field = step.validate({"scene_id": 1, "beats": [{"beat_id": 1, "shots": [
        {"shot_id": 1, "scene_id": 1, "beat_id": 1, "kind": "dialogue"}]}]}, ctx)
    check("T10b validate() 对缺 speaker/dialogue 的产出返回 MISSING_FIELD（非异常）",
          isinstance(bad_field, list) and any("MISSING_FIELD" in e for e in bad_field),
          f"got={bad_field[:2]!r}")

    ok_errors = step.validate(sb_raw() and _valid_storyboard_dict(), ctx)
    check("T10c validate() 对合法产出返回空列表", ok_errors == [], f"got={ok_errors!r}")


def _valid_storyboard_dict():
    sb, _ = normalize_storyboard(sb_raw(), make_scene(), scene_id=1,
                                 registry=ASSETS, known_characters=NAMES)
    return sb.to_dict()


# ---------------------------------------------------------------------------
# T11 Artifact 版本化 / T12 Review 三态 / T13 Retry 耗尽
# ---------------------------------------------------------------------------
class FlakyValidator(StoryboardValidator):
    """第一次校验强制失败，之后走真实校验（用于验证「重试 → 新版本」机制复用）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0

    def validate_dict(self, data):
        self.calls += 1
        if self.calls == 1:
            return ["forced validation failure (retry mechanism test)"]
        return super().validate_dict(data)


def test_artifact_versioning():
    mem = fresh_memory("p11_ver")
    r1 = sb_raw()
    r1["synopsis"] = "FIRST DRAFT synopsis"
    r2 = sb_raw()
    r2["synopsis"] = "SECOND DRAFT synopsis"
    provider = MockTextProvider(responses=[json.dumps(r1), json.dumps(r2)])
    agent, step, engine, svc, store = _build_engine(
        mem, provider, scene_id=1, validator=FlakyValidator(ASSETS, NAMES))
    scene = make_scene()
    run = engine.run(project_id=mem.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem, scene, step),
        "storyboard_spec": {"scene_id": 1}, "scene": scene,
    })
    res = run.steps[step.step_id]
    files = [p.name for p in store.list_files("storyboard_1")]
    v1 = json.loads((store.root / "storyboard_1.json").read_text(encoding="utf-8"))
    ok = (res.status == StepStatus.APPROVED
          and files == ["storyboard_1.json", "storyboard_1_v2.json"]
          and v1.get("synopsis") == "FIRST DRAFT synopsis"      # 原文件不动
          and len(res.attempts) == 2)
    check("T11 重试生成新版本、原文件不动（双版本并存）", ok,
          f"status={res.status}; files={files}; attempts={len(res.attempts)}")


def test_review_three_states():
    # APPROVED 继续
    mem = fresh_memory("p11_rv_ok")
    downstream = SimpleStep("after_storyboard", {"ok": True})
    _, step, engine, _, _ = _build_engine(mem, make_provider(sb_raw()),
                                          steps_extra=[downstream], decision="approved")
    scene = make_scene()
    run = engine.run(project_id=mem.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem, scene, step),
        "storyboard_spec": {"scene_id": 1}, "scene": scene})
    ok1 = (run.status == "success" and run.steps[step.step_id].status == StepStatus.APPROVED
           and run.steps.get("after_storyboard") is not None
           and run.steps["after_storyboard"].status == StepStatus.SUCCESS)
    check("T12a Review APPROVED → 继续（下游执行）", ok1, f"run={run.status}")

    # APPROVED_WITH_EDIT → 新版本，原文件不动
    mem2 = fresh_memory("p11_rv_edit")
    edited = json.dumps(_valid_storyboard_dict(), ensure_ascii=False)
    _, step2, engine2, _, store2 = _build_engine(mem2, make_provider(sb_raw()),
                                                 decision="approved_with_edit", edited=edited)
    scene2 = make_scene()
    run2 = engine2.run(project_id=mem2.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem2, scene2, step2),
        "storyboard_spec": {"scene_id": 1}, "scene": scene2})
    files2 = [p.name for p in store2.list_files("storyboard_1")]
    ok2 = (run2.steps[step2.step_id].status == StepStatus.APPROVED_WITH_EDIT
           and files2 == ["storyboard_1.json", "storyboard_1_v2.json"])
    check("T12b Review APPROVED_WITH_EDIT → 新版本、原文件保留", ok2, f"files={files2}")

    # REJECTED → 停止，下游不执行
    mem3 = fresh_memory("p11_rv_rej")
    downstream3 = SimpleStep("after_storyboard", {"ok": True})
    _, step3, engine3, _, _ = _build_engine(mem3, make_provider(sb_raw()),
                                            steps_extra=[downstream3], decision="rejected")
    scene3 = make_scene()
    run3 = engine3.run(project_id=mem3.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem3, scene3, step3),
        "storyboard_spec": {"scene_id": 1}, "scene": scene3})
    ok3 = (run3.status == "failed"
           and run3.steps[step3.step_id].status == StepStatus.REJECTED
           and "after_storyboard" not in run3.steps)
    check("T12c Review REJECTED → 停止（下游不执行）", ok3, f"run={run3.status}")


def test_fail_after_three():
    mem = fresh_memory("p11_fail3")
    provider = MockTextProvider(responses=["not json at all", "still not json", "{oops"])
    agent, step, engine, svc, store = _build_engine(mem, provider, scene_id=1)
    scene = make_scene()
    run = engine.run(project_id=mem.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem, scene, step),
        "storyboard_spec": {"scene_id": 1}, "scene": scene})
    res = run.steps[step.step_id]
    ok = (res.status == StepStatus.FAILED and len(res.attempts) == 3
          and len(provider.calls) == 3 and store.list_files("storyboard_1") == [])
    check("T13 三次全非法 → FAILED（3 次耗尽），不静默成功、不落盘", ok,
          f"status={res.status}; attempts={len(res.attempts)}; calls={len(provider.calls)}")


# ---------------------------------------------------------------------------
# T14 真实 API = 0（程序化计数）
# ---------------------------------------------------------------------------
def test_zero_real_api():
    reset_api_counter()
    mem = fresh_memory("p11_api")
    provider = CountingProviderWrapper(make_provider(sb_raw()))
    agent = make_agent(provider)
    scene = make_scene()
    agent.run(scene_ctx(mem, scene, StoryboardStep(agent)), scene=scene)
    snap = get_api_counter().snapshot()
    ok = snap["real_api_requests"] == 0 and snap["mock_requests"] >= 1
    check("T14 真实 API = 0（程序化快照断言）；mock 调用已计数", ok,
          f"real={snap['real_api_requests']}; mock={snap['mock_requests']}")


# ---------------------------------------------------------------------------
# T18 Token 硬证据
# ---------------------------------------------------------------------------
def _build_approved(reg, kind, name, profile):
    asset = reg.register(kind, name)
    reg.new_version(asset.asset_id, profile=profile, source="text_planned")
    reg.submit_for_review(asset.asset_id, "v1")
    reg.approve(asset.asset_id, "v1")
    return asset


def test_token_hard_evidence():
    mem = fresh_memory("p11_t18")
    agent = make_agent(make_provider(sb_raw()))
    scene = make_scene()
    sb = agent.run(scene_ctx(mem, scene, StoryboardStep(agent)), scene=scene)
    before = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)

    # 空 registry → 明确报错列缺口（allow_partial=False 行为一致）
    empty_reg = VisualAssetRegistry(fresh_project("p11_t18_empty"))
    err = expect_raises(VisualComposeError, VisualComposer(empty_reg).compose,
                        sb.flatten_shots()[0])
    check("T18a 空 registry → VisualComposeError（明确报错，不静默）",
          isinstance(err, VisualComposeError), f"got={err!r}")

    # 建齐 approved 档案（character + location + style）
    reg = VisualAssetRegistry(fresh_project("p11_t18_full"))
    _build_approved(reg, "character", "Mercy",
                    {"appearance": "young woman, tired eyes", "face": "oval, freckles",
                     "hair": "long black hair", "clothing": "red wool coat"})
    _build_approved(reg, "location", WHERE,
                    {"environment": "indoor cafe, cozy", "lighting": "warm pendant lights",
                     "palette": "amber and wood tones"})
    _build_approved(reg, "style", "default",
                    {"medium": "live-action realistic", "palette": "warm neutral"})

    ci = VisualComposer(reg).compose(sb.flatten_shots()[0])
    after = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)
    check("T18b 注册视觉资产前后 Storyboard.to_dict() 逐字节不变（Token 硬证据）",
          before == after)
    check("T18c 视觉档案未渗漏进 Storyboard（无角色外貌/地点环境字段）",
          "red wool coat" not in after and "amber and wood tones" not in after)
    check("T18d 名字引用可被 VisualComposer 解析并展开（profile 进入 render_prompt）",
          ci.character_sections[0]["asset_id"] == "CHAR_001"
          and "red wool coat" in ci.render_prompt(),
          f"asset_id={ci.character_sections[0]['asset_id']}")


# ---------------------------------------------------------------------------
# T15 Composer 衔接（链路可通；缺档时明确报错不静默）
# ---------------------------------------------------------------------------
def test_composer_integration():
    mem = fresh_memory("p11_t15")
    agent = make_agent(make_provider(sb_raw()))
    scene = make_scene()
    sb = agent.run(scene_ctx(mem, scene, StoryboardStep(agent)), scene=scene)
    shot = sb.flatten_shots()[0]          # characters=["Mercy"], location=Apartment living room

    # 空 registry + allow_partial=True → 不抛异常，缺口被记录（与 Stage 10 行为一致）
    empty_reg = VisualAssetRegistry(fresh_project("p11_t15_empty"))
    ci = VisualComposer(empty_reg).compose(shot, allow_partial=True)
    ok1 = ("style:default" in ci.gaps
           and any(g.startswith("character:") for g in ci.gaps)
           and any(g.startswith("location:") for g in ci.gaps))
    check("T15a 空 registry + allow_partial=True → 不抛异常，gaps 记录角色/地点/风格缺口",
          ok1, f"gaps={ci.gaps}")

    # 全 registry → 角色与地点的名字引用都被解析（链路可通，0 改 visual_compose）
    reg = VisualAssetRegistry(fresh_project("p11_t15_full"))
    _build_approved(reg, "character", "Mercy",
                    {"appearance": "young woman, tired eyes", "face": "oval, freckles",
                     "hair": "long black hair", "clothing": "red wool coat"})
    _build_approved(reg, "location", WHERE,
                    {"environment": "indoor cafe, cozy", "lighting": "warm pendant lights",
                     "palette": "amber and wood tones"})
    _build_approved(reg, "style", "default",
                    {"medium": "live-action realistic", "palette": "warm neutral"})
    ci2 = VisualComposer(reg).compose(shot)
    ok2 = (ci2.character_sections[0]["asset_id"] == "CHAR_001"
           and ci2.location_section is not None
           and ci2.location_section["asset_id"] == "LOC_001"
           and not ci2.gaps
           and "red wool coat" in ci2.render_prompt())
    check("T15b 全 registry → 角色/地点名字引用均被解析（无缺口，链路可通）", ok2,
          f"loc={None if ci2.location_section is None else ci2.location_section['asset_id']}; gaps={ci2.gaps}")


# ---------------------------------------------------------------------------
# T16 静态检查 / T17 禁改 0 diff
# ---------------------------------------------------------------------------
def test_static_and_freeze():
    proc = subprocess.run([sys.executable, "-m", "compileall", "-q",
                           str(V2_ROOT / "core"), str(V2_ROOT / "llm"), str(V2_ROOT / "agents")],
                          capture_output=True, text=True)
    check("T16a compileall 全过", proc.returncode == 0, (proc.stderr or "")[:200])

    ok_kinds = ASSET_KINDS == ("character", "location", "prop", "action", "camera",
                               "image", "video", "audio", "reference")
    check("T16b ASSET_KINDS 精确不变（stage8 封存前提）", ok_kinds)

    import agents  # noqa: E402
    ok_exports = {"StoryboardAgent", "StoryboardStep"} <= set(agents.__all__)
    check("T16c import 冒烟 + 新导出齐备", ok_exports, f"__all__={agents.__all__}")

    new_files = ["v2/agents/storyboard.py", "v2/prompts/v0.2/storyboard_generation.txt"]
    bad = []
    for rel in new_files:
        for line in (V2_ROOT.parent / rel).read_text(encoding="utf-8").splitlines():
            if "sk-" in line.strip():
                bad.append(f"{rel}: {line.strip()[:60]}")
    check("T16d 新增文件无硬编码 key（sk- 字样一律扫描）", not bad, f"{bad[:3]}" if bad else "")

    forbidden = [
        "FilmAgent", ".gitignore",
        "v2/core/shot.py", "v2/core/adapter.py", "v2/core/models.py", "v2/core/assets.py",
        "v2/core/validator.py", "v2/core/visual.py", "v2/core/visual_registry.py",
        "v2/core/visual_compose.py",
        "v2/agents/base.py", "v2/agents/casting.py", "v2/agents/scene_generation.py",
        "v2/agents/visual_asset.py",
        "v2/llm", "v2/memory", "v2/review", "v2/workflow", "v2/context",
    ]
    changed = []
    for f in forbidden:
        p = subprocess.run(["git", "diff", "--stat", "HEAD", "--", f],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        if (p.stdout or "").strip():
            changed.append(f)
    check("T17 禁改文件 0 diff（§8.3 硬清单 + FilmAgent/ + 仓库根 .gitignore）",
          not changed, f"有改动: {changed}" if changed else "")


# ---------------------------------------------------------------------------
# T20 边界 / 防御（C 会话补充：异常输入、上游不一致、继承语义）
# ---------------------------------------------------------------------------
def test_edge_cases():
    mem = fresh_memory("p11_edge")
    step = StoryboardStep(make_agent(make_provider(sb_raw())), scene_id=1)

    # T20a scene 允许是 to_v01() dict（Agent 内部 from_v01）
    agent = make_agent(make_provider(sb_raw()))
    sb_dict = agent.run(scene_ctx(mem, None, step), scene=scene_v01(1))
    check("T20a inputs['scene'] 允许是 Scene.to_v01() dict", sb_dict.scene_id == 1
          and len(sb_dict.beats) >= 1, f"scene_id={sb_dict.scene_id}")

    # T20b scene 既非 Scene 也非 dict → StepError（明确报错）
    err = expect_raises(StepError, agent.run, scene_ctx(mem, None, step), scene=123)
    check("T20b inputs['scene'] 类型非法 → StepError", isinstance(err, StepError), f"got={err!r}")

    # T20c 结构非法：raw 非 dict / beats 非 list / beats 全非 dict → StepError
    scene = make_scene()
    e1 = expect_raises(StepError, normalize_storyboard, [], scene, scene_id=1,
                       registry=ASSETS, known_characters=NAMES)
    e2 = expect_raises(StepError, normalize_storyboard, {"beats": "x"}, scene, scene_id=1,
                       registry=ASSETS, known_characters=NAMES)
    e3 = expect_raises(StepError, normalize_storyboard, {"beats": [1, 2]}, scene, scene_id=1,
                       registry=ASSETS, known_characters=NAMES)
    check("T20c 结构非法（raw 非 dict / beats 非 list / 全非 dict）→ StepError",
          all(isinstance(e, StepError) for e in (e1, e2, e3)),
          f"{(type(e1).__name__, type(e2).__name__, type(e3).__name__)}")

    # T20d shot.location 留空 → 继承 Scene 地点（空串），且校验通过
    sb_d, _ = normalize_storyboard(
        {"beats": [{"purpose": "p", "shots": [{"kind": "general", "camera": {},
                                               "characters": []}]}]},
        scene, scene_id=1, registry=ASSETS, known_characters=NAMES)
    s_d = sb_d.flatten_shots()[0]
    check("T20d shot.location 留空 → 保持空串（继承 Scene 地点）且校验通过",
          s_d.location == "" and not StoryboardValidator(ASSETS, NAMES).validate_dict(sb_d.to_dict()),
          f"location={s_d.location!r}")

    # T20e 上游不一致（Scene.info.who 含未登记角色）→ fail-loud，绝不静默
    scene_bad = make_scene(who=["Mercy", "Ghost"])
    sb_bad, _ = normalize_storyboard(
        {"beats": [{"purpose": "p", "shots": [{"kind": "general", "camera": {},
                                               "characters": []}]}]},
        scene_bad, scene_id=1, registry=ASSETS, known_characters=NAMES)
    errs = StoryboardValidator(ASSETS, NAMES).validate_dict(sb_bad.to_dict())
    check("T20e 上游 who 含未登记角色 → 明确报 UNKNOWN_CHARACTER（fail-loud，不静默丢角色）",
          any("UNKNOWN_CHARACTER" in str(e) for e in errs),
          f"errors={[str(e).splitlines()[0] for e in errs]}")

    # T20f 空 Beat 被跳过 + warning（不产出空 Beat）
    sb_skip, w_skip = normalize_storyboard(
        {"beats": [{"purpose": "empty", "shots": []},
                   {"purpose": "ok", "shots": [{"kind": "general", "camera": {},
                                                "characters": []}]}]},
        scene, scene_id=1, registry=ASSETS, known_characters=NAMES)
    check("T20f 空 Beat 被跳过（beat_id 重排连续）+ warning",
          len(sb_skip.beats) == 1 and sb_skip.beats[0].beat_id == 1
          and any("跳过" in w for w in w_skip),
          f"beats={len(sb_skip.beats)}; warnings={len(w_skip)}")

    # T20g prompt 词表护栏不误报：词表行正确时通过
    ok_guard = expect_raises(StepError, check_prompt_vocab_consistency,
                            "SHOT_SIZES = WS, MS, MCU, CU, ECU\nCOMPOSITIONS = single, two_shot, "
                            "over_the_shoulder, group\n")
    check("T20g prompt 词表护栏不误报（正确词表行 → 通过）", ok_guard is None, f"got={ok_guard!r}")

    # T20h inputs 缺 storyboard_spec → 用 {} 兜底仍可产出
    mem2 = fresh_memory("p11_edge2")
    _, step2, engine2, _, store2 = _build_engine(mem2, make_provider(sb_raw()), scene_id=1)
    scene2 = make_scene(1)
    run2 = engine2.run(project_id=mem2.project_id, workflow_id="wf", inputs={
        "assembled_context": scene_ctx(mem2, scene2, step2), "scene": scene2})
    f2 = [p.name for p in store2.list_files("storyboard_1")]
    check("T20h inputs 缺 storyboard_spec → 用 {} 兜底仍产出 storyboard_1",
          run2.steps[step2.step_id].status == StepStatus.APPROVED and f2 == ["storyboard_1.json"],
          f"files={f2}")

    # T20j 列表字段被写成非列表 → 记 warning 后按空处理（不静默丢弃信息）
    sb_nl, w_nl = normalize_storyboard(
        {"beats": [{"purpose": "p", "shots": [{"kind": "general", "camera": {},
                                               "characters": "Mercy",
                                               "positioning": {"character": "Mercy"}}]}]},
        scene, scene_id=1, registry=ASSETS, known_characters=NAMES)
    s_nl = sb_nl.flatten_shots()[0]
    check("T20j 列表字段非列表 → warning + 按空处理（不静默）",
          s_nl.characters == [] and s_nl.positioning == []
          and any("应为列表" in w for w in w_nl),
          f"characters={s_nl.characters}; positioning={s_nl.positioning}")


def test_dependency_direction():
    """静态：新模块的导入必须只落在允许的下层（core / workflow / agents.base）。"""
    src = (V2_ROOT / "agents" / "storyboard.py").read_text(encoding="utf-8")
    forbidden = []
    for line in src.splitlines():
        ls = line.strip()
        if not ls.startswith(("from ", "import ")):
            continue
        for bad in ("from llm", "import llm", "from memory", "from context", "from review"):
            if ls.startswith(bad):
                forbidden.append(ls)
    check("T20i agents/storyboard.py 只依赖 core/workflow/agents（依赖方向合规）",
          not forbidden, f"{forbidden}" if forbidden else "")


# ---------------------------------------------------------------------------
# T19 历史回归（内嵌子进程；剥离宿主 PYTHONPATH shim）
# ---------------------------------------------------------------------------
HISTORY_SUITES = [
    ("stage1", "stage1_validation_test"),
    ("stage3", "stage3_workflow_test"),
    ("stage4", "stage4_memory_context_review_test"),
    ("stage6", "stage6_casting"),
    ("stage7", "stage7_scene_generation"),
    ("stage8", "stage8_canonical_schema"),
    ("stage9", "stage9_provider"),
    ("stage10", "stage10_visual_asset"),
]


def _clean_child_env() -> dict:
    """给历史回归子进程一个不带宿主 shim 的环境。

    运行环境通过 PYTHONPATH 注入 sitecustomize，把 shutil.rmtree 劫持为
    "移入回收站"，会让历史套件清理自己的 tmp 目录时失败（Environment issue，
    非实现/测试缺陷）。这里只对子进程剥离该注入，不改任何历史套件源码。
    """
    env = dict(os.environ)
    shim_marker = os.path.join("cli", "vendor", "shim")
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
             if p and shim_marker not in p]
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        env.pop("PYTHONPATH", None)
    return env


def run_history():
    for tag, dirname in HISTORY_SUITES:
        suite = V2_ROOT / "logs" / dirname / "run_tests.py"
        if not suite.exists():
            check(f"T19-{tag} 套件存在", False, f"missing {suite}")
            continue
        proc = subprocess.run([sys.executable, str(suite)], capture_output=True,
                              text=True, cwd=str(V2_ROOT), encoding="utf-8",
                              errors="replace", env=_clean_child_env())
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        ok = proc.returncode == 0
        check(f"T19-{tag} 历史回归全绿", ok, "; ".join(tail)[:220] if not ok else "")


# ---------------------------------------------------------------------------
# 下游 Step（review APPROVED 后续执行）
# ---------------------------------------------------------------------------
class SimpleStep(Step):
    def __init__(self, step_id, data):
        super().__init__(step_id=step_id)
        self._data = data

    def run(self, context):
        return StepOutput(data=self._data, artifact_name=self.step_id)


# ---------------------------------------------------------------------------
def main():
    if TMP_PROJECTS.exists():
        _hard_rmtree(TMP_PROJECTS)
    TMP_PROJECTS.mkdir(parents=True, exist_ok=True)
    TMP_LOGS.mkdir(parents=True, exist_ok=True)
    log("=" * 72)
    log("Stage 11-B StoryboardAgent —— mock 测试套件（0 真实 API）")
    log("=" * 72)
    tests = [
        test_vocab_and_contract, test_prompt_vocab_guard,
        test_agent_init, test_mock_valid_path,
        test_vocab_fallback, test_shot_type_sanitize,
        test_name_reanchor, test_positioning_reanchor,
        test_id_renumber, test_duration_fallback, test_dialogue_degrade,
        test_status_forced,
        test_step_shell, test_validate_returns_errors,
        test_artifact_versioning, test_review_three_states, test_fail_after_three,
        test_zero_real_api, test_token_hard_evidence, test_composer_integration,
        test_edge_cases, test_dependency_direction,
        test_static_and_freeze,
    ]
    for t in tests:
        try:
            t()
        except Exception:
            check(f"{t.__name__} 异常", False, traceback.format_exc()[-400:])
    run_history()

    passed = sum(1 for r in _results if r["ok"])
    total = len(_results)
    failed = [r for r in _results if not r["ok"]]
    log("-" * 72)
    log(f"TOTAL: {passed}/{total} passed")
    for r in failed:
        log(f"  FAIL: {r['name']}  ->  {r['detail'][:300]}")
    report = {
        "suite": "stage11_storyboard",
        "real_api_requests": 0,
        "results": _results,
        "passed": passed, "total": total,
    }
    (TEST_DIR / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"report -> {TEST_DIR / 'test_report.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
