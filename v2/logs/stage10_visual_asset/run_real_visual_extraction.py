# -*- coding: utf-8 -*-
"""
V0.2 Stage 10-B —— 真实视觉冒烟脚本（run_real_visual_extraction.py）

设计约束（stage10_plan §25 + Stage 10-B 补充指令 §15 硬性验收）：
    1. 计数器 + 预算保护 + Mock 测试已完成（run_tests.py 62/62 全绿），
       本脚本才被允许发起真实 Vision 请求（补充指令收尾要求）。
    2. 真实 Vision 请求硬上限 = 3（APIRequestCounter 程序化强制，retry 也计数）；
       第 4 次请求会被立即拒绝——不是靠人自觉，是程序拒绝。
    3. 前置条件四项齐备才执行：API key / 模型名 / 参考图 / 人工批准（--yes）。
       任何一项缺失 → 打印"阻塞：需人工配置"并退出，绝不伪造真实结果。
    4. 全程文本 API = 0 次（规范化是确定性代码，不经文本模型）。
    5. 结束时输出程序化计数报告（real / mock / retries / budget / 超限与否 /
       三次请求与样例的对应），全部来自 APIRequestCounter.snapshot()，
       绝不人工数数；token/cost 走 TokenLogger（与计数分开）。

三个样例（恰好 3 次真实视觉调用）：
    #1 人物参考图 A + 语义锚点 → extract → CHAR_001:v1（审批后生效）
    #2 场景参考图   + 地点名   → extract → LOC_001:v1
    #3 人物参考图 B（同角色不同造型）→ extract → CHAR_001:v2（v1 转 superseded）

用法：
    python run_real_visual_extraction.py --check            # 只做前置检查（0 调用）
    python run_real_visual_extraction.py --yes              # 正式执行（计费，≤3 次）
可选：
    --project <dir>          项目目录（默认本目录下 real_project/）
    --image-character-a <p>  人物参考图 A（默认项目 images/character_a.png，
                             缺失时回退仓库 pics/*.png——报告中明示"仅管线验证"）
    --image-character-b <p>  人物参考图 B（同上 character_b.png）
    --image-location <p>     场景参考图（同上 location.png）
    --model <name>           VLM 模型名（缺省读 config bailian_vision.default_model）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]              # v2/
TEST_DIR = Path(__file__).resolve().parent                 # v2/logs/stage10_visual_asset/
REPO_ROOT = Path(__file__).resolve().parents[3]            # git 根

sys.path.insert(0, str(V2_ROOT))

import config                                              # noqa: E402
from core.token_log import TokenLogger                     # noqa: E402
from core.visual_registry import VisualAssetRegistry       # noqa: E402
from llm.request_counter import (                          # noqa: E402
    MAX_REAL_VISION_REQUESTS, APIRequestCounter, APIBudgetExceededError,
)
from llm.providers.openai_compatible_vision import (       # noqa: E402
    OpenAICompatibleVisionProvider,
)
from agents.visual_asset import VisualAssetAgent           # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 语义锚点（真实项目应来自 Casting/Memory；冒烟脚本用最小事实，不调文本 API）
SEMANTIC = {
    "character": {"name": "Mercy", "age": "28", "gender": "female",
                  "occupation": "ER nurse", "note": "tired but composed"},
    "location": {"name": "City Cafe", "note": "where the friends reunion begins"},
}


def die(msg: str, code: int = 2) -> None:
    print(f"\n[阻塞：需人工配置] {msg}")
    print("真实 Vision 调用未执行（0 次请求）。绝不伪造真实结果。")
    sys.exit(code)


def preflight(args) -> dict:
    """四项前置检查（0 调用）。全部通过返回 wiring 参数。"""
    print("=" * 72)
    print("Stage 10-B 真实视觉冒烟 —— 前置检查（0 API 调用）")
    print("=" * 72)

    # 1) API key
    key = config.load_provider_api_key("bailian")
    print(f"[1/4] BAILIAN_API_KEY: {'已配置 (' + config.mask_api_key(key) + ')' if key else '未配置'}")
    if not key:
        die("环境变量 BAILIAN_API_KEY 未配置。")

    # 2) 模型名（Config 档优先，--model 覆盖；两者都空 = 阻塞）
    conf = config.get_provider_config("bailian_vision")
    model = str(args.model or conf.get("default_model", "") or "").strip()
    print(f"[2/4] VLM 模型: {model or '(未配置)'}"
          + ("" if args.model or conf.get("default_model") else "  <- config bailian_vision.default_model 为空"))
    if not model:
        die("未指定 VLM 模型名：请在 config.PROVIDERS['bailian_vision']['default_model'] "
            "填入真实型号（如 qwen-vl 系列具体型号），或用 --model 传入。")

    # 3) 参考图（项目 images/ 优先；缺失回退仓库 pics/*.png 并明示"仅管线验证"）
    project = Path(args.project) if args.project else TEST_DIR / "real_project"
    images_dir = project / "visual_assets" / "images"
    fallback = REPO_ROOT / "pics"
    picks = {
        "character_a": args.image_character_a,
        "character_b": args.image_character_b,
        "location": args.image_location,
    }
    resolved, notes = {}, []
    for slot, given in picks.items():
        if given:
            p = Path(given)
            if not p.is_file():
                die(f"--image {slot}: 文件不存在 {p}")
            resolved[slot] = p
            continue
        cand = images_dir / f"{slot}.png"
        if cand.is_file():
            resolved[slot] = cand
            continue
        fb = sorted(fallback.glob("*.png"))
        if fb:
            resolved[slot] = fb[hash(slot) % len(fb)]
            notes.append(f"{slot}: 语义无效，仅管线验证（回退 {resolved[slot].name}）")
    print(f"[3/4] 参考图: " + ", ".join(f"{k}={v.name}" for k, v in resolved.items()))
    for n in notes:
        print(f"      注意: {n}")
    if len(resolved) < 3:
        die("参考图不足 3 张（character_a / character_b / location）。请放入 "
            f"{images_dir} 或用 --image-* 指定。")

    # 4) 人工批准
    print(f"[4/4] 人工批准: {'--yes 已给出' if args.yes else '未给出'}")
    if not args.yes:
        die("未获得人工批准：真实调用计费，请加 --yes 显式批准后重跑。")

    print("前置检查全部通过。\n")
    return {"project": project, "key": key, "base_url": conf["base_url"],
            "model": model, "images": resolved, "fallback_notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage 10-B 真实视觉冒烟（≤3 次真实 Vision 请求）")
    ap.add_argument("--check", action="store_true", help="只做前置检查（0 调用）")
    ap.add_argument("--yes", action="store_true", help="人工批准真实计费调用")
    ap.add_argument("--project", default="", help="项目目录（默认 real_project/）")
    ap.add_argument("--image-character-a", default="")
    ap.add_argument("--image-character-b", default="")
    ap.add_argument("--image-location", default="")
    ap.add_argument("--model", default="", help="VLM 模型名（覆盖 Config）")
    args = ap.parse_args()

    wiring = preflight(args)
    if args.check:
        print("--check：仅前置检查，退出（0 真实请求）。")
        return 0

    project: Path = wiring["project"]
    counter = APIRequestCounter()                    # vision 预算 = MAX_REAL_VISION_REQUESTS(3)
    logger = TokenLogger(TEST_DIR, filename="real_vision_calls.jsonl")
    provider = OpenAICompatibleVisionProvider(
        api_key=wiring["key"], base_url=wiring["base_url"], model=wiring["model"],
        vendor="bailian", counter=counter, logger=logger,
        timeout=120.0, max_retries=1, retry_backoff_seconds=1.0,
    )
    agent = VisualAssetAgent(llm_client=None, vision=provider)
    reg = VisualAssetRegistry(project)

    print("=" * 72)
    print(f"执行 3 个样例（预算上限 {MAX_REAL_VISION_REQUESTS}，程序化强制）")
    print("=" * 72)
    samples = [
        ("character", "Mercy", "character_a", "人物参考图 A → CHAR v1"),
        ("location", "City Cafe", "location", "场景参考图 → LOC v1"),
        ("character", "Mercy", "character_b", "人物参考图 B → CHAR v2（版本机制）"),
    ]
    results = []
    for i, (kind, name, slot, label) in enumerate(samples, 1):
        real_before = counter.real_requests("vision")
        remaining = counter.remaining_budget("vision")
        print(f"\n--- 样例 #{i}: {label}（真实请求 {real_before}，剩余预算 {remaining}）")
        t0 = time.monotonic()
        try:
            snap = agent.run_extract(kind=kind, name=name, image=str(wiring["images"][slot]),
                                     semantic_data=SEMANTIC.get(kind))
        except APIBudgetExceededError as e:
            print(f"    预算拒绝（程序强制）: {e}")
            results.append({"sample": i, "label": label, "status": "budget_rejected",
                            "requests_used": counter.real_requests("vision")})
            break
        except Exception as e:
            print(f"    调用失败: {type(e).__name__}: {e}")
            results.append({"sample": i, "label": label, "status": f"error: {e}",
                            "requests_used": counter.real_requests("vision")})
            break
        dur = int((time.monotonic() - t0) * 1000)
        used = counter.real_requests("vision") - real_before

        # 确定性写回 registry（0 文本 API；审批在真实流程走 Review，这里直接批准并注明）
        if reg.find(kind, name) is None:
            reg.register(kind, name)
        asset = reg.find(kind, name)
        ver = reg.new_version(asset.asset_id, profile=snap["profile"],
                              source=snap["source"], visual_prompt=snap["visual_prompt"],
                              notes=f"real smoke sample #{i}")
        reg.submit_for_review(asset.asset_id, ver.version)
        reg.approve(asset.asset_id, ver.version)
        active = reg.active_version(asset.asset_id)
        print(f"    提取完成: {asset.asset_id}:{ver.version} (approved), "
              f"complete={snap['complete']}, 用时 {dur}ms")
        print(f"    profile: {json.dumps(snap['profile'], ensure_ascii=False)[:220]}")
        results.append({
            "sample": i, "label": label, "status": "ok",
            "asset": asset.asset_id, "version": ver.version,
            "requests_used_this_sample": used,
            "business_request_id": provider._business_request_id,
            "active_version": active.version if active else None,
            "complete": snap["complete"],
            "profile": snap["profile"],
        })

    # ------------------------------------------------------------------
    # 程序化计数报告（绝不人工数数）
    # ------------------------------------------------------------------
    snap = counter.snapshot()
    budget_exceeded = snap["budget_rejections"] > 0
    v1_status = None
    char = reg.find("character", "Mercy")
    if char is not None:
        v1 = char.get_version("v1")
        v1_status = v1.status if v1 else None

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": wiring["model"],
        "vendor": "bailian",
        "api_key": config.mask_api_key(wiring["key"]),
        "counter_snapshot": {k: v for k, v in snap.items() if k != "audit"},
        "audit_records": snap["audit"],
        "samples": results,
        "fallback_notes": wiring["fallback_notes"],
        "registry_summary": reg.summary(),
        "char_v1_status_after_v2": v1_status,
        "token_cost_log": str(TEST_DIR / "real_vision_calls.jsonl"),
    }
    report_path = TEST_DIR / "real_run_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    print("\n" + "=" * 72)
    print("程序化计数报告（APIRequestCounter.snapshot()，非人工统计）")
    print("=" * 72)
    print(f"Real API requests         : {snap['real_api_requests']}")
    print(f"  - by kind               : { {k: v['real'] for k, v in snap['by_kind'].items()} }")
    print(f"Mock requests             : {snap['mock_requests']}")
    print(f"Logical calls             : {snap['logical_calls']}")
    print(f"Retries                   : {snap['retries']}")
    print(f"Maximum real Vision budget: {MAX_REAL_VISION_REQUESTS}")
    print(f"Budget exceeded           : {'YES' if budget_exceeded else 'NO'}"
          f"（被拒绝 {snap['budget_rejections']} 次）")
    for r in results:
        if r["status"] == "ok":
            print(f"  Request(s) for sample #{r['sample']} ({r['label']}): "
                  f"{r['requests_used_this_sample']} 次 -> {r['asset']}:{r['version']} "
                  f"[active={r['active_version']}]")
        else:
            print(f"  sample #{r['sample']}: {r['status']}")
    if v1_status:
        print(f"版本机制验证: CHAR_001:v1 状态 = {v1_status}（v2 批准后应 superseded）")
    print(f"\n报告: {report_path}")
    print(f"token/cost: {report['token_cost_log']}（与请求计数分开记账）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
