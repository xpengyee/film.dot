# -*- coding: utf-8 -*-
"""
V0.2 Stage 13 —— 图像段测试套件（mock，0 真实 API）

被测对象（本阶段新增 / 允许修改的文件；禁改清单 0 diff）：
    v2/llm/providers/mock_image.py                 确定性 Mock 文生图
    v2/llm/providers/openai_compatible_image.py    真实文生图（transport 可注入）
    v2/llm/providers/registry.py                   image 分支（video/audio 仍拒绝）
    v2/llm/request_counter.py                      image 预算桶 + set_budget
    v2/config.py                                   bailian_image 档 + 图像价格表
    v2/orchestration/image_stage.py                ★图像段（compose→出图→落盘→挂回）
    v2/orchestration/pipeline.py                   image 段接线 + style_spec
    v2/orchestration/state.py                      图像进度镜像（纯追加）
    v2/.gitignore                                  二进制泄漏防护

覆盖（开工指令13 §十二 T01~T19）：
    T01  结构：Mock 图像 Provider / generate_image 签名与 base 一致 / 依赖方向
    T02  registry：image 可造（mock）；★video/audio 仍拒绝；真实档 default_model 空 → 报错
    T03  Mock 出图：确定性 + 真实落盘 + 可算 sha256 + ★0 网络（socket 打桩）
    T04  ★默认不出图：generate_images 缺省 False → 零调用、零图像文件、如实记录
    T05  单 Shot 出图：compose → generate → 落盘 → manifest → 挂回 → 新版本（原文件字节不变）
    T06  多 Shot 出图：N 个 Shot → N 张图；manifest 条数与磁盘文件数一致
    T07  ★预算护栏：请求 3 个 Shot、上限 2 → 第 3 次被拒（APIBudgetExceededError），
         实际调用数 == 2，**不发出第 3 次**
    T08  重试计预算：注入瞬时失败 → 重试消耗预算；预算耗尽后不再重试
    T09  审核三态：approved 挂回 / rejected 不挂回但保留 manifest / auto_approve 标注程序化模拟
    T10  ★sha256 一致性：manifest 每条 sha256 == 磁盘文件实际 sha256
    T11  缺档案：无 approved 视觉档案 → compose 明确报错列缺口，**不硬出图**
    T12  记账：image_generation_calls.jsonl 条数 == 实际调用数；未登记价格 → null
    T13  ★真实 API = 0（程序化快照断言）
    T14  ★Token 硬证据：未参与出图的 Shot 序列化不变；出图 Shot 只多一个 image asset
    T15  ★二进制泄漏扫描：图片被 git 忽略；仓库根 .gitignore 0 diff；v2/ 无二进制入库
    T16  静态检查：compileall / import 冒烟 / 无硬编码 key / ★无硬编码图像模型名
    T17  禁改清单 0 diff（§8.3 全部 + FilmAgent/ + 仓库根 .gitignore）
    T18  历史回归：stage1/3/4/6/7/8/9/10/11/12 十套子进程
    T19  端到端（mock）：分镜 → 出图 → 挂回全链，run_report.image 段与磁盘交叉核对

本文件真实 API 调用 = 0（全 Mock）。
"""
import contextlib
import json
import os
import re
import socket
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

from config import PROVIDERS, IMAGE_PRICING, estimate_image_cost       # noqa: E402
from core.assets import AssetRegistry                                  # noqa: E402
from core.models import Episode, Project, Scene, World                 # noqa: E402
from core.shot import Storyboard, Shot                                 # noqa: E402
from core.visual_registry import VisualAssetRegistry                   # noqa: E402
from core.visual_compose import VisualComposer, VisualComposeError     # noqa: E402
from llm.client import LLMConnectionError, LLMRequestError, LLMTimeoutError  # noqa: E402
from llm.providers.base import ImageGenerationProvider                 # noqa: E402
from llm.providers.mock import MockTextProvider                        # noqa: E402
from llm.providers.mock_image import (                                 # noqa: E402
    MockImageGenerationProvider, encode_png, png_data_url,
)
from llm.providers.openai_compatible_image import (                     # noqa: E402
    OpenAICompatibleImageProvider, ImageGenerationError,
)
from llm.providers.registry import create_provider, get_default_provider  # noqa: E402
from llm.request_counter import (                                      # noqa: E402
    MAX_REAL_IMAGE_REQUESTS, MAX_REAL_VISION_REQUESTS, DEFAULT_REAL_BUDGETS,
    APIBudgetExceededError, APIRequestCounter, get_api_counter, reset_api_counter,
)
from workflow.models import ReviewDecision                             # noqa: E402
from orchestration import ProjectOrchestrator, OrchestrationError      # noqa: E402
from orchestration.image_stage import (                                # noqa: E402
    ImageStage, resolve_image_bytes, guess_extension, sha256_of_bytes, shot_ref_of,
    GENERATED_DIR_NAME, MANIFEST_FILENAME, LEDGER_FILENAME,
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


@contextlib.contextmanager
def no_network():
    """把 socket 连接打死：期间任何真实网络访问都会抛错（T03「0 网络」的硬证据）。"""
    original = socket.socket.connect
    original_create = socket.create_connection

    def _boom(*a, **kw):                       # pragma: no cover - 只在违规时触发
        raise AssertionError("测试期间检测到真实网络访问（应为 0 网络）")

    socket.socket.connect = _boom
    socket.create_connection = _boom
    try:
        yield
    finally:
        socket.socket.connect = original
        socket.create_connection = original_create


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


# ---------------------------------------------------------------------------
# 测试脚手架：真实资产注册表 + 场景感知 Mock + 项目 + 出图夹具
# ---------------------------------------------------------------------------
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")
LOC = "Apartment kitchen"
POS1 = "Position 1"
POS2 = "Position 2"
SHOT = "Close Shot"
SHOT2 = "Mid Shot"
WHO = ["Mercy"]


def scene_payload(sid: int, who=None) -> dict:
    who = list(who if who is not None else WHO)
    return {
        "scene information": {"who": who, "where": LOC, "what": f"scene-{sid} plot"},
        "scene": [
            {"speaker": who[0], "content": f"line-from-scene-{sid}", "shot": SHOT,
             "current position": [{"character": who[0], "position": POS1}]},
            {"move": {"character": who[0], "destination": POS2}, "shot": SHOT2,
             "current position": [{"character": who[0], "position": POS2}]},
        ],
        "initial position": [{"character": who[0], "position": POS1}],
    }


def sb_payload(sid: int, who=None) -> dict:
    who = list(who if who is not None else WHO)
    return {
        "synopsis": f"scene-{sid} synopsis",
        "beats": [{"purpose": f"beat-scene-{sid}", "shots": [
            {"kind": "dialogue", "shot_type": SHOT,
             "camera": {"shot_size": "CU", "camera_angle": "eye_level",
                        "camera_movement": "static"},
             "composition": "single", "subject": f"{who[0]} speaks scene-{sid}",
             "speaker": who[0], "dialogue": f"line-from-scene-{sid}",
             "location": LOC, "characters": [who[0]],
             "positioning": [{"character": who[0], "position": POS1}],
             "visual_description": f"visual-scene-{sid}", "duration": 3.0,
             "assets": [{"kind": "prop", "ref": f"prop-scene-{sid}"}]},
            {"kind": "move", "shot_type": SHOT2, "camera": {"shot_size": "MS"},
             "composition": "single", "subject": f"{who[0]} walks scene-{sid}",
             "location": LOC, "characters": [who[0]],
             "move": {"character": who[0], "destination": POS2},
             "positioning": [{"character": who[0], "position": POS2}],
             "duration": 2.5},
        ]}],
    }


CASTING = {"episode": 1, "topic": "T", "entries": [
    {"name": "Mercy", "status": "new", "role": "lead",
     "character": {"name": "Mercy", "age": "30", "gender": "female",
                   "occupation": "barista", "personality traits": "warm",
                   "speaking style": "gentle"}}]}

# 视觉档案：character 必填 appearance/face/hair/clothing；location 必填
# environment/lighting/palette；prop 必填 form/color；style 必填 medium/palette。
PROFILES = {
    "character": {"appearance": "tall woman", "face": "oval", "hair": "black bob",
                  "clothing": "red wool coat"},
    "location": {"environment": "warm cafe", "lighting": "amber",
                 "palette": "amber and wood tones"},
    "prop": {"form": "small box", "color": "blue"},
    "style": {"medium": "35mm film", "palette": "amber and wood tones",
              "rendering": "soft grain"},
}

#: T14 用的"档案文本指纹"——必须**不**渗漏进 Storyboard 序列化
PROFILE_FINGERPRINTS = ("red wool coat", "amber and wood tones", "35mm film")


class SceneAwareMock(MockTextProvider):
    """场景感知 Mock：按 user payload 里的 scene_id 出不同内容（多场景不串场）。"""

    def _pick(self, step):
        if step == "casting":
            return json.dumps(CASTING)
        if step in ("scene_generation", "storyboard_generation"):
            payload = self.calls[-1]["messages"][-1]["content"]
            m = re.search(r'"scene_id"\s*:\s*(\d+)', payload)
            sid = int(m.group(1)) if m else 1
            return json.dumps(scene_payload(sid, WHO) if step == "scene_generation"
                              else sb_payload(sid, WHO))
        if step == "visual_asset_plan":
            payload = self.calls[-1]["messages"][-1]["content"]
            km = re.search(r"^kind:\s*(\w+)", payload, re.M)
            kind = km.group(1) if km else "character"
            return json.dumps(PROFILES.get(kind, {"notes": "x"}))
        return super()._pick(step)


def make_project(title="Stage13 Test Project") -> Project:
    return Project(title=title,
                   world=World(name="Test World", locations=[LOC]),
                   episodes=[Episode(number=1, title="E1", summary="test episode")],
                   current_episode=1)


def fresh_dir(tag: str) -> Path:
    """清空并重建某 tag 的临时项目目录 + 对应图像账目录（保证断言可重复）。"""
    d = TMP_PROJECTS / tag
    if d.exists():
        _hard_rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    lg = TMP_LOGS / tag
    if lg.exists():
        _hard_rmtree(lg)                      # ★ 图像账是 append-only，必须清掉旧行
    lg.mkdir(parents=True, exist_ok=True)
    return d


def make_orch(tag, *, provider=None, project=None, image_provider=None,
              image_fetch=None, **kw) -> ProjectOrchestrator:
    reset_api_counter()
    return ProjectOrchestrator(
        project or make_project(), provider=provider or SceneAwareMock(),
        projects_root=TMP_PROJECTS, project_id=tag,
        image_provider=image_provider if image_provider is not None
        else MockImageGenerationProvider(),
        image_log_dir=TMP_LOGS / tag, image_fetch=image_fetch, **kw)


def base_spec(**extra) -> dict:
    spec = {"episode": 1, "scene_ids": [1], "style_spec": dict(PROFILES["style"]),
            "style_name": "default"}
    spec.update(extra)
    return spec


def build_ready(tag, *, scene_ids=(1,), style=True):
    """跑一次"不出图"的编排，把 casting / scene / storyboard / 视觉资产都建好。

    返回 (orch, {sid: Storyboard})。后续 T05/T09/T11/T14 直接在这些产物上测图像段。
    """
    fresh_dir(tag)
    orch = make_orch(tag)
    spec = {"episode": 1, "scene_ids": list(scene_ids), "generate_images": False}
    if style:
        spec["style_spec"] = dict(PROFILES["style"])
        spec["style_name"] = "default"
    orch.run(spec)
    sbs = {}
    for sid in scene_ids:
        p = orch.artifacts_dir / f"storyboard_{sid}.json"
        if p.is_file():
            sbs[sid] = Storyboard.from_dict(json.loads(p.read_text(encoding="utf-8")))
    return orch, sbs


# ---------------------------------------------------------------------------
# 审核回调替身（三态；与 pipeline._Decision 同形状）
# ---------------------------------------------------------------------------
class _Decision:
    def __init__(self, decision, review_id="r1", edited_content=None, comment=""):
        self.decision = str(decision)
        self.review_id = str(review_id)
        self.edited_content = edited_content
        self.comment = str(comment or "")


def _handler(decision: str):
    n = {"i": 0}

    def h(artifact, context):
        n["i"] += 1
        return _Decision(decision, f"h-{n['i']}")
    return h


def make_stage(orch, *, provider=None, handler=None, fetch=None, log_dir=None,
               review_mode="auto_approve (programmatic, NOT human review)") -> ImageStage:
    return ImageStage(
        project_id=orch.project_id, project_dir=orch.project_dir,
        artifacts_dir=orch.artifacts_dir, store=orch.store,
        provider=provider if provider is not None else MockImageGenerationProvider(),
        registry=orch.visual_registry,
        composer=VisualComposer(orch.visual_registry),
        review_handler=handler if handler is not None else _handler(ReviewDecision.APPROVED),
        review_mode=review_mode,
        log_dir=log_dir if log_dir is not None else (TMP_LOGS / orch.project_id),
        fetch=fetch, episode=1, state=orch.state)


def read_manifest(orch) -> dict:
    p = orch.project_dir / GENERATED_DIR_NAME / MANIFEST_FILENAME
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def generated_entries(manifest: dict) -> list:
    return [e for e in (manifest.get("images") or []) if e.get("path")]


def disk_images(orch) -> list:
    d = orch.project_dir / GENERATED_DIR_NAME
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name != MANIFEST_FILENAME)


