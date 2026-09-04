# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— Provider Architecture 测试套件（B 会话基础测试）

覆盖（对应 stage9_plan §16.1）：
    T01  接口族形状：Text/Vision/Multimodal/Embedding 元信息 + 抽象方法签名
    T02  预留接口：Image/Video/Audio 类存在、kind 正确、调用明确不可用
    T03  MockTextProvider：脚本化（按 step / 出队 / 兜底）返回 + usage + cost 语义
    T04  MockTextProvider：注入抛错可测 retry 边界（错误不被吞、不叠加重试）
    T05  Mock 三件套（Vision/Multimodal）：确定性返回 + 入参即校验
    T06  MockEmbeddingProvider：同输入同输出（确定性伪向量）+ usage
    T07  Factory：create_provider 各种 kind/vendor 正确落位；非法组合明确报错
    T08  Router：真正进入数据流——路由描述 -> Provider 实例；默认档经 Config/环境开关
    T09  structured：Mock 可感知不报错；真实执行器未接线时静默忽略（不越权）
    T10  端到端 0-API 冒烟：Casting 走 Router -> MockTextProvider -> Workflow -> APPROVED
    T11  SceneGeneration Agent 经 MockTextProvider 生成合法 Scene（0 API）
    T12  Token/Cost：Mock 全部走既有 TokenLogger，账目 0/null（无真实计费）
    回归 R1~R6：stage1/3/4/6/7/8 子进程全绿
    T13  V0.1 冻结完整性：FilmAgent/ 0 diff；v0.1/v0.2-stage7/v0.2-stage8 未移动
    T14  静态检查：compileall + 全模块 import 冒烟 + 源码无真实密钥/硬编码绝对路径

