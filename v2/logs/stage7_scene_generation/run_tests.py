# -*- coding: utf-8 -*-
"""
V0.2 阶段 7 —— Scene / Script Generation Agent 测试（T01~T19 + T26 + 阶段 1/3/4/6 回归）

覆盖（对应阶段 7 开工指令 §十六）：
    T01  Scene Generation Agent 可以被正常初始化
    T02  Scene Generation Agent 正确声明 needs（含资产类 needs）
    T03  ContextAssembler 能提供 Scene 所需全部上下文（注入 AssetRegistry）
    T04  Mock LLM 返回合法 Scene -> Agent 解析成 V0.2 Scene 对象
    T05  合法 Scene 能通过 Validator（0 错误）
    T06  非法 Scene 被 Validator 拒绝（未知角色/非法地点/非法动作/非法镜头/非法位置/缺 where）
    T07  非法输出进入 retry（第一次不合格 -> 重试成功）
    T08  retry 不覆盖旧 Artifact（scene_1.json + scene_1_v2.json 双版本并存）
    T09  连续三次非法 JSON -> Workflow FAILED 停止
    T10  scene_spec 兜底 who/where（LLM 缺字段时用场景规范补齐）
    T11  跨集角色一致性：第二集继续使用已有角色，不创建新档案
    T12  candidate 不会自动进入 approved_facts，Scene 只用 approved_facts
    T13  Review APPROVED 能继续
    T14  Review APPROVED_WITH_EDIT 能产生新版本（原文件不覆盖）
    T15  Review REJECTED 能停止流程
    T16  LLM 调用经过 LLMClient（request_id/token/cost 记录）
    T17  不同 scene_id 产生不同 artifact（scene_1 / scene_2）
    T18  Workflow 可以 resume（已批准 Step 直接 SKIPPED，不再调 LLM）
    T19  artifact 落盘内容能读回为 Scene（版本化产物可恢复）
    T26  V0.1 完整性检查通过

回归（§十六 E）：
    R1  阶段 1（19/19）
    R2  阶段 3（38/38）
    R3  阶段 4（25/25，内部复验阶段 1/2(mock)/3）
    R4  阶段 6（24/24，mock）

真实 DeepSeek 最小调用单独放在 run_real_scene_generation.py（T21~T24）。
本文件真实 LLM 调用 = 0。
"""
import importlib.util
import json
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
FIXTURES = TEST_DIR / "fixtures"
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用项目目录（绝不碰真实项目）
TMP_LOGS = TEST_DIR / "tmp_logs"                           # 测试专用 token 日志

sys.path.insert(0, str(V2_ROOT))
from core.io import read_json, write_json                   # noqa: E402
from core.models import Scene, SceneInfo                    # noqa: E402
from core.validator import Validator                        # noqa: E402
from core.assets import AssetRegistry                       # noqa: E402
from core.token_log import TokenUsage, TokenLogger           # noqa: E402
from memory.store import MemoryStore                         # noqa: E402
from memory.models import FactStatus                         # noqa: E402
from context.assembler import ContextAssembler               # noqa: E402
from review.service import ReviewService                     # noqa: E402
from workflow.artifact import ArtifactStore                  # noqa: E402
from workflow.engine import WorkflowEngine                   # noqa: E402
from workflow.step import Step                               # noqa: E402
from workflow.models import (                                # noqa: E402
    StepStatus, StepOutput, WorkflowContext, WorkflowRun,
)
from llm.client import LLMClient                             # noqa: E402
from agents.scene_generation import SceneGenerationAgent, SceneGenerationStep  # noqa: E402

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
# 真实 V0.1 数据（只读 fixture，阶段 7 指令 §二十一）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES / "actors_profile.json")   # 真实角色资料（4 人）
REAL_NAMES = [c["name"] for c in REAL_PROFILE]                # ["Mercy","Caleb","Ivy","Theo"]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}
REAL_SPEC = read_json(FIXTURES / "scene_spec.json")           # 真实场景规范（来自 scenes_1.json 第 1 场）
REAL_APPROVED_FACT = ("Theo has been estranged from the friend group for three years "
                      "after a bitter fallout over a shared business venture.")

