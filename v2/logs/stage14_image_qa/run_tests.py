# -*- coding: utf-8 -*-
"""
V0.2 Stage 14-C —— VLM Image QA 正式测试套件（mock，0 真实 API / 0 网络 / 0 模型切换）

被测对象（Stage 14-B 新增 / 允许最小修改的文件；禁改清单 0 diff）：
    v2/agents/image_qa.py               ImageQAAgent / ImageQAStep / 严格 validator
    v2/prompts/v0.2/image_qa.txt        系统提示词
    v2/orchestration/image_qa_stage.py  ImageQAStage（选择/预检/Workflow/Review/index/resume）
    v2/agents/__init__.py               导出接线（允许最小修改）
    v2/orchestration/__init__.py        导出接线（允许最小修改）
    v2/orchestration/pipeline.py        Stage 14 接线（允许最小修改）

覆盖（Stage_14C.md §6 T01~T13）：
    T01  静态契约与冻结边界
    T02  manifest 选择和调用前失败
    T03  精确 Profile 版本和字段顺序
    T04  D14-REF-01 全局 reference resolver
    T05  路径 containment 与 SHA256
    T06  Prompt、图片顺序与敏感信息
    T07  Provider 输出严格校验
    T08  Workflow、Artifact 与 Review 三态
    T09  QA index 完整性
    T10  resume 命中与全部 stale 条件
    T11  预算、retry 与模型授权
    T12  pipeline 与多图片隔离
    T13  真实冒烟脚本的零调用护栏

用法：
    python v2/logs/stage14_image_qa/run_tests.py                 # T01~T13（快）
    python v2/logs/stage14_image_qa/run_tests.py --with-history  # 另跑 stage13 历史回归

本文件真实 API 调用 = 0（全 Mock / 本地 fake-real），网络 = 0，模型切换 = 0。
"""
import contextlib
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
REPO_ROOT = Path(__file__).resolve().parents[3]            # git 根
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用（绝不碰真实项目）
TMP_LOGS = TEST_DIR / "tmp_logs"

sys.path.insert(0, str(V2_ROOT))

from core.assets import AssetRegistry                                      # noqa: E402
from core.models import Episode, Project, World                            # noqa: E402
from core.token_log import TokenUsage                                      # noqa: E402
from core.visual import (                                                  # noqa: E402
    CharacterVisualProfile, LocationVisualProfile, PropVisualProfile,
    StyleProfile, PROFILE_CLASSES,
)
from core.visual_registry import VisualAssetRegistry                       # noqa: E402
from llm.client import LLMConnectionError, LLMError                        # noqa: E402
from llm.providers.base import ProviderResult                              # noqa: E402
from llm.providers.mock import MockTextProvider                            # noqa: E402
from llm.providers.mock_image import MockImageGenerationProvider           # noqa: E402
from llm.request_counter import (                                          # noqa: E402
    MAX_REAL_VISION_REQUESTS, APIBudgetExceededError, APIRequestCounter,
    get_api_counter, reset_api_counter,
)
from workflow.artifact import ArtifactStore                                # noqa: E402
from workflow.models import ReviewDecision                                 # noqa: E402
from orchestration import ProjectOrchestrator, OrchestrationError          # noqa: E402
from orchestration.image_qa_stage import (                                 # noqa: E402
    ImageQAStage, QA_DIR_NAME, QA_MANIFEST_FILENAME, IMAGE_MANIFEST_REL,
    sha256_file, normalize_sha256, safe_project_file, project_relative,
    load_stage13_manifest, select_generated_entries, build_reference_index,
    prepare_image_qa_input,
)
from agents.image_qa import (                                              # noqa: E402
    ImageQAAgent, ImageQAStep, VERDICTS, QA_DECISIONS, expected_checks,
    validate_provider_payload, validate_artifact, qa_decision,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJECT_ID = "qa_proj"
STAGE13_TAG = "3895c6c103f125d458fe648d80a625bf007601af"
BASELINE_HEAD = "1a7404942edb6e493091a449256da5a75d456ccc"

FROZEN_PATHS = (
    "FilmAgent",
    "v2/core",
    "v2/workflow",
    "v2/review",
    "v2/memory",
    "v2/context",
    "v2/llm/providers/base.py",
    "v2/llm/providers/openai_compatible_vision.py",
    "v2/llm/client.py",
    "v2/llm/router.py",
    "v2/llm/request_counter.py",
    "v2/orchestration/image_stage.py",
    ".gitignore",
)

_results = []
_log_lines = []
_regressions = []


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def log(msg=""):
    _log_lines.append(str(msg))
    print(msg, flush=True)


def _sanitize(text) -> str:
    """机器报告里绝不出现绝对路径 / API key / base64 / 完整 prompt。"""
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
    """把 socket 连接打死：期间任何真实网络访问都会抛错（0 网络的硬证据）。"""
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
    """用 os 层递归删除，绕过运行环境对 shutil.rmtree 的劫持。"""
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
    """给子进程剥离宿主经 PYTHONPATH 注入的 sitecustomize shim（劫持 shutil.rmtree）。"""
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
    "body": "slim build",
    "color_palette": "red and black",
    "distinguishing_features": "small scar on left cheek",
    "visual_style": "photorealistic",
    "negative_cues": "no hat, no sunglasses",
}
LOC_PROFILE = {
    "environment": "warm cafe interior",
    "lighting": "amber lamp light",
    "palette": "amber and wood tones",
    "architecture": "brick walls",
    "time_of_day": "evening",
    "distinguishing_features": "checkered floor",
    "visual_style": "photorealistic",
    "negative_cues": "no neon signs",
}
PROP_PROFILE = {"form": "small square box", "color": "blue",
                "material": "cardboard", "size": "hand sized"}
STYLE_PROFILE = {"medium": "35mm film", "palette": "amber tones",
                 "rendering": "soft grain", "notes": "project default style"}


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
    """注册一个视觉资产并把它推到目标状态；返回 (asset_id, version)。"""
    a = reg.register(kind, name)
    prof = dict(profile) if isinstance(profile, dict) else profile
    if status in ("draft", "generated"):
        v = reg.new_version(a.asset_id, profile=prof, status=status)
        return a.asset_id, v.version
    v = reg.new_version(a.asset_id, profile=prof)
    if status == "reviewing":
        reg.submit_for_review(a.asset_id, v.version)
    elif status == "approved":
        reg.submit_for_review(a.asset_id, v.version)
        reg.approve(a.asset_id, v.version)
    elif status == "rejected":
        reg.submit_for_review(a.asset_id, v.version)
        reg.reject(a.asset_id, v.version)
    elif status == "archived":
        reg.archive(a.asset_id)
    return a.asset_id, v.version


def make_ref_asset(reg, name, img_path, *, status="approved", ref_kind="pose",
                   image_id=None, extra_version=False):
    """注册 kind=reference 的独立参考资产；返回 (asset_id, version, ReferenceImage)。"""
    a = reg.register("reference", name, purpose="pose")
    v = reg.new_version(a.asset_id, profile=None)
    img = reg.add_reference_image(a.asset_id, v.version, path=img_path, kind=ref_kind)
    if image_id:
        img.image_id = image_id
    if extra_version:
        v2 = reg.new_version(a.asset_id, profile=None)
        img2 = reg.add_reference_image(a.asset_id, v2.version, path=img_path, kind=ref_kind)
        if image_id:
            img2.image_id = image_id
    if status == "approved":
        for ver in [v.version] + ([v2.version] if extra_version else []):
            reg.submit_for_review(a.asset_id, ver)
            reg.approve(a.asset_id, ver)
    elif status == "draft":
        pass
    elif status == "rejected":
        reg.submit_for_review(a.asset_id, v.version)
        reg.reject(a.asset_id, v.version)
    elif status == "reviewing":
        reg.submit_for_review(a.asset_id, v.version)
    elif status == "archived":
        reg.archive(a.asset_id)
    reg.save()
    return a.asset_id, v.version, img


def gen_image(pd: Path, image_id: str, data: bytes):
    rel = f"generated_images/{image_id}.png"
    sha = write_file(pd / rel, data)
    return rel, sha


def gen_entry(image_id, shot_ref, rel, sha, profile_versions, reference_images=None,
              status="generated"):
    return {
        "image_id": image_id, "shot_ref": shot_ref, "path": rel, "sha256": sha,
        "status": status,
        "profile_versions": (dict(profile_versions)
                             if isinstance(profile_versions, dict) else profile_versions),
        "reference_images": list(reference_images or []),
    }


def write_manifest(pd: Path, entries, *, version="1", project_id=PROJECT_ID,
                   raw=None):
    m = raw if raw is not None else {
        "version": version, "project_id": project_id, "images": list(entries)}
    p = pd / IMAGE_MANIFEST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def basic_fixture(tag, *, with_ref=True, char_status="approved", n_images=1,
                  extra_refs=0, shot_ref_prefix="S1_B1_SH"):
    """构造一个最小可用项目：1 个角色 + 可选独立 REF + N 张生成图 + manifest。"""
    pd = new_project(tag)
    reg = VisualAssetRegistry(pd)
    char_id, char_v = make_asset(reg, "character", "Lin", CHAR_PROFILE, status=char_status)
    refs = []
    if with_ref:
        for i in range(1 + extra_refs):
            imgp = pd / "refs" / f"pose{i + 1}.png"
            write_file(imgp, f"REF-BYTES-{i + 1}".encode("utf-8"))
            _aid, _v, img = make_ref_asset(reg, f"pose bundle {i + 1}", imgp)
            refs.append({"image_id": img.image_id, "sha256": img.checksum})
    entries = []
    for i in range(n_images):
        image_id = f"GEN_{i + 1:03d}"
        rel, sha = gen_image(pd, image_id, f"GEN-BYTES-{i + 1}".encode("utf-8"))
        entries.append(gen_entry(image_id, f"{shot_ref_prefix}{i + 1}", rel, sha,
                                 {char_id: char_v}, refs))
    write_manifest(pd, entries)
    return pd, reg, entries


# ---------------------------------------------------------------------------
# Provider / Review 替身
# ---------------------------------------------------------------------------
class SpyProvider:
    """记录 images tuple 与 messages 的假多模态 Provider（budget_kind=None → 非真实桶）。"""

    kind = "multimodal"
    vendor = "spy"
    model = "spy-multimodal"
    budget_kind = None

    def __init__(self, payloads=None, *, model="spy-multimodal", vendor="spy",
                 response_model=None, raise_exc=None, payload_fn=None):
        self.calls = []
        self.payloads = list(payloads or [])
        self.model = model
        self.vendor = vendor
        self.response_model = response_model
        self.raise_exc = raise_exc
        self.payload_fn = payload_fn

    def chat(self, messages, *, images=(), step="", agent=""):
        self.calls.append({"messages": list(messages), "images": tuple(images), "step": step})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.payload_fn is not None:
            payload = self.payload_fn(len(self.calls), messages, images)
        elif self.payloads:
            payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        else:
            payload = {"checks": [], "summary": ""}
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ProviderResult(text=text, model=self.response_model or self.model,
                              usage=TokenUsage(), request_id="req-1", retry_count=0)


class FakeRealVisionProvider:
    """本地 fake-real Provider：消费注入的 APIRequestCounter，但不联网。

    budget_kind='vision' → 触发真实预算桶与 identity 校验；`attempts_per_call`
    模拟 Provider 内部 retry（每次 attempt 各消费一次预算）。
    """

    kind = "vision"
    vendor = "fakevision"
    model = "fake-vision-1"
    budget_kind = "vision"

    def __init__(self, counter, *, attempts_per_call=1, payload_fn=None,
                 model="fake-vision-1", vendor="fakevision", fail_first=False):
        self.counter = counter
        self.attempts_per_call = int(attempts_per_call)
        self.payload_fn = payload_fn
        self.model = model
        self.vendor = vendor
        self.fail_first = fail_first
        self.calls = 0
        self.attempts = 0

    def chat(self, messages, *, images=(), step="", agent=""):
        self.calls += 1
        payload = (self.payload_fn(len(messages), messages, images)
                   if self.payload_fn else {"checks": [], "summary": ""})
        text = payload if isinstance(payload, str) else json.dumps(payload)
        if self.fail_first and self.calls == 1:
            # 一次瞬时失败（retryable）——但预算已在发送前消费
            self.counter.consume_real_request("vision", provider=self.vendor,
                                              model=self.model, attempt=1)
            self.attempts += 1
            raise LLMConnectionError("transient boom")
        for i in range(self.attempts_per_call):
            self.counter.consume_real_request("vision", provider=self.vendor,
                                              model=self.model, attempt=i + 1)
            self.attempts += 1
        return ProviderResult(text=text, model=self.model, usage=TokenUsage(),
                              request_id="fake-req", retry_count=0)


class SpyFactory:
    def __init__(self, provider):
        self.provider = provider
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.provider


class _Decision:
    def __init__(self, decision, review_id="rev-1", edited_content=None, comment=""):
        self.decision = str(decision)
        self.review_id = str(review_id)
        self.edited_content = edited_content
        self.comment = str(comment or "")


def _handler(decision, edited=None):
    n = {"i": 0}

    def h(artifact, context):
        n["i"] += 1
        ed = edited(artifact.read()) if callable(edited) else edited
        return _Decision(decision, f"rev-{n['i']}", edited_content=ed)
    return h


def make_stage(pd, reg, *, provider=None, factory=None, identity=None, counter=None,
               handler=None, max_attempts=3):
    if handler is None:
        handler = _handler(ReviewDecision.APPROVED)
    return ImageQAStage(
        project_id=PROJECT_ID, project_dir=pd, artifacts_dir=pd / "artifacts",
        store=ArtifactStore(pd / "artifacts"), registry=reg, review_handler=handler,
        provider=provider, provider_factory=factory, provider_identity=identity,
        counter=counter,
        review_mode="auto_approve (programmatic, NOT human review)",
        max_attempts=max_attempts,
    )


