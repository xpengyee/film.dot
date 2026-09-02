# -*- coding: utf-8 -*-
"""
V0.2 阶段 7 —— 最小真实 DeepSeek 调用（T21~T25）

用途：在本地 Mock 闭环（run_tests.py）全部通过之后，做**最小化**真实调用，
验证 V0.2 Scene / Script Generation 整条链路对真实 LLM 输入是否成立。
绝不完整生成剧本。

真实调用次数 = 3 次（T21 / T22 / T25 各 1 次），满足开工指令 §十六 D 的 3~8 次：
    T21  真实调用（第 1 场）：完整 scene_spec（真实已确认事实 + 3 个真实角色
         + Apartment living room）-> 解析成 Scene、通过 Validator（0 错误）
    T22  真实调用（第 2 场）：另一个场景规范（不同地点 / 不同角色子集）
         -> 同样解析成 Scene、通过 Validator（同一项目多场连续生成可行）
    T23  真实 token / cost / request_id 正确记录（复用 T21/T22 日志，不再调）
    T24  真实环境的失败路径：注入"断网"transport，验证能记录错误且不崩溃（mock，不烧钱）
    T25  完整 Workflow 真实走一遍：真实 LLM -> 落盘 scene_1.json -> Review approved
         -> Workflow APPROVED（生成真实产物供报告引用）

安全（开工指令 §十八）：
    不打印 key；只用 config.api_key_masked 显示脱敏形式；日志只写 request_id / token / cost；
    prompt 正文不进日志（token_log 只存 prompt_hash / prompt_length）。

用法：
    python v2/logs/stage7_scene_generation/run_real_scene_generation.py
    需要先配置 API key（环境变量 DEEPSEEK_API_KEY 或 FilmAgent/api_key.txt）。
"""
import json
import shutil
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent
FIXTURES = TEST_DIR / "fixtures"
REAL_ROOT = TEST_DIR / "real_tmp"                  # 真实调用专用临时目录（不污染 run_tests 的 tmp）
REAL_PROJECTS = REAL_ROOT / "projects"
REAL_LOGS = REAL_ROOT / "logs"

sys.path.insert(0, str(V2_ROOT))
from config import (                                # noqa: E402
    load_api_key, api_key_configured, api_key_masked,
    API_BASE_URL, DEFAULT_MODEL,
)
from core.io import read_json, write_json            # noqa: E402
from core.assets import AssetRegistry                # noqa: E402
from core.models import Scene                        # noqa: E402
from core.token_log import TokenLogger               # noqa: E402
from memory.store import MemoryStore                 # noqa: E402
from context.assembler import ContextAssembler       # noqa: E402
from review.service import ReviewService             # noqa: E402
from workflow.artifact import ArtifactStore          # noqa: E402
from workflow.engine import WorkflowEngine           # noqa: E402
from workflow.models import StepStatus, WorkflowContext  # noqa: E402
from llm.client import LLMClient, LLMConnectionError # noqa: E402
from workflow.errors import StepError                 # noqa: E402
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
# 真实 V0.1 数据（只读 fixture，与 run_tests.py 同源）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES / "actors_profile.json")   # 4 个真实角色
REAL_NAMES = [c["name"] for c in REAL_PROFILE]               # ["Mercy","Caleb","Ivy","Theo"]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}
REAL_SPEC = read_json(FIXTURES / "scene_spec.json")           # 第 1 场真实场景规范
REAL_APPROVED_FACT = ("Theo has been estranged from the friend group for three years "
                      "after a bitter fallout over a shared business venture.")
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")  # 真实资产注册表

# 第 2 场场景规范：不同地点 / 不同角色子集（验证同项目多场连续生成）
SPEC2 = {
    "scene_id": 2,
    "episode": 1,
    "location": "Apartment kitchen",
    "characters": ["Mercy", "Caleb"],
    "time": "Morning after the reunion",
    "objective": ("Mercy and Caleb catch up in the kitchen. Mercy explains her illness "
                  "calmly, and Caleb offers to help her with daily errands."),
    "constraints": ("Use only the confirmed characters, this exact location, and the "
                    "registered actions/shots. Keep it to a warm, low-key dialogue."),
}


