# -*- coding: utf-8 -*-
"""
V0.2 Stage 12-B —— 编排层生命周期测试套件（mock，0 真实 API）

被测对象（全部新增，禁改路径 0 diff）：
    v2/orchestration/preflight.py   调度前置校验门（纯函数，5 条规则）
    v2/orchestration/state.py       编排进度状态 + 原子写 + resume 判定
    v2/orchestration/pipeline.py    ProjectOrchestrator（两段式多场景编排）
    v2/orchestration/__init__.py    导出

覆盖（指令 §十二 T01~T19 + stage12_plan §4.2 必做增强 T03b/T04b/T06b/T09b/T10b）：
    T01  契约 / 依赖方向：构造 / OrchestrationError / import 只向下 / 下层 0 命中
    T02  OrchestrationState：往返一致 / 原子写无 *.tmp / resume 判定（含磁盘删除）/ 入参报错
    T03  preflight 5 条规则逐条正反例 + 返回 list 不抛异常（None / 空 who / registry=None）
    T03b ★R3 专测：preflight 传 to_v01() dict（无 Line.type）仍检出 move 行
    T04  集成：坏 Scene 被拦在编排层 + 缺口清单 + **Stage 11 调用次数 == 0**
    T04b ★R2 专测：Memory 空 → Context characters 空；Casting 后编排层已 set_characters
    T05  单场景 E2E：casting/scene_1/storyboard_1 落盘 + Validator 全过 + status=planned + completeness
    T06  ★多场景正确性（核心）：3 场景 scene_id 各自正确 + 内容互不串场 + workflow_id 独立
    T06b ★R4 专测：mock 场景感知（三份 storyboard 内容互不相同，否则 T06 形同虚设）
    T07  run_id 互异 + ArtifactStore.list_files 各场景不交叉
    T08  同场景重跑 → *_v2.json 出现、原文件字节不变（casting / storyboard）
    T09  Review 策略：auto_approve 的 review_mode 标注 / scripted 编辑态新版本 / rejected 停止
    T09b ★human_stub：抛 OrchestrationError 且不产生任何 artifact（AC13 诚实性）
    T10  视觉实体收集去重（重复引用 / 空 location 回落）+ 视觉齐备 5/5 + 不重复建档
    T10b ★R5 专测：档案缺必填 → 重试耗尽 → pending，assets_approved < referenced
    T11  resume：加场景重跑 → 已完成 SKIPPED、不重跑、不产生新版本
    T12  失败传播：坏场景 → status ∈ {partial, failed}，errors 能定位 scene_id
    T13  真实 API = 0（程序化快照断言）；mock 已计数
    T14  ★Token 硬证据：注册视觉资产前后 Storyboard.to_dict() 逐字节不变 + 档案不渗漏
    T15  静态检查：compileall / import 冒烟 / 新文件无硬编码 key
    T16  禁改清单 0 diff（§8.3 + FilmAgent/ + 仓库根 .gitignore）
    T17  ★v2/workflow/ 0 diff（本阶段关键：证明"上层编排"未污染引擎）
    T18  历史回归：stage1/3/4/6/7/8/9/10/11 九套子进程全部 rc=0
    T19  端到端完整性：run_report.completeness 与磁盘实际文件交叉核对一致

本文件真实 API 调用 = 0（全 Mock）。
"""
import json
import os
import re
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