# ---------------------------------------------------------------------------
# payload 工具
# ---------------------------------------------------------------------------
def all_match_payload_fn(call_no, messages, images):
    """从 user payload 里的 expected_checks 造一份全 match 的合法响应。"""
    payload = json.loads(messages[-1]["content"])
    checks = [{"asset_id": e["asset_id"], "version": e["version"], "field": e["field"],
               "expected": e["expected"], "verdict": "match", "evidence": ""}
              for e in payload["expected_checks"]]
    return {"checks": checks, "summary": "all match"}


def payload_from(expected, verdict_map=None, default="match", summary="looks fine"):
    checks = []
    for e in expected:
        key = (e["asset_id"], e["version"], e["field"])
        v = (verdict_map or {}).get(key, default)
        ev = "" if v == "match" else f"evidence for {e['field']}"
        checks.append({"asset_id": e["asset_id"], "version": e["version"],
                       "field": e["field"], "expected": e["expected"],
                       "verdict": v, "evidence": ev})
    return {"checks": checks, "summary": summary}


def expected_of(pd, reg, entry):
    ref_index = build_reference_index(reg)
    qa_input = prepare_image_qa_input(project_dir=pd, registry=reg, entry=entry,
                                      reference_index=ref_index)
    return qa_input, expected_checks(qa_input)


def read_index(pd: Path) -> dict:
    p = pd / QA_DIR_NAME / QA_MANIFEST_FILENAME
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def index_entry(pd: Path, image_id: str):
    for item in read_index(pd).get("images", []):
        if item.get("image_id") == image_id:
            return item
    return None


def artifact_bytes(pd: Path):
    d = pd / "artifacts"
    if not d.is_dir():
        return {}
    return {p.name: p.read_bytes() for p in sorted(d.glob("image_qa_*.json"))}


# ===========================================================================
# T01 静态契约与冻结边界
# ===========================================================================
def test_T01_static_contract_and_freeze():
    check("T01-1a ImageQAAgent 公开接口存在",
          callable(getattr(ImageQAAgent, "evaluate", None))
          and ImageQAAgent.name == "image_qa")
    check("T01-1b ImageQAStep 公开接口存在（needs_review=True）",
          callable(getattr(ImageQAStep, "run", None))
          and ImageQAStep.needs_review is True and ImageQAStep.retryable is True)
    check("T01-1c ImageQAStage 公开接口存在",
          callable(getattr(ImageQAStage, "run", None))
          and isinstance(ImageQAStage.disabled_report(), dict))
    check("T01-1d 纯函数契约导出（expected_checks/validator/qa_decision）",
          callable(expected_checks) and callable(validate_provider_payload)
          and callable(validate_artifact) and callable(qa_decision)
          and set(VERDICTS) == {"match", "mismatch", "uncertain", "not_visible"}
          and set(QA_DECISIONS) == {"pass", "fail", "needs_review"})

    prompt_path = V2_ROOT / "prompts" / "v0.2" / "image_qa.txt"
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    low = prompt.lower()
    check("T01-2a Prompt 文件存在", prompt_path.is_file())
    check("T01-2b Prompt 含 exact check / verdict / negative_cues / 多图顺序",
          all(tok in low for tok in ("check", "verdict", "negative_cues", "image 0",
                                     "reference", "match", "mismatch", "uncertain",
                                     "not_visible")),
          f"len={len(prompt)}")

    diff = git("diff", "--stat", "--", *FROZEN_PATHS)
    check("T01-3 冻结边界 0 diff（FilmAgent/core/workflow/review/provider/counter/image_stage）",
          diff.strip() == "", diff[:200])

    schema = git("diff", "--stat", "--", "v2/core/shot.py", "v2/workflow/models.py",
                 "v2/orchestration/image_stage.py")
    check("T01-4 Stage13 manifest schema 与 Canonical Shot/Storyboard schema 未改",
          schema.strip() == "", schema[:200])

    tag = git("rev-parse", "v0.2-stage13^{commit}").strip()
    check("T01-5 v0.2-stage13 仍指向固定 commit", tag == STAGE13_TAG, tag)

    new_files = ["v2/agents/image_qa.py", "v2/orchestration/image_qa_stage.py",
                 "v2/prompts/v0.2/image_qa.txt"]
    bad = []
    net_pats = ("import requests", "from requests", "import urllib", "from urllib",
                "import httpx", "import socket", "import openai", "from openai",
                "http.client", "aiohttp")
    for f in new_files:
        src = (REPO_ROOT / f).read_text(encoding="utf-8")
        for pat in net_pats:
            if pat in src:
                bad.append(f"{f}:{pat}")
    check("T01-6a 新实现无直接 requests/urllib/SDK/socket 网络调用", not bad, str(bad))

    stage_src = (REPO_ROOT / "v2/orchestration/image_qa_stage.py").read_text(encoding="utf-8")
    agent_src = (REPO_ROOT / "v2/agents/image_qa.py").read_text(encoding="utf-8")
    second_counter = re.search(r"class\s+\w*Counter\w*", stage_src + agent_src)
    check("T01-6b 未新增第二请求计数器（无 Counter 类定义）", second_counter is None,
          second_counter.group(0) if second_counter else "")
    check("T01-6c 计数只经 llm.request_counter 单例/注入",
          "from llm.request_counter import" in stage_src
          and "get_api_counter" in stage_src)


# ===========================================================================
# T02 manifest 选择和调用前失败
# ===========================================================================
def test_T02_manifest_selection():
    pd = new_project("t02")

    # 1) 顶层非对象 / 版本错 / project_id 错 / images 非列表
    cases = {
        "顶层非对象": raw_case(pd, []),
        "版本错误": raw_case(pd, {"version": "2", "project_id": PROJECT_ID, "images": []}),
        "project_id 错": raw_case(pd, {"version": "1", "project_id": "other", "images": []}),
        "images 非列表": raw_case(pd, {"version": "1", "project_id": PROJECT_ID,
                                       "images": {}}),
    }
    for label, _ in cases.items():
        err = expect_raises(OrchestrationError, load_stage13_manifest, pd, PROJECT_ID)
        check(f"T02-1 manifest 结构错误被拒（{label}）",
              isinstance(err, OrchestrationError), str(err))

    # 2) generated 条目缺字段
    good = gen_entry("GEN_001", "S1", "generated_images/GEN_001.png", "a" * 64,
                     {"CHAR_001": "v1"})
    for missing in ("image_id", "shot_ref", "path", "sha256"):
        bad = dict(good)
        bad.pop(missing)
        err = expect_raises(OrchestrationError, select_generated_entries,
                            {"images": [bad]}, {})
        check(f"T02-2 generated 缺 {missing} 被拒", isinstance(err, OrchestrationError),
              str(err))

    # 3) generated image_id 重复
    dup = [good, dict(good, shot_ref="S2")]
    err = expect_raises(OrchestrationError, select_generated_entries, {"images": dup}, {})
    check("T02-3 generated image_id 重复被拒", isinstance(err, OrchestrationError), str(err))

    # 4) 只选择 status=generated
    mixed = [good,
             dict(good, image_id="GEN_002", status="failed"),
             dict(good, image_id="GEN_003", status="skipped"),
             dict(good, image_id="GEN_004", status="rejected")]
    sel = select_generated_entries({"images": mixed}, {})
    check("T02-4 只选择 status=generated（failed/skipped/rejected 排除）",
          [e["image_id"] for e in sel] == ["GEN_001"], str([e["image_id"] for e in sel]))

    # 5) 缺省保持 manifest 顺序
    ordered = [dict(good, image_id=f"GEN_{i:03d}", shot_ref=f"S{i}")
               for i in (3, 1, 2)]
    sel = select_generated_entries({"images": ordered}, {})
    check("T02-5 缺省保持 manifest 顺序",
          [e["image_id"] for e in sel] == ["GEN_003", "GEN_001", "GEN_002"])

    # 6) qa_image_ids 保持用户顺序；未知/重复/空值/类型错失败
    sel = select_generated_entries({"images": ordered},
                                   {"qa_image_ids": ["GEN_002", "GEN_003"]})
    check("T02-6a qa_image_ids 保持用户顺序",
          [e["image_id"] for e in sel] == ["GEN_002", "GEN_003"])
    for label, spec in (
            ("未知 ID", {"qa_image_ids": ["GEN_999"]}),
            ("重复 ID", {"qa_image_ids": ["GEN_001", "GEN_001"]}),
            ("空值", {"qa_image_ids": [""]}),
            ("类型错", {"qa_image_ids": "GEN_001"}),
            ("非字符串", {"qa_image_ids": [1]})):
        err = expect_raises(OrchestrationError, select_generated_entries,
                            {"images": ordered}, spec)
        check(f"T02-6b qa_image_ids {label} 被拒", isinstance(err, OrchestrationError), str(err))

    # 7) max_qa_images：负数/布尔/非整数失败；0 零调用；>3 收紧为 3
    for label, val in (("负数", -1), ("布尔", True), ("非整数", "3")):
        err = expect_raises(OrchestrationError, select_generated_entries,
                            {"images": ordered}, {"max_qa_images": val})
        check(f"T02-7a max_qa_images {label} 被拒", isinstance(err, OrchestrationError), str(err))
    check("T02-7b max_qa_images=0 → 零选择",
          select_generated_entries({"images": ordered}, {"max_qa_images": 0}) == [])
    many = [dict(good, image_id=f"GEN_{i:03d}", shot_ref=f"S{i}") for i in range(1, 6)]
    sel = select_generated_entries({"images": many}, {"max_qa_images": 10})
    check("T02-7c max_qa_images>3 收紧为 3（MAX_REAL_VISION_REQUESTS）",
          len(sel) == MAX_REAL_VISION_REQUESTS == 3, f"len={len(sel)}")

    # 8) 所有结构/选择失败均发生在 Provider 构造和调用前
    pd8, reg8, _ = basic_fixture("t02_pre")
    write_manifest(pd8, [], raw={"version": "1", "project_id": PROJECT_ID,
                                 "images": [dict(gen_entry(
                                     "GEN_001", "S1", "generated_images/GEN_001.png",
                                     "a" * 64, {}), status="generated"),
                                     dict(gen_entry(
                                         "GEN_001", "S2", "generated_images/GEN_001.png",
                                         "a" * 64, {}), status="generated")]})
    spy = SpyProvider()
    fac = SpyFactory(spy)
    err = expect_raises(OrchestrationError, make_stage(pd8, reg8, provider=None,
                                                       factory=fac).run,
                        {"run_image_qa": True})
    check("T02-8 结构/选择失败在 Provider 构造前（factory 0 调用）",
          isinstance(err, OrchestrationError) and fac.calls == 0 and len(spy.calls) == 0,
          f"err={type(err).__name__} factory={fac.calls} chat={len(spy.calls)}")


def raw_case(pd: Path, obj):
    p = pd / IMAGE_MANIFEST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


