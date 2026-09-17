# -*- coding: utf-8 -*-
"""
V0.2 Stage 13 —— ★真实出图冒烟脚本（v2/logs/stage13_image/run_real_image.py）

    ⚠️ 本脚本会**产生真实费用**。默认什么都不做，只打印"能否执行"的体检报告。

用法（三步，缺一不可）：

    # ① 只体检（默认；0 真实调用）：看配置是否齐备、计划几次调用、成本粗估
    env -u PYTHONPATH python v2/logs/stage13_image/run_real_image.py

    # ② 明确批准执行（计费）
    env -u PYTHONPATH python v2/logs/stage13_image/run_real_image.py \
        --approve --text-vendor deepseek --image-model <你的图像模型名>

    # ③ 若图像单价未登记在 config.IMAGE_PRICING，需额外显式接受"成本未知"
    ... --accept-unknown-cost

四项人工前置（§13，缺任何一项都**明确报告阻塞**，绝不伪造）：

    ① `config.PROVIDERS["bailian_image"]["default_model"]` 已填（或 `--image-model` 显式传入）
    ② 环境变量 `BAILIAN_API_KEY` 已注入（**不落盘、不进日志**）
    ③ 明确批准（`--approve`）
    ④ 确认预算上限（`--max-images`，缺省 3；同时受 MAX_REAL_IMAGE_REQUESTS 硬上限约束）

本脚本覆盖**一条完整真实链**（并入 Stage 12 遗留的 run_real_lifecycle，§7.7）：
    Casting → Scene → Storyboard → 视觉档案（含项目级 STYLE）→ 出图 ≤3 张

成本纪律（§十三 / §7.3）：
    - 全程上限：真实图像 ≤ `--max-images`（缺省 3，且 ≤ MAX_REAL_IMAGE_REQUESTS）；
                真实文本 ≤ `--max-text-calls`（缺省 5）。
    - 文本侧**关闭重试**（max_retries=0），保证"真实文本调用数 == 计划数"，不被重试放大。
    - 预检发现计划调用数 > 上限 → **拒绝执行**（不静默超支）。
    - 未登记图像单价 → 需 `--accept-unknown-cost` 才继续；报告里 estimated_cost 记 null。
    - **绝不用占位图 / 纯色块 / mock 结果冒充真实出图**（铁律 11）。

产出（结构化证据，不含图片本体）：
    v2/logs/stage13_image/real_run/real_run_report.json   实际张数 / 成本 / 耗时 / 成功率
    v2/logs/stage13_image/real_run/manifest_copy.json     manifest 副本（sha256 溯源锚点）
    v2/logs/stage13_image/real_run/real_llm_calls.jsonl   文本侧真实记账
    v2/logs/stage13_image/real_run/image_generation_calls.jsonl  图像侧真实记账
    （以上均被 v2/.gitignore 覆盖为运行产物，不进版本库）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(V2_ROOT))

from config import (                                     # noqa: E402
    PROVIDERS, PRICING, get_image_pricing, estimate_image_cost,
    load_provider_api_key, mask_api_key, FILMAGENT_PROVIDER,
)
from core.models import Episode, Project, World                          # noqa: E402
from core.token_log import TokenLogger                                   # noqa: E402
from llm.request_counter import (                                        # noqa: E402
    MAX_REAL_IMAGE_REQUESTS, APIRequestCounter, set_api_counter,
)
from llm.providers.registry import create_provider                       # noqa: E402
from orchestration import ProjectOrchestrator                            # noqa: E402
from orchestration.image_stage import GENERATED_DIR_NAME, MANIFEST_FILENAME  # noqa: E402

TEXT_CAP_DEFAULT = 5
IMAGE_CAP_DEFAULT = min(3, MAX_REAL_IMAGE_REQUESTS)
RUN_DIR = V2_ROOT / "logs" / "stage13_image" / "real_run"
REAL_LLM_LEDGER = "real_llm_calls.jsonl"
IMAGE_LEDGER = "image_generation_calls.jsonl"

#: 粗估用：单次文本调用的 token 量级（保守偏大；真账以平台为准）
_EST_IN_TOKENS = 2500
_EST_OUT_TOKENS = 1000


# ---------------------------------------------------------------------------
# 计划（确定性输入；真实链的"最小可跑"配置）
# ---------------------------------------------------------------------------
def build_plan(args) -> dict:
    """算出"这一次真实冒烟要跑什么、要调几次、大概多少钱"（不执行任何调用）。"""
    style_spec = {}
    if args.style_medium or args.style_palette:
        style_spec = {"medium": args.style_medium or "35mm film",
                      "palette": args.style_palette or "warm amber",
                      "rendering": args.style_rendering or ""}
    scene_ids = list(range(1, int(args.scenes) + 1))
    # 文本调用计划：casting 1 次 + 每场景 (scene 1 + storyboard 1) +
    #               每实体视觉档案 1 次（character/location/prop 各 1 + 可选 style 1）
    entities = 3 + (1 if style_spec else 0)
    text_planned = 1 + 2 * len(scene_ids) + entities
    # 出图计划：每场景 1 个镜头（只取每场景首个 Shot，控制成本）
    images_planned = min(len(scene_ids), args.max_images)
    return {
        "scene_ids": scene_ids,
        "style_spec": style_spec,
        "text_calls_planned": text_planned,
        "images_planned": images_planned,
        "character": args.character,
        "location": args.location,
        "title": args.title,
    }


def estimate_costs(plan: dict, args) -> dict:
    """成本粗估（文本按 PRICING，图像按 IMAGE_PRICING；未登记一律记 unknown）。"""
    out: dict = {"text": {}, "image": {}}

    text_model = ""
    if args.text_vendor and args.text_vendor != "mock":
        conf = PROVIDERS.get(f"{args.text_vendor}_text") or {}
        text_model = str(conf.get("default_model") or "")
    pricing = PRICING.get(text_model) if text_model else None
    if pricing:
        miss = pricing.get("input_cache_miss")
        outp = pricing.get("output")
        if miss is not None and outp is not None:
            per = (_EST_IN_TOKENS / 1e6) * miss + (_EST_OUT_TOKENS / 1e6) * outp
            out["text"] = {
                "model": text_model, "per_call": round(per, 6),
                "calls": plan["text_calls_planned"],
                "total": round(per * plan["text_calls_planned"], 6),
                "currency": pricing.get("currency", ""), "note": "粗估（保守 token 量级）",
            }
    if not out["text"]:
        out["text"] = {"model": text_model or "(未确定)",
                       "calls": plan["text_calls_planned"], "total": None,
                       "note": "价格未登记 → 无法估算（如实记 null）"}

    img_model = args.image_model or str(
        (PROVIDERS.get("bailian_image") or {}).get("default_model") or "")
    if img_model:
        cost, cur = estimate_image_cost(img_model, args.image_size)
        out["image"] = {
            "model": img_model, "per_image": cost, "images": plan["images_planned"],
            "total": (round(cost * plan["images_planned"], 6) if cost is not None else None),
            "currency": cur,
            "registered": bool(get_image_pricing(img_model)),
            "note": "" if cost is not None else
                    "IMAGE_PRICING 未登记该模型 → estimated_cost 记 null（不用 token 价冒充）",
        }
    else:
        out["image"] = {"model": "(未确定)", "images": plan["images_planned"],
                        "total": None, "registered": False,
                        "note": "未指定图像模型名（Config default_model 为空且未传 --image-model）"}
    return out


def preflight(args) -> dict:
    """体检：四项人工前置 + 上限检查。返回 (report, blocking_reasons)。"""
    plan = build_plan(args)
    reasons: list = []

    # ① 图像模型名
    img_model = args.image_model or str(
        (PROVIDERS.get("bailian_image") or {}).get("default_model") or "")
    if not img_model:
        reasons.append("① 图像模型名未配置：请在 config.PROVIDERS['bailian_image']"
                       "['default_model'] 填入具体模型名，或显式传 --image-model。")

    # ② API key
    key = load_provider_api_key("bailian")
    if not key:
        reasons.append("② BAILIAN_API_KEY 未注入（环境变量）。真实出图需要先配置密钥。")

    # ③ 明确批准
    if not args.approve:
        reasons.append("③ 未明确批准（缺 --approve）。真实出图会计费，需人工显式批准。")

    # ④ 预算上限
    if args.max_images > MAX_REAL_IMAGE_REQUESTS:
        reasons.append(f"④ --max-images={args.max_images} 超过硬上限 "
                       f"MAX_REAL_IMAGE_REQUESTS={MAX_REAL_IMAGE_REQUESTS}。")

    # 文本侧
    if args.text_vendor == "mock":
        reasons.append("文本侧仍是 mock（--text-vendor mock）：本脚本是**真实链**冒烟，"
                       "请显式指定 --text-vendor deepseek|bailian|local。")
    if plan["text_calls_planned"] > args.max_text_calls:
        reasons.append(f"文本调用计划 {plan['text_calls_planned']} 次 > 上限 "
                       f"--max-text-calls={args.max_text_calls}：拒绝执行（不静默超支）。"
                       f"如确认可接受，请显式提高 --max-text-calls。")
    if plan["images_planned"] > args.max_images:
        reasons.append(f"出图计划 {plan['images_planned']} 张 > 上限 {args.max_images}。")

    # 图像单价未登记
    if img_model and not get_image_pricing(img_model) and not args.accept_unknown_cost:
        reasons.append(f"图像模型 {img_model!r} 的单价未登记在 config.IMAGE_PRICING；"
                       "如接受「成本未知」（estimated_cost 记 null），"
                       "请加 --accept-unknown-cost。")

    report = {
        "plan": plan,
        "estimate": estimate_costs(plan, args),
        "text_vendor": args.text_vendor,
        "image_model": img_model,
        "api_key_masked": mask_api_key(key),
        "image_size": args.image_size,
        "max_images": args.max_images,
        "max_text_calls": args.max_text_calls,
        "blocking": reasons,
    }
    return report


# ---------------------------------------------------------------------------
# 真实链执行
# ---------------------------------------------------------------------------
def build_project(plan: dict) -> Project:
    return Project(
        title=plan["title"],
        world=World(name=f"{plan['title']} world", locations=[plan["location"]]),
        episodes=[Episode(number=1, title="Real E1", summary="stage13 real smoke")],
        current_episode=1,
    )


def count_ledger(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def run_real(args, report: dict) -> int:
    plan = report["plan"]
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 真实文本 Provider：显式接 TokenLogger（避免 坑 2 的根目录记账泄漏）----
    text_provider = create_provider("text", vendor=args.text_vendor)
    text_provider.logger = TokenLogger(RUN_DIR, REAL_LLM_LEDGER)
    # ★ 关闭重试：保证"真实文本调用数 == 计划数"，不被重试放大成本
    if hasattr(text_provider, "max_retries"):
        text_provider.max_retries = 0
        text_provider.retry_backoff_seconds = 0

    # ---- 真实图像 Provider（厂商差异 = Config）----
    img_kwargs = {}
    if args.image_model:
        conf = dict(PROVIDERS["bailian_image"])
        conf["default_model"] = args.image_model
        img_kwargs["cfg"] = conf
    image_provider = create_provider("image", "bailian", **img_kwargs)

    # ---- 独立计数器（真实请求硬上限 + 审计）----
    counter = APIRequestCounter()
    set_api_counter(counter)
    if hasattr(image_provider, "counter"):
        image_provider.counter = counter

    log_before = count_ledger(RUN_DIR / REAL_LLM_LEDGER)
    started = time.time()

    orch = ProjectOrchestrator(
        build_project(plan),
        provider=text_provider,
        project_id=args.project_id,
        review_policy="auto_approve",     # ★程序化模拟，报告中必须标注
        max_attempts=1,                   # 真实冒烟不重试（成本可控）
        image_provider=image_provider,
        image_log_dir=RUN_DIR,
    )
    run_spec = {
        "episode": 1,
        "scene_ids": plan["scene_ids"],
        "generate_images": True,
        "image_size": args.image_size,
        "max_images": args.max_images,
        "resume": False,
    }
    if plan["style_spec"]:
        run_spec["style_spec"] = plan["style_spec"]
        run_spec["style_name"] = "default"

    print(f"[run] 真实链开始：scenes={plan['scene_ids']} max_images={args.max_images} "
          f"text_vendor={args.text_vendor} image_model={report['image_model']}")
    run_report = orch.run(run_spec)
    elapsed = time.time() - started

    # ---- 实际账目 ----
    text_calls = count_ledger(RUN_DIR / REAL_LLM_LEDGER) - log_before
    image_calls = count_ledger(RUN_DIR / IMAGE_LEDGER)
    st = run_report["stages"]["image"]
    gen_dir = orch.project_dir / GENERATED_DIR_NAME
    manifest_src = gen_dir / MANIFEST_FILENAME
    manifest = (json.loads(manifest_src.read_text(encoding="utf-8"))
                if manifest_src.is_file() else {})
    images = manifest.get("images") or []
    on_disk = sorted(p.name for p in gen_dir.glob("*")
                     if p.is_file() and p.name != MANIFEST_FILENAME) if gen_dir.is_dir() else []

    snap = counter.snapshot()
    final = {
        "stage": "stage13_real_image_smoke",
        "executed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "duration_seconds": round(elapsed, 1),
        "orchestration_status": run_report["status"],
        "review_mode": run_report["review_mode"],
        "plan": plan,
        "estimate": report["estimate"],
        "actual": {
            "images_requested": st["images_requested"],
            "images_generated": st["images_generated"],
            "images_failed": st["images_failed"],
            "images_rejected": st["images_rejected"],
            "images_on_disk": len(on_disk),
            "image_files": on_disk,
            "budget_used": st["budget_used"],
            "budget_cap": st["budget_cap"],
            "cost_total": st["cost_total"],
            "cost_currency": st["cost_currency"],
            "unpriced_images": st["unpriced_images"],
            "text_calls_actual": text_calls,
            "text_calls_planned": plan["text_calls_planned"],
            "image_ledger_lines": image_calls,
        },
        "real_api_requests": snap["real_api_requests"],
        "counter_by_kind": snap["by_kind"],
        "budget": snap["budget"],
        "limits": {"max_images": args.max_images,
                   "max_text_calls": args.max_text_calls,
                   "hard_cap_images": MAX_REAL_IMAGE_REQUESTS},
        "manifest_entries": images,
        "errors": run_report["errors"],
        "warnings": run_report["warnings"],
        "notes": [
            "review_mode 是**程序化模拟**（auto_approve），不是人工审核。",
            "图像成本来自 config.IMAGE_PRICING；未登记 → null（未用 token 价冒充）。",
            "manifest 副本（无二进制）留档于本目录，供溯源与 Stage 14 VLM QA 消费。",
        ],
    }
    if manifest_src.is_file():
        shutil.copyfile(manifest_src, RUN_DIR / "manifest_copy.json")
        final["manifest_copy"] = str(RUN_DIR / "manifest_copy.json")
    (RUN_DIR / "run_report.json").write_text(
        json.dumps(run_report, ensure_ascii=False, indent=2), encoding="utf-8")
    final["run_report"] = str(RUN_DIR / "run_report.json")

    (RUN_DIR / "real_run_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print("真实出图冒烟 —— 实际结果（不采信预估，只看账目）")
    print("=" * 72)
    print(json.dumps(final["actual"], ensure_ascii=False, indent=2))
    print(f"real_api_requests = {snap['real_api_requests']}  by_kind = {snap['by_kind']}")
    print(f"报告 -> {RUN_DIR / 'real_run_report.json'}")

    # 上限事后核验（预检已拦，这里再核一遍，如实报告）
    violations = []
    if image_calls > args.max_images:
        violations.append(f"图像调用 {image_calls} 次 > 上限 {args.max_images}")
    if text_calls > args.max_text_calls:
        violations.append(f"文本调用 {text_calls} 次 > 上限 {args.max_text_calls}")
    if violations:
        print("\n★ 上限核验未通过（如实报告，不掩盖）：")
        for v in violations:
            print("   -", v)
        return 4

    if st["images_generated"] == 0:
        print("\n★ 阻塞：本次真实出图 0 张。原因见 run_report.errors / manifest 的 error 字段。")
        print("   —— 绝不伪造结果，绝不用占位图冒充。")
        return 5

    print(f"\n✅ 真实出图成功 {st['images_generated']} 张，"
          f"成本 {st['cost_total']} {st['cost_currency']}")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage 13 真实出图冒烟（默认只体检，0 真实调用）")
    ap.add_argument("--approve", action="store_true",
                    help="★ 明确批准执行（计费）。不加则只打印体检报告。")
    ap.add_argument("--accept-unknown-cost", action="store_true",
                    help="图像单价未登记在 config.IMAGE_PRICING 时仍继续（成本记 null）。")
    ap.add_argument("--text-vendor", default="deepseek",
                    choices=["mock", "deepseek", "bailian", "local"],
                    help="真实文本档（缺省 deepseek；mock 会被判为阻塞）。")
    ap.add_argument("--image-model", default="",
                    help="图像模型名（缺省取 config.PROVIDERS['bailian_image']"
                         "['default_model']；★不硬编码在代码里）。")
    ap.add_argument("--max-images", type=int, default=IMAGE_CAP_DEFAULT,
                    help=f"真实图像上限（缺省 {IMAGE_CAP_DEFAULT}，硬上限 "
                         f"{MAX_REAL_IMAGE_REQUESTS}）。")
    ap.add_argument("--max-text-calls", type=int, default=TEXT_CAP_DEFAULT,
                    help=f"真实文本调用上限（缺省 {TEXT_CAP_DEFAULT}）。")
    ap.add_argument("--image-size", default="1024*1024",
                    help='canonical 尺寸记法（缺省 "1024*1024"）。')
    ap.add_argument("--scenes", type=int, default=1, help="场景数（缺省 1，控制成本）。")
    ap.add_argument("--project-id", default="stage13_real_smoke")
    ap.add_argument("--title", default="Stage13 Real Smoke")
    ap.add_argument("--character", default="Lin")
    ap.add_argument("--location", default="Apartment kitchen")
    ap.add_argument("--style-medium", default="", help="项目级风格：medium（填了才建 STYLE 档案）")
    ap.add_argument("--style-palette", default="", help="项目级风格：palette")
    ap.add_argument("--style-rendering", default="", help="项目级风格：rendering（可选）")
    args = ap.parse_args()

    if os.environ.get(FILMAGENT_PROVIDER, "").strip().lower() == "mock":
        print("提示：环境变量 FILMAGENT_PROVIDER=mock；本脚本按 --text-vendor 显式建真实档。")

    report = preflight(args)
    print("=" * 72)
    print("Stage 13 真实出图冒烟 —— 体检报告（尚未发出任何真实调用）")
    print("=" * 72)
    print(json.dumps({"plan": report["plan"], "estimate": report["estimate"],
                      "text_vendor": report["text_vendor"],
                      "image_model": report["image_model"] or "(未配置)",
                      "api_key_masked": report["api_key_masked"] or "(未注入)",
                      "image_size": report["image_size"],
                      "limits": {"max_images": report["max_images"],
                                 "max_text_calls": report["max_text_calls"]}},
                     ensure_ascii=False, indent=2))

    if report["blocking"]:
        print("\n★ 阻塞：以下前置条件未满足，**不执行任何真实调用**（不伪造结果）：")
        for r in report["blocking"]:
            print("   -", r)
        print("\n提示：以上四项为人工前置（§13）；补齐后重跑本脚本并加 --approve。")
        return 2

    if not args.approve:
        print("\n（体检通过；未加 --approve，未执行。加 --approve 即开始计费执行。）")
        return 3

    return run_real(args, report)


if __name__ == "__main__":
    sys.exit(main())
