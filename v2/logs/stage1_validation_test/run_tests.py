# -*- coding: utf-8 -*-
"""
V0.2 阶段 1 —— 纯代码测试（零 LLM）

覆盖内容（对应开工指令 §22/§23）：
    T1  合法地点
    T2  非法地点（必须报 INVALID_LOCATION，不能抛 FileNotFoundError）
    T3  非法动作（含编号变体剥离）
    T4  非法镜头（含编号变体剥离）
    T5  非法站位点
    T6  非法角色
    T7  script/0.json 双向转换 Round-trip（重点验收）
    T8  V0.1 字段名变体兼容（scenes_2 / scenes_8）
    T9  真实数据 state 覆盖检查
    T10 重试不覆盖轨迹（WorkflowStepState）
    T11 actors_profile 角色双向转换
    T12 真实最终剧本整体校验应 0 错误

数据来源：只读真实 V0.1 文件 + 内存中构造非法数据。
测试输出只写本目录（v2/logs/stage1_validation_test/）。
不修改 V0.1 任何文件；不调用任何 LLM。
"""
import sys
import traceback
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]        # v2/
V01_ROOT = V2_ROOT.parent / "FilmAgent"              # V0.1 核心目录
TEST_DIR = Path(__file__).resolve().parent           # 本目录

sys.path.insert(0, str(V2_ROOT))
from core.io import read_json, write_json, ensure_dir  # noqa: E402
from core.models import (                              # noqa: E402
    Scene, SceneInfo, Line, Action, Move, PositionRef,
    from_v01_script, to_v01_script, from_v01_actors, to_v01_actors,
    WorkflowStepState, WorkflowState,
)
from core.assets import AssetRegistry, base_name        # noqa: E402
from core.validator import (                            # noqa: E402
    Validator, ValidationError, format_errors, VALID_STATES,
)

# 让控制台输出用 UTF-8（Git Bash 可能显示乱码，但写入的文件不受影响）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------------------
# 准备：资产注册表 + 角色清单
# ---------------------------------------------------------------------------
REGISTRY = AssetRegistry.load(V2_ROOT / "assets" / "locations")
CHARACTERS = from_v01_actors(read_json(V01_ROOT / "Script" / "actors_profile.json"))
CHAR_NAMES = [c.name for c in CHARACTERS]

_results: list = []          # 每一项: {"name", "ok", "detail"}
_log_lines: list = []


