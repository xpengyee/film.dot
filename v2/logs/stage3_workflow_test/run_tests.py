# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Workflow 引擎测试（纯本地，真实 LLM 调用 0 次）

覆盖（对应开工指令 §十七 15 项 + §十八 专门轨迹测试 + §十九 异常恢复）：
    T1   单 Step 成功
    T2   多 Step 顺序执行
    T3   Step 状态正确
    T4   Step 输出正确传递
    T5   Step 失败能够被捕获
    T6   不可重试错误不会无限重试
    T7   可重试错误能够重试
    T8   retry 次数正确
    T9   第一次失败 artifact 保留
    T10  第二次失败 artifact 保留
    T11  第三次成功 artifact 保留
    T12  最终 artifact 指向正确
    T13  Workflow Run 可以恢复/读取历史
    T14  错误信息完整
    T15  V0.1 未发生任何修改（git diff -- FilmAgent/ 为空）
    T16  专门的重试轨迹测试：attempt1 FAIL -> attempt2 FAIL -> attempt3 SUCCESS，
         磁盘 scenes_5.json / scenes_5_v2.json / scenes_5_v3.json 三份全保留，
         内容分别 = attempt-1 / attempt-2 / attempt-3（不能全部变成 attempt-3）
    T17  异常恢复：StepA SUCCESS 的 artifact 不被删除；StepB 重跑不覆盖 StepA
    T18  WorkflowRun -> core.WorkflowState 桥接正确（attempts / approved_output）
    T19  resume 模式下已成功 Step 标记 SKIPPED 且不重跑