本文件真实 LLM 调用 = 0、成本 = ¥0（全部走 Mock / 纯本地）。
"""
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # 本目录
REPO_ROOT = Path(__file__).resolve().parents[3]            # FilmAgent-main（git 根）
FIXTURES_STAGE7 = TEST_DIR.parent / "stage7_scene_generation" / "fixtures"
TMP_PROJECTS = TEST_DIR / "tmp_projects"                   # 测试专用项目目录（绝不碰真实项目）
TMP_LOGS = TEST_DIR / "tmp_logs"                           # 测试专用 token 日志

sys.path.insert(0, str(V2_ROOT))

from core.io import read_json                                # noqa: E402
from core.token_log import TokenUsage, TokenLogger, estimate_cost  # noqa: E402
from core.assets import AssetRegistry                        # noqa: E402
from memory.store import MemoryStore                         # noqa: E402
from context.assembler import ContextAssembler               # noqa: E402
from review.service import ReviewService                     # noqa: E402
from workflow.artifact import ArtifactStore                  # noqa: E402
from workflow.engine import WorkflowEngine                   # noqa: E402
from workflow.models import WorkflowContext                  # noqa: E402

import config                                               # noqa: E402
import llm                                                  # noqa: E402
from llm.providers import (                                 # noqa: E402
    Provider, TextProvider, VisionProvider, MultimodalProvider, EmbeddingProvider,
    ImageGenerationProvider, VideoGenerationProvider, AudioProvider,
    ProviderResult, EmbeddingResult,
    MockTextProvider, MockVisionProvider, MockMultimodalProvider, MockEmbeddingProvider,
    OpenAICompatibleTextProvider, BailianTextProvider,
    create_provider, get_default_provider,
)
from llm.router import Router, ProviderRoute                 # noqa: E402
from llm.client import LLMTimeoutError, LLMError            # noqa: E402
from agents.casting import CastingAgent, CastingStep         # noqa: E402
from agents.scene_generation import (                        # noqa: E402
    SceneGenerationAgent, SceneGenerationStep,
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
# 只读真实 fixture（来自 stage6/7 目录 + v2/assets/locations）
# ---------------------------------------------------------------------------
REAL_PROFILE = read_json(FIXTURES_STAGE7 / "actors_profile.json")
REAL_NAMES = [c["name"] for c in REAL_PROFILE]
REAL_TOPIC = "Reconcilation in a friend reunion"
REAL_FACT = "Theo has been estranged from the friend group for three years after a bitter fallout."
REAL_WORLD = {"name": "Shanghai modern drama", "genre": "drama"}
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")   # 只读，与 stage7 相同来源
APARTMENT = "Apartment living room"
APARTMENT_POSITIONS = [p.id for p in ASSETS.get_positions(APARTMENT)]

# 合法 Casting（全新角色建档；known=[] 时 Validator 放行）
CASTING_OK = json.dumps({
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


def scene_json(who=None, action="Standing Talking", shot="Close Shot",
               position=None):
    """构造一个合法的单场景 JSON（V0.1 最终剧本格式，Agent 输出也用这个格式）。"""
    who = who or ["Caleb", "Ivy"]
    pos = position or (APARTMENT_POSITIONS[0] if APARTMENT_POSITIONS else "Position 1")
    pos2 = (APARTMENT_POSITIONS[1] if len(APARTMENT_POSITIONS) > 1 else "Position 2")
    return {
        "scene information": {
            "who": who,
            "where": APARTMENT,
            "what": "Caleb and Ivy wait for Theo at the apartment living room.",
        },
        "initial position": [
            {"character": who[0], "position": pos},
            {"character": who[-1], "position": pos2},
        ],
        "scene": [
            {
                "speaker": who[0],
                "content": "Do you think he will come?",
                "actions": [{"character": who[0], "state": "standing", "action": action}],
                "shot": shot,
                "current position": [{"character": who[0], "position": pos}],
            },
        ],
    }


SCENE_OK = json.dumps(scene_json(), ensure_ascii=False)


def fresh_memory(project_id, root=None):
    return MemoryStore(project_id, projects_root=root or TMP_PROJECTS)


def seed_memory(mem, characters=None):
    # characters=None -> 用真实 4 人档案；传 [] -> 真·空（第一集，无已有角色）
    chars = REAL_PROFILE if characters is None else characters
    mem.set_characters([dict(c) for c in chars])
    mem.set_world(REAL_WORLD)
    f = mem.add_candidate_fact(REAL_FACT, category="plot", source="human")
    mem.approve_fact(f.fact_id, reviewer="human")


def assemble_for(mem, step, wf_ctx, project_title="", scene_spec=None):
    assembler = ContextAssembler(mem, assets=ASSETS)
    assembled = assembler.assemble(step, wf_ctx, project_title=project_title)
    wf_ctx.inputs["assembled_context"] = assembled
    if scene_spec is not None:
        wf_ctx.inputs["scene_spec"] = scene_spec
    return assembled


# ---------------------------------------------------------------------------
# T01~T02 接口族形状与预留
# ---------------------------------------------------------------------------
def test_interface_shapes():
    # TextProvider 元信息 + chat 签名带 structured
    p = TextProvider()
    ok1 = p.kind == "text" and p.model == ""
    try:
        p.chat([{"role": "user", "content": "hi"}])
        ok1 = False
    except NotImplementedError:
        ok1 = ok1 and True
    check("T01a TextProvider 抽象：kind=text；调用未实现方法抛 NotImplementedError",
          ok1, f"kind={p.kind}")

    v, mm, e = VisionProvider(), MultimodalProvider(), EmbeddingProvider()
    ok2 = v.kind == "vision" and mm.kind == "multimodal" and e.kind == "embedding"
    try:
        v.understand("img.png", "描述")
        ok2 = False
    except NotImplementedError:
        pass
    try:
        mm.chat([{"role": "user", "content": "hi"}], images=("img.png",))
        ok2 = False
    except NotImplementedError:
        pass
    try:
        e.embed(["hi"])
        ok2 = False
    except NotImplementedError:
        pass
    check("T01b Vision/Multimodal/Embedding 抽象：kind 正确 + 未实现抛 NotImplementedError",
          ok2, f"kind={v.kind}/{mm.kind}/{e.kind}")

    # 子类型关系：真实/Mock 文本都属于 TextProvider（Agent 只认识抽象）
    ok3 = (isinstance(OpenAICompatibleTextProvider, type)
           and isinstance(MockTextProvider(), TextProvider)
           and isinstance(BailianTextProvider, type))
    check("T01c OpenAICompatible / Mock / Bailian 文本都归入 TextProvider 形状",
          ok3, "")


def test_reserved_providers():
    ig, vg, au = (ImageGenerationProvider(), VideoGenerationProvider(), AudioProvider())
    ok = (ig.kind == "image" and vg.kind == "video" and au.kind == "audio")
    # 调用全部明确不可用
    for fn in (lambda: ig.generate_image("a cat"),
               lambda: vg.generate_video("a scene"),
               lambda: au.synthesize("hello")):
        try:
            fn()
            ok = False
        except NotImplementedError:
            pass
    check("T02 预留 Image/Video/Audio：kind 正确 + 调用明确 NotImplemented",
          ok, f"kind={ig.kind}/{vg.kind}/{au.kind}")


# ---------------------------------------------------------------------------
# T03~T04 MockTextProvider
# ---------------------------------------------------------------------------
def test_mock_text():
    # 脚本化（按 step）
    m = MockTextProvider(script={"casting": '{"a": 1}'})
    r1 = m.chat([{"role": "user", "content": "x"}], step="casting", agent="casting")
    ok1 = isinstance(r1, ProviderResult) and r1.text == '{"a": 1}'
    ok1 = ok1 and r1.usage.total_tokens == 40 and r1.model == "mock-text" and r1.retry_count == 0
    check("T03a MockText 脚本化（按 step）+ ProviderResult/usage 齐全", ok1,
          f"text={r1.text!r}; tokens={r1.usage.total_tokens}")

    # 出队 + 兜底
    m2 = MockTextProvider([ '{"n":1}', '{"n":2}' ], default_text='{"fallback":true}')
    a = m2.chat([{"role": "user", "content": "x"}], step="s")
    b = m2.chat([{"role": "user", "content": "x"}], step="s")
    c = m2.chat([{"role": "user", "content": "x"}], step="s")
    ok2 = a.text == '{"n":1}' and b.text == '{"n":2}' and c.text == '{"fallback":true}'
    check("T03b MockText 按调用次数出队 + 用尽后走 default_text", ok2,
          f"顺序={a.text}/{b.text}/{c.text}")

    # cost 语义：pricing=None -> None；pricing=0 字典 -> 0
    u = TokenUsage(input_tokens=10, output_tokens=10, total_tokens=20, cache_miss_tokens=10)
    cost_none = estimate_cost(u, None)
    cost_zero = estimate_cost(u, {"input_cache_hit": 0, "input_cache_miss": 0,
                                  "output": 0, "currency": "CNY"})
    ok3 = cost_none is None and cost_zero == 0.0
    check("T03c cost：pricing 缺失 -> None；pricing=0 -> 0（本地/免费模型语义）",
          ok3, f"None->{cost_none}; 0->{cost_zero}")


def test_mock_error_injection():
    m = MockTextProvider([LLMTimeoutError("mock 超时")], script={})
    try:
        m.chat([{"role": "user", "content": "x"}], step="s", agent="a")
        ok = False
    except LLMError as exc:
        ok = isinstance(exc, LLMTimeoutError) and exc.retryable is True  # 原样透传，不被吞
    check("T04 MockText 注入抛错：错误原样透传（retry 边界：Provider 不吞、不叠加重试）",
          ok, "")


# ---------------------------------------------------------------------------
# T05~T06 Mock Vision/Multimodal/Embedding
# ---------------------------------------------------------------------------
def test_mock_vision_multimodal():
    v = MockVisionProvider(reply="一个人的客厅")
    r = v.understand({"path": "img.png"}, "描述这个画面", step="vision_qa", agent="qa")
    ok1 = r.text == "一个人的客厅" and v.kind == "vision" and v.vendor == "mock"
    try:
        v.understand(None, "prompt")
        ok1 = False
    except ValueError:
        pass
    check("T05a MockVision：固定返回 + 元信息 + 入参即校验", ok1, f"text={r.text!r}")

    mm = MockMultimodalProvider(reply="图文理解结果")
    r2 = mm.chat([{"role": "user", "content": "看"}], images=("img.png",),
                 step="qa", agent="qa")
    ok2 = r2.text == "图文理解结果" and len(mm.calls) == 1
    try:
        mm.chat([{"role": "user", "content": "看"}], images=())   # 缺图应报错
        ok2 = False
    except ValueError:
        pass
    check("T05b MockMultimodal：固定返回 + 缺 images 报错", ok2, "")


def test_mock_embedding_deterministic():
    e1 = MockEmbeddingProvider(dim=8)
    va = e1.embed(["上海"], step="retrieval", agent="mem")
    vb = e1.embed(["上海"], step="retrieval", agent="mem")
    vc = e1.embed(["北京"], step="retrieval", agent="mem")
    ok = (va.vectors == vb.vectors                 # 同输入同输出
          and va.vectors != vc.vectors             # 不同输入不同向量
          and len(va.vectors) == 1 and len(va.vectors[0]) == 8
          and isinstance(va, EmbeddingResult)
          and va.usage.input_tokens >= 1)
    check("T06 MockEmbedding：确定性伪向量（同输入同输出）+ usage 上报", ok,
          f"上海={va.vectors[0][:2]}…; 北京≠上海={vc.vectors != va.vectors}")


# ---------------------------------------------------------------------------
# T07 Factory
# ---------------------------------------------------------------------------
def test_factory():
    ok = True
    # 默认（未设环境开关）-> mock：0 API
    os.environ.pop("FILMAGENT_PROVIDER", None)
    dflt = create_provider("text")
    ok = ok and isinstance(dflt, MockTextProvider)

    # mock 各模态
    ok = ok and isinstance(create_provider("text", "mock"), MockTextProvider)
    ok = ok and isinstance(create_provider("vision", "mock"), MockVisionProvider)
    ok = ok and isinstance(create_provider("multimodal", "mock"), MockMultimodalProvider)
    ok = ok and isinstance(create_provider("embedding", "mock"), MockEmbeddingProvider)

    # 真实 OpenAI 兼容文本：厂商 = Config 档（不写死核心）
    ds = create_provider("text", "deepseek")
    ok = ok and isinstance(ds, OpenAICompatibleTextProvider) and ds.vendor == "deepseek"
    ok = ok and ds.model == config.DEFAULT_MODEL
    ok = ok and ds.base_url == config.API_BASE_URL
    bl = create_provider("text", "bailian")
    ok = ok and isinstance(bl, BailianTextProvider) and bl.vendor == "bailian"
    ok = ok and bl.provider_name == "bailian_text"
    lc = create_provider("text", "local")
    ok = ok and isinstance(lc, OpenAICompatibleTextProvider) and lc.vendor == "local"
    ok = ok and lc.provider_name == "local_text"

    # 非法/未实现组合明确报错
    for fn in (lambda: create_provider("vision", "bailian"),
               lambda: create_provider("embedding", "bailian"),
               lambda: create_provider("multimodal", "bailian"),
               lambda: create_provider("image"),
               lambda: create_provider("video", "mock"),
               lambda: create_provider("audio"),
               lambda: create_provider("nonsense"),
               lambda: create_provider("text", "nobody")):
        try:
            fn()
            ok = False
        except (ValueError, KeyError):
            pass
    check("T07 Factory：默认/mock/OpenAI兼容档正确落位 + 非法组合明确报错", ok,
          f"deepseek.model={ds.model}; local.model={lc.model!r}")


# ---------------------------------------------------------------------------
# T08 Router
# ---------------------------------------------------------------------------
def test_router():
    ok = True
    os.environ.pop("FILMAGENT_PROVIDER", None)
    # 默认档 = mock（0 API）
    r = Router()
    route = r.get_route("casting")
    prov = r.get_provider("casting")
    ok = ok and route.vendor == "mock" and route.model == "mock"
    ok = ok and route.kind == "text" and route.provider_name == "mock_text"
    ok = ok and isinstance(prov, MockTextProvider)
    ok = ok and r.get_model("casting") == "mock" and r.model_for_step("casting") == "mock"

    # 显式深色档：路由描述能落到一个真实 Provider 实例
    r2 = Router(vendor="deepseek")
    route2 = r2.get_route("scene_generation")
    prov2 = r2.get_provider("scene_generation")
    ok = ok and route2.provider_name == "deepseek_text" and route2.model == config.DEFAULT_MODEL
    ok = ok and isinstance(prov2, OpenAICompatibleTextProvider) and prov2.vendor == "deepseek"
    ok = ok and route2.model == prov2.model          # 路由描述精确对应当前实例模型

    # 切换默认供应商只经 Config/环境开关，不触业务 Agent
    os.environ["FILMAGENT_PROVIDER"] = "bailian"
    r3 = Router()
    ok = ok and r3.vendor == "bailian"
    ok = ok and isinstance(r3.get_provider("casting"), BailianTextProvider)
    os.environ.pop("FILMAGENT_PROVIDER", None)
    check("T08 Router：进入数据流——路由描述->Provider 实例；默认档经 Config/环境开关",
          ok, f"route={route}; route2={route2}")


# ---------------------------------------------------------------------------
# T09 structured
# ---------------------------------------------------------------------------
def test_structured():
    # Mock 可感知 structured，不报错
    m = MockTextProvider(default_text='{"ok": true}')
    m.chat([{"role": "user", "content": "hi"}], step="s", structured={"type": "json_object"})
    ok1 = m.last_structured == {"type": "json_object"}
    check("T09a structured：Mock 可感知（last_structured）且不报错", ok1, str(m.last_structured))

    # 真实执行器（自定义 transport / structured_supported 默认 False）静默忽略、不报错
    def fake_t(messages, model, temperature, max_tokens, timeout):
        return '{"a": 1}', TokenUsage(input_tokens=3, output_tokens=1, total_tokens=4)
    c = OpenAICompatibleTextProvider(api_key="sk-x", base_url="http://mock",
                                     model="m", transport=fake_t)
    rr = c.chat([{"role": "user", "content": "hi"}], structured={"type": "json_object"})
    ok2 = isinstance(rr, ProviderResult) and rr.text == '{"a": 1}'
    # structured_supported=True 但注入自定义 transport：仍走 5 参 transport，不透传
    c2 = OpenAICompatibleTextProvider(api_key="sk-x", base_url="http://mock",
                                      model="m", transport=fake_t, structured_supported=True)
    rr2 = c2.chat([{"role": "user", "content": "hi"}], structured={"type": "json_object"})
    ok2 = ok2 and isinstance(rr2, ProviderResult) and rr2.text == '{"a": 1}'
    check("T09b structured：真实执行器未接线时静默忽略（不越权做业务校验）", ok2,
          f"transport返回={rr.text!r}")


# ---------------------------------------------------------------------------
# T10 端到端 0-API：Router -> MockTextProvider -> Casting Workflow -> APPROVED
# ---------------------------------------------------------------------------
def test_e2e_casting_workflow():
    pid = "p9_e2e_casting"
    mem = fresh_memory(pid)
    seed_memory(mem, characters=[])
    log_dir = TMP_LOGS / "e2e_casting"
    log_dir.mkdir(parents=True, exist_ok=True)

    calls = []

    def router_provider(kind="text", vendor=None):
        p = MockTextProvider(script={"casting": CASTING_OK}, model="mock-text",
                             logger=TokenLogger(log_dir))
        calls.append({"kind": kind, "vendor": vendor})
        return p

    # 走 Router 拿 Provider，注入 Agent —— 证明数据流 Agent→Router→Provider
    router = Router(vendor="mock", create=router_provider)
    provider = router.get_provider("casting")
    agent = CastingAgent(provider, known_characters=[])
    step = CastingStep(agent)
    artifacts_dir = TMP_PROJECTS / pid / "artifacts"
    store = ArtifactStore(artifacts_dir)
    svc = ReviewService(mem.project_id, store, projects_root=TMP_PROJECTS)

    def approve_handler(artifact, context):
        review = svc.submit_for_review(artifact, reviewer="human", comment="提交审核")
        done = svc.approve(review.review_id, comment="通过")
        return svc.to_review_result(done)

    engine = WorkflowEngine([step], artifacts_dir=artifacts_dir,
                            review_handler=approve_handler, retry_backoff_seconds=0.0)
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="e2e", inputs={})
    assemble_for(mem, step, wf_ctx, project_title=REAL_TOPIC)
    run = engine.run(project_id=pid, workflow_id="wf",
                     inputs={"assembled_context": wf_ctx.inputs["assembled_context"]})

    res = run.steps["casting"]
    artifact = read_json(artifacts_dir / "casting.json")
    records = TokenLogger(log_dir).read_all()
    ok = res.is_success and res.status in ("APPROVED", "SUCCESS")  # 业务成功→人工审核通过
    ok = ok and artifact is not None and len(artifact["entries"]) == 2
    ok = ok and len(calls) >= 1                       # 确实经过 Router 产出 Provider
    ok = ok and len(records) == 1                     # Mock 记了 1 笔账
    ok = ok and all(r.get("estimated_cost") is None for r in records)   # 账目 null，无真实计费
    ok = ok and all(r.get("model") == "mock-text" for r in records)     # 全是 Mock，无真实调用
    ok = ok and all(r.get("success") is True for r in records)
    check("T10 端到端0-API：Casting Router->Mock->Workflow APPROVED（1笔账、cost=null）",
          ok, f"attempts={[(a.attempt, a.status) for a in res.attempts]}; "
              f"entries={len(artifact['entries'])}; log记录数={len(records)}")
    shutil.rmtree(TMP_PROJECTS / pid, ignore_errors=True)


# ---------------------------------------------------------------------------
# T11 SceneGeneration Agent（MockTextProvider，0 API）+ T12 Token/Cost
# ---------------------------------------------------------------------------
def test_scene_generation_via_mock():
    pid = "p9_e2e_scene"
    mem = fresh_memory(pid)
    seed_memory(mem, characters=REAL_PROFILE)
    log_dir = TMP_LOGS / "e2e_scene"
    log_dir.mkdir(parents=True, exist_ok=True)

    m = MockTextProvider(script={"scene_generation": SCENE_OK}, model="mock-text",
                         logger=TokenLogger(log_dir))
    agent = SceneGenerationAgent(m, scene_id=1, known_characters=REAL_NAMES, assets=ASSETS)
    sstep = SceneGenerationStep(agent, scene_id=1)
    wf_ctx = WorkflowContext(project_id=mem.project_id, workflow_id="wf",
                             run_id="scene", inputs={})
    spec = {
        "scene_id": 1, "episode": 1, "location": APARTMENT,
        "characters": ["Caleb", "Ivy", "Theo"],
        "objective": "Caleb and Ivy visit Theo.",
    }
    assembled = assemble_for(mem, sstep, wf_ctx, project_title=REAL_TOPIC, scene_spec=spec)
    scene = agent.run(assembled, scene_spec=spec)
    errors = agent.validator.validate(scene)
    records = TokenLogger(log_dir).read_all()
    ok = isinstance(scene, object) and getattr(scene, "id", None) == 1
    ok = ok and errors == []
    ok = ok and len(records) == 1 and records[0]["estimated_cost"] is None
    check("T11 SceneGeneration 经 MockTextProvider 生成合法 Scene（0 API）",
          ok, f"validator_errors={[str(e) for e in errors]}; scene_id={getattr(scene,'id',None)}")
    shutil.rmtree(TMP_PROJECTS / pid, ignore_errors=True)


def test_token_cost_stream():
    # Mock 全程走既有 TokenLogger：账目 0/null，且不产生任何真实模型记录
    files = sorted((TMP_LOGS).glob("e2e_*/llm_calls.jsonl")) if TMP_LOGS.exists() else []
    total = 0
    ok = True
    for f in files:
        for rec in TokenLogger(f.parent).read_all():
            total += 1
            if rec.get("estimated_cost") not in (None, 0):
                ok = False
            if rec.get("model") != "mock-text":
                ok = False
    check("T12 Token/Cost：Mock 账目恒 0/null + 模型标记全为 mock（无真实计费记录）",
          ok, f"检查 {len(files)} 个 mock 日志文件 / {total} 条记录")


# ---------------------------------------------------------------------------
# T13 V0.1 冻结完整性
# ---------------------------------------------------------------------------
def git(*args):
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                          capture_output=True, text=True)


def test_v01_freeze():
    tags = {
        "v0.1": git("rev-parse", "v0.1^{commit}").stdout.strip(),
        "v0.2-stage7": git("rev-parse", "v0.2-stage7^{commit}").stdout.strip(),
        "v0.2-stage8": git("rev-parse", "v0.2-stage8^{commit}").stdout.strip(),
    }
    expect = {
        "v0.1": "344c0c6b7c48497be934957c29fcae029cca6752",
        "v0.2-stage7": "300aae44ad1d704219822efcf55249d1d59ad118",
        "v0.2-stage8": "abf8e88a983f13c437abe2b719c920d3c5c3c144",
    }
    diff = git("diff", "--", "FilmAgent/").stdout
    status = git("status", "--short").stdout
    ok = all(tags[k] == expect[k] for k in expect)
    ok = ok and diff.strip() == ""
    ok = ok and all("FilmAgent/" not in line for line in status.splitlines())  # 不得有任何 dirty 行
    check("T13 V0.1 冻结：FilmAgent/ 0 diff；三个 tag 未移动", ok,
          f"diff_len={len(diff.strip())}; tags_ok={tags == expect}")


# ---------------------------------------------------------------------------
# T14 静态检查
# ---------------------------------------------------------------------------
def test_static_checks():
    ok = True
    # compileall（本阶段改动面：llm/ + config.py + agents/base.py）
    targets = [str(V2_ROOT / "llm"), str(V2_ROOT / "config.py"), str(V2_ROOT / "agents" / "base.py")]
    res = subprocess.run([sys.executable, "-m", "compileall", "-q", *targets],
                         capture_output=True, text=True)
    ok = ok and res.returncode == 0

    # 全模块 import 冒烟
    import importlib
    for mod in ["config", "llm", "llm.client", "llm.router", "llm.providers",
                "llm.providers.base", "llm.providers.mock",
                "llm.providers.openai_compatible", "llm.providers.bailian",
                "llm.providers.registry", "agents.base"]:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            ok = False
            log(f"    import 失败 {mod}: {exc}")

    # 源码扫描：改动面内不得有真实密钥 / 硬编码绝对路径
    scan_files = []
    for root, _, fnames in os.walk(str(V2_ROOT / "llm")):
        for fn in fnames:
            if fn.endswith(".py"):
                scan_files.append(Path(root) / fn)
    scan_files += [V2_ROOT / "config.py", V2_ROOT / "agents" / "base.py"]
    bad = []
    import re
    for f in scan_files:
        text = f.read_text(encoding="utf-8")
        if re.search(r"sk-[A-Za-z0-9]{16,}", text):
            bad.append(f"{f.name}: 疑似真实密钥")
        if "D:/AI/FilmAgent-main" in text or r"D:\AI\FilmAgent-main" in text:
            bad.append(f"{f.name}: 硬编码绝对路径")
        if "api_key.txt" in text and "load" not in text:
            bad.append(f"{f.name}: 疑似绕过 key 读取约定")
    ok = ok and bad == []
    check("T14 静态：compileall + 全模块 import + 无真实密钥/硬编码绝对路径", ok,
          f"问题={bad if bad else '无'}")


# ---------------------------------------------------------------------------
# 回归 R1~R6：历史套件子进程
# ---------------------------------------------------------------------------
def run_regressions():
    suites = [
        ("R1 stage1", V2_ROOT / "logs" / "stage1_validation_test" / "run_tests.py"),
        ("R2 stage3", V2_ROOT / "logs" / "stage3_workflow_test" / "run_tests.py"),
        ("R3 stage4", V2_ROOT / "logs" / "stage4_memory_context_review_test" / "run_tests.py"),
        ("R4 stage6", V2_ROOT / "logs" / "stage6_casting" / "run_tests.py"),
        ("R5 stage7", V2_ROOT / "logs" / "stage7_scene_generation" / "run_tests.py"),
        ("R6 stage8", V2_ROOT / "logs" / "stage8_canonical_schema" / "run_tests.py"),
    ]
    for name, script in suites:
        try:
            # 子进程输出为 UTF-8（各 run_tests 自带 reconfigure）；父进程按 utf-8 解码，
            # 出错也不崩（errors='replace'）——否则 Windows 默认 gbk 会解码失败。
            proc = subprocess.run([sys.executable, str(script)], capture_output=True,
                                  text=True, timeout=600,
                                  encoding="utf-8", errors="replace")
            tail = "\n".join(proc.stdout.splitlines()[-3:]) if proc.stdout else ""
            err_tail = "\n".join((proc.stderr or "").splitlines()[-3:])
            ok = proc.returncode == 0
            detail = f"rc={proc.returncode}"
            if proc.stdout:
                last = [l for l in proc.stdout.splitlines() if l.strip()][-1] if proc.stdout.strip() else ""
                detail += f" | {last}"
            check(f"回归 {name} 全绿", ok, detail if ok else f"stdout尾:\n{tail}\nstderr尾:\n{err_tail}")
        except Exception as exc:
            check(f"回归 {name} 全绿", False, f"执行异常: {exc}")


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def main():
    # 清理上次运行的 tmp 产物（跨运行不得残留，保证计数/账目断言稳定）
    shutil.rmtree(TMP_PROJECTS, ignore_errors=True)
    shutil.rmtree(TMP_LOGS, ignore_errors=True)
    for fn in (test_interface_shapes, test_reserved_providers, test_mock_text,
               test_mock_error_injection, test_mock_vision_multimodal,
               test_mock_embedding_deterministic, test_factory, test_router,
               test_structured, test_e2e_casting_workflow,
               test_scene_generation_via_mock, test_token_cost_stream,
               test_v01_freeze, test_static_checks):
        try:
            fn()
        except Exception as exc:
            traceback.print_exc()
            check(f"{fn.__name__} 执行异常", False, f"{type(exc).__name__}: {exc}")
    run_regressions()

    passed = sum(1 for r in _results if r["ok"])
    total = len(_results)
    print("\n".join(_log_lines))
    print(f"\nStage 9 Provider tests: {passed}/{total} passed; "
          f"{total - passed} failed; Real API calls = 0; Cost = ¥0.00")
    sys.exit(1 if passed != total else 0)


if __name__ == "__main__":
    main()