def log(msg: str = "") -> None:
    _log_lines.append(str(msg))


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append({"name": name, "ok": bool(ok), "detail": detail})
    log(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


def build_scene(scene_id: int, where: str, *,
                who=None, what="...",
                lines=None, initial_positions=None) -> Scene:
    """构造测试用 Scene（默认空行、空初始站位）。"""
    return Scene(
        id=scene_id,
        info=SceneInfo(who=who or [], where=where, what=what),
        lines=lines or [],
        initial_positions=initial_positions or [],
    )


# ---------------------------------------------------------------------------
# T1 合法地点
# ---------------------------------------------------------------------------
def test_legal_location() -> None:
    scene = build_scene(
        0, "Apartment living room", who=["Theo"],
        lines=[Line(type="dialogue", speaker="Theo", content="Hello.",
                    actions=[Action(character="Theo", state="standing", action="Standing Talking")],
                    shot="Close Shot",
                    positions=[PositionRef("Theo", "Position 1")])],
        initial_positions=[PositionRef("Theo", "Position 1")],
    )
    errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    check("T1 合法地点", not errors, f"errors={format_errors(errors)}")


# ---------------------------------------------------------------------------
# T2 非法地点（必须 INVALID_LOCATION，不能 FileNotFoundError）
# ---------------------------------------------------------------------------
def test_invalid_location() -> None:
    scene = build_scene(1, "Billiard room kitchenette", who=["Theo"])
    try:
        errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    except FileNotFoundError as exc:  # 明确失败：绝不抛出 FileNotFoundError
        check("T2 非法地点", False, f"抛出了 FileNotFoundError: {exc}")
        return
    codes = [e.code for e in errors]
    ok = "INVALID_LOCATION" in codes and len(errors) == 1
    check("T2 非法地点", ok,
          f"INVALID_LOCATION 命中={('INVALID_LOCATION' in codes)}; 错误总数={len(errors)}")


# ---------------------------------------------------------------------------
# T3 非法动作 + 编号变体剥离
# ---------------------------------------------------------------------------
def test_invalid_action() -> None:
    # 编号变体必须能匹配基础名
    check("T3 编号变体剥离", REGISTRY.has_action("Standing Talking 5"),
          "has_action('Standing Talking 5') 应为 True")
    check("T3 编号变体剥离-镜头", REGISTRY.has_shot("Long Shot 2"),
          "has_shot('Long Shot 2') 应为 True")
    # 非法动作
    scene = build_scene(
        2, "Apartment living room", who=["Theo"],
        lines=[Line(type="dialogue", speaker="Theo", content="x",
                    actions=[Action(character="Theo", state="standing", action="Totally Fake Action")],
                    shot="Close Shot")],
    )
    errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    codes = [e.code for e in errors]
    check("T3 非法动作", "INVALID_ACTION" in codes,
          f"INVALID_ACTION 命中={'INVALID_ACTION' in codes}")


# ---------------------------------------------------------------------------
# T4 非法镜头
# ---------------------------------------------------------------------------
def test_invalid_shot() -> None:
    scene = build_scene(
        3, "Apartment living room", who=["Theo"],
        lines=[Line(type="dialogue", speaker="Theo", content="x",
                    actions=[Action(character="Theo", state="standing", action="Standing Talking")],
                    shot="Fake Shot 999")],
    )
    errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    codes = [e.code for e in errors]
    check("T4 非法镜头", "INVALID_SHOT" in codes,
          f"INVALID_SHOT 命中={'INVALID_SHOT' in codes}")


# ---------------------------------------------------------------------------
# T5 非法站位点
# ---------------------------------------------------------------------------
def test_invalid_position() -> None:
    # 另一个地点存在的 Position（Billiard room 没有 Position 5 可用来对比）
    scene = build_scene(
        4, "Billiard room", who=["Theo"],
        initial_positions=[PositionRef("Theo", "Position 999")],
    )
    errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    codes = [e.code for e in errors]
    check("T5 非法站位点", "INVALID_POSITION" in codes,
          f"INVALID_POSITION 命中={'INVALID_POSITION' in codes}; errors={format_errors(errors)}")
    # 走位目标位置也查
    scene2 = build_scene(
        5, "Apartment living room", who=["Theo"],
        lines=[Line(type="move", move=Move(character="Theo", destination="Position 999"),
                    shot="Follow Shot", positions=[PositionRef("Theo", "Position 1")])],
    )
    errors2 = Validator(REGISTRY, CHAR_NAMES).validate(scene2)
    check("T5 走位目标非法", any(e.code == "INVALID_POSITION" and "move.destination" in e.path
                                 for e in errors2),
          "move.destination 的非法位置应被拦截")


# ---------------------------------------------------------------------------
# T6 非法角色
# ---------------------------------------------------------------------------
def test_invalid_character() -> None:
    scene = build_scene(
        6, "Apartment living room",
        who=["Theo", "Stranger"],
        lines=[Line(type="dialogue", speaker="Unknown Character", content="x",
                    actions=[Action(character="Theo", state="standing", action="Standing Talking")])],
    )
    errors = Validator(REGISTRY, CHAR_NAMES).validate(scene)
    codes = [e.code for e in errors]
    check("T6 非法角色(speaker)", "UNKNOWN_CHARACTER" in codes,
          f"UNKNOWN_CHARACTER 命中={'UNKNOWN_CHARACTER' in codes}")
    check("T6 非法角色(who)", any(e.code == "UNKNOWN_CHARACTER" and "info.who" in e.path
                                  for e in errors),
          "who 里的未知角色也应被拦截")


# ---------------------------------------------------------------------------
# T7 script/0.json Round-trip（重点验收）
# ---------------------------------------------------------------------------
def test_roundtrip_final_script() -> None:
    src_path = V01_ROOT / "Script" / "script" / "0.json"
    original = read_json(src_path)
    scenes = from_v01_script(original)
    out = to_v01_script(scenes)

    deep_equal = (out == original)
    check("T7 双向转换后与原数据完全相等", deep_equal,
          f"共 {len(original)} 个场景；deep-equal={'相等' if deep_equal else '不相等'}")

    # 逐字段清点（证明核心信息都保留）
    n_lines = sum(len(s.lines) for s in scenes)
    n_dialogue = sum(1 for s in scenes for l in s.lines if l.type == "dialogue")
    n_move = sum(1 for s in scenes for l in s.lines if l.type == "move")
    n_actions = sum(len(l.actions) for s in scenes for l in s.lines)
    n_shots = sum(1 for s in scenes for l in s.lines if l.shot)
    n_positions = sum(len(l.positions) for s in scenes for l in s.lines)
    n_init = sum(len(s.initial_positions) for s in scenes)

    ok_fields = all([
        all(s.info.who and s.info.where and s.info.what for s in scenes),   # who/where/what
        all(l.speaker and l.content for s in scenes for l in s.lines if l.type == "dialogue"),
        n_shots == n_lines,                                                 # 每行都有镜头
        all(l.positions for s in scenes for l in s.lines),                  # 每行都有非空当前位置
        n_init >= len(scenes),                                              # 每场都有初始站位
    ])
    check("T7 核心字段完整性", ok_fields,
          f"场景={len(scenes)} 行={n_lines}(台词{n_dialogue}/走位{n_move}) "
          f"动作={n_actions} 镜头={n_shots} 位置={n_positions} 初始站位={n_init}")

    # 用 Validator 校验真实最终剧本：应当是干净的（0 错误）
    real_errors = Validator(REGISTRY, CHAR_NAMES).validate_many(scenes)
    check("T7 真实最终剧本整体校验", not real_errors,
          f"errors={format_errors(real_errors)}" if real_errors else "0 errors")


# ---------------------------------------------------------------------------
# T8 字段名变体兼容（scenes_2 / scenes_8）
# ---------------------------------------------------------------------------
def test_field_variants() -> None:
    # scenes_2：scene_information + dialogues（只有台词，无动作/镜头）
    d2 = read_json(V01_ROOT / "Script" / "scenes_2.json")
    s2 = Scene.from_v01(d2[0], id=0)
    ok_2 = (s2.info.where == d2[0]["scene_information"]["where"]
            and s2.lines[0].speaker == d2[0]["dialogues"][0]["speaker"]
            and s2.lines[0].content == d2[0]["dialogues"][0]["content"])
    check("T8 scenes_2 变体(scene_information/dialogues)", ok_2,
          f"where={s2.info.where!r}; 首行 speaker={s2.lines[0].speaker!r}")

    # scenes_8：sceneinformation + selected shot + 动作带 reason + 走位带 insertion
    d8 = read_json(V01_ROOT / "Script" / "scenes_8.json")
    s8 = Scene.from_v01(d8[0], id=0)
    first_action = s8.lines[0].actions[0]
    move_lines = [l for l in s8.lines if l.type == "move"]
    ok_8 = True
    ok_8 &= (s8.info.who == d8[0]["sceneinformation"]["who"])
    ok_8 &= (s8.lines[0].shot == d8[0]["dialogues"][0]["selected shot"])
    ok_8 &= (first_action.reason == d8[0]["dialogues"][0]["actions"][0]["reason"])
    if move_lines:
        ml = move_lines[0]
        orig_move = [l for l in d8[0]["dialogues"] if "move" in l][0]
        ok_8 &= (ml.insertion_reason == orig_move["insertion"]["insertion reason"])
        ok_8 &= (ml.insertion_position == orig_move["insertion"]["insertion position"])
        ok_8 &= (ml.reason == orig_move.get("reason", ""))
    check("T8 scenes_8 变体(sceneinformation/selected shot/reason/insertion)", ok_8,
          f"who 一致={s8.info.who == d8[0]['sceneinformation']['who']}; "
          f"动作 reason 保留={bool(first_action.reason)}")


# ---------------------------------------------------------------------------
# T9 真实数据 state 覆盖检查
# ---------------------------------------------------------------------------
def test_state_coverage() -> None:
    states = set()
    for fn in ["Script/scenes_8.json", "Script/script/0.json"]:
        for scene in read_json(V01_ROOT / fn):
            for line in scene.get("scene", scene.get("dialogues", [])):
                for act in line.get("actions", []):
                    states.add(act["state"])
    unknown = states - set(VALID_STATES)
    check("T9 真实数据 state ⊆ 合法集合", not unknown,
          f"实测状态={sorted(states)}; 不在合法集合的={sorted(unknown)}")


# ---------------------------------------------------------------------------
# T10 重试不覆盖轨迹（决策②）
# ---------------------------------------------------------------------------
def test_retry_keeps_trajectory() -> None:
    st = WorkflowStepState(step="lines_generate", status="running")
    st.record_attempt("scenes_5.json")       # 第一次输出
    st.record_attempt("scenes_5_v2.json")    # 重试
    st.record_attempt("scenes_5_v3.json")    # 再次重试
    st.approve("scenes_5_v3.json")
    ok = (st.attempts == ["scenes_5.json", "scenes_5_v2.json", "scenes_5_v3.json"]
          and st.approved_output == "scenes_5_v3.json"
          and st.status == "passed")
    check("T10 重试不覆盖轨迹", ok,
          f"attempts={st.attempts}; approved_output={st.approved_output}; "
          f"历史版本未删除={'scenes_5.json' in st.attempts and 'scenes_5_v2.json' in st.attempts}")
    ws = WorkflowState(version="1", steps=[st])
    check("T10 WorkflowState 可查到步骤", ws.step("lines_generate") is st, "step() 命中")


# ---------------------------------------------------------------------------
# T11 actors_profile 角色双向转换
# ---------------------------------------------------------------------------
def test_actors_roundtrip() -> None:
    original = read_json(V01_ROOT / "Script" / "actors_profile.json")
    chars = from_v01_actors(original)
    out = to_v01_actors(chars)
    check("T11 角色清单 round-trip", out == original,
          f"角色数={len(chars)}; deep-equal={'相等' if out == original else '不相等'}")


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def main() -> int:
    ensure_dir(TEST_DIR)
    log("=" * 64)
    log("V0.2 阶段 1 —— 纯代码测试（零 LLM）")
    log("=" * 64)
    log(f"资产注册表: {REGISTRY}")
    log(f"角色清单  : {CHAR_NAMES}")
    log("")

    tests = [
        ("T1 合法地点", test_legal_location),
        ("T2 非法地点", test_invalid_location),
        ("T3 非法动作", test_invalid_action),
        ("T4 非法镜头", test_invalid_shot),
        ("T5 非法站位点", test_invalid_position),
        ("T6 非法角色", test_invalid_character),
        ("T7 script/0.json Round-trip", test_roundtrip_final_script),
        ("T8 字段名变体兼容", test_field_variants),
        ("T9 state 覆盖", test_state_coverage),
        ("T10 重试不覆盖轨迹", test_retry_keeps_trajectory),
        ("T11 角色清单 round-trip", test_actors_roundtrip),
    ]
    for name, fn in tests:
        log(f"--- {name} ---")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 —— 测试框架兜底，逐条报错而不是整体崩溃
            tb = traceback.format_exc()
            check(name, False, f"测试抛异常: {exc}\n{tb}")
            log(tb)
        log("")

    # ---- 汇总 ----
    total = len(_results)
    passed = sum(1 for r in _results if r["ok"])
    log("=" * 64)
    log(f"测试结果：{passed}/{total} 通过")
    for r in _results:
        if not r["ok"]:
            log(f"  FAIL: {r['name']} -> {r['detail']}")
    log("LLM calls = 0（本阶段没有任何 LLM 调用）")

    # 写报告
    report = {
        "stage": "stage1_validation_test",
        "total": total,
        "passed": passed,
        "llm_calls": 0,
        "results": _results,
    }
    write_json(TEST_DIR / "stage1_report.json", report)
    (TEST_DIR / "stage1_console.txt").write_text("\n".join(_log_lines), encoding="utf-8")
    print("\n".join(_log_lines))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