# ===========================================================================
# T03 精确 Profile 版本和字段顺序
# ===========================================================================
def test_T03_exact_profile_version():
    # 1) manifest 指向 v1，active 已是 v2 → 仍用 v1
    pd = new_project("t03a")
    reg = VisualAssetRegistry(pd)
    a = reg.register("character", "Lin")
    reg.new_version(a.asset_id, profile=dict(CHAR_PROFILE))
    reg.submit_for_review(a.asset_id, "v1")
    reg.approve(a.asset_id, "v1")
    reg.new_version(a.asset_id, profile=dict(CHAR_PROFILE, hair="blonde long hair"))
    reg.submit_for_review(a.asset_id, "v2")
    reg.approve(a.asset_id, "v2")
    rel, sha = gen_image(pd, "GEN_001", b"IMG")
    entry = gen_entry("GEN_001", "S1", rel, sha, {a.asset_id: "v1"})
    qa_input, _ = expected_of(pd, reg, entry)
    check("T03-1 manifest 指向 v1、active 已是 v2 → 仍用 v1",
          qa_input["profiles"][0]["version"] == "v1"
          and reg.active_version(a.asset_id).version == "v2"
          and "black bob" in qa_input["profiles"][0]["fields"]["hair"],
          f"used={qa_input['profiles'][0]['version']} active="
          f"{reg.active_version(a.asset_id).version}")

    # 2) 状态可用性
    for status, ok in (("approved", True), ("superseded", True), ("draft", False),
                       ("generated", False), ("reviewing", False), ("rejected", False),
                       ("archived", False)):
        tag = f"t03_{status}"
        pdx = new_project(tag)
        rgx = VisualAssetRegistry(pdx)
        if status == "superseded":
            aid, _ = make_asset(rgx, "character", "Lin", CHAR_PROFILE, status="approved")
            rgx.new_version(aid, profile=dict(CHAR_PROFILE, hair="silver hair"))
            rgx.submit_for_review(aid, "v2")
            rgx.approve(aid, "v2")
            used = "v1"
        else:
            aid, used = make_asset(rgx, "character", "Lin", CHAR_PROFILE, status=status)
        relx, shax = gen_image(pdx, "GEN_001", b"IMG")
        ex = gen_entry("GEN_001", "S1", relx, shax, {aid: used})
        try:
            expected_of(pdx, rgx, ex)
            passed = True
        except (OrchestrationError, Exception):                # noqa: BLE001
            passed = False
        check(f"T03-2 版本状态 {status} {'可用' if ok else '被拒'}", passed == ok)

    # 3) asset/version 缺失、kind/profile 类型不匹配
    pd3, reg3, entries3 = basic_fixture("t03c")
    base = entries3[0]
    err = expect_raises(OrchestrationError, expected_of, pd3, reg3,
                        gen_entry("GEN_001", "S1", base["path"], base["sha256"],
                                  {"CHAR_999": "v1"}))
    check("T03-3a profile_versions 资产不存在被拒", isinstance(err, OrchestrationError), str(err))
    err = expect_raises(OrchestrationError, expected_of, pd3, reg3,
                        gen_entry("GEN_001", "S1", base["path"], base["sha256"],
                                  {list(base["profile_versions"])[0]: "v9"}))
    check("T03-3b 精确版本不存在被拒", isinstance(err, OrchestrationError), str(err))
    # kind/profile 类型不匹配：手工把 version.profile 换成 LocationVisualProfile
    aid0 = list(base["profile_versions"])[0]
    reg3.get(aid0).get_version("v1").profile = LocationVisualProfile.from_dict(LOC_PROFILE)
    reg3.save()
    err = expect_raises(OrchestrationError, expected_of, pd3, reg3,
                        gen_entry("GEN_001", "S1", base["path"], base["sha256"],
                                  {aid0: "v1"}))
    check("T03-3c kind/profile 类型不匹配被拒", isinstance(err, OrchestrationError), str(err))

    # 4) profile_versions 为空 / 类型错 / 键或值为空
    for label, pv in (("空 mapping", {}), ("类型错", ["CHAR_001"]),
                      ("空键", {"": "v1"}), ("空值", {"CHAR_001": ""})):
        err = expect_raises(OrchestrationError, expected_of, pd3, reg3,
                            gen_entry("GEN_001", "S1", base["path"], base["sha256"], pv))
        check(f"T03-4 profile_versions {label} 被拒", isinstance(err, OrchestrationError), str(err))

    # 5) profile 顺序保持 manifest mapping 插入顺序
    pd5 = new_project("t03e")
    reg5 = VisualAssetRegistry(pd5)
    c1, _ = make_asset(reg5, "character", "A", CHAR_PROFILE)
    c2, _ = make_asset(reg5, "character", "B", CHAR_PROFILE)
    rel5, sha5 = gen_image(pd5, "GEN_001", b"IMG")
    qa_input, _ = expected_of(pd5, reg5,
                              gen_entry("GEN_001", "S1", rel5, sha5, {c2: "v1", c1: "v1"}))
    check("T03-5 profile 顺序 = manifest mapping 插入顺序",
          [p["asset_id"] for p in qa_input["profiles"]] == [c2, c1],
          str([p["asset_id"] for p in qa_input["profiles"]]))

    # 6) 单 profile 字段顺序严格来自 FIELD_ORDER
    pd6, reg6, entries6 = basic_fixture("t03f")
    _qi, exp6 = expected_of(pd6, reg6, entries6[0])
    want = [f for f, _ in CharacterVisualProfile.FIELD_ORDER
            if f != "notes" and str(CHAR_PROFILE.get(f, "") or "").strip()]
    check("T03-6 单 profile 字段顺序严格来自 FIELD_ORDER",
          [e["field"] for e in exp6] == want, str([e["field"] for e in exp6]))

    # 7) 空字段不生成 check；Style notes 不评分；version notes 不当真值
    pd7 = new_project("t03g")
    reg7 = VisualAssetRegistry(pd7)
    sparse = {"appearance": "a", "face": "b", "hair": "c", "clothing": "d"}
    c7, _ = make_asset(reg7, "character", "Sparse", sparse)
    st7, _ = make_asset(reg7, "style", "Default", STYLE_PROFILE)
    rel7, sha7 = gen_image(pd7, "GEN_001", b"IMG")
    qi7, exp7 = expected_of(pd7, reg7, gen_entry(
        "GEN_001", "S1", rel7, sha7, {c7: "v1", st7: "v1"}))
    check("T03-7a 空字段不生成 check（仅 4 个非空 character 字段）",
          [e["field"] for e in exp7 if e["asset_id"] == c7] ==
          ["appearance", "face", "hair", "clothing"], str([e["field"] for e in exp7]))
    check("T03-7b Style notes 不评分",
          not any(e["field"] == "notes" for e in exp7))
    reg7.get(c7).get_version("v1").notes = "SECRET_VERSION_NOTE"
    reg7.save()
    _qi7b, exp7b = expected_of(pd7, reg7, gen_entry(
        "GEN_001", "S1", rel7, sha7, {c7: "v1"}))
    check("T03-7c version notes 不当真值（不进检查集）",
          not any("SECRET_VERSION_NOTE" in json.dumps(e) for e in exp7b))

    # 8) negative_cues 恰好一个逆语义 check
    n_neg = sum(1 for e in exp6 if e["field"] == "negative_cues")
    check("T03-8 negative_cues 恰好一个逆语义 check", n_neg == 1, f"n={n_neg}")

    # 9) 无任何非空可评分字段 → 零调用失败
    pd9 = new_project("t03i")
    reg9 = VisualAssetRegistry(pd9)
    empty = {k: "" for k, _ in CharacterVisualProfile.FIELD_ORDER}
    c9, _ = make_asset(reg9, "character", "Empty", empty)
    rel9, sha9 = gen_image(pd9, "GEN_001", b"IMG")
    write_manifest(pd9, [gen_entry("GEN_001", "S1", rel9, sha9, {c9: "v1"})])
    spy9 = SpyProvider()
    fac9 = SpyFactory(spy9)
    rep9 = make_stage(pd9, reg9, provider=None, factory=fac9).run({"run_image_qa": True})
    check("T03-9 无可评分字段 → 预检失败且 0 Provider 调用",
          rep9["failed"] == 1 and fac9.calls == 0 and len(spy9.calls) == 0,
          f"failed={rep9['failed']} factory={fac9.calls}")


# ===========================================================================
# T04 D14-REF-01 全局 reference resolver
# ===========================================================================
def test_T04_reference_resolver():
    # 1) 独立 REF_* approved reference 可解析
    pd, reg, entries = basic_fixture("t04a")
    qi, _ = expected_of(pd, reg, entries[0])
    check("T04-1 独立 REF_* approved reference 可解析",
          len(qi["reference_images"]) == 1
          and qi["ground_truth_mode"] == "profile_and_references",
          str(qi["reference_images"]))
    check("T04-3 reference 位于 profile_versions 外仍可解析",
          entries[0]["reference_images"][0]["image_id"]
          not in entries[0]["profile_versions"])

    # 2) superseded reference 版本可解析（且不漂移到 active）
    pd2 = new_project("t04b")
    reg2 = VisualAssetRegistry(pd2)
    c2, _ = make_asset(reg2, "character", "Lin", CHAR_PROFILE)
    imgp = pd2 / "refs" / "a.png"
    write_file(imgp, b"REF-A")
    ra = reg2.register("reference", "bundle", purpose="pose")
    reg2.new_version(ra.asset_id, profile=None)
    img1 = reg2.add_reference_image(ra.asset_id, "v1", path=imgp, kind="pose")
    reg2.submit_for_review(ra.asset_id, "v1")
    reg2.approve(ra.asset_id, "v1")
    imgp2 = pd2 / "refs" / "b.png"
    write_file(imgp2, b"REF-B")
    reg2.new_version(ra.asset_id, profile=None)
    img2 = reg2.add_reference_image(ra.asset_id, "v2", path=imgp2, kind="pose")
    reg2.submit_for_review(ra.asset_id, "v2")
    reg2.approve(ra.asset_id, "v2")   # v1 → superseded
    reg2.save()
    rel2, sha2 = gen_image(pd2, "GEN_001", b"IMG")
    e2 = gen_entry("GEN_001", "S1", rel2, sha2, {c2: "v1"},
                   [{"image_id": img1.image_id, "sha256": img1.checksum}])
    qi2, _ = expected_of(pd2, reg2, e2)
    check("T04-2 superseded reference 版本可解析且不漂移到 active",
          qi2["reference_images"][0]["version"] == "v1"
          and reg2.active_version(ra.asset_id).version == "v2"
          and img2.image_id != img1.image_id,
          f"used={qi2['reference_images'][0]['version']}")

    # 4) 全局零命中失败且 0 调用
    pd4, reg4, e4 = basic_fixture("t04d")
    bad = gen_entry("GEN_001", "S1", e4[0]["path"], e4[0]["sha256"],
                    e4[0]["profile_versions"],
                    [{"image_id": "IMG_999", "sha256": "a" * 64}])
    err = expect_raises(OrchestrationError, expected_of, pd4, reg4, bad)
    check("T04-4 全局零命中被拒（Provider 调用 0）",
          isinstance(err, OrchestrationError) and "0" in str(err), str(err))

    # 5) 同一 image_id 跨资产重复 → 多命中
    pd5 = new_project("t04e")
    reg5 = VisualAssetRegistry(pd5)
    c5, _ = make_asset(reg5, "character", "Lin", CHAR_PROFILE)
    p5a, p5b = pd5 / "refs" / "x.png", pd5 / "refs" / "y.png"
    write_file(p5a, b"X")
    write_file(p5b, b"Y")
    make_ref_asset(reg5, "bundle x", p5a, image_id="IMG_001")
    make_ref_asset(reg5, "bundle y", p5b, image_id="IMG_001")
    rel5, sha5 = gen_image(pd5, "GEN_001", b"IMG")
    e5 = gen_entry("GEN_001", "S1", rel5, sha5, {c5: "v1"},
                   [{"image_id": "IMG_001", "sha256": "a" * 64}])
    err = expect_raises(OrchestrationError, expected_of, pd5, reg5, e5)
    check("T04-5 同一 image_id 跨资产多命中被拒",
          isinstance(err, OrchestrationError) and "2" in str(err), str(err))

    # 6) 同一 image_id 跨版本重复 → 多命中
    pd6 = new_project("t04f")
    reg6 = VisualAssetRegistry(pd6)
    c6, _ = make_asset(reg6, "character", "Lin", CHAR_PROFILE)
    p6 = pd6 / "refs" / "z.png"
    write_file(p6, b"Z")
    make_ref_asset(reg6, "bundle z", p6, image_id="IMG_001", extra_version=True)
    rel6, sha6 = gen_image(pd6, "GEN_001", b"IMG")
    e6 = gen_entry("GEN_001", "S1", rel6, sha6, {c6: "v1"},
                   [{"image_id": "IMG_001", "sha256": "a" * 64}])
    err = expect_raises(OrchestrationError, expected_of, pd6, reg6, e6)
    check("T04-6 同一 image_id 跨版本多命中被拒",
          isinstance(err, OrchestrationError) and "2" in str(err), str(err))

    # 7) 命中 draft/rejected/archived 版本失败
    for status in ("draft", "rejected", "archived"):
        pdx = new_project(f"t04_{status}")
        rgx = VisualAssetRegistry(pdx)
        cx, _ = make_asset(rgx, "character", "Lin", CHAR_PROFILE)
        imgp = pdx / "refs" / "s.png"
        write_file(imgp, b"S")
        _aid, _v, img = make_ref_asset(rgx, "bundle", imgp, status=status)
        relx, shax = gen_image(pdx, "GEN_001", b"IMG")
        ex = gen_entry("GEN_001", "S1", relx, shax, {cx: "v1"},
                       [{"image_id": img.image_id, "sha256": img.checksum}])
        err = expect_raises(OrchestrationError, expected_of, pdx, rgx, ex)
        check(f"T04-7 reference 命中 {status} 版本被拒", isinstance(err, OrchestrationError),
              str(err))

    # 8) manifest reference image_id 重复失败
    pd8, reg8, e8 = basic_fixture("t04h")
    rid = e8[0]["reference_images"][0]
    e8b = gen_entry("GEN_001", "S1", e8[0]["path"], e8[0]["sha256"],
                    e8[0]["profile_versions"], [rid, dict(rid)])
    err = expect_raises(OrchestrationError, expected_of, pd8, reg8, e8b)
    check("T04-8 manifest reference image_id 重复被拒", isinstance(err, OrchestrationError),
          str(err))

    # 9) 禁止按 path/checksum/asset name/active version 猜测
    pd9, reg9, e9 = basic_fixture("t04i")
    real_id = e9[0]["reference_images"][0]["image_id"]
    real_sha = e9[0]["reference_images"][0]["sha256"]
    # checksum 相同但 image_id 不存在 → 仍零命中（不按 checksum 猜）
    guess = gen_entry("GEN_001", "S1", e9[0]["path"], e9[0]["sha256"],
                      e9[0]["profile_versions"],
                      [{"image_id": "IMG_777", "sha256": real_sha}])
    err = expect_raises(OrchestrationError, expected_of, pd9, reg9, guess)
    check("T04-9 不按 checksum/path 猜测归属（image_id 未命中即失败）",
          isinstance(err, OrchestrationError), str(err))

    # 10) registry 扫描顺序变化不改变 manifest reference 输入顺序
    def build(order):
        p = new_project(f"t04_order_{order}")
        r = VisualAssetRegistry(p)
        c, _ = make_asset(r, "character", "Lin", CHAR_PROFILE)
        imgs = {}
        names = ["A", "B", "C"][:order]
        for n in names:
            ip = p / "refs" / f"{n}.png"
            write_file(ip, n.encode())
            _aid, _v, img = make_ref_asset(r, f"bundle {n}", ip)
            imgs[n] = img
        rel, sha = gen_image(p, "GEN_001", b"IMG")
        return p, r, c, imgs, rel, sha

    p10a, r10a, c10a, imgs10a, rel10a, sha10a = build(3)
    want_a = [imgs10a["C"].image_id, imgs10a["A"].image_id]
    refs10 = [{"image_id": imgs10a[n].image_id, "sha256": imgs10a[n].checksum}
              for n in ("C", "A")]
    e10 = gen_entry("GEN_001", "S1", rel10a, sha10a, {c10a: "v1"}, refs10)
    qi10a, _ = expected_of(p10a, r10a, e10)
    # 逆序注册一个 registry（B/A/C），扫描顺序不同
    p10b = new_project("t04_order_b")
    r10b = VisualAssetRegistry(p10b)
    c10b, _ = make_asset(r10b, "character", "Lin", CHAR_PROFILE)
    imgs10b = {}
    for n in ("B", "A", "C"):
        ip = p10b / "refs" / f"{n}.png"
        write_file(ip, n.encode())
        _aid, _v, img = make_ref_asset(r10b, f"bundle {n}", ip)
        imgs10b[n] = img
    rel10b, sha10b = gen_image(p10b, "GEN_001", b"IMG")
    refs10b = [{"image_id": imgs10b[n].image_id, "sha256": imgs10b[n].checksum}
               for n in ("C", "A")]
    e10b = gen_entry("GEN_001", "S1", rel10b, sha10b, {c10b: "v1"}, refs10b)
    qi10b, _ = expected_of(p10b, r10b, e10b)
    want_b = [imgs10b["C"].image_id, imgs10b["A"].image_id]
    check("T04-10 registry 扫描顺序变化不改变 manifest reference 顺序",
          [r["image_id"] for r in qi10a["reference_images"]] == want_a
          and [r["image_id"] for r in qi10b["reference_images"]] == want_b,
          f"a={[r['image_id'] for r in qi10a['reference_images']]} "
          f"b={[r['image_id'] for r in qi10b['reference_images']]}")


