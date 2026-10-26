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


# ---------------------------------------------------------------------------
# 图像价格配置（★ Stage 13 新增；与 token 计价**分开**，绝不混用）
# ---------------------------------------------------------------------------
IMAGE_PRICING: dict = {
    # ★ 这里刻意**不登记任何图像模型价格** —— 这是护栏，不是缺陷（开工指令13 §7.3-5）：
    #   未登记 → manifest 与图像记账里 estimated_cost = null，
    #   **绝不允许**拿文本 token 价格冒充图像价格（两者计价单位不同）。
    #
    # 登记结构（"按每张 / 按尺寸"两种计价都支持）：
    #   "<model>": {
    #       "per_image": 0.20,                  # 每张基础价
    #       "by_size": {"1024*1024": 0.20},     # 按尺寸覆盖 per_image（可选）
    #       "currency": "CNY",
    #       "note": "价格来源 + 查证日期（真账以平台为准）",
    #   }
    #
    # 真实出图前，请人工把所选模型的单价填在这里；填了才会算出非 null 的成本。
}


def get_image_pricing(model: str) -> Optional[dict]:
    """取某图像模型的价格配置；未登记返回 None。"""
    return IMAGE_PRICING.get(str(model or ""))


def estimate_image_cost(model: str, size: Optional[str] = None):
    """按图像价格表估算**一张图**的成本。

    :returns: (cost, currency)。未登记 / 价格字段不全 → (None, "")。
              ★ 返回 None 表示"无法估算"，调用方必须如实记 null，
              绝不用文本 token 价格冒充（§7.3-5）。
    """
    pricing = IMAGE_PRICING.get(str(model or ""))
    if not pricing:
        return None, ""
    currency = str(pricing.get("currency", "") or "")
    key = str(size or "")
    by_size = pricing.get("by_size") or {}
    if key and key in by_size:
        val = by_size[key]
        if val is None:
            return None, currency
        return round(float(val), 8), currency
    per = pricing.get("per_image")
    if per is None:
        return None, currency
    return round(float(per), 8), currency


# ---------------------------------------------------------------------------
# 视频价格配置（★ Stage 15 新增；与 token/图像计价**分开**，绝不混用）
# ---------------------------------------------------------------------------
VIDEO_PRICING: dict = {
    # ★ 这里刻意**不登记任何视频模型价格** —— 这是护栏，不是缺陷（§15-B4）：
    #   未登记 → manifest 与视频记账里 estimated_cost = null，
    #   **绝不允许**拿文本 token 价格或图像单价冒充视频价格（计价单位不同）。
    #
    # 登记结构（视频按"每秒 / 按分辨率"计价）：
    #   "<model>": {
    #       "per_second": 0.50,                    # 每秒基础价
    #       "by_resolution": {"1280*720": 0.50},   # 按分辨率覆盖 per_second（可选）
    #       "currency": "CNY",
    #       "note": "价格来源 + 查证日期（真账以平台为准）",
    #   }
    #
    # 真实生成前，请人工把所选模型的单价填在这里；填了才会算出非 null 的成本。
}


def get_video_pricing(model: str) -> Optional[dict]:
    """取某视频模型的价格配置；未登记返回 None。"""
    return VIDEO_PRICING.get(str(model or ""))


def estimate_video_cost(model: str, duration: Optional[float] = None,
                        resolution: Optional[str] = None):
    """按视频价格表估算**一段视频**的成本（§15-B4）。

    :returns: (cost, currency)。未登记 / 价格字段不全 / 时长未知 → (None, currency)。
              ★ 返回 None 表示"无法估算"，调用方必须如实记 null，
              绝不用 token/图像价格冒充视频价格。
    """
    pricing = VIDEO_PRICING.get(str(model or ""))
    if not pricing:
        return None, ""
    currency = str(pricing.get("currency", "") or "")
    key = str(resolution or "")
    by_res = pricing.get("by_resolution") or {}
    per_second = pricing.get("per_second")
    if key and key in by_res:
        per_second = by_res[key]
    if per_second is None:
        return None, currency
    if duration is None:
        return None, currency
    try:
        dur = float(duration)
    except (TypeError, ValueError):
        return None, currency
    if dur <= 0:
        return None, currency
    return round(float(per_second) * dur, 8), currency