# 真实资产注册表（只读 v2/assets/locations/）
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")
APARTMENT = "Apartment living room"                            # 真实地点
POSITION_IDS = [p.id for p in ASSETS.get_positions(APARTMENT)]  # ["Position 1", ...]


# ---------------------------------------------------------------------------
# Mock LLM transport（可编程：每次调用弹一个预设响应 + 记录调用）
# ---------------------------------------------------------------------------
class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, messages, model, temperature, max_tokens, timeout):
        self.calls.append({"messages": messages, "model": model})
        if not self.responses:
            raise AssertionError("FakeTransport 预设响应已用完")
        text = self.responses.pop(0)
        usage = TokenUsage(input_tokens=25, output_tokens=15, total_tokens=40,
                           cache_miss_tokens=25)
        return text, usage


def _scene_json(who=None, where=APARTMENT, action="Standing Talking", shot="Close Shot",
                position="Position 1", extra_line=None, with_info=True):
    """构造一个合法的单场景 JSON（V0.1 最终剧本格式，Agent 输出也用这个格式）。"""
    who = who or ["Caleb", "Ivy"]
    spec = {}
    if with_info:
        spec["scene information"] = {
            "who": who,
            "where": where,
            "what": "Caleb and Ivy wait for Theo at the apartment living room.",
        }
    spec["initial position"] = [
        {"character": who[0], "position": position},
        {"character": who[-1], "position": "Position 2" if position != "Position 2" else "Position 3"},
    ]
    spec["scene"] = [
        {
            "speaker": who[0],
            "content": "Do you think he will come?",
            "actions": [{"character": who[0], "state": "standing", "action": action}],
            "shot": shot,
            "current position": [{"character": who[0], "position": position}],
        },
    ]
    if extra_line:
        spec["scene"].append(extra_line)
    return spec


# Mock 响应样例
VALID_SCENE = json.dumps(_scene_json(), ensure_ascii=False)
INVALID_UNKNOWN_CHARACTER = json.dumps(
    _scene_json(who=["Caleb", "Ivy", "Charlie"]), ensure_ascii=False)          # 凭空造角色
INVALID_LOCATION = json.dumps(
    _scene_json(where="Billiard room kitchenette"), ensure_ascii=False)        # 非法地点
INVALID_ACTION = json.dumps(
    _scene_json(action="Flying Dance"), ensure_ascii=False)                    # 非法动作
INVALID_SHOT = json.dumps(
    _scene_json(shot="Drone Shot 2"), ensure_ascii=False)                      # 非法镜头
INVALID_POSITION = json.dumps(
    _scene_json(position="Position 99"), ensure_ascii=False)                   # 非法站位点
NO_WHERE_NO_INFO = json.dumps(_scene_json(with_info=False), ensure_ascii=False)  # 缺场景信息（无 who/where）
NO_WHERE_WITH_INFO = json.dumps(_scene_json(where=""), ensure_ascii=False)       # 缺 where
BAD_JSON = "this is not valid json at all"


# ---------------------------------------------------------------------------
# 测试工具
# ---------------------------------------------------------------------------
class SimpleStep(Step):
    """固定产出一步成功结果的 Step（模拟下游步骤）。"""

    def __init__(self, step_id, data, artifact_name=None, needs=None):
        super().__init__(step_id=step_id, needs=needs)
        self._data = data
        self._artifact_name = artifact_name or step_id

    def run(self, context):
        return StepOutput(data=self._data, artifact_name=self._artifact_name)


def make_review_handler(review_svc, decision, edited=None, comment="通过"):
    """构造"模拟人工审核"的 handler（决策由测试指定）。"""
    def handler(artifact, context):
        review = review_svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        if decision == "approved":
            done = review_svc.approve(review.review_id, comment=comment)
            return review_svc.to_review_result(done)
        if decision == "approved_with_edit":
            done = review_svc.approve_with_edit(review.review_id, edited, comment=comment)
            return review_svc.to_review_result(done, edited_content=edited)
        if decision == "rejected":
            done = review_svc.reject(review.review_id, reason=comment)
            return review_svc.to_review_result(done)
        raise ValueError(f"未知 decision: {decision}")
    return handler