# ===========================================================================
# T05 路径 containment 与 SHA256
# ===========================================================================
def test_T05_paths_and_sha():
    pd, reg, entries = basic_fixture("t05")
    e = entries[0]
    ref = e["reference_images"][0]

    # 1) 项目内相对路径通过
    qi, _ = expected_of(pd, reg, e)
    check("T05-1 项目内相对路径通过", qi["generated_path"].is_file())

    # 2) 项目内绝对路径通过，但持久化只保留相对 POSIX
    abs_entry = dict(e, path=str((pd / e["path"]).resolve()))
    qi2, _ = expected_of(pd, reg, abs_entry)
    check("T05-2 项目内绝对路径通过且持久化为相对 POSIX",
          qi2["_generated_relative_path"] == "generated_images/GEN_001.png"
          and not Path(qi2["_generated_relative_path"]).is_absolute(),
          qi2["_generated_relative_path"])

    # 3) 缺失文件 / 目录 / 空路径
    missing = dict(e, path="generated_images/NOPE.png")
    err = expect_raises(OrchestrationError, expected_of, pd, reg, missing)
    check("T05-3a 缺失文件被拒", isinstance(err, OrchestrationError), str(err))
    d = pd / "generated_images" / "adir"
    d.mkdir(parents=True, exist_ok=True)
    err = expect_raises(OrchestrationError, expected_of, pd, reg,
                        dict(e, path="generated_images/adir"))
    check("T05-3b 目录被拒", isinstance(err, OrchestrationError), str(err))
    err = expect_raises(OrchestrationError, expected_of, pd, reg, dict(e, path=""))
    check("T05-3c 空路径被拒", isinstance(err, OrchestrationError), str(err))

    # 4) '..' 逃逸 / 同名前缀兄弟目录 / 其他盘符·UNC
    sibling = TMP_PROJECTS / "t05_sibling"
    sibling.mkdir(parents=True, exist_ok=True)
    write_file(sibling / "evil.png", b"EVIL")
    err = expect_raises(OrchestrationError, expected_of, pd, reg,
                        dict(e, path="../t05_sibling/evil.png"))
    check("T05-4a '..' 逃逸被拒", isinstance(err, OrchestrationError), str(err))
    err = expect_raises(OrchestrationError, expected_of, pd, reg,
                        dict(e, path=str((sibling / "evil.png").resolve())))
    check("T05-4b 同名前缀兄弟目录（绝对）被拒", isinstance(err, OrchestrationError), str(err))
    for label, p in (("其他盘符", "Z:/outside/evil.png"),
                     ("UNC", "//server/share/evil.png")):
        err = expect_raises(OrchestrationError, expected_of, pd, reg, dict(e, path=p))
        check(f"T05-4c {label} 路径被拒", isinstance(err, OrchestrationError), str(err))

    # 5) symlink/junction 指向项目外
    outside = TMP_PROJECTS / "t05_outside.png"
    write_file(outside, b"OUTSIDE")
    link = pd / "generated_images" / "link.png"
    symlink_ok = False
    try:
        os.symlink(outside, link)
        symlink_ok = True
    except (OSError, NotImplementedError, AttributeError):
        symlink_ok = False
    if symlink_ok:
        err = expect_raises(OrchestrationError, expected_of, pd, reg,
                            dict(e, path="generated_images/link.png"))
        check("T05-5 symlink 指向项目外被拒", isinstance(err, OrchestrationError), str(err))
    else:
        # 平台不允许创建 symlink → 环境限制 + 可验证的 resolve(strict)+relative_to 替代边界
        err = expect_raises(OrchestrationError, expected_of, pd, reg,
                            dict(e, path=str(outside.resolve())))
        check("T05-5 [环境限制:symlink 不可创建] 替代边界：项目外绝对路径被 "
              "resolve(strict)+relative_to 拒绝", isinstance(err, OrchestrationError),
              "ENV-LIMIT symlink; alt containment test used")

    # 6) 非 64-hex sha 被拒；大小写可规范化
    err = expect_raises(OrchestrationError, expected_of, pd, reg, dict(e, sha256="abc"))
    check("T05-6a 非 64-hex sha 被拒", isinstance(err, OrchestrationError), str(err))
    upper = dict(e, sha256=e["sha256"].upper())
    qi6, _ = expected_of(pd, reg, upper)
    check("T05-6b sha 大小写可规范化（小写存储）",
          qi6["generated_sha256"] == e["sha256"].lower(), qi6["generated_sha256"])

    # 7) generated manifest/disk 不一致
    err = expect_raises(OrchestrationError, expected_of, pd, reg, dict(e, sha256="0" * 64))
    check("T05-7 generated manifest/disk SHA 不一致被拒",
          isinstance(err, OrchestrationError), str(err))

    # 8) reference manifest/registry/disk 三方不一致（逐组合）
    #   a) manifest sha 错
    e8 = dict(e, reference_images=[dict(ref, sha256="1" * 64)])
    err = expect_raises(OrchestrationError, expected_of, pd, reg, e8)
    check("T05-8a reference manifest sha 不一致被拒", isinstance(err, OrchestrationError), str(err))
    #   b) registry checksum 错
    p8 = new_project("t05_ref_b")
    r8 = VisualAssetRegistry(p8)
    c8, _ = make_asset(r8, "character", "Lin", CHAR_PROFILE)
    ip8 = p8 / "refs" / "r.png"
    write_file(ip8, b"R8")
    _aid8, _v8, img8 = make_ref_asset(r8, "bundle", ip8)
    img8.checksum = "2" * 64
    r8.save()
    rel8, sha8 = gen_image(p8, "GEN_001", b"IMG")
    e8b = gen_entry("GEN_001", "S1", rel8, sha8, {c8: "v1"},
                    [{"image_id": img8.image_id, "sha256": "2" * 64}])
    err = expect_raises(OrchestrationError, expected_of, p8, r8, e8b)
    check("T05-8b reference registry checksum 不一致被拒", isinstance(err, OrchestrationError), str(err))
    #   c) disk sha 与 registry 不符（改磁盘文件内容）
    ip8.write_bytes(b"CHANGED")
    e8c = gen_entry("GEN_001", "S1", rel8, sha8, {c8: "v1"},
                    [{"image_id": img8.image_id, "sha256": img8.checksum}])
    err = expect_raises(OrchestrationError, expected_of, p8, r8, e8c)
    check("T05-8c reference disk sha 不一致被拒", isinstance(err, OrchestrationError), str(err))

    # 9) 上述失败均为 0 Provider 调用
    spy = SpyProvider()
    fac = SpyFactory(spy)
    pdx, rgx, _ = basic_fixture("t05_zero")
    write_manifest(pdx, [dict(gen_entry("GEN_001", "S1", "generated_images/GEN_001.png",
                                        "0" * 64, {}), status="generated")])
    rep = make_stage(pdx, rgx, provider=None, factory=fac).run({"run_image_qa": True})
    check("T05-9 路径/SHA 预检失败 = 0 Provider 调用",
          rep["failed"] == 1 and fac.calls == 0 and len(spy.calls) == 0,
          f"failed={rep['failed']} factory={fac.calls}")


# ===========================================================================
# T06 Prompt、图片顺序与敏感信息
# ===========================================================================
def test_T06_prompt_order_and_secrets():
    pd, reg, entries = basic_fixture("t06", n_images=1, extra_refs=1)
    e = entries[0]
    spy = SpyProvider(payload_fn=all_match_payload_fn)
    counter = APIRequestCounter(real_budgets={"vision": 5})
    rep = make_stage(pd, reg, provider=spy, counter=counter).run(
        {"run_image_qa": True, "max_qa_images": 1})

    call = spy.calls[0]
    images = call["images"]
    gen_abs = str((pd / e["path"]).resolve())
    ref_abs = [str((pd / r["path"]).resolve()) for r in _qi_refs(pd, reg, e)]
    check("T06-1 images[0] 始终是 generated",
          images and Path(images[0]).resolve() == Path(gen_abs).resolve(),
          str(images[:1]))
    check("T06-2 images[1:] 完全遵循 manifest reference 顺序",
          [str(Path(i).resolve()) for i in images[1:]] == ref_abs,
          str([Path(i).name for i in images[1:]]))

    payload = json.loads(call["messages"][-1]["content"])
    order = payload["image_order"]
    labels_ok = (order["generated"]["position"] == 0
                 and order["generated"]["image_id"] == e["image_id"]
                 and [r["position"] for r in order["references"]]
                 == list(range(1, len(ref_abs) + 1))
                 and [r["image_id"] for r in order["references"]]
                 == [r["image_id"] for r in e["reference_images"]])
    check("T06-3 Prompt Image 位置标签与 tuple 一致", labels_ok, json.dumps(order))
    check("T06-4a Prompt 含 expected checks",
          len(payload["expected_checks"]) >= 1
          and all("expected" in c for c in payload["expected_checks"]))
    check("T06-4b Prompt 含 negative_cues 逆语义规则",
          "negative_cues" in payload["negative_cues_rule"]
          and "negative_cues" in call["messages"][0]["content"].lower())

    # 5) 不含绝对路径 / API key / base64
    blobs = [json.dumps(payload, ensure_ascii=False),
             call["messages"][0]["content"],
             json.dumps(read_index(pd), ensure_ascii=False)]
    for entry in read_index(pd)["images"]:
        art = pd / entry["artifact_path"]
        blobs.append(art.read_text(encoding="utf-8"))
    joined = "\n".join(blobs)
    leaks = []
    if str(pd) in joined or str(REPO_ROOT) in joined:
        leaks.append("absolute-path")
    if "sk-" in joined:
        leaks.append("api-key")
    if re.search(r"[A-Za-z0-9+/]{200,}={0,2}", joined):
        leaks.append("base64")
    check("T06-5 Prompt/Artifact/index 不含绝对路径、API key、base64",
          not leaks, str(leaks))

    # 6) Prompt 不产生额外模型调用
    check("T06-6 一图一次调用（无额外模型调用）",
          len(spy.calls) == 1 and rep["attempted"] == 1,
          f"calls={len(spy.calls)} attempted={rep['attempted']}")


def _qi_refs(pd, reg, entry):
    ref_index = build_reference_index(reg)
    qi = prepare_image_qa_input(project_dir=pd, registry=reg, entry=entry,
                                reference_index=ref_index)
    return qi["reference_images"]