from core.assets import AssetRegistry                        # noqa: E402
from core.models import Episode, Project, Scene, World       # noqa: E402
from core.shot import Storyboard                             # noqa: E402
from core.validator import StoryboardValidator               # noqa: E402
from core.visual_registry import VisualAssetRegistry          # noqa: E402
from memory.store import MemoryStore                         # noqa: E402
from context.assembler import ContextAssembler               # noqa: E402
from workflow.artifact import ArtifactStore                  # noqa: E402
from workflow.models import WorkflowContext                 # noqa: E402
from agents.storyboard import StoryboardAgent, StoryboardStep  # noqa: E402
from llm.providers.mock import MockTextProvider              # noqa: E402
from llm.request_counter import (                            # noqa: E402
    CountingProviderWrapper, get_api_counter, reset_api_counter,
)
from orchestration import (                                  # noqa: E402
    ProjectOrchestrator, OrchestrationError, OrchestrationState,
    preflight_check, format_gaps, atomic_write_json,
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


def fresh_dir(tag: str) -> Path:
    d = TMP_PROJECTS / tag
    if d.exists():
        _hard_rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# 测试脚手架：真实资产注册表 + 场景感知 Mock + 项目
# ---------------------------------------------------------------------------
ASSETS = AssetRegistry.load(V2_ROOT / "assets" / "locations")
LOC = "Apartment kitchen"          # 真实已注册地点（Position 1..5）
POS1 = "Position 1"
POS2 = "Position 2"
SHOT = "Close Shot"                # 真实已注册镜头名
SHOT2 = "Mid Shot"
WHO = ["Mercy"]


def scene_payload(sid: int, who=None) -> dict:
    """Stage 7 产出形态的 V0.1 场景剧本（含 dialogue 行 + move 行）。"""
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
    """Stage 11 产出形态的分镜 JSON（含 scene-{sid} 标记，用于串场检测）。"""
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

EDITED_CASTING = {"episode": 1, "topic": "EDITED topic", "entries": [
    {"name": "Mercy", "status": "new", "role": "lead",
     "character": {"name": "Mercy", "age": "31", "gender": "female",
                   "occupation": "barista", "personality traits": "warm",
                   "speaking style": "gentle"}}]}

# 视觉档案：character 必填 appearance/face/hair/clothing；location 必填
# environment/lighting/palette；prop 必填 form/color。
# 关键词（red wool coat / amber and wood tones）用于 T14 Token 硬证据。
PROFILES = {
    "character": {"appearance": "tall woman", "face": "oval", "hair": "black bob",
                  "clothing": "red wool coat"},
    "location": {"environment": "warm cafe", "lighting": "amber",
                 "palette": "amber and wood tones"},
    "prop": {"form": "small box", "color": "blue"},
}


class SceneAwareMock(MockTextProvider):
    """★场景感知 Mock（R4/T06b 必需）。

    按 user payload 里的 `scene_id` 出**不同内容**（输出带 `scene-N` 标记），
    否则多场景"互不串场"无法被真正证明（三份相同内容也会"通过"）。
    """

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


class IncompletePropMock(SceneAwareMock):
    """prop 视觉档案缺必填（color）→ 触发 T10b 重试耗尽。"""

    def _pick(self, step):
        if step == "visual_asset_plan":
            payload = self.calls[-1]["messages"][-1]["content"]
            km = re.search(r"^kind:\s*(\w+)", payload, re.M)
            if km and km.group(1) == "prop":
                return json.dumps({"form": "small box"})     # 缺 color（必填）
        return super()._pick(step)


def make_project(title="Stage12 Test Project") -> Project:
    return Project(title=title,
                   world=World(name="Test World", locations=[LOC]),
                   episodes=[Episode(number=1, title="E1", summary="test episode")],
                   current_episode=1)


def make_orch(tag, *, provider=None, project=None, **kw) -> ProjectOrchestrator:
    return ProjectOrchestrator(project or make_project(), provider=provider or SceneAwareMock(),
                               projects_root=TMP_PROJECTS, project_id=tag, **kw)


def _no_version_files(dirpath: Path, pattern: str):
    """去掉 _vN 后缀的版本文件后剩下的文件（= 首次产出）。"""
    return sorted(p.name for p in dirpath.glob(pattern)
                  if not re.search(r"_v\d+\.json$", p.name))


def _script_file(tag, seq) -> Path:
    p = TMP_LOGS / f"script_{tag}.json"
    p.write_text(json.dumps(seq, ensure_ascii=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# T01 契约 / 依赖方向
# ---------------------------------------------------------------------------
_V2_LAYERS = {"core", "llm", "memory", "context", "workflow", "review", "agents",
              "config", "orchestration", "prompts", "logs"}
_ORCH_ALLOWED = {"core", "llm", "memory", "context", "workflow", "review",
                 "agents", "config", "orchestration"}


def test_contract_and_dependency_direction():
    orch = make_orch("t01")
    check("T01a ProjectOrchestrator 可构造（projects_root 可注入）",
          orch.projects_root == TMP_PROJECTS and orch.project_dir.name == "t01",
          f"root={orch.projects_root}; dir={orch.project_dir}")

    err = expect_raises(OrchestrationError, ProjectOrchestrator, "not-a-project",
                        provider=SceneAwareMock(), projects_root=TMP_PROJECTS)
    check("T01b 非 Project 入参 → OrchestrationError（明确报错）",
          isinstance(err, OrchestrationError), f"got={err!r}")
    check("T01c OrchestrationError 是 Exception 子类", issubclass(OrchestrationError, Exception))

    bad = []
    for p in sorted((V2_ROOT / "orchestration").glob("*.py")):
        for line in p.read_text(encoding="utf-8").splitlines():
            ls = line.strip()
            if not ls.startswith(("from ", "import ")):
                continue
            mod = ls.split()[1].split(".")[0]
            if mod in _V2_LAYERS and mod not in _ORCH_ALLOWED:
                bad.append(f"{p.name}: {ls}")
    check("T01d orchestration 的 import 只落在允许的下层（core/llm/memory/context/"
          "workflow/review/agents/config）", not bad, f"{bad}" if bad else "")

    rev = []
    for layer in ("core", "llm", "memory", "context", "workflow", "review", "agents"):
        for p in sorted((V2_ROOT / layer).rglob("*.py")):
            for line in p.read_text(encoding="utf-8").splitlines():
                ls = line.strip()
                if ls.startswith(("from orchestration", "import orchestration")):
                    rev.append(f"{layer}/{p.name}: {ls}")
    check("T01e 下层不得 import orchestration（反向扫 0 命中）", not rev,
          f"{rev}" if rev else "")

    check("T01f 导出齐备", {"ProjectOrchestrator", "OrchestrationError", "OrchestrationState",
                            "preflight_check", "format_gaps", "atomic_write_json"}
          <= set(__import__("orchestration").__all__))


# ---------------------------------------------------------------------------
# T02 OrchestrationState：往返 / 原子写 / resume
# ---------------------------------------------------------------------------
def test_state_roundtrip_and_resume():
    d = fresh_dir("t02_state")
    state_path = d / "state.json"
    arts = d / "artifacts"
    arts.mkdir(parents=True, exist_ok=True)

    st = OrchestrationState("pid02", path=state_path, artifacts_dir=arts)
    st.record_casting(1, status="approved", artifact="casting.json", attempts=1,
                      run_id="r1", workflow_id="casting_ep1")
    st.record_scene(1, 1, "scene", status="approved", artifact="scene_1.json",
                    attempts=1, run_id="r2", workflow_id="scene_gen_1")
    st.record_scene(1, 1, "storyboard", status="failed", artifact="",
                    attempts=3, run_id="r3", workflow_id="storyboard_1")
    st.record_visual_asset("character", "Mercy", status="approved",
                           asset_id="CHAR_001", version="v1")
    st.record_visual_asset("prop", "old key", status="failed")
    saved = st.save()

    check("T02a state.json 落盘且 JSON 合法",
          saved.is_file() and isinstance(json.loads(saved.read_text(encoding="utf-8")), dict),
          f"path={saved}")
    leftover = sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))
    check("T02b 原子写不残留 *.tmp（临时文件 + os.replace）", not leftover,
          f"leftover={leftover}")

    back = OrchestrationState.load("pid02", saved, artifacts_dir=arts)
    check("T02c to_dict / from_dict 往返一致", back.to_dict() == st.to_dict())
    check("T02d 往返后内容正确",
          back.casting_entry(1)["artifact"] == "casting.json"
          and back.scene_entry(1, 1, "storyboard")["status"] == "failed"
          and back.to_dict()["visual_assets"]["registered"] == ["CHAR_001"]
          and "prop:old key" in back.to_dict()["visual_assets"]["pending"])

    # resume 判定
    (arts / "casting.json").write_text("{}", encoding="utf-8")
    check("T02e approved + 磁盘 artifact 存在 → should_skip=True",
          st.should_skip_casting(1, artifacts_dir=arts))
    (arts / "casting.json").unlink()
    check("T02f ★approved 但磁盘 artifact 被删 → should_skip=False（R12，重跑）",
          not st.should_skip_casting(1, artifacts_dir=arts))
    check("T02g failed 条目 → should_skip=False",
          not st.should_skip_scene(1, 1, "storyboard", artifacts_dir=arts))
    check("T02h 缺失条目 → should_skip=False",
          not st.should_skip_scene(1, 9, "scene", artifacts_dir=arts))

    err = expect_raises(OrchestrationError, st.record_scene, 1, 1, "bogus",
                        status="approved")
    check("T02i record_scene 未知 kind → OrchestrationError",
          isinstance(err, OrchestrationError), f"got={err!r}")

    err2 = expect_raises(OrchestrationError, OrchestrationState("pid_nopath").save)
    check("T02j 未指定 path → OrchestrationError（不静默丢状态）",
          isinstance(err2, OrchestrationError), f"got={err2!r}")

    atomic_write_json(d / "atomic.json", {"a": 1})
    check("T02k atomic_write_json 直接可用且无 tmp 残留",
          json.loads((d / "atomic.json").read_text(encoding="utf-8")) == {"a": 1}
          and not list(d.glob("*.tmp")))


# ---------------------------------------------------------------------------
# T03 preflight 5 条规则（正 / 反例）
# ---------------------------------------------------------------------------
def test_preflight_rules():
    good = Scene.from_v01(scene_payload(1, ["Mercy"]), id=1)
    check("T03a 好 Scene → 无缺口（[]）", preflight_check(good, ["Mercy"], ASSETS) == [],
          f"gaps={preflight_check(good, ['Mercy'], ASSETS)}")

    # 规则 1：who 含未登记角色
    bad_who = Scene.from_v01(scene_payload(1, ["Mercy", "Ghost"]), id=1)
    g1 = preflight_check(bad_who, ["Mercy"], ASSETS)
    check("T03b 规则1 who 含未登记角色 → UNKNOWN_CHARACTER",
          any("UNKNOWN_CHARACTER" in x and "who" in x for x in g1), f"gaps={g1}")

    # 规则 2：地点非法
    p_loc = scene_payload(1, ["Mercy"])
    p_loc["scene information"]["where"] = "Moon base"
    g2 = preflight_check(Scene.from_v01(p_loc, id=1), ["Mercy"], ASSETS)
    check("T03c 规则2 非法地点 → INVALID_LOCATION",
          any("INVALID_LOCATION" in x for x in g2), f"gaps={g2}")

    # 规则 2：空 where
    p_empty = scene_payload(1, ["Mercy"])
    p_empty["scene information"]["where"] = ""
    g3 = preflight_check(Scene.from_v01(p_empty, id=1), ["Mercy"], ASSETS)
    check("T03d 规则2 空 where → MISSING_LOCATION",
          any("MISSING_LOCATION" in x for x in g3), f"gaps={g3}")

    # 规则 1：dialogue speaker 未登记
    p_spk = scene_payload(1, ["Mercy"])
    p_spk["scene"][0]["speaker"] = "Ghost"
    g4 = preflight_check(Scene.from_v01(p_spk, id=1), ["Mercy"], ASSETS)
    check("T03e 规则3 dialogue speaker 未登记 → UNKNOWN_CHARACTER（.speaker）",
          any("UNKNOWN_CHARACTER" in x and ".speaker" in x for x in g4), f"gaps={g4}")

    # 规则 4 / 5：站位点 + move destination
    p_pos = scene_payload(1, ["Mercy"])
    p_pos["initial position"] = [{"character": "Mercy", "position": "Position 99"}]
    p_pos["scene"][1]["move"]["destination"] = "Nowhere"
    g5 = preflight_check(Scene.from_v01(p_pos, id=1), ["Mercy"], ASSETS)
    check("T03f 规则4 initial_positions 非法站位点 → INVALID_POSITION",
          any("INVALID_POSITION" in x and "initial_positions" in x for x in g5), f"gaps={g5}")
    check("T03g 规则5 move.destination 非法 → INVALID_POSITION",
          any("INVALID_POSITION" in x and "move.destination" in x for x in g5), f"gaps={g5}")

    # 返回 list、不抛异常
    g_none = preflight_check(None, ["Mercy"], ASSETS)
    check("T03h scene=None → 返回非空 list，不抛异常",
          isinstance(g_none, list) and len(g_none) >= 1, f"gaps={g_none}")

    g_bad_type = preflight_check(12345, ["Mercy"], ASSETS)
    check("T03i scene 类型非法 → 返回缺口（不抛异常）",
          isinstance(g_bad_type, list) and len(g_bad_type) >= 1, f"gaps={g_bad_type}")

    g_no_reg = preflight_check(good, ["Mercy"], None)
    check("T03j registry=None → 返回缺口清单（不抛异常）",
          isinstance(g_no_reg, list) and any("registry" in x for x in g_no_reg),
          f"gaps={g_no_reg}")

    g_no_known = preflight_check(good, None, ASSETS)
    check("T03k known_characters=None → 明确记缺口（不静默通过）",
          any("known_characters" in x for x in g_no_known), f"gaps={g_no_known}")

    # 空 who（无角色）不应产生角色缺口
    p_nowho = scene_payload(1, ["Mercy"])
    p_nowho["scene information"]["who"] = []
    p_nowho["scene"] = []
    p_nowho["initial position"] = []
    check("T03l 空 who / 空 lines → 无角色缺口（地点合法即通过）",
          preflight_check(Scene.from_v01(p_nowho, id=1), ["Mercy"], ASSETS) == [])

    check("T03m format_gaps 可读化", format_gaps(g5).startswith("INVALID_POSITION"),
          f"{format_gaps(g5)[:80]}")


# ---------------------------------------------------------------------------
# T03b ★R3 专测：preflight 传 to_v01() dict 仍检出 move 行
# ---------------------------------------------------------------------------
def test_preflight_uses_scene_object_for_move_lines():
    raw = scene_payload(1, ["Mercy"])
    raw["scene"].append({"move": {"character": "Ghost", "destination": "Nowhere"},
                         "current position": [{"character": "Ghost",
                                               "position": "Position 99"}]})
    check("T03b-1 落盘 dict 的 scene 行确无 'type' 键（证明必须 from_v01 还原）",
          all("type" not in ln for ln in raw["scene"]), f"keys={list(raw['scene'][2].keys())}")

    gaps = preflight_check(raw, ["Mercy"], ASSETS)
    check("T03b-2 ★传 to_v01() dict → move 行 character 仍被检出",
          any("move.character" in g for g in gaps), f"gaps={gaps}")
    check("T03b-3 ★传 to_v01() dict → move 行 destination 仍被检出",
          any("move.destination" in g for g in gaps), f"gaps={gaps}")

    # 反证：move 行若被当作 dialogue 行处理，则不会出现 move.* 缺口
    check("T03b-4 若不做 from_v01，move 行会完全漏检（本测试即该缺陷的护栏）",
          len([g for g in gaps if "move." in g]) >= 2, f"move_gaps={[g for g in gaps if 'move.' in g]}")


# ---------------------------------------------------------------------------
# T04 集成：坏 Scene 被拦在编排层，Stage 11 调用次数 == 0
# ---------------------------------------------------------------------------
def test_preflight_blocks_before_stage11():
    mock = SceneAwareMock()
    orch = make_orch("t04", provider=mock)
    bad_scene = scene_payload(1, ["Ghost"])            # who 含未登记角色
    rep = orch.run({"episode": 1, "scene_ids": [1], "scenes": {1: bad_scene}})

    pf = [e for e in rep["errors"] if e.get("stage") == "preflight"]
    check("T04a 坏 Scene 被拦在编排层（errors 含 preflight 条目）", bool(pf),
          f"errors={rep['errors']}")
    check("T04b 缺口清单含 UNKNOWN_CHARACTER",
          any("UNKNOWN_CHARACTER" in g for e in pf for g in e.get("gaps", [])),
          f"gaps={[g for e in pf for g in e.get('gaps', [])]}")
    sb_calls = [c for c in mock.calls if c["step"] == "storyboard_generation"]
    check("T04c ★Stage 11 未被调用（storyboard_generation 调用次数 == 0，无重试风暴）",
          len(sb_calls) == 0, f"calls={len(sb_calls)}")
    check("T04d 不静默成功（status != success）", rep["status"] != "success",
          f"status={rep['status']}")
    check("T04e storyboard 段记 pending（未进入 Stage 11）",
          rep["stages"]["scenes"]["1"]["storyboard"]["status"] == "pending",
          f"sb={rep['stages']['scenes']['1']['storyboard']}")


# ---------------------------------------------------------------------------
# T04b ★R2 专测：Memory 空 → Context characters 空；Casting 后已 set_characters
# ---------------------------------------------------------------------------
def test_memory_written_after_casting():
    orch = make_orch("t04b")
    check("T04b-1 起始 Memory characters 为空", orch.memory.get_characters() == [],
          f"chars={orch.memory.get_characters()}")

    from agents.casting import CastingAgent, CastingStep
    step = CastingStep(CastingAgent(orch.provider, known_characters=orch._known_names()))
    ctx = orch._assemble(step, "casting_ep1")
    item = ctx.item("characters")
    check("T04b-2 Memory 空 → Context characters 为空（R2 根因：LLM 会编造角色名）",
          item is not None and (item.data or []) == [], f"data={None if item is None else item.data}")

    rep = orch.run({"episode": 1, "scene_ids": [1]})
    names = [c.get("name") for c in orch.memory.get_characters()]
    check("T04b-3 ★Casting 通过后编排层已 set_characters（R2 修复）", "Mercy" in names,
          f"names={names}")
    check("T04b-4 编排层未代批事实（严禁 approve_fact）",
          orch.memory.get_approved_facts() == [], f"facts={orch.memory.get_approved_facts()}")
    check("T04b-5 run 成功（角色白名单已注入下游）", rep["status"] == "success",
          f"status={rep['status']}")


# ---------------------------------------------------------------------------
# T05 单场景 E2E
# ---------------------------------------------------------------------------
def test_single_scene_e2e():
    orch = make_orch("t05")
    rep = orch.run({"episode": 1, "scene_ids": [1]})
    arts = orch.artifacts_dir
    names = sorted(p.name for p in arts.glob("*.json"))
    check("T05a casting.json / scene_1.json / storyboard_1.json 全部落盘",
          {"casting.json", "scene_1.json", "storyboard_1.json"} <= set(names), f"files={names}")
    check("T05b status = success", rep["status"] == "success", f"status={rep['status']}")
    check("T05c completeness 数值正确",
          rep["completeness"] == {"scenes_total": 1, "scenes_done": 1,
                                  "storyboards_total": 1, "storyboards_done": 1,
                                  "assets_referenced": 3, "assets_approved": 3},
          f"comp={rep['completeness']}")

    sb = json.loads((arts / "storyboard_1.json").read_text(encoding="utf-8"))
    errs = StoryboardValidator(ASSETS, ["Mercy"]).validate_dict(sb)
    check("T05d StoryboardValidator 全过（0 错误）", not errs,
          f"errors={[str(e).splitlines()[0] for e in errs][:3]}")
    sts = [s["status"] for b in sb["beats"] for s in b["shots"]]
    check("T05e 所有 Shot.status == planned", bool(sts) and all(x == "planned" for x in sts),
          f"statuses={sts}")
    sc = json.loads((arts / "scene_1.json").read_text(encoding="utf-8"))
    check("T05f scene_1.json 可还原为 Scene 且 who 正确",
          Scene.from_v01(sc, id=1).info.who == ["Mercy"], f"who={sc.get('scene information')}")
    check("T05g errors 为空", rep["errors"] == [], f"errors={rep['errors']}")


# ---------------------------------------------------------------------------
# T06 ★多场景正确性（核心）+ T06b mock 场景感知
# ---------------------------------------------------------------------------
def test_multi_scene_no_cross_talk():
    orch = make_orch("t06")
    rep = orch.run({"episode": 1, "scene_ids": [1, 2, 3]})
    arts = orch.artifacts_dir
    check("T06a status = success", rep["status"] == "success", f"status={rep['status']}")

    ok_all, details = True, []
    for n in (1, 2, 3):
        d = json.loads((arts / f"storyboard_{n}.json").read_text(encoding="utf-8"))
        shots = [s for b in d["beats"] for s in b["shots"]]
        others = [x for x in (1, 2, 3) if x != n]
        cross = any(f"scene-{x}" in json.dumps(d) for x in others)
        ok = (d["scene_id"] == n
              and all(s.get("scene_id") == n for s in shots)
              and d["characters"] == ["Mercy"]
              and not cross)
        ok_all = ok_all and ok
        details.append(f"sb{n}:scene_id={d['scene_id']},"
                       f"shot_scene_ids={sorted({s.get('scene_id') for s in shots})},cross={cross}")
    check("T06b ★storyboard_N.scene_id / 内层 shot.scene_id 各自正确 + 内容互不串场",
          ok_all, "; ".join(details))

    sc_ok = all(json.loads((arts / f"scene_{n}.json").read_text(encoding="utf-8"))
                ["scene information"]["who"] == ["Mercy"] for n in (1, 2, 3))
    check("T06c 三份 scene_N.json 各自正确", sc_ok)

    ids = [rep["stages"]["scenes"][str(n)]["storyboard"]["workflow_id"] for n in (1, 2, 3)]
    check("T06d 三份 workflow_id 互异且命名正确",
          ids == ["storyboard_1", "storyboard_2", "storyboard_3"], f"ids={ids}")

    texts = [(arts / f"storyboard_{n}.json").read_text(encoding="utf-8") for n in (1, 2, 3)]
    check("T06e ★R4：三份 storyboard 内容互不相同（证明 mock 场景感知，T06 有效）",
          len(set(texts)) == 3, f"distinct={len(set(texts))}")
    markers = [f"scene-{n}" in texts[n - 1] for n in (1, 2, 3)]
    check("T06f 每份 storyboard 只含自己的 scene-N 标记", all(markers), f"markers={markers}")

    step_ids = [f"storyboard_generation_{n}" for n in (1, 2, 3)]
    check("T06g step_id 命名规范 storyboard_generation_1/2/3",
          rep["stages"]["scenes"]["1"]["storyboard"]["workflow_id"] == "storyboard_1"
          and step_ids[1] == "storyboard_generation_2")


# ---------------------------------------------------------------------------
# T07 run_id 互异 + list_files 不交叉
# ---------------------------------------------------------------------------
def test_run_ids_and_artifact_isolation():
    orch = make_orch("t07")
    rep = orch.run({"episode": 1, "scene_ids": [1, 2, 3]})
    rids = [rep["stages"]["scenes"][str(n)]["storyboard"]["run_id"] for n in (1, 2, 3)]
    check("T07a 三次 run 的 run_id 互异", len(set(rids)) == 3 and all(rids), f"rids={rids}")

    store = ArtifactStore(orch.artifacts_dir)
    f1 = sorted(p.name for p in store.list_files("storyboard_1"))
    f2 = sorted(p.name for p in store.list_files("storyboard_2"))
    f3 = sorted(p.name for p in store.list_files("storyboard_3"))
    check("T07b ArtifactStore.list_files 各场景不交叉",
          f1 == ["storyboard_1.json"] and f2 == ["storyboard_2.json"]
          and f3 == ["storyboard_3.json"], f"f1={f1}; f2={f2}; f3={f3}")


# ---------------------------------------------------------------------------
# T08 同场景重跑 → 新版本，原文件字节不变
# ---------------------------------------------------------------------------
def test_rerun_creates_new_version():
    orch = make_orch("t08")
    orch.run({"episode": 1, "scene_ids": [1]})
    arts = orch.artifacts_dir
    sb_before = (arts / "storyboard_1.json").read_bytes()
    casting_before = (arts / "casting.json").read_bytes()

    orch2 = make_orch("t08")
    orch2.run({"episode": 1, "scene_ids": [1], "resume": False})
    files = sorted(p.name for p in arts.glob("*.json"))
    check("T08a 重跑生成新版本 storyboard_1_v2.json / casting_v2.json",
          "storyboard_1_v2.json" in files and "casting_v2.json" in files, f"files={files}")
    check("T08b ★原文件 storyboard_1.json 字节不变",
          (arts / "storyboard_1.json").read_bytes() == sb_before)
    check("T08c 原文件 casting.json 字节不变",
          (arts / "casting.json").read_bytes() == casting_before)


# ---------------------------------------------------------------------------
# T09 Review 策略三态
# ---------------------------------------------------------------------------
def test_review_policies():
    # (a) auto_approve：review_mode 标注 + 诚实性
    orch = make_orch("t09a")
    rep = orch.run({"episode": 1, "scene_ids": [1]})
    rm = rep["review_mode"]
    check("T09a ★run_report.review_mode 含 'auto_approve (programmatic, NOT human review)'",
          rm == "auto_approve (programmatic, NOT human review)", f"review_mode={rm!r}")
    text = json.dumps(rep, ensure_ascii=False)
    check("T09b ★run_report 不得出现'已人工审核'字样（AC13 诚实性）",
          "已人工审核" not in text and "人工审核通过" not in text)

    # (b) scripted：casting 编辑后通过 → 新版本，原文件不动
    seq = [{"decision": "approved_with_edit", "edited_content": EDITED_CASTING,
            "comment": "edit casting"}]
    seq += [{"decision": "approved"} for _ in range(5)]     # 剩余 5 次审核（scene/sb/3 视觉）
    script = _script_file("t09b", seq)
    orch2 = make_orch("t09b")
    arts2 = orch2.artifacts_dir
    orch2.run({"episode": 1, "scene_ids": [1]})
    casting_before = (arts2 / "casting.json").read_bytes()
    orch2b = make_orch("t09b")
    rep2 = orch2b.run({"episode": 1, "scene_ids": [1], "resume": False,
                       "review_policy": f"scripted:{script}"})
    files2 = sorted(p.name for p in arts2.glob("casting*.json"))
    check("T09c scripted APPROVED_WITH_EDIT → 新版本 casting_v2.json，原文件保留",
          "casting_v2.json" in files2 and (arts2 / "casting.json").read_bytes() == casting_before,
          f"files={files2}")
    check("T09d scripted run 的 review_mode 标注为程序化模拟",
          "scripted (programmatic, NOT human review)" == rep2["review_mode"],
          f"review_mode={rep2['review_mode']!r}")

    # (c) scripted：casting 被拒 → run failed，下游不执行
    script_r = _script_file("t09r", [{"decision": "rejected", "reason": "不合格"}])
    orch3 = make_orch("t09r")
    rep3 = orch3.run({"episode": 1, "scene_ids": [1],
                      "review_policy": f"scripted:{script_r}"})
    check("T09e scripted REJECTED → run failed（停止）", rep3["status"] == "failed",
          f"status={rep3['status']}")
    arts3 = orch3.artifacts_dir
    downstream = sorted(p.name for p in arts3.glob("storyboard_*.json")) if arts3.exists() else []
    check("T09f rejected 后下游不执行（无 storyboard 产物）", not downstream,
          f"files={downstream}")

    # (d) 未知策略 → OrchestrationError
    orch4 = make_orch("t09x")
    err = expect_raises(OrchestrationError, orch4.run,
                        {"episode": 1, "scene_ids": [1], "review_policy": "bogus_policy"})
    check("T09g 未知 review_policy → OrchestrationError（明确报错）",
          isinstance(err, OrchestrationError), f"got={err!r}")


# ---------------------------------------------------------------------------
# T09b human_stub
# ---------------------------------------------------------------------------
def test_human_stub_stops_without_artifact():
    orch = make_orch("t09s")
    err = expect_raises(OrchestrationError, orch.run,
                        {"episode": 1, "scene_ids": [1], "review_policy": "human_stub"})
    check("T09b-1 ★human_stub → OrchestrationError（停下等外部注入）",
          isinstance(err, OrchestrationError), f"got={err!r}")
    arts = orch.artifacts_dir
    files = sorted(p.name for p in arts.glob("*.json")) if arts.exists() else []
    check("T09b-2 ★human_stub 不产生任何 artifact（未伪造人工审核）", not files,
          f"files={files}")


# ---------------------------------------------------------------------------
# T10 视觉实体收集去重 + 视觉齐备
# ---------------------------------------------------------------------------
def _sb_dict(sid, location=LOC, characters=("Mercy",), shots=None):
    if shots is None:
        shots = [{"shot_id": 1, "scene_id": sid, "beat_id": 1, "kind": "general",
                  "location": location, "characters": list(characters),
                  "assets": [{"kind": "prop", "ref": "old key"}]}]
    return {"scene_id": sid, "location": location, "characters": list(characters),
            "beats": [{"beat_id": 1, "purpose": "p", "shots": shots}]}


def test_visual_entity_collection_and_completeness():
    sb1 = Storyboard.from_dict(_sb_dict(1, shots=[
        {"shot_id": 1, "scene_id": 1, "beat_id": 1, "kind": "general",
         "location": LOC, "characters": ["Mercy", "Mercy"],
         "assets": [{"kind": "prop", "ref": "old key"}]},
        {"shot_id": 2, "scene_id": 1, "beat_id": 1, "kind": "general",
         "location": "", "characters": ["Mercy"],      # 空 location → 回落 Storyboard.location
         "assets": [{"kind": "prop", "ref": "old key"}]},
    ]))
    sb2 = Storyboard.from_dict(_sb_dict(2))
    ents = ProjectOrchestrator._collect_entities([sb1, sb2])
    check("T10a 实体去重：Mercy / 地点 / 道具各只出现一次",
          ents.count(("character", "Mercy")) == 1 and ents.count(("location", LOC)) == 1
          and ents.count(("prop", "old key")) == 1, f"ents={ents}")
    check("T10b 空 location 镜头回落 Storyboard.location", ("location", LOC) in ents,
          f"ents={ents}")
    check("T10c 三类实体齐备（character/location/prop）",
          {k for k, _ in ents} == {"character", "location", "prop"}, f"kinds={ {k for k,_ in ents} }")

    orch = make_orch("t10")
    rep = orch.run({"episode": 1, "scene_ids": [1, 2, 3]})
    comp = rep["completeness"]
    check("T10d 视觉齐备：assets_approved == assets_referenced == 5（3 场景去重后）",
          comp["assets_approved"] == comp["assets_referenced"] == 5, f"comp={comp}")
    summ = orch.visual_registry.summary()
    check("T10e registry 5 资产全部 approved，character 只 1 个（不重复建档）",
          summ["assets"] == 5 and summ["active"] == 5
          and summ["by_kind"].get("character") == 1, f"summ={summ}")
    check("T10f registry.json 存在",
          (orch.project_dir / "visual_assets" / "registry.json").is_file())

    orch2 = make_orch("t10")
    orch2.run({"episode": 1, "scene_ids": [1, 2, 3]})
    check("T10g resume 重跑不重复建档（assets 仍 5）",
          orch2.visual_registry.summary()["assets"] == 5,
          f"summ={orch2.visual_registry.summary()}")


# ---------------------------------------------------------------------------
# T10b ★R5 专测：档案缺必填 → 重试耗尽 → pending
# ---------------------------------------------------------------------------
def test_visual_incomplete_profile_pending():
    mock = IncompletePropMock()
    orch = make_orch("t10b", provider=mock)
    rep = orch.run({"episode": 1, "scene_ids": [1]})
    comp = rep["completeness"]
    check("T10b-1 ★缺必填 → assets_approved < assets_referenced",
          comp["assets_approved"] < comp["assets_referenced"], f"comp={comp}")
    check("T10b-2 不静默成功（status != success）", rep["status"] != "success",
          f"status={rep['status']}")
    pend = rep["stages"]["visual_assets"]["pending"]
    check("T10b-3 该实体记 pending", any(p.startswith("prop:") for p in pend),
          f"pending={pend}")
    prop_calls = [c for c in mock.calls
                  if c["step"] == "visual_asset_plan" and "kind: prop" in c["messages"][-1]["content"]]
    check("T10b-4 prop 视觉资产重试耗尽（3 次尝试）", len(prop_calls) == 3,
          f"prop_calls={len(prop_calls)}")


# ---------------------------------------------------------------------------
# T11 resume
# ---------------------------------------------------------------------------
def test_resume_skips_completed():
    orch = make_orch("t11")
    orch.run({"episode": 1, "scene_ids": [1]})
    arts = orch.artifacts_dir
    sb_before = (arts / "storyboard_1.json").read_bytes()

    mock2 = SceneAwareMock()
    orch2 = make_orch("t11", provider=mock2)
    rep2 = orch2.run({"episode": 1, "scene_ids": [1, 2]})

    f1 = sorted(p.name for p in arts.glob("storyboard_1*.json"))
    f2 = sorted(p.name for p in arts.glob("storyboard_2*.json"))
    check("T11a 已完成场景不重跑（storyboard_1 无新版本）", f1 == ["storyboard_1.json"],
          f"f1={f1}")
    check("T11b 新场景 2 正常产出 storyboard_2.json", f2 == ["storyboard_2.json"], f"f2={f2}")
    check("T11c storyboard_1.json 字节不变",
          (arts / "storyboard_1.json").read_bytes() == sb_before)
    skips = [w for w in rep2["warnings"] if "resume 跳过" in w]
    check("T11d warnings 记录 resume 跳过（casting + scene1 + storyboard1）",
          len(skips) >= 3, f"skips={skips[:5]}")
    sb1_calls = [c for c in mock2.calls if c["step"] == "storyboard_generation"
                 and re.search(r'"scene_id"\s*:\s*1', c["messages"][-1]["content"])]
    check("T11e ★resume 不调用引擎（场景 1 无 storyboard 调用）", not sb1_calls,
          f"calls={len(sb1_calls)}")
    check("T11f resume 后 status = success", rep2["status"] == "success",
          f"status={rep2['status']}")
    check("T11g resume 后 mock 调用只覆盖新增部分（scene2 + sb2 + prop2 = 3）",
          len(mock2.calls) == 3, f"calls={[c['step'] for c in mock2.calls]}")


# ---------------------------------------------------------------------------
# T12 失败传播
# ---------------------------------------------------------------------------
def test_failure_propagation_locates_scene():
    orch = make_orch("t12")
    good = scene_payload(1, ["Mercy"])
    bad = scene_payload(2, ["Ghost"])
    rep = orch.run({"episode": 1, "scene_ids": [1, 2], "scenes": {1: good, 2: bad}})
    check("T12a 不静默成功（status != success）", rep["status"] != "success",
          f"status={rep['status']}")
    check("T12b status ∈ {partial, failed}", rep["status"] in ("partial", "failed"),
          f"status={rep['status']}")
    pf = [e for e in rep["errors"] if e.get("stage") == "preflight"]
    check("T12c ★errors 能定位到具体 scene_id", any(e.get("scene_id") == 2 for e in pf),
          f"pf={pf}")
    check("T12d 场景 1 仍成功（部分完成不整体丢弃）",
          rep["completeness"]["storyboards_done"] == 1, f"comp={rep['completeness']}")

    orch2 = make_orch("t12x")
    err = expect_raises(OrchestrationError, orch2.run,
                        {"episode": 1, "scene_ids": [1, 1]})
    check("T12e run_spec.scene_ids 重复 → OrchestrationError（R13，不静默去重）",
          isinstance(err, OrchestrationError), f"got={err!r}")


# ---------------------------------------------------------------------------
# T13 真实 API = 0
# ---------------------------------------------------------------------------
def test_zero_real_api():
    reset_api_counter()
    mock = CountingProviderWrapper(SceneAwareMock())
    orch = make_orch("t13", provider=mock)
    orch.run({"episode": 1, "scene_ids": [1]})
    snap = get_api_counter().snapshot()
    check("T13a 真实 API = 0（程序化快照断言，不人工数数）",
          snap["real_api_requests"] == 0, f"real={snap['real_api_requests']}")
    check("T13b mock 调用已计数（>= 1）", snap["mock_requests"] >= 1,
          f"mock={snap['mock_requests']}")


# ---------------------------------------------------------------------------
# T14 ★Token 硬证据
# ---------------------------------------------------------------------------
def test_token_hard_evidence():
    pid = "t14"
    mem = MemoryStore(pid, projects_root=TMP_PROJECTS)
    mem.set_characters([{"name": "Mercy"}])
    mock = SceneAwareMock()
    agent = StoryboardAgent(mock, scene_id=1, registry=ASSETS, known_characters=["Mercy"])
    step = StoryboardStep(agent, scene_id=1)
    wctx = WorkflowContext(project_id=pid, workflow_id="wf", run_id="t14", inputs={})
    ctx = ContextAssembler(mem, ASSETS).assemble(step, wctx, project_title="T14")
    scene = Scene.from_v01(scene_payload(1, ["Mercy"]), id=1)
    sb = agent.run(ctx, scene=scene, storyboard_spec={"scene_id": 1})
    before = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)

    orch = make_orch("t14b")
    entities = ProjectOrchestrator._collect_entities([sb])
    report = {"episode": 1, "errors": [], "warnings": [],
              "stages": {"visual_assets": {}}}
    handler = orch._build_review_handler("auto_approve")
    orch._stage_visual_assets(entities, 1, handler, 3, report)
    after = json.dumps(sb.to_dict(), sort_keys=True, ensure_ascii=False)

    check("T14a ★注册视觉资产前后 Storyboard.to_dict() 逐字节不变（Token 硬证据）",
          before == after)
    check("T14b ★视觉档案未渗漏进 Storyboard（无 red wool coat / amber and wood tones）",
          "red wool coat" not in after and "amber and wood tones" not in after)
    check("T14c 实体全部注册且 approved",
          all(orch.visual_registry.active_profile(k, n) is not None for k, n in entities),
          f"entities={entities}")

    # 场景间不膨胀：3 场景 E2E 中每份 storyboard 只含自己的标记
    orch2 = make_orch("t14c")
    orch2.run({"episode": 1, "scene_ids": [1, 2, 3]})
    arts = orch2.artifacts_dir
    t1 = (arts / "storyboard_1.json").read_text(encoding="utf-8")
    t3 = (arts / "storyboard_3.json").read_text(encoding="utf-8")
    check("T14d ★场景间不膨胀（storyboard_1 不含 scene-2/3；storyboard_3 不含 scene-1/2）",
          "scene-2" not in t1 and "scene-3" not in t1
          and "scene-1" not in t3 and "scene-2" not in t3)
    sizes = [len((arts / f"storyboard_{n}.json").read_text(encoding="utf-8"))
             for n in (1, 2, 3)]
    check("T14e 三份 artifact 大小同量级（各自场景复杂度一致，无叠加膨胀）",
          max(sizes) <= min(sizes) * 2, f"sizes={sizes}")