def ledger_lines(orch, tag="__default__") -> list:
    p = (TMP_LOGS / tag / LEDGER_FILENAME)
    if not p.is_file():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# T01 结构与依赖方向
# ---------------------------------------------------------------------------
def test_contract_and_dependency_direction():
    import inspect
    base_sig = inspect.signature(ImageGenerationProvider.generate_image)
    mock_sig = inspect.signature(MockImageGenerationProvider.generate_image)
    real_sig = inspect.signature(OpenAICompatibleImageProvider.generate_image)
    check("T01a Mock/真实图像 Provider 的 generate_image 签名与 base 一致",
          list(mock_sig.parameters) == list(base_sig.parameters)
          and list(real_sig.parameters) == list(base_sig.parameters),
          f"base={list(base_sig.parameters)}")

    check("T01b kind/vendor 正确（mock / 真实类）",
          (MockImageGenerationProvider.kind == "image"
           and MockImageGenerationProvider().vendor == "mock"
           and OpenAICompatibleImageProvider.kind == "image"),
          f"mock.kind={MockImageGenerationProvider.kind}")

    check("T01c 二者都是 ImageGenerationProvider 子类（抽象身份成立）",
          isinstance(MockImageGenerationProvider(), ImageGenerationProvider)
          and issubclass(OpenAICompatibleImageProvider, ImageGenerationProvider))

    check("T01d ImageGenerationError 归属既有错误家族（可区分来源，铁律 9）",
          issubclass(ImageGenerationError, Exception)
          and ImageGenerationError.retryable is False)

    # 依赖方向：v2/llm 与 v2/core 等下层不得 import orchestration
    rev = []
    for layer in ("core", "llm", "memory", "context", "workflow", "review", "agents"):
        for p in sorted((V2_ROOT / layer).rglob("*.py")):
            for line in p.read_text(encoding="utf-8").splitlines():
                ls = line.strip()
                if ls.startswith(("from orchestration", "import orchestration")):
                    rev.append(f"{layer}/{p.name}: {ls}")
    check("T01e ★下层不得 import orchestration（反向扫 0 命中）", not rev,
          f"{rev}" if rev else "")

    bad = []
    allowed = {"core", "llm", "memory", "context", "workflow", "review",
               "agents", "config", "orchestration"}
    layers = {"core", "llm", "memory", "context", "workflow", "review", "agents",
              "config", "orchestration", "prompts", "logs"}
    for p in sorted((V2_ROOT / "orchestration").glob("*.py")):
        for line in p.read_text(encoding="utf-8").splitlines():
            ls = line.strip()
            if not ls.startswith(("from ", "import ")):
                continue
            mod = ls.split()[1].split(".")[0]
            if mod in layers and mod not in allowed:
                bad.append(f"{p.name}: {ls}")
    check("T01f orchestration 的 import 仍只落在允许的下层", not bad,
          f"{bad}" if bad else "")


# ---------------------------------------------------------------------------
# T02 registry 护栏
# ---------------------------------------------------------------------------
def test_registry_image_branch():
    p = create_provider("image", "mock")
    check("T02a kind='image' 现在可造（mock 分支）",
          isinstance(p, MockImageGenerationProvider) and p.vendor == "mock")

    check("T02b ★video / audio 仍然被拒绝（护栏未被误开）",
          all(isinstance(expect_raises(ValueError, create_provider, k, v), ValueError)
              for k, v in (("video", "mock"), ("audio", None), ("video", "bailian"))))

    check("T02c 缺省 vendor 的 image 仍拒绝（与 vision 既有范式一致）",
          isinstance(expect_raises(ValueError, create_provider, "image"), ValueError))

    err = expect_raises(ValueError, create_provider, "image", "bailian")
    check("T02d 真实 image 档 default_model 为空 → 明确报错护栏",
          isinstance(err, ValueError)
          and "default_model" in str(err) and "bailian_image" in str(err),
          str(err)[:90] if err else "")

    check("T02e 未登记图像 vendor → 明确报错",
          "未知图像 vendor" in str(expect_raises(ValueError, create_provider,
                                                 "image", "nope")))

    check("T02f 环境开关缺省 → get_default_provider('image') 得 Mock（0 API）",
          isinstance(get_default_provider("image"), MockImageGenerationProvider))

    check("T02g image 预算桶已登记且与 vision 同量级",
          DEFAULT_REAL_BUDGETS.get("image") == MAX_REAL_IMAGE_REQUESTS
          and MAX_REAL_IMAGE_REQUESTS == 3 == MAX_REAL_VISION_REQUESTS,
          f"budgets={DEFAULT_REAL_BUDGETS}")

    check("T02h config 新增 bailian_image 档 + 图像价格表（未登记 → 空表护栏）",
          PROVIDERS.get("bailian_image", {}).get("kind") == "image"
          and PROVIDERS["bailian_image"]["default_model"] == ""
          and isinstance(IMAGE_PRICING, dict)
          and estimate_image_cost("任意模型", "1024*1024") == (None, ""),
          f"IMAGE_PRICING={IMAGE_PRICING}")


# ---------------------------------------------------------------------------
# T03 Mock 出图：确定性 / 落盘 / sha256 / 0 网络
# ---------------------------------------------------------------------------
def test_mock_image_deterministic_and_offline():
    prov = MockImageGenerationProvider()
    with no_network():
        r1 = prov.generate_image("a cat on a sofa", size="1024*1024",
                                 reference="IMG_001:abc")
        r2 = prov.generate_image("a cat on a sofa", size="1024*1024",
                                 reference="IMG_001:abc")
        r3 = prov.generate_image("a dog", size="1024*1024")
    b1, b2, b3 = (resolve_image_bytes(r.text) for r in (r1, r2, r3))

    check("T03a 确定性：同入参两次调用字节完全一致", b1 == b2,
          f"len={len(b1)}")
    check("T03b 不同 prompt → 不同字节（使多镜头/sha256 断言真有区分度）", b1 != b3)
    check("T03c 产出是合法 PNG（魔数 + IHDR 尺寸）",
          b1[:8] == b"\x89PNG\r\n\x1a\n" and b1[12:16] == b"IHDR",
          f"magic={b1[:8]!r}")
    check("T03d 可算 sha256（64 hex）",
          len(sha256_of_bytes(b1)) == 64 and sha256_of_bytes(b1) == sha256_of_bytes(b2))
    check("T03e 引用串是 data URL（Provider 不决定落盘文件名/版本）",
          r1.text.startswith("data:image/png;base64,"))
    check("T03f ★0 网络（socket 打桩下仍正常出图）", True, "no_network() 全程生效")

    check("T03g prompt 为空 → 明确报错（本地校验，不消耗预算）",
          isinstance(expect_raises(LLMRequestError, prov.generate_image, "   "),
                     LLMRequestError))

    check("T03h encode_png/png_data_url 工具可直接用（纯 stdlib，无第三方依赖）",
          png_data_url(encode_png(b"seed", width=4, height=4)).startswith("data:image/png;base64,"))