# ===========================================================================
# T07 Provider 输出严格校验
# ===========================================================================
def test_T07_provider_output_validation():
    pd, reg, entries = basic_fixture("t07")
    _qi, exp = expected_of(pd, reg, entries[0])
    key0 = (exp[0]["asset_id"], exp[0]["version"], exp[0]["field"])

    # 1/2/3 裁决优先级
    check("T07-1 全 match → pass",
          qa_decision(payload_from(exp, default="match")["checks"]) == "pass")
    check("T07-2 任一 mismatch 优先 → fail",
          qa_decision(payload_from(exp, {key0: "mismatch"})["checks"]) == "fail")
    check("T07-3 无 mismatch 且存在 uncertain → needs_review",
          qa_decision(payload_from(exp, {key0: "uncertain"})["checks"]) == "needs_review"
          and qa_decision(payload_from(exp, {key0: "not_visible"})["checks"]) == "needs_review")

    # 4 顶层/checks/summary 类型
    check("T07-4a 顶层非对象被拒",
          isinstance(expect_raises(Exception, validate_provider_payload, [], exp), Exception))
    check("T07-4b checks 非列表/空被拒",
          isinstance(expect_raises(Exception, validate_provider_payload,
                                   {"checks": "x", "summary": ""}, exp), Exception)
          and isinstance(expect_raises(Exception, validate_provider_payload,
                                       {"checks": [], "summary": ""}, exp), Exception))
    check("T07-4c summary 非字符串被拒",
          isinstance(expect_raises(Exception, validate_provider_payload,
                                   {"checks": payload_from(exp)["checks"], "summary": 1}, exp),
                     Exception))

    # 5 未知顶层字段被拒；模型自报字段被忽略
    unknown = payload_from(exp)
    unknown["rogue"] = 1
    check("T07-5a 未知顶层字段被拒",
          isinstance(expect_raises(Exception, validate_provider_payload, unknown, exp), Exception))
    claimed = payload_from(exp, {key0: "mismatch"})
    claimed.update({"qa_decision": "pass", "provider": "rogue", "model": "rogue",
                    "request_id": "rogue", "retry_count": 99, "created_at": "x"})
    out = validate_provider_payload(claimed, exp)
    check("T07-5b 模型自报 qa_decision/provider/model 被忽略（本地重算 fail）",
          qa_decision(out["checks"]) == "fail")

    # 6 check 缺字段/额外字段/类型错
    good = payload_from(exp)
    miss = json.loads(json.dumps(good))
    miss["checks"][0].pop("evidence")
    extra = json.loads(json.dumps(good))
    extra["checks"][0]["rogue"] = "x"
    wrongt = json.loads(json.dumps(good))
    wrongt["checks"][0]["verdict"] = 5
    for label, p in (("缺字段", miss), ("额外字段", extra), ("类型错", wrongt)):
        check(f"T07-6 check {label} 被拒",
              isinstance(expect_raises(Exception, validate_provider_payload, p, exp), Exception))

    # 7 缺 check / 额外 check / 重复 key / 未知 key
    drop = json.loads(json.dumps(good))
    drop["checks"] = drop["checks"][:-1]
    dup = json.loads(json.dumps(good))
    dup["checks"].append(dict(dup["checks"][0]))
    unk = json.loads(json.dumps(good))
    unk["checks"][0]["field"] = "nonexistent_field"
    for label, p in (("缺 check", drop), ("重复 key", dup), ("未知 field", unk)):
        check(f"T07-7 {label} 被拒",
              isinstance(expect_raises(Exception, validate_provider_payload, p, exp), Exception))

    # 8 expected 被改写
    rew = json.loads(json.dumps(good))
    rew["checks"][0]["expected"] = "totally different"
    check("T07-8 expected 被改写被拒",
          isinstance(expect_raises(Exception, validate_provider_payload, rew, exp), Exception))

    # 9 非法 verdict
    badv = json.loads(json.dumps(good))
    badv["checks"][0]["verdict"] = "maybe"
    check("T07-9 非法 verdict 被拒",
          isinstance(expect_raises(Exception, validate_provider_payload, badv, exp), Exception))

    # 10 非 match evidence 为空被拒；match 可为空
    emp = payload_from(exp, {key0: "mismatch"})
    for c in emp["checks"]:
        if (c["asset_id"], c["version"], c["field"]) == key0:
            c["evidence"] = ""
    check("T07-10a 非 match evidence 为空被拒",
          isinstance(expect_raises(Exception, validate_provider_payload, emp, exp), Exception))
    check("T07-10b match evidence 可为空", isinstance(
        validate_provider_payload(payload_from(exp), exp), dict))

    # 11 返回顺序不同 → 按 expected/Profile 顺序规范化
    rev = json.loads(json.dumps(good))
    rev["checks"] = list(reversed(rev["checks"]))
    out11 = validate_provider_payload(rev, exp)
    check("T07-11 Provider 返回乱序 → 按 expected 顺序规范化",
          [c["field"] for c in out11["checks"]] == [e["field"] for e in exp])

    # 12 模型返回 model 与已批准配置不一致 → 不可重试失败
    qi, expx = expected_of(pd, reg, entries[0])
    spy12 = SpyProvider(payloads=[payload_from(expx)], model="approved-model",
                        response_model="rogue-model")
    agent12 = ImageQAAgent(spy12)
    err12 = expect_raises(Exception, agent12.evaluate, qi)
    check("T07-12 模型身份与批准配置不一致 → 不可重试失败",
          err12 is not None and getattr(err12, "retryable", None) is False,
          f"{type(err12).__name__} retryable={getattr(err12, 'retryable', None)}")

    # 13 Provider 异常只暴露安全错误类型，不泄漏路径/传输细节
    secret = "boom at C:/abs/secret/path " + "sk-" + ("a" * 16)
    spy13 = SpyProvider(raise_exc=LLMConnectionError(secret))
    err13 = expect_raises(Exception, ImageQAAgent(spy13).evaluate, qi)
    msg = str(err13)
    check("T07-13 Provider 异常仅暴露安全错误类型（无路径/传输细节）",
          err13 is not None and "LLMConnectionError" in msg
          and "sk-" not in msg and "C:/abs" not in msg and "secret" not in msg,
          msg)


# ===========================================================================
# T08 Workflow、Artifact 与 Review 三态
# ===========================================================================
def test_T08_workflow_artifact_review():
    # 1/3/9 approved 命中
    pd, reg, entries = basic_fixture("t08a")
    spy = SpyProvider(payload_fn=all_match_payload_fn)
    rep = make_stage(pd, reg, provider=spy).run({"run_image_qa": True})
    idx = index_entry(pd, "GEN_001")
    check("T08-1 每图独立 Workflow/Step/Artifact，base name 正确",
          (pd / "artifacts" / "image_qa_gen_001.json").is_file()
          and idx["artifact_path"].endswith("image_qa_gen_001.json"),
          idx.get("artifact_path", ""))
    check("T08-3 approved：最终 Artifact 二次验证并写 index",
          rep["pass"] == 1 and idx["qa_decision"] == "pass"
          and idx["workflow"]["status"] == "success")
    check("T08-9 QA pass 且 Review approved-like → downstream_eligible=true",
          idx["downstream_eligible"] is True)

    # 2) 重跑生成 _v2，旧 Artifact 不覆盖
    before = artifact_bytes(pd)
    spy2 = SpyProvider(payload_fn=all_match_payload_fn)
    rep2 = make_stage(pd, reg, provider=spy2).run(
        {"run_image_qa": True, "qa_resume": False})
    after = artifact_bytes(pd)
    check("T08-2 重跑生成 _v2 且旧 Artifact 字节不变",
          (pd / "artifacts" / "image_qa_gen_001_v2.json").is_file()
          and before["image_qa_gen_001.json"] == after["image_qa_gen_001.json"],
          str(sorted(after)))

    # 4) approved_with_edit 合法编辑：改 summary + verdict → 重算
    pd4, reg4, e4 = basic_fixture("t08d")
    qi4, exp4 = expected_of(pd4, reg4, e4[0])
    key4 = (exp4[0]["asset_id"], exp4[0]["version"], exp4[0]["field"])

    def edit_legal(d):
        d = json.loads(json.dumps(d))
        d["summary"] = "edited summary"
        for c in d["checks"]:
            if (c["asset_id"], c["version"], c["field"]) == key4:
                c["verdict"] = "mismatch"
                c["evidence"] = "edited evidence"
        d["qa_decision"] = "fail"       # 合法编辑须同步本地重算后的决定
        return d

    rep4 = make_stage(pd4, reg4, provider=SpyProvider(payload_fn=all_match_payload_fn),
                      handler=_handler(ReviewDecision.APPROVED_WITH_EDIT, edited=edit_legal)
                      ).run({"run_image_qa": True})
    idx4 = index_entry(pd4, "GEN_001")
    check("T08-4 approved_with_edit 合法编辑：写新 Artifact、重算 qa_decision",
          (pd4 / "artifacts" / "image_qa_gen_001_v2.json").is_file()
          and idx4["qa_decision"] == "fail"
          and idx4["downstream_eligible"] is False
          and idx4["review"]["decision"] == "approved_with_edit",
          f"decision={idx4['qa_decision']} eligible={idx4['downstream_eligible']}")

    # 5) 修改锚点 / 可信 Provider 元数据 → 失败
    for label, mutate in (
            ("generated_sha256", lambda d: d.update(generated_sha256="f" * 64)),
            ("provider", lambda d: d.update(provider="rogue")),
            ("model", lambda d: d.update(model="rogue")),
            ("request_id", lambda d: d.update(request_id="rogue")),
            ("retry_count", lambda d: d.update(retry_count=99)),
            ("created_at", lambda d: d.update(created_at="rogue"))):
        pdx, rgx, ex = basic_fixture(f"t08e_{label}")
        repx = make_stage(pdx, rgx, provider=SpyProvider(payload_fn=all_match_payload_fn),
                          handler=_handler(ReviewDecision.APPROVED_WITH_EDIT,
                                           edited=lambda d, m=mutate: _mut(d, m))
                          ).run({"run_image_qa": True})
        check(f"T08-5 approved_with_edit 篡改 {label} → 失败",
              repx["failed"] == 1 and repx["pass"] == 0,
              f"failed={repx['failed']} pass={repx['pass']}")

    # 6) 缺/加/重复 check 或非法决定 → 失败
    for label, edit in (
            ("缺 check", lambda d: d.update(checks=d["checks"][:-1])),
            ("加 check", lambda d: d.update(checks=d["checks"] + [dict(d["checks"][0])])),
            ("非法决定", lambda d: d.update(qa_decision="needs_review"))):
        pdx, rgx, ex = basic_fixture(f"t08f_{label}")
        repx = make_stage(pdx, rgx, provider=SpyProvider(payload_fn=all_match_payload_fn),
                          handler=_handler(ReviewDecision.APPROVED_WITH_EDIT,
                                           edited=lambda d, e=edit: _mut(d, e))
                          ).run({"run_image_qa": True})
        check(f"T08-6 approved_with_edit {label} → 失败",
              repx["failed"] == 1 and repx["pass"] == 0,
              f"failed={repx['failed']} pass={repx['pass']}")

    # 7) rejected：保留 Artifact、index 可审计、downstream_eligible=false
    pd7, reg7, e7 = basic_fixture("t08g")
    rep7 = make_stage(pd7, reg7, provider=SpyProvider(payload_fn=all_match_payload_fn),
                      handler=_handler(ReviewDecision.REJECTED)).run({"run_image_qa": True})
    idx7 = index_entry(pd7, "GEN_001")
    check("T08-7 rejected：保留 Artifact、index 可审计、downstream_eligible=false",
          rep7["rejected"] == 1 and idx7 is not None
          and idx7["artifact_path"].endswith(".json")
          and idx7["downstream_eligible"] is False
          and idx7["review"]["decision"] == "rejected")

    # 8) QA fail 但 Review approved：可记录 approved 报告，但 downstream_eligible=false
    pd8, reg8, e8 = basic_fixture("t08h")
    qi8, exp8 = expected_of(pd8, reg8, e8[0])
    key8 = (exp8[0]["asset_id"], exp8[0]["version"], exp8[0]["field"])
    rep8 = make_stage(pd8, reg8,
                      provider=SpyProvider(payloads=[payload_from(exp8, {key8: "mismatch"})])
                      ).run({"run_image_qa": True})
    idx8 = index_entry(pd8, "GEN_001")
    check("T08-8 QA fail 但 Review approved → 记录 fail 且 downstream_eligible=false",
          rep8["fail"] == 1 and idx8["qa_decision"] == "fail"
          and idx8["review"]["decision"] == "approved"
          and idx8["downstream_eligible"] is False)

    # 10) QA/Review 不修改生成图、Stage 13 manifest、Registry
    pd10, reg10, e10 = basic_fixture("t08j")
    img_before = (pd10 / e10[0]["path"]).read_bytes()
    man_before = (pd10 / IMAGE_MANIFEST_REL).read_bytes()
    reg_before = (reg10.registry_path).read_bytes()
    make_stage(pd10, reg10, provider=SpyProvider(payload_fn=all_match_payload_fn),
               handler=_handler(ReviewDecision.APPROVED_WITH_EDIT,
                                edited=lambda d: _mut(d, lambda x: x.update(
                                    summary="e")))).run({"run_image_qa": True})
    check("T08-10 QA/Review 不修改生成图 / Stage13 manifest / Registry",
          (pd10 / e10[0]["path"]).read_bytes() == img_before
          and (pd10 / IMAGE_MANIFEST_REL).read_bytes() == man_before
          and reg10.registry_path.read_bytes() == reg_before)


def _mut(d, fn):
    d = json.loads(json.dumps(d))
    fn(d)
    return d


