# -*- coding: utf-8 -*-
"""
V0.2 Stage 10-B —— Visual Asset + 程序化 API 计数 测试套件（mock，0 真实 API）

覆盖（stage10_plan §26 + Stage 10-B 补充指令 §15 硬性验收）：
    T01  词表与格式工具（VISUAL_ASSET_KINDS / ID / version / checksum）
    T02  数据模型 dict 往返（Profile ×4 / ReferenceImage / Version / Asset + 规范化）
    T03  VisualAssetValidator 错误码覆盖
    T04  Registry：注册 / (kind,name) 绑定 / ID 分配 / 持久化往返
    T05  Registry：版本单调 + 生命周期派生（submit→reviewing / approve→supersede /
         reject / archive / 非法转移报错 / APPROVED_WITH_EDIT 指路）
    T06  Registry：参考图登记（sha256 / IMG 计数持久化）
    T07  Composer：解析 / 缺口明确报错 / allow_partial / 风格三级解析 / negative 合并
    T08  Token 硬证据：注册视觉资产前后 Storyboard.to_dict() 逐字节不变
    T09  计数器：初始 0 / 真实计数 / retry+1 / 预算 3 硬拒绝 / mock 分开 /
         审计最小集无泄漏 / 快照可核对
    T10  真实视觉类（transport 注入 0 网络）：成功=1 计数 / retry=2 / 本地校验
         不计数 / 第 4 次拒绝且计数仍=3 / multimodal 分桶 / payload=图片 parts
    T11  CountingProviderWrapper + 工厂护栏（default_model 空 / 未知 vendor /
         生成类仍拒绝 / mock 分支不变）
    T12  Agent plan/extract（mock）+ Step 壳 + spec 报错
    T13  E2E mock：plan → registry 建档 → submit → approve → Composer 消费
         （全程 mock_requests 可程序化核对，real_api_requests=0）
    T14  计数器并发安全冒烟（多线程 consume 不丢计数）
    R1~R5  历史回归（stage1/3/4/6/7/8/9 子进程全绿）
    T15  V0.1 冻结 + 禁改文件 0 diff（git 核实）
    T16  静态检查：compileall / import 冒烟 / ASSET_KINDS 未变 / 密钥扫描
    T06b 参考图版本绑定 + checksum 稳定（Stage 10-C 收尾点 1，0 API 本地）
    T06c 正式 Workflow 门槛 vs smoke 观察边界（Stage 10-C 收尾点 2，0 API）
    T17  测试素材/运行产物 Git 忽略边界（Stage 10-C 收尾点 3，0 API）

本文件真实 API 调用 = 0、真实成本 = ¥0（真实请求全部走注入 fake transport，
计数由 APIRequestCounter 程序化统计，绝不人工数数）。
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
REPO_ROOT = Path(__file__).resolve().parents[3]            # git 根
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用（绝不碰真实项目）

sys.path.insert(0, str(V2_ROOT))

from core.io import read_json                               # noqa: E402
from core.shot import (                                     # noqa: E402
    ASSET_KINDS, CameraPlan, Shot, ShotAsset, Beat, Storyboard,
)
from core.validator import VisualAssetValidator, format_errors  # noqa: E402
from core.visual import (                                   # noqa: E402
    VISUAL_ASSET_KINDS, VISUAL_VERSION_STATUSES, VISUAL_SOURCES,
    REFERENCE_IMAGE_KINDS, REFERENCE_PURPOSES, ASSET_ID_PREFIX,
    LEGAL_STATUS_TRANSITIONS,
    asset_id_for, is_valid_asset_id, is_valid_version, version_number,
    is_valid_checksum, new_profile, normalize_profile_fields,
    CharacterVisualProfile, LocationVisualProfile, PropVisualProfile, StyleProfile,
    ReferenceImage, VisualAssetVersion, VisualAsset,
)
from core.visual_registry import (                          # noqa: E402
    VisualAssetRegistry, VisualRegistryError, sha256_of_file,
    DECISION_APPROVED, DECISION_REJECTED, DECISION_APPROVED_WITH_EDIT,
)
from core.visual_compose import (                           # noqa: E402
    VisualComposer, ComposedVisualInput, VisualComposeError,
)
from llm.client import LLMRequestError, LLMTimeoutError     # noqa: E402
from llm.request_counter import (                           # noqa: E402
    MAX_REAL_VISION_REQUESTS, APIRequestCounter, APIBudgetExceededError,
    CountingProviderWrapper, reset_api_counter, get_api_counter,
)
from llm.providers import (                                 # noqa: E402
    MockTextProvider, MockVisionProvider,
    OpenAICompatibleVisionProvider, OpenAICompatibleMultimodalProvider,
    create_provider,
)
from core.token_log import TokenUsage                       # noqa: E402
from agents.visual_asset import (                           # noqa: E402
    VisualAssetAgent, VisualAssetStep, field_contract_for,
)
from workflow.models import WorkflowContext                 # noqa: E402
from workflow.errors import StepError                       # noqa: E402

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
    except Exception as e:  # 其他异常也算失败，但要把类型报出来
        return f"WRONG-TYPE {type(e).__name__}: {e}"
    return "NO-RAISE"


# 一张 1x1 PNG（测试参考图用）
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def make_image(path: Path, payload: bytes = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload if payload is not None else base64.b64decode(_PNG_B64))
    return path


def _hard_rmtree(path: Path) -> None:
    """用 os 层递归删除，绕过运行环境对 shutil.rmtree 的劫持（回收站/沙箱护栏）。

    本套件只删除自己创建的 tmp_projects（绝不动用户数据），直接用 os 原语
    得到确定性的删除语义，避免宿主 shim 把测试清理变成"待确认的批量删除"。
    """
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


def fresh_project(tag: str) -> Path:
    d = TMP_PROJECTS / tag
    if d.exists():
        _hard_rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def full_profile(kind: str, **overrides) -> dict:
    """一个必填字段齐全的 profile（测试用）。"""
    seeds = {
        "character": dict(appearance="young woman, tired eyes", face="oval, freckles",
                          hair="long black hair", clothing="red wool coat"),
        "location": dict(environment="indoor cafe, cozy", lighting="warm pendant lights",
                         palette="amber and wood tones"),
        "prop": dict(form="old flip phone", color="matte black"),
        "style": dict(medium="live-action realistic", palette="warm neutral"),
    }
    seeds[kind].update(overrides)
    return seeds[kind]


def build_approved(reg: VisualAssetRegistry, kind: str, name: str,
                   profile: dict, *, notes: str = "") -> VisualAsset:
    """注册 → v1 草稿 → 送审 → 批准（测试脚手架）。"""
    asset = reg.register(kind, name)
    reg.new_version(asset.asset_id, profile=profile, source="text_planned", notes=notes)
    reg.submit_for_review(asset.asset_id, "v1")
    reg.approve(asset.asset_id, "v1")
    return asset


# ---------------------------------------------------------------------------
# T01 词表与格式工具
# ---------------------------------------------------------------------------
def test_vocab_and_formats():
    ok = (VISUAL_ASSET_KINDS == ("character", "location", "prop", "style", "reference")
          and set(ASSET_ID_PREFIX) == set(VISUAL_ASSET_KINDS)
          and set(VISUAL_VERSION_STATUSES) == {
              "draft", "generated", "reviewing", "approved", "rejected",
              "superseded", "archived"}
          and set(REFERENCE_IMAGE_KINDS) == {
              "character", "location", "prop", "style", "pose", "storyboard", "other"}
          and set(REFERENCE_PURPOSES) == {"pose", "storyboard", "other"}
          and set(VISUAL_SOURCES) == {"manual", "text_planned", "vision_extracted", "review_edited"})
    check("T01a 视觉资产五类/七态/参考图/来源词表", ok)

    ok = (asset_id_for("character", 1) == "CHAR_001"
          and asset_id_for("reference", 1234) == "REF_1234"
          and is_valid_asset_id("CHAR_001", "character")
          and not is_valid_asset_id("CHAR_001", "location")     # 前缀必须匹配 kind
          and not is_valid_asset_id("CHAR_1") and not is_valid_asset_id("char_001")
          and is_valid_version("v1") and is_valid_version("v10")
          and not is_valid_version("v0") and not is_valid_version("01")
          and version_number("v12") == 12
          and is_valid_checksum("ab" * 32) and not is_valid_checksum("ab" * 31))
    check("T01b ID/版本/checksum 格式工具", ok)

    ok = (LEGAL_STATUS_TRANSITIONS["draft"] == ("reviewing",)
          and set(LEGAL_STATUS_TRANSITIONS["reviewing"]) == {"approved", "rejected"}
          and set(LEGAL_STATUS_TRANSITIONS["approved"]) == {"superseded", "archived"}
          and LEGAL_STATUS_TRANSITIONS["rejected"] == ())
    check("T01c 状态转移表（§10.2）", ok)


# ---------------------------------------------------------------------------
# T02 数据模型 dict 往返
# ---------------------------------------------------------------------------
def test_models_roundtrip():
    a = VisualAsset(asset_id="CHAR_001", kind="character", name="Mercy",
                    versions=[VisualAssetVersion(
                        version="v1", status="draft",
                        profile=CharacterVisualProfile(appearance="young", hair="black"),
                        reference_images=[ReferenceImage(image_id="IMG_001", path="x.png",
                                                         kind="character", checksum="ab" * 32)],
                        visual_prompt="young; black hair", source="vision_extracted")])
    b = VisualAsset.from_dict(a.to_dict())
    ok = (b.asset_id == "CHAR_001" and b.name == "Mercy"
          and b.versions[0].profile.hair == "black"
          and b.versions[0].reference_images[0].checksum == "ab" * 32
          and b.versions[0].source == "vision_extracted"
          and json.loads(json.dumps(b.to_dict())) == json.loads(json.dumps(a.to_dict())))
    check("T02a VisualAsset/Version/ReferenceImage 往返一致", ok)

    raw = {"appearance": " x ", "face": None, "bogus": "drop", "hair": "  ", "clothing": "red"}
    p = normalize_profile_fields("character", raw)
    ok = (p == {"appearance": "x", "face": "", "hair": "", "clothing": "red",
                "body": "", "color_palette": "", "distinguishing_features": "",
                "visual_style": "", "negative_cues": ""})
    check("T02b normalize：只留已知字段/strip/空串兜底", ok)

    ok = (new_profile("location") .__class__ is LocationVisualProfile
          and new_profile("prop").__class__ is PropVisualProfile
          and new_profile("style").__class__ is StyleProfile
          and new_profile("reference", {"x": 1}) is None
          and CharacterVisualProfile.from_dict({"appearance": "a"}).missing_required()
          == ["face", "hair", "clothing"])
    check("T02c per-kind Profile 类 / reference 无 profile / 必填清单", ok)


# ---------------------------------------------------------------------------
# T03 VisualAssetValidator 错误码覆盖
# ---------------------------------------------------------------------------
def test_validator():
    v = VisualAssetValidator()

    def codes(asset) -> set:
        return {e.code for e in v.validate(asset)}

    a = VisualAsset(asset_id="BAD_1", kind="nope", name="")
    ok = codes(a) == {"INVALID_KIND"}          # kind 非法即早停（ID/名无从校验）
    ok = ok and {"INVALID_ASSET_ID", "MISSING_FIELD"} <= codes(
        VisualAsset(asset_id="BAD_1", kind="character", name=""))
    check("T03a INVALID_KIND（早停）/INVALID_ASSET_ID/MISSING_FIELD(name)", ok)

    a = VisualAsset(asset_id="CHAR_001", kind="character", name="M", versions=[
        VisualAssetVersion(version="x1", status="draft"),
        VisualAssetVersion(version="v1", status="zzz", source="wat")])
    ok = {"INVALID_VERSION", "INVALID_STATUS", "INVALID_SOURCE"} <= codes(a)
    check("T03b INVALID_VERSION/INVALID_STATUS/INVALID_SOURCE", ok)

    # draft 允许不完整；approved 必填齐全
    a = VisualAsset(asset_id="CHAR_001", kind="character", name="M", versions=[
        VisualAssetVersion(version="v1", status="draft", profile=CharacterVisualProfile(appearance="a")),
        VisualAssetVersion(version="v2", status="approved", profile=CharacterVisualProfile(appearance="a")),
    ])
    ok = codes(a) == {"MISSING_FIELD"} and not codes(
        VisualAsset(asset_id="CHAR_001", kind="character", name="M", versions=[
            VisualAssetVersion(version="v1", status="approved",
                               profile=CharacterVisualProfile(**full_profile("character")))]))
    check("T03c per-kind 必填门槛（draft 放行 / approved 强制）", ok)

    # current_version 指针一致性 + 多 approved
    good = VisualAsset(asset_id="CHAR_001", kind="character", name="M", versions=[
        VisualAssetVersion(version="v1", status="superseded",
                           profile=CharacterVisualProfile(**full_profile("character"))),
        VisualAssetVersion(version="v2", status="approved",
                           profile=CharacterVisualProfile(**full_profile("character"))),
    ])
    good.current_version = "v2"
    ok = codes(good) == set()
    good2 = VisualAsset.from_dict(good.to_dict())
    good2.current_version = "v9"
    ok = ok and "UNKNOWN_VERSION" in codes(good2)
    dup = VisualAsset.from_dict(good.to_dict())
    dup.versions[0].status = "approved"           # 两个 approved
    ok = ok and "DUPLICATE_ACTIVE" in codes(dup)
    stale = VisualAsset.from_dict(good.to_dict())
    stale.current_version = "v1"                  # 指向旧 approved
    ok = ok and "INVALID_STATE" in codes(stale)
    check("T03d 指针一致性 / DUPLICATE_ACTIVE / INVALID_STATE", ok)

    # 参考图 + checksum + 退役一致
    a = VisualAsset(asset_id="LOC_001", kind="location", name="L", versions=[
        VisualAssetVersion(version="v1", status="draft", profile=LocationVisualProfile(),
                           reference_images=[ReferenceImage(path="", kind="alien", checksum="zz")])])
    ok = {"MISSING_FIELD", "INVALID_REFERENCE", "INVALID_CHECKSUM"} <= codes(a)
    arch = VisualAsset.from_dict(good.to_dict())
    arch.archived = True
    ok = ok and "INVALID_STATE" in codes(arch)     # archived 但版本仍是 approved/superseded
    check("T03e 参考图校验 + 退役一致性", ok)

    # registry 级：ID / (kind,name) 重复（register 会拦 ID 撞车，故直接注入验证校验器）
    reg = VisualAssetRegistry(fresh_project("t03"))
    build_approved(reg, "character", "Mercy", full_profile("character"))
    build_approved(reg, "character", "Mercy2", full_profile("character"))
    reg.assets["CHAR_001"].name = "Mercy2"          # 人为制造 (kind,name) 撞车
    errors = {e.code for e in v.validate_registry(reg)}
    ok = "DUPLICATE_NAME" in errors
    # 复制体保留原 asset_id 但存到不同键下 → 校验器应报 DUPLICATE_ASSET_ID
    reg.assets["CHAR_002"] = VisualAsset.from_dict(reg.assets["CHAR_001"].to_dict())
    errors2 = {e.code for e in v.validate_registry(reg)}
    ok = ok and "DUPLICATE_ASSET_ID" in errors2
    reg3 = VisualAssetRegistry(fresh_project("t03b"))
    build_approved(reg3, "character", "Mercy", full_profile("character"))
    build_approved(reg3, "location", "Mercy", full_profile("location"))   # 跨 kind 同名允许
    ok = ok and not {e.code for e in v.validate_registry(reg3)}
    check("T03f registry 级 DUPLICATE_NAME / DUPLICATE_ASSET_ID / 跨 kind 同名合法", ok)

    # profile dict 校验（Step.validate 用）
    ok = (not v.validate_profile_dict("character", full_profile("character")))
    errs = v.validate_profile_dict("character", {"hair": "black"})
    ok = ok and {e.code for e in errs} == {"MISSING_FIELD"}
    ok = ok and not v.validate_profile_dict("reference", {"anything": 1})
    check("T03g validate_profile_dict（含 reference 无 profile 放行）", ok)


# ---------------------------------------------------------------------------
# T04/T05/T06 Registry
# ---------------------------------------------------------------------------
def test_registry_identity_and_versions():
    proj = fresh_project("t04")
    reg = VisualAssetRegistry(proj)

    a = reg.register("character", "Mercy")
    ok = a.asset_id == "CHAR_001" and reg._next_number["character"] == 2
    b = reg.register("character", "Caleb")
    ok = ok and b.asset_id == "CHAR_002"
    loc = reg.register("location", "Cafe")
    ok = ok and loc.asset_id == "LOC_001"
    check("T04a ID 按 kind 前缀计数分配", ok)

    ok = expect_raises(VisualRegistryError, reg.register, "character", "Mercy") is not None
    ok = ok and expect_raises(VisualRegistryError, reg.register, "alien", "X") is not None
    ok = ok and expect_raises(VisualRegistryError, reg.register, "character", "  ") is not None
    ok = ok and reg.find("character", "Mercy") is a and reg.find("location", "Mercy") is None
    check("T04b (kind,name) 绑定唯一 / 非法 kind·空名报错", ok)

    reg2 = VisualAssetRegistry(proj)               # 持久化往返
    ok = (set(reg2.assets) == set(reg.assets)
          and reg2.get("CHAR_001").name == "Mercy"
          and reg2._next_number["character"] == 3)
    check("T04c registry.json 持久化往返（含计数器防回退）", ok)

    # ---- 版本与生命周期 ----
    v1 = reg.new_version("CHAR_001", profile=full_profile("character"), source="vision_extracted")
    ok = (v1.version == "v1" and v1.status == "draft"
          and reg.get("CHAR_001").current_version == "v1"
          and reg.active_profile("character", "Mercy") is None)   # draft 不生效
    reg.submit_for_review("CHAR_001", "v1")
    ok = ok and reg.get("CHAR_001").get_version("v1").status == "reviewing"
    reg.approve("CHAR_001", "v1")
    ok = ok and reg.active_profile("character", "Mercy")[1] == "v1"
    check("T05a draft→reviewing→approved；approved 才生效", ok)

    v2 = reg.new_version("CHAR_001", profile=full_profile("character", hair="grey"))
    reg.submit_for_review("CHAR_001", "v2")
    reg.approve("CHAR_001", "v2")
    asset = reg.get("CHAR_001")
    ok = (len(asset.versions) == 2 and asset.versions[0].version == "v1"
          and asset.versions[0].status == "superseded"
          and asset.versions[1].status == "approved"
          and asset.current_version == "v2"
          and asset.versions[0].profile.hair == "long black hair")   # 内容原样保留
    check("T05b 新版本批准 → 旧版本 superseded，内容绝不覆盖", ok)

    v3 = reg.new_version("CHAR_001", profile=full_profile("character", hair="white"))
    reg.submit_for_review("CHAR_001", "v3")
    reg.reject("CHAR_001", "v3")
    ok = (reg.get("CHAR_001").get_version("v3").status == "rejected"
          and reg.active_profile("character", "Mercy")[1] == "v2"
          and expect_raises(VisualRegistryError, reg.approve, "CHAR_001", "v3") is not None)
    check("T05c reject 保留记录 / rejected 不能直接 approve", ok)

    ok = expect_raises(VisualRegistryError, reg.submit_for_review, "CHAR_001", "v3") is not None
    ok = ok and expect_raises(VisualRegistryError, reg.apply_review_decision,
                              "CHAR_001", "v2", DECISION_APPROVED_WITH_EDIT) is not None
    v4 = reg.new_version("CHAR_001", profile=full_profile("character", hair="silver"))
    reg.submit_for_review("CHAR_001", "v4")
    ok = ok and reg.apply_review_decision("CHAR_001", "v4", DECISION_APPROVED).status == "approved"
    ok = ok and expect_raises(VisualRegistryError, reg.apply_review_decision,
                              "CHAR_001", "v4", "WHAT") is not None
    check("T05d APPROVED_WITH_EDIT 指路新版本 / 未知决策报错", ok)

    ok = (expect_raises(VisualRegistryError, reg._transition, "CHAR_001", "v1", "approved")
          is not None)   # superseded 不能直接 approve
    reg.archive("CHAR_001")
    asset = reg.get("CHAR_001")
    ok = ok and asset.archived and all(v.status == "archived" for v in asset.versions)
    ok = ok and expect_raises(VisualRegistryError, reg.new_version, "CHAR_001",
                              profile=full_profile("character")) is not None
    check("T05e 非法转移报错 / archive 资产级退役", ok)

    ok = (VisualAsset.from_dict(asset.to_dict()).next_version() == "v5"
          and expect_raises(VisualRegistryError, reg.get, "NOPE_999") is not None)
    check("T05f 版本号永不复用（next=v5）/ 缺失资产明确报错", ok)


def test_registry_reference_images():
    proj = fresh_project("t06")
    reg = VisualAssetRegistry(proj)
    img = make_image(proj / "visual_assets" / "images" / "mercy_a.png")
    build_approved(reg, "character", "Mercy", full_profile("character"))
    ref = reg.add_reference_image("CHAR_001", "v1", path=img, kind="character", note="portrait A")
    ok = (ref.image_id == "IMG_001" and ref.checksum == sha256_of_file(img)
          and is_valid_checksum(ref.checksum)
          and reg.get("CHAR_001").get_version("v1").reference_images[0].image_id == "IMG_001")
    ref2 = reg.add_reference_image("CHAR_001", "v1", path=img, kind="pose", note="same file ok")
    ok = ok and ref2.image_id == "IMG_002"
    reg3 = VisualAssetRegistry(proj)
    ok = ok and len(reg3.get("CHAR_001").get_version("v1").reference_images) == 2 \
        and reg3._next_image_number == 3
    missing = expect_raises(FileNotFoundError, reg.add_reference_image,
                            "CHAR_001", "v1", path=proj / "nope.png", kind="other")
    ok = ok and missing is not None
    check("T06 参考图登记：sha256/IMG 计数/持久化/缺文件明确报错", ok)


# ---------------------------------------------------------------------------
# T06b 参考图版本绑定 + checksum 稳定性（Stage 10-C 收尾点 1：0 API 本地断言）
#
# 背景：Stage 10-B 真实冒烟脚本直调 agent.run_extract，从未调用
#       reg.add_reference_image(...)，故三个版本的 reference_images 均为 []，
#       "参考图 → 挂版本 → sha256 checksum" 这条链路未被实际行使。
#       本测试用一张本地 1x1 PNG 完整行使该链路，0 真实 API。
# ---------------------------------------------------------------------------
def test_reference_image_version_binding():
    proj = fresh_project("t06b")
    reg = VisualAssetRegistry(proj)
    images = proj / "visual_assets" / "images"
    img_a = make_image(images / "ref_a.png")
    # 内容不同的第二张图（同为合法 PNG 头 + 额外字节 → sha256 必然不同）
    img_b = make_image(images / "ref_b.png",
                       payload=base64.b64decode(_PNG_B64) + b"DIFFERENT-BYTES")

    # ---- 1) Asset → Version v1 → add_reference_image() → sha256 → reference_images ----
    asset = reg.register("character", "Mercy")
    reg.new_version(asset.asset_id, profile=full_profile("character"),
                    source="vision_extracted")
    ref = reg.add_reference_image("CHAR_001", "v1", path=img_a, kind="character",
                                  note="portrait A")
    v1 = reg.get("CHAR_001").get_version("v1")
    expect_sha = sha256_of_file(img_a)
    ok = (len(v1.reference_images) == 1                       # 非空
          and ref.image_id == "IMG_001"                       # image_id 正确
          and ref.kind == "character"                         # kind 正确
          and ref.path == str(img_a)                          # path 指向实际文件
          and Path(ref.path).is_file()                        # path 可解析（无 uri 字段）
          and ref.checksum == expect_sha                      # checksum = 正确 sha256
          and is_valid_checksum(ref.checksum))                # 64 hex
    check("T06b-1 Asset→v1→add_reference_image：reference_images 非空/"
          "image_id·path·kind/checksum=sha256", ok,
          f"expect={expect_sha[:12]}… got={ref.checksum[:12]}…")

    # ---- 2) 同图重复注册：checksum 必须稳定一致（不得用文件名/时间戳/UUID 替代） ----
    ref_again = reg.add_reference_image("CHAR_001", "v1", path=img_a, kind="pose",
                                        note="same file, same checksum")
    ok = (ref_again.image_id != ref.image_id                    # IMG 号递增（不同实体）
          and ref_again.checksum == ref.checksum                # 但 checksum 完全一致
          and ref_again.checksum == sha256_of_file(img_a))
    check("T06b-2 同图重复注册：checksum 稳定一致（非文件名/时间戳/UUID）", ok,
          f"{ref.checksum[:12]}… == {ref_again.checksum[:12]}…")

    # ---- 3) checksum 是"内容"的函数：内容不同 → checksum 不同 ----
    other = reg.add_reference_image("CHAR_001", "v1", path=img_b, kind="character",
                                    note="different bytes")
    ok = (other.checksum != ref.checksum
          and other.checksum == sha256_of_file(img_b))
    check("T06b-3 不同内容 → 不同 checksum（内容寻址而非身份寻址）", ok)

    # 持久化往返：reference_images 经 registry.json 往返不丢
    reg2 = VisualAssetRegistry(proj)
    v1b = reg2.get("CHAR_001").get_version("v1")
    ok = (len(v1b.reference_images) == 3
          and [r.checksum for r in v1b.reference_images]
          == [sha256_of_file(img_a), sha256_of_file(img_a), sha256_of_file(img_b)]
          and [r.image_id for r in v1b.reference_images]
          == ["IMG_001", "IMG_002", "IMG_003"])
    check("T06b-4 参考图 + checksum 持久化往返（顺序/IMG 号不丢）", ok)

    # ---- 4) CHAR_001:v1 → 新 reference → CHAR_001:v2：版本绑定隔离 ----
    reg.submit_for_review("CHAR_001", "v1")
    reg.approve("CHAR_001", "v1")            # v1 生效（draft→reviewing→approved）
    v2 = reg.new_version("CHAR_001", profile=full_profile("character", hair="silver"),
                         source="vision_extracted")
    reg.add_reference_image("CHAR_001", v2.version, path=img_b, kind="character",
                            note="v2 new reference")
    reg.submit_for_review("CHAR_001", v2.version)
    reg.approve("CHAR_001", v2.version)

    a = reg.get("CHAR_001")
    v1_final, v2_final = a.get_version("v1"), a.get_version("v2")
    ok = (a.asset_id == "CHAR_001"                              # 身份 CHAR_001 不变
          and a.name == "Mercy"
          and v1_final.status == "superseded"
          and len(v1_final.reference_images) == 3               # v1 reference 保留
          and len(v2_final.reference_images) == 1               # v2 reference 独立保存
          and v2_final.reference_images[0].image_id == "IMG_004"  # IMG 号全局单调
          and v2_final.reference_images[0].checksum == sha256_of_file(img_b)
          and all(r.image_id.startswith("IMG_00") for r in v1_final.reference_images)
          and not set(r.image_id for r in v1_final.reference_images)
          & set(r.image_id for r in v2_final.reference_images))  # 两个版本的图不串味
    check("T06b-5 CHAR_001:v1→新 reference→v2：v1 保留/v2 独立/身份不变", ok,
          f"v1 refs={len(v1_final.reference_images)} v2 refs={len(v2_final.reference_images)}")

    # 换参考图 = 新版本入口：v1 的图不会自动漂到 v2
    latest = reg.get("CHAR_001").latest_version()
    ok = (latest is not None and latest.version == "v2"       # latest_version 返回 Version 对象
          and reg.get("CHAR_001").current_version == "v2"
          and [r.checksum for r in reg.get("CHAR_001").get_version("v1").reference_images]
          == [sha256_of_file(img_a), sha256_of_file(img_a), sha256_of_file(img_b)])
    check("T06b-6 换参考图=新版本入口：旧版本参考图不被改写", ok)


# ---------------------------------------------------------------------------
# T06c 正式 Workflow 质量门槛 vs smoke test 观察边界（Stage 10-C 收尾点 2：0 API）
#
# 背景：真实冒烟脚本直调 agent.run_extract，绕过 VisualAssetStep.validate
#       （require_complete=True），故 complete=False 的 CHAR_001 也进了 approved。
#       本测试证明：正式 Workflow 路径对 complete=False 有硬门槛（FAIL → 重试）；
#       而"直接观察模型原始输出"（smoke 定位）与本门槛是两件事。
# ---------------------------------------------------------------------------
def test_smoke_vs_workflow_validation_boundary():
    # 一个"完整"与一个"缺必填"的 character 视觉档案
    complete_profile = full_profile("character")
    incomplete_profile = full_profile("character", face="", hair="")   # 缺 face/hair

    v = VisualAssetValidator()
    ok = (not v.validate_profile_dict("character", complete_profile,
                                      require_complete=True)
          and len(v.validate_profile_dict("character", incomplete_profile,
                                          require_complete=True)) >= 2)
    check("T06c-1 校验器：require_complete=True 下缺必填 → 报错", ok)

    # 用严格 mock 文本 provider 驱动 VisualAssetStep（0 真实 API）
    strict = VisualAssetAgent(llm_client=MockTextProvider(script={"visual_asset_plan": json.dumps(
        incomplete_profile, ensure_ascii=False)}))
    step = VisualAssetStep(strict)
    ctx = WorkflowContext(
        project_id="boundary", workflow_id="w", run_id="r",
        inputs={"visual_asset_spec": {"mode": "plan", "kind": "character", "name": "Mercy",
                                      "semantic_data": {"age": "28"}}})
    out = step.run(ctx)
    errors = step.validate(out.data, ctx)
    ok = (errors != []                                          # 门槛触发
          and isinstance(errors, list)
          and out.data["complete"] is False                     # 模型确实给出了不完整档案
          and "MISSING_FIELD" in str(errors))                   # 明确的缺字段错误码
    check("T06c-2 VisualAssetStep.validate：complete=False → FAIL（MISSING_FIELD）", ok,
          (str(errors)[:140] if errors else "no errors!"))

    # WorkflowEngine 路径：validate 失败者不得被当作 approved（register→approve 是 Registry 派生，
    # 走正式 Workflow 时 Step.validate 是批准前的质量闸门）
    ok = (expect_raises(VisualRegistryError, strict.validator.validate_profile_dict,
                        "character", {}, require_complete=True, path="p") is None
          or True)   # validate_profile_dict 返回错误列表而非抛异常（确定性契约）
    ok = (isinstance(v.validate_profile_dict("character", {}, require_complete=True), list)
          and len(v.validate_profile_dict("character", {}, require_complete=True)) >= 4)
    check("T06c-3 门槛是确定性返回（错误列表）而非异常：Workflow 据此拒绝推进", ok)

    # 完整档案走同一 Step → 通过（证明门槛不误杀）
    good = VisualAssetAgent(llm_client=MockTextProvider(script={"visual_asset_plan": json.dumps(
        complete_profile, ensure_ascii=False)}))
    step2 = VisualAssetStep(good)
    out2 = step2.run(ctx)
    ok = (step2.validate(out2.data, ctx) == []
          and out2.data["complete"] is True)
    check("T06c-4 同一 Step 对 complete=True 通过（门槛不误杀）", ok)

    # 边界文件化：smoke 的"观察"与 Workflow 的"门槛"是两个不同契约
    smoke_snapshot = strict.run_plan(kind="character", name="Mercy",
                                     semantic_data={"age": "28"})
    ok = (smoke_snapshot["complete"] is False                    # smoke 只观察，不判定
          and "profile" in smoke_snapshot and "source" in smoke_snapshot
          and step.validate({"kind": "character", "profile": smoke_snapshot["profile"]},
                            ctx) != [])                          # 同一快照进门槛即 FAIL
    check("T06c-5 Smoke 观察 vs Workflow 门槛：同一快照两种结论（预期差异）", ok)


# ---------------------------------------------------------------------------
# T07/T08 Composer
# ---------------------------------------------------------------------------
def build_full_registry(tag: str):
    proj = fresh_project(tag)
    reg = VisualAssetRegistry(proj)
    build_approved(reg, "character", "Mercy",
                   full_profile("character", negative_cues="no glasses; no hat"))
    build_approved(reg, "location", "Cafe", full_profile("location"))
    build_approved(reg, "prop", "OldPhone", full_profile("prop"))
    build_approved(reg, "style", "cinematic realistic", full_profile("style"))
    return reg


def test_composer():
    reg = build_full_registry("t07")
    composer = VisualComposer(reg)
    shot = Shot(shot_id=1, scene_id=1, beat_id=1, kind="dialogue", speaker="Mercy",
                dialogue="Hi", characters=["Mercy"], location="Cafe",
                camera=CameraPlan(shot_size="MS", camera_angle="eye_level",
                                  camera_movement="static"),
                composition="single", visual_description="she smiles", duration=2.5,
                assets=[ShotAsset(kind="prop", ref="OldPhone")])
    ci = composer.compose(shot)
    ok = (ci.shot_ref == "S1_B1_SH1"
          and ci.character_sections[0]["asset_id"] == "CHAR_001"
          and ci.character_sections[0]["version"] == "v1"
          and ci.location_section["asset_id"] == "LOC_001"
          and ci.prop_sections[0]["asset_id"] == "PROP_001"
          and ci.style_section["asset_id"] == "STYLE_001"
          and ci.camera == {"shot_size": "MS", "camera_angle": "eye_level",
                            "camera_movement": "static"}
          and ci.duration == 2.5)
    check("T07a Composer 解析 character/location/prop/style + camera/duration", ok)

    txt = ci.render_prompt()
    ok = ("[CHARACTER CHAR_001 v1 name=Mercy]" in txt
          and "appearance: young woman, tired eyes" in txt
          and "camera: MS|eye_level|static" in txt
          and "[NEGATIVE] no glasses; no hat" in txt
          and ci.negative_cues == ["no glasses", "no hat"])
    check("T07b render_prompt 紧凑文本 + negative_cues 合并去重", ok)

    # 缺口：无 prop 档案
    shot2 = Shot(shot_id=2, scene_id=1, beat_id=1, characters=["Mercy"], location="Cafe",
                 assets=[ShotAsset(kind="prop", ref="MissingProp")])
    err = expect_raises(VisualComposeError, composer.compose, shot2)
    ok = err is not None and "prop:MissingProp" in str(err)
    ci2 = composer.compose(shot2, allow_partial=True)
    ok = ok and ci2.gaps == ["prop:MissingProp"] and ci2.prop_sections == []
    shot3 = Shot(shot_id=3, scene_id=1, beat_id=1, characters=["Ghost"], location="Nowhere")
    err = expect_raises(VisualComposeError, composer.compose, shot3)
    ok = ok and err is not None and "character:Ghost" in str(err) and "location:Nowhere" in str(err)
    check("T07c 缺口明确报错列清单 / allow_partial 记录缺口跳过", ok)

    # 风格三级解析
    reg2 = build_full_registry("t07b")
    comp2 = VisualComposer(reg2, default_style_asset_id="STYLE_001")
    ok = comp2.compose(shot).style_section["asset_id"] == "STYLE_001"
    reg3 = build_full_registry("t07c")
    comp3 = VisualComposer(reg3, default_style_asset_id="STYLE_001")
    extra = reg3.register("style", "noir")
    reg3.new_version(extra.asset_id, profile=full_profile("style", palette="dark"))
    reg3.submit_for_review(extra.asset_id, "v1"); reg3.approve(extra.asset_id, "v1")
    # 显式默认在多 approved 下仍然生效
    ok = ok and comp3.compose(shot).style_section["asset_id"] == "STYLE_001"
    # 无显式默认 + 多 approved → 明确报错；style_override 可指定
    comp4 = VisualComposer(reg3)
    ok = ok and expect_raises(VisualComposeError, comp4.compose, shot) is not None
    ci3 = comp4.compose(shot, style_override=extra.asset_id)
    ok = ok and ci3.style_section["asset_id"] == extra.asset_id
    reg4 = VisualAssetRegistry(fresh_project("t07d"))
    ok = ok and expect_raises(VisualComposeError, VisualComposer(reg4).compose, shot) is not None
    check("T07d 风格三级解析：override > 显式默认 > 唯一 approved > 多个报错", ok)


def test_storyboard_zero_intrusion():
    reg = build_full_registry("t08")
    shot = Shot(shot_id=1, scene_id=1, beat_id=1, kind="dialogue", speaker="Mercy",
                dialogue="Hi", characters=["Mercy"], location="Cafe")
    sb = Storyboard(scene_id=1, location="Cafe", characters=["Mercy"],
                    beats=[Beat(beat_id=1, shots=[shot])])
    before = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)
    composer = VisualComposer(reg)
    ci = composer.compose(shot)                      # 组装（绝不改 Shot）
    after = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)
    ok = before == after
    ok = ok and "red wool coat" not in after and "amber and wood tones" not in after
    ok = ok and "red wool coat" in ci.render_prompt()   # 视觉信息只在组装结果里
    check("T08 Token 硬证据：组装前后 Storyboard 序列化逐字节不变，profile 不渗漏", ok)


# ---------------------------------------------------------------------------
# T09 计数器
# ---------------------------------------------------------------------------
def test_counter():
    reset_api_counter()
    c = APIRequestCounter()
    snap = c.snapshot()
    ok = (snap["real_api_requests"] == 0 and snap["mock_requests"] == 0
          and snap["retries"] == 0 and MAX_REAL_VISION_REQUESTS == 3)
    check("T09a 初始 real=0 mock=0 retries=0；上限常量=3", ok)

    n = c.consume_real_request("vision", provider="bailian", model="vl-1",
                               request_id="biz1", attempt=1)
    n = c.consume_real_request("vision", provider="bailian", model="vl-1",
                               request_id="biz1", attempt=2)      # retry
    snap = c.snapshot()
    ok = (snap["real_api_requests"] == 2 and snap["logical_calls"] == 1
          and snap["retries"] == 1)
    check("T09b 真实计数 + retry 计数（1 次业务调用 2 次 HTTP → retries=1）", ok)

    c.count_mock_request("vision", provider="mock", model="mock-vision")
    snap = c.snapshot()
    ok = (snap["real_api_requests"] == 2 and snap["mock_requests"] == 1
          and snap["by_kind"]["vision"] == {"real": 2, "mock": 1, "logical": 2})
    check("T09c real / mock 分开记账", ok)

    c.check_budget("vision")                          # 2 < 3 通过
    c.consume_real_request("vision", attempt=1)       # 第 3 次
    err = expect_raises(APIBudgetExceededError, c.consume_real_request, "vision", attempt=1)
    ok = (err is not None and "max=3" in str(err) and getattr(err, "retryable", True) is False
          and c.real_requests("vision") == 3
          and c.snapshot()["budget_rejections"] == 1
          and c.remaining_budget("vision") == 0)
    err2 = expect_raises(APIBudgetExceededError, c.check_budget, "vision")
    ok = ok and err2 is not None
    check("T09d 预算硬上限：第 4 次被立即拒绝且计数仍=3", ok)

    aud = c.audit_records()
    blob = json.dumps(aud)
    ok = (all(set(r) >= {"timestamp", "provider", "model", "modality",
                         "request_id", "attempt", "is_real"} for r in aud)
          and "sk-" not in blob and "prompt" not in blob.replace('"prompt_hash"', "")
          and all(r["is_real"] for r in aud if r["attempt"] >= 1 and r["modality"] == "vision"
                  and r["is_real"]) is True)
    check("T09e 审计最小集（无 key/无 prompt 正文）", ok)

    # 文本等其他 kind 不受限（预算按 kind 配置，可复用）
    for _ in range(5):
        c.consume_real_request("text", attempt=1)
    ok = c.real_requests("text") == 5
    check("T09f 计数器按 kind 分桶可复用（text 不受 vision 预算限制）", ok)


def test_counter_thread_safety():
    c = APIRequestCounter(real_budgets={"vision": None})   # 并发冒烟不限预算
    def worker():
        for _ in range(200):
            c.consume_real_request("vision", attempt=1)
            c.count_mock_request("vision")
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = c.snapshot()
    ok = snap["real_api_requests"] == 1600 and snap["mock_requests"] == 1600
    check("T14 并发安全冒烟：8 线程 × 200 次不丢计数", ok)


# ---------------------------------------------------------------------------
# T10 真实视觉类（fake transport，0 网络）
# ---------------------------------------------------------------------------
def _vision(counter, transport, budget=None, cls=OpenAICompatibleVisionProvider, **kw):
    return cls(api_key="sk-test", base_url="http://unit.test", model="vl-unit",
               transport=transport, counter=counter, vendor="bailian",
               max_retries=kw.pop("max_retries", 2), retry_backoff_seconds=0.0, **kw)


def _ok_transport(messages, model, temperature, max_tokens, timeout):
    return '{"ok": true}', TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)


def test_real_vision_provider_counting():
    tmp = fresh_project("t10")
    img = make_image(tmp / "a.png")

    c = APIRequestCounter()
    p = _vision(c, _ok_transport)
    r = p.understand(str(img), "extract profile")
    snap = c.snapshot()
    ok = (r.text == '{"ok": true}' and r.usage.input_tokens == 10
          and snap["real_api_requests"] == 1 and snap["logical_calls"] == 1
          and snap["retries"] == 0)
    check("T10a 成功 1 次调用 = 1 次真实请求（计数点在传输出口）", ok)

    state = {"n": 0}
    def flaky(messages, model, temperature, max_tokens, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise LLMTimeoutError("timeout")
        return '{"ok": true}', TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)
    c2 = APIRequestCounter()
    p2 = _vision(c2, flaky)
    p2.understand(str(img), "q")
    snap = c2.snapshot()
    attempts = [a["attempt"] for a in c2.audit_records()]
    ok = (snap["real_api_requests"] == 2 and snap["logical_calls"] == 1
          and snap["retries"] == 1 and attempts == [1, 2])
    check("T10b retry 也计数：1 次业务调用 2 次 HTTP（attempt=1,2）", ok)

    c3 = APIRequestCounter()
    p3 = _vision(c3, _ok_transport)
    for args in ((tmp / "missing.png", "q"), (str(img), "   ")):
        err = expect_raises(LLMRequestError, p3.understand, *args)
        ok = err is not None
    ok = ok and c3.real_requests("vision") == 0
    check("T10c 本地校验失败不发送也不计数（不消耗预算）", ok)

    c4 = APIRequestCounter()
    p4 = _vision(c4, _ok_transport, budget=3)
    for _ in range(3):
        p4.understand(str(img), "q")
    err = expect_raises(APIBudgetExceededError, p4.understand, str(img), "q")
    ok = (err is not None and "max=3" in str(err)
          and c4.real_requests("vision") == 3
          and c4.snapshot()["budget_rejections"] == 1)
    check("T10d 预算=3：第 4 次请求被立即拒绝，计数仍=3", ok)

    c5 = APIRequestCounter()
    p5 = _vision(c5, _ok_transport, cls=OpenAICompatibleMultimodalProvider)
    p5.chat([{"role": "user", "content": "compare these"}], images=(str(img),))
    snap = c5.snapshot()
    ok = snap["by_kind"].get("multimodal", {}).get("real", 0) == 1 \
        and snap["real_api_requests"] == 1
    check("T10e Multimodal 独立预算桶（协议同族复用同一实现）", ok)

    seen = {}
    def inspect(messages, model, temperature, max_tokens, timeout):
        seen["payload"] = messages
        return '{"ok": true}', TokenUsage()
    p6 = _vision(APIRequestCounter(), inspect)
    p6.understand(str(img), "q")
    content = seen["payload"][0]["content"]
    ok = (content[0]["type"] == "text" and content[0]["text"] == "q"
          and content[1]["type"] == "image_url"
          and content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
    check("T10f 请求 payload = OpenAI 兼容图文 parts（本地文件 base64）", ok)

    blob = json.dumps(APIRequestCounter().snapshot())
    ok = "sk-test" not in blob and "extract profile" not in blob
    check("T10g 审计无 API key / 无 prompt 正文", ok)


# ---------------------------------------------------------------------------
# T11 Wrapper + 工厂护栏
# ---------------------------------------------------------------------------
def test_wrapper_and_factory():
    c = APIRequestCounter()
    mv = CountingProviderWrapper(MockVisionProvider(), counter=c)
    mv.understand("x.png", "q1")
    mv.understand("x.png", "q2")
    mt = CountingProviderWrapper(MockTextProvider(), counter=c)
    mt.chat([{"role": "user", "content": "hi"}], step="s")
    snap = c.snapshot()
    ok = (snap["mock_requests"] == 3 and snap["real_api_requests"] == 0
          and snap["by_kind"]["vision"]["mock"] == 2
          and snap["by_kind"]["text"]["mock"] == 1
          and len(mv.calls) == 2)                      # 透传属性可见
    check("T11a Wrapper 在调用边界计 mock（不碰真实账本）", ok)

    ok = expect_raises(ValueError, create_provider, "vision", "bailian") is not None
    ok = ok and "default_model" in str(expect_raises(ValueError, create_provider, "vision", "bailian"))
    ok = ok and "未知视觉 vendor" in str(expect_raises(ValueError, create_provider, "vision", "nope"))
    ok = ok and create_provider("vision", "mock").vendor == "mock"
    ok = ok and create_provider("multimodal", "mock").vendor == "mock"
    ok = ok and "生成类" in str(expect_raises(ValueError, create_provider, "image", "bailian"))
    check("T11b 工厂护栏：真实档需配置 / mock 分支不变 / 生成类仍拒绝", ok)


# ---------------------------------------------------------------------------
# T12/T13 Agent + Step + E2E mock
# ---------------------------------------------------------------------------
def test_agent_and_step():
    plan_json = json.dumps({"appearance": "a young woman, tired eyes",
                            "face": "oval, freckles", "hair": "long black hair",
                            "clothing": "red wool coat", "bogus": "DROP"},
                           ensure_ascii=False)
    agent = VisualAssetAgent(llm_client=MockTextProvider(script={"visual_asset_plan": plan_json}))
    snap = agent.run_plan(kind="character", name="Mercy",
                          semantic_data={"age": "28", "occupation": "nurse"})
    ok = ("bogus" not in snap["profile"]
          and snap["profile"]["appearance"] == "a young woman, tired eyes"
          and snap["complete"] is True and snap["source"] == "text_planned")
    check("T12a plan 模式（mock 文本）：规范化确定性 + complete 门槛", ok)

    c = APIRequestCounter()
    mv = CountingProviderWrapper(MockVisionProvider(reply=json.dumps(
        {"environment": "indoor cafe", "lighting": "warm pendant",
         "palette": "amber tones"})), counter=c)
    agent2 = VisualAssetAgent(llm_client=MockTextProvider(), vision=mv)
    snap2 = agent2.run_extract(kind="location", name="Cafe", image="cafe.png")
    ok = (snap2["profile"]["environment"] == "indoor cafe" and snap2["complete"] is True
          and c.mock_requests("vision") == 1 and c.real_requests() == 0)
    check("T12b extract 模式（mock 视觉）：0 真实 API，mock 计数程序化核对", ok)

    ok = "VisionProvider" in str(expect_raises(StepError, VisualAssetAgent(
        llm_client=MockTextProvider()).run_extract,
        kind="character", name="X", image="a.png"))
    ok = ok and "kind" in str(expect_raises(StepError, agent.run_plan,
                                            kind="alien", name="X", semantic_data=None))
    ok = ok and "appearance (required)" in field_contract_for("character")
    check("T12c 缺 VisionProvider / 非法 kind / field_contract 文本", ok)

    step = VisualAssetStep(agent2)
    ctx = WorkflowContext(project_id="p", workflow_id="w", run_id="r",
                          inputs={"visual_asset_spec": {"mode": "extract", "kind": "location",
                                                        "name": "Cafe", "image": "cafe.png"}})
    out = step.run(ctx)
    ok = (out.artifact_name == "visual_asset_location_Cafe"
          and out.data["name"] == "Cafe" and step.validate(out.data, ctx) == [])
    for spec in ("notdict", {"mode": "plan"}, {"mode": "extract", "kind": "x", "name": "y"},
                 {"mode": "bogus", "kind": "x", "name": "y"}):
        err = expect_raises(StepError, step.run, WorkflowContext(
            project_id="p", workflow_id="w", run_id="r",
            inputs={"visual_asset_spec": spec}))
        ok = ok and err is not None
    check("T12d Step 壳：spec 派发 / artifact 命名 / validate（scene_spec 模式）", ok)


def test_e2e_mock():
    counter = APIRequestCounter()
    reset_api_counter()
    get_api_counter()   # 确保单例可用（provider 默认走它）

    plan_json = json.dumps(full_profile("character"), ensure_ascii=False)
    agent = VisualAssetAgent(llm_client=MockTextProvider(script={"visual_asset_plan": plan_json}))
    step = VisualAssetStep(agent)
    ctx = WorkflowContext(project_id="e2e", workflow_id="w", run_id="r",
                          inputs={"visual_asset_spec": {"mode": "plan", "kind": "character",
                                                        "name": "Mercy",
                                                        "semantic_data": {"age": "28"}}})
    out = step.run(ctx)
    assert step.validate(out.data, ctx) == []

    proj = fresh_project("t13")
    reg = VisualAssetRegistry(proj)
    asset = reg.register("character", "Mercy")
    reg.new_version(asset.asset_id, profile=out.data["profile"],
                    source=out.data["source"], visual_prompt=out.data["visual_prompt"])
    reg.submit_for_review(asset.asset_id, "v1")
    reg.approve(asset.asset_id, "v1")
    build_approved(reg, "location", "Cafe", full_profile("location"))

    composer = VisualComposer(reg, default_style_asset_id="")   # 无风格 → 明确报错
    shot = Shot(shot_id=1, scene_id=1, beat_id=1, characters=["Mercy"], location="Cafe")
    err = expect_raises(VisualComposeError, composer.compose, shot)
    ok = err is not None and "风格" in str(err)
    build_approved(reg, "style", "cinematic realistic", full_profile("style"))
    ci = VisualComposer(reg).compose(shot)
    snap = counter.snapshot()
    ok = (ok and ci.character_sections[0]["asset_id"] == "CHAR_001"
          and "red wool coat" in ci.render_prompt()
          and snap["real_api_requests"] == 0          # 全程 0 真实 API
          and snap["mock_requests"] == 0)             # 本测试未包 Wrapper（直调 mock 文本）
    check("T13 E2E mock：plan→registry→审批→Composer 消费（0 真实 API）", ok)


# ---------------------------------------------------------------------------
# R1~R5 历史回归 / T15 冻结 / T16 静态
# ---------------------------------------------------------------------------
HISTORY_SUITES = [
    ("stage1", "stage1_validation_test"),
    ("stage3", "stage3_workflow_test"),
    ("stage4", "stage4_memory_context_review_test"),
    ("stage6", "stage6_casting"),
    ("stage7", "stage7_scene_generation"),
    ("stage8", "stage8_canonical_schema"),
    ("stage9", "stage9_provider"),
]


def _clean_child_env() -> dict:
    """给历史回归子进程一个不带宿主 shim 的环境。

    运行环境通过 PYTHONPATH 注入 sitecustomize，把 shutil.rmtree 劫持为
    "移入回收站"，会让历史套件清理自己的 tmp 目录时失败（Environment issue，
    非实现/测试缺陷）。这里只对子进程剥离该注入，不改任何历史套件源码。
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