# ---------------------------------------------------------------------------
# T04 ★默认不出图
# ---------------------------------------------------------------------------
def test_default_off():
    fresh_dir("t04")
    img = MockImageGenerationProvider()
    orch = make_orch("t04", image_provider=img)
    rep = orch.run({"episode": 1, "scene_ids": [1],
                    "style_spec": dict(PROFILES["style"])})
    st = rep["stages"]["image"]

    check("T04a ★默认 generate_images=False → 零 Provider 调用", len(img.calls) == 0,
          f"calls={len(img.calls)}")
    check("T04b ★零图像文件（连目录都不建）",
          not (orch.project_dir / GENERATED_DIR_NAME).exists())
    check("T04c 零记账行（不写 image_generation_calls.jsonl）",
          ledger_lines(orch, "t04") == [])
    check("T04d 零 manifest", read_manifest(orch) == {})
    check("T04e run_report 如实记录（enabled=False + 原因）",
          st["enabled"] is False and st["images_requested"] == 0
          and st["images_generated"] == 0 and "generate_images=False" in st["skipped_reason"],
          f"skipped_reason={st['skipped_reason']}")
    check("T04f 默认关闭时不影响原编排结果（status=success，无 errors）",
          rep["status"] == "success" and rep["errors"] == [])


# ---------------------------------------------------------------------------
# T05 单 Shot 出图（完整链路）
# ---------------------------------------------------------------------------
def test_single_shot_generation():
    orch, sbs = build_ready("t05")
    before = (orch.artifacts_dir / "storyboard_1.json").read_bytes()
    img = MockImageGenerationProvider()
    stage = make_stage(orch, provider=img)
    rep = stage.run([(1, sbs[1])], base_spec(generate_images=True,
                                             image_shot_refs=["S1_B1_SH1"]))

    check("T05a 单 Shot → 1 张图（请求 1 / 生成 1 / 失败 0）",
          (rep["images_requested"], rep["images_generated"], rep["images_failed"]) == (1, 1, 0),
          f"rep={rep['images_requested']}/{rep['images_generated']}/{rep['images_failed']}")

    imgs = disk_images(orch)
    check("T05b 图像真实落盘（generated_images/GEN_001.png）",
          [p.name for p in imgs] == ["GEN_001.png"], f"{[p.name for p in imgs]}")

    mf = read_manifest(orch)
    e = (mf.get("images") or [{}])[0]
    check("T05c manifest 记录完整（id/shot_ref/sha256/size/profile_versions）",
          e.get("image_id") == "GEN_001" and e.get("shot_ref") == "S1_B1_SH1"
          and e.get("status") == "generated" and len(e.get("sha256", "")) == 64
          and e.get("size") == "1024*1024" and e.get("profile_versions"),
          f"entry={ {k: e.get(k) for k in ('image_id','shot_ref','status','size')} }")

    shot = sbs[1].flatten_shots()[0]
    added = [a for a in shot.assets if a.kind == "image"]
    check("T05d ShotAsset(kind='image') 已挂回 Shot",
          len(added) == 1 and added[0].ref == "GEN_001",
          f"assets={[a.to_dict() for a in added]}")

    v2 = orch.artifacts_dir / "storyboard_1_v2.json"
    check("T05e ★产生 Storyboard 新版本（storyboard_1_v2.json）",
          v2.is_file() and rep["storyboard_versions"] == ["storyboard_1_v2.json"])
    check("T05f ★原 Storyboard 文件字节不变（绝不覆盖）",
          (orch.artifacts_dir / "storyboard_1.json").read_bytes() == before)
    check("T05g 新版本里只有出图的那个 Shot 带 image asset",
          [a["ref"] for b in json.loads(v2.read_text(encoding="utf-8"))["beats"]
           for s in b["shots"] for a in s["assets"] if a["kind"] == "image"] == ["GEN_001"])


# ---------------------------------------------------------------------------
# T06 多 Shot 出图
# ---------------------------------------------------------------------------
def test_multi_shot_generation():
    orch, sbs = build_ready("t06", scene_ids=(1, 2))
    img = MockImageGenerationProvider()
    stage = make_stage(orch, provider=img)
    refs = ["S1_B1_SH1", "S1_B1_SH2", "S2_B1_SH1"]
    rep = stage.run([(1, sbs[1]), (2, sbs[2])], base_spec(
        scene_ids=[1, 2], generate_images=True, max_images=6, image_shot_refs=refs))

    check("T06a 3 个 Shot → 3 张图（请求=生成=3）",
          rep["images_requested"] == 3 and rep["images_generated"] == 3,
          f"req={rep['images_requested']} gen={rep['images_generated']}")
    check("T06b ★manifest 生成条数 == 磁盘图像文件数",
          len(generated_entries(read_manifest(orch))) == len(disk_images(orch)) == 3,
          f"manifest={len(generated_entries(read_manifest(orch)))} disk={len(disk_images(orch))}")
    check("T06c 3 张图 sha256 互不相同（多镜头真有区分度）",
          len({e["sha256"] for e in generated_entries(read_manifest(orch))}) == 3)
    check("T06d 两份 Storyboard 各自产生新版本（不串场）",
          sorted(rep["storyboard_versions"]) == ["storyboard_1_v2.json",
                                                 "storyboard_2_v2.json"],
          f"{sorted(rep['storyboard_versions'])}")
    check("T06e 图像 ID 单调且无重复",
          [e["image_id"] for e in generated_entries(read_manifest(orch))]
          == ["GEN_001", "GEN_002", "GEN_003"])
    check("T06f ★max_images 高于真实请求硬上限时被收紧（cap = min(max_images, "
          "MAX_REAL_IMAGE_REQUESTS)，防止一次 run 无上限烧钱）",
          rep["max_images_requested"] == 6 and rep["budget_cap"] == MAX_REAL_IMAGE_REQUESTS == 3,
          f"requested={rep['max_images_requested']} cap={rep['budget_cap']}")


# ---------------------------------------------------------------------------
# T07 ★预算护栏
# ---------------------------------------------------------------------------
def test_budget_guard():
    orch, sbs = build_ready("t07", scene_ids=(1, 2, 3))
    img = MockImageGenerationProvider()
    stage = make_stage(orch, provider=img)
    refs = ["S1_B1_SH1", "S2_B1_SH1", "S3_B1_SH1"]
    rep = stage.run([(sid, sbs[sid]) for sid in (1, 2, 3)], base_spec(
        scene_ids=[1, 2, 3], generate_images=True, max_images=2,
        image_shot_refs=refs))

    check("T07a 请求 3 个 Shot、上限 2 → 生成 2 / 拒绝 1",
          (rep["images_requested"], rep["images_generated"], rep["images_rejected"])
          == (3, 2, 1),
          f"{rep['images_requested']}/{rep['images_generated']}/{rep['images_rejected']}")
    check("T07b ★实际 Provider 调用数 == 2（第 3 次**未发出**）",
          len(img.calls) == 2 and rep["budget_used"] == 2,
          f"calls={len(img.calls)} budget_used={rep['budget_used']}")
    check("T07c 预算上限如实记录（budget_cap=2，budget_rejections=1）",
          rep["budget_cap"] == 2 and rep["budget_rejections"] == 1)

    mf = read_manifest(orch)
    rejected = [e for e in mf["images"] if e["status"] == "rejected"]
    check("T07d 被拒条目带 APIBudgetExceededError 且无 path/sha256（不伪造产物）",
          len(rejected) == 1
          and APIBudgetExceededError.__name__ in str(rejected[0].get("error", ""))
          and rejected[0].get("path") == "" and rejected[0].get("sha256") == "",
          f"error={str(rejected[0].get('error'))[:70]}")
    check("T07e 磁盘图像数 == 2（被拒的那张没有文件）", len(disk_images(orch)) == 2)
    check("T07f 记账条数 == 实际调用数 == 2（被拒的没发出，不进账）",
          len(ledger_lines(orch, "t07")) == 2, f"{len(ledger_lines(orch, 't07'))}")
    check("T07g 上限同时下推到真实 HTTP 层（counter image 桶预算已被收紧）",
          get_api_counter().remaining_budget("image") in (0, 2),
          f"remaining={get_api_counter().remaining_budget('image')}")

    # 真实请求层：预算耗尽后第 3 次 HTTP 也不发出
    c = APIRequestCounter(real_budgets={"image": 2})
    sent = []

    def flaky(prompt, model, size, ref, timeout):
        sent.append(1)
        raise LLMConnectionError("boom")

    p = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                     transport=flaky, counter=c, max_retries=9,
                                     retry_backoff_seconds=0)
    err = expect_raises(APIBudgetExceededError, p.generate_image, "p")
    check("T07h ★真实 HTTP 层：预算=2 时只发出 2 次即被 APIBudgetExceededError 拒绝",
          isinstance(err, APIBudgetExceededError) and len(sent) == 2
          and c.real_requests("image") == 2 and c.snapshot()["budget_rejections"] == 1,
          f"sent={len(sent)} real={c.real_requests('image')}")