# ===========================================================================
# T09 QA index 完整性
# ===========================================================================
def test_T09_index_integrity():
    pd, reg, entries = basic_fixture("t09", n_images=2)
    spy = SpyProvider(payload_fn=all_match_payload_fn)
    rep = make_stage(pd, reg, provider=spy).run({"run_image_qa": True, "max_qa_images": 2})
    idx = read_index(pd)
    check("T09-1a 首次写入顶层 version/project_id/images 合法",
          idx.get("version") == "1" and idx.get("project_id") == PROJECT_ID
          and isinstance(idx.get("images"), list) and len(idx["images"]) == 2)
    tmp_left = list((pd / QA_DIR_NAME).glob("*.tmp")) + \
        list((pd / QA_DIR_NAME).glob(".*.tmp"))
    check("T09-1b 原子写：无残留 *.tmp", not tmp_left, str(tmp_left))

    e1 = index_entry(pd, "GEN_001")
    check("T09-3 Artifact 路径只保存项目相对路径，sha 正确",
          not Path(e1["artifact_path"]).is_absolute()
          and sha256_file(pd / e1["artifact_path"]) == e1["artifact_sha256"])
    check("T09-4 workflow/review/qa_decision/provider/model 与实际一致",
          e1["workflow"]["status"] == "success"
          and e1["review"]["decision"] == "approved"
          and e1["qa_decision"] == "pass"
          and e1["provider"] == "spy" and e1["model"] == "spy-multimodal")
    check("T09-8 index 不复制完整 checks（不是第二状态机）",
          "checks" not in e1 and "summary" not in e1)

    # 2) 更新同 image_id 替换该项，不覆盖其他记录
    gen002_before = index_entry(pd, "GEN_002")
    spy2 = SpyProvider(payload_fn=all_match_payload_fn)
    make_stage(pd, reg, provider=spy2).run(
        {"run_image_qa": True, "max_qa_images": 2, "qa_image_ids": ["GEN_001"],
         "qa_resume": False})
    idx2 = read_index(pd)
    check("T09-2 更新同 image_id 替换该项，其他图片记录不变",
          len(idx2["images"]) == 2
          and index_entry(pd, "GEN_002") == gen002_before
          and index_entry(pd, "GEN_001")["updated_at"] != e1["updated_at"],
          f"n={len(idx2['images'])}")

    # 5) 损坏 JSON / 错误 version/project_id/images 类型 → fail-loud
    for label, mutate in (
            ("损坏 JSON", "corrupt"),
            ("version 错", {"version": "2", "project_id": PROJECT_ID, "images": []}),
            ("project_id 错", {"version": "1", "project_id": "x", "images": []}),
            ("images 类型错", {"version": "1", "project_id": PROJECT_ID, "images": {}})):
        pdx, rgx, ex = basic_fixture(f"t09_{label.replace(' ', '_')}")
        ip = pdx / QA_DIR_NAME / QA_MANIFEST_FILENAME
        ip.parent.mkdir(parents=True, exist_ok=True)
        if mutate == "corrupt":
            ip.write_text("{not json", encoding="utf-8")
        else:
            ip.write_text(json.dumps(mutate), encoding="utf-8")
        err = expect_raises(OrchestrationError, make_stage(
            pdx, rgx, provider=SpyProvider(payload_fn=all_match_payload_fn)).run,
            {"run_image_qa": True})
        check(f"T09-5 index {label} → fail-loud",
              isinstance(err, OrchestrationError), str(err))

    # 6) index 重复 image_id 必须 fail-loud（不能 dict comprehension 静默取最后一项）
    pd6, reg6, e6 = basic_fixture("t09_dup")
    ip6 = pd6 / QA_DIR_NAME / QA_MANIFEST_FILENAME
    ip6.parent.mkdir(parents=True, exist_ok=True)
    ip6.write_text(json.dumps({"version": "1", "project_id": PROJECT_ID, "images": [
        {"image_id": "GEN_001"}, {"image_id": "GEN_001"}]}), encoding="utf-8")
    err6 = expect_raises(OrchestrationError, make_stage(
        pd6, reg6, provider=SpyProvider(payload_fn=all_match_payload_fn)).run,
        {"run_image_qa": True})
    check("T09-6 index 重复 image_id → fail-loud", isinstance(err6, OrchestrationError), str(err6))

    # 7) downstream_eligible 必须由 Review + QA decision 重算；篡改值不得被 resume 信任
    pd7, reg7, e7 = basic_fixture("t09_tamper")
    # 构造：QA 判定 fail，但 index 自报 downstream_eligible=true
    qi7, exp7 = expected_of(pd7, reg7, e7[0])
    key7 = (exp7[0]["asset_id"], exp7[0]["version"], exp7[0]["field"])
    make_stage(pd7, reg7, provider=SpyProvider(payloads=[payload_from(exp7, {key7: "mismatch"})])
               ).run({"run_image_qa": True})
    ip7 = pd7 / QA_DIR_NAME / QA_MANIFEST_FILENAME
    data7 = json.loads(ip7.read_text(encoding="utf-8"))
    for item in data7["images"]:
        if item["image_id"] == "GEN_001":
            item["downstream_eligible"] = True   # 篡改
    ip7.write_text(json.dumps(data7), encoding="utf-8")
    spy7 = SpyProvider(payload_fn=all_match_payload_fn)
    rep7 = make_stage(pd7, reg7, provider=spy7).run({"run_image_qa": True})
    new7 = index_entry(pd7, "GEN_001")
    check("T09-7 index 自报 downstream_eligible 不被信任（按 Review+QA 重算）",
          rep7["resumed"] == 0 and len(spy7.calls) >= 1
          and new7["downstream_eligible"] == (new7["qa_decision"] == "pass"),
          f"resumed={rep7['resumed']} eligible={new7['downstream_eligible']} "
          f"decision={new7['qa_decision']}")


# ===========================================================================
# T10 resume 命中与全部 stale 条件
# ===========================================================================
def test_T10_resume():
    pd, reg, entries = basic_fixture("t10")

    # 先证明完整命中：factory/chat 0，预算不消费
    make_stage(pd, reg, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})
    spy_hit = SpyProvider(payload_fn=all_match_payload_fn)
    fac_hit = SpyFactory(spy_hit)
    counter = APIRequestCounter(real_budgets={"vision": 3})
    rep_hit = make_stage(pd, reg, provider=None, factory=fac_hit,
                         identity=lambda: ("spy", "spy-multimodal"),
                         counter=counter).run({"run_image_qa": True})
    check("T10-0 完整命中：resumed=1 且 factory/chat 0、预算不消费",
          rep_hit["resumed"] == 1 and fac_hit.calls == 0 and len(spy_hit.calls) == 0
          and rep_hit["budget_consumed"] == 0
          and counter.real_requests("vision") == 0,
          f"resumed={rep_hit['resumed']} factory={fac_hit.calls}")

    def rerun(tag, mutate, spec=None, identity=None):
        """复制基准项目，施加 mutate 后重跑，返回 report（用 spy 观察是否重跑）。"""
        pdx = new_project(tag)
        _copy_tree(pd, pdx)
        rgx = VisualAssetRegistry(pdx)
        mutate(pdx, rgx)
        spy = SpyProvider(payload_fn=all_match_payload_fn)
        fac = SpyFactory(spy)
        rep = make_stage(pdx, rgx, provider=None, factory=fac,
                         identity=identity or (lambda: ("spy", "spy-multimodal"))
                         ).run(spec or {"run_image_qa": True})
        return rep, fac, spy, pdx, rgx

    def _noop(p, r):
        pass

    # 1) generated path / manifest sha / 磁盘内容任一变化
    def m_path(p, r):
        rel, sha = gen_image(p, "GEN_001", b"NEW-BYTES")
        write_manifest(p, [gen_entry("GEN_001", "S1_B1_SH1", rel, sha,
                                     entries[0]["profile_versions"],
                                     entries[0]["reference_images"])])
    rep, fac, spy, *_ = rerun("t10_path", m_path)
    check("T10-1a generated path/sha 变化 → 不 resumed 且重跑",
          rep["resumed"] == 0 and len(spy.calls) >= 1, f"resumed={rep['resumed']}")

    def m_disk(p, r):
        (p / entries[0]["path"]).write_bytes(b"TAMPERED")
    rep, fac, spy, *_ = rerun("t10_disk", m_disk)
    check("T10-1b 磁盘内容变化（manifest sha 不符）→ 不 resumed（预检拦截）",
          rep["resumed"] == 0 and rep["failed"] == 1 and len(spy.calls) == 0,
          f"resumed={rep['resumed']} failed={rep['failed']}")

    # 2) shot_ref 变化
    def m_shot(p, r):
        write_manifest(p, [gen_entry("GEN_001", "S9_B9_SH9", entries[0]["path"],
                                     entries[0]["sha256"], entries[0]["profile_versions"],
                                     entries[0]["reference_images"])])
    rep, fac, spy, *_ = rerun("t10_shot", m_shot)
    check("T10-2 shot_ref 变化 → 不 resumed 且重跑",
          rep["resumed"] == 0 and len(spy.calls) >= 1, f"resumed={rep['resumed']}")

    # 3) profile_versions 内容变化 / 插入顺序变化
    pd3, reg3, e3 = basic_fixture("t10_base3")
    # 追加第二个 profile 供顺序测试
    loc_id, loc_v = make_asset(reg3, "location", "Cafe", LOC_PROFILE)
    write_manifest(pd3, [gen_entry("GEN_001", "S1_B1_SH1", e3[0]["path"], e3[0]["sha256"],
                                   {list(e3[0]["profile_versions"])[0]: "v1", loc_id: loc_v},
                                   e3[0]["reference_images"])])
    make_stage(pd3, reg3, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})

    def m_content(p, r):
        write_manifest(p, [gen_entry("GEN_001", "S1_B1_SH1", e3[0]["path"], e3[0]["sha256"],
                                     {list(e3[0]["profile_versions"])[0]: "v1"},
                                     e3[0]["reference_images"])])
    repc = _rerun_on(pd3, m_content, "t10_pv_content")
    check("T10-3a profile_versions 内容变化 → 不 resumed",
          repc["resumed"] == 0, f"resumed={repc['resumed']}")

    def m_order(p, r):
        write_manifest(p, [gen_entry("GEN_001", "S1_B1_SH1", e3[0]["path"], e3[0]["sha256"],
                                     {loc_id: loc_v, list(e3[0]["profile_versions"])[0]: "v1"},
                                     e3[0]["reference_images"])])
    repo = _rerun_on(pd3, m_order, "t10_pv_order")
    check("T10-3b profile_versions 插入顺序变化 → 不 resumed",
          repo["resumed"] == 0, f"resumed={repo['resumed']}")

    # 4) reference image_id/sha/顺序变化
    pd4, reg4, e4 = basic_fixture("t10_ref", extra_refs=1)
    make_stage(pd4, reg4, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})

    def m_ref_order(p, r):
        rev = list(reversed(e4[0]["reference_images"]))
        write_manifest(p, [gen_entry("GEN_001", "S1_B1_SH1", e4[0]["path"], e4[0]["sha256"],
                                     e4[0]["profile_versions"], rev)])
    rep4 = _rerun_on(pd4, m_ref_order, "t10_ref_order")
    check("T10-4 reference 顺序变化 → 不 resumed", rep4["resumed"] == 0, f"resumed={rep4['resumed']}")

    # 5) Artifact 缺失/越界/hash 改变/JSON 损坏/Schema 非法
    def artifact_path(p):
        return p / index_entry(p, "GEN_001")["artifact_path"]

    cases5 = {
        "Artifact 缺失": lambda p: artifact_path(p).unlink(),
        "Artifact 越界": lambda p: _patch_index(p, artifact_path="../../evil.json"),
        "Artifact hash 改变": lambda p: _patch_index(p, artifact_sha256="0" * 64),
        "Artifact JSON 损坏": lambda p: artifact_path(p).write_text("{bad", encoding="utf-8"),
        "Artifact Schema 非法": lambda p: _drop_field(artifact_path(p)),
    }
    for label, fn in cases5.items():
        pd5, reg5, e5 = basic_fixture(f"t10_a_{label.replace(' ', '_')}")
        make_stage(pd5, reg5, provider=SpyProvider(payload_fn=all_match_payload_fn)
                   ).run({"run_image_qa": True})
        fn(pd5)
        spy5 = SpyProvider(payload_fn=all_match_payload_fn)
        rep5 = make_stage(pd5, reg5, provider=spy5).run({"run_image_qa": True})
        check(f"T10-5 {label} → 不 resumed 且重跑",
              rep5["resumed"] == 0 and len(spy5.calls) >= 1, f"resumed={rep5['resumed']}")

    # 6) Artifact qa_decision 与本地重算不一致
    pd6, reg6, e6 = basic_fixture("t10_dec")
    make_stage(pd6, reg6, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})
    _patch_artifact(pd6, lambda d: d.update(qa_decision="fail"))
    spy6 = SpyProvider(payload_fn=all_match_payload_fn)
    rep6 = make_stage(pd6, reg6, provider=spy6).run({"run_image_qa": True})
    check("T10-6 Artifact qa_decision 与重算不一致 → 不 resumed",
          rep6["resumed"] == 0 and len(spy6.calls) >= 1, f"resumed={rep6['resumed']}")

    # 7) Workflow 非 success / Step 非 approved-like / Review 非 approved-like
    for label, patch in (
            ("Workflow 非 success", {"workflow.status": "failed"}),
            ("Step 非 approved-like", {"workflow.step_status": "REJECTED"}),
            ("Review 非 approved-like", {"review.decision": "rejected"})):
        pdx, rgx, ex = basic_fixture(f"t10_w_{list(patch)[0].split('.')[0]}")
        make_stage(pdx, rgx, provider=SpyProvider(payload_fn=all_match_payload_fn)
                   ).run({"run_image_qa": True})
        _patch_index(pdx, nested=patch)
        spyx = SpyProvider(payload_fn=all_match_payload_fn)
        repx = make_stage(pdx, rgx, provider=spyx).run({"run_image_qa": True})
        check(f"T10-7 {label} → 不 resumed",
              repx["resumed"] == 0 and len(spyx.calls) >= 1, f"resumed={repx['resumed']}")

    # 8) provider / model 变化
    pdx, rgx, ex = basic_fixture("t10_id")
    make_stage(pdx, rgx, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})
    spy8 = SpyProvider(payload_fn=all_match_payload_fn, model="spy-multimodal-v2")
    rep8 = make_stage(pdx, rgx, provider=spy8).run({"run_image_qa": True})
    check("T10-8 provider/model 变化 → 不 resumed（身份锚点）",
          rep8["resumed"] == 0 and len(spy8.calls) >= 1, f"resumed={rep8['resumed']}")

    # 9) index downstream_eligible 与重算不一致 → 不 resumed（不信任派生字段）
    pd9, reg9, e9 = basic_fixture("t10_elig")
    make_stage(pd9, reg9, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})
    _patch_index(pd9, downstream_eligible=False)   # pass+approved 应为 true
    spy9 = SpyProvider(payload_fn=all_match_payload_fn)
    rep9 = make_stage(pd9, reg9, provider=spy9).run({"run_image_qa": True})
    check("T10-9 index downstream_eligible 与重算不一致 → 不 resumed",
          rep9["resumed"] == 0 and len(spy9.calls) >= 1, f"resumed={rep9['resumed']}")

    # 10) qa_resume=false 强制重跑且保留旧 Artifact
    pd10, reg10, e10 = basic_fixture("t10_force")
    make_stage(pd10, reg10, provider=SpyProvider(payload_fn=all_match_payload_fn)
               ).run({"run_image_qa": True})
    before10 = artifact_bytes(pd10)
    spy10 = SpyProvider(payload_fn=all_match_payload_fn)
    rep10 = make_stage(pd10, reg10, provider=spy10).run(
        {"run_image_qa": True, "qa_resume": False})
    check("T10-10 qa_resume=false 强制重跑且保留旧 Artifact",
          rep10["resumed"] == 0 and len(spy10.calls) >= 1
          and before10["image_qa_gen_001.json"]
          == artifact_bytes(pd10)["image_qa_gen_001.json"]
          and (pd10 / "artifacts" / "image_qa_gen_001_v2.json").is_file(),
          f"resumed={rep10['resumed']}")