def fresh_memory(project_id):
    return MemoryStore(project_id, projects_root=REAL_PROJECTS)


def make_real_client():
    """真实 LLMClient：走 config 的真实 key / base_url / model。"""
    return LLMClient(api_key=load_api_key(), base_url=API_BASE_URL,
                     model=DEFAULT_MODEL, timeout=120.0,
                     logger=TokenLogger(REAL_LOGS))


def make_agent(client, scene_id=1):
    """真实 Agent：已知角色 + 真实资产注册表由测试注入（Agent 自己不读文件）。"""
    return SceneGenerationAgent(client, scene_id=scene_id,
                                known_characters=REAL_NAMES, assets=ASSETS)


def make_ctx(mem, step, scene_spec, project_title=REAL_TOPIC):
    """调度层：ContextAssembler 按 needs 组装上下文（注入 assets），返回 AssembledContext。"""
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    assembler = ContextAssembler(mem, assets=ASSETS)
    return assembler.assemble(step, wf_ctx, project_title=project_title)


def make_review_handler(review_svc, comment="真实调用通过"):
    def handler(artifact, context):
        review = review_svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        done = review_svc.approve(review.review_id, comment=comment)
        return review_svc.to_review_result(done)
    return handler


def _seed_memory(mem):
    """给 Memory 灌真实角色 / 世界观 / 一条人工确认的事实。"""
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    mem.set_world(REAL_WORLD)
    f = mem.add_candidate_fact(REAL_APPROVED_FACT, category="plot", source="memory_writer")
    mem.approve_fact(f.fact_id, reviewer="human")


# ---------------------------------------------------------------------------
# T21 真实调用（第 1 场，完整场景规范）
# ---------------------------------------------------------------------------
def t21_real_scene_one():
    pid = "r21_scene_one"
    mem = fresh_memory(pid)
    _seed_memory(mem)
    agent = make_agent(make_real_client(), scene_id=1)
    ctx = make_ctx(mem, SceneGenerationStep(agent, scene_id=1), REAL_SPEC)
    # 真实 LLM 输出偶尔含瑕疵（站位冲突 / 非法 JSON 控制字符），Validator 与
    # StepError 会拦截——这正是它们存在的意义。被拦截时按业务规则（Workflow
    # 引擎的重试语义，max_attempts=3）再试，并如实记录每次拦截证据。
    first_rejections = []
    last_error = None
    scene = None
    errors = None
    for attempt in (1, 2, 3):
        try:
            scene = agent.run(ctx, scene_spec=REAL_SPEC)
        except StepError as exc:                # 非法 JSON 等 -> 引擎语义：可重试
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        errors = agent.validator.validate(scene)
        if not errors:
            break
        if attempt == 1:
            first_rejections = [e.code for e in errors]
    ok = (isinstance(scene, Scene)
          and len(scene.lines) >= 1
          and set(scene.info.who) <= set(REAL_NAMES)
          and errors == [])
    detail = (f"who={scene.info.who} @ {scene.info.where}; lines={len(scene.lines)}; "
              f"第1次被Validator拦截={first_rejections or '无'}; 中途异常={last_error or '无'}; "
              f"最终 validator errors={[str(e).splitlines()[0] for e in errors]}")
    check("T21 真实调用（第 1 场）：解析成 Scene 且通过 Validator（含重试兜底）", ok, detail)
    return scene