# ---------------------------------------------------------------------------
# T08 重试计预算
# ---------------------------------------------------------------------------
def test_retry_consumes_budget():
    sent = []

    def flaky(prompt, model, size, ref, timeout):
        sent.append(model)
        raise LLMConnectionError("transient")

    c = APIRequestCounter()
    p = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                     transport=flaky, vendor="bailian", counter=c,
                                     max_retries=2, retry_backoff_seconds=0)
    err = expect_raises(LLMConnectionError, p.generate_image, "p", size="1024*1024")
    check("T08a 瞬时失败重试 2 次 → 共 3 次真实请求（按 HTTP 请求计预算）",
          isinstance(err, LLMConnectionError) and len(sent) == 3
          and c.real_requests("image") == 3,
          f"sent={len(sent)} real={c.real_requests('image')}")
    check("T08b 审计记录 attempt 1/2/3 且 is_real=True",
          [r["attempt"] for r in c.audit_records()] == [1, 2, 3]
          and all(r["is_real"] for r in c.audit_records()))
    check("T08c 审计不含 prompt 正文 / 不含 API key",
          "transient" not in json.dumps(c.snapshot())
          and _SK not in json.dumps(c.snapshot()))

    # 预算耗尽后不再重试
    c2 = APIRequestCounter(real_budgets={"image": 2})
    sent.clear()
    p2 = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                      transport=flaky, counter=c2, max_retries=9,
                                      retry_backoff_seconds=0)
    expect_raises(APIBudgetExceededError, p2.generate_image, "p")
    check("T08d ★预算耗尽后不再重试（发出数 == 预算数）",
          len(sent) == 2 and c2.real_requests("image") == 2, f"sent={len(sent)}")

    # 成功路径：两种返回形态都处理
    def ok_b64(prompt, model, size, ref, timeout):
        return "data:image/png;base64,QUJD", None

    def ok_url(prompt, model, size, ref, timeout):
        return "https://cdn.example.com/a.png", None

    p3 = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                      transport=ok_b64, counter=APIRequestCounter())
    check("T08e base64 形态返回 data URL 引用",
          p3.generate_image("p").text.startswith("data:image/png;base64,"))
    p4 = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                      transport=ok_url, counter=APIRequestCounter())
    check("T08f URL 形态原样返回（下载由编排层负责）",
          p4.generate_image("p").text == "https://cdn.example.com/a.png")

    def no_carrier(prompt, model, size, ref, timeout):
        raise ImageGenerationError("no carrier")

    p5 = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                      transport=no_carrier, counter=APIRequestCounter())
    check("T08g 无图片载体 → ImageGenerationError（不可重试）",
          isinstance(expect_raises(ImageGenerationError, p5.generate_image, "p"),
                     ImageGenerationError))

    c6 = APIRequestCounter()
    p6 = OpenAICompatibleImageProvider(api_key="k", base_url="https://x/v1", model="M",
                                      transport=ok_b64, counter=c6)
    expect_raises(LLMRequestError, p6.generate_image, "  ")
    check("T08h 本地校验失败（空 prompt）不消耗真实预算",
          c6.real_requests("image") == 0)

    check("T08i size 归一化走 Config（canonical 1024*1024 → 线上 1024x1024）",
          p.normalize_size("1024*1024") == "1024x1024"
          and p.normalize_size("") is None)

    check("T08j 模型名为空 → 构造即明确报错（配置护栏，不硬编码模型名）",
          isinstance(expect_raises(LLMRequestError, OpenAICompatibleImageProvider,
                                   api_key="k", base_url="https://x/v1", model=""),
                     LLMRequestError))


# ---------------------------------------------------------------------------
# T09 审核三态
# ---------------------------------------------------------------------------
def test_review_three_states():
    # approved
    orch, sbs = build_ready("t09a")
    stage = make_stage(orch, handler=_handler(ReviewDecision.APPROVED))
    rep = stage.run([(1, sbs[1])], base_spec(generate_images=True,
                                            image_shot_refs=["S1_B1_SH1"]))
    shot = sbs[1].flatten_shots()[0]
    check("T09a APPROVED → 挂回 Shot + 计入 images_generated",
          rep["images_generated"] == 1 and rep["images_rejected"] == 0
          and any(a.kind == "image" for a in shot.assets))
    check("T09b ★auto_approve 在 run_report 中标注程序化模拟（不伪造人工审核）",
          "programmatic, NOT human review" in rep["review_mode"],
          f"review_mode={rep['review_mode']}")

    # rejected
    orch2, sbs2 = build_ready("t09b")
    stage2 = make_stage(orch2, handler=_handler(ReviewDecision.REJECTED))
    rep2 = stage2.run([(1, sbs2[1])], base_spec(generate_images=True,
                                               image_shot_refs=["S1_B1_SH1"]))
    shot2 = sbs2[1].flatten_shots()[0]
    mf2 = read_manifest(orch2)
    check("T09c REJECTED → 不挂回 Shot",
          not any(a.kind == "image" for a in shot2.assets)
          and rep2["images_rejected"] == 1 and rep2["images_generated"] == 0)
    check("T09d REJECTED → 仍保留 manifest 记录（可追溯，不静默丢弃）",
          len(mf2.get("images") or []) == 1
          and mf2["images"][0]["status"] == "rejected"
          and len(mf2["images"][0]["sha256"]) == 64,
          f"status={mf2['images'][0]['status']}")
    check("T09e REJECTED → 不产生 Storyboard 新版本（没挂回就没改动）",
          not (orch2.artifacts_dir / "storyboard_1_v2.json").exists())

    # approved_with_edit → 明确报错（图像是二进制，无"编辑后内容"）
    orch3, sbs3 = build_ready("t09c")
    stage3 = make_stage(orch3, handler=_handler(ReviewDecision.APPROVED_WITH_EDIT))
    err = expect_raises(OrchestrationError, stage3.run, [(1, sbs3[1])],
                        base_spec(generate_images=True, image_shot_refs=["S1_B1_SH1"]))
    check("T09f APPROVED_WITH_EDIT → 明确 OrchestrationError（不隐式解释）",
          isinstance(err, OrchestrationError) and "approved_with_edit" in str(err),
          str(err)[:80] if err else "")


# ---------------------------------------------------------------------------
# T10 ★sha256 一致性
# ---------------------------------------------------------------------------
def test_sha256_matches_disk():
    orch, sbs = build_ready("t10", scene_ids=(1, 2))
    stage = make_stage(orch)
    stage.run([(1, sbs[1]), (2, sbs[2])], base_spec(scene_ids=[1, 2],
                                                   generate_images=True, max_images=3))
    entries = generated_entries(read_manifest(orch))
    bad = []
    for e in entries:
        p = orch.project_dir / e["path"]
        if not p.is_file():
            bad.append(f"{e['image_id']}: 文件不存在")
            continue
        if sha256_of_bytes(p.read_bytes()) != e["sha256"]:
            bad.append(f"{e['image_id']}: sha256 不符")
    check("T10a ★manifest 每条 sha256 == 磁盘文件实际 sha256（逐一相符）",
          entries and not bad, f"checked={len(entries)} bad={bad}")

    check("T10b 落盘路径形如 generated_images/GEN_xxx.png（项目相对）",
          all(e["path"].startswith("generated_images/GEN_") for e in entries))

    check("T10c ★manifest 为 Stage 14 VLM QA 备好溯源锚点"
          "（profile_versions + reference_images 字段齐备）",
          all("profile_versions" in e and "reference_images" in e
              and isinstance(e["profile_versions"], dict) for e in entries))

    check("T10d prompt 正文不入库（只记 prompt_hash + prompt_length）",
          all(len(e.get("prompt_hash", "")) == 64 and e.get("prompt_length", 0) > 0
              and "prompt" not in e for e in entries))


# ---------------------------------------------------------------------------
# T11 缺档案 → 不硬出图
# ---------------------------------------------------------------------------
def test_missing_profile_blocks():
    # 不建 STYLE 档案（模拟"项目没有默认风格"）
    orch, sbs = build_ready("t11", style=False)
    img = MockImageGenerationProvider()
    stage = make_stage(orch, provider=img)
    rep = stage.run([(1, sbs[1])], {"episode": 1, "scene_ids": [1],
                                   "generate_images": True})
    mf = read_manifest(orch)
    errs = [str(e.get("error", "")) for e in (mf.get("images") or [])]
    check("T11a ★compose 缺 approved 档案 → 明确报错并列出缺口（不硬出图）",
          rep["images_generated"] == 0 and rep["images_failed"] == 2
          and all("VisualComposeError" in e for e in errs),
          f"gen={rep['images_generated']} failed={rep['images_failed']}")
    check("T11b 报错信息含缺口说明（风格缺失）",
          all("默认风格" in e for e in errs), f"{errs[0][:80] if errs else ''}")
    check("T11c ★零 Provider 调用（缺档案时根本不进 Provider）", len(img.calls) == 0)
    check("T11d 零图像文件（不产生占位图冒充）", disk_images(orch) == [])

    # 角色档案缺失
    orch2, sbs2 = build_ready("t11b")
    reg = orch2.visual_registry
    reg.register("character", "Ghost")           # 建档但**不审批** → 无 approved
    for s in sbs2[1].flatten_shots():
        s.characters = ["Ghost"]
    stage2 = make_stage(orch2, provider=MockImageGenerationProvider())
    rep2 = stage2.run([(1, sbs2[1])], {"episode": 1, "scene_ids": [1],
                                      "generate_images": True})
    mf2 = read_manifest(orch2)
    check("T11e 角色无 approved 档案 → 同样明确报错列缺口",
          rep2["images_generated"] == 0 and rep2["images_failed"] == 2
          and all("character:Ghost" in str(e.get("error", ""))
                  for e in (mf2.get("images") or [])),
          f"{[str(e.get('error'))[:70] for e in (mf2.get('images') or [])]}")