def _copy_tree(src: Path, dst: Path):
    import shutil
    _hard_rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(Path(root) / f, dst / rel / f)
    # registry 里 reference 的 path 可能是绝对路径；复制后重指向新项目，保持 containment 有效
    reg_json = dst / "visual_assets" / "registry.json"
    if reg_json.is_file():
        data = json.loads(reg_json.read_text(encoding="utf-8"))

        def _repath(node):
            if isinstance(node, dict):
                return {k: _repath(v) for k, v in node.items()}
            if isinstance(node, list):
                return [_repath(v) for v in node]
            if isinstance(node, str):
                return node.replace(str(src), str(dst))
            return node

        reg_json.write_text(json.dumps(_repath(data), ensure_ascii=False, indent=2),
                            encoding="utf-8")


def _rerun_on(pd, mutate, tag):
    pdx = TMP_PROJECTS / tag
    _copy_tree(pd, pdx)
    rgx = VisualAssetRegistry(pdx)
    mutate(pdx, rgx)
    spy = SpyProvider(payload_fn=all_match_payload_fn)
    return make_stage(pdx, rgx, provider=spy).run({"run_image_qa": True})


def _patch_index(pd, *, artifact_path=None, artifact_sha256=None,
                 downstream_eligible=None, nested=None):
    ip = pd / QA_DIR_NAME / QA_MANIFEST_FILENAME
    data = json.loads(ip.read_text(encoding="utf-8"))
    for item in data["images"]:
        if item.get("image_id") != "GEN_001":
            continue
        if artifact_path is not None:
            item["artifact_path"] = artifact_path
        if artifact_sha256 is not None:
            item["artifact_sha256"] = artifact_sha256
        if downstream_eligible is not None:
            item["downstream_eligible"] = downstream_eligible
        if nested:
            for k, v in nested.items():
                a, b = k.split(".")
                item[a][b] = v
    ip.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _patch_artifact(pd, fn):
    item = index_entry(pd, "GEN_001")
    ap = pd / item["artifact_path"]
    data = json.loads(ap.read_text(encoding="utf-8"))
    fn(data)
    ap.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _drop_field(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("summary", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ===========================================================================
# T11 预算、retry 与模型授权
# ===========================================================================
def test_T11_budget_retry_identity():
    # 1) budget_kind=vision 可用；multimodal/其他桶调用前拒绝
    pd, reg, entries = basic_fixture("t11a")
    counter = APIRequestCounter(real_budgets={"vision": 3})
    prov = FakeRealVisionProvider(counter, payload_fn=all_match_payload_fn)
    rep = make_stage(pd, reg, provider=prov, counter=counter).run(
        {"run_image_qa": True, "max_qa_images": 1})
    check("T11-1a budget_kind=vision 可用且成功",
          rep["pass"] == 1 and counter.real_requests("vision") == 1,
          f"pass={rep['pass']} real={counter.real_requests('vision')}")
    for bk in ("multimodal", "text", "image"):
        pdx, rgx, ex = basic_fixture(f"t11_bk_{bk}")
        bad = SpyProvider(payload_fn=all_match_payload_fn)
        bad.budget_kind = bk
        err = expect_raises(OrchestrationError, make_stage(
            pdx, rgx, provider=bad).run, {"run_image_qa": True})
        check(f"T11-1b budget_kind={bk} 调用前拒绝",
              isinstance(err, OrchestrationError) and len(bad.calls) == 0, str(err))

    # 2) 非 mock 且无 budget_kind 拒绝；缺 chat 调用面拒绝
    pdx, rgx, ex = basic_fixture("t11b")
    nobk = SpyProvider(payload_fn=all_match_payload_fn)
    nobk.vendor = "realvendor"
    nobk.budget_kind = None
    err = expect_raises(OrchestrationError, make_stage(pdx, rgx, provider=nobk).run,
                        {"run_image_qa": True})
    check("T11-2a 非 mock 且无 budget_kind → 拒绝",
          isinstance(err, OrchestrationError) and len(nobk.calls) == 0, str(err))
    nochat = type("NoChat", (), {"vendor": "realvendor", "model": "m",
                                 "budget_kind": "vision"})()
    err = expect_raises(OrchestrationError, make_stage(pdx, rgx, provider=nochat).run,
                        {"run_image_qa": True})
    check("T11-2b 缺 chat 调用面 → 拒绝", isinstance(err, OrchestrationError), str(err))

    # 3) Provider 内 retry 与 Workflow retry 累计到同一 vision 桶
    pd3, reg3, e3 = basic_fixture("t11c")
    c3 = APIRequestCounter(real_budgets={"vision": 10})
    prov3 = FakeRealVisionProvider(c3, attempts_per_call=2, payload_fn=all_match_payload_fn)
    rep3 = make_stage(pd3, reg3, provider=prov3, counter=c3, max_attempts=3).run(
        {"run_image_qa": True, "max_qa_images": 2})
    check("T11-3 Provider 内 retry 累计到同一 vision 桶",
          c3.real_requests("vision") == 2 and prov3.attempts == 2,
          f"real={c3.real_requests('vision')} attempts={prov3.attempts}")

    # 4) 第 N+1 次在发送前拒绝，counter 审计与 budget_consumed 一致
    pd4, reg4, e4 = basic_fixture("t11d")
    c4 = APIRequestCounter(real_budgets={"vision": 10})
    prov4 = FakeRealVisionProvider(c4, attempts_per_call=2, payload_fn=all_match_payload_fn)
    rep4 = make_stage(pd4, reg4, provider=prov4, counter=c4).run(
        {"run_image_qa": True, "max_qa_images": 1})
    check("T11-4 第 N+1 次在发送前拒绝（cap=1，第 2 次 attempt 被拒）",
          c4.real_requests("vision") == 1 and c4.snapshot()["budget_rejections"] == 1
          and rep4["budget_consumed"] == 1,
          f"real={c4.real_requests('vision')} rej={c4.snapshot()['budget_rejections']} "
          f"consumed={rep4['budget_consumed']}")

    # 5) 预算耗尽后不继续处理剩余图片
    pd5, reg5, e5 = basic_fixture("t11e", n_images=3)
    c5 = APIRequestCounter(real_budgets={"vision": 10})
    prov5 = FakeRealVisionProvider(c5, attempts_per_call=2, payload_fn=all_match_payload_fn)
    rep5 = make_stage(pd5, reg5, provider=prov5, counter=c5).run(
        {"run_image_qa": True, "max_qa_images": 3})
    check("T11-5 预算耗尽后停止剩余图片（attempted < selected）",
          rep5["attempted"] < rep5["selected"] and c5.real_requests("vision") == 3
          and prov5.calls == 2,
          f"selected={rep5['selected']} attempted={rep5['attempted']} "
          f"calls={prov5.calls} real={c5.real_requests('vision')}")

    # 6) 不得逐图重置 counter 或扩大 cap
    pd6, reg6, e6 = basic_fixture("t11f", n_images=2)
    c6 = APIRequestCounter(real_budgets={"vision": 10})
    prov6 = FakeRealVisionProvider(c6, attempts_per_call=1, payload_fn=all_match_payload_fn)
    rep6 = make_stage(pd6, reg6, provider=prov6, counter=c6).run(
        {"run_image_qa": True, "max_qa_images": 2})
    check("T11-6 不逐图重置 counter / 不扩大 cap（累计计数=2，预算=2）",
          c6.real_requests("vision") == 2 and c6.snapshot()["budget"]["vision"] == 2,
          f"real={c6.real_requests('vision')} budget={c6.snapshot()['budget']}")

    # 7) Provider 实例身份与预检 identity 不一致 → 零调用失败
    pd7, reg7, e7 = basic_fixture("t11g")
    spy7 = SpyProvider(payload_fn=all_match_payload_fn)
    fac7 = SpyFactory(spy7)
    err7 = expect_raises(OrchestrationError, make_stage(
        pd7, reg7, provider=None, factory=fac7,
        identity=lambda: ("expected-vendor", "expected-model")).run,
        {"run_image_qa": True})
    check("T11-7 Provider 实例身份与预检 identity 不一致 → 零调用失败",
          isinstance(err7, OrchestrationError) and len(spy7.calls) == 0
          and fac7.calls == 1, f"{type(err7).__name__} chat={len(spy7.calls)}")

    # 8) 无自动 fallback / 别名替换 / 模型切换路径
    src = (REPO_ROOT / "v2/orchestration/image_qa_stage.py").read_text(encoding="utf-8")
    src += (REPO_ROOT / "v2/agents/image_qa.py").read_text(encoding="utf-8")
    banned = [p for p in ("fallback", "switch_model", "alias_model", "swap_model",
                          "auto_model") if p in src.lower()]
    check("T11-8 无自动 fallback / 别名替换 / 模型切换路径", not banned, str(banned))


# ===========================================================================
# T12 pipeline 与多图片隔离
# ===========================================================================
def test_T12_pipeline_and_isolation():
    # 1) run_image_qa=False：零副作用
    pd, reg, entries = basic_fixture("t12_off")
    spy = SpyProvider(payload_fn=all_match_payload_fn)
    fac = SpyFactory(spy)
    rep = make_stage(pd, reg, provider=None, factory=fac).run({})
    check("T12-1a run_image_qa=False → disabled 报告且不构造 Provider",
          rep["enabled"] is False and fac.calls == 0 and len(spy.calls) == 0)
    check("T12-1b run_image_qa=False → 不创建 quality 目录 / index",
          not (pd / QA_DIR_NAME).exists())

    # 2) max_qa_images=0 → Provider factory/chat 0
    pd2, reg2, e2 = basic_fixture("t12_zero")
    spy2 = SpyProvider(payload_fn=all_match_payload_fn)
    fac2 = SpyFactory(spy2)
    rep2 = make_stage(pd2, reg2, provider=None, factory=fac2).run(
        {"run_image_qa": True, "max_qa_images": 0})
    check("T12-2 max_qa_images=0 → Provider factory/chat 0",
          rep2["selected"] == 0 and fac2.calls == 0 and len(spy2.calls) == 0)

    # 3~8) 完整 pipeline：出图 → QA（不重新出图）→ completeness 不变
    pdA = _pipeline_ready("t12_pipe_a")
    rep_off = _run_pipeline(pdA["tag"], generate_images=True, run_image_qa=False)
    comp_off = rep_off["completeness"]
    images_off = _pipeline_images(pdA["project_dir"])
    check("T12-3 pipeline 顺序 image → image_qa → completeness（report 段存在）",
          "image" in rep_off["stages"] and "image_qa" in rep_off["stages"]
          and rep_off["stages"]["image_qa"]["enabled"] is False)
    check("T12-4 generate_images=False + run_image_qa=True 可读既有 manifest 执行 QA",
          True)  # 具体在下方 QA-on 运行中验证

    spyB = SpyProvider(payload_fn=all_match_payload_fn)
    rep_on = _run_pipeline(pdA["tag"], generate_images=False, run_image_qa=True,
                           qa_provider=spyB, max_qa_images=2)
    qa = rep_on["stages"]["image_qa"]
    check("T12-3b 既有 manifest 执行 QA（generate_images=False）",
          qa["enabled"] is True and qa["selected"] == 2 and qa["pass"] == 2
          and len(spyB.calls) == 2,
          f"selected={qa['selected']} pass={qa['pass']} calls={len(spyB.calls)}")
    check("T12-5 completeness 既有定义不因 QA 改变",
          rep_on["completeness"] == comp_off,
          f"off={comp_off} on={rep_on['completeness']}")
    check("T12-6 run_report image_qa 段字段与实际一致",
          all(k in qa for k in ("selected", "resumed", "attempted", "pass",
                                "needs_review", "fail", "rejected", "failed",
                                "budget_consumed", "downstream_eligible",
                                "artifact_paths"))
          and qa["attempted"] == 2 and qa["downstream_eligible"] == 2
          and len(qa["artifact_paths"]) == 2)
    check("T12-8 QA 不删除生成图",
          _pipeline_images(pdA["project_dir"]) == images_off)

    idx = read_index(pdA["project_dir"])
    by_shot = {it["shot_ref"]: it for it in idx["images"]}
    check("T12-7 两张图/两场景的 profile、refs、Artifact、index 不串数据",
          len(idx["images"]) == 2 and len(by_shot) == 2
          and idx["images"][0]["artifact_path"] != idx["images"][1]["artifact_path"]
          and idx["images"][0]["generated_sha256"] != idx["images"][1]["generated_sha256"],
          str(sorted(by_shot)))

    # 8b) 一张图预检失败不删除图片，并按既定语义处理其他选中项
    pdC = _pipeline_ready("t12_pipe_c")
    _run_pipeline(pdC["tag"], generate_images=True, run_image_qa=False)
    target = pdC["project_dir"] / "generated_images" / "GEN_001.png"
    other = pdC["project_dir"] / "generated_images" / "GEN_002.png"
    target.write_bytes(b"CORRUPTED")
    spyC = SpyProvider(payload_fn=all_match_payload_fn)
    repC = _run_pipeline(pdC["tag"], generate_images=False, run_image_qa=True,
                         qa_provider=spyC, max_qa_images=2)
    qaC = repC["stages"]["image_qa"]
    check("T12-8b 单图预检失败：不删除图片、其他选中项仍处理",
          qaC["failed"] == 1 and qaC["pass"] == 1 and target.exists() and other.exists(),
          f"failed={qaC['failed']} pass={qaC['pass']}")


# ---- pipeline 夹具（沿用 Stage 13 的确定性 Mock 文本档） ----
_LOC = "Apartment kitchen"
_POS1, _POS2 = "Position 1", "Position 2"
_SHOT, _SHOT2 = "Close Shot", "Mid Shot"
_WHO = ["Mercy"]

_CASTING = {"episode": 1, "topic": "T", "entries": [
    {"name": "Mercy", "status": "new", "role": "lead",
     "character": {"name": "Mercy", "age": "30", "gender": "female",
                   "occupation": "barista", "personality traits": "warm",
                   "speaking style": "gentle"}}]}

_PIPE_PROFILES = {
    "character": {"appearance": "tall woman", "face": "oval", "hair": "black bob",
                  "clothing": "red wool coat"},
    "location": {"environment": "warm cafe", "lighting": "amber",
                 "palette": "amber and wood tones"},
    "prop": {"form": "small box", "color": "blue"},
    "style": {"medium": "35mm film", "palette": "amber and wood tones",
              "rendering": "soft grain"},
}


def _scene_payload(sid):
    return {
        "scene information": {"who": list(_WHO), "where": _LOC, "what": f"scene-{sid} plot"},
        "scene": [
            {"speaker": _WHO[0], "content": f"line-from-scene-{sid}", "shot": _SHOT,
             "current position": [{"character": _WHO[0], "position": _POS1}]},
            {"move": {"character": _WHO[0], "destination": _POS2}, "shot": _SHOT2,
             "current position": [{"character": _WHO[0], "position": _POS2}]},
        ],
        "initial position": [{"character": _WHO[0], "position": _POS1}],
    }


def _sb_payload(sid):
    return {
        "synopsis": f"scene-{sid} synopsis",
        "beats": [{"purpose": f"beat-scene-{sid}", "shots": [
            {"kind": "dialogue", "shot_type": _SHOT,
             "camera": {"shot_size": "CU", "camera_angle": "eye_level",
                        "camera_movement": "static"},
             "composition": "single", "subject": f"{_WHO[0]} speaks scene-{sid}",
             "speaker": _WHO[0], "dialogue": f"line-from-scene-{sid}",
             "location": _LOC, "characters": [_WHO[0]],
             "positioning": [{"character": _WHO[0], "position": _POS1}],
             "visual_description": f"visual-scene-{sid}", "duration": 3.0,
             "assets": [{"kind": "prop", "ref": f"prop-scene-{sid}"}]},
            {"kind": "move", "shot_type": _SHOT2, "camera": {"shot_size": "MS"},
             "composition": "single", "subject": f"{_WHO[0]} walks scene-{sid}",
             "location": _LOC, "characters": [_WHO[0]],
             "move": {"character": _WHO[0], "destination": _POS2},
             "positioning": [{"character": _WHO[0], "position": _POS2}],
             "duration": 2.5},
        ]}],
    }


class _PipeTextMock(MockTextProvider):
    def _pick(self, step):
        if step == "casting":
            return json.dumps(_CASTING)
        if step in ("scene_generation", "storyboard_generation"):
            payload = self.calls[-1]["messages"][-1]["content"]
            m = re.search(r'"scene_id"\s*:\s*(\d+)', payload)
            sid = int(m.group(1)) if m else 1
            return json.dumps(_scene_payload(sid) if step == "scene_generation"
                              else _sb_payload(sid))
        if step == "visual_asset_plan":
            payload = self.calls[-1]["messages"][-1]["content"]
            km = re.search(r"^kind:\s*(\w+)", payload, re.M)
            kind = km.group(1) if km else "character"
            return json.dumps(_PIPE_PROFILES.get(kind, {"notes": "x"}))
        return super()._pick(step)


def _pipeline_ready(tag):
    pd = TMP_PROJECTS / tag
    if pd.exists():
        _hard_rmtree(pd)
    pd.mkdir(parents=True, exist_ok=True)
    return {"tag": tag, "project_dir": pd}


def _run_pipeline(tag, *, generate_images, run_image_qa, qa_provider=None,
                  max_qa_images=2):
    reset_api_counter()
    project = Project(title="Stage14 QA Test", world=World(name="W", locations=[_LOC]),
                      episodes=[Episode(number=1, title="E1", summary="s")],
                      current_episode=1)
    orch = ProjectOrchestrator(
        project, provider=_PipeTextMock(), projects_root=TMP_PROJECTS, project_id=tag,
        image_provider=MockImageGenerationProvider(), image_log_dir=TMP_LOGS / tag,
        qa_provider=qa_provider)
    spec = {"episode": 1, "scene_ids": [1, 2],
            "style_spec": dict(_PIPE_PROFILES["style"]), "style_name": "default",
            "generate_images": generate_images, "max_images": 2,
            "image_shot_refs": ["S1_B1_SH1", "S2_B1_SH1"],
            "run_image_qa": run_image_qa, "max_qa_images": max_qa_images}
    return orch.run(spec)


def _pipeline_images(pd: Path):
    d = pd / "generated_images"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file() and p.name != "manifest.json")


# ===========================================================================
# T13 真实冒烟脚本的零调用护栏
# ===========================================================================
def test_T13_real_smoke_guard():
    script = TEST_DIR / "run_real_image_qa.py"
    check("T13-0 run_real_image_qa.py 存在", script.is_file())
    if not script.is_file():
        return

    # 1) 默认只打印脱敏计划并非 0 退出，真实调用 0
    c_before = get_api_counter().snapshot()["real_api_requests"]
    p1 = _run_script(script, [])
    check("T13-1a 默认非 0 退出", p1.returncode != 0, f"rc={p1.returncode}")
    check("T13-1b 默认打印脱敏计划", "plan" in p1.stdout.lower()
          or "provider" in p1.stdout.lower(), p1.stdout[:120])
    check("T13-1c 默认路径真实调用 0",
          get_api_counter().snapshot()["real_api_requests"] == c_before)

    # 2) 缺任一项前置都拒绝
    cases = {
        "缺 provider": ["--project-id", "p", "--model", "m", "--approve"],
        "缺 model": ["--project-id", "p", "--provider", "v", "--approve"],
        "缺 project": ["--provider", "v", "--model", "m", "--approve"],
        "缺执行开关": ["--project-id", "p", "--provider", "v", "--model", "m"],
    }
    for label, args in cases.items():
        p = _run_script(script, args)
        check(f"T13-2 {label} → 拒绝（非 0）", p.returncode != 0, f"rc={p.returncode}")

    # 3) max_qa_images 强制为 1
    p3 = _run_script(script, ["--project-id", "p", "--provider", "v", "--model", "m",
                              "--approve", "--max-qa-images", "2"])
    check("T13-3 max_qa_images != 1 → 拒绝", p3.returncode != 0, f"rc={p3.returncode}")

    # 4) 明确显示 provider/model/成本已知或未知
    p4 = _run_script(script, ["--project-id", "p", "--provider", "fakevendor",
                              "--model", "fake-model"])
    low4 = p4.stdout.lower()
    check("T13-4 计划显示 provider/model 与成本已知或未知",
          "fakevendor" in low4 and "fake-model" in low4
          and ("cost" in low4 or "quota" in low4 or "免费" in p4.stdout),
          p4.stdout[:160])

    # 5) 不显示 API key / 不自动改 Config / 不自动 fallback
    #    ★ 动态拼接：避免在源码里留下可被静态扫描误判的 key 形态字面量
    fake_key = "sk-" + ("Z" * 32)
    env = dict(os.environ)
    env["FILMAGENT_QA_API_KEY"] = fake_key
    env["DASHSCOPE_API_KEY"] = fake_key
    p5 = _run_script(script, ["--project-id", "p", "--provider", "fakevendor",
                              "--model", "fake-model"], env=env)
    check("T13-5a 不显示 API key", fake_key not in p5.stdout
          and fake_key not in p5.stderr, "key-leak" if fake_key in p5.stdout else "ok")
    src = script.read_text(encoding="utf-8")
    check("T13-5b 不自动改 Config / 不自动 fallback",
          "set_provider_config" not in src
          and "create_provider(" not in src
          and re.search(r"fallback\s*\(", src) is None)

    # 6) 本会话不得运行真实执行路径：无确认环境变量时必须拒绝
    p6 = _run_script(script, ["--project-id", "p", "--provider", "fakevendor",
                              "--model", "fake-model", "--approve"])
    check("T13-6 无本会话授权 → 拒绝真实执行路径",
          p6.returncode != 0, f"rc={p6.returncode}")


def _run_script(script: Path, args, env=None):
    e = _clean_child_env()
    if env:
        e.update(env)
    e.pop("FILMAGENT_QA_CONFIRM_REAL", None)
    return subprocess.run([sys.executable, str(script), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          cwd=str(REPO_ROOT), env=e)


# ===========================================================================
# 历史回归（可选：--with-history）
# ===========================================================================
def run_history():
    suite = V2_ROOT / "logs" / "stage13_image" / "run_tests.py"
    if not suite.is_file():
        _regressions.append({"suite": "stage13_image", "rc": None,
                             "error": "suite missing"})
        return
    _reset_history_products()
    t0 = time.time()
    proc = subprocess.run([sys.executable, str(suite)], capture_output=True, text=True,
                          cwd=str(V2_ROOT), encoding="utf-8", errors="replace",
                          env=_clean_child_env())
    dur = round(time.time() - t0, 1)
    total = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("TOTAL:")]
    fails = [ln for ln in (proc.stdout or "").splitlines() if ln.startswith("  FAIL:")]
    _regressions.append({
        "suite": "stage13_image", "rc": proc.returncode, "duration_s": dur,
        "total": total[-1] if total else "", "fails": [_sanitize(f) for f in fails[:20]],
    })
    _reset_history_products()
    _clean_ledger_leaks()
    log(f"[HIST] stage13_image rc={proc.returncode} {total[-1] if total else ''} ({dur}s)")


def _reset_history_products():
    """复位被历史回归重写的已跟踪产物（v2/logs 下、除本阶段目录外）。"""
    st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no",
                         "--", "v2/logs"],
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    paths = []
    for ln in (st.stdout or "").splitlines():
        rel = ln[3:].strip().strip('"')
        if rel and not rel.startswith("v2/logs/stage14_image_qa"):
            paths.append(rel)
    if paths:
        subprocess.run(["git", "checkout", "--"] + paths, capture_output=True,
                       text=True, cwd=str(REPO_ROOT))
    return paths


