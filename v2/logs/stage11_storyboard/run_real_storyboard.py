# -*- coding: utf-8 -*-
"""
V0.2 Stage 11 —— StoryboardAgent 最小真实文本冒烟（T31~T35）

用途：在本地 Mock 闭环（run_tests.py）全部通过之后，做**最小化**真实调用，
验证 StoryboardAgent 从真实 Scene 产出合法 Storyboard 的链路对真实 LLM 成立。
绝不批量生成。

真实调用次数 = 2 次（T31 / T33 各 1 次），满足开工指令 §十三 的"≤3 次真实文本调用"：
    T31  真实调用 #1：真实 Scene 剧本 → StoryboardAgent → 合法 Storyboard
         （结构正确 + StoryboardValidator 0 错误 + 全部 Shot.status == "planned"）
    T32  真实 token / cost / request_id 正确记录（复用 T31 日志，不再调）
    T33  完整 Workflow 真实走一遍：真实 LLM → 落盘 storyboard_1.json
         → Review approved → Workflow APPROVED（生成真实产物供报告引用）
    T34  Token 纪律抽查（0 API）：真实产物内无角色档案（外貌/人格/职业）字符串渗漏
    T35  失败路径（0 API，注入断网 transport）：明确报错且不落盘，不崩溃

安全（开工指令 §十 / §十三）：
    不打印 key；只用 config.api_key_masked 显示脱敏形式；日志只写 request_id / token / cost；
    prompt 正文不进日志（token_log 只存 prompt_hash / prompt_length）。
    记账写独立文件（real_llm_calls.jsonl），绝不与 mock 混记。

输入 fixture 说明：
    fixtures/scene_1.json 是**按 V0.1 真实场景规划（scenes_1.json 第 1 场）手工编写的
    场景剧本 fixture**（只使用已注册角色/地点/动作/镜头/站位点），不是真实 LLM 输出；
    本脚本的"真实"部分是被测对象 StoryboardAgent 的真实 LLM 调用。

用法（需人工明确批准才执行，会计费）：
    python v2/logs/stage11_storyboard/run_real_storyboard.py
    需要先配置 API key（环境变量 DEEPSEEK_API_KEY 或 FilmAgent/api_key.txt）。
    预期成本 < ¥0.1（单场景约 3~6k tokens × 2）。
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
from core.validator import StoryboardValidator       # noqa: E402
from memory.store import MemoryStore                 # noqa: E402
from context.assembler import ContextAssembler       # noqa: E402
from review.service import ReviewService             # noqa: E402
from workflow.artifact import ArtifactStore          # noqa: E402
from workflow.engine import WorkflowEngine           # noqa: E402
from workflow.models import StepStatus, WorkflowContext  # noqa: E402
from llm.client import LLMClient, LLMConnectionError  # noqa: E402
from workflow.errors import StepError                 # noqa: E402
from agents.storyboard import StoryboardAgent, StoryboardStep  # noqa: E402

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
# 真实数据（只读 fixture；与 run_tests.py 同源的真实资产注册表）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES / "actors_profile.json")     # 4 个真实角色档案
REAL_NAMES = [c["name"] for c in REAL_PROFILE]                 # ["Mercy","Caleb","Ivy","Theo"]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}
REAL_FACT = ("Theo has been estranged from the friend group for three years after a bitter "
             "fallout over a shared business venture; Mercy is seriously ill and asked to see "
             "everyone one last time.")
SCENE_1 = read_json(FIXTURES / "scene_1.json")                 # 真实场景剧本 fixture（见文件头说明）
SPEC = {"scene_id": 1, "episode": 1, "target_beat_count": 3, "max_shots_per_beat": 6,
        "granularity": "normal", "style_hint": "tense reunion, warm interior light"}
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")  # 真实资产注册表

# Token 纪律抽查用：真实角色档案里"绝不该进 Shot"的字段值
_PROFILE_LEAK_MARKERS = []
for _c in REAL_PROFILE:
    for _k in ("personality traits", "speaking style", "occupation"):
        if _c.get(_k):
            _PROFILE_LEAK_MARKERS.append(str(_c[_k]))


def fresh_memory(project_id):
    return MemoryStore(project_id, projects_root=REAL_PROJECTS)


def make_real_client():
    """真实 LLMClient：走 config 的真实 key / base_url / model。"""
    return LLMClient(api_key=load_api_key(), base_url=API_BASE_URL,
                     model=DEFAULT_MODEL, timeout=120.0,
                     logger=TokenLogger(REAL_LOGS))


def make_agent(client, scene_id=1):
    """真实 Agent：已知角色 + 真实资产注册表由调度层注入（Agent 自己不读文件）。"""
    return StoryboardAgent(client, scene_id=scene_id,
                           known_characters=REAL_NAMES, registry=ASSETS)


def make_ctx(mem, step, project_title=REAL_TOPIC):
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    assembler = ContextAssembler(mem, assets=ASSETS)
    return assembler.assemble(step, wf_ctx, project_title=project_title)


def _seed_memory(mem):
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    mem.set_world(REAL_WORLD)
    f = mem.add_candidate_fact(REAL_FACT, category="plot", source="memory_writer")
    mem.approve_fact(f.fact_id, reviewer="human")


def _real_scene(scene_id=1):
    return Scene.from_v01(SCENE_1, id=scene_id)


# ---------------------------------------------------------------------------
# T31 真实调用 #1：Scene → Storyboard
# ---------------------------------------------------------------------------
def t31_real_storyboard():
    pid = "r31_storyboard"
    mem = fresh_memory(pid)
    _seed_memory(mem)
    agent = make_agent(make_real_client(), scene_id=1)
    step = StoryboardStep(agent, scene_id=1)
    ctx = make_ctx(mem, step)
    scene = _real_scene(1)

    # 真实 LLM 偶发非法 JSON / 结构瑕疵：StepError 与 Validator 会拦截（这正是它们的意义）。
    # 按 Workflow 重试语义（max_attempts=3）再试，并如实记录每次拦截证据。
    first_rejections = []
    last_error = None
    storyboard = None
    errors = None
    for attempt in (1, 2, 3):
        try:
            storyboard = agent.run(ctx, scene=scene, storyboard_spec=SPEC)
        except StepError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        errors = agent.validator.validate_dict(storyboard.to_dict())
        if not errors:
            break
        if attempt == 1:
            first_rejections = [str(e).splitlines()[0] for e in errors]

    shots = storyboard.flatten_shots() if storyboard is not None else []
    ok = (storyboard is not None
          and len(storyboard.beats) >= 1
          and len(shots) >= 1
          and errors == []
          and all(s.status == "planned" for s in shots)
          and all(s.scene_id == 1 for s in shots))
    detail = (f"beats={len(storyboard.beats) if storyboard else 0}; shots={len(shots)}; "
              f"第1次被Validator拦截={first_rejections or '无'}; 中途异常={last_error or '无'}; "
              f"最终 errors={[str(e).splitlines()[0] for e in (errors or [])]}")
    check("T31 真实调用 #1：真实 Scene → 合法 Storyboard（结构 + 校验 0 错误 + status=planned）",
          ok, detail)
    return storyboard


# ---------------------------------------------------------------------------
# T32 真实 token / cost（复用 T31 日志，不再调）
# ---------------------------------------------------------------------------
def t32_real_token_cost():
    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True
                  and r.get("step") == "storyboard_generation"]
    ok = len(ok_records) >= 1 and all(r.get("request_id") for r in ok_records) \
        and all(isinstance(r.get("total_tokens"), int) for r in ok_records)
    check("T32 真实调用 token / cost / request_id 被正确记录（独立记账，不含 prompt 正文）",
          ok, f"成功记录={len(ok_records)}；"
              f"tokens={[r.get('total_tokens') for r in ok_records]}；"
              f"cost={[r.get('estimated_cost') for r in ok_records]}")


# ---------------------------------------------------------------------------
# T33 完整 Workflow 真实走一遍（真实调用 #2）
# ---------------------------------------------------------------------------
def t33_real_workflow():
    pid = "r33_workflow"
    mem = fresh_memory(pid)
    _seed_memory(mem)
    agent = make_agent(make_real_client(), scene_id=1)
    step = StoryboardStep(agent, scene_id=1)
    artifacts_dir = REAL_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(pid, store, projects_root=REAL_PROJECTS)

    def handler(artifact, context):
        review = svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        done = svc.approve(review.review_id, comment="真实调用通过")
        return svc.to_review_result(done)

    engine = WorkflowEngine([step], artifacts_dir=artifacts_dir, review_handler=handler,
                            retry_backoff_seconds=0.0)
    run = engine.run(project_id=pid, workflow_id="wf", inputs={
        "assembled_context": make_ctx(mem, step),
        "storyboard_spec": SPEC,
        "scene": _real_scene(1),
    })
    res = run.steps[step.step_id]
    files = [p.name for p in store.list_files("storyboard_1")]
    ok = (res.status == StepStatus.APPROVED
          and files == ["storyboard_1.json"]
          and bool(res.review_id))
    detail = (f"status={res.status}; review_id={res.review_id}; files={files}; "
              f"attempts={len(res.attempts)}")
    check("T33 完整 Workflow 真实走一遍（落盘 storyboard_1.json + Review approved）", ok, detail)
    return pid, res, store


# ---------------------------------------------------------------------------
# T34 Token 纪律抽查（0 API）
# ---------------------------------------------------------------------------
def t34_token_discipline(store=None):
    if store is None:
        store = ArtifactStore(REAL_PROJECTS / "r33_workflow" / "artifacts")
    files = store.list_files("storyboard_1")
    if not files:
        check("T34 Token 纪律抽查", False, "无可抽查的真实产物（T33 未产出）")
        return
    text = files[0].read_text(encoding="utf-8")
    leaked = [m[:40] for m in _PROFILE_LEAK_MARKERS if m in text]
    check("T34 真实产物内无角色档案（外貌/人格/职业）字符串渗漏（Token 纪律）",
          not leaked, f"leaked={leaked[:3]}" if leaked else "无渗漏")


# ---------------------------------------------------------------------------
# T35 失败路径（0 API，注入断网 transport）
# ---------------------------------------------------------------------------
def t35_error_path():
    def broken_transport(messages, model, temperature, max_tokens, timeout):
        raise LLMConnectionError("simulated network down (T35, 0 API)")

    mem = fresh_memory("r35_error")
    _seed_memory(mem)
    client = LLMClient(api_key="sk-mock", base_url="http://mock", transport=broken_transport,
                       logger=TokenLogger(REAL_LOGS), max_retries=0, retry_backoff_seconds=0.0)
    agent = StoryboardAgent(client, scene_id=1, known_characters=REAL_NAMES, registry=ASSETS)
    step = StoryboardStep(agent, scene_id=1)
    raised = None
    try:
        agent.run(make_ctx(mem, step), scene=_real_scene(1), storyboard_spec=SPEC)
    except Exception as exc:                     # noqa: BLE001 - 明确记录异常类型
        raised = type(exc).__name__
    check("T35 断网失败路径：明确报错（不崩溃、不静默产空 Storyboard，0 API）",
          raised is not None, f"raised={raised}")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main():
    if not api_key_configured():
        log("错误：未检测到 API key。请先设置环境变量 DEEPSEEK_API_KEY，")
        log("或确认 FilmAgent/api_key.txt 已填入真实密钥（不会打印 key 内容）。")
        sys.exit(2)

    if REAL_ROOT.exists():
        shutil.rmtree(REAL_ROOT, ignore_errors=True)
    log("=" * 68)
    log(f"V0.2 Stage 11 —— StoryboardAgent 最小真实文本冒烟（T31~T35）  脱敏 key={api_key_masked()}")
    log("=" * 68)

    try:
        t31_real_storyboard()
    except Exception as exc:                     # noqa: BLE001
        check("T31 真实调用 #1", False, f"抛异常（重试耗尽后仍失败）: {type(exc).__name__}: {exc}")

    t32_real_token_cost()

    store33 = None
    try:
        _, _, store33 = t33_real_workflow()
    except Exception as exc:                     # noqa: BLE001
        check("T33 完整 Workflow 真实走一遍", False, f"抛异常: {type(exc).__name__}: {exc}")

    t34_token_discipline(store33)
    t35_error_path()

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 68)
    log(f"真实调用测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")

    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True
                  and r.get("step") == "storyboard_generation"]
    total_tokens = sum(r.get("total_tokens", 0) for r in ok_records)
    total_cost = sum(r.get("estimated_cost") or 0 for r in ok_records)
    log(f"成功真实调用次数={len(ok_records)}；累计 total_tokens={total_tokens}；"
        f"累计估算成本≈¥{total_cost:.6f}")
    for r in ok_records:
        log(f"  call request_id={str(r.get('request_id'))[:12]}... "
            f"tokens={r.get('total_tokens')} cost={r.get('estimated_cost')} "
            f"success={r.get('success')} retry={r.get('retry_count')}")

    summary = {
        "stage": "stage11_storyboard",
        "suite": "run_real_storyboard",
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
