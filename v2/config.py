# -*- coding: utf-8 -*-
"""
V0.2 阶段 2 —— 配置文件（v2/config.py）

作用：集中管理 LLM 调用层的所有配置：
    - API base URL
    - API key 的获取方式（绝不硬编码在 Python 里）
    - 默认模型
    - 超时
    - 最大重试次数
    - 模型价格（pricing）

安全（开工指令 §十八）：
    - 绝对不出现 API_KEY = "sk-xxxx" 这种硬编码。
    - 读取顺序：环境变量 DEEPSEEK_API_KEY 优先，其次只读 V0.1 已存在的
      api_key.txt（该文件已被 .gitignore 忽略，不会进入 git）。
      这是"当前项目已有的安全配置方式"，V0.2 只是复用，不修改它。
    - 任何代码、日志、报告都只显示脱敏后的 key（如 sk-****abcd）。

价格（开工指令 §八）：
    - 价格放这里，不硬编码进业务 Agent。
    - 依据 2026-08-31 查证的 DeepSeek 官方公开价（2026-08-17 起的高峰/低谷计价，
      本配置取高峰价做保守估计，宁可多算不少算），单位人民币（CNY）。
    - 价格处于调整期（官方有提价传闻），真账请以平台为准；改这个 dict 即可。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
V2_ROOT = Path(__file__).resolve().parent                 # v2/
V01_KEY_FILE = V2_ROOT.parent / "FilmAgent" / "api_key.txt"  # V0.1 已存在的密钥文件（只读）

# ---------------------------------------------------------------------------
# API 基础配置
# ---------------------------------------------------------------------------
API_BASE_URL = "https://api.deepseek.com"   # DeepSeek，OpenAI 兼容格式
DEFAULT_MODEL = "deepseek-chat"             # = deepseek-v4-flash（非思考模式）
TIMEOUT_SECONDS = 120.0                     # 单次请求超时，避免无限挂死
MAX_RETRIES = 2                             # 默认最多重试 2 次（1 次初始 + 2 次重试）
RETRY_BACKOFF_SECONDS = 1.0                 # 重试间隔（指数退避起点）

# 环境变量名（优先于 api_key.txt）
ENV_API_KEY_NAME = "DEEPSEEK_API_KEY"

# ---------------------------------------------------------------------------
# API Key 读取（只读，不修改任何文件；绝不硬编码）
# ---------------------------------------------------------------------------
def load_api_key() -> str:
    """获取 API key：先看环境变量，再看 V0.1 的 api_key.txt（只读）。

    返回空字符串表示"未配置"（此时真实调用会以认证失败收场，测试会明确报错）。
    """
    env_key = os.environ.get(ENV_API_KEY_NAME, "").strip()
    if env_key:
        return env_key
    if V01_KEY_FILE.exists():
        key = V01_KEY_FILE.read_text(encoding="utf-8").strip()
        # V0.1 的占位符（没填真实密钥）不算配置成功
        if key and not key.startswith("sk-在这里"):
            return key
    return ""


def api_key_configured() -> bool:
    """密钥是否已配置（只看有没有，绝不输出内容）。"""
    return bool(load_api_key())


def mask_api_key(key: str) -> str:
    """脱敏：sk-****abcd 形式。空 key 返回空串。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:3] + "****" + key[-4:]


def api_key_masked() -> str:
    """已脱敏的 key（用于测试/报告显示）。"""
    return mask_api_key(load_api_key())


# ---------------------------------------------------------------------------
# 价格配置（CNY / 1M tokens；保守取高峰价）
# ---------------------------------------------------------------------------
PRICING: dict = {
    "deepseek-chat": {
        "input_cache_hit": 0.10,    # 输入缓存命中  ¥0.10 / 1M
        "input_cache_miss": 3.00,   # 输入缓存未命中 ¥3.00 / 1M
        "output": 9.00,             # 输出          ¥9.00 / 1M
        "currency": "CNY",
        "note": ("DeepSeek 官方 2026-08-17 起改为高峰/低谷计价（高峰：北京时间 "
                 "9-12、14-18；低谷其余时段，约半价）。本配置取高峰价做保守估计。"
                 "价格处于调整期，真账以平台为准。"),
    },
}


def get_pricing(model: str) -> Optional[dict]:
    """取某模型的价格配置；没有则返回 None（此时成本计为 null）。"""
    return PRICING.get(model)


__all__ = [
    "V2_ROOT",
    "API_BASE_URL",
    "DEFAULT_MODEL",
    "TIMEOUT_SECONDS",
    "MAX_RETRIES",
    "RETRY_BACKOFF_SECONDS",
    "load_api_key",
    "api_key_configured",
    "mask_api_key",
    "api_key_masked",
    "PRICING",
    "get_pricing",
]