# ---------------------------------------------------------------------------
# Provider 配置注册表（Stage 9 加法；不改动上面任何既有导出）
# ---------------------------------------------------------------------------
# 目标（stage9_plan §13）：给 Config 增加"供应商/Provider"维度，接百炼或本地模型时
# 不用在 DeepSeek 的命名空间里硬塞第二套 URL / key / 价格。
#
# 结构约定：
#     "kind"          模态+协议标签（text/vision/embedding…），Router 按它选队列
#     "vendor"        供应商标签（deepseek/bailian/local…），Router 按它精确选实例
#     "base_url"/"default_model"  是"同一实现类的不同配置"，不是写死进业务代码
#     "api_key_env"   环境变量名（key 绝不硬编码；见 load_provider_api_key）
#     "pricing"       成本走既有 flat PRICING 键；None = 未登记价格（cost 计 null）
#
# 重要：这里登记的都是"真实端点/厂商档"。mock_* 不需要登记（factory 按需合成）。
# default_model 留空的（bailian/local）表示"真实调用前由用户在 Config 填"，不硬编码。
FILMAGENT_PROVIDER = "FILMAGENT_PROVIDER"          # 环境开关：mock|deepseek|bailian|local
_SUPPORTED_PROVIDERS = ("mock", "deepseek", "bailian", "local")

# 供应商 -> API key 环境变量名（空 = 该供应商通常无 key）
PROVIDER_ENV_API_KEYS = {
    "mock": "",
    "deepseek": ENV_API_KEY_NAME,
    "bailian": "BAILIAN_API_KEY",
    "local": "",
    # ★ Stage 13-P：新增图像 vendor（纯追加；既有 4 项 0 改）
    "bailian_native": "BAILIAN_API_KEY",   # 百炼原生档（与 bailian 同一把 key）
    "ark": "ARK_API_KEY",                  # 火山方舟
    "openai": "OPENAI_API_KEY",            # OpenAI 官方
}

PROVIDERS: dict = {
    # DeepSeek：与既有 API_BASE_URL / DEFAULT_MODEL 同源（历史 DeepSeek 计费原样保留）
    "deepseek_text": {
        "kind": "text", "vendor": "deepseek",
        "base_url": API_BASE_URL,
        "default_model": DEFAULT_MODEL,
        "api_key_env": ENV_API_KEY_NAME,
        "pricing": "deepseek-chat",
    },
    # 百炼 DashScope（OpenAI 兼容模式）。真实调用前请在 Config 填 default_model。
    "bailian_text": {
        "kind": "text", "vendor": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "",              # 用户 Config 填（如 qwen-plus / qwen-max / …）
        "api_key_env": "BAILIAN_API_KEY",
        "pricing": None,                  # 未登记价格 -> cost=null（不以 DeepSeek 价冒充）
    },
    # 本地模型（vLLM/Ollama/llama.cpp 普遍兼容 OpenAI /v1/chat/completions）：
    # 未来 LocalTextProvider 大概率不是新类，而是本档配置（stage9_plan §10）。
    "local_text": {
        "kind": "text", "vendor": "local",
        "base_url": "http://127.0.0.1:8000/v1",   # vLLM 示例地址；真实部署按需改
        "default_model": "",              # 由部署决定，Config 填
        "api_key_env": "",
        "pricing": None,                  # 本地通常不计费 -> cost=null
    },
    # 视觉 / 向量：Stage 9 只立"接口位 + Config 槽位"，不实现真实调用（见工厂报错）。
    "bailian_vision": {
        "kind": "vision", "vendor": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "", "api_key_env": "BAILIAN_API_KEY", "pricing": None,
    },
    "bailian_embedding": {
        "kind": "embedding", "vendor": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "", "api_key_env": "BAILIAN_API_KEY", "pricing": None,
    },
    # ★ Stage 13 新增：文生图档（厂商差异全部落在这里，代码里不出现任何具体模型名）。
    #   真实出图前置四项（人工）：填 default_model + 注入 BAILIAN_API_KEY + 明确批准 + 确认预算。
    "bailian_image": {
        "kind": "image", "vendor": "bailian",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "",              # 用户 Config 填（具体型号由人工决定，绝不硬编码）
        "api_key_env": "BAILIAN_API_KEY",
        "pricing": None,                  # 图像价格走 IMAGE_PRICING（与 token 价分开）
        "size_separator": "x",            # canonical "1024*1024" → 线上协议 "1024x1024"
        "default_size": "1024*1024",      # 缺省尺寸（实际由编排层 run_spec 显式传）
    },

    # ------------------------------------------------------------------
    # ★ Stage 13-P：图像 Provider 档位（★纯新增，既有档位 0 改、行为 0 变）
    #
    #   `protocol` 字段决定用哪个实现（缺省 = "openai"）：
    #       缺省 / "openai"      → OpenAICompatibleImageProvider（OpenAI Images 兼容）
    #       "dashscope_native"   → BailianImageProvider（百炼 DashScope 原生，同步/异步）
    #
    #   ★ 两档**并存、互不影响**：想换回兼容档，只需让调用方指向另一个 vendor
    #     （或改环境开关 FILMAGENT_PROVIDER），代码一行不用动。
    # ------------------------------------------------------------------

    # OpenAI Images 兼容档 —— 火山方舟（同步返回，url 或 b64_json 都支持）
    "ark_image": {
        "kind": "image", "vendor": "ark",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "",              # 填具体图像模型名（人工填，绝不硬编码；型号见厂商文档）
        "api_key_env": "ARK_API_KEY",
        "pricing": None,
        "size_separator": "x",            # 方舟：canonical "1024*1024" → 线上 "1024x1024"
        "default_size": "1024*1024",
    },

    # OpenAI Images 兼容档 —— OpenAI 官方
    "openai_image": {
        "kind": "image", "vendor": "openai",
        "base_url": "https://api.openai.com/v1",
        "default_model": "",              # 填具体图像模型名（人工填，绝不硬编码）
        "api_key_env": "OPENAI_API_KEY",
        "pricing": None,
        "size_separator": "x",
        "default_size": "1024*1024",
    },

    # 百炼 DashScope **原生**档（同步/异步由 `async` 决定；形状见 bailian_image.py）
    "bailian_native_image": {
        "kind": "image", "vendor": "bailian_native",
        "protocol": "dashscope_native",
        # ★ 官方文档（2026-09）示例用**业务空间域**：
        #     https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1
        #   若账号要求空间域，请把下行覆盖为空间域；此处默认用长期稳定的公共域。
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "default_model": "",              # 填具体图像模型名（人工填，绝不硬编码；型号见厂商文档）
        "api_key_env": "BAILIAN_API_KEY",
        "pricing": None,
        "size_separator": "*",            # ★ DashScope 用 "宽*高"，与 canonical 记法一致
        "default_size": "1024*1024",
        "async": False,                   # true → 万相异步（X-DashScope-Async + task 轮询）
        "poll_interval": 3.0,
        "poll_timeout": 180.0,
    },

    # ------------------------------------------------------------------
    # ★ Stage 15：图生视频档（原生异步协议；厂商差异全部落这里，代码不硬编码模型名）
    #
    #   `protocol` 明确标识"原生异步视频协议"（dashscope_native_video）。
    #   base_url 已含 `/api/v1`（公共域与业务空间域共用此语义，/api/v1 恰好一次）。
    #   default_model 留空 = 真实生成前由用户 Config 填入精确模型名（不硬编码）。
    # ------------------------------------------------------------------
    "bailian_video": {
        "kind": "video", "vendor": "bailian",
        "protocol": "dashscope_native_video",
        "base_url": "https://dashscope.aliyuncs.com/api/v1",
        "default_model": "",              # 填具体视频模型名（人工填，绝不硬编码）
        "api_key_env": "BAILIAN_API_KEY",
        "pricing": None,                  # 视频价格走 VIDEO_PRICING（与 token/图像分开）
        "create_path": "/services/aigc/video-generation/video-synthesis",
        "task_path": "/tasks/{task_id}",
        "poll_interval": 3.0,
        "capabilities": {
            "resolutions": ["1280*720", "720*1280"],
            "duration": {"min": 2, "max": 10},
            "prompt_extend": False,
            "watermark": False,
        },
    },
}


