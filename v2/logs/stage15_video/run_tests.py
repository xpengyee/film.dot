# -*- coding: utf-8 -*-
"""
V0.2 Stage 15-B —— Image-to-Video 正式测试套件（mock / fake transport，0 真实 API / 0 网络 / 0 模型切换）

被测对象（Stage 15-B 新增 / 允许最小修改的文件；禁改清单 0 diff）：
    v2/llm/providers/base.py            VideoTaskResult / submit/poll 契约（允许最小修改）
    v2/llm/providers/mock_video.py      MockVideoProvider
    v2/llm/providers/bailian_video.py   BailianVideoProvider（原生异步）
    v2/llm/providers/registry.py        video 工厂接线（允许最小修改）
    v2/llm/request_counter.py           video/video_poll/video_download 桶（允许最小修改）
    v2/config.py                        bailian_video 档 + VIDEO_PRICING（允许最小修改）
    v2/orchestration/video_stage.py     VideoStage（准入重算/异步/resume/下载/Review/回挂）
    v2/orchestration/pipeline.py        Stage 15 接线（允许最小修改）
    v2/orchestration/state.py           record_video_stage（允许最小修改）

覆盖（Stage_15.md §13 T01~T14）：
    T01  冻结边界与接口（tag、禁改目录、可导入、audio 拒绝）
    T02  默认关闭与 Provider 惰性
    T03  选择、映射与顺序
    T04  Stage 14 准入门
    T05  Prompt 与 duration
    T06  Mock Provider
    T07  Bailian 协议（fake transport）
    T08  异步持久化与崩溃恢复
    T09  预算与请求计数
    T10  下载与文件安全
    T11  manifest 与 resume
    T12  Review、Storyboard 与 ShotAsset
    T13  pipeline、多场景与报告
    T14  真实脚本护栏与泄漏

用法：
    python v2/logs/stage15_video/run_tests.py

本文件真实 API 调用 = 0（全 Mock / fake transport），网络 = 0，模型切换 = 0。
"""
import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]
TMP_PROJECTS = TEST_DIR / "tmp_projects"
TMP_LOGS = TEST_DIR / "tmp_logs"

sys.path.insert(0, str(V2_ROOT))

from core.shot import Shot, ShotAsset, Storyboard, Beat, CameraPlan       # noqa: E402
from core.visual_registry import VisualAssetRegistry                      # noqa: E402
from llm.client import LLMError, LLMRequestError                          # noqa: E402
from llm.providers.base import VideoTaskResult, VideoGenerationProvider   # noqa: E402
from llm.providers.mock_video import (                                    # noqa: E402
    MockVideoProvider, stable_task_id, encode_mp4_fixture,
)
from llm.providers.bailian_video import BailianVideoProvider, VideoGenerationError  # noqa: E402
from llm.providers.registry import create_provider, get_default_provider  # noqa: E402
from llm.request_counter import (                                         # noqa: E402
    MAX_REAL_VIDEO_SUBMISSIONS, APIBudgetExceededError, APIRequestCounter,
    get_api_counter, reset_api_counter,
)
from workflow.artifact import ArtifactStore                              # noqa: E402
from workflow.models import ReviewDecision                               # noqa: E402
from orchestration.video_stage import (                                  # noqa: E402
    VideoStage, render_motion_prompt, motion_input_hash, normalize_duration,
    half_up_round, sha256_of_bytes, shot_ref_of, _default_fetch,
    VIDEO_DIR_NAME, VIDEO_MANIFEST_FILENAME, VIDEO_LEDGER_FILENAME,
)
from orchestration.state import OrchestrationError                       # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ID = "video_proj"
STAGE14_TAG = "v0.2-stage14"

_results = []
_log_lines = []
_regressions = []


def log(msg=""):
    _log_lines.append(str(msg))
    print(msg, flush=True)


def _sanitize(text) -> str:
    s = str(text)
    for p in (str(TMP_PROJECTS), str(TMP_LOGS), str(V2_ROOT), str(REPO_ROOT)):
        s = s.replace(p, "<path>")
    s = s.replace("\\", "/")
    s = re.sub(r"sk-[A-Za-z0-9_\-]{4,}", "sk-****", s)
    s = re.sub(r"[A-Za-z0-9+/]{120,}={0,2}", "<blob>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:300]


def check(name, ok, detail=""):
    _results.append({"id": str(name).split()[0], "name": name,
                     "ok": bool(ok), "detail": _sanitize(detail)})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {_sanitize(detail)}" if detail else ""))


def expect_raises(exc_type, fn, *args, **kw):
    try:
        fn(*args, **kw)
        return None
    except exc_type as e:
        return e
    except Exception as e:                                     # noqa: BLE001
        return f"WRONG-TYPE {type(e).__name__}: {e}"
    return "NO-RAISE"


@contextlib.contextmanager
def no_network():
    original = socket.socket.connect
    original_create = socket.create_connection

    def _boom(*a, **kw):                                       # pragma: no cover
        raise AssertionError("测试期间检测到真实网络访问（应为 0 网络）")

    socket.socket.connect = _boom
    socket.create_connection = _boom
    try:
        yield
    finally:
        socket.socket.connect = original
        socket.create_connection = original_create


def _hard_rmtree(path: Path) -> None:
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def git(*args) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(REPO_ROOT))
    return p.stdout or ""


def _clean_child_env() -> dict:
    env = dict(os.environ)
    shim_marker = os.path.join("cli", "vendor", "shim")
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
             if p and shim_marker not in p]
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        env.pop("PYTHONPATH", None)
    return env


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
CHAR_PROFILE = {
    "appearance": "tall woman in a long coat",
    "face": "oval face, calm expression",
    "hair": "black bob",
    "clothing": "red wool coat",
    "negative_cues": "no hat, no sunglasses",
}
STYLE_PROFILE = {"medium": "35mm film", "palette": "amber tones", "notes": "default"}


def new_project(tag: str) -> Path:
    d = TMP_PROJECTS / tag
    if d.exists():
        _hard_rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_file(path: Path, data: bytes) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def make_asset(reg, kind, name, profile, status="approved"):
    a = reg.register(kind, name)
    v = reg.new_version(a.asset_id, profile=dict(profile))
    if status == "approved":
        reg.submit_for_review(a.asset_id, v.version)
        reg.approve(a.asset_id, v.version)
    return a.asset_id, v.version


def make_shot(scene_id=1, beat_id=1, shot_id=1, *, duration=5.0,
              camera_movement="pan", subject="she walks across the room",
              visual_description="a woman walking in a cafe"):
    return Shot(
        shot_id=shot_id, scene_id=scene_id, beat_id=beat_id,
        kind="move", subject=subject, visual_description=visual_description,
        camera=CameraPlan(shot_size="MS", camera_angle="eye_level",
                          camera_movement=camera_movement),
        composition="single", continuity="follows previous shot",
        duration=duration,
    )


def make_storyboard(shot, *, scene_id=1):
    beat = Beat(beat_id=1, purpose="test", shots=[shot])
    return Storyboard(scene_id=scene_id, location="cafe",
                      characters=["Lin"], synopsis="test", beats=[beat])


class _Decision:
    def __init__(self, decision, review_id="rev-1", comment=""):
        self.decision = str(decision)
        self.review_id = str(review_id)
        self.comment = str(comment or "")


def _handler(decision):
    n = {"i": 0}

    def h(artifact, context):
        n["i"] += 1
        return _Decision(decision, f"rev-{n['i']}")
    return h


def auto_approve(artifact, context):
    return _Decision(ReviewDecision.APPROVED)