# ---------------------------------------------------------------------------
# T22 真实调用（第 2 场，不同地点/角色子集）
# ---------------------------------------------------------------------------
def t22_real_scene_two():
    pid = "r22_scene_two"
    mem = fresh_memory(pid)
    _seed_memory(mem)
    agent = make_agent(make_real_client(), scene_id=2)
    ctx = make_ctx(mem, SceneGenerationStep(agent, scene_id=2), SPEC2)
    # 与 T21 相同的重试兜底：真实 LLM 长输出偶发非法 JSON 控制字符，StepError -> 重试
    last_error = None
    scene = None
    errors = None
    for attempt in (1, 2, 3):
        try:
            scene = agent.run(ctx, scene_spec=SPEC2)
        except StepError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        errors = agent.validator.validate(scene)
        if not errors:
            break
    ok = (isinstance(scene, Scene)
          and len(scene.lines) >= 1
          and set(scene.info.who) <= set(REAL_NAMES)
          and errors == [])
    detail = (f"who={scene.info.who} @ {scene.info.where}; lines={len(scene.lines)}; "
              f"中途异常={last_error or '无'}; "
              f"最终 validator errors={[str(e).splitlines()[0] for e in errors]}")
    check("T22 真实调用（第 2 场）：另一场景规范同样生成合法 Scene（含重试兜底）", ok, detail)
    return scene


# ---------------------------------------------------------------------------
# T23 真实 token / cost / request_id 记录（复用上面的日志）
# ---------------------------------------------------------------------------
def t23_real_token_cost():
    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True
                  and r.get("step") == "scene_generation"]
    last = ok_records[-1] if ok_records else {}
    ok = (len(ok_records) >= 2                          # T21 + T22 至少两条成功记录
          and bool(last.get("request_id"))
          and last.get("total_tokens", 0) > 0
          and isinstance(last.get("estimated_cost"), (int, float))
          and last.get("estimated_cost", 0) > 0
          and last.get("agent") == "scene_generation"
          and last.get("retry_count", 0) == 0)          # 真实首次即成功
    detail = (f"成功记录数={len(ok_records)}; 最近一条 total_tokens={last.get('total_tokens')}, "
              f"cost={last.get('estimated_cost')}, request_id={str(last.get('request_id'))[:12]}..., "
              f"retry_count={last.get('retry_count')}")
    check("T23 真实 token / cost / request_id 正确记录", ok, detail)
    return ok_records


# ---------------------------------------------------------------------------
# T24 真实环境的失败路径：断网也能记录错误、不崩溃（mock transport，不烧钱）
# ---------------------------------------------------------------------------
def failing_transport(messages, model, temperature, max_tokens, timeout):
    raise LLMConnectionError("simulated network failure (T24)")


def t24_real_error_path():
    logger = TokenLogger(REAL_LOGS)
    client = LLMClient(api_key="sk-mock", base_url="http://unreachable.example",
                       transport=failing_transport, logger=logger,
                       max_retries=1, retry_backoff_seconds=0.0)
    try:
        client.chat([{"role": "user", "content": "hi"}],
                    step="scene_generation", agent="scene_generation")
        raised = False
    except LLMConnectionError:
        raised = True
    records = TokenLogger(REAL_LOGS).read_all()
    failed_recs = [r for r in records if r.get("success") is False]
    last = failed_recs[-1] if failed_recs else {}
    ok = (raised
          and last.get("success") is False
          and "LLMConnectionError" in (last.get("error") or "")
          and last.get("retry_count") == 1)            # 1 次初始 + 1 次重试都失败
    detail = (f"抛异常={raised}; 失败记录数={len(failed_recs)}; "
              f"retry_count={last.get('retry_count')}; error={last.get('error')}")
    check("T24 真实环境失败路径：能记录错误且不崩溃", ok, detail)


