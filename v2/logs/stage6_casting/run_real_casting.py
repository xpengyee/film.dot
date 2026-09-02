# -*- coding: utf-8 -*-
"""
V0.2 阶段 6 —— 最小真实 DeepSeek 调用（T21~T25）

用途：在本地 Mock 闭环（run_tests.py）全部通过之后，做**最小化**真实调用，
验证 V0.2 Casting 整条链路对真实 LLM 输入是否成立。绝不完整生成剧本。

真实调用次数 = 3 次（T21 / T22 / T25 各 1 次），尽量少烧钱：
    T21  真实调用（第一集，无已知角色）-> 全是新角色、档案字段齐全、通过 Validator
    T22  真实调用（第二集，已有 4 个真实角色）-> 老角色复用（existing）、不重复建档
    T23  真实 token / cost / request_id 正确记录（复用 T21/T22 的日志，不再调）
    T24  真实环境下的失败路径：注入"断网"transport，验证能记录错误且不崩溃（mock，不烧钱）
    T25  完整 Workflow 真实走一遍：真实 LLM -> 落盘 casting.json -> Review approved
         -> Workflow APPROVED（生成真实产物供报告引用）

安全（开工指令 §十八）：
    不打印 key；只用 config.mask_api_key 显示脱敏形式；日志只写 request_id / token / cost。

用法：
    python v2/logs/stage6_casting/run_real_casting.py
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
from core.token_log import TokenLogger               # noqa: E402
from memory.store import MemoryStore                 # noqa: E402
from context.assembler import ContextAssembler       # noqa: E402
from review.service import ReviewService             # noqa: E402
from workflow.artifact import ArtifactStore          # noqa: E402
from workflow.engine import WorkflowEngine           # noqa: E402
from workflow.models import StepStatus, WorkflowContext  # noqa: E402
from llm.client import LLMClient, LLMConnectionError # noqa: E402
from agents.casting import CastingAgent, CastingStep # noqa: E402

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
# 真实 V0.1 数据（只读 fixture）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES / "actors_profile.json")   # 4 个真实角色
REAL_NAMES = [c["name"] for c in REAL_PROFILE]               # ["Mercy","Caleb","Ivy","Theo"]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}


def fresh_memory(project_id):
    return MemoryStore(project_id, projects_root=REAL_PROJECTS)


def make_real_client():
    """真实 LLMClient：走 config 的真实 key / base_url / model。"""
    return LLMClient(api_key=load_api_key(), base_url=API_BASE_URL,
                     model=DEFAULT_MODEL, timeout=120.0,
                     logger=TokenLogger(REAL_LOGS))


def make_ctx(mem, step, project_title=REAL_TOPIC):
    """调度层：ContextAssembler 组装上下文。"""
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="probe", inputs={})
    assembler = ContextAssembler(mem)
    assembled = assembler.assemble(step, wf_ctx, project_title=project_title)
    return assembled


def make_review_handler(review_svc, decision="approved", comment="真实调用通过"):
    def handler(artifact, context):
        review = review_svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        done = review_svc.approve(review.review_id, comment=comment)
        return review_svc.to_review_result(done)
    return handler


# ---------------------------------------------------------------------------
# T21 真实最小调用（第一集，无已知角色）
# ---------------------------------------------------------------------------
def t21_real_first_episode():
    pid = "r21_first_episode"
    mem = fresh_memory(pid)
    agent = CastingAgent(make_real_client(), known_characters=[])
    step = CastingStep(agent)
    ctx = make_ctx(mem, step)
    result = agent.run(ctx)
    errors = agent.validator.validate(result)
    ok = (len(result.entries) > 0
          and all(e.status == "new" for e in result.entries)
          and all(e.character is not None for e in result.entries)
          and errors == [])
    detail = (f"episode={result.episode}; entries={[(e.name, e.status) for e in result.entries]}; "
              f"validator errors={[str(e) for e in errors]}")
    check("T21 真实调用（第一集）：全为新角色且通过 Validator", ok, detail)
    return result


# ---------------------------------------------------------------------------
# T22 真实调用（第二集，已有 4 个真实角色）
# ---------------------------------------------------------------------------
def t22_real_reuse():
    pid = "r22_reuse"
    mem = fresh_memory(pid)
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    mem.set_world(REAL_WORLD)
    agent = CastingAgent(make_real_client(), known_characters=REAL_NAMES)
    step = CastingStep(agent)
    ctx = make_ctx(mem, step)
    result = agent.run(ctx)
    # 跨集决策验证：已知角色出现时必须 existing；new 必须不在 known 且带完整档案
    known = set(REAL_NAMES)
    reused_ok = all(e.status == "existing" for e in result.entries if e.name in known)
    new_ok = all(e.status == "new" and e.character is not None and e.name not in known
                 for e in result.entries if e.name not in known)
    errors = agent.validator.validate(result)
    ok = reused_ok and new_ok and errors == []
    detail = (f"entries={[(e.name, e.status) for e in result.entries]}; "
              f"已知角色均复用={reused_ok}; new 均带档案={new_ok}; "
              f"validator errors={[str(e) for e in errors]}")
    check("T22 真实调用（第二集）：老角色复用、不重复建档", ok, detail)
    return result


# ---------------------------------------------------------------------------
# T23 真实 token / cost / request_id 记录（复用上面的日志）
# ---------------------------------------------------------------------------
def t23_real_token_cost():
    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True]
    last = ok_records[-1] if ok_records else {}
    ok = (len(ok_records) >= 2                          # T21 + T22 至少两条成功记录
          and bool(last.get("request_id"))
          and last.get("total_tokens", 0) > 0
          and isinstance(last.get("estimated_cost"), (int, float))
          and last.get("estimated_cost", 0) > 0
          and last.get("step") == "casting"
          and last.get("agent") == "casting")
    detail = (f"成功记录数={len(ok_records)}; 最近一条 total_tokens={last.get('total_tokens')}, "
              f"cost={last.get('estimated_cost')}, request_id={str(last.get('request_id'))[:12]}..., "
              f"step/agent={last.get('step')}/{last.get('agent')}")
    check("T23 真实 token / cost / request_id 正确记录", ok, detail)
    return last


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
        client.chat([{"role": "user", "content": "hi"}], step="casting", agent="casting")
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
    mem.set_characters([dict(c) for c in REAL_PROFILE])
    mem.set_world(REAL_WORLD)
    agent = CastingAgent(make_real_client(), known_characters=REAL_NAMES)
    step = CastingStep(agent)
    artifacts_dir = REAL_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(pid, store, projects_root=REAL_PROJECTS)
    engine = WorkflowEngine([step], artifacts_dir=artifacts_dir,
                            review_handler=make_review_handler(svc, "approved"),
                            retry_backoff_seconds=0.0)
    wf_ctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="r", inputs={})
    assembler = ContextAssembler(mem)
    assembled = assembler.assemble(step, wf_ctx, project_title=REAL_TOPIC)
    wf_ctx.inputs["assembled_context"] = assembled
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": assembled})
    res = run.steps["casting"]
    files = sorted(p.name for p in store.list_files("casting"))
    ok = (run.status == "success" and res.status == StepStatus.APPROVED
          and files == ["casting.json"] and res.approved_artifact is not None)
    detail = (f"run={run.status}; casting={res.status}; files={files}; "
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
    log(f"V0.2 阶段 6 —— 最小真实 DeepSeek 调用（T21~T25）  脱敏 key={api_key_masked()}")
    log("=" * 68)

    try:
        r21 = t21_real_first_episode()
    except Exception as exc:
        log(f"T21 抛异常（真实调用失败，如实记录）：{type(exc).__name__}: {exc}")
        r21 = None

    try:
        r22 = t22_real_reuse()
    except Exception as exc:
        log(f"T22 抛异常（真实调用失败，如实记录）：{type(exc).__name__}: {exc}")
        r22 = None

    t23_real_token_cost()
    t24_real_error_path()

    try:
        pid25, res25, store25 = t25_real_workflow()
    except Exception as exc:
        log(f"T25 抛异常（真实调用失败，如实记录）：{type(exc).__name__}: {exc}")
        pid25, res25, store25 = None, None, None

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 68)
    log(f"真实调用测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")

    # 汇总真实调用成本
    records = TokenLogger(REAL_LOGS).read_all()
    ok_records = [r for r in records if r.get("success") is True]
    total_tokens = sum(r.get("total_tokens", 0) for r in ok_records)
    total_cost = sum(r.get("estimated_cost") or 0 for r in ok_records)
    log(f"成功真实调用次数={len(ok_records)}；累计 total_tokens={total_tokens}；"
        f"累计估算成本≈¥{total_cost:.6f}")

    summary = {
        "stage": "stage6_casting",
        "suite": "run_real_casting",
        "real_llm_calls": len(ok_records),
        "success": ok_records,
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