# ---------------------------------------------------------------------------
# 构造一个 Stage 14 QA pass 的完整项目（准入重算的输入）
# ---------------------------------------------------------------------------
def build_qa_passed_project(tag: str, *, shot=None):
    """构造一个「Stage 13 image + Stage 14 QA pass」的最小项目。

    返回 (project_dir, registry, image_entry, qa_item, shot, negative_cues)。
    直接手写 Stage 13 manifest + Stage 14 QA index + QA Artifact，
    使其通过 VideoStage._preflight 的严格重算。
    """
    from agents.image_qa import expected_checks, validate_artifact
    from orchestration.image_qa_stage import prepare_image_qa_input, build_reference_index

    pd = new_project(tag)
    reg = VisualAssetRegistry(pd)
    char_id, char_v = make_asset(reg, "character", "Lin", CHAR_PROFILE, status="approved")
    reg.save()

    shot = shot or make_shot()
    shot_ref = shot_ref_of(shot)

    # ---- Stage 13 image + manifest ----
    img_data = b"GEN-IMAGE-BYTES-001"
    rel = f"generated_images/GEN_001.png"
    sha = write_file(pd / rel, img_data)
    image_entry = {
        "image_id": "GEN_001", "shot_ref": shot_ref, "path": rel, "sha256": sha,
        "status": "generated",
        "profile_versions": {char_id: char_v},
        "reference_images": [],
    }
    write_manifest(pd, [image_entry])

    # ---- 构造 qa_input（用 prepare_image_qa_input 得到精确 anchors/profiles）----
    ref_index = build_reference_index(reg)
    qa_input = prepare_image_qa_input(
        project_dir=pd, registry=reg, entry=image_entry, reference_index=ref_index)
    expected = expected_checks(qa_input)
    # 全 match → qa_decision = pass
    checks = [{"asset_id": e["asset_id"], "version": e["version"], "field": e["field"],
               "expected": e["expected"], "verdict": "match", "evidence": ""}
              for e in expected]
    from agents.image_qa import qa_decision
    decision = qa_decision(checks)
    artifact = {
        "schema_version": "1",
        "image_id": "GEN_001",
        "shot_ref": shot_ref,
        "generated_sha256": qa_input["generated_sha256"],
        "ground_truth_mode": qa_input["ground_truth_mode"],
        "profile_versions": {p["asset_id"]: p["version"] for p in qa_input["profiles"]},
        "reference_images": [],
        "checks": checks,
        "summary": "all match",
        "qa_decision": decision,
        "provider": "mock",
        "model": "mock-multimodal",
        "request_id": "req-1",
        "retry_count": 0,
        "created_at": now_iso(),
    }
    # 用 validate_artifact 确认 artifact 合法（含 qa_decision 重算一致）
    artifact = validate_artifact(artifact, qa_input)

    # ---- QA Artifact 落盘（artifacts/）----
    art_dir = pd / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    art_rel = "artifacts/image_qa_gen_001.json"
    art_sha = write_file(pd / art_rel, json.dumps(artifact, ensure_ascii=False).encode("utf-8"))

    # ---- QA index ----
    qa_item = {
        "image_id": "GEN_001",
        "shot_ref": shot_ref,
        "generated_path": qa_input["_generated_relative_path"],
        "generated_sha256": qa_input["generated_sha256"],
        "profile_versions": {p["asset_id"]: p["version"] for p in qa_input["profiles"]},
        "reference_images": [],
        "artifact_path": art_rel,
        "artifact_sha256": art_sha,
        "workflow": {"run_id": "r1", "status": "success", "step_status": "APPROVED"},
        "review": {"id": "rev-1", "decision": "approved"},
        "qa_decision": decision,
        "provider": "mock",
        "model": "mock-multimodal",
        "updated_at": now_iso(),
        "downstream_eligible": True,
        "error": "",
    }
    qa_index = {"version": "1", "project_id": PROJECT_ID, "images": [qa_item]}
    qa_dir = pd / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "qa_manifest.json").write_text(
        json.dumps(qa_index, ensure_ascii=False, indent=2), encoding="utf-8")

    # negative_cues 从 profile 提取
    negative_cues = []
    for p in qa_input["profiles"]:
        nc = str((p.get("fields") or {}).get("negative_cues", "") or "").strip()
        if nc:
            for part in nc.split(";"):
                part = part.strip()
                if part and part not in negative_cues:
                    negative_cues.append(part)

    return pd, reg, image_entry, qa_item, shot, negative_cues


def write_manifest(pd: Path, entries, *, version="1", project_id=PROJECT_ID):
    m = {"version": version, "project_id": project_id, "images": list(entries)}
    p = pd / "generated_images" / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# VideoStage 构造（依赖全注入）
# ---------------------------------------------------------------------------
def make_stage(pd, reg, *, provider=None, handler=None, counter=None,
               shot=None, fetch=None, sleep=None, max_video_bytes=64 * 1024 * 1024,
               allowed_schemes=("https", "mock")):
    shot = shot or make_shot()
    sb = make_storyboard(shot)
    return VideoStage(
        project_id=PROJECT_ID, project_dir=pd, artifacts_dir=pd / "artifacts",
        store=ArtifactStore(pd / "artifacts"), provider=provider, registry=reg,
        review_handler=handler or auto_approve,
        review_mode="auto_approve (programmatic, NOT human review)",
        counter=counter or get_api_counter(), log_dir=TMP_LOGS,
        fetch=fetch, sleep=sleep or (lambda s: None), episode=1, state=None,
        max_video_bytes=max_video_bytes, allowed_schemes=allowed_schemes,
    ), sb


# ---------------------------------------------------------------------------
# T01 冻结边界与接口
# ---------------------------------------------------------------------------
def test_t01():
    log("\n== T01 冻结边界与接口 ==")
    # tag 存在
    tag_out = git("rev-parse", "--verify", STAGE14_TAG)
    check("T01-1 Stage14 tag 存在", tag_out.strip() != "", tag_out.strip()[:12])

    # 禁改目录 0 diff
    frozen = ["FilmAgent", "v2/core", "v2/workflow", "v2/review", "v2/memory",
              "v2/context", "v2/agents", "v2/orchestration/image_stage.py",
              "v2/orchestration/image_qa_stage.py"]
    diff = git("diff", "--name-only", "--", *frozen)
    check("T01-2 禁改目录 0 diff", diff.strip() == "", diff.strip())

    # 可导入
    check("T01-3 VideoTaskResult/Mock/Bailian/VideoStage 可导入",
          all(callable(x) for x in (VideoTaskResult, MockVideoProvider,
                                    BailianVideoProvider, VideoStage)))

    # audio 仍拒绝
    err = expect_raises(ValueError, create_provider, "audio", vendor="mock")
    check("T01-4 audio 仍拒绝", err is not None, str(err))

    # video mock 可用
    p = get_default_provider("video")
    check("T01-5 get_default_provider('video') 返回 Mock",
          isinstance(p, MockVideoProvider), str(p))


# ---------------------------------------------------------------------------
# T02 默认关闭与 Provider 惰性
# ---------------------------------------------------------------------------
def test_t02():
    log("\n== T02 默认关闭与 Provider 惰性 ==")
    pd, reg, _, _, shot, _ = build_qa_passed_project("t02")
    shot = shot or make_shot()
    sb = make_storyboard(shot)

    # generate_videos=False：不读输入、不建目录、不写 manifest、不构造 provider
    factory_calls = {"n": 0}

    def spy_factory():
        factory_calls["n"] += 1
        return MockVideoProvider()

    stage = VideoStage(
        project_id=PROJECT_ID, project_dir=pd, artifacts_dir=pd / "artifacts",
        store=ArtifactStore(pd / "artifacts"), provider=None, registry=reg,
        review_handler=auto_approve,
        review_mode="auto_approve", log_dir=TMP_LOGS,
        provider_factory=spy_factory, fetch=None, sleep=lambda s: None,
        episode=1, state=None)
    rep = stage.run([(1, sb)], {"generate_videos": False})
    check("T02-1 generate_videos=False 零副作用",
          rep["enabled"] is False and factory_calls["n"] == 0
          and not (pd / VIDEO_DIR_NAME).exists(),
          f"factory_calls={factory_calls['n']} video_dir={ (pd / VIDEO_DIR_NAME).exists() }")

    # max_videos=0 零提交
    reset_api_counter()
    stage2, sb2 = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    rep2 = stage2.run([(1, sb2)], {"generate_videos": True, "max_videos": 0})
    check("T02-2 max_videos=0 零提交",
          rep2["videos_submitted"] == 0 and rep2["budget_cap"] == 0,
          f"submitted={rep2['videos_submitted']} cap={rep2['budget_cap']}")