# ---------------------------------------------------------------------------
# T25 完整 Workflow 真实走一遍（落盘 + Review）
# ---------------------------------------------------------------------------
def t25_real_workflow():
    pid = "r25_full_workflow"
    mem = fresh_memory(pid)
    _seed_memory(mem)
    agent = make_agent(make_real_client(), scene_id=1)
    step = SceneGenerationStep(agent, scene_id=1)
    artifacts_dir = REAL_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(pid, store, projects_root=REAL_PROJECTS)
    engine = WorkflowEngine([step], artifacts_dir=artifacts_dir,
                            review_handler=make_review_handler(svc, "真实 Workflow 通过"),
                            retry_backoff_seconds=0.0)
    ctx = make_ctx(mem, step, REAL_SPEC)
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": ctx, "scene_spec": REAL_SPEC})
    res = run.steps[step.step_id]
    files = sorted(p.name for p in store.list_files("scene_1"))
    ok = (run.status == "success" and res.status == StepStatus.APPROVED
          and files == ["scene_1.json"] and res.approved_artifact is not None)
    detail = (f"run={run.status}; scene={res.status}; files={files}; "
              f"approved={res.approved_artifact.filename if res.approved_artifact else '?'}; "
              f"review_id={res.review_id}")
    check("T25 完整 Workflow 真实走一遍（落盘 + Review approved）", ok, detail)
    return pid, res, store


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    if not api_key_configured():
        log("错误：未检测到 API key。请先设置环境变量 DEEPSEEK_API_KEY，")
        log("或确认 FilmAgent/api_key.txt 已填入真实密钥（不会打印 key 内容）。")
        sys.exit(2)

    shutil.rmtree(REAL_ROOT, ignore_errors=True)
    log("=" * 68)
    log(f"V0.2 阶段 7 —— 最小真实 DeepSeek 调用（T21~T25）  脱敏 key={api_key_masked()}")
    log("=" * 68)

    try:
        s21 = t21_real_scene_one()
    except Exception as exc:
        check("T21 真实调用（第 1 场）", False, f"抛异常（重试耗尽后仍失败）: {type(exc).__name__}: {exc}")
        s21 = None

    try:
        s22 = t22_real_scene_two()
    except Exception as exc:
        check("T22 真实调用（第 2 场）", False, f"抛异常（重试耗尽后仍失败）: {type(exc).__name__}: {exc}")
        s22 = None

    t23_real_token_cost()
    t24_real_error_path()

    try:
        pid25, res25, store25 = t25_real_workflow()
    except Exception as exc:
        check("T25 完整 Workflow 真实走一遍", False, f"抛异常: {type(exc).__name__}: {exc}")
        pid25, res25, store25 = None, None, None

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 68)
    log(f"真实调用测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")

    # 汇总真实调用成本（只统计 scene_generation 成功记录）
    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True
                  and r.get("step") == "scene_generation"]
    total_tokens = sum(r.get("total_tokens", 0) for r in ok_records)
    total_cost = sum(r.get("estimated_cost") or 0 for r in ok_records)
    log(f"成功真实调用次数={len(ok_records)}；累计 total_tokens={total_tokens}；"
        f"累计估算成本≈¥{total_cost:.6f}")
    for r in ok_records:
        log(f"  call request_id={str(r.get('request_id'))[:12]}... "
            f"tokens={r.get('total_tokens')} cost={r.get('estimated_cost')} "
            f"success={r.get('success')} retry={r.get('retry_count')}")

    summary = {
        "stage": "stage7_scene_generation",
        "suite": "run_real_scene_generation",
        "real_llm_calls": len(ok_records),
        "per_call": [{k: r.get(k) for k in ("request_id", "step", "agent", "model",
                                            "total_tokens", "estimated_cost",
                                            "success", "retry_count", "duration_ms")}
                     for r in ok_records],
        "total_tokens": total_tokens,
        "estimated_cost": round(total_cost, 8),
        "results": _results,
        "masked_api_key": api_key_masked(),
    }
    write_json(TEST_DIR / "real_summary.json", summary)
    with (TEST_DIR / "real_console.txt").open("w", encoding="utf-8") as f:
        f.write("\n".join(_log_lines) + "\n")

    print("\n".join(_log_lines))
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
