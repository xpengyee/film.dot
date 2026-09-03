# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Memory + ContextAssembler + 人工审核 测试

覆盖（对应开工指令 §十九 的 20 项 + §二十 的 1~3 阶段回归）：
    T1   Memory 创建项目
    T2   Memory 写入 candidate fact
    T3   candidate fact 不会直接成为 approved fact
    T4   approve fact 后可以读取
    T5   reject fact 后不会进入 approved context
    T6   project A 与 project B Memory 隔离
    T7   Memory 持久化后重新读取一致
    T8   Step 可以声明 needs
    T9   ContextAssembler 根据 needs 组装 Context
    T10  不需要的数据不会进入 Context
    T11  Context 包含来源信息
    T12  approved review
    T13  approved_with_edit review
    T14  rejected review
    T15  人工修改不会覆盖原始 artifact
    T16  review 状态能够进入 Workflow
    T17  approved 后 Workflow 可以继续
    T18  rejected 后 Workflow 正确停止
    T19  阶段 3 原有 38/38 测试继续通过（回归）
    T20  V0.1 仍然完全没有修改
    另加：
    R1   阶段 1 回归（19/19）
    R2   阶段 2 回归（纯 mock 复验核心能力，0 真实调用）

原则：全部用本地 fixture / mock / 临时 JSON 目录，真实 LLM 调用 = 0。
"""
import importlib.util
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用项目目录（绝不碰真实项目）

sys.path.insert(0, str(V2_ROOT))
from core.io import write_json                             # noqa: E402
from core.assets import AssetRegistry                       # noqa: E402
from memory.store import MemoryStore                        # noqa: E402
from memory.models import FactStatus                        # noqa: E402
from context.assembler import ContextAssembler              # noqa: E402
from review.service import ReviewService                    # noqa: E402
from workflow.artifact import ArtifactStore                 # noqa: E402
from workflow.engine import WorkflowEngine                  # noqa: E402
from workflow.step import Step                              # noqa: E402
from workflow.models import (                               # noqa: E402
    StepStatus, StepOutput, WorkflowContext,
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
# 测试用 Step 与工具
# ---------------------------------------------------------------------------
class SimpleStep(Step):
    """固定产出一步成功结果的 Step。"""

    def __init__(self, step_id, data, artifact_name=None, needs=None, needs_review=False):
        super().__init__(step_id=step_id, needs=needs, needs_review=needs_review)
        self._data = data
        self._artifact_name = artifact_name or step_id

    def run(self, context):
        return StepOutput(data=self._data, artifact_name=self._artifact_name)


class NeedsStep(Step):
    """只声明 needs、不实际产出的 Step（测 ContextAssembler 用）。"""

    def __init__(self, step_id, needs):
        super().__init__(step_id=step_id, needs=needs)

    def run(self, context):
        raise AssertionError("NeedsStep 不应被真正执行")


def fresh_memory(project_id):
    """建一个测试专用 MemoryStore（落到 tmp_projects，避免污染真实项目）。"""
    return MemoryStore(project_id, projects_root=TMP_PROJECTS)


def make_review_handler(review_svc, decision, edited=None, comment="通过"):
    """构造一个"模拟人工审核"的 handler，给 WorkflowEngine 注入。

    decision: "approved" | "approved_with_edit" | "rejected"
    """
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


# ---------------------------------------------------------------------------
# T1 ~ T7  Memory
# ---------------------------------------------------------------------------
def test_memory_create():
    mem = fresh_memory("p_mem_create")
    check("T1 Memory 创建项目", mem.root.exists() and mem.project_id == "p_mem_create",
          f"memory 目录={mem.root.relative_to(V2_ROOT)}")


def test_memory_candidate():
    mem = fresh_memory("p_mem_candidate")
    fact = mem.add_candidate_fact("Theo left the apartment", category="plot", source="memory_writer")
    check("T2 写入 candidate fact", fact.status == FactStatus.CANDIDATE and bool(fact.fact_id),
          f"fact_id={fact.fact_id}; status={fact.status}; source={fact.source}")


def test_memory_candidate_not_approved():
    mem = fresh_memory("p_mem_not_approved")
    mem.add_candidate_fact("Ivy knows the location of the key", category="plot")
    approved = mem.get_approved_facts()
    check("T3 candidate 不直接成为 approved", len(approved) == 0,
          f"get_approved_facts() 空（candidate 未确认前绝不进 approved）")


def test_memory_approve_read():
    mem = fresh_memory("p_mem_approve")
    fact = mem.add_candidate_fact("Theo left the apartment", category="plot", source="memory_writer")
    mem.approve_fact(fact.fact_id, reviewer="human", comment="OK")
    approved = mem.get_approved_facts()
    ok = len(approved) == 1 and approved[0].content == "Theo left the apartment" \
        and approved[0].reviewer == "human" and approved[0].status == FactStatus.APPROVED
    check("T4 approve 后可以读取", ok,
          f"approved[0]={approved[0].content!r}; reviewer={approved[0].reviewer}; "
          f"category={approved[0].category}")


def test_memory_reject_not_in_context():
    mem = fresh_memory("p_mem_reject")
    f1 = mem.add_candidate_fact("应该保留的事实", category="plot")
    f2 = mem.add_candidate_fact("应该拒绝的事实", category="plot")
    mem.approve_fact(f1.fact_id, reviewer="human")
    mem.reject_fact(f2.fact_id, reviewer="human", comment="与设定冲突")
    approved = mem.get_approved_facts()
    rejected = mem.get_rejected_facts()
    ok = len(approved) == 1 and approved[0].fact_id == f1.fact_id \
        and len(rejected) == 1 and rejected[0].fact_id == f2.fact_id \
        and all(f.fact_id != f2.fact_id for f in approved)
    check("T5 reject 后不会进入 approved context", ok,
          f"approved={[f.content for f in approved]}; rejected={[f.content for f in rejected]}")


def test_memory_project_isolation():
    mem_a = fresh_memory("project_A")
    mem_b = fresh_memory("project_B")
    fa = mem_a.add_candidate_fact("A 的独家事实")
    mem_a.approve_fact(fa.fact_id, reviewer="human")
    fb = mem_b.add_candidate_fact("B 的独家事实")
    mem_b.approve_fact(fb.fact_id, reviewer="human")
    a_facts = mem_a.get_approved_facts()
    b_facts = mem_b.get_approved_facts()
    ok = (len(a_facts) == 1 and len(b_facts) == 1
          and a_facts[0].fact_id == fa.fact_id and b_facts[0].fact_id == fb.fact_id
          and a_facts[0].content != b_facts[0].content
          and all(f.fact_id != fb.fact_id for f in a_facts)
          and all(f.fact_id != fa.fact_id for f in b_facts))
    check("T6 project A/B Memory 隔离", ok,
          f"A approved={[f.content for f in a_facts]}; B approved={[f.content for f in b_facts]}")


def test_memory_persistence():
    pid = "p_mem_persist"
    mem = fresh_memory(pid)
    f = mem.add_candidate_fact("持久化的事实", category="world")
    mem.approve_fact(f.fact_id, reviewer="human")
    mem.set_characters([{"name": "Ivy", "occupation": "lawyer"}])
    mem.set_world({"city": "Shanghai", "no_supernatural": True})
    mem.add_history_entry({"episode": 1, "artifact": "script_0.json"})
    # 模拟"关闭程序"：重新 new 一个 MemoryStore，从磁盘重新读
    mem2 = MemoryStore(pid, projects_root=TMP_PROJECTS)
    ok = (mem2.get_approved_facts()[0].content == "持久化的事实"
          and mem2.get_characters()[0]["name"] == "Ivy"
          and mem2.get_world()["city"] == "Shanghai"
          and mem2.get_history()[0]["artifact"] == "script_0.json"
          and mem2.revision == mem.revision)
    check("T7 Memory 持久化后重新读取一致", ok,
          f"重读 facts={len(mem2.get_approved_facts())} characters={len(mem2.get_characters())} "
          f"world.city={mem2.get_world().get('city')} history={len(mem2.get_history())} "
          f"revision={mem2.revision}")


# ---------------------------------------------------------------------------
# T8 ~ T11  ContextAssembler
# ---------------------------------------------------------------------------
def test_step_declares_needs():
    step = NeedsStep(step_id="lines_generate", needs=["approved_facts", "characters"])
    check("T8 Step 可以声明 needs", step.needs == ["approved_facts", "characters"],
          f"needs={step.needs}")
    check("T8 默认 Step 无 needs（兼容阶段 3）", SimpleStep("x", {}).needs == [],
          "不声明 needs 时为空列表，向后兼容")


def test_context_assemble_by_needs():
    mem = fresh_memory("p_ctx_assemble")
    f = mem.add_candidate_fact("Theo left the apartment")
    mem.approve_fact(f.fact_id, reviewer="human")
    mem.set_characters([{"name": "Theo", "occupation": "architect"}])
    assembler = ContextAssembler(mem)
    step = NeedsStep(step_id="lines_generate", needs=["approved_facts", "characters"])
    ctx = assembler.assemble(step, _dummy_context("p_ctx_assemble"))
    af = ctx.item("approved_facts")
    chars = ctx.item("characters")
    ok = ctx.keys() == ["approved_facts", "characters"] \
        and af is not None and af.data[0]["content"] == "Theo left the apartment" \
        and chars is not None and chars.data[0]["name"] == "Theo"
    check("T9 ContextAssembler 根据 needs 组装 Context", ok,
          f"keys={ctx.keys()}; approved_facts[0]={af.data[0] if af else None}; "
          f"characters[0]={chars.data[0] if chars else None}")


def test_context_not_overloaded():
    mem = fresh_memory("p_ctx_lean")
    f = mem.add_candidate_fact("只给台词步骤用的事实")
    mem.approve_fact(f.fact_id, reviewer="human")
    # 声明式 needs 明确区分"要"和"不要"
    step_lines = NeedsStep(step_id="lines_generate", needs=["approved_facts"])
    ctx_lines = ContextAssembler(mem).assemble(step_lines, _dummy_context("p_ctx_lean"))
    no_leak = ctx_lines.keys() == ["approved_facts"] \
        and ctx_lines.item("locations") is None \
        and ctx_lines.item("characters") is None
    # 需要 locations 的 Step 才有 locations（且只列名字，不塞整部剧）
    assets = AssetRegistry.load(V2_ROOT / "assets" / "locations")
    step_scene = NeedsStep(step_id="scene_plan", needs=["approved_facts", "locations"])
    ctx_scene = ContextAssembler(mem, assets=assets).assemble(step_scene, _dummy_context("p_ctx_lean"))
    has_loc = ctx_scene.item("locations") is not None \
        and ctx_scene.item("locations").data and isinstance(ctx_scene.item("locations").data, list)
    check("T10 不需要的数据不会进入 Context", no_leak and has_loc,
          f"lines 只带 {ctx_lines.keys()}; scene 带 {ctx_scene.keys()}（含 locations）")


def test_context_traceable():
    mem = fresh_memory("p_ctx_trace")
    f = mem.add_candidate_fact("可追踪的事实")
    mem.approve_fact(f.fact_id, reviewer="human")
    step = NeedsStep(step_id="qa", needs=["approved_facts"])
    ctx = ContextAssembler(mem).assemble(step, _dummy_context("p_ctx_trace"))
    src = ctx.item("approved_facts").source
    ok = (src.source == "memory" and src.source_type == "approved_fact"
          and src.source_id == "p_ctx_trace" and src.version.startswith("rev")
          and ctx.item("approved_facts").char_count > 0)
    check("T11 Context 包含来源信息", ok,
          f"source={src.source} type={src.source_type} id={src.source_id} "
          f"version={src.version} char_count={ctx.item('approved_facts').char_count}")


def _dummy_context(project_id):
    return WorkflowContext(
        project_id=project_id, workflow_id="wf", run_id="run_dummy",
        status="running",
    )


# ---------------------------------------------------------------------------
# T12 ~ T15  Review（复用 ArtifactStore 版本机制）
# ---------------------------------------------------------------------------
def _review_fixture(project_id):
    artifacts_root = TMP_PROJECTS / project_id / "artifacts"
    store = ArtifactStore(artifacts_root)
    review_svc = ReviewService(project_id, store, projects_root=TMP_PROJECTS)
    artifact = store.save(step_id="scene", attempt=1, name="scene_1",
                          data={"content": "原始版本"}, type="json")
    return store, review_svc, artifact


def test_review_approved():
    _, svc, art = _review_fixture("p_rev_approved")
    review = svc.submit_for_review(art, reviewer="human")
    done = svc.approve(review.review_id, comment="OK")
    ok = done.status == "APPROVED" \
        and done.original_artifact["filename"] == art.filename \
        and done.edited_artifact is None
    check("T12 approved review", ok,
          f"status={done.status}; original={done.original_artifact['filename']}; edited=None")


def test_review_approved_with_edit():
    _, svc, art = _review_fixture("p_rev_edit")
    review = svc.submit_for_review(art, reviewer="human")
    done = svc.approve_with_edit(review.review_id, {"content": "修改后版本"}, comment="改一下")
    ok = (done.status == "APPROVED_WITH_EDIT"
          and done.edited_artifact is not None
          and done.edited_artifact["filename"] != art.filename
          and done.edited_artifact["filename"].endswith("_v2.json"))
    check("T13 approved_with_edit review（产生新版本）", ok,
          f"status={done.status}; 原始={art.filename}; 新产物={done.edited_artifact['filename']}")


def test_review_rejected():
    _, svc, art = _review_fixture("p_rev_reject")
    review = svc.submit_for_review(art, reviewer="human")
    done = svc.reject(review.review_id, reason="剧情不合设定")
    ok = done.status == "REJECTED" and done.reason == "剧情不合设定"
    check("T14 rejected review", ok,
          f"status={done.status}; reason={done.reason!r}")


def test_review_no_overwrite():
    store, svc, art = _review_fixture("p_rev_no_overwrite")
    before = art.read()          # 原始文件内容
    review = svc.submit_for_review(art, reviewer="human")
    done = svc.approve_with_edit(review.review_id, {"content": "编辑后"}, comment="改")
    # 原始文件仍在、内容不变
    original_still = store.load(art) == before
    edited_art = store.load(__from_dict(done.edited_artifact))
    new_content = edited_art == {"content": "编辑后"}
    ok = original_still and new_content
    check("T15 人工修改不会覆盖原始 artifact", ok,
          f"原始文件内容={before!r}（未变）; 新产物内容={edited_art!r}; "
          f"文件并存=[{art.filename}, {done.edited_artifact['filename']}]")


def __from_dict(d):
    from workflow.artifact import Artifact
    return Artifact.from_dict(d)


# ---------------------------------------------------------------------------
# T16 ~ T18  Workflow + Review 集成
# ---------------------------------------------------------------------------
def test_workflow_review_approved():
    pid = "p_flow_approved"
    artifacts_root = TMP_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_root)
    svc = ReviewService(pid, store, projects_root=TMP_PROJECTS)
    step = SimpleStep("draft_scene", data={"content": "初稿"}, artifact_name="scene_1",
                      needs_review=True)
    engine = WorkflowEngine([step], artifacts_dir=artifacts_root,
                            review_handler=make_review_handler(svc, "approved"))
    run = engine.run(project_id=pid, workflow_id="wf", inputs={})
    res = run.steps["draft_scene"]
    reviews = svc.list_reviews()
    ok = (res.status == StepStatus.APPROVED and res.is_success
          and run.status == "success" and res.review_id
          and len(reviews) == 1 and reviews[0].status == "APPROVED")
    check("T16 review 状态进入 Workflow(approved)", ok,
          f"step.status={res.status}; run.status={run.status}; review_id={res.review_id}; "
          f"review记录={reviews[0].status}")


def test_workflow_review_edit():
    pid = "p_flow_edit"
    artifacts_root = TMP_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_root)
    svc = ReviewService(pid, store, projects_root=TMP_PROJECTS)
    step = SimpleStep("draft_scene", data={"content": "初稿"}, artifact_name="scene_1",
                      needs_review=True)
    engine = WorkflowEngine([step], artifacts_dir=artifacts_root,
                            review_handler=make_review_handler(
                                svc, "approved_with_edit", edited={"content": "人工改后"},
                                comment="人工修改"))
    run = engine.run(project_id=pid, workflow_id="wf", inputs={})
    res = run.steps["draft_scene"]
    # 采用的新产物 = 编辑后的新版本（原始 scene_1.json 仍在，不被覆盖）
    ok = (res.status == StepStatus.APPROVED_WITH_EDIT and res.is_success
          and run.status == "success"
          and res.approved_artifact.filename != "scene_1.json"
          and res.approved_artifact.read() == {"content": "人工改后"})
    check("T16 review 状态进入 Workflow(approved_with_edit)", ok,
          f"step.status={res.status}; approved={res.approved_artifact.filename} "
          f"内容={res.approved_artifact.read()!r}")


def test_workflow_approved_continue():
    pid = "p_flow_continue"
    artifacts_root = TMP_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_root)
    svc = ReviewService(pid, store, projects_root=TMP_PROJECTS)
    step1 = SimpleStep("casting", data={"roles": ["A", "B"]}, artifact_name="casting",
                       needs_review=True)
    step2 = SimpleStep("scene_plan", data={"scene": 1}, artifact_name="scenes_plan")
    engine = WorkflowEngine([step1, step2], artifacts_dir=artifacts_root,
                            review_handler=make_review_handler(svc, "approved"))
    run = engine.run(project_id=pid, workflow_id="wf", inputs={})
    ok = (run.status == "success"
          and run.steps["casting"].status == StepStatus.APPROVED
          and run.steps["scene_plan"].status == StepStatus.SUCCESS
          and run.steps["scene_plan"].approved_artifact is not None)
    check("T17 approved 后 Workflow 继续到下一 Step", ok,
          f"run.status={run.status}; casting={run.steps['casting'].status}; "
          f"scene_plan={run.steps['scene_plan'].status}")


def test_workflow_rejected_stop():
    pid = "p_flow_rejected"
    artifacts_root = TMP_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_root)
    svc = ReviewService(pid, store, projects_root=TMP_PROJECTS)
    step1 = SimpleStep("casting", data={"roles": ["A"]}, artifact_name="casting",
                       needs_review=True)
    step2 = SimpleStep("scene_plan", data={"scene": 1}, artifact_name="scenes_plan")
    engine = WorkflowEngine([step1, step2], artifacts_dir=artifacts_root,
                            review_handler=make_review_handler(svc, "rejected", comment="角色不合理"))
    run = engine.run(project_id=pid, workflow_id="wf", inputs={})
    stopped = "scene_plan" not in run.steps
    ok = (run.status == "failed"
          and run.steps["casting"].status == StepStatus.REJECTED
          and not run.steps["casting"].is_success
          and stopped)
    check("T18 rejected 后 Workflow 正确停止", ok,
          f"run.status={run.status}; casting={run.steps['casting'].status}; "
          f"scene_plan 未执行={stopped}")


def test_workflow_needs_review_requires_handler():
    pid = "p_flow_no_handler"
    artifacts_root = TMP_PROJECTS / pid / "artifacts"
    step = SimpleStep("draft", data={}, needs_review=True)
    engine = WorkflowEngine([step], artifacts_dir=artifacts_root, review_handler=None)
    try:
        engine.run(project_id=pid, workflow_id="wf", inputs={})
        check("T16 needs_review 但未配置 handler", False, "居然没报错")
    except Exception as exc:
        check("T16 needs_review 但未配置 handler", "review_handler" in str(exc),
              f"明确报错：{type(exc).__name__}")


# ---------------------------------------------------------------------------
# T19 阶段 3 回归 / T20 V0.1 完整性
# ---------------------------------------------------------------------------
def _run_python(path):
    proc = subprocess.run([sys.executable, str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(V2_ROOT.parent))
    return proc.returncode, proc.stdout


def test_regression_stage3():
    rc, out = _run_python(V2_ROOT / "logs" / "stage3_workflow_test" / "run_tests.py")
    ok = rc == 0 and "38/38 通过" in out
    check("T19 阶段 3 原有 38/38 测试继续通过", ok,
          f"returncode={rc}; 输出含 '38/38 通过'={('38/38 通过' in out)}")


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
    check("T20 V0.1 仍然完全没有修改", ok,
          f"v0.1={v01[:7]}; baseline-tag={tag[:7] or '缺失'}; "
          f"diff空={diff == ''}; FilmAgent dirty={dirty_v01 or '无'}")


# ---------------------------------------------------------------------------
# R1 阶段 1 回归 / R2 阶段 2 回归（mock）
# ---------------------------------------------------------------------------
def regression_stage1():
    rc, out = _run_python(V2_ROOT / "logs" / "stage1_validation_test" / "run_tests.py")
    ok = rc == 0 and "19/19 通过" in out
    check("R1 阶段 1 回归（19/19）", ok,
          f"returncode={rc}; 输出含 '19/19 通过'={('19/19 通过' in out)}")


def regression_stage2_mock():
    """阶段 2 回归：只复验"不需要真实 API"的核心能力（配置/异常/重试/成本计算）。

    不运行阶段 2 里需要真实 API 的 T2/T3/T4/T5(真实usage)，因此 0 次真实 LLM 调用。
    """
    path = V2_ROOT / "logs" / "stage2_llm_test" / "run_tests.py"
    spec = importlib.util.spec_from_file_location("stage2_run_tests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    before = len(mod._results)
    mod.test_config()
    mod.test_error_handling()
    mod.test_retry()
    mod.test_retry_limit()
    new_results = mod._results[before:]
    ok = len(new_results) >= 7 and all(r["ok"] for r in new_results)
    names = [r["name"] for r in new_results]
    check("R2 阶段 2 回归（纯 mock，0 真实调用）", ok,
          f"复验 {len(new_results)} 项断言全 PASS: {names}")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    shutil.rmtree(TMP_PROJECTS, ignore_errors=True)   # 每次运行从干净目录开始（只清测试目录）
    log("=" * 68)
    log("V0.2 阶段 4 —— Memory + ContextAssembler + 人工审核 测试")
    log("=" * 68)

    log("--- Memory（T1~T7）---")
    test_memory_create()
    test_memory_candidate()
    test_memory_candidate_not_approved()
    test_memory_approve_read()
    test_memory_reject_not_in_context()
    test_memory_project_isolation()
    test_memory_persistence()

    log("--- ContextAssembler（T8~T11）---")
    test_step_declares_needs()
    test_context_assemble_by_needs()
    test_context_not_overloaded()
    test_context_traceable()

    log("--- Review（T12~T15）---")
    test_review_approved()
    test_review_approved_with_edit()
    test_review_rejected()
    test_review_no_overwrite()

    log("--- Workflow + Review 集成（T16~T18）---")
    test_workflow_review_approved()
    test_workflow_review_edit()
    test_workflow_approved_continue()
    test_workflow_rejected_stop()
    test_workflow_needs_review_requires_handler()

    log("--- 回归（T19/T20 + R1/R2）---")
    test_regression_stage3()
    test_v01_unchanged()
    regression_stage1()
    regression_stage2_mock()

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 68)
    log(f"测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")
    log("真实 LLM 调用 = 0 次（本阶段全部用本地 fixture / mock）")

    report = {
        "stage": "stage4_memory_context_review_test",
        "real_llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": 0.0,
        "currency": "CNY",
        "total": total,
        "passed": passed,
        "results": _results,
    }
    write_json(TEST_DIR / "stage4_report.json", report)
    (TEST_DIR / "stage4_console.txt").write_text("\n".join(_log_lines), encoding="utf-8")
    print("\n".join(_log_lines))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