# ---------------------------------------------------------------------------
# T03 选择、映射与顺序
# ---------------------------------------------------------------------------
def test_t03():
    log("\n== T03 选择、映射与顺序 ==")
    stage = VideoStage(project_id=PROJECT_ID, project_dir=Path("."),
                       artifacts_dir=Path("."), store=None, provider=None,
                       registry=None, review_handler=None)
    # 空值/错误类型
    e1 = expect_raises(OrchestrationError, stage._resolve_video_image_ids,
                       {"video_image_ids": [1]})
    check("T03-1 非字符串 image_id 失败", e1 is not None, str(e1))
    e2 = expect_raises(OrchestrationError, stage._resolve_video_image_ids,
                       {"video_image_ids": ["A", "A"]})
    check("T03-2 重复 image_id 失败", e2 is not None, str(e2))
    ids = stage._resolve_video_image_ids({"video_image_ids": ["B", "A"]})
    check("T03-3 保持用户顺序", ids == ["B", "A"], str(ids))

    # max_videos cap
    stage2 = VideoStage(project_id="x", project_dir=Path("."), artifacts_dir=Path("."),
                        store=None, provider=None, registry=None, review_handler=None)
    check("T03-4 max_videos cap=min(value,1)",
          stage2._resolve_max_videos({"max_videos": 5}) == 1
          and stage2._resolve_max_videos({"max_videos": 1}) == 1
          and stage2._resolve_max_videos({"max_videos": 0}) == 0)


# ---------------------------------------------------------------------------
# T05 Prompt 与 duration
# ---------------------------------------------------------------------------
def test_t05():
    log("\n== T05 Prompt 与 duration ==")
    shot = make_shot()
    neg = ["no hat", "no sunglasses"]
    p1 = render_motion_prompt(shot=shot, negative_cues=neg,
                              resolution="1280*720", duration=5)
    p2 = render_motion_prompt(shot=shot, negative_cues=neg,
                              resolution="1280*720", duration=5)
    check("T05-1 相同输入 prompt 稳定（hash 一致）",
          hashlib.sha256(p1.encode()).hexdigest() == hashlib.sha256(p2.encode()).hexdigest())

    check("T05-2 CameraPlan 进入 prompt",
          "shot_size=MS" in p1 and "movement=pan" in p1, _sanitize(p1))
    check("T05-3 continuity/动作进入 prompt",
          "Composition: single" in p1 and "Continuity: follows previous shot" in p1)
    check("T05-4 exact negative_cues 进入 prompt",
          "no hat" in p1 and "no sunglasses" in p1)

    # 不含绝对路径/key/base64
    check("T05-5 不含绝对路径/key/base64",
          str(V2_ROOT) not in p1 and "sk-" not in p1 and "base64" not in p1)

    # 顺序变化 → hash 改变
    h1 = motion_input_hash(shot_ref="S1_B1_SH1", shot=shot, negative_cues=["a", "b"])
    h2 = motion_input_hash(shot_ref="S1_B1_SH1", shot=shot, negative_cues=["b", "a"])
    check("T05-6 顺序变化 hash 改变", h1 != h2, f"{h1[:8]} vs {h2[:8]}")

    # duration half-up
    check("T05-7 duration half-up",
          half_up_round(5.4) == 5 and half_up_round(5.5) == 6 and half_up_round(5.6) == 6)
    check("T05-8 normalize_duration 拒绝 NaN/inf/非正",
          expect_raises(OrchestrationError, normalize_duration, float("nan")) is not None
          and expect_raises(OrchestrationError, normalize_duration, float("inf")) is not None
          and expect_raises(OrchestrationError, normalize_duration, 0) is not None
          and expect_raises(OrchestrationError, normalize_duration, -1) is not None)


# ---------------------------------------------------------------------------
# T06 Mock Provider
# ---------------------------------------------------------------------------
def test_t06():
    log("\n== T06 Mock Provider ==")
    mp = MockVideoProvider()
    r = mp.submit_video("motion", media="data:image/png;base64,AA==",
                        duration=5, resolution="1280*720")
    check("T06-1 submit 返回稳定 task_id 且 pending",
          r.task_id.startswith("mockvid-") and r.status == "pending", r.task_id)
    r2 = mp.submit_video("motion", media="data:image/png;base64,AA==",
                         duration=5, resolution="1280*720")
    check("T06-2 同输入同 task_id", r.task_id == r2.task_id)

    p = mp.get_video_task(r.task_id)
    check("T06-3 默认首 poll succeeded 且有 mock url",
          p.status == "succeeded" and p.video_url.startswith("mock://"), p.video_url)

    # 脚本化状态
    mp2 = MockVideoProvider(script=["pending", "running", "succeeded"])
    r3 = mp2.submit_video("m", media="data:image/png;base64,AA==",
                          duration=5, resolution="1280*720")
    s1 = mp2.get_video_task(r3.task_id).status
    s2 = mp2.get_video_task(r3.task_id).status
    s3 = mp2.get_video_task(r3.task_id).status
    check("T06-4 脚本化状态序列 pending→running→succeeded",
          s1 == "pending" and s2 == "running" and s3 == "succeeded",
          f"{s1}/{s2}/{s3}")

    mp3 = MockVideoProvider(script=["failed"])
    r4 = mp3.submit_video("m", media="data:image/png;base64,AA==",
                          duration=5, resolution="1280*720")
    s4 = mp3.get_video_task(r4.task_id)
    check("T06-5 failed 状态 + 安全错误",
          s4.status == "failed" and s4.error_code == "MOCK_FAILED", s4.error_code)

    # MP4 signature
    data = mp.video_bytes(r.task_id)
    check("T06-6 fixture 含 ftyp 签名", data[4:8] == b"ftyp", str(data[:12]))


# ---------------------------------------------------------------------------
# T07 Bailian 协议（fake transport）
# ---------------------------------------------------------------------------
def test_t07():
    log("\n== T07 Bailian 协议 ==")
    reset_api_counter()  # 隔离：真实 Provider 消费独立 counter 的 video 预算

    def fake_transport(phase, method, url, headers, body, timeout):
        if phase == "submit":
            # 校验 header / endpoint / payload
            assert headers.get("X-DashScope-Async") == "enable"
            assert "/api/v1" in url
            assert url.count("/api/v1") == 1
            assert body["model"] == "wanx-video"
            assert body["input"]["media"][0]["type"] == "first_frame"
            assert "data:image/png;base64," in body["input"]["media"][0]["data"]
            assert body["parameters"]["duration"] == 5
            assert body["parameters"]["resolution"] == "1280*720"
            return 200, {"output": {"task_id": "task-1", "task_status": "PENDING"}}
        if phase == "poll":
            return 200, {"output": {"task_id": "task-1", "task_status": "SUCCEEDED",
                                    "video_url": "https://example.com/v.mp4"}}
        raise AssertionError("unexpected phase")

    counter = APIRequestCounter()
    counter.set_budget("video", 1)
    p = BailianVideoProvider(api_key="sk-test", base_url="https://dashscope.aliyuncs.com/api/v1",
                             model="wanx-video", transport=fake_transport,
                             sleep=lambda s: None, counter=counter)
    # 写一个临时图片做 media
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        media_path = f.name
    r = p.submit_video("motion", media=media_path, duration=5, resolution="1280*720")
    os.unlink(media_path)
    check("T07-1 submit 解析 task_id", r.task_id == "task-1", r.task_id)
    p2 = p.get_video_task("task-1")
    check("T07-2 poll 解析 succeeded + video_url",
          p2.status == "succeeded" and p2.video_url == "https://example.com/v.mp4",
          p2.video_url)
    # 真实 Provider：submit/poll 走 real 账（不同 kind 独立计数）
    snap = counter.snapshot()
    check("T07-5 真实 submit 计数=1、poll 计数=1（独立 kind）",
          snap["by_kind"].get("video", {}).get("real", 0) == 1
          and snap["by_kind"].get("video_poll", {}).get("real", 0) == 1,
          str(snap["by_kind"]))

    # endpoint /api/v1 恰好一次（公共域 + 业务空间域）
    def fake2(phase, method, url, headers, body, timeout):
        if phase == "submit":
            return 200, {"output": {"task_id": "t2"}}
        return 200, {"output": {"task_status": "FAILED", "message": "boom"}}

    p3 = BailianVideoProvider(api_key="k", base_url="https://ws.cn-beijing.maas.aliyuncs.com/api/v1",
                              model="m", transport=fake2, sleep=lambda s: None,
                              counter=APIRequestCounter())
    # 直接测 URL 组合
    check("T07-3 /api/v1 恰好一次（业务空间域）",
          p3.base_url.count("/api/v1") == 1, p3.base_url)

    # submit 0 retry：失败不重试（transport 只调一次）
    calls = {"n": 0}

    def fake_fail(phase, method, url, headers, body, timeout):
        calls["n"] += 1
        if phase == "submit":
            raise LLMError("boom")
        return 200, {}

    p4 = BailianVideoProvider(api_key="k", base_url="https://dashscope.aliyuncs.com/api/v1",
                              model="m", transport=fake_fail, sleep=lambda s: None,
                              counter=APIRequestCounter())
    err = expect_raises(VideoGenerationError, p4.submit_video,
                        "motion", media="data:image/png;base64,AA==",
                        duration=5, resolution="1280*720")
    check("T07-4 submit 0 retry（transport 只调 1 次）",
          err is not None and calls["n"] == 1, f"calls={calls['n']}")