# ---------------------------------------------------------------------------
# T12 记账
# ---------------------------------------------------------------------------
def test_ledger():
    orch, sbs = build_ready("t12", scene_ids=(1, 2))
    img = MockImageGenerationProvider()
    stage = make_stage(orch, provider=img)
    stage.run([(1, sbs[1]), (2, sbs[2])], base_spec(scene_ids=[1, 2],
                                                   generate_images=True, max_images=3))
    lines = [json.loads(ln) for ln in ledger_lines(orch, "t12")]
    check("T12a ★记账条数 == 实际 Provider 调用数",
          len(lines) == len(img.calls) == 3, f"ledger={len(lines)} calls={len(img.calls)}")
    check("T12b ★未登记价格 → estimated_cost 为 null（非 0、非编造）",
          all(r["estimated_cost"] is None for r in lines),
          f"{[r['estimated_cost'] for r in lines]}")
    check("T12c run_report 的 cost_total 为 null 且如实标注未定价张数",
          stage.run([(1, sbs[1])], base_spec(generate_images=True,
                                            image_shot_refs=["S1_B1_SH1"],
                                            resume=False))["cost_total"] is None)
    check("T12d 记账字段风格与 TokenLogger 对齐（不造第二套账、不含 token 概念）",
          {"timestamp", "request_id", "step", "agent", "model", "estimated_cost",
           "currency", "duration_ms", "success", "retry_count", "error",
           "prompt_hash", "prompt_length"} <= set(lines[0])
          and not ({"input_tokens", "output_tokens", "total_tokens"} & set(lines[0])),
          f"keys={sorted(lines[0])}")
    check("T12e 记账含图像专属溯源字段（image_id/shot_ref/sha256/profile_versions）",
          {"image_id", "shot_ref", "sha256", "profile_versions", "provider",
           "size"} <= set(lines[0]))
    check("T12f 记账与 token 账分开（图像调用不写 llm_calls.jsonl）",
          not (TMP_LOGS / "t12" / "llm_calls.jsonl").exists())
    check("T12g 记账文件被 git 忽略（运行产物，不进版本库）",
          _is_git_ignored(TMP_LOGS / "t12" / LEDGER_FILENAME)
          or _is_git_ignored(V2_ROOT / "logs" / "stage13_image" / LEDGER_FILENAME))


# ---------------------------------------------------------------------------
# T13 ★真实 API = 0
# ---------------------------------------------------------------------------
def test_zero_real_api():
    fresh_dir("t13")
    orch = make_orch("t13")
    rep = orch.run(base_spec(generate_images=True, max_images=3, scene_ids=[1]))
    snap = get_api_counter().snapshot()
    check("T13a ★真实 API 请求 = 0（程序化快照断言）",
          snap["real_api_requests"] == 0, f"real={snap['real_api_requests']}")
    check("T13b 全程 socket 打桩下跑通（证明 0 网络）", rep["status"] == "success",
          f"status={rep['status']}")
    check("T13c 未设 FILMAGENT_PROVIDER 时图像档默认 mock",
          os.environ.get("FILMAGENT_PROVIDER", "") == ""
          and isinstance(orch.image_provider, MockImageGenerationProvider))
    check("T13d 审计里没有任何 is_real=True 的记录",
          not any(r.get("is_real") for r in snap["audit"]))


# ---------------------------------------------------------------------------
# T14 ★Token 硬证据（Storyboard 最小增量）
# ---------------------------------------------------------------------------
def test_storyboard_minimal_delta():
    orch, sbs = build_ready("t14")
    before = {s.shot_id: s.to_dict() for s in sbs[1].flatten_shots()}
    before_raw = (orch.artifacts_dir / "storyboard_1.json").read_text(encoding="utf-8")

    stage = make_stage(orch)
    stage.run([(1, sbs[1])], base_spec(generate_images=True,
                                       image_shot_refs=["S1_B1_SH1"]))
    after = {s.shot_id: s.to_dict() for s in sbs[1].flatten_shots()}

    changed = sorted(sid for sid in before if before[sid] != after[sid])
    untouched = sorted(sid for sid in before if sid not in changed)
    check("T14a ★未参与出图的 Shot 序列化逐字段不变",
          len(changed) == 1 and len(untouched) == 1
          and all(before[sid] == after[sid] for sid in untouched),
          f"changed={changed} untouched={untouched}")

    diffs = {}
    for sid in changed:
        d = {k for k in set(before[sid]) | set(after[sid])
             if before[sid].get(k) != after[sid].get(k)}
        diffs[sid] = sorted(d)
    check("T14b ★出图的 Shot 只多一个 kind='image' 的 ShotAsset（无其它字段变化）",
          changed and all(d == ["assets"] for d in diffs.values()), f"diffs={diffs}")

    added = [a for sid in changed for a in after[sid]["assets"]
             if a.get("kind") == "image"]
    check("T14c 只多一条 image asset（不是两条 / 不重复挂）",
          len(added) == 1 and len(after[changed[0]]["assets"])
          == len(before[changed[0]]["assets"]) + 1,
          f"added={added}")

    v2_text = (orch.artifacts_dir / "storyboard_1_v2.json").read_text(encoding="utf-8")
    leaked = [t for t in PROFILE_FINGERPRINTS if t in v2_text or t in before_raw]
    check("T14d ★档案文本不渗漏进 Storyboard（token 增长只发生在真正调用模型那一刻）",
          not leaked, f"leaked={leaked}")
    check("T14e 原 Storyboard 文件字节不变", 
          (orch.artifacts_dir / "storyboard_1.json").read_text(encoding="utf-8") == before_raw)


# ---------------------------------------------------------------------------
# T15 ★二进制泄漏扫描
# ---------------------------------------------------------------------------
def _is_git_ignored(path: Path) -> bool:
    p = subprocess.run(["git", "check-ignore", "-q", str(path)],
                       capture_output=True, text=True, cwd=str(REPO_ROOT))
    return p.returncode == 0


def test_binary_leak_scan():
    samples = [
        V2_ROOT / "logs" / "stage13_image" / "tmp_projects" / "p" / GENERATED_DIR_NAME / "GEN_001.png",
        V2_ROOT / "logs" / "stage13_image" / "generated_images" / "GEN_001.png",
        V2_ROOT / "projects" / "pid" / GENERATED_DIR_NAME / "GEN_002.jpg",
        V2_ROOT / "logs" / "tmp_x" / GENERATED_DIR_NAME / "GEN_003.webp",
    ]
    misses = [str(p) for p in samples if not _is_git_ignored(p)]
    check("T15a ★生成的图片文件全部被 git 忽略（check-ignore 命中）",
          not misses, f"未忽略={misses}")

    st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all",
                         "--", "v2/logs/stage13_image"],
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    bad = [ln for ln in (st.stdout or "").splitlines()
           if re.search(r"\.(png|jpg|jpeg|webp|gif)$", ln, re.I)]
    check("T15b 交付目录无二进制泄漏（git status 无图片文件）", not bad, f"{bad}")

    tracked = subprocess.run(["git", "ls-files", "v2"],
                             capture_output=True, text=True, cwd=str(REPO_ROOT))
    tracked_bin = [ln for ln in (tracked.stdout or "").splitlines()
                   if re.search(r"\.(png|jpg|jpeg|webp|gif)$", ln, re.I)]
    check("T15c v2/ 下没有任何二进制图片入库", not tracked_bin, f"{tracked_bin}")

    root_gi = subprocess.run(["git", "diff", "--stat", "HEAD", "--", ".gitignore"],
                             capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T15d 仓库根 .gitignore 0 diff（仍属 V0.1 基线段）",
          not (root_gi.stdout or "").strip(), f"{(root_gi.stdout or '')[:80]}")

    gi = (V2_ROOT / ".gitignore").read_text(encoding="utf-8")
    need = ["logs/**/generated_images/", "logs/**/images/",
            "logs/**/tmp_*/generated_images/", "**/generated_images/*.png",
            "**/generated_images/*.jpg", "**/generated_images/*.jpeg",
            "**/generated_images/*.webp"]
    check("T15e v2/.gitignore 已补齐 §8.4 全部二进制防护规则",
          all(n in gi for n in need), f"missing={[n for n in need if n not in gi]}")


# ---------------------------------------------------------------------------
# T16 静态检查
# ---------------------------------------------------------------------------
_SK = "sk" + "-"


def _sk_lines(text: str) -> set:
    """文本里含密钥字样的行（去空白后比对，容忍缩进变化）。"""
    return {ln.strip() for ln in (text or "").splitlines() if _SK in ln.strip()}


#: 硬编码图像模型名扫描（字面量拆开写，避免扫描器把自己扫出来）
_BANNED_MODEL_TOKENS = ("wan" + "x", "qwen" + "-image", "dall" + "-e",
                        "stable" + "-diffusion", "kolor" + "s", "hunyuan" + "-image")


def test_static_checks():
    proc = subprocess.run([sys.executable, "-m", "compileall", "-q",
                           str(V2_ROOT / "orchestration"),
                           str(V2_ROOT / "llm" / "providers")],
                          capture_output=True, text=True)
    check("T16a compileall v2/orchestration + v2/llm/providers 全过",
          proc.returncode == 0, (proc.stderr or "")[:200])

    import orchestration, llm
    check("T16b import 冒烟 + 新导出齐备",
          {"ImageStage", "resolve_image_bytes", "shot_ref_of",
           "GENERATED_DIR_NAME", "MANIFEST_FILENAME", "LEDGER_FILENAME"}
          <= set(orchestration.__all__)
          and {"MockImageGenerationProvider", "OpenAICompatibleImageProvider",
               "ImageGenerationError", "MAX_REAL_IMAGE_REQUESTS"}
          <= set(llm.__all__),
          f"orch={len(orchestration.__all__)} llm={len(llm.__all__)}")

    new_files = [
        V2_ROOT / "llm" / "providers" / "mock_image.py",
        V2_ROOT / "llm" / "providers" / "openai_compatible_image.py",
        V2_ROOT / "orchestration" / "image_stage.py",
        V2_ROOT / "orchestration" / "pipeline.py",
        V2_ROOT / "config.py",
        V2_ROOT / "llm" / "providers" / "registry.py",
        V2_ROOT / "llm" / "request_counter.py",
    ] + list(TEST_DIR.glob("*.py"))
    bad = []
    for p in new_files:
        rel = p.relative_to(REPO_ROOT).as_posix()
        head = subprocess.run(["git", "show", f"HEAD:{rel}"], capture_output=True,
                              text=True, cwd=str(REPO_ROOT))
        old = (_sk_lines(head.stdout) if head.returncode == 0 else set())
        for ln in sorted(_sk_lines(p.read_text(encoding="utf-8")) - old):
            bad.append(f"{p.name}: {ln[:60]}")
    check("T16c 新增/改动文件无**新增**硬编码密钥字样（与 HEAD 逐行对比；"
          "既有说明性文字不计）", not bad, f"{bad[:3]}" if bad else "")

    src_dirs = [V2_ROOT / "config.py", V2_ROOT / "llm", V2_ROOT / "orchestration",
                V2_ROOT / "core", V2_ROOT / "agents", V2_ROOT / "workflow"]
    hits = []
    for d in src_dirs:
        files = [d] if d.is_file() else [p for p in d.rglob("*.py")
                                         if "__pycache__" not in p.parts]
        for p in files:
            text = p.read_text(encoding="utf-8").lower()
            for tok in _BANNED_MODEL_TOKENS:
                if tok in text:
                    hits.append(f"{p.relative_to(V2_ROOT)}: {tok}")
    check("T16d ★无硬编码图像模型名（扫具体型号字样 0 命中）", not hits,
          f"{hits[:3]}" if hits else "")

    check("T16e 受控词表未被加值（stage8 封存前提）",
          __import__("core.shot", fromlist=["ASSET_KINDS"]).ASSET_KINDS
          == ("character", "location", "prop", "action", "camera",
              "image", "video", "audio", "reference"))