def make_agent(fake, known_characters=None, scene_id=1, log_dir=None):
    """构造一个走 FakeTransport 的 SceneGenerationAgent（绝不碰真实 API）。

    已知角色 + 真实资产注册表由测试注入（Agent 自己不读文件）。
    """
    logger = TokenLogger(log_dir or TMP_LOGS)
    client = LLMClient(api_key="sk-mock", base_url="http://mock",
                       transport=fake, logger=logger,
                       max_retries=0, retry_backoff_seconds=0.0)
    return SceneGenerationAgent(client, scene_id=scene_id,
                                known_characters=known_characters or REAL_NAMES,
                                assets=ASSETS)


def build_pipeline(fake, mem, known=None, scene_id=1, decision="approved", edited=None,
                   comment="通过", extra_steps=None):
    """构造一条完整 Scene Generation 流水线（agent + step + engine + review）。"""
    agent = make_agent(fake, known_characters=known, scene_id=scene_id)
    step = SceneGenerationStep(agent, scene_id=scene_id)
    steps = [step] + list(extra_steps or [])
    artifacts_dir = TMP_PROJECTS / mem.project_id / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(mem.project_id, store, projects_root=TMP_PROJECTS)
    engine = WorkflowEngine(steps, artifacts_dir=artifacts_dir,
                            review_handler=make_review_handler(svc, decision, edited=edited,
                                                               comment=comment),
                            retry_backoff_seconds=0.0)
    return agent, step, engine, svc, store


def assemble_for(mem, step, wf_ctx, project_title="", scene_spec=None):
    """调度层：用 ContextAssembler 按 needs 组装上下文（注入 assets），塞进 inputs。"""
    assembler = ContextAssembler(mem, assets=ASSETS)
    assembled = assembler.assemble(step, wf_ctx, project_title=project_title)
    wf_ctx.inputs["assembled_context"] = assembled
    if scene_spec is not None:
        wf_ctx.inputs["scene_spec"] = scene_spec
    return assembled


def make_ctx(mem, scene_id=1, project_title=REAL_TOPIC, scene_spec=None):
    """直接给 agent.run 用的 AssembledContext（不经 Workflow）。"""
    step = SceneGenerationStep(make_agent(FakeTransport([]), known_characters=REAL_NAMES),
                               scene_id=scene_id)
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    return assemble_for(mem, step, wf_ctx, project_title=project_title, scene_spec=scene_spec)


def fresh_memory(project_id):
    return MemoryStore(project_id, projects_root=TMP_PROJECTS)


# ---------------------------------------------------------------------------
# T01~T03 初始化 / needs / ContextAssembler
# ---------------------------------------------------------------------------
def test_agent_init():
    agent = SceneGenerationAgent(llm_client=None, known_characters=REAL_NAMES, assets=ASSETS)
    ok = (agent.name == "scene_generation"
          and agent.known_characters == REAL_NAMES
          and agent.assets is not None)
    check("T01 Scene Generation Agent 可被正常初始化", ok,
          f"name={agent.name}; known={agent.known_characters}")


def test_agent_needs():
    mem = fresh_memory("p7_needs")
    agent = make_agent(FakeTransport([]))
    step = SceneGenerationStep(agent, scene_id=1)
    assembler = ContextAssembler(mem, assets=ASSETS)
    registered = set(assembler.available_keys())
    ok = (step.needs == ["project_info", "approved_facts", "characters",
                         "locations", "actions", "shots"]
          and all(n in registered for n in step.needs)
          and step.needs_review)
    check("T02 Scene Generation Agent 正确声明 needs（且都已被注册）", ok,
          f"needs={step.needs}; needs_review={step.needs_review}")


def test_context_assemble():
    mem = fresh_memory("p7_ctx")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    mem.set_world(REAL_WORLD)
    agent = make_agent(FakeTransport([]))
    step = SceneGenerationStep(agent, scene_id=1)
    ctx = make_ctx(mem, scene_id=1, scene_spec=REAL_SPEC)
    got = set(ctx.keys())
    need = {"project_info", "approved_facts", "characters", "locations", "actions", "shots"}
    ok = need <= got and ctx.item("locations") is not None \
        and len(ctx.item("locations").data) >= 1 \
        and ctx.item("actions").data and ctx.item("shots").data
    check("T03 ContextAssembler 能提供 Scene 所需全部上下文（含真实资产）", ok,
          f"keys={sorted(got)}; locations={len(ctx.item('locations').data)} 个; "
          f"actions={len(ctx.item('actions').data)} 个; shots={len(ctx.item('shots').data)} 个")