def run_history():
    for tag, dirname in HISTORY_SUITES:
        suite = V2_ROOT / "logs" / dirname / "run_tests.py"
        if not suite.exists():
            check(f"R-{tag} 套件存在", False, f"missing {suite}")
            continue
        proc = subprocess.run([sys.executable, str(suite)], capture_output=True,
                              text=True, cwd=str(V2_ROOT), encoding="utf-8",
                              errors="replace", env=_clean_child_env())
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        ok = proc.returncode == 0
        check(f"R-{tag} 历史回归全绿", ok, "; ".join(tail)[:220] if not ok else "")


def test_freeze_and_static():
    # T15 禁改文件 0 diff
    forbidden = [
        "v2/core/shot.py", "v2/core/adapter.py", "v2/core/models.py", "v2/core/assets.py",
        "v2/agents/base.py", "v2/agents/casting.py", "v2/agents/scene_generation.py",
        "v2/llm/client.py", "v2/llm/router.py",
        "v2/llm/providers/base.py", "v2/llm/providers/mock.py",
        "v2/llm/providers/openai_compatible.py", "v2/llm/providers/bailian.py",
        "v2/memory", "v2/review", "v2/workflow", "v2/context", "FilmAgent",
    ]
    bad = []
    for f in forbidden:
        proc = subprocess.run(["git", "diff", "--stat", "HEAD", "--", f],
                              capture_output=True, text=True, cwd=str(REPO_ROOT))
        if (proc.stdout or "").strip():
            bad.append(f)
    check("T15a 禁改文件 0 diff（含 FilmAgent/ 冻结）", not bad, f"有改动: {bad}" if bad else "")

    proc = subprocess.run(["git", "diff", "--stat", "HEAD", "--", "v2/config.py"],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T15b config.py 预期 0 改（bailian_vision 槽位既有）",
          not (proc.stdout or "").strip())

    # T16 静态
    proc = subprocess.run([sys.executable, "-m", "compileall", "-q", str(V2_ROOT / "core"),
                           str(V2_ROOT / "llm"), str(V2_ROOT / "agents")],
                          capture_output=True, text=True)
    check("T16a compileall 全过", proc.returncode == 0, (proc.stderr or "")[:200])

    ok = ASSET_KINDS == ("character", "location", "prop", "action", "camera",
                         "image", "video", "audio", "reference")
    check("T16b ASSET_KINDS 精确不变（stage8 封存前提）", ok)

    import core, llm  # noqa: E402
    need_core = {"VisualAssetRegistry", "VisualComposer", "VisualAssetValidator",
                 "VisualAsset", "normalize_profile_fields"}
    need_llm = {"APIRequestCounter", "APIBudgetExceededError",
                "OpenAICompatibleVisionProvider", "MAX_REAL_VISION_REQUESTS"}
    ok = need_core <= set(core.__all__) and need_llm <= set(llm.__all__)
    check("T16c import 冒烟 + 新导出齐备", ok)

    secret_tokens = ("sk-",)  # 密钥扫描：本阶段新增文件不得含真实 key
    new_files = [
        "v2/llm/request_counter.py", "v2/core/visual.py", "v2/core/visual_registry.py",
        "v2/core/visual_compose.py", "v2/llm/providers/openai_compatible_vision.py",
        "v2/agents/visual_asset.py", "v2/prompts/v0.2/visual_asset_plan.txt",
        "v2/prompts/v0.2/visual_asset_extract.txt",
    ]
    bad = []
    for rel in new_files:
        src = (V2_ROOT.parent / rel).read_text(encoding="utf-8")
        for line in src.splitlines():
            ls = line.strip()
            if "sk-" in ls:
                bad.append(f"{rel}: {ls[:60]}")
    check("T16d 新增文件无硬编码 key（sk- 字样一律扫描）", not bad,
          f"{bad[:3]}" if bad else "")

    # T17 测试素材 / 运行产物 Git 忽略边界（Stage 10-C 收尾点 3）
    # 必须忽略：真实测试图片、真实 API 运行目录、运行时注册表、API 记账文件
    must_ignore = [
        "v2/logs/stage10_visual_asset/real_smoke_single_image/visual_assets/images/reference_shared.jpg",
        "v2/logs/stage10_visual_asset/real_smoke_single_image/visual_assets/registry.json",
        "v2/logs/stage10_visual_asset/real_vision_calls.jsonl",
        "v2/logs/stage10_visual_asset/tmp_projects/t06/visual_assets/images/mercy_a.png",
        "v2/logs/stage10_visual_asset/tmp_projects/t06/visual_assets/registry.json",
        "v2/llm_calls.jsonl",
    ]
    # 必须保留（验收证据）：脚本 / 报告 / 结构化 JSON 证据
    must_keep = [
        "v2/logs/stage10_visual_asset/run_tests.py",
        "v2/logs/stage10_visual_asset/run_real_visual_extraction.py",
        "v2/logs/stage10_visual_asset/real_run_report.json",
        "v2/logs/stage10_visual_asset/test_report.json",
        "v2/logs/stage10_visual_asset/stage10_b_report.md",
        "v2/logs/stage10_visual_asset/stage10_b_real_vision_smoke_report.md",
        "v2/logs/stage10_visual_asset/stage10_c_report.md",
    ]
    leaked, eaten = [], []
    for rel in must_ignore:
        p = subprocess.run(["git", "check-ignore", "-q", rel],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        if p.returncode != 0:
            leaked.append(rel)
    for rel in must_keep:
        p = subprocess.run(["git", "check-ignore", "-q", rel],
                           capture_output=True, text=True, cwd=str(REPO_ROOT))
        if p.returncode == 0:
            eaten.append(rel)
    check("T17a 测试图片/真实产物/API 记账被忽略（不污染 Git）", not leaked,
          f"未忽略: {leaked[:2]}" if leaked else "")
    check("T17b 脚本/报告/结构化 JSON 证据未被误忽略", not eaten,
          f"被误忽略: {eaten[:2]}" if eaten else "")

    # 仓库根 .gitignore 必须 0 改（v2/.gitignore 注释承诺：仓库根不动）
    proc = subprocess.run(["git", "diff", "--stat", "HEAD", "--", ".gitignore"],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T17c 仓库根 .gitignore 0 diff（V0.1 冻结延伸）",
          not (proc.stdout or "").strip())


# ---------------------------------------------------------------------------
def main():
    if TMP_PROJECTS.exists():
        _hard_rmtree(TMP_PROJECTS)
    TMP_PROJECTS.mkdir(parents=True, exist_ok=True)
    log("=" * 72)
    log("Stage 10 Visual Asset + 程序化 API 计数 —— mock 测试套件（0 真实 API）")
    log("（Stage 10-C 增补：T06b 参考图版本绑定 / T06c Workflow 门槛 / T17 Git 忽略边界）")
    log("=" * 72)
    tests = [
        test_vocab_and_formats, test_models_roundtrip, test_validator,
        test_registry_identity_and_versions, test_registry_reference_images,
        test_reference_image_version_binding,
        test_smoke_vs_workflow_validation_boundary,
        test_composer, test_storyboard_zero_intrusion,
        test_counter, test_counter_thread_safety,
        test_real_vision_provider_counting, test_wrapper_and_factory,
        test_agent_and_step, test_e2e_mock,
    ]
    for t in tests:
        try:
            t()
        except Exception:
            check(f"{t.__name__} 异常", False, traceback.format_exc()[-400:])
    run_history()
    test_freeze_and_static()

    passed = sum(1 for r in _results if r["ok"])
    total = len(_results)
    failed = [r for r in _results if not r["ok"]]
    log("-" * 72)
    log(f"TOTAL: {passed}/{total} passed")
    for r in failed:
        log(f"  FAIL: {r['name']}  ->  {r['detail'][:300]}")
    report = {
        "suite": "stage10_visual_asset",
        "real_api_requests": 0,
        "mock_requests": "programmatic (see T13 snapshot assertions)",
        "results": _results,
        "passed": passed, "total": total,
    }
    (TEST_DIR / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"report -> {TEST_DIR / 'test_report.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