def get_provider_config(name: str) -> dict:
    """取某 Provider 配置档的拷贝（防调用方误改常量）。查不到明确报错。"""
    if name not in PROVIDERS:
        raise KeyError(
            f"未登记的 Provider 配置档: {name!r}。现有: {sorted(PROVIDERS)}"
        )
    return dict(PROVIDERS[name])


def default_text_vendor() -> str:
    """默认文本供应商：环境变量 FILMAGENT_PROVIDER 优先，缺省 mock（0 API）。

    非法取值明确报错（宁可不跑，也不要悄悄退回 mock 造成"以为真调了"的错觉）。
    """
    token = os.environ.get(FILMAGENT_PROVIDER, "").strip().lower()
    if not token:
        return "mock"
    if token not in _SUPPORTED_PROVIDERS:
        raise ValueError(
            f"FILMAGENT_PROVIDER 取值非法: {token!r}"
            f"（支持 {list(_SUPPORTED_PROVIDERS)}）"
        )
    return token


def load_provider_api_key(vendor: str) -> str:
    """按供应商读 API key：环境变量优先；deepseek 兼容回退到 V0.1 api_key.txt。

    安全：只读不写、绝不打印 key；空串 = 未配置（真实调用将以认证失败收场）。
    """
    env = PROVIDER_ENV_API_KEYS.get(vendor, "")
    if env:
        key = os.environ.get(env, "").strip()
        if key:
            return key
    if vendor == "deepseek":
        return load_api_key()   # 回退：V0.1 api_key.txt（只读，已被 .gitignore 忽略）
    return ""


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
    "IMAGE_PRICING",
    "get_image_pricing",
    "estimate_image_cost",
    "VIDEO_PRICING",
    "get_video_pricing",
    "estimate_video_cost",
    "FILMAGENT_PROVIDER",
    "PROVIDER_ENV_API_KEYS",
    "PROVIDERS",
    "get_provider_config",
    "default_text_vendor",
    "load_provider_api_key",
]