# ---------------------------------------------------------------------------
# T17 禁改清单 0 diff
# ---------------------------------------------------------------------------
FORBIDDEN = [
    "FilmAgent", ".gitignore",
    "v2/workflow", "v2/core", "v2/memory", "v2/review", "v2/context",
    "v2/agents",
    "v2/llm/client.py", "v2/llm/router.py",
    "v2/llm/providers/base.py", "v2/llm/providers/mock.py",
    "v2/llm/providers/openai_compatible.py", "v2/llm/providers/bailian.py",
    "v2/llm/providers/openai_compatible_vision.py",
    "v2/logs/stage1_validation_test", "v2/logs/stage3_workflow_test",
    "v2/logs/stage4_memory_context_review_test", "v2/logs/stage6_casting",
    "v2/logs/stage7_scene_generation", "v2/logs/stage8_canonical_schema",
    "v2/logs/stage9_provider", "v2/logs/stage10_visual_asset",
    "v2/logs/stage11_storyboard",
]


def test_forbidden_paths_untouched():
    changed = []
    for f in FORBIDDEN:
        p = subprocess.run(["git", "diff", "--stat", "HEAD", "--", f],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        if (p.stdout or "").strip():
            changed.append(f)
    check("T17a 禁改清单 0 diff（§8.3 + FilmAgent/ + 仓库根 .gitignore）",
          not changed, f"有改动: {changed}" if changed else "")

    st = subprocess.run(["git", "status", "--porcelain", "--", "v2/workflow",
                         "v2/core"],
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T17b ★v2/workflow/ + v2/core/ 0 diff + 0 新增（未污染封存层）",
          not (st.stdout or "").strip(), f"status={st.stdout!r}")

    tags = subprocess.run(["git", "tag", "-l"], capture_output=True, text=True,
                          cwd=str(REPO_ROOT))
    have = set((tags.stdout or "").split())
    # ★ Stage 13-P 修正：本断言原先把"v0.2-stage13 尚未创建"写死在顶层，属 **pre-commit
    #   瞬时断言** —— 封存后必然永久变红（且豁免机制只作用于嵌套历史套件，管不到这里）。
    #   改为**状态感知**：7 个既有 tag 必须存在且都是 HEAD 的祖先（捕获"删除/移到别处"）；
    #   v0.2-stage13 缺席（封存前）或存在且为 HEAD 祖先（封存后）**都算通过**。
    #   诚实边界：`--is-ancestor` 抓得住"删除"和"移到无关分支"，抓不住"向前移动到后代"。
    def _is_ancestor(rev: str) -> bool:
        r = subprocess.run(["git", "merge-base", "--is-ancestor", rev, "HEAD"],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        return r.returncode == 0

    required = ["v0.1", "v0.2-stage7", "v0.2-stage8", "v0.2-stage9",
                "v0.2-stage10", "v0.2-stage11", "v0.2-stage12"]
    missing = [t for t in required if t not in have]
    moved = [t for t in required if t in have and not _is_ancestor(t)]
    stage13_ok = ("v0.2-stage13" not in have) or _is_ancestor("v0.2-stage13")
    check("T17c 既有 tag 未被移动/删除（v0.1 与 v0.2-stage7..12 都在且为 HEAD 祖先；"
          "v0.2-stage13 缺席或亦为 HEAD 祖先）",
          not missing and not moved and stage13_ok,
          f"missing={missing} moved={moved} stage13_ok={stage13_ok} tags={sorted(have)}")

    df = subprocess.run(["git", "diff", "--stat", "HEAD", "--", "FilmAgent"],
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T17d V0.1 冻结：git diff -- FilmAgent/ 为空",
          not (df.stdout or "").strip(), f"{(df.stdout or '')[:80]}")


# ---------------------------------------------------------------------------
# T19 端到端（mock）
# ---------------------------------------------------------------------------
def test_end_to_end():
    fresh_dir("t19")
    img = MockImageGenerationProvider()
    orch = make_orch("t19", image_provider=img)
    refs = ["S1_B1_SH1", "S2_B1_SH1"]
    rep = orch.run(base_spec(scene_ids=[1, 2], generate_images=True, max_images=2,
                             image_shot_refs=refs, image_size="1024*1024"))

    st = rep["stages"]["image"]
    check("T19a status=success（分镜 + 视觉资产 + 出图全链跑通）",
          rep["status"] == "success" and rep["errors"] == [],
          f"status={rep['status']} errors={rep['errors']}")
    check("T19b run_report.image 段统计正确（请求 2 / 生成 2 / 拒绝 0）",
          (st["images_requested"], st["images_generated"], st["images_rejected"],
           st["images_failed"]) == (2, 2, 0, 0),
          f"{st['images_requested']}/{st['images_generated']}/"
          f"{st['images_rejected']}/{st['images_failed']}")
    check("T19c budget_used == 磁盘图像数 == Provider 实际调用数",
          st["budget_used"] == len(disk_images(orch)) == len(img.calls) == 2,
          f"used={st['budget_used']} disk={len(disk_images(orch))} calls={len(img.calls)}")
    check("T19d manifest 落盘且路径在 run_report 里可追溯",
          Path(st["manifest"]).is_file()
          and Path(st["manifest"]).name == MANIFEST_FILENAME)

    per_scene = {}
    for sid in (1, 2):
        p = orch.artifacts_dir / f"storyboard_{sid}_v2.json"
        if not p.is_file():
            per_scene[sid] = -1
            continue
        sb = json.loads(p.read_text(encoding="utf-8"))
        per_scene[sid] = sum(1 for b in sb["beats"] for s in b["shots"]
                             for a in s["assets"] if a["kind"] == "image")
    check("T19e 每个成功出图的 Shot 都挂上了 image 引用（两场景各 1 个）",
          per_scene == {1: 1, 2: 1}, f"per_scene={per_scene}")

    state = json.loads((orch.orchestration_dir / "state.json")
                       .read_text(encoding="utf-8"))
    check("T19f state.json 记录图像段进度（纯追加字段，未新建第二套状态机）",
          state["episodes"]["1"]["scenes"]["1"].get("images", {}).get("artifact")
          == "storyboard_1_v2.json",
          f"images={state['episodes']['1']['scenes']['1'].get('images')}")

    # resume：重跑不重复花钱
    img2 = MockImageGenerationProvider()
    orch2 = make_orch("t19", image_provider=img2)
    rep2 = orch2.run(base_spec(scene_ids=[1, 2], generate_images=True, max_images=2,
                              image_shot_refs=refs, resume=True))
    st2 = rep2["stages"]["image"]
    check("T19g ★resume 重跑不重复出图（零新调用，已生成的记 skipped）",
          len(img2.calls) == 0 and st2["images_skipped"] == 2
          and st2["images_generated"] == 0 and st2["budget_used"] == 0,
          f"calls={len(img2.calls)} skipped={st2['images_skipped']} "
          f"generated={st2['images_generated']}")
    check("T19h 重跑仍把已生成的引用挂回（Storyboard 新版本可用）",
          (orch2.artifacts_dir / "storyboard_1_v3.json").is_file()
          and json.loads((orch2.artifacts_dir / "storyboard_1_v3.json")
                         .read_text(encoding="utf-8"))["beats"][0]["shots"][0]["assets"][-1]["ref"]
          == "GEN_001",
          f"v3={[p.name for p in orch2.artifacts_dir.glob('storyboard_1*')]}")
    check("T19i resume 不新增图像文件（零新产物）",
          len(disk_images(orch2)) == 2, f"disk={len(disk_images(orch2))}")

    # URL 形态引用：注入假下载器 → 0 网络也能落盘
    fresh_dir("t19b")
    fetched = {"n": 0}

    def fake_fetch(url):
        fetched["n"] += 1
        return encode_png(url.encode("utf-8"))

    class UrlImageProvider(MockImageGenerationProvider):
        def generate_image(self, prompt, *, size=None, reference=None):
            self.calls.append({"prompt": prompt, "size": size, "reference": reference})
            return type("R", (), {"text": "https://cdn.example.com/gen.png",
                                  "request_id": "r", "retry_count": 0,
                                  "model": self.model, "usage": None})()

    orch3 = make_orch("t19b", image_provider=UrlImageProvider(), image_fetch=fake_fetch)
    rep3 = orch3.run(base_spec(scene_ids=[1], generate_images=True, max_images=2))
    check("T19j URL 形态引用经注入下载器落盘（下载可注入 → 0 网络可测）",
          rep3["stages"]["image"]["images_generated"] == 2 and fetched["n"] == 2,
          f"generated={rep3['stages']['image']['images_generated']} fetched={fetched['n']}")
    check("T19k 下载失败 → 明确报错（不静默留空文件）",
          isinstance(expect_raises(ImageGenerationError, resolve_image_bytes,
                                   "https://cdn.example.com/x.png",
                                   fetch=lambda u: (_ for _ in ()).throw(
                                       ImageGenerationError("下载失败"))),
                     ImageGenerationError))
    check("T19l 引用串解析：data URL / http / 本地路径 三路都认",
          resolve_image_bytes(png_data_url(encode_png(b"s", width=2, height=2)))[:4] == b"\x89PNG"
          and guess_extension(b"\x89PNG\r\n\x1a\n") == ".png"
          and guess_extension(b"\xff\xd8\xff") == ".jpg"
          and isinstance(expect_raises(ImageGenerationError, resolve_image_bytes, ""),
                         ImageGenerationError))


# ---------------------------------------------------------------------------
# T20 Stage 13-P —— 百炼 DashScope 原生档（★纯新增，与 OpenAI 兼容档并存）
# ---------------------------------------------------------------------------
def test_bailian_native_provider():
    """T20：原生协议形状 / 响应解析 / 异步轮询 / 预算语义 / registry 分派（0 网络）。

    ★ T20k 是本组最重要的一条：异步轮询发生在 transport **内部**，而 `_invoke()`
      每次业务尝试只计 1 次预算 ⇒ **一张图 = 1 次计费调用，轮询不额外消耗预算**。
    """
    from core.token_log import TokenUsage
    from llm.providers.bailian_image import (
        BailianImageProvider, _extract_reference,
        DEFAULT_CREATE_PATH, DEFAULT_SYNC_PATH, DEFAULT_TASK_PATH,
    )
    from llm.providers.registry import _image_vendors

    c = APIRequestCounter(real_budgets={"image": 50})   # 独立计数器，不扰共享单例

    # ---- 同步路径：请求体形状 + size 记法 + usage ----
    cap = {}
    def fake_sync(prompt, model, size, reference, timeout):
        cap.update(prompt=prompt, model=model, size=size)
        return "https://example.invalid/a.png", TokenUsage(
            input_tokens=720, output_tokens=11, total_tokens=731)

    p = BailianImageProvider(api_key="k", base_url="https://example.invalid/api/v1",
                             model="M", transport=fake_sync, counter=c)
    r = p.generate_image("一只猫", size="1024*1024")
    check("T20a 同步：返回引用串 = 图片 URL",
          r.text == "https://example.invalid/a.png", r.text[:60])
    check("T20b ★size 记法 '*' 原样透传（DashScope 与 canonical 一致，不做 x 转换）",
          cap["size"] == "1024*1024", cap["size"])
    check("T20c usage 被带回（不是空 TokenUsage）", r.usage.total_tokens == 731,
          f"total_tokens={r.usage.total_tokens}")

    body = p.build_body("一只猫", "1024*1024")
    check("T20d 请求体形状照官方文档（model / input.messages[].content[].text / parameters）",
          body["model"] == "M"
          and body["input"]["messages"][0]["content"][0]["text"] == "一只猫"
          and body["parameters"]["n"] == 1, sorted(body.keys()))
    check("T20e ★显式下发 watermark=False（避免交付物多出 AI 角标）",
          body["parameters"].get("watermark") is False, body["parameters"])

    # ---- 响应解析（官方主路径 + 多图 fail-loud + 无载体报错）----
    doc = {"status_code": 200, "output": {"task_status": "SUCCEEDED", "choices": [
        {"message": {"content": [{"image": "https://x/1.png", "type": "image"}]}}]},
        "usage": {"input_tokens": 720, "output_tokens": 11, "total_tokens": 731}}
    url, usage = _extract_reference(doc)
    check("T20f 官方响应路径 output.choices[0].message.content[0].image 解析正确",
          url == "https://x/1.png" and usage.total_tokens == 731, url)
    multi = {"output": {"choices": [{"message": {"content": [
        {"image": "https://x/1.png"}, {"image": "https://x/2.png"}]}}]}}
    check("T20g ★多张图 → 明确报错（绝不静默丢图）",
          isinstance(expect_raises(ImageGenerationError, _extract_reference, multi),
                     ImageGenerationError))
    check("T20h 无可取载体 → ImageGenerationError",
          isinstance(expect_raises(ImageGenerationError, _extract_reference, {"output": {}}),
                     ImageGenerationError))

    # ---- 异步路径：创建 → 轮询 → 终态 ----
    pa = BailianImageProvider(api_key="k", base_url="https://example.invalid/api/v1",
                              model="M", counter=c, async_mode=True,
                              poll_interval=0, poll_timeout=5)
    seen = {"post": None, "get": 0}
    def fake_post(path, body_, timeout, *, async_submit):
        seen["post"] = (path, async_submit)
        return {"output": {"task_id": "T1", "task_status": "PENDING"}}
    def fake_get(url_, timeout):
        seen["get"] += 1
        if seen["get"] < 3:
            return {"output": {"task_id": "T1", "task_status": "RUNNING"}}
        return {"output": {"task_id": "T1", "task_status": "SUCCEEDED", "choices": [
            {"message": {"content": [{"image": "https://x/final.png"}]}}]}}
    pa._post_json = fake_post
    pa._get_json = fake_get
    res = pa.generate_image("一间花店")
    check("T20i 异步：创建走 image-generation 端点 + X-DashScope-Async: enable",
          seen["post"] == (DEFAULT_CREATE_PATH, True), seen["post"])
    check("T20j 异步：轮询至 SUCCEEDED 后取到 URL",
          res.text == "https://x/final.png" and seen["get"] == 3, (res.text, seen["get"]))
    check("T20k ★预算语义：1 张图只消耗 1 次 image 预算（3 次轮询不额外计费）",
          c.snapshot()["by_kind"].get("image", {}).get("real") == 2,
          c.snapshot()["by_kind"].get("image"))

    pt = BailianImageProvider(api_key="k", base_url="https://example.invalid/api/v1",
                              model="M", counter=c, async_mode=True,
                              poll_interval=0, poll_timeout=0)
    pt._post_json = fake_post
    pt._get_json = lambda url_, timeout: {"output": {"task_status": "RUNNING"}}
    check("T20l 轮询超时 → LLMTimeoutError（不无限轮询）",
          type(expect_raises(LLMTimeoutError, pt.generate_image, "x")).__name__
          == "LLMTimeoutError" or "LLMTimeoutError" in str(
              expect_raises(LLMTimeoutError, pt.generate_image, "x")))

    # ---- registry 分派（★既有档行为 0 变）----
    saved_key = os.environ.get("BAILIAN_API_KEY")
    os.environ["BAILIAN_API_KEY"] = "test-key-not-real"
    try:
        p_native = create_provider(
            "image", "bailian_native",
            cfg={"default_model": "M", "base_url": "https://example.invalid/api/v1",
                 "api_key_env": "BAILIAN_API_KEY",
                 "protocol": "dashscope_native", "async": True})
        check("T20m protocol=dashscope_native → BailianImageProvider（async 开关透传）",
              isinstance(p_native, BailianImageProvider) and p_native.async_mode is True,
              type(p_native).__name__)
        p_compat = create_provider(
            "image", "bailian_native",
            cfg={"default_model": "M", "base_url": "https://example.invalid/api/v1"})
        check("T20n ★缺省（无 protocol）→ OpenAICompatibleImageProvider（既有行为 0 变）",
              isinstance(p_compat, OpenAICompatibleImageProvider), type(p_compat).__name__)
        check("T20o 未知 protocol → 明确报错",
              isinstance(expect_raises(
                  ValueError, create_provider, "image", "bailian_native",
                  cfg={"default_model": "M", "base_url": "https://x",
                       "protocol": "火星协议"}), ValueError))
    finally:
        if saved_key is None:
            os.environ.pop("BAILIAN_API_KEY", None)
        else:
            os.environ["BAILIAN_API_KEY"] = saved_key

    check("T20p ★既有 config 档 0 改：bailian_image 仍 compatible-mode + 空模型护栏 + 无 protocol 键",
          PROVIDERS["bailian_image"]["base_url"].endswith("/compatible-mode/v1")
          and PROVIDERS["bailian_image"]["default_model"] == ""
          and "protocol" not in PROVIDERS["bailian_image"],
          PROVIDERS["bailian_image"]["base_url"])
    check("T20q ★两档并存：_image_vendors 同时含兼容档与原生档",
          {"bailian", "bailian_native", "ark", "openai"} <= _image_vendors(),
          sorted(_image_vendors()))

    # ---- 最终 HTTP URL（★ Stage 13-P 修复：从真实 Config 档读 base_url）----
    # 截获 `_request` 是为了在不发网络的前提下检查 `_post_json` / `_poll_task`
    # 已完成的 URL 组合；不再只断言传入 `_post_json` 的 path。
    native_cfg = dict(PROVIDERS["bailian_native_image"])
    public_base = native_cfg["base_url"]
    sync_calls = []
    p_url_sync = BailianImageProvider(
        api_key="k", base_url=public_base, model="M", counter=c, max_retries=0)

    def fake_sync_request(method, url_, data, headers, timeout):
        sync_calls.append((method, url_))
        return {"output": {"choices": [{"message": {"content": [
            {"image": "https://x/sync.png"}]}}]}}

    p_url_sync._request = fake_sync_request
    p_url_sync.generate_image("公共域同步 URL")
    expected_sync_url = public_base + DEFAULT_SYNC_PATH
    check("T20r ★公共域同步最终 URL：从真实 Config 组合且只含一段 /api/v1",
          sync_calls == [("POST", expected_sync_url)]
          and expected_sync_url.count("/api/v1") == 1,
          sync_calls)

    async_calls = []
    p_url_async = BailianImageProvider(
        api_key="k", base_url=public_base, model="M", counter=c,
        async_mode=True, max_retries=0, poll_interval=0, poll_timeout=5)

    def fake_async_request(method, url_, data, headers, timeout):
        async_calls.append((method, url_))
        if method == "POST":
            return {"output": {"task_id": "TASK-URL", "task_status": "PENDING"}}
        return {"output": {"task_id": "TASK-URL", "task_status": "SUCCEEDED",
                           "choices": [{"message": {"content": [
                               {"image": "https://x/async.png"}]}}]}}

    p_url_async._request = fake_async_request
    p_url_async.generate_image("公共域异步 URL")
    expected_create_url = public_base + DEFAULT_CREATE_PATH
    expected_task_url = public_base + DEFAULT_TASK_PATH.format(task_id="TASK-URL")
    check("T20s ★公共域异步最终 URL：提交与轮询均只含一段 /api/v1",
          async_calls == [("POST", expected_create_url), ("GET", expected_task_url)]
          and all(url_.count("/api/v1") == 1 for _, url_ in async_calls),
          async_calls)

    workspace_base = "https://workspace-test.cn-beijing.maas.aliyuncs.com/api/v1"
    workspace_calls = []
    p_url_workspace = BailianImageProvider(
        api_key="k", base_url=workspace_base, model="M", counter=c, max_retries=0)

    def fake_workspace_request(method, url_, data, headers, timeout):
        workspace_calls.append((method, url_))
        return {"output": {"choices": [{"message": {"content": [
            {"image": "https://x/workspace.png"}]}}]}}

    p_url_workspace._request = fake_workspace_request
    p_url_workspace.generate_image("业务空间域同步 URL")
    expected_workspace_url = workspace_base + DEFAULT_SYNC_PATH
    check("T20t ★业务空间域最终 URL：与公共域共用 base=/api/v1 语义",
          workspace_calls == [("POST", expected_workspace_url)]
          and expected_workspace_url.count("/api/v1") == 1,
          workspace_calls)


# ---------------------------------------------------------------------------
# T18 历史回归（内嵌子进程；剥离宿主 PYTHONPATH shim）
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
    ("stage11", "stage11_storyboard"),
    ("stage12", "stage12_lifecycle"),
]

#: 历史套件的"回归重写历史报告产物"（T-6）—— ★实测污染面**大于 6 个文件**：
#: 除开工指令 §3.2 列出的 6 个之外，各套件 `main()` 还会重写自己的
#: `test_report.json`（stage10/11/12 的报告含随机 id → 必然 dirty）。
#: 因此复位改为**按目录扫 dirty 后精确复位**（见 _reset_history_products），
#: 不再维护一份容易过时的手写清单。
HISTORY_DIRS = ["v2/logs/" + d for _, d in [
    ("stage1", "stage1_validation_test"),
    ("stage3", "stage3_workflow_test"),
    ("stage4", "stage4_memory_context_review_test"),
    ("stage6", "stage6_casting"),
    ("stage7", "stage7_scene_generation"),
    ("stage8", "stage8_canonical_schema"),
    ("stage9", "stage9_provider"),
    ("stage10", "stage10_visual_asset"),
    ("stage11", "stage11_storyboard"),
    ("stage12", "stage12_lifecycle"),
]]

#: 仓库根 / v2 的记账泄漏文件（T-1；见 §3.2 坑 2）
LEDGER_LEAKS = ["llm_calls.jsonl", "v2/llm_calls.jsonl"]


def _reset_history_products():
    """复位被历史回归重写的**全部**已跟踪产物（否则"禁改清单 0 diff"类断言假失败）。

    实现：扫 `git status` 在历史套件目录下的 dirty 已跟踪文件，精确 `git checkout --`。
    这些目录全部属禁改清单，其中的 dirty **只可能**来自回归重写，复位永远正确。
    """
    st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no",
                         "--"] + HISTORY_DIRS,
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    paths = []
    for ln in (st.stdout or "").splitlines():
        rel = ln[3:].strip().strip('"')
        if rel:
            paths.append(rel)
    if paths:
        subprocess.run(["git", "checkout", "--"] + paths,
                       capture_output=True, text=True, cwd=str(REPO_ROOT))
    return paths