全部 Step 都是纯本地 FakeStep / MockStep，不调用任何真实 LLM。
"""
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]        # v2/
TEST_DIR = Path(__file__).resolve().parent           # 本目录（stage3_workflow_test）
ARTIFACT_ROOT = TEST_DIR / "project_001"             # 测试产物目录（模拟项目）
RUN_LOG_DIR = TEST_DIR / "runs"                      # run 历史记录目录
REPO_ROOT = V2_ROOT.parent                           # 仓库根目录（v2/ 的父目录，动态推导，不硬编码机器路径）

sys.path.insert(0, str(V2_ROOT))
from core.io import read_json, write_json              # noqa: E402
from core.models import WorkflowState                  # noqa: E402
from workflow import (                                 # noqa: E402
    WorkflowEngine, Step, StepOutput, StepStatus,
    WorkflowRun, StepResult, AttemptResult,
    StepError, ArtifactError, WorkflowError,
    ArtifactStore, Artifact, to_core_workflow_state,
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


# ===========================================================================
# 本地 FakeStep 集合
# ===========================================================================
class SuccessStep(Step):
    """总是成功，产出 {"step": step_id, "value": value}。"""

    def __init__(self, step_id, name=None, value=None):
        super().__init__(step_id=step_id, name=name or step_id)
        self.value = value if value is not None else f"{step_id}-value"

    def run(self, context):
        return StepOutput(data={"step": self.step_id, "value": self.value},
                          artifact_name=self.step_id)

    def validate(self, data, context):
        return []


class DownstreamStep(Step):
    """读取上游输出，验证"输出正确传递"。"""

    def __init__(self, step_id, upstream_id):
        super().__init__(step_id=step_id)
        self.upstream_id = upstream_id

    def run(self, context):
        upstream = context.get_output(self.upstream_id)
        if upstream is None:
            raise StepError(f"上游 {self.upstream_id} 没有输出")
        return StepOutput(data={"from_upstream": upstream}, artifact_name=self.step_id)

    def validate(self, data, context):
        return []


class FailOnRunStep(Step):
    """run() 直接抛 StepError（异常型失败，默认可重试）。"""

    def run(self, context):
        raise StepError("故意失败：run 阶段抛出")


class FailNonRetryableStep(Step):
    """run() 抛 ArtifactError（retryable=False）—— 验证不可重试不无限重试。"""

    def run(self, context):
        raise ArtifactError("写入失败（不可重试）")


class FailOnceStep(Step):
    """前 1 次 validate 失败，之后成功。产出 dict，type=json。"""

    def __init__(self, step_id, fail_until=1):
        super().__init__(step_id=step_id)
        self.calls = 0
        self.fail_until = fail_until

    def run(self, context):
        self.calls += 1
        return StepOutput(data={"attempt": self.calls}, artifact_name=self.step_id)

    def validate(self, data, context):
        if self.calls <= self.fail_until:
            return [f"故意失败第 {self.calls} 次"]
        return []


class FailFailPassStep(Step):
    """专门用于轨迹测试：前 2 次 validate 失败，第 3 次成功。
    产出 {"attempt": "attempt-N"}，type=json（文件名 scenes_5.json 系列）。
    """

    def __init__(self, step_id="scenes_5"):
        super().__init__(step_id=step_id)
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return StepOutput(data={"attempt": f"attempt-{self.calls}"},
                          artifact_name=self.step_id)

    def validate(self, data, context):
        if self.calls <= 2:
            return [f"故意失败第 {self.calls} 次"]
        return []


class AlwaysFailValidateStep(Step):
    """总是 validate 失败（用于异常恢复测试）。产出 {"attempt": N}。"""

    def __init__(self, step_id):
        super().__init__(step_id=step_id)
        self.calls = 0

    def run(self, context):
        self.calls += 1
        return StepOutput(data={"attempt": self.calls}, artifact_name=self.step_id)

    def validate(self, data, context):
        return ["总是失败"]


def make_engine(steps, *, backoff=0.0):
    return WorkflowEngine(steps, artifacts_dir=ARTIFACT_ROOT,
                          run_log_dir=RUN_LOG_DIR, retry_backoff_seconds=backoff)


def clean_test_dirs():
    for d in (ARTIFACT_ROOT, RUN_LOG_DIR):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)


# ===========================================================================
# T1 / T3  单 Step 成功，状态正确
# ===========================================================================
def test_single_step_success():
    clean_test_dirs()
    engine = make_engine([SuccessStep("stepA")])
    run = engine.run(project_id="p1", workflow_id="w1")
    res = run.step_result("stepA")
    ok_run = run.status == "success"
    ok_status = res is not None and res.status == StepStatus.SUCCESS and res.is_success
    ok_artifact = res.approved_artifact is not None and res.approved_artifact.filename == "stepA.json"
    check("T1 单 Step 成功(run.status=success)", ok_run, f"run.status={run.status}")
    check("T3 Step 状态正确(SUCCESS)", ok_status, f"stepA.status={res.status if res else None}")
    check("T1 产出已落盘(approved_artifact)", ok_artifact,
          f"filename={res.approved_artifact.filename if res and res.approved_artifact else None}")
    # 读回磁盘内容确认
    if res and res.approved_artifact:
        back = res.approved_artifact.read()
        check("T1 落盘内容可读回", back == {"step": "stepA", "value": "stepA-value"},
              f"read={back}")


# ===========================================================================
# T2 / T3 / T4  多 Step 顺序执行，输出正确传递
# ===========================================================================
def test_multi_step_order_and_passing():
    clean_test_dirs()
    engine = make_engine([SuccessStep("stepA"), DownstreamStep("stepB", "stepA"),
                          SuccessStep("stepC")])
    run = engine.run(project_id="p1", workflow_id="w1")
    ok_order = list(run.steps.keys()) == ["stepA", "stepB", "stepC"]
    ok_all_success = all(run.steps[s].status == StepStatus.SUCCESS for s in run.steps)
    b = run.step_result("stepB")
    b_data = b.approved_artifact.read() if b and b.approved_artifact else None
    ok_passing = (b_data is not None
                  and b_data.get("from_upstream") == {"step": "stepA", "value": "stepA-value"})
    check("T2 多 Step 顺序执行", ok_order and ok_all_success,
          f"order={list(run.steps.keys())}; 全部={ok_all_success}")
    check("T4 Step 输出正确传递(B 读到了 A 的产出)", ok_passing,
          f"B.read={b_data}")
    check("T3 各 Step 状态正确", all(run.steps[s].status == "SUCCESS" for s in run.steps),
          f"{[run.steps[s].status for s in run.steps]}")


# ===========================================================================
# T5 / T14  Step 失败能够被捕获，错误信息完整
# ===========================================================================
def test_failure_captured():
    clean_test_dirs()
    engine = make_engine([SuccessStep("stepA"), FailOnRunStep("stepB")])
    run = engine.run(project_id="p1", workflow_id="w1")  # 不应抛异常，而是返回 failed run
    a = run.step_result("stepA")
    b = run.step_result("stepB")
    ok_captured = run.status == "failed" and b is not None and b.status == StepStatus.FAILED
    # 失败即停：stepC 本不该存在，这里只有 A、B
    ok_stop = set(run.steps.keys()) == {"stepA", "stepB"}
    att = b.attempt(1) if b else None
    ok_error_full = (att is not None and att.error is not None
                     and "故意失败" in att.error)
    check("T5 Step 失败能够被捕获(不崩)", ok_captured,
          f"run.status={run.status}; stepB.status={b.status if b else None}")
    check("T5 失败即停(下游不再执行)", ok_stop, f"steps={set(run.steps.keys())}")
    check("T14 错误信息完整", ok_error_full,
          f"attempt1.error={att.error if att else None}")


# ===========================================================================
# T6  不可重试错误不会无限重试
# ===========================================================================
def test_non_retryable_no_infinite():
    clean_test_dirs()
    engine = make_engine([FailNonRetryableStep("stepX")])
    run = engine.run(project_id="p1", workflow_id="w1", max_attempts=3)
    res = run.step_result("stepX")
    n_attempts = len(res.attempts) if res else 0
    att = res.attempt(1) if res else None
    ok_once = n_attempts == 1
    ok_error = att is not None and att.error is not None and "ArtifactError" in att.error
    check("T6 不可重试只尝试 1 次(不无限重试)", ok_once,
          f"max_attempts=3 但实际尝试 {n_attempts} 次")
    check("T6 失败原因被记录", ok_error, f"attempt1.error={att.error if att else None}")


# ===========================================================================
# T7 / T8  可重试错误能够重试，retry 次数正确
# ===========================================================================
def test_retryable_then_success():
    clean_test_dirs()
    engine = make_engine([FailOnceStep("stepY", fail_until=1)])
    run = engine.run(project_id="p1", workflow_id="w1", max_attempts=3)
    res = run.step_result("stepY")
    ok_status = res is not None and res.status == StepStatus.SUCCESS
    ok_attempts = res is not None and len(res.attempts) == 2
    ok_first_failed = res is not None and res.attempt(1).status == StepStatus.FAILED
    ok_second_success = res is not None and res.attempt(2).status == StepStatus.SUCCESS
    ok_approved = res is not None and res.approved_artifact is not None \
        and res.approved_artifact.filename == "stepY_v2.json"
    check("T7 可重试错误重试后成功", ok_status,
          f"stepY.status={res.status if res else None}")
    check("T8 retry 次数正确(共 2 次尝试)", ok_attempts,
          f"attempts={[a.status for a in res.attempts] if res else []}")
    check("T8 第1次失败/第2次成功", ok_first_failed and ok_second_success,
          f"[{res.attempt(1).status}, {res.attempt(2).status}]" if res else "")
    check("T8 最终采用第2版(approved=stepY_v2.json)", ok_approved,
          f"approved={res.approved_artifact.filename if res and res.approved_artifact else None}")
    # 历史文件都保留
    store = ArtifactStore(ARTIFACT_ROOT)
    files = [p.name for p in store.list_files("stepY")]
    check("T9 第一次失败 artifact 保留(stepY.json 存在)", "stepY.json" in files, f"files={files}")
    check("T10 第二次成功 artifact 保留(stepY_v2.json 存在)", "stepY_v2.json" in files)


# ===========================================================================
# T16（核心） 专门重试轨迹测试：attempt1 FAIL -> attempt2 FAIL -> attempt3 SUCCESS
#            磁盘三份文件内容分别 = attempt-1 / attempt-2 / attempt-3
# ===========================================================================
def test_trace_three_attempts():
    clean_test_dirs()
    engine = make_engine([FailFailPassStep("scenes_5")])
    run = engine.run(project_id="p1", workflow_id="w1", max_attempts=3)
    res = run.step_result("scenes_5")
    ok_success = run.status == "success" and res.status == StepStatus.SUCCESS
    ok_3_attempts = len(res.attempts) == 3
    ok_seq = ([a.status for a in res.attempts] ==
              [StepStatus.FAILED, StepStatus.FAILED, StepStatus.SUCCESS])
    # 磁盘上三个文件全存在，内容各自保留原值
    store = ArtifactStore(ARTIFACT_ROOT)
    files = {p.name for p in store.list_files("scenes_5")}
    v1 = ARTIFACT_ROOT / "scenes_5.json"
    v2 = ARTIFACT_ROOT / "scenes_5_v2.json"
    v3 = ARTIFACT_ROOT / "scenes_5_v3.json"
    ok_files = files == {"scenes_5.json", "scenes_5_v2.json", "scenes_5_v3.json"}
    ok_contents = (
        v1.exists() and read_json(v1) == {"attempt": "attempt-1"}
        and v2.exists() and read_json(v2) == {"attempt": "attempt-2"}
        and v3.exists() and read_json(v3) == {"attempt": "attempt-3"}
    )
    ok_approved = res.approved_artifact is not None and res.approved_artifact.filename == "scenes_5_v3.json"
    ok_final = res.approved_artifact is not None and res.approved_artifact.read() == {"attempt": "attempt-3"}

    check("T16 三次尝试状态序列[FAIL,FAIL,SUCCESS]", ok_3_attempts and ok_seq,
          f"statuses={[a.status for a in res.attempts]}")
    check("T16 最终 run 成功且批准 v3", ok_success and ok_approved,
          f"approved={res.approved_artifact.filename if res.approved_artifact else None}")
    check("T11 磁盘三份文件全部保留", ok_files, f"files={sorted(files)}")
    check("T9/T10/T11 三份内容分别=attempt-1/2/3(不互相覆盖)", ok_contents,
          "v1=attempt-1, v2=attempt-2, v3=attempt-3")
    check("T12 最终 artifact 指向正确(v3)", ok_final,
          f"approved.read()={res.approved_artifact.read() if res.approved_artifact else None}")


# ===========================================================================
# T13 / T19  Run 历史可恢复 / 读取；resume 跳过已成功 Step（SKIPPED）
# ===========================================================================
def test_run_history_and_resume():
    clean_test_dirs()
    # 第一次 run：stepA 成功，stepB 永远失败
    engine = make_engine([SuccessStep("stepA"), AlwaysFailValidateStep("stepB")])
    run1 = engine.run(project_id="p1", workflow_id="w1", max_attempts=3)
    check("T13 第一次 run 失败(failed)", run1.status == "failed", f"run1.status={run1.status}")

    # 从 run 日志目录读取历史
    run_log = RUN_LOG_DIR / f"{run1.run_id}.json"
    ok_saved = run_log.exists()
    loaded = WorkflowRun.from_dict(read_json(run_log)) if ok_saved else None
    ok_restore = (loaded is not None
                  and loaded.run_id == run1.run_id
                  and loaded.status == "failed"
                  and loaded.step_result("stepA").status == StepStatus.SUCCESS
                  and len(loaded.steps) == 2)
    check("T13 Workflow Run 可以恢复/读取历史", ok_saved and ok_restore,
          f"saved={ok_saved}; run_id 一致={loaded is not None and loaded.run_id == run1.run_id}")

    # 第二次 run（resume）：stepA 已成功 -> SKIPPED；stepB 重新执行
    engine2 = make_engine([SuccessStep("stepA"), AlwaysFailValidateStep("stepB")])
    run2 = engine2.run(project_id="p1", workflow_id="w1", max_attempts=3, resume_from=loaded)
    a2 = run2.step_result("stepA")
    b2 = run2.step_result("stepB")
    ok_skipped = a2 is not None and a2.status == StepStatus.SKIPPED
    ok_b_reran = b2 is not None and b2.status == StepStatus.FAILED and len(b2.attempts) == 3
    check("T19 resume 后已成功 Step 标记 SKIPPED(不重跑)", ok_skipped,
          f"stepA2.status={a2.status if a2 else None}")
    check("T19 失败 Step 被重新执行", ok_b_reran,
          f"stepB2.status={b2.status if b2 else None}; attempts={len(b2.attempts) if b2 else 0}")
    # stepA 的产物在第二次 run 后依然存在且没被重写
    a1_path = ARTIFACT_ROOT / "stepA.json"
    ok_a_intact = a1_path.exists() and read_json(a1_path) == {"step": "stepA", "value": "stepA-value"}
    check("T13 历史产物未被删除/覆盖", ok_a_intact, f"stepA.json exists={a1_path.exists()}")


# ===========================================================================
# T17  异常恢复：StepA SUCCESS 的 artifact 不被删除；StepB 重跑不覆盖 StepA
# ===========================================================================
def test_exception_recovery():
    clean_test_dirs()
    engine = make_engine([SuccessStep("stepA"), AlwaysFailValidateStep("stepB")])
    run1 = engine.run(project_id="p1", workflow_id="w1", max_attempts=1)  # B 只试 1 次
    a_path = ARTIFACT_ROOT / "stepA.json"
    b_v1 = ARTIFACT_ROOT / "stepB.json"
    ok_a_exists1 = a_path.exists()
    ok_b_v1_exists = b_v1.exists()
    a_content_before = read_json(a_path) if ok_a_exists1 else None

    # 第二次 run（resume）：StepA 已成功 -> SKIPPED；StepB 重跑（仍失败）
    run2 = engine.run(project_id="p1", workflow_id="w1", max_attempts=1, resume_from=run1)
    a2 = run2.step_result("stepA")
    b_v2 = ARTIFACT_ROOT / "stepB_v2.json"   # 重跑时版本递增，不覆盖 stepB.json
    ok_a2_skipped = a2 is not None and a2.status == StepStatus.SKIPPED
    ok_a_untouched = (a_path.exists()
                      and read_json(a_path) == a_content_before
                      and read_json(a_path) == {"step": "stepA", "value": "stepA-value"})
    # 同一个 engine 实例跨 run 复用，AlwaysFailValidateStep.calls 累加：
    # 第一次 run 产出 attempt=1（写 stepB.json），第二次 run 产出 attempt=2（写 stepB_v2.json）。
    # 关键是 stepB.json（旧版）仍在、stepB_v2.json（新版）是重跑新生成的内容。
    ok_b_not_overwritten = b_v1.exists() and b_v2.exists() \
        and read_json(b_v1) == {"attempt": 1} and read_json(b_v2) == {"attempt": 2}
    check("T17 StepA 的 artifact 不被删除", ok_a_exists1 and ok_a_untouched,
          f"stepA.json 内容一致={ok_a_untouched}")
    check("T17 StepB 重跑不覆盖旧版本(stepB.json 与 stepB_v2.json 并存)",
          ok_b_not_overwritten,
          f"v1={read_json(b_v1) if b_v1.exists() else None}; v2={read_json(b_v2) if b_v2.exists() else None}")
    check("T17 StepA 在重跑中为 SKIPPED(未重新生成)", ok_a2_skipped,
          f"stepA2.status={a2.status if a2 else None}")


# ===========================================================================
# T18  WorkflowRun -> core.WorkflowState 桥接
# ===========================================================================
def test_core_state_bridge():
    clean_test_dirs()
    engine = make_engine([FailFailPassStep("scenes_5")])
    run = engine.run(project_id="p1", workflow_id="w1", max_attempts=3)
    core_state = to_core_workflow_state(run)
    ok_type = isinstance(core_state, WorkflowState)
    s = core_state.step("scenes_5")
    ok_step = s is not None
    ok_attempts = s is not None and s.attempts == ["scenes_5.json", "scenes_5_v2.json", "scenes_5_v3.json"]
    ok_approved = s is not None and s.approved_output == "scenes_5_v3.json"
    ok_status = s is not None and s.status == "passed"
    check("T18 桥接到 core.WorkflowState", ok_type and ok_step,
          f"type={type(core_state).__name__}; step={'scenes_5' if ok_step else None}")
    check("T18 全部尝试文件名保留", ok_attempts, f"attempts={s.attempts if ok_step else None}")
    check("T18 approved_output 指向最终版本", ok_approved and ok_status,
          f"approved={s.approved_output if ok_step else None}; status={s.status if ok_step else None}")


# ===========================================================================
# T15  V0.1 未发生任何修改
# ===========================================================================
def test_v01_untouched():
    diff = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--", "FilmAgent/"],
        capture_output=True, text=True)
    rev = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "v0.1^{commit}"],
        capture_output=True, text=True)
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--short"],
        capture_output=True, text=True)
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True)
    tag = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "v0.2-stage7^{commit}"],
        capture_output=True, text=True)
    ok_diff = diff.returncode == 0 and diff.stdout.strip() == ""
    ok_rev = rev.stdout.strip() == "344c0c6b7c48497be934957c29fcae029cca6752"
    # 阶段 7 基线封存后 HEAD 已不是 v0.1（v0.2-stage7 = 300aae44 基线 commit）。
    # "V0.1 冻结"的真实不变量 = v0.1 commit 未动（ok_rev）+ FilmAgent/ 无改动
    # （ok_diff/ok_clean）；本检查改为确认 v0.2-stage7 基线里程碑已落库。
    ok_head = tag.returncode == 0 and bool(tag.stdout.strip())
    dirty_v01 = [ln for ln in status.stdout.splitlines() if ln.startswith(("M ", "D ", "R ", "A ")) and "FilmAgent/" in ln]
    ok_clean = not dirty_v01
    check("T15 V0.1 未发生任何修改(git diff 为空)", ok_diff,
          f"git diff -- FilmAgent/ 空={ok_diff}")
    check("T15 v0.1 commit 未变", ok_rev, f"v0.1={rev.stdout.strip()}")
    check("T15 FilmAgent/ 无 M/D/R 记录", ok_clean, f"dirty={dirty_v01 or '无'}")
    check("T15 v0.2-stage7 基线 tag 已建立(HEAD 演进自此基线)", ok_head,
          f"tag={tag.stdout.strip()}")


# ===========================================================================
# 运行
# ===========================================================================
def main():
    log("=" * 64)
    log("V0.2 阶段 3 —— Workflow 引擎测试（纯本地，0 次真实 LLM 调用）")
    log("=" * 64)
    log(f"artifact 目录 = {ARTIFACT_ROOT}")
    log(f"run 日志目录  = {RUN_LOG_DIR}")
    log("")

    try:
        test_single_step_success()
        log("")
        test_multi_step_order_and_passing()
        log("")
        test_failure_captured()
        log("")
        test_non_retryable_no_infinite()
        log("")
        test_retryable_then_success()
        log("")
        test_trace_three_attempts()
        log("")
        test_run_history_and_resume()
        log("")
        test_exception_recovery()
        log("")
        test_core_state_bridge()
        log("")
        test_v01_untouched()
    except Exception:
        log("")
        log("!! 测试代码自身抛异常，未跑完全部用例：")
        log(traceback.format_exc())

    log("")
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 64)
    log(f"测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")
    log("真实 LLM 调用 = 0 次（本阶段全部用 FakeStep / MockStep）")

    report = {
        "stage": "stage3_workflow_test",
        "total": total,
        "passed": passed,
        "real_llm_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost": None,
        "currency": None,
        "results": _results,
    }
    write_json(TEST_DIR / "stage3_report.json", report)
    (TEST_DIR / "stage3_console.txt").write_text("\n".join(_log_lines), encoding="utf-8")
    print("\n".join(_log_lines))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