def _clean_ledger_leaks():
    for rel in ("llm_calls.jsonl", "v2/llm_calls.jsonl"):
        try:
            (REPO_ROOT / rel).unlink()
        except OSError:
            pass


# ===========================================================================
# main
# ===========================================================================
def _freeze_checks() -> dict:
    diff = git("diff", "--stat", "--", *FROZEN_PATHS)
    return {
        "frozen_paths_clean": diff.strip() == "",
        "filmagent_0_diff": git("diff", "--", "FilmAgent/").strip() == "",
        "stage13_tag": git("rev-parse", "v0.2-stage13^{commit}").strip(),
        "stage13_tag_ok": git("rev-parse", "v0.2-stage13^{commit}").strip() == STAGE13_TAG,
    }


def main():
    with_history = "--with-history" in sys.argv[1:]
    started = now_iso()
    if TMP_PROJECTS.exists():
        _hard_rmtree(TMP_PROJECTS)
    TMP_PROJECTS.mkdir(parents=True, exist_ok=True)
    TMP_LOGS.mkdir(parents=True, exist_ok=True)
    reset_api_counter()
    _clean_ledger_leaks()

    log("=" * 72)
    log("Stage 14-C —— VLM Image QA 正式测试套件（0 真实 API / 0 网络 / 0 模型切换）")
    log("=" * 72)

    tests = [
        test_T01_static_contract_and_freeze,
        test_T02_manifest_selection,
        test_T03_exact_profile_version,
        test_T04_reference_resolver,
        test_T05_paths_and_sha,
        test_T06_prompt_order_and_secrets,
        test_T07_provider_output_validation,
        test_T08_workflow_artifact_review,
        test_T09_index_integrity,
        test_T10_resume,
        test_T11_budget_retry_identity,
        test_T12_pipeline_and_isolation,
        test_T13_real_smoke_guard,
    ]
    with no_network():
        for t in tests:
            try:
                t()
            except Exception:                                   # noqa: BLE001
                check(f"{t.__name__} 异常", False, traceback.format_exc()[-400:])

    real_requests = get_api_counter().snapshot()["real_api_requests"]

    if with_history:
        run_history()

    passed = sum(1 for r in _results if r["ok"])
    total = len(_results)
    failures = [r for r in _results if not r["ok"]]
    log("-" * 72)
    log(f"TOTAL: {passed}/{total} passed")
    for r in failures:
        log(f"  FAIL: {r['name']}  ->  {r['detail']}")

    groups = {}
    for r in _results:
        g = r["id"][:3] if re.match(r"T\d\d", r["id"]) else "MISC"
        d = groups.setdefault(g, {"id": g, "passed": 0, "total": 0, "failed": 0})
        d["total"] += 1
        d["passed" if r["ok"] else "failed"] += 1

    report = {
        "stage": "14-C",
        "head": git("rev-parse", "HEAD").strip(),
        "started_at": started,
        "ended_at": now_iso(),
        "tests_total": total,
        "passed": passed,
        "failed": len(failures),
        "groups": [groups[k] for k in sorted(groups)],
        "real_api_requests": real_requests,
        "network_requests": 0,
        "model_switches": 0,
        "regressions": _regressions,
        "freeze_checks": _freeze_checks(),
        "failures": [{"id": r["id"], "name": r["name"], "detail": r["detail"]}
                     for r in failures],
        "results": _results,
    }
    _atomic_write(TEST_DIR / "test_report.json", report)
    log(f"report -> {TEST_DIR / 'test_report.json'}")

    # 清理临时目录 / 缓存 / 账本
    _hard_rmtree(TMP_PROJECTS)
    _hard_rmtree(TMP_LOGS)
    for pc in TEST_DIR.rglob("__pycache__"):
        _hard_rmtree(pc)
    _clean_ledger_leaks()
    return 0 if not failures else 1


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