# ---------------------------------------------------------------------------
# T04~T06 Mock LLM / Validator
# ---------------------------------------------------------------------------
def test_mock_valid_scene():
    mem = fresh_memory("p7_valid")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    fake = FakeTransport([VALID_SCENE])
    agent = make_agent(fake, known_characters=REAL_NAMES)
    scene = agent.run(make_ctx(mem, scene_spec=REAL_SPEC), scene_spec=REAL_SPEC)
    ok = (isinstance(scene, Scene)
          and scene.info.who == ["Caleb", "Ivy"]
          and scene.info.where == APARTMENT
          and len(scene.lines) >= 1
          and len(fake.calls) == 1)
    check("T04 Mock LLM 合法输出 -> Agent 解析成 V0.2 Scene 对象", ok,
          f"who={scene.info.who}; where={scene.info.where}; lines={len(scene.lines)}")


def test_validator_pass():
    mem = fresh_memory("p7_vpass")
    fake = FakeTransport([VALID_SCENE])
    agent = make_agent(fake, known_characters=REAL_NAMES)
    scene = agent.run(make_ctx(mem, scene_spec=REAL_SPEC), scene_spec=REAL_SPEC)
    errors = agent.validator.validate(scene)
    check("T05 合法 Scene 能通过 Validator（0 错误）", errors == [],
          f"errors={[str(e) for e in errors]}")


def test_validator_reject():
    mem = fresh_memory("p7_vreject")
    cases = [
        ("未知角色 Charlie", INVALID_UNKNOWN_CHARACTER, "UNKNOWN_CHARACTER"),
        ("非法地点", INVALID_LOCATION, "INVALID_LOCATION"),
        ("非法动作", INVALID_ACTION, "INVALID_ACTION"),
        ("非法镜头", INVALID_SHOT, "INVALID_SHOT"),
        ("非法站位点", INVALID_POSITION, "INVALID_POSITION"),
        ("缺 where（scene_spec 也不提供）", NO_WHERE_WITH_INFO, "MISSING_LOCATION"),
    ]
    for label, payload, code in cases:
        fake = FakeTransport([payload])
        agent = make_agent(fake, known_characters=REAL_NAMES)
        # 注意：缺 where 这一条必须让 scene_spec 也不给 location，Agent 才补不上
        spec = dict(REAL_SPEC)
        if code == "MISSING_LOCATION":
            spec.pop("location", None)
        scene = agent.run(make_ctx(mem, scene_spec=spec), scene_spec=spec)
        errors = agent.validator.validate(scene)
        found = any(code in str(e) for e in errors)
        check(f"T06 Validator 拒绝：{label}", found,
              f"期望含 {code}; 实际 errors={[str(e).splitlines()[0] for e in errors]}")


# ---------------------------------------------------------------------------
# T07~T09 retry / 版本留存 / 三次失败
# ---------------------------------------------------------------------------
def test_retry_and_versions():
    mem = fresh_memory("p7_retry")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    # 第一次未知角色（不合格，但文件保留），第二次合法
    fake = FakeTransport([INVALID_UNKNOWN_CHARACTER, VALID_SCENE])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES)
    wf_ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    # 手动放入 engine 需要的 inputs（engine.run 会自己建 WorkflowContext）
    run = engine.run(project_id=mem.project_id, workflow_id="wf",
                     inputs={"assembled_context": wf_ctx,
                             "scene_spec": REAL_SPEC})
    res = run.steps[step.step_id]
    files = sorted(p.name for p in store.list_files("scene_1"))
    ok = (res.status == StepStatus.APPROVED
          and len(fake.calls) == 2          # 第 1 次失败 + 第 2 次成功
          and files == ["scene_1.json", "scene_1_v2.json"]
          and len(res.attempts) == 2)
    check("T07 非法输出进入 retry（重试后成功，2 次 LLM 调用）", ok,
          f"status={res.status}; calls={len(fake.calls)}; files={files}; attempts={len(res.attempts)}")

    # T08：两个版本文件内容不同、互不覆盖
    v1 = read_json(store.root / "scene_1.json")
    v2 = read_json(store.root / "scene_1_v2.json")
    v1_ok = any("Charlie" in json.dumps(l) for l in v1.get("scene", [])) or \
        "Charlie" in json.dumps(v1.get("scene information", {}))
    v2_ok = "Charlie" not in json.dumps(v2)
    check("T08 retry 不覆盖旧 Artifact（双版本并存且内容不同）", v1_ok and v2_ok,
          f"v1 含 Charlie={v1_ok}; v2 不含 Charlie={v2_ok}")