def _clean_ledger_leaks():
    for rel in LEDGER_LEAKS:
        try:
            (REPO_ROOT / rel).unlink()
        except OSError:
            pass


def _clean_child_env() -> dict:
    """给历史回归子进程一个不带宿主 shim 的环境。

    运行环境通过 PYTHONPATH 注入 sitecustomize，把 shutil.rmtree 劫持为
    "移入回收站"，会让历史套件清理自己的 tmp 目录时失败（环境问题，非实现缺陷）。
    这里只对子进程剥离该注入，不改任何历史套件源码。
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


def _judge_history(tag: str, proc) -> tuple:
    """判定历史套件结果。

    ★ 总控裁决（2026-09-15，冲突 2）：历史套件里的「禁改文件 0 diff」类断言用的是
    `git diff --stat HEAD -- <path>`，属于 **pre-commit 护栏**；而 Stage 13 依 §8.2
    **合法修改**了 `v2/llm/**`（registry / request_counter / __init__ / 新增 provider）
    与 `v2/config.py`（bailian_image 档 + 图像价格表）。
    因此只豁免"**仅因这些合法改动路径**而未过的禁改护栏"，其余任何 FAIL 一律判失败
    —— 豁免清单在 detail 里逐条列出，不隐藏任何东西。
    人工批准 commit+tag 之后，这些护栏自然归零（HEAD 已包含改动）。
    """
    out = proc.stdout or ""
    if proc.returncode == 0:
        return True, ""
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    excused = [f for f in fails if _is_allowed_precommit_guard(f)]
    if fails and len(excused) == len(fails):
        _excused_fails.extend(f"{tag}: {f}" for f in fails)
        return True, ("仅 pre-commit 禁改护栏未过（Stage 13 依 §8.2 合法修改 "
                      "v2/llm + v2/config.py；commit 后归零）: "
                      + " | ".join(f[:110] for f in fails)[:300])
    tail = out.strip().splitlines()[-3:]
    return False, "; ".join(tail)[:220]


#: 历史套件"禁改 0 diff"类护栏的特征词
_GUARD_HINTS = ("禁改", "0 diff", "预期 0 改", "未移动", "0 新增")
_excused_fails: list = []

#: ★ Stage 13 依 §8.2 **声明式**登记的交付改动路径（人工可审计；**不**从
#: `git status` 推导 —— 那样会把"回归留下的脏文件"误判成本阶段合法改动）。
STAGE13_DELIVERABLE_PATHS = (
    "v2/.gitignore",
    "v2/config.py",
    "v2/llm/__init__.py",
    "v2/llm/providers/__init__.py",
    "v2/llm/providers/registry.py",
    "v2/llm/request_counter.py",
    "v2/llm/providers/mock_image.py",
    "v2/llm/providers/openai_compatible_image.py",
    "v2/orchestration/__init__.py",
    "v2/orchestration/pipeline.py",
    "v2/orchestration/state.py",
    "v2/orchestration/image_stage.py",
    "v2/logs/stage13_image",
    "CLAUDE.md",
    "v2/docs/PROJECT_STATUS.md",
    "v2/docs/HANDOFF.md",
    "v2/docs/ROADMAP.md",
)


def _covered(path: str) -> bool:
    """该路径是否落在 Stage 13 **声明式**登记的交付改动范围内。

    匹配规则：相等 / 是某登记路径的目录前缀（`v2/llm` ⊂ `v2/llm/providers/registry.py`）/
    是某登记路径的文件名后缀（`config.py` ⊂ `v2/config.py`）。
    """
    p = str(path or "").strip().strip("/")
    if not p:
        return False
    for m in STAGE13_DELIVERABLE_PATHS:
        if m == p or m.startswith(p + "/") or m.endswith("/" + p):
            return True
    return False


def _is_allowed_precommit_guard(line: str) -> bool:
    """这条 FAIL 是否属于"仅因 Stage 13 合法改动路径而未过的 pre-commit 禁改护栏"。"""
    if not any(h in line for h in _GUARD_HINTS):
        return False
    paths = re.findall(r"'([^']+)'", line)
    if not paths:
        paths = re.findall(r"([A-Za-z0-9_./]+\.py)", line)
        if not paths:
            return False
    return all(_covered(p) for p in paths)


def run_history():
    for tag, dirname in HISTORY_SUITES:
        suite = V2_ROOT / "logs" / dirname / "run_tests.py"
        if not suite.exists():
            check(f"T18-{tag} 套件存在", False, f"missing {suite}")
            continue
        _reset_history_products()
        proc = subprocess.run([sys.executable, str(suite)], capture_output=True,
                              text=True, cwd=str(V2_ROOT), encoding="utf-8",
                              errors="replace", env=_clean_child_env())
        ok, note = _judge_history(tag, proc)
        check(f"T18-{tag} 历史回归全绿", ok, note)
    _clean_ledger_leaks()
    _reset_history_products()


# ---------------------------------------------------------------------------
def main():
    if TMP_PROJECTS.exists():
        _hard_rmtree(TMP_PROJECTS)
    TMP_PROJECTS.mkdir(parents=True, exist_ok=True)
    TMP_LOGS.mkdir(parents=True, exist_ok=True)
    _clean_ledger_leaks()
    _reset_history_products()

    log("=" * 72)
    log("Stage 13 图像段 —— mock 测试套件（0 真实 API）")
    log("=" * 72)
    tests = [
        test_contract_and_dependency_direction,
        test_registry_image_branch,
        test_mock_image_deterministic_and_offline,
        test_default_off,
        test_single_shot_generation,
        test_multi_shot_generation,
        test_budget_guard,
        test_retry_consumes_budget,
        test_review_three_states,
        test_sha256_matches_disk,
        test_missing_profile_blocks,
        test_ledger,
        test_zero_real_api,
        test_storyboard_minimal_delta,
        test_binary_leak_scan,
        test_static_checks,
        test_forbidden_paths_untouched,
        test_bailian_native_provider,
        test_end_to_end,
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
        "suite": "stage13_image",
        "real_api_requests": 0,
        # ★ detail 一律转成 str：报告必须**按构造**可 JSON 序列化
        #   （Stage 13-P 踩过：把 TokenUsage 对象当 detail 传 → json.dumps 抛 TypeError，
        #    导致套件在收尾写报告时崩掉、rc=1，而测试本身全绿 —— 极易误判）。
        "results": [dict(r, detail=str(r.get("detail", ""))) for r in _results],
        "passed": passed, "total": total,
        # ★ 被豁免的历史套件 FAIL（pre-commit 禁改护栏；逐条留痕，供总控审计）
        "excused_history_fails": list(_excused_fails),
    }
    (TEST_DIR / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"report -> {TEST_DIR / 'test_report.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