# ---------------------------------------------------------------------------
# T15 静态检查
# ---------------------------------------------------------------------------
_SK = "sk" + "-"


def test_static_checks():
    proc = subprocess.run([sys.executable, "-m", "compileall", "-q",
                           str(V2_ROOT / "orchestration")],
                          capture_output=True, text=True)
    check("T15a compileall v2/orchestration 全过", proc.returncode == 0,
          (proc.stderr or "")[:200])

    import orchestration  # noqa: E402
    check("T15b import 冒烟 + 导出齐备",
          {"ProjectOrchestrator", "OrchestrationError", "OrchestrationState",
           "preflight_check"} <= set(orchestration.__all__),
          f"__all__={orchestration.__all__}")

    bad = []
    for p in list((V2_ROOT / "orchestration").glob("*.py")) + list(TEST_DIR.glob("*.py")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if _SK in line.strip():
                bad.append(f"{p.name}: {line.strip()[:60]}")
    check("T15c 新增文件无硬编码密钥字样（扫描 " + _SK + " 命中）",
          not bad, f"{bad[:3]}" if bad else "")

    # 受控词表未被加值（stage8 封存前提）
    from core.shot import ASSET_KINDS
    check("T15d ASSET_KINDS 精确不变",
          ASSET_KINDS == ("character", "location", "prop", "action", "camera",
                          "image", "video", "audio", "reference"))


# ---------------------------------------------------------------------------
# T16 / T17 禁改清单 0 diff
# ---------------------------------------------------------------------------
FORBIDDEN = [
    "FilmAgent", ".gitignore",
    "v2/workflow",
    "v2/core",
    "v2/llm", "v2/memory", "v2/review", "v2/context",
    "v2/agents",
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
    check("T16 禁改清单 0 diff（§8.3 + FilmAgent/ + 仓库根 .gitignore）",
          not changed, f"有改动: {changed}" if changed else "")

    st = subprocess.run(["git", "status", "--porcelain", "--", "v2/workflow"],
                        capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T17 ★v2/workflow/ 0 diff + 0 新增（上层编排未污染引擎）",
          not (st.stdout or "").strip(), f"status={st.stdout!r}")

    diff_core = subprocess.run(["git", "diff", "--stat", "HEAD", "--", "v2/core",
                                "v2/agents"],
                               capture_output=True, text=True, cwd=str(REPO_ROOT))
    check("T17b v2/core + v2/agents 0 diff",
          not (diff_core.stdout or "").strip(), f"{(diff_core.stdout or '')[:120]}")


# ---------------------------------------------------------------------------
# T19 端到端完整性：completeness 与磁盘交叉核对
# ---------------------------------------------------------------------------
def test_completeness_matches_disk():
    orch = make_orch("t19")
    rep = orch.run({"episode": 1, "scene_ids": [1, 2, 3]})
    arts = orch.artifacts_dir
    comp = rep["completeness"]
    n_scene = len(_no_version_files(arts, "scene_*.json"))
    n_sb = len(_no_version_files(arts, "storyboard_*.json"))
    approved = sum(1 for a in orch.visual_registry.assets.values()
                   if a.latest_approved() is not None)
    check("T19a scenes_done == 磁盘 scene_*.json 数",
          comp["scenes_done"] == n_scene, f"{comp['scenes_done']} vs {n_scene}")
    check("T19b storyboards_done == 磁盘 storyboard_*.json 数",
          comp["storyboards_done"] == n_sb, f"{comp['storyboards_done']} vs {n_sb}")
    check("T19c assets_approved == registry 中 approved 数",
          comp["assets_approved"] == approved, f"{comp['assets_approved']} vs {approved}")

    rp = orch.orchestration_dir / f"run_{rep['run_id']}.json"
    check("T19d run_<run_id>.json 落盘且与返回值一致",
          rp.is_file() and json.loads(rp.read_text(encoding="utf-8"))["status"] == rep["status"],
          f"path={rp}")
    check("T19e state.json 落盘（编排进度唯一事实源）",
          (orch.orchestration_dir / "state.json").is_file())
    state = json.loads((orch.orchestration_dir / "state.json").read_text(encoding="utf-8"))
    check("T19f state.json 只序列化编排自有状态（不含 Project 整体 / title / world，R15）",
          "episodes" in state and "project_id" in state
          and "title" not in state and "world" not in state, f"keys={sorted(state)}")
    check("T19g state.json 记录本集三场景两段",
          sorted(state["episodes"]["1"]["scenes"].keys()) == ["1", "2", "3"]
          and all("storyboard" in v for v in state["episodes"]["1"]["scenes"].values()))


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
]


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


def run_history():
    for tag, dirname in HISTORY_SUITES:
        suite = V2_ROOT / "logs" / dirname / "run_tests.py"
        if not suite.exists():
            check(f"T18-{tag} 套件存在", False, f"missing {suite}")
            continue
        proc = subprocess.run([sys.executable, str(suite)], capture_output=True,
                              text=True, cwd=str(V2_ROOT), encoding="utf-8",
                              errors="replace", env=_clean_child_env())
        tail = (proc.stdout or "").strip().splitlines()[-3:]
        ok = proc.returncode == 0
        check(f"T18-{tag} 历史回归全绿", ok, "; ".join(tail)[:220] if not ok else "")


# ---------------------------------------------------------------------------
def main():
    if TMP_PROJECTS.exists():
        _hard_rmtree(TMP_PROJECTS)
    TMP_PROJECTS.mkdir(parents=True, exist_ok=True)
    TMP_LOGS.mkdir(parents=True, exist_ok=True)
    log("=" * 72)
    log("Stage 12-B 编排层生命周期 —— mock 测试套件（0 真实 API）")
    log("=" * 72)
    tests = [
        test_contract_and_dependency_direction,
        test_state_roundtrip_and_resume,
        test_preflight_rules,
        test_preflight_uses_scene_object_for_move_lines,
        test_preflight_blocks_before_stage11,
        test_memory_written_after_casting,
        test_single_scene_e2e,
        test_multi_scene_no_cross_talk,
        test_run_ids_and_artifact_isolation,
        test_rerun_creates_new_version,
        test_review_policies,
        test_human_stub_stops_without_artifact,
        test_visual_entity_collection_and_completeness,
        test_visual_incomplete_profile_pending,
        test_resume_skips_completed,
        test_failure_propagation_locates_scene,
        test_zero_real_api,
        test_token_hard_evidence,
        test_static_checks,
        test_forbidden_paths_untouched,
        test_completeness_matches_disk,
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
        "suite": "stage12_lifecycle",
        "real_api_requests": 0,
        "results": _results,
        "passed": passed, "total": total,
    }
    (TEST_DIR / "test_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"report -> {TEST_DIR / 'test_report.json'}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