def test_fail_after_three():
    mem = fresh_memory("p7_fail3")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    fake = FakeTransport([BAD_JSON, BAD_JSON, BAD_JSON])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES)
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    run = engine.run(project_id=mem.project_id, workflow_id="wf",
                     inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    res = run.steps[step.step_id]
    ok = (res.status == StepStatus.FAILED
          and len(res.attempts) == 3
          and len(fake.calls) == 3
          and store.list_files("scene_1") == [])
    check("T09 连续三次非法 JSON -> Workflow FAILED 停止（不落盘）", ok,
          f"status={res.status}; attempts={len(res.attempts)}; "
          f"files={store.list_files('scene_1')}")


# ---------------------------------------------------------------------------
# T10 scene_spec 兜底 / T11 跨集一致 / T12 candidate
# ---------------------------------------------------------------------------
def test_spec_fallback():
    mem = fresh_memory("p7_spec")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    # LLM 只输出行内容，不输出 scene information（无 who/where）
    fake = FakeTransport([NO_WHERE_NO_INFO])
    agent = make_agent(fake, known_characters=REAL_NAMES)
    scene = agent.run(make_ctx(mem, scene_spec=REAL_SPEC), scene_spec=REAL_SPEC)
    ok = (scene.info.where == APARTMENT
          and scene.info.who == ["Caleb", "Ivy", "Theo"]     # 来自 scene_spec.characters
          and len(scene.lines) >= 1)
    check("T10 scene_spec 兜底 who/where（LLM 缺字段时用场景规范补齐）", ok,
          f"where={scene.info.where}; who={scene.info.who}")


def test_cross_episode_consistency():
    mem = fresh_memory("p7_cross")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    # 第二集场景：仍用已有角色，agent 输出 Scene 不携带任何"新角色档案"
    ep2_spec = dict(REAL_SPEC)
    ep2_spec["episode"] = 2
    ep2_spec["scene_id"] = 1
    fake = FakeTransport([VALID_SCENE])
    agent = make_agent(fake, known_characters=REAL_NAMES)
    scene = agent.run(make_ctx(mem, scene_spec=ep2_spec), scene_spec=ep2_spec)
    used = set(scene.info.who)
    ok = used <= set(REAL_NAMES) and len(used) >= 1
    check("T11 跨集一致性：第二集继续使用已有角色，不创建新角色档案", ok,
          f"who={sorted(used)}; 全部在已有角色内={used <= set(REAL_NAMES)}")


def test_candidate_not_auto_approved():
    mem = fresh_memory("p7_cand")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    # 两条候选：一条人工 approve，一条保持 candidate
    c1 = mem.add_candidate_fact(REAL_APPROVED_FACT, category="plot", source="memory_writer")
    mem.add_candidate_fact("Theo secretly won the lottery (candidate ONLY)",
                           category="plot", source="memory_writer")
    mem.approve_fact(c1.fact_id, reviewer="human")

    # 1) Memory 层：approved 只含人工确认的那条
    approved_contents = [f.content for f in mem.get_approved_facts()]
    ok1 = len(approved_contents) == 1 and "lottery" not in approved_contents[0]

    # 2) Scene 的 Context：approved_facts 只有 confirmed，candidate 不进入
    agent = make_agent(FakeTransport([VALID_SCENE]), known_characters=REAL_NAMES)
    fake_for_probe = FakeTransport([])
    step_for_ctx = SceneGenerationStep(
        make_agent(fake_for_probe, known_characters=REAL_NAMES), scene_id=1)
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf", run_id="probe", inputs={})
    assembled = assemble_for(mem, step_for_ctx, wf_ctx, project_title=REAL_TOPIC,
                             scene_spec=REAL_SPEC)
    ctx_facts = assembled.item("approved_facts").data
    ok2 = all("lottery" not in str(f) for f in ctx_facts) and len(ctx_facts) == 1

    # 3) 实际喂给 LLM 的 user payload 不含 candidate 内容
    fake3 = FakeTransport([VALID_SCENE])
    agent3 = make_agent(fake3, known_characters=REAL_NAMES)
    scene3 = agent3.run(assembled, scene_spec=REAL_SPEC)
    user_content = fake3.calls[0]["messages"][1]["content"]
    ok3 = "lottery" not in user_content and REAL_APPROVED_FACT in user_content

    check("T12 candidate 不自动进入 approved_facts；Scene 只用 approved_facts", ok1 and ok2 and ok3,
          f"approved 条数={len(approved_contents)}; ctx 条数={len(ctx_facts)}; "
          f"payload 含 approved={REAL_APPROVED_FACT in user_content}, 含 candidate={('lottery' in user_content)}")


# ---------------------------------------------------------------------------
# T13~T15 Review 三态
# ---------------------------------------------------------------------------
def test_review_approved_continue():
    mem = fresh_memory("p7_rv_ok")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    downstream = SimpleStep("after_scene", data={"ok": True}, artifact_name="after_scene")
    fake = FakeTransport([VALID_SCENE])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES,
                                               decision="approved",
                                               extra_steps=[downstream])
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    run = engine.run(project_id=mem.project_id, workflow_id="wf",
                     inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    ok = (run.status == "success"
          and run.steps[step.step_id].status == StepStatus.APPROVED
          and run.steps[step.step_id].review_id
          and "after_scene" in run.steps
          and run.steps["after_scene"].status == StepStatus.SUCCESS)
    check("T13 Review APPROVED 能继续（下游步骤执行）", ok,
          f"run={run.status}; scene={run.steps[step.step_id].status}; "
          f"review={run.steps[step.step_id].review_id}; downstream={run.steps.get('after_scene').status if 'after_scene' in run.steps else '?'}")


def test_review_approved_with_edit():
    mem = fresh_memory("p7_rv_edit")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    edited = json.dumps({"scene information": {"who": ["Caleb"], "where": APARTMENT,
                                               "what": "人工精简版"},
                         "scene": []}, ensure_ascii=False)
    fake = FakeTransport([VALID_SCENE])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES,
                                               decision="approved_with_edit", edited=edited)
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    run = engine.run(project_id=mem.project_id, workflow_id="wf",
                     inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    res = run.steps[step.step_id]
    files = sorted(p.name for p in store.list_files("scene_1"))
    ok = (res.status == StepStatus.APPROVED_WITH_EDIT
          and files == ["scene_1.json", "scene_1_v2.json"]   # 原文件 + 编辑版，都不覆盖
          and read_json(store.root / "scene_1.json").get("scene") is not None)  # 原始内容保留
    check("T14 Review APPROVED_WITH_EDIT 写新版本不覆盖原始文件", ok,
          f"status={res.status}; files={files}")


def test_review_rejected_stop():
    mem = fresh_memory("p7_rv_rej")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    downstream = SimpleStep("after_scene", data={"ok": True}, artifact_name="after_scene")
    fake = FakeTransport([VALID_SCENE])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES,
                                               decision="rejected", comment="质量不合格",
                                               extra_steps=[downstream])
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    run = engine.run(project_id=mem.project_id, workflow_id="wf",
                     inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    ok = (run.status == "failed"
          and run.steps[step.step_id].status == StepStatus.REJECTED
          and "after_scene" not in run.steps)          # 下游未执行
    check("T15 Review REJECTED 能停止流程（下游不执行）", ok,
          f"run={run.status}; scene={run.steps[step.step_id].status}; 下游存在={('after_scene' in run.steps)}")


# ---------------------------------------------------------------------------
# T16~T19 LLMClient / scene_id / resume / 可恢复
# ---------------------------------------------------------------------------
def test_llm_via_client():
    mem = fresh_memory("p7_llm")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    log_dir = TMP_LOGS
    fake = FakeTransport([VALID_SCENE])
    logger = TokenLogger(log_dir)
    client = LLMClient(api_key="sk-mock", base_url="http://mock", transport=fake,
                       logger=logger, max_retries=0, retry_backoff_seconds=0.0)
    agent = SceneGenerationAgent(client, scene_id=1, known_characters=REAL_NAMES, assets=ASSETS)
    scene = agent.run(make_ctx(mem, scene_spec=REAL_SPEC), scene_spec=REAL_SPEC)
    recs = TokenLogger(log_dir).read_all()
    mine = [r for r in recs if r.get("step") == "scene_generation"
            and r.get("agent") == "scene_generation"
            and r.get("request_id")]
    ok = (len(fake.calls) == 1
          and len(mine) >= 1
          and all(r.get("total_tokens", 0) > 0 and r.get("estimated_cost", 0) > 0
                  for r in mine))
    check("T16 LLM 调用经过 LLMClient（request_id/token/cost 记录）", ok,
          f"命中记录数={len(mine)}; tokens={[r.get('total_tokens') for r in mine]}; "
          f"cost={[r.get('estimated_cost') for r in mine]}")


def test_different_scene_ids():
    mem = fresh_memory("p7_multi")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    spec1 = dict(REAL_SPEC)
    spec1["scene_id"] = 1
    spec2 = dict(REAL_SPEC)
    spec2["scene_id"] = 2
    spec2["location"] = "Apartment kitchen"
    spec2["characters"] = ["Mercy", "Caleb"]

    fake1 = FakeTransport([json.dumps(_scene_json(who=["Caleb", "Ivy"]), ensure_ascii=False)])
    agent1, step1, engine1, _, store1 = build_pipeline(fake1, mem, known=REAL_NAMES, scene_id=1)
    ctx1 = make_ctx(mem, scene_id=1, scene_spec=spec1)
    engine1.run(project_id=mem.project_id, workflow_id="wf1",
                inputs={"assembled_context": ctx1, "scene_spec": spec1})

    fake2 = FakeTransport([json.dumps(_scene_json(who=["Mercy", "Caleb"]), ensure_ascii=False)])
    agent2, step2, engine2, _, store2 = build_pipeline(fake2, mem, known=REAL_NAMES, scene_id=2)
    ctx2 = make_ctx(mem, scene_id=2, scene_spec=spec2)
    engine2.run(project_id=mem.project_id, workflow_id="wf2",
                inputs={"assembled_context": ctx2, "scene_spec": spec2})

    f1 = sorted(p.name for p in store1.list_files("scene_1"))
    f2 = sorted(p.name for p in store2.list_files("scene_2"))
    ok = f1 == ["scene_1.json"] and f2 == ["scene_2.json"]
    check("T17 不同 scene_id 产生独立 artifact（scene_1 / scene_2）", ok,
          f"scene_1 files={f1}; scene_2 files={f2}")


def test_workflow_resume():
    mem = fresh_memory("p7_resume")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    fake = FakeTransport([VALID_SCENE])
    _, step, engine, _, _ = build_pipeline(fake, mem, known=REAL_NAMES)
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    run1 = engine.run(project_id=mem.project_id, workflow_id="wf",
                      inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    # resume：已批准的场景 Step 直接 SKIPPED，不再调 LLM
    run2 = engine.run(project_id=mem.project_id, workflow_id="wf", resume_from=run1,
                      inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    res2 = run2.steps[step.step_id]
    ok = (run1.steps[step.step_id].status == StepStatus.APPROVED
          and res2.status == StepStatus.SKIPPED
          and len(fake.calls) == 1)          # resume 不新增 LLM 调用
    check("T18 Workflow resume：已批准 Step 直接 SKIPPED（不再调 LLM）", ok,
          f"run1={run1.steps[step.step_id].status}; resume={res2.status}; calls={len(fake.calls)}")


def test_artifact_roundtrip():
    mem = fresh_memory("p7_round")
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    fake = FakeTransport([VALID_SCENE])
    _, step, engine, _, store = build_pipeline(fake, mem, known=REAL_NAMES)
    ctx = make_ctx(mem, scene_spec=REAL_SPEC)
    engine.run(project_id=mem.project_id, workflow_id="wf",
               inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    data = read_json(store.root / "scene_1.json")
    scene = Scene.from_v01(data, id=1)
    ok = (scene.info.where == APARTMENT and len(scene.lines) >= 1)
    check("T19 artifact 落盘内容能读回为 Scene（版本化产物可恢复）", ok,
          f"where={scene.info.where}; lines={len(scene.lines)}")


# ---------------------------------------------------------------------------
# T26 V0.1 完整性
# ---------------------------------------------------------------------------
def test_v01_unchanged():
    git_checks = [
        ["git", "rev-parse", "HEAD"],
        ["git", "rev-parse", "v0.1^{commit}"],
        ["git", "diff", "--", "FilmAgent/"],
        ["git", "status", "--short"],
    ]
    head = subprocess.run(git_checks[0], capture_output=True, text=True,
                          cwd=str(V2_ROOT.parent)).stdout.strip()
    v01 = subprocess.run(git_checks[1], capture_output=True, text=True,
                         cwd=str(V2_ROOT.parent)).stdout.strip()
    diff = subprocess.run(git_checks[2], capture_output=True, text=True,
                          cwd=str(V2_ROOT.parent)).stdout.strip()
    status = subprocess.run(git_checks[3], capture_output=True, text=True,
                            cwd=str(V2_ROOT.parent)).stdout.strip()
    ok = head == "344c0c6b7c48497be934957c29fcae029cca6752" \
        and v01 == "344c0c6b7c48497be934957c29fcae029cca6752" \
        and diff == "" \
        and all(not line.startswith(" M ") and not line.startswith("M ")
                for line in status.splitlines() if line.strip())
    check("T26 V0.1 完整性检查通过", ok,
          f"HEAD={head[:7]}; v0.1={v01[:7]}; diff空={diff == ''}; status={status!r}")


# ---------------------------------------------------------------------------
# 回归：阶段 1 / 3 / 4 / 6
# ---------------------------------------------------------------------------
def _run_python(path):
    proc = subprocess.run([sys.executable, str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(V2_ROOT.parent))
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


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    shutil.rmtree(TMP_PROJECTS, ignore_errors=True)
    shutil.rmtree(TMP_LOGS, ignore_errors=True)
    log("=" * 68)
    log("V0.2 阶段 7 —— Scene Generation Agent 测试（T01~T19 + T26 + 回归）")
    log("=" * 68)

    tests = [
        ("T01~T03 初始化 / needs / ContextAssembler",
         [test_agent_init, test_agent_needs, test_context_assemble]),
        ("T04~T06 Mock LLM / Validator",
         [test_mock_valid_scene, test_validator_pass, test_validator_reject]),
        ("T07~T09 retry / 版本留存 / 三次失败",
         [test_retry_and_versions, test_fail_after_three]),
        ("T10 scene_spec 兜底 / T11 跨集一致 / T12 candidate 隔离",
         [test_spec_fallback, test_cross_episode_consistency, test_candidate_not_auto_approved]),
        ("T13~T15 Review 三态",
         [test_review_approved_continue, test_review_approved_with_edit,
          test_review_rejected_stop]),
        ("T16~T19 LLMClient / scene_id / resume / 可恢复",
         [test_llm_via_client, test_different_scene_ids, test_workflow_resume,
          test_artifact_roundtrip]),
        ("T26 V0.1 完整性",
         [test_v01_unchanged]),
        ("回归 R1~R4（阶段 1 / 3 / 4 / 6）",
         [regression_stage1, regression_stage3, regression_stage4, regression_stage6]),
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
        "stage": "stage7_scene_generation",
        "summary": f"{passed}/{total} 通过",
        "real_llm_calls": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "results": _results,
    }
    write_json(TEST_DIR / "stage7_report.json", report)
    with (TEST_DIR / "console.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")

    print("\n".join(_log_lines))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
