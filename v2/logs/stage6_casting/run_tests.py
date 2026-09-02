# -*- coding: utf-8 -*-
"""
V0.2 阶段 6 —— Casting Agent 测试（T01~T20 + T26 + 阶段 1~4 回归）

覆盖（对应阶段 6 开工指令 §十六）：
    T01  Casting Agent 可以被正常初始化
    T02  Casting Agent 正确声明 needs
    T03  ContextAssembler 能提供 Casting 所需上下文
    T04  Mock LLM 返回合法 Casting
    T05  合法 Casting 能通过 Validator
    T06  非法 Casting 能被 Validator 拒绝
    T07  非法输出进入 retry
    T08  retry 不覆盖旧 Artifact
    T09  三次失败后 Workflow 正确停止
    T10  已有角色可以复用
    T11  新角色能够创建
    T12  已有角色不会重复创建
    T13  candidate memory 不会自动进入 approved memory
    T14  Review APPROVED 能继续
    T15  Review APPROVED_WITH_EDIT 能产生新版本
    T16  Review REJECTED 能停止流程
    T17  Workflow 可以 resume
    T18  LLM 调用经过 LLMClient
    T19  token / cost 正确记录
    T20  request_id 正确记录
    T26  V0.1 完整性检查通过

回归（§二十二）：
    R1  阶段 1（19/19）
    R2  阶段 3（38/38）
    R3  阶段 4（25/25，内部复验阶段 1/2(mock)/3）

T21~T25（真实 DeepSeek 最小调用）单独放在 run_real_casting.py，
只在本地闭环通过后执行一次，避免烧 token。本文件真实 LLM 调用 = 0。
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
from core.models import CastingEntry, CastingResult, Character   # noqa: E402
from core.validator import CastingValidator, format_errors  # noqa: E402
from core.token_log import TokenUsage, TokenLogger           # noqa: E402
from memory.store import MemoryStore                         # noqa: E402
from memory.models import FactStatus                         # noqa: E402
from context.assembler import ContextAssembler               # noqa: E402
from context.models import AssembledContext                  # noqa: E402
from review.service import ReviewService                     # noqa: E402
from workflow.artifact import ArtifactStore                  # noqa: E402
from workflow.engine import WorkflowEngine                   # noqa: E402
from workflow.step import Step                               # noqa: E402
from workflow.models import (                                # noqa: E402
    StepStatus, StepOutput, WorkflowContext, WorkflowRun,
)
from llm.client import LLMClient                             # noqa: E402
from agents.casting import CastingAgent, CastingStep         # noqa: E402

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
# 真实 V0.1 数据（只读 fixture，阶段 6 指令 §十七）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES / "actors_profile.json")       # 真实角色资料（4 人）
REAL_NAMES = [c["name"] for c in REAL_PROFILE]                    # ["Mercy","Caleb","Ivy","Theo"]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_FACT = "Theo has been estranged from the friend group for three years after a bitter fallout."
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}


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


# Mock 响应样例
VALID_NEW = json.dumps({
    "episode": 1,
    "topic": REAL_TOPIC,
    "entries": [
        {"name": "Caleb", "status": "new", "role": "纪录片导演，回归聚会的主角之一",
         "character": {"name": "Caleb", "age": "35", "gender": "male",
                       "occupation": "Documentary filmmaker",
                       "personality traits": "Charismatic, restless, guilt-ridden",
                       "speaking style": "Fast-paced, sarcastic"}},
        {"name": "Ivy", "status": "new", "role": "急诊护士，主角之一",
         "character": {"name": "Ivy", "age": "33", "gender": "female",
                       "occupation": "ER nurse",
                       "personality traits": "Blunt, pragmatic, loyal",
                       "speaking style": "Direct and clipped"}},
    ],
}, ensure_ascii=False)

VALID_MIXED = json.dumps({
    "episode": 2,
    "topic": REAL_TOPIC,
    "entries": [
        {"name": "Caleb", "status": "existing", "role": "继续出场的主角"},
        {"name": "Nina", "status": "new", "role": "新登场的记者",
         "character": {"name": "Nina", "age": "29", "gender": "female",
                       "occupation": "Journalist",
                       "personality traits": "Curious, fearless",
                       "speaking style": "Witty, direct"}},
    ],
}, ensure_ascii=False)

INVALID_INCOMPLETE = json.dumps({
    "episode": 1,
    "entries": [
        {"name": "Nina", "status": "new", "character": {"name": "Nina"}},
    ],
}, ensure_ascii=False)   # 新角色档案缺字段：能进 normalize，被 Validator 拒绝

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


def make_agent(fake, known_characters=None, log_dir=None):
    """构造一个走 FakeTransport 的 CastingAgent（绝不碰真实 API）。"""
    logger = TokenLogger(log_dir or TMP_LOGS)
    client = LLMClient(api_key="sk-mock", base_url="http://mock",
                       transport=fake, logger=logger,
                       max_retries=0, retry_backoff_seconds=0.0)
    return CastingAgent(client, known_characters=known_characters)


def build_pipeline(fake, mem, known=None, decision="approved", edited=None,
                   comment="通过", extra_steps=None):
    """构造一条完整 Casting 流水线（agent + step + engine + review）。"""
    agent = make_agent(fake, known_characters=known)
    step = CastingStep(agent)
    steps = [step] + list(extra_steps or [])
    artifacts_dir = TMP_PROJECTS / mem.project_id / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(mem.project_id, store, projects_root=TMP_PROJECTS)
    engine = WorkflowEngine(steps, artifacts_dir=artifacts_dir,
                            review_handler=make_review_handler(svc, decision, edited=edited,
                                                               comment=comment),
                            retry_backoff_seconds=0.0)
    return agent, step, engine, svc, store


def assemble_for(mem, step, wf_ctx, project_title=""):
    """调度层：用 ContextAssembler 按 needs 组装上下文，塞进 WorkflowContext.inputs。"""
    assembler = ContextAssembler(mem)
    assembled = assembler.assemble(step, wf_ctx, project_title=project_title)
    wf_ctx.inputs["assembled_context"] = assembled
    return assembled


def make_ctx(mem, project_title=REAL_TOPIC):
    """直接给 agent.run 用的 AssembledContext（不经 Workflow）。"""
    step = CastingStep(make_agent(FakeTransport([])))
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    return assemble_for(mem, step, wf_ctx, project_title=project_title)


def fresh_memory(project_id):
    return MemoryStore(project_id, projects_root=TMP_PROJECTS)


# ---------------------------------------------------------------------------
# T01~T03 初始化 / needs / ContextAssembler
# ---------------------------------------------------------------------------
def test_agent_init():
    agent = CastingAgent(llm_client=None, known_characters=REAL_NAMES)
    ok = agent.name == "casting" and agent.known_characters == REAL_NAMES
    check("T01 Casting Agent 可被正常初始化", ok,
          f"name={agent.name}; known={agent.known_characters}")


def test_agent_needs():
    mem = fresh_memory("p6_needs")
    agent = make_agent(FakeTransport([]))
    step = CastingStep(agent)
    assembler = ContextAssembler(mem)
    registered = set(assembler.available_keys())
    ok = (step.needs == ["project_info", "approved_facts", "characters", "world"]
          and all(n in registered for n in step.needs)
          and step.needs_review)
    check("T02 Casting Agent 正确声明 needs（且都已被注册）", ok,
          f"needs={step.needs}; needs_review={step.needs_review}")


def test_context_assemble():
    mem = fresh_memory("p6_ctx")
    mem.set_characters([{"name": "Caleb", "age": "35"}])
    mem.set_world(REAL_WORLD)
    mem.add_candidate_fact(REAL_FACT, category="plot", source="human")
    facts = mem.get_candidate_facts()
    mem.approve_fact(facts[0].fact_id, reviewer="human")

    agent = make_agent(FakeTransport([]))
    step = CastingStep(agent)
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="r1", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    ok = (set(assembled.keys()) == set(step.needs)
          and assembled.item("project_info") is not None
          and assembled.item("approved_facts") is not None
          and assembled.item("characters") is not None
          and assembled.item("world") is not None
          and assembled.project_id == mem.project_id
          and assembled.step_id == "casting")
    detail = f"keys={assembled.keys()}; project_id={assembled.project_id}"
    check("T03 ContextAssembler 能提供 Casting 所需上下文", ok, detail)


# ---------------------------------------------------------------------------
# T04~T06 Mock LLM / Validator
# ---------------------------------------------------------------------------
def test_mock_valid_casting():
    mem = fresh_memory("p6_valid")
    fake = FakeTransport([VALID_NEW])
    agent = make_agent(fake, known_characters=[])
    result = agent.run(make_ctx(mem))
    ok = isinstance(result, CastingResult) and len(result.entries) == 2
    ok = ok and all(e.status == "new" for e in result.entries)
    ok = ok and all(e.character is not None for e in result.entries)
    check("T04 Mock LLM 返回合法 Casting", ok,
          f"entries={[(e.name, e.status) for e in result.entries]}; episode={result.episode}")


def test_validator_pass():
    mem = fresh_memory("p6_valid2")
    fake = FakeTransport([VALID_NEW])
    agent = make_agent(fake, known_characters=[])
    result = agent.run(make_ctx(mem))
    errors = agent.validator.validate(result)
    check("T05 合法 Casting 能通过 Validator", errors == [],
          f"errors={[str(e) for e in errors]}")


def test_validator_reject():
    v = CastingValidator(known_characters=["Caleb"])
    # 非法情形一：existing 角色名不在已知清单
    errs1 = v.validate_dict({"episode": 1, "entries": [
        {"name": "Nobody", "status": "existing"}]})
    # 非法情形二：新角色与已有角色重名（该复用却新建）
    errs2 = v.validate_dict({"episode": 1, "entries": [
        {"name": "Caleb", "status": "new", "character": {
            "name": "Caleb", "age": "35", "gender": "male", "occupation": "x",
            "personality traits": "p", "speaking style": "s"}}]})
    # 非法情形三：新角色档案缺字段
    errs3 = v.validate_dict({"episode": 1, "entries": [
        {"name": "Nina", "status": "new", "character": {"name": "Nina"}}]})
    # 非法情形四：角色名重复
    errs4 = v.validate_dict({"episode": 1, "entries": [
        {"name": "Caleb", "status": "existing"},
        {"name": "Caleb", "status": "existing"}]})
    # 非法情形五：episode 类型错误
    errs5 = v.validate_dict({"episode": "one", "entries": [
        {"name": "Caleb", "status": "existing"}]})
    ok = (len(errs1) > 0 and len(errs2) > 0 and len(errs3) > 0
          and len(errs4) > 0 and len(errs5) > 0)
    codes = []
    for e in (errs1 + errs2 + errs3 + errs4 + errs5):
        codes.append(e.code)
    check("T06 非法 Casting 能被 Validator 拒绝", ok, f"错误码={sorted(set(codes))}")


# ---------------------------------------------------------------------------
# T07~T09 retry / 版本留存 / 三次失败
# ---------------------------------------------------------------------------
def test_retry_and_versions():
    pid = "p6_retry"
    mem = fresh_memory(pid)
    fake = FakeTransport([INVALID_INCOMPLETE, VALID_NEW])   # 第一次不合格，第二次合法
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=[], decision="approved")
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf", inputs={"assembled_context": assembled})

    res = run.steps["casting"]
    # T07：第一次 validate 不合格进入重试，第二次通过
    ok_t07 = (len(res.attempts) == 2
              and res.attempts[0].status == "FAILED"
              and res.attempts[0].errors
              and res.is_success)
    check("T07 非法输出进入 retry", ok_t07,
          f"attempts={[(a.attempt, a.status) for a in res.attempts]}; "
          f"最终={res.status}")

    # T08：两版文件都在，内容不被覆盖
    files = sorted(p.name for p in store.list_files("casting"))
    first = store.load(res.attempts[0].artifact) if res.attempts[0].artifact else None
    second = store.load(res.attempts[1].artifact) if res.attempts[1].artifact else None
    # 注意：Character.from_v01({"name": "Nina"}) 缺 age 时得到 None（落盘成 null），
    # validator 会把它判为 MISSING_FIELD；新版（VALID_NEW）age="35"。用 age 区分两版。
    old_age = first["entries"][0]["character"].get("age", "")
    new_age = second["entries"][0]["character"].get("age", "")
    ok_t08 = (files == ["casting.json", "casting_v2.json"]
              and first is not None and second is not None
              and first != second
              and old_age is None        # 旧版=缺字段版（age 为 null）
              and new_age == "35")       # 新版=完整档案
    check("T08 retry 不覆盖旧 Artifact", ok_t08,
          f"files={files}; 旧版 age={old_age!r}; 新版 age={new_age!r}; 内容不同={first != second}")


def test_fail_after_three():
    pid = "p6_fail3"
    mem = fresh_memory(pid)
    fake = FakeTransport([BAD_JSON, BAD_JSON, BAD_JSON])
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=[], decision="approved")
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf", max_attempts=3,
                     inputs={"assembled_context": assembled})
    res = run.steps["casting"]
    ok = (run.status == "failed" and res.status == "FAILED"
          and len(res.attempts) == 3 and not res.is_success)
    check("T09 三次失败后 Workflow 正确停止", ok,
          f"run.status={run.status}; step.status={res.status}; attempts={len(res.attempts)}")


# ---------------------------------------------------------------------------
# T10~T12 跨集角色复用 / 新角色 / 不重复建档
# ---------------------------------------------------------------------------
def test_reuse_existing():
    mem = fresh_memory("p6_reuse")
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])   # Mercy, Caleb 已有
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])                        # Caleb(existing) + Nina(new)
    agent = make_agent(fake, known_characters=known)
    result = agent.run(make_ctx(mem))
    caleb = [e for e in result.entries if e.name == "Caleb"][0]
    ok = caleb.status == "existing" and caleb.character is None
    check("T10 已有角色可以复用", ok,
          f"Caleb 状态={caleb.status}; 带档案={caleb.character is not None}")


def test_new_character():
    mem = fresh_memory("p6_newchar")
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])
    agent = make_agent(fake, known_characters=known)
    result = agent.run(make_ctx(mem))
    nina = [e for e in result.entries if e.name == "Nina"][0]
    ok = (nina.status == "new" and nina.character is not None
          and nina.character.name == "Nina" and nina.character.age == "29")
    check("T11 新角色能够创建", ok,
          f"Nina 状态={nina.status}; 档案={nina.character.to_v01() if nina.character else None}")


def test_no_duplicate_character():
    mem = fresh_memory("p6_nodup")
    # 第二集：Nina 已经是项目已有角色（第一集建档过）
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]] + [
        {"name": "Nina", "age": "29", "gender": "female", "occupation": "Journalist",
         "personality traits": "Curious, fearless", "speaking style": "Witty, direct"}])
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])   # LLM 又输出 Nina（这次应复用，不重复建档）
    agent = make_agent(fake, known_characters=known)
    result = agent.run(make_ctx(mem))
    nina = [e for e in result.entries if e.name == "Nina"][0]
    caleb = [e for e in result.entries if e.name == "Caleb"][0]
    ok = (nina.status == "existing" and caleb.status == "existing"
          and all(e.status == "existing" for e in result.entries))
    check("T12 已有角色不会重复创建", ok,
          f"entries={[(e.name, e.status) for e in result.entries]}")


# ---------------------------------------------------------------------------
# T13 candidate memory 不自动升 approved
# ---------------------------------------------------------------------------
def test_candidate_not_auto_approved():
    mem = fresh_memory("p6_candidate")
    fact = mem.add_candidate_fact("Nina is a new reporter in episode 2",
                                  category="character", source="casting")
    status_before = fact.status                    # 在 approve 之前记录，避免事后被原地修改
    approved_before = [f.content for f in mem.get_approved_facts()]
    ok1 = (status_before == FactStatus.CANDIDATE and "Nina is a new reporter in episode 2"
           not in approved_before)
    # 人工 approve 后，才能进入 approved
    done = mem.approve_fact(fact.fact_id, reviewer="human", comment="确认")
    approved_after = [f.content for f in mem.get_approved_facts()]
    ok2 = done.status == FactStatus.APPROVED and "Nina is a new reporter in episode 2" in approved_after
    check("T13 candidate memory 不会自动进入 approved memory", ok1 and ok2,
          f"候选时={status_before}; approved 清单（前）={approved_before}; "
          f"人工审核后={done.status}; approved 清单（后）含该条={ok2}")


# ---------------------------------------------------------------------------
# T14~T16 Review 三态
# ---------------------------------------------------------------------------
def test_review_approved_continue():
    pid = "p6_rv_approved"
    mem = fresh_memory(pid)
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])
    scene_plan = SimpleStep("scene_plan", data={"scene": 1}, artifact_name="scenes_plan")
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=known,
                                                     decision="approved",
                                                     extra_steps=[scene_plan])
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": assembled})
    res = run.steps["casting"]
    ok = (res.status == StepStatus.APPROVED and res.is_success
          and run.status == "success"
          and run.steps["scene_plan"].status == StepStatus.SUCCESS
          and res.review_id is not None)
    check("T14 Review APPROVED 能继续", ok,
          f"casting={res.status}; scene_plan={run.steps['scene_plan'].status}; "
          f"run={run.status}; review_id={res.review_id}")


def test_review_approved_with_edit():
    pid = "p6_rv_edit"
    mem = fresh_memory(pid)
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])
    known = [c["name"] for c in mem.get_characters()]
    edited = json.loads(VALID_MIXED)   # 人工修改版：给 Nina 的 role 加注
    edited["entries"][1]["role"] = "人工修改：Nina 升级为重要配角"
    fake = FakeTransport([VALID_MIXED])
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=known,
                                                     decision="approved_with_edit",
                                                     edited=edited)
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": assembled})
    res = run.steps["casting"]
    files = sorted(p.name for p in store.list_files("casting"))
    # 编辑版是新版本文件（casting_v2.json），原始 casting.json 保留
    ok = (res.status == StepStatus.APPROVED_WITH_EDIT and res.is_success
          and run.status == "success"
          and res.approved_artifact.filename == "casting_v2.json"
          and files == ["casting.json", "casting_v2.json"]
          and res.approved_artifact.read() == edited)
    check("T15 Review APPROVED_WITH_EDIT 能产生新版本", ok,
          f"status={res.status}; approved={res.approved_artifact.filename}; files={files}")


def test_review_rejected_stop():
    pid = "p6_rv_rejected"
    mem = fresh_memory(pid)
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])
    scene_plan = SimpleStep("scene_plan", data={"scene": 1}, artifact_name="scenes_plan")
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=known,
                                                     decision="rejected", comment="角色不合理",
                                                     extra_steps=[scene_plan])
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": assembled})
    res = run.steps["casting"]
    stopped = "scene_plan" not in run.steps
    ok = (run.status == "failed" and res.status == StepStatus.REJECTED
          and not res.is_success and stopped)
    check("T16 Review REJECTED 能停止流程", ok,
          f"run={run.status}; casting={res.status}; scene_plan 未执行={stopped}")


# ---------------------------------------------------------------------------
# T17 Workflow resume
# ---------------------------------------------------------------------------
def test_workflow_resume():
    pid = "p6_resume"
    mem = fresh_memory(pid)
    mem.set_characters([dict(c) for c in REAL_PROFILE[:2]])
    known = [c["name"] for c in mem.get_characters()]
    fake = FakeTransport([VALID_MIXED])
    agent, step, engine, svc, store = build_pipeline(fake, mem, known=known,
                                                     decision="approved")
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembled = assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)

    run1 = engine.run(project_id=pid, workflow_id="wf",
                      inputs={"assembled_context": assembled})
    llm_calls_after_first = len(fake.calls)
    run2 = engine.run(project_id=pid, workflow_id="wf", resume_from=run1,
                      inputs={"assembled_context": assembled})
    res2 = run2.steps["casting"]
    ok = (run1.status == "success" and res2.status == StepStatus.SKIPPED
          and run2.status == "success"
          and len(fake.calls) == llm_calls_after_first   # resume 不再调 LLM
          and res2.approved_artifact is not None)
    check("T17 Workflow 可以 resume（跳过已成功 Step）", ok,
          f"run1={run1.status}; resume 后 casting={res2.status}; "
          f"LLM 调用保持 {llm_calls_after_first} 次")


# ---------------------------------------------------------------------------
# T18~T20 LLMClient / token / request_id
# ---------------------------------------------------------------------------
def test_llm_via_client():
    mem = fresh_memory("p6_llm")
    fake = FakeTransport([VALID_NEW])
    agent = make_agent(fake, known_characters=[], log_dir=TMP_LOGS)
    result = agent.run(make_ctx(mem))
    ok = len(fake.calls) == 1
    ok = ok and fake.calls[0]["messages"][0]["role"] == "system"
    ok = ok and fake.calls[0]["messages"][1]["role"] == "user"
    ok = ok and fake.calls[0]["model"] == "deepseek-chat"
    check("T18 LLM 调用经过 LLMClient", ok,
          f"calls={len(fake.calls)}; model={fake.calls[0]['model'] if fake.calls else '?'}")


def test_token_cost_recorded():
    mem = fresh_memory("p6_token")
    fake = FakeTransport([VALID_NEW])
    agent = make_agent(fake, known_characters=[], log_dir=TMP_LOGS)
    agent.run(make_ctx(mem))
    records = TokenLogger(TMP_LOGS).read_all()
    last = records[-1] if records else {}
    ok = (len(records) > 0
          and last.get("total_tokens", 0) == 40
          and last.get("input_tokens") == 25 and last.get("output_tokens") == 15
          and isinstance(last.get("estimated_cost"), (int, float))
          and last.get("step") == "casting" and last.get("agent") == "casting"
          and last.get("success") is True)
    check("T19 token / cost 正确记录", ok,
          f"records={len(records)}; total={last.get('total_tokens')}; "
          f"cost={last.get('estimated_cost')}")


def test_request_id_recorded():
    fake = FakeTransport([VALID_NEW])
    logger = TokenLogger(TMP_LOGS)
    client = LLMClient(api_key="sk-mock", base_url="http://mock",
                       transport=fake, logger=logger, max_retries=0,
                       retry_backoff_seconds=0.0)
    resp = client.chat([{"role": "user", "content": "hello"}],
                       step="casting", agent="casting")
    records = logger.read_all()
    last = records[-1] if records else {}
    ok = bool(resp.request_id) and resp.request_id == last.get("request_id")
    check("T20 request_id 正确记录", ok,
          f"request_id={resp.request_id[:12]}...; 与日志一致={resp.request_id == last.get('request_id')}")


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
# 回归：阶段 1 / 3 / 4
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


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    shutil.rmtree(TMP_PROJECTS, ignore_errors=True)
    shutil.rmtree(TMP_LOGS, ignore_errors=True)
    log("=" * 68)
    log("V0.2 阶段 6 —— Casting Agent 测试（T01~T20 + T26 + 回归）")
    log("=" * 68)

    tests = [
        ("T01~T03 初始化 / needs / ContextAssembler",
         [test_agent_init, test_agent_needs, test_context_assemble]),
        ("T04~T06 Mock LLM / Validator",
         [test_mock_valid_casting, test_validator_pass, test_validator_reject]),
        ("T07~T09 retry / 版本留存 / 三次失败",
         [test_retry_and_versions, test_fail_after_three]),
        ("T10~T12 跨集角色复用 / 新角色 / 不重复建档",
         [test_reuse_existing, test_new_character, test_no_duplicate_character]),
        ("T13 candidate memory 不自动升 approved",
         [test_candidate_not_auto_approved]),
        ("T14~T16 Review 三态",
         [test_review_approved_continue, test_review_approved_with_edit,
          test_review_rejected_stop]),
        ("T17 Workflow resume",
         [test_workflow_resume]),
        ("T18~T20 LLMClient / token / request_id",
         [test_llm_via_client, test_token_cost_recorded, test_request_id_recorded]),
        ("T26 V0.1 完整性",
         [test_v01_unchanged]),
        ("回归 R1~R3（阶段 1 / 3 / 4）",
         [regression_stage1, regression_stage3, regression_stage4]),
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
        "stage": "stage6_casting",
        "summary": f"{passed}/{total} 通过",
        "real_llm_calls": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "results": _results,
    }
    write_json(TEST_DIR / "stage6_report.json", report)
    with (TEST_DIR / "console.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")

    print("\n".join(_log_lines))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
