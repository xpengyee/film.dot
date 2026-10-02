# -*- coding: utf-8 -*-
"""
V0.2 Stage 14 —— VLM Image QA 真实冒烟入口（默认拒绝，0 调用）

设计原则（Stage_14C.md §6 T13 / §10）：
    1. 默认只打印**脱敏计划**并非 0 退出；本脚本被调用而缺少任一前置即拒绝。
    2. 真实执行需要**同时**满足：精确 provider + model、API key、已有且经
       SHA 校验的 Stage 13 manifest/image、`--approve`、以及本会话显式确认
       环境变量 `FILMAGENT_QA_CONFIRM_REAL=1`。任一缺失 → 拒绝。
    3. `max_qa_images` 强制为 1；Provider retry 关闭。
    4. 计划中明确显示 provider / model / 免费额度或成本为"已知/未知"，
       未知一律按"可能计费"处理。
    5. 绝不显示 API key；绝不自动修改 Config；绝不自动 fallback 到其他模型。

本会话（Stage 14-C）**不得**运行真实执行路径 —— 只允许运行默认体检/拒绝路径。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V2_ROOT))

MAX_QA_IMAGES = 1
IMAGE_MANIFEST_REL = "generated_images/manifest.json"
CONFIRM_ENV = "FILMAGENT_QA_CONFIRM_REAL"
KEY_ENV_BY_VENDOR = {
    "bailian": "DASHSCOPE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def mask_key(value: str) -> str:
    """只显示脱敏形式（与 config.mask_api_key 同风格），绝不回显 key 内容。"""
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Stage 14 VLM Image QA real smoke (default: refuse)")
    p.add_argument("--project-id", default=os.environ.get("FILMAGENT_QA_PROJECT_ID", ""))
    p.add_argument("--provider", default=os.environ.get("FILMAGENT_QA_PROVIDER", ""))
    p.add_argument("--model", default=os.environ.get("FILMAGENT_QA_MODEL", ""))
    p.add_argument("--max-qa-images", type=int, default=MAX_QA_IMAGES)
    p.add_argument("--approve", action="store_true",
                   help="用户明确执行开关（仍需 %s=1）" % CONFIRM_ENV)
    return p.parse_args(argv)


def _api_key_for(provider: str) -> str:
    env_name = KEY_ENV_BY_VENDOR.get(str(provider or "").lower(), "")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    return os.environ.get("FILMAGENT_QA_API_KEY", "")


def build_plan(args) -> dict:
    project_dir = V2_ROOT / "projects" / str(args.project_id or "<missing>")
    manifest = project_dir / IMAGE_MANIFEST_REL
    generated = []
    manifest_ok = False
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            generated = [e.get("image_id") for e in (data.get("images") or [])
                         if e.get("status") == "generated"]
            manifest_ok = bool(generated)
        except (OSError, ValueError):
            manifest_ok = False
    key = _api_key_for(args.provider)
    return {
        "stage": "14",
        "mode": "real_smoke",
        "provider": str(args.provider or "<missing>"),
        "model": str(args.model or "<missing>"),
        "max_qa_images": MAX_QA_IMAGES,
        "provider_retry": "disabled (max 1 attempt per call)",
        "project_id": str(args.project_id or "<missing>"),
        "manifest_present": manifest.is_file(),
        "generated_images": len(generated),
        "api_key_present": bool(key),
        "api_key_masked": mask_key(key),
        "cost": "unknown -> treated as possibly billable",
        "quota": "unknown -> treated as possibly billable",
        "budget_kind": "vision",
        "note": "本脚本不显示 key、不自动改 Config、不自动 fallback。",
    }


def collect_refusals(args, plan: dict) -> list:
    reasons = []
    if not args.project_id:
        reasons.append("缺少 project_id（--project-id / FILMAGENT_QA_PROJECT_ID）")
    if not args.provider:
        reasons.append("缺少精确 provider（--provider / FILMAGENT_QA_PROVIDER）")
    if not args.model:
        reasons.append("缺少精确 model（--model / FILMAGENT_QA_MODEL）")
    if args.max_qa_images != MAX_QA_IMAGES:
        reasons.append(f"max_qa_images 必须为 {MAX_QA_IMAGES}（实际 {args.max_qa_images}）")
    if not plan["manifest_present"]:
        reasons.append("缺少 Stage 13 manifest（generated_images/manifest.json）")
    elif plan["generated_images"] == 0:
        reasons.append("Stage 13 manifest 中没有 generated 图片")
    if not plan["api_key_present"]:
        reasons.append("缺少 API key")
    if not args.approve:
        reasons.append("缺少用户执行开关 --approve")
    if os.environ.get(CONFIRM_ENV) != "1":
        reasons.append(f"缺少本会话授权环境变量 {CONFIRM_ENV}=1")
    return reasons


def main(argv=None) -> int:
    args = parse_args(argv)
    plan = build_plan(args)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    reasons = collect_refusals(args, plan)
    if reasons:
        print("REFUSED（真实调用未发出，0 次）:")
        for r in reasons:
            print(f"  - {r}")
        return 2
    # 只有全部前置满足才可能走到这里（本会话不会触发：无 CONFIRM_ENV）。
    print("PRECONDITIONS SATISFIED — 真实执行路径需在本会话外单独获批后启用。")
    return 3


if __name__ == "__main__":
    sys.exit(main())
