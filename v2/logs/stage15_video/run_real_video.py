# -*- coding: utf-8 -*-
"""
V0.2 Stage 15 —— 真实视频生成脚本（脱敏计划 + 拒绝护栏）

本脚本**本阶段不执行真实 API**。默认行为：打印一份脱敏的生成计划，
并以非 0 退出码结束（写 REAL_EXECUTION_NOT_IMPLEMENTED），明确声明
"真实执行尚未实现 / 未取得用户对精确 provider+model 的批准"。

护栏（§15-B10 / D15-05 / T14）：
    缺 tag / project / image / provider / model / key / region / quota-cost 声明 /
    用户开关 任一 → 立即拒绝，不构造 Provider、不发请求。

真实运行前必须向用户列明（§7 / D15-05）：
    provider、精确 model、region/endpoint、输入 image_id/shot_ref、
    resolution、duration、最大提交次数、已确认免费额度（或"额度未知，可能计费"）、
    估算费用（或"价格未知"）。

用法：
    python v2/logs/stage15_video/run_real_video.py               # 打印脱敏计划，非 0 退出
    python v2/logs/stage15_video/run_real_video.py --plan-only   # 同上（显式）

本脚本真实 API 调用 = 0，网络 = 0，模型切换 = 0。
"""
import os
import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V2_ROOT))

REAL_EXECUTION_NOT_IMPLEMENTED = (
    "REAL_EXECUTION_NOT_IMPLEMENTED: 真实视频生成尚未实现。"
    "Stage 15 只交付能力与测试；真实冒烟须另行取得用户对「精确 provider + 精确 model」"
    "的明确批准，且确认剩余额度 / 费用后方可执行。"
)


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:3] + "****" + key[-4:]


def _collect_plan() -> dict:
    """从环境 / 入参收集一份脱敏计划（绝不打印 key / 签名 URL）。"""
    from config import get_provider_config
    provider = os.environ.get("FILMAGENT_PROVIDER", "").strip().lower() or "mock"
    model = ""
    base_url = ""
    key_masked = ""
    try:
        conf = get_provider_config(f"{provider}_video")
        model = str(conf.get("default_model", "") or "")
        base_url = str(conf.get("base_url", "") or "")
        key_env = str(conf.get("api_key_env", "") or "")
        key_masked = _mask_key(os.environ.get(key_env, "") or "")
    except (KeyError, ValueError):
        pass
    return {
        "provider": provider,
        "model": model or "<未配置>",
        "endpoint": base_url or "<未配置>",
        "api_key_masked": key_masked or "<未配置>",
        "image_ids": [],            # 真实运行时由用户显式指定
        "resolution": str(os.environ.get("FILMAGENT_VIDEO_RESOLUTION", "") or "<未配置>"),
        "duration": str(os.environ.get("FILMAGENT_VIDEO_DURATION", "") or "<未配置>"),
        "max_submissions": 1,       # 强制 1（§15-B10）
        "quota": "额度未知，可能计费",   # 未确认额度，绝不声明"免费"
        "cost": "价格未知",              # 未登记价格，绝不冒充
    }


def _reject(reason: str) -> int:
    print("[REJECTED]", reason)
    print(REAL_EXECUTION_NOT_IMPLEMENTED)
    return 2


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Stage 15 真实视频生成（护栏）")
    parser.add_argument("--plan-only", action="store_true",
                        help="仅打印脱敏计划（默认行为）")
    args = parser.parse_args()

    # 用户开关：默认关闭（未显式授权不得运行）
    if not os.environ.get("FILMAGENT_VIDEO_REAL_RUN") == "1":
        print("默认拒绝：未设置用户授权开关 FILMAGENT_VIDEO_REAL_RUN=1。")
        print("本阶段不执行真实 API；以下为脱敏计划：")
        plan = _collect_plan()
        print(f"  provider        : {plan['provider']}")
        print(f"  model           : {plan['model']}")
        print(f"  endpoint        : {plan['endpoint']}")
        print(f"  api_key         : {plan['api_key_masked']}")
        print(f"  image_ids       : {plan['image_ids'] or '<未提供>（真实运行须显式指定）'}")
        print(f"  resolution      : {plan['resolution']}")
        print(f"  duration        : {plan['duration']}")
        print(f"  max_submissions : {plan['max_submissions']}")
        print(f"  额度            : {plan['quota']}")
        print(f"  费用估算        : {plan['cost']}")
        print()
        print(REAL_EXECUTION_NOT_IMPLEMENTED)
        return 3

    # 即使开关打开，本阶段也拒绝真实执行（未实现）
    return _reject("真实执行路径未实现（FILMAGENT_VIDEO_REAL_RUN=1 但代码无真实执行分支）")


if __name__ == "__main__":
    sys.exit(main())