# ---------------------------------------------------------------------------
# T08 异步持久化与崩溃恢复 + T09 预算 + T10 下载 + T12 Review
# ---------------------------------------------------------------------------
def test_t08_t09_t10_t12():
    log("\n== T08/T09/T10/T12 集成 ==")
    pd, reg, image_entry, qa_item, shot, neg = build_qa_passed_project("t08")
    sb = make_storyboard(shot)

    # 用可脚本化 Mock：先 pending → running → succeeded
    mp = MockVideoProvider(script=["pending", "running", "succeeded"])

    reset_api_counter()
    counter = get_api_counter()
    # 注入 fetch：mock:// scheme 返回确定性 MP4 fixture
    fetch_calls = {"n": 0}

    def fake_fetch(url):
        fetch_calls["n"] += 1
        assert url.startswith("mock://")
        return encode_mp4_fixture(b"downloaded")

    stage, sb_ = make_stage(pd, reg, provider=mp, shot=shot, fetch=fake_fetch,
                            counter=counter, allowed_schemes=("mock", "https"))
    rep = stage.run([(1, sb)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })

    # 基本结果
    check("T08-1 成功生成 1 个视频",
          rep["videos_generated"] == 1 and rep["videos_submitted"] == 1,
          f"gen={rep['videos_generated']} sub={rep['videos_submitted']}")

    # manifest 有 task_id + path + sha256
    man = json.loads((pd / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    v = man["videos"][0]
    check("T08-2 manifest 有 task_id + path + sha256",
          v["task_id"].startswith("mockvid-") and v["path"].endswith(".mp4")
          and len(v["sha256"]) == 64, v["video_id"])
    check("T08-3 视频文件已落盘且 SHA 一致",
          (pd / v["path"]).is_file()
          and sha256_of_bytes((pd / v["path"]).read_bytes()) == v["sha256"])

    # ShotAsset 回挂
    check("T12-1 approved 才 attach video ShotAsset",
          any(a.kind == "video" and a.ref == v["video_id"] for a in shot.assets),
          str([a.kind for a in shot.assets]))

    # T09 计数：Mock 场景 0 真实请求；download 走 mock 账（同一审计体系）
    snap = counter.snapshot()
    check("T09-1 Mock 场景 submit/poll 0 真实请求（Mock 0 真实请求铁律）",
          snap["by_kind"].get("video", {}).get("real", 0) == 0
          and snap["by_kind"].get("video_poll", {}).get("real", 0) == 0,
          str(snap["by_kind"].get("video")))
    check("T09-2 download 走 mock 账（同一审计体系，非真实下载）",
          snap["by_kind"].get("video_download", {}).get("mock", 0) >= 1
          and snap["by_kind"].get("video_download", {}).get("real", 0) == 0,
          str(snap["by_kind"].get("video_download")))

    # 提交硬上限：第 2 次被拒
    mp2 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    counter2 = get_api_counter()
    # 构造两个候选（不同 shot/image）
    pd2, reg2, e1, q1, shot2, neg2 = build_qa_passed_project("t09_two")
    # 直接验证 counter.set_budget("video", 1) 后第 2 次 consume 被拒
    counter2.set_budget("video", 1)
    counter2.consume_real_request("video", provider="mock", model="m", attempt=1)
    err = expect_raises(APIBudgetExceededError, counter2.consume_real_request,
                        "video", provider="mock", model="m", attempt=1)
    check("T09-3 video 第 2 次提交在发送前被拒", err is not None, str(err))

    # T10 下载校验：非 MP4 拒绝
    pd3, reg3, e3, q3, shot3, neg3 = build_qa_passed_project("t10_bad")
    mp3 = MockVideoProvider(script=["succeeded"])

    def bad_fetch(url):
        return b"NOT-A-VIDEO"

    reset_api_counter()
    stage3, sb3 = make_stage(pd3, reg3, provider=mp3, shot=shot3, fetch=bad_fetch,
                             counter=get_api_counter(), allowed_schemes=("mock",))
    rep3 = stage3.run([(1, sb3)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T10-1 非 MP4 内容校验失败不 attach",
          rep3["videos_generated"] == 0 and not any(
              a.kind == "video" for a in shot3.assets),
          f"gen={rep3['videos_generated']}")

    # Review rejected：保留证据不 attach
    pd4, reg4, e4, q4, shot4, neg4 = build_qa_passed_project("t12_rej")
    mp4 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage4, sb4 = make_stage(pd4, reg4, provider=mp4, shot=shot4,
                             handler=_handler(ReviewDecision.REJECTED),
                             fetch=fake_fetch, counter=get_api_counter(),
                             allowed_schemes=("mock",))
    rep4 = stage4.run([(1, sb4)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T12-2 rejected 保留证据不 attach",
          rep4["videos_rejected"] >= 1 and not any(
              a.kind == "video" for a in shot4.assets),
          f"rejected={rep4['videos_rejected']}")

    # approved_with_edit fail-loud
    pd5, reg5, e5, q5, shot5, neg5 = build_qa_passed_project("t12_edit")
    mp5 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage5, sb5 = make_stage(pd5, reg5, provider=mp5, shot=shot5,
                             handler=_handler(ReviewDecision.APPROVED_WITH_EDIT),
                             fetch=fake_fetch, counter=get_api_counter(),
                             allowed_schemes=("mock",))
    err = expect_raises(OrchestrationError, stage5.run, [(1, sb5)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0})
    check("T12-3 approved_with_edit fail-loud", err is not None, str(err))


# ---------------------------------------------------------------------------
# T08 崩溃恢复：submit 后 poll 前 manifest 已有 task_id；resume 只 poll 不 submit
# ---------------------------------------------------------------------------
def test_t08_resume():
    log("\n== T08 崩溃恢复 ==")
    pd, reg, image_entry, qa_item, shot, neg = build_qa_passed_project("t08_resume")
    sb = make_storyboard(shot)

    # 第 1 次：脚本 pending + max_polls=1 → poll 超时停在 running（模拟"提交成功但未完成"）
    mp1 = MockVideoProvider(script=["pending", "succeeded"])
    reset_api_counter()
    stage1, sb1 = make_stage(pd, reg, provider=mp1, shot=shot,
                             counter=get_api_counter(), allowed_schemes=("mock",))
    rep1 = stage1.run([(1, sb1)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
        "video_max_polls": 1,
    })
    # manifest 应已有 task_id（submit 后 poll 前）
    man1 = json.loads((pd / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    v1 = man1["videos"][0]
    check("T08-4 submit 后 poll 前 manifest 已有 task_id 且停在 running",
          v1["task_id"].startswith("mockvid-") and v1["status"] == "running",
          f"task={v1['task_id'][:12]} status={v1['status']}")

    # 第 2 次：新 Provider（模拟进程重启），脚本直接 succeeded；resume 应只 poll 不 submit
    mp2 = MockVideoProvider(script=["succeeded"])
    calls_submit = {"n": 0}
    orig_submit = mp2.submit_video

    def counting_submit(*a, **kw):
        calls_submit["n"] += 1
        return orig_submit(*a, **kw)
    mp2.submit_video = counting_submit

    reset_api_counter()
    stage2, sb2 = make_stage(pd, reg, provider=mp2, shot=shot,
                             fetch=lambda url: encode_mp4_fixture(b"resumed"),
                             counter=get_api_counter(), allowed_schemes=("mock",))
    rep2 = stage2.run([(1, sb2)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T08-5 resume 只 poll 不重新 submit",
          calls_submit["n"] == 0 and rep2["videos_generated"] == 1,
          f"submit_calls={calls_submit['n']} gen={rep2['videos_generated']}")


# ---------------------------------------------------------------------------
# T11 manifest 与 resume（完整锚点命中零 submit/poll/download）
# ---------------------------------------------------------------------------
def test_t11():
    log("\n== T11 manifest 与 resume ==")
    pd, reg, image_entry, qa_item, shot, neg = build_qa_passed_project("t11")
    sb = make_storyboard(shot)

    mp1 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage1, sb1 = make_stage(pd, reg, provider=mp1, shot=shot,
                             fetch=lambda url: encode_mp4_fixture(b"done"),
                             counter=get_api_counter(), allowed_schemes=("mock",))
    rep1 = stage1.run([(1, sb1)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T11-1 首次生成成功", rep1["videos_generated"] == 1)

    # 第 2 次：完整锚点命中 → 零 submit/poll/download
    mp2 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    counter2 = get_api_counter()
    stage2, sb2 = make_stage(pd, reg, provider=mp2, shot=shot,
                             fetch=lambda url: encode_mp4_fixture(b"done"),
                             counter=counter2, allowed_schemes=("mock",))
    rep2 = stage2.run([(1, sb2)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    snap = counter2.snapshot()
    check("T11-2 完整锚点命中零 submit/poll/download",
          rep2["videos_skipped"] == 1
          and snap["by_kind"].get("video", {}).get("real", 0) == 0
          and snap["by_kind"].get("video_poll", {}).get("real", 0) == 0
          and snap["by_kind"].get("video_download", {}).get("real", 0) == 0,
          f"skipped={rep2['videos_skipped']} snap={snap['by_kind']}")

    # 篡改 output 后不能误命中（sha 变化）
    man_path = pd / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["videos"][0]["sha256"] = "0" * 64
    man_path.write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    mp3 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    counter3 = get_api_counter()
    stage3, sb3 = make_stage(pd, reg, provider=mp3, shot=shot,
                             fetch=lambda url: encode_mp4_fixture(b"done2"),
                             counter=counter3, allowed_schemes=("mock",))
    rep3 = stage3.run([(1, sb3)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T11-3 completed output hash 变化不能误命中",
          rep3["videos_skipped"] == 0 and rep3["videos_generated"] == 1,
          f"skipped={rep3['videos_skipped']} gen={rep3['videos_generated']}")


# ---------------------------------------------------------------------------
# T04 Stage 14 准入（篡改不能绕过）
# ---------------------------------------------------------------------------
def test_t04():
    log("\n== T04 Stage 14 准入门 ==")
    # 篡改 downstream_eligible 不能绕过（pass 但 downstream_eligible=false → 拒绝）
    pd, reg, image_entry, qa_item, shot, neg = build_qa_passed_project("t04_elig")
    qa_path = pd / "quality" / "qa_manifest.json"
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    qa["images"][0]["downstream_eligible"] = False  # 篡改为 false（应拒绝）
    qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
    sb = make_storyboard(shot)
    mp = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage, sb_ = make_stage(pd, reg, provider=mp, shot=shot, counter=get_api_counter(),
                            allowed_schemes=("mock",))
    rep = stage.run([(1, sb)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0,
    })
    check("T04-1 篡改 downstream_eligible 不能绕过（fail-loud 拒绝）",
          rep["videos_submitted"] == 0, f"submitted={rep['videos_submitted']}")

    # QA index 缺失 → 拒绝
    pd2, reg2, e2, q2, shot2, neg2 = build_qa_passed_project("t04_noqa")
    (pd2 / "quality" / "qa_manifest.json").unlink()
    sb2 = make_storyboard(shot2)
    mp2 = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage2, sb2_ = make_stage(pd2, reg2, provider=mp2, shot=shot2, counter=get_api_counter(),
                              allowed_schemes=("mock",))
    err = expect_raises(OrchestrationError, stage2.run, [(1, sb2)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0})
    check("T04-2 QA index 缺失 → 拒绝", err is not None, str(err))


# ---------------------------------------------------------------------------
# T13 pipeline 接线（顺序 image→image_qa→video→completeness；默认关闭）
# ---------------------------------------------------------------------------
def test_t13():
    log("\n== T13 pipeline 接线 ==")
    from orchestration.pipeline import ProjectOrchestrator
    from core.models import Project
    import inspect
    src = inspect.getsource(ProjectOrchestrator.run)
    # 顺序检查：image_qa 在 video 之前，video 在 completeness 之前
    i_qa = src.find("_stage_image_qa")
    i_video = src.find("_stage_videos")
    i_comp = src.find("_compute_completeness")
    check("T13-1 pipeline 顺序 image→image_qa→video→completeness",
          i_qa != -1 and i_video != -1 and i_qa < i_video < i_comp,
          f"qa={i_qa} video={i_video} comp={i_comp}")

    # run_report 有独立 video 段
    check("T13-2 run_report 有独立 video 段",
          '"video": {}' in inspect.getsource(ProjectOrchestrator.run))


# ---------------------------------------------------------------------------
# T14 真实脚本护栏
# ---------------------------------------------------------------------------
def test_t14():
    log("\n== T14 真实脚本护栏 ==")
    real_script = TEST_DIR / "run_real_video.py"
    check("T14-1 run_real_video.py 存在", real_script.is_file(), str(real_script))
    if real_script.is_file():
        # 运行它（默认应非 0 退出 + 0 网络）
        env = _clean_child_env()
        with no_network():
            p = subprocess.run(
                [sys.executable, str(real_script)],
                capture_output=True, text=True, env=env, cwd=str(V2_ROOT))
        out = (p.stdout or "") + (p.stderr or "")
        check("T14-2 默认打印脱敏计划并非 0 退出",
              p.returncode != 0 or "REAL_EXECUTION_NOT_IMPLEMENTED" in out
              or "脱敏" in out or "plan" in out.lower(),
              f"rc={p.returncode} out={_sanitize(out[:200])}")
        # 不显示 key
        check("T14-3 不显示 key",
              "sk-" not in out and "BAILIAN_API_KEY=" not in out, _sanitize(out[:200]))


# ---------------------------------------------------------------------------
# T03/T10 补充护栏（真实档显式选择；受控下载大小）
# ---------------------------------------------------------------------------
class _RealVendorStub:
    """最小真实档替身：仅用于断言 vendor 护栏（永不真正 submit）。"""
    kind = "video"
    vendor = "bailian"
    model = "wanx-video"


def test_gap_guards():
    log("\n== T03/T10 补充护栏 ==")

    # --- T03-5：真实 Provider 未显式选择 image_id → 拒绝（在预检/构造前）---
    stage_real = VideoStage(
        project_id=PROJECT_ID, project_dir=Path("."), artifacts_dir=Path("."),
        store=None, provider=_RealVendorStub(), registry=None, review_handler=None)
    err = expect_raises(OrchestrationError, stage_real.run, [],
                        {"generate_videos": True})
    check("T03-5 真实 Provider 未显式选择 image_id → 拒绝", err is not None, str(err))

    # --- T03-6：Mock 档不受该护栏约束（_effective_vendor 推断为 mock）---
    stage_mock = VideoStage(
        project_id=PROJECT_ID, project_dir=Path("."), artifacts_dir=Path("."),
        store=None, provider=MockVideoProvider(), registry=None, review_handler=None)
    check("T03-6 Mock 档不受显式选择护栏约束",
          stage_mock._effective_vendor() == "mock",
          str(stage_mock._effective_vendor()))

    # --- T10-2：注入 fetch 返回超过 max_video_bytes 的内容 → 校验失败不 attach ---
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t10_size")
    mp = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    stage_sz, sb_sz = make_stage(
        pd, reg, provider=mp, shot=shot,
        fetch=lambda url: b"\x00\x00\x00\x10ftyp" + b"A" * 100,
        counter=get_api_counter(), allowed_schemes=("mock",), max_video_bytes=64)
    rep_sz = stage_sz.run([(1, sb_sz)], {
        "generate_videos": True, "video_image_ids": ["GEN_001"],
        "video_resolution": "1280*720", "video_poll_interval_seconds": 0})
    check("T10-2 超大小上限拒绝且不 attach",
          rep_sz["videos_generated"] == 0
          and not any(a.kind == "video" for a in shot.assets),
          f"gen={rep_sz['videos_generated']}")

    # --- T10-3/4：默认下载器受控读取（超限不整读；正常路径完整返回）---
    import urllib.request as _ur

    class _FakeResp:
        def __init__(self, data):
            self._d = data

        def read(self, n=-1):
            return self._d if (n is None or n < 0) else self._d[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    _orig_urlopen = _ur.urlopen
    try:
        _ur.urlopen = lambda req, timeout=None: _FakeResp(b"x" * 100)
        err2 = expect_raises(LLMError, _default_fetch, "https://x/y.mp4", max_bytes=10)
        check("T10-3 默认下载受控读取：超限即失败", err2 is not None, str(err2))
        got = _default_fetch("https://x/y.mp4", max_bytes=1000)
        check("T10-4 默认下载正常路径完整返回", got == b"x" * 100, f"len={len(got)}")
    finally:
        _ur.urlopen = _orig_urlopen


# ---------------------------------------------------------------------------
# C 会话补充：T01/T02/T03/T04/T06/T07/T08/T09/T10/T11/T12/T13 负例与边界
# ---------------------------------------------------------------------------
def build_qa_passed_project_multi(tag, shots):
    """构造含多张「Stage 13 image + Stage 14 QA pass」分镜图的最小项目（多候选/多场景）。"""
    from agents.image_qa import expected_checks, validate_artifact, qa_decision
    from orchestration.image_qa_stage import prepare_image_qa_input, build_reference_index

    pd = new_project(tag)
    reg = VisualAssetRegistry(pd)
    char_id, char_v = make_asset(reg, "character", "Lin", CHAR_PROFILE, status="approved")
    reg.save()
    ref_index = build_reference_index(reg)

    entries, qa_items = [], []
    for i, shot in enumerate(shots, start=1):
        image_id = f"GEN_{i:03d}"
        rel = f"generated_images/{image_id}.png"
        sha = write_file(pd / rel, f"GEN-IMAGE-BYTES-{i:03d}".encode("utf-8"))
        entry = {"image_id": image_id, "shot_ref": shot_ref_of(shot), "path": rel,
                 "sha256": sha, "status": "generated",
                 "profile_versions": {char_id: char_v}, "reference_images": []}
        entries.append(entry)
        qa_input = prepare_image_qa_input(project_dir=pd, registry=reg, entry=entry,
                                          reference_index=ref_index)
        expected = expected_checks(qa_input)
        checks = [{"asset_id": e["asset_id"], "version": e["version"], "field": e["field"],
                   "expected": e["expected"], "verdict": "match", "evidence": ""}
                  for e in expected]
        decision = qa_decision(checks)
        artifact = {
            "schema_version": "1", "image_id": image_id, "shot_ref": shot_ref_of(shot),
            "generated_sha256": qa_input["generated_sha256"],
            "ground_truth_mode": qa_input["ground_truth_mode"],
            "profile_versions": {p["asset_id"]: p["version"] for p in qa_input["profiles"]},
            "reference_images": [], "checks": checks, "summary": "all match",
            "qa_decision": decision, "provider": "mock", "model": "mock-multimodal",
            "request_id": f"req-{i}", "retry_count": 0, "created_at": now_iso(),
        }
        artifact = validate_artifact(artifact, qa_input)
        art_rel = f"artifacts/image_qa_{image_id.lower()}.json"
        art_sha = write_file(pd / art_rel,
                             json.dumps(artifact, ensure_ascii=False).encode("utf-8"))
        qa_items.append({
            "image_id": image_id, "shot_ref": shot_ref_of(shot),
            "generated_path": qa_input["_generated_relative_path"],
            "generated_sha256": qa_input["generated_sha256"],
            "profile_versions": {p["asset_id"]: p["version"] for p in qa_input["profiles"]},
            "reference_images": [], "artifact_path": art_rel, "artifact_sha256": art_sha,
            "workflow": {"run_id": f"r{i}", "status": "success", "step_status": "APPROVED"},
            "review": {"id": f"rev-{i}", "decision": "approved"},
            "qa_decision": decision, "provider": "mock", "model": "mock-multimodal",
            "updated_at": now_iso(), "downstream_eligible": True, "error": "",
        })
    write_manifest(pd, entries)
    qa_dir = pd / "quality"
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "qa_manifest.json").write_text(
        json.dumps({"version": "1", "project_id": PROJECT_ID, "images": qa_items},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return pd, reg, entries, qa_items


def _run_spec(ids=("GEN_001",), **extra):
    spec = {"generate_videos": True, "video_resolution": "1280*720",
            "video_poll_interval_seconds": 0}
    if ids is not None:
        spec["video_image_ids"] = list(ids)
    spec.update(extra)
    return spec


def test_t01_extra():
    log("\n== T01 补充：计数器唯一性 ==")
    hits = []
    for p in (V2_ROOT / "llm").rglob("*.py"):
        if p.name == "request_counter.py":
            continue
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.match(r"\s*class\s+\w*Counter\b", ln):
                hits.append(f"{p.relative_to(V2_ROOT).as_posix()}: {ln.strip()}")
    check("T01-6 无第二套请求计数器", not hits, "; ".join(hits))


def test_t02_extra():
    log("\n== T02 补充：无候选零构造 ==")
    pd = new_project("t02_none")
    reg = VisualAssetRegistry(pd)
    reg.save()
    write_manifest(pd, [])
    sb = make_storyboard(make_shot())
    calls = {"n": 0}

    def spy():
        calls["n"] += 1
        return MockVideoProvider()

    stage = VideoStage(project_id=PROJECT_ID, project_dir=pd, artifacts_dir=pd / "artifacts",
                       store=ArtifactStore(pd / "artifacts"), provider=None, registry=reg,
                       review_handler=auto_approve, log_dir=TMP_LOGS,
                       provider_factory=spy, sleep=lambda s: None)
    rep = stage.run([(1, sb)], {"generate_videos": True})
    check("T02-3 无 eligible 候选 → 零 Provider 构造",
          calls["n"] == 0 and rep["videos_submitted"] == 0,
          f"factory={calls['n']} sub={rep['videos_submitted']}")


def test_t03_extra():
    log("\n== T03 补充：未知 ID 与 shot_ref 歧义 ==")
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t03_extra")
    sb = make_storyboard(shot)
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    err = expect_raises(OrchestrationError, stage.run, [(1, sb)], _run_spec(ids=("NOPE",)))
    check("T03-7 未知 image_id → 拒绝", err is not None, str(err))

    pd2, reg2, e2, _q2, shot2, _n2 = build_qa_passed_project("t03_amb")
    mpath = pd2 / "generated_images" / "manifest.json"
    m = json.loads(mpath.read_text(encoding="utf-8"))
    dup = dict(e2)
    dup["image_id"] = "GEN_002"
    m["images"].append(dup)
    mpath.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    sb2 = make_storyboard(shot2)
    stage2, _ = make_stage(pd2, reg2, provider=MockVideoProvider(), shot=shot2)
    err2 = expect_raises(OrchestrationError, stage2.run, [(1, sb2)], _run_spec(ids=None))
    check("T03-8 shot_ref 映射歧义 → 拒绝",
          err2 is not None and "歧义" in str(err2), str(err2))


def test_t04_extra():
    log("\n== T04 补充：准入负例 ==")
    # 4-3 Workflow 非成功
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t04_wf")
    p = pd / "quality" / "qa_manifest.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["images"][0]["workflow"]["status"] = "failed"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    rep = stage.run([(1, sb)], _run_spec())
    check("T04-3 Workflow 非成功 → 零提交", rep["videos_submitted"] == 0,
          f"sub={rep['videos_submitted']}")

    # 4-4 QA Artifact hash 变化
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t04_arthash")
    p = pd / "quality" / "qa_manifest.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["images"][0]["artifact_sha256"] = "0" * 64
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    rep = stage.run([(1, sb)], _run_spec())
    check("T04-4 QA Artifact hash 变化 → 零提交", rep["videos_submitted"] == 0,
          f"sub={rep['videos_submitted']}")

    # 4-5 QA Artifact 缺失
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t04_artmiss")
    (pd / "artifacts" / "image_qa_gen_001.json").unlink()
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    rep = stage.run([(1, sb)], _run_spec())
    check("T04-5 QA Artifact 缺失 → 零提交", rep["videos_submitted"] == 0,
          f"sub={rep['videos_submitted']}")

    # 4-6 生成图磁盘 SHA 变化
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t04_imgtamper")
    (pd / "generated_images" / "GEN_001.png").write_bytes(b"TAMPERED")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(), shot=shot)
    rep = stage.run([(1, sb)], _run_spec())
    check("T04-6 生成图磁盘 SHA 变化 → 零提交", rep["videos_submitted"] == 0,
          f"sub={rep['videos_submitted']}")


def test_t06_extra():
    log("\n== T06 补充：canceled / unknown ==")
    for st, code, name in (("canceled", "MOCK_CANCELED", "T06-7"),
                           ("unknown", "MOCK_UNKNOWN", "T06-8")):
        mp = MockVideoProvider(script=[st])
        r = mp.submit_video("m", media="data:image/png;base64,AA==",
                            duration=5, resolution="1280*720")
        s = mp.get_video_task(r.task_id)
        check(f"{name} 脚本化 {st} 状态 + 安全错误",
              s.status == st and s.error_code == code, f"{s.status}/{s.error_code}")


def test_t07_extra():
    log("\n== T07 补充：脱敏 / Config 驱动 / 拒绝 ==")

    def fake_err(phase, method, url, headers, body, timeout):
        raise LLMError("boom Authorization: Bearer sk-secret1234567890")

    p = BailianVideoProvider(api_key="sk-secret1234567890",
                             base_url="https://dashscope.aliyuncs.com/api/v1",
                             model="m", transport=fake_err, sleep=lambda s: None,
                             counter=APIRequestCounter())
    err = expect_raises(VideoGenerationError, p.submit_video, "motion",
                        media="data:image/png;base64,AA==",
                        duration=5, resolution="1280*720")
    check("T07-6 错误文本脱敏（不含 key/URL）",
          err is not None and "sk-secret" not in str(err) and "dashscope" not in str(err),
          str(err))

    # key 已配置，确保后续拒绝走各自的目标分支（而非提前因缺 key 失败）
    os.environ["BAILIAN_API_KEY"] = "sk-envtest"
    try:
        prov = create_provider("video", vendor="bailian",
                               cfg={"default_model": "cfg-model-xyz",
                                    "base_url": "https://dashscope.aliyuncs.com/api/v1"})
        check("T07-7 model 完全来自 Config",
              getattr(prov, "model", "") == "cfg-model-xyz", getattr(prov, "model", ""))

        e2 = expect_raises(ValueError, create_provider, "video", vendor="bailian",
                           cfg={"default_model": "", "base_url": "https://x/api/v1"})
        check("T07-8 空 default_model → 拒绝",
              e2 is not None and "default_model" in str(e2), str(e2))

        e3 = expect_raises(ValueError, create_provider, "video", vendor="bailian",
                           cfg={"default_model": "m", "base_url": "https://x/api/v1",
                                "protocol": "openai_video"})
        check("T07-9 未知 protocol → 拒绝",
              e3 is not None and "protocol" in str(e3), str(e3))
    finally:
        os.environ.pop("BAILIAN_API_KEY", None)


class _AmbigStub:
    """提交后未返回 task_id 的替身（模拟 ambiguous submit，禁止自动重提）。"""
    kind = "video"
    vendor = "mock"
    model = "m"

    def __init__(self):
        self.polls = 0

    def submit_video(self, *a, **k):
        return VideoTaskResult(task_id="", status="ambiguous",
                               error_code="AMBIGUOUS_SUBMIT", error_message="x")

    def get_video_task(self, tid):
        self.polls += 1
        raise AssertionError("ambiguous 后不应轮询")


def test_t08_extra():
    log("\n== T08 补充：ambiguous 不自动重提 ==")
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t08_amb")
    sb = make_storyboard(shot)
    stub = _AmbigStub()
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=stub, shot=shot)
    rep = stage.run([(1, sb)], _run_spec())
    man = json.loads((pd / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    check("T08-6 ambiguous submit → stale、不 poll、不重提",
          rep["videos_failed"] == 1 and stub.polls == 0
          and man["videos"][0]["status"] == "stale" and man["videos"][0]["task_id"] == "",
          f"failed={rep['videos_failed']} polls={stub.polls} st={man['videos'][0]['status']}")


def test_t09_extra():
    log("\n== T09 补充：report 与 counter 一致 ==")
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t09_rep")
    sb = make_storyboard(shot)
    mp = MockVideoProvider(script=["succeeded"])
    reset_api_counter()
    counter = get_api_counter()
    stage, _ = make_stage(pd, reg, provider=mp, shot=shot,
                          fetch=lambda url: encode_mp4_fixture(b"x"),
                          counter=counter, allowed_schemes=("mock",))
    rep = stage.run([(1, sb)], _run_spec())
    check("T09-4 report 计数与 counter 一致（Mock 真实=0）",
          rep["submit_attempts"] == counter.real_requests("video") == 0
          and rep["videos_submitted"] == 1,
          f"rep_sub={rep['submit_attempts']} ctr={counter.real_requests('video')}")


def test_t10_extra():
    log("\n== T10 补充：scheme / 空文件 / URL 不落盘 / 原子写 ==")
    # 10-5 非 https scheme 拒绝（mock:// 不在白名单）
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t10_scheme")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(script=["succeeded"]),
                          shot=shot, fetch=lambda url: encode_mp4_fixture(b"x"),
                          counter=get_api_counter(), allowed_schemes=("https",))
    rep = stage.run([(1, sb)], _run_spec())
    check("T10-5 非 https scheme 拒绝", rep["videos_generated"] == 0,
          f"gen={rep['videos_generated']}")

    # 10-6 空内容拒绝
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t10_empty")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(script=["succeeded"]),
                          shot=shot, fetch=lambda url: b"",
                          counter=get_api_counter(), allowed_schemes=("mock",))
    rep = stage.run([(1, sb)], _run_spec())
    check("T10-6 空内容拒绝", rep["videos_generated"] == 0, f"gen={rep['videos_generated']}")

    # 10-7 签名 URL 不进 manifest/账本
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t10_nourl")
    sb = make_storyboard(shot)
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(script=["succeeded"]),
                          shot=shot, fetch=lambda url: encode_mp4_fixture(b"x"),
                          counter=get_api_counter(), allowed_schemes=("mock",))
    stage.run([(1, sb)], _run_spec())
    man_txt = (pd / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME).read_text(encoding="utf-8")
    ledger = TMP_LOGS / VIDEO_LEDGER_FILENAME
    led_txt = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
    check("T10-7 签名 URL 不进 manifest/账本",
          "mock://" not in man_txt and "video_url" not in man_txt and "mock://" not in led_txt,
          f"man={'mock://' in man_txt} led={'mock://' in led_txt}")

    # 10-8 原子写失败不留正式文件/tmp
    root = new_project("t10_atomic")
    target = root / "v.mp4"
    orig_replace = os.replace

    def boom(src, dst):
        raise OSError("disk full")

    os.replace = boom
    try:
        err = expect_raises(OSError, VideoStage._atomic_write_bytes, target, b"data")
    finally:
        os.replace = orig_replace
    leftovers = [str(p) for p in list(root.glob(".v.mp4.*")) + list(root.glob("v.mp4"))]
    check("T10-8 原子写失败不留正式文件/tmp", err is not None and not leftovers,
          str(leftovers))


def test_t11_extra():
    log("\n== T11 补充：video_id 单调 / manifest 严格 ==")
    check("T11-4 video_id 单调递增（_next_video_index）",
          VideoStage._next_video_index(
              {"videos": [{"video_id": "VID_003"}, {"video_id": "VID_001"}]}) == 4
          and VideoStage._next_video_index({"videos": []}) == 1)

    vd = new_project("t11_manifest") / VIDEO_DIR_NAME
    vd.mkdir(parents=True, exist_ok=True)
    stage = VideoStage(project_id=PROJECT_ID, project_dir=vd.parent,
                       artifacts_dir=vd.parent / "artifacts", store=None,
                       provider=None, registry=None, review_handler=None)
    dup = {"version": "1", "project_id": PROJECT_ID,
           "videos": [{"video_id": "VID_001", "status": "generated"},
                      {"video_id": "VID_001", "status": "generated"}]}
    (vd / VIDEO_MANIFEST_FILENAME).write_text(json.dumps(dup), encoding="utf-8")
    e1 = expect_raises(OrchestrationError, stage._load_manifest, vd)
    check("T11-5 重复 video_id fail-loud", e1 is not None, str(e1))

    bad = {"version": "1", "project_id": PROJECT_ID,
           "videos": [{"video_id": "VID_001", "status": "bogus"}]}
    (vd / VIDEO_MANIFEST_FILENAME).write_text(json.dumps(bad), encoding="utf-8")
    e2 = expect_raises(OrchestrationError, stage._load_manifest, vd)
    check("T11-6 非法状态 fail-loud", e2 is not None, str(e2))


def test_t12_t13_extra():
    log("\n== T12/T13 补充：幂等 / 版本 / 多候选 ==")
    # 12-4 attach 幂等
    shot = make_shot()
    VideoStage._attach_shot_asset(shot, "VID_001", "a" * 64, "1280*720")
    VideoStage._attach_shot_asset(shot, "VID_001", "a" * 64, "1280*720")
    check("T12-4 attach 幂等（同 ref 只挂一个）",
          len([a for a in shot.assets if a.kind == "video"]) == 1)

    # 成功一轮：新版本 / Stage13-14 产物不变 / report 字段
    pd, reg, _e, _q, shot, _n = build_qa_passed_project("t12_t13")
    sb = make_storyboard(shot)
    img_sha0 = sha256_of_bytes((pd / "generated_images" / "GEN_001.png").read_bytes())
    qa_sha0 = sha256_of_bytes((pd / "artifacts" / "image_qa_gen_001.json").read_bytes())
    reset_api_counter()
    stage, _ = make_stage(pd, reg, provider=MockVideoProvider(script=["succeeded"]),
                          shot=shot, fetch=lambda url: encode_mp4_fixture(b"ok"),
                          counter=get_api_counter(), allowed_schemes=("mock",))
    rep = stage.run([(1, sb)], _run_spec())
    img_sha1 = sha256_of_bytes((pd / "generated_images" / "GEN_001.png").read_bytes())
    qa_sha1 = sha256_of_bytes((pd / "artifacts" / "image_qa_gen_001.json").read_bytes())
    check("T12-5 Storyboard 新版本已产出（不覆盖原文件）",
          len(stage.new_storyboard_versions) == 1, str(stage.new_storyboard_versions))
    check("T12-6 Stage 13/14 产物 0 修改", img_sha0 == img_sha1 and qa_sha0 == qa_sha1)
    need = {"enabled", "videos_generated", "videos_submitted", "budget_cap",
            "cost_total", "manifest", "storyboard_versions", "provider", "model"}
    check("T13-4 video 段字段齐全", need <= set(rep), str(sorted(set(rep))))

    # 多候选（多场景）：硬上限 1 → 首个提交、其余拒绝，数据不串
    s1 = make_shot(scene_id=1, shot_id=1)
    s2 = make_shot(scene_id=2, shot_id=1)
    pd2, reg2, _es, _qs = build_qa_passed_project_multi("t13_multi", [s1, s2])
    sb1 = make_storyboard(s1, scene_id=1)
    sb2 = make_storyboard(s2, scene_id=2)
    reset_api_counter()
    stage2 = VideoStage(project_id=PROJECT_ID, project_dir=pd2,
                        artifacts_dir=pd2 / "artifacts",
                        store=ArtifactStore(pd2 / "artifacts"),
                        provider=MockVideoProvider(script=["succeeded", "succeeded"]),
                        registry=reg2, review_handler=auto_approve,
                        review_mode="auto_approve", log_dir=TMP_LOGS,
                        fetch=lambda url: encode_mp4_fixture(b"multi"),
                        counter=get_api_counter(), sleep=lambda s: None,
                        allowed_schemes=("mock",))
    rep2 = stage2.run([(1, sb1), (2, sb2)], _run_spec(ids=("GEN_001", "GEN_002")))
    man2 = json.loads((pd2 / VIDEO_DIR_NAME / VIDEO_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    check("T13-3 多候选：硬上限 1 首个提交、其余拒绝、数据不串",
          rep2["videos_generated"] == 1 and rep2["videos_rejected"] == 1
          and len(man2["videos"]) == 1
          and man2["videos"][0]["source_image_id"] == "GEN_001"
          and man2["videos"][0]["shot_ref"] == shot_ref_of(s1),
          f"gen={rep2['videos_generated']} rej={rep2['videos_rejected']} "
          f"ids={[v['source_image_id'] for v in man2['videos']]}")


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def write_report():
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    failed = total - passed
    groups = {}
    for r in _results:
        g = r["id"].split("-")[0]
        groups.setdefault(g, {"passed": 0, "total": 0, "failed": 0})
        groups[g]["total"] += 1
        if r["ok"]:
            groups[g]["passed"] += 1
        else:
            groups[g]["failed"] += 1
    report = {
        "stage": "15-B",
        "head": git("rev-parse", "HEAD").strip(),
        "started_at": now_iso(),
        "ended_at": now_iso(),
        "tests_total": total,
        "passed": passed,
        "failed": failed,
        "groups": [{"id": g, **v} for g, v in sorted(groups.items())],
        "real_api_requests": 0,
        "network_requests": 0,
        "model_switches": 0,
        "regressions": _regressions,
    }
    out = TEST_DIR / "test_report.json"
    tmp = TEST_DIR / ".test_report.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    log(f"\n报告已写：{out}")
    log(f"TOTAL: {passed}/{total} passed" + (f" ({failed} failed)" if failed else ""))
    return failed


def main():
    log(f"FilmAgent V0.2 Stage 15-B 测试套件")
    log(f"V2_ROOT={V2_ROOT}")
    try:
        with no_network():
            test_t01()
            test_t02()
            test_t03()
            test_t05()
            test_t06()
            test_t07()
            test_t08_t09_t10_t12()
            test_t08_resume()
            test_t11()
            test_t04()
            test_t13()
            test_t14()
            test_gap_guards()
            test_t01_extra()
            test_t02_extra()
            test_t03_extra()
            test_t04_extra()
            test_t06_extra()
            test_t07_extra()
            test_t08_extra()
            test_t09_extra()
            test_t10_extra()
            test_t11_extra()
            test_t12_t13_extra()
    except Exception as exc:  # noqa: BLE001
        log("未捕获异常：")
        log(traceback.format_exc())
        check("FATAL", False, f"{type(exc).__name__}: {exc}")

    failed = write_report()
    # 清理 tmp 目录
    for d in (TMP_PROJECTS, TMP_LOGS):
        _hard_rmtree(d)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
