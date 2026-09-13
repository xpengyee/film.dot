# -*- coding: utf-8 -*-
"""
V0.2 Stage 9 —— Provider Registry / Factory（v2/llm/providers/registry.py）

作用（stage9_plan §14）：把"Config → Provider 实例"收敛到唯一入口，替代各处接线
脚本手写 LLMClient(...) 的散落状态。刻意保持朴素：字典映射 + 工厂函数即可，
不上装饰器 / IoC 容器（指令反对过度工程）。

选择规则（kind = 模态+协议标签，vendor = 供应商标签）：
    create_provider(kind, vendor)   显式造一个 Provider（测试/真实脚本用）
    get_default_provider(kind)      按环境开关 FILMAGENT_PROVIDER 造"默认档"
                                    （缺省 mock -> 0 API；跑真实脚本时显式切）

关键约定（stage9_plan §3 / §10 / §13）：
    - "协议变了"（vision 换 payload / embedding 换 endpoint / 原生非 OpenAI 兼容）
      才需要新实现类；"只是换一家 OpenAI 兼容厂商"统一走 OpenAICompatibleTextProvider
      的不同 Config 档（deepseek / bailian / local 都是同一实现类的不同配置）。
    - 生成类（image/video/audio）Stage 9 只预留接口，不提供实现也不提供 mock，
      create_provider 遇到它们会明确报错。
"""
from __future__ import annotations

import os
from typing import Optional

from config import (
    PROVIDERS,
    FILMAGENT_PROVIDER,
    get_provider_config,
    default_text_vendor,
    load_provider_api_key,
)

from llm.providers.base import (
    Provider,
    GENERATION_KINDS,
    VisionProvider,
    MultimodalProvider,
    EmbeddingProvider,
)
from llm.providers.mock import (
    MockTextProvider,
    MockVisionProvider,
    MockMultimodalProvider,
    MockEmbeddingProvider,
)
from llm.providers.openai_compatible import OpenAICompatibleTextProvider
from llm.providers.bailian import BailianTextProvider


# ---------------------------------------------------------------------------
# 工厂：按 kind + vendor 造 Provider
# ---------------------------------------------------------------------------
def create_provider(
    kind: str = "text",
    vendor: Optional[str] = None,
    *,
    cfg: Optional[dict] = None,
) -> Provider:
    """按 (kind, vendor) 造一个 Provider 实例。

    :param kind:   模态+协议标签（text/vision/multimodal/embedding）
    :param vendor: 供应商标签（mock/deepseek/bailian/local）；None -> 环境开关默认
    :param cfg:    可选配置覆盖（base_url / default_model / ...），覆盖 PROVIDERS 档
    """
    kind = (kind or "text").lower()
    vendor = ((vendor or "").lower()) or None

    if kind in GENERATION_KINDS:
        raise ValueError(
            f"create_provider(kind={kind!r})：生成类 Provider（image/video/audio）"
            f"是 Stage 9 预留接口，本阶段不实现、不提供 mock。"
        )

    if kind == "text":
        return _create_text_provider(vendor, cfg=cfg)
    if kind == "vision":
        if vendor == "mock":
            return MockVisionProvider()
        return _create_real_image_provider(vendor, kind="vision", cfg=cfg)
    if kind == "multimodal":
        if vendor == "mock":
            return MockMultimodalProvider()
        return _create_real_image_provider(vendor, kind="multimodal", cfg=cfg)
    if kind == "embedding":
        if vendor == "mock":
            return MockEmbeddingProvider()
        raise ValueError(
            f"create_provider(kind='embedding', vendor={vendor!r})：Stage 9 未实现真实"
            f"向量 Provider。请用 vendor='mock'。"
        )
    raise ValueError(f"未知 Provider kind: {kind!r}（支持 text/vision/multimodal/embedding）")


def _create_text_provider(vendor: Optional[str], *, cfg: Optional[dict]) -> Provider:
    vendor = vendor or default_text_vendor()
    if vendor == "mock":
        return MockTextProvider()
    if vendor not in _text_vendors():
        raise ValueError(
            f"未知文本 vendor: {vendor!r}。支持 {sorted(_text_vendors() | {'mock'})}。"
        )
    # 真实 OpenAI 兼容文本：厂商差异 = Config 差异（protocol 没变就不用新类）
    conf = dict(cfg) if cfg else get_provider_config(f"{vendor}_text")
    cls = BailianTextProvider if vendor == "bailian" else OpenAICompatibleTextProvider
    kwargs = dict(
        api_key=load_provider_api_key(vendor),
        base_url=conf.get("base_url", ""),
        model=conf.get("default_model", ""),
    )
    if vendor != "bailian":   # Bailian 类自身已固定 vendor="bailian"，避免重复传参
        kwargs["vendor"] = vendor
    return cls(**kwargs)


def _text_vendors() -> set:
    return {c["vendor"] for name, c in PROVIDERS.items()
            if name.endswith("_text") and c.get("kind") == "text"}


def _image_kinds() -> set:
    return {c["vendor"] for name, c in PROVIDERS.items()
            if name.endswith("_vision") and c.get("kind") == "vision"}


def _create_real_image_provider(vendor: Optional[str], *, kind: str,
                                cfg: Optional[dict]) -> Provider:
    """真实视觉/多模态 Provider（Stage 10-B）：厂商差异 = Config 档。

    护栏（stage10_plan §23）：
        - 未登记厂商 / default_model 为空 → 明确报错（不硬编码模型名，
          不静默退回 mock 造成"以为真调了"的错觉）；
        - 构造成功 ≠ 会发请求；每个真实 HTTP 请求仍受 APIRequestCounter
          预算硬上限（vision=3）保护。
    """
    from llm.providers.openai_compatible_vision import (
        OpenAICompatibleVisionProvider, OpenAICompatibleMultimodalProvider,
    )
    vendor = (vendor or "").lower()
    if not vendor or vendor == "mock":
        raise ValueError(f"create_provider(kind={kind!r})：vendor 不能为空/mock（mock 请走 kind='vision', vendor='mock'）。")
    if vendor not in _image_kinds():
        raise ValueError(
            f"未知视觉 vendor: {vendor!r}。支持 {sorted(_image_kinds() | {'mock'})}。")
    conf = dict(cfg) if cfg else get_provider_config(f"{vendor}_vision")
    model = str(conf.get("default_model", "") or "")
    if not model:
        raise ValueError(
            f"config.PROVIDERS['{vendor}_vision']['default_model'] 为空：真实视觉调用前"
            f"请在 Config 填入具体 VLM 模型名（不硬编码），或显式传 cfg 覆盖。")
    base_url = str(conf.get("base_url", "") or "")
    if not base_url:
        raise ValueError(f"config.PROVIDERS['{vendor}_vision']['base_url'] 为空。")
    api_key = load_provider_api_key(vendor)
    if not api_key:
        raise ValueError(
            f"vendor={vendor!r} 的 API key 未配置（环境变量 "
            f"{conf.get('api_key_env', '')}）。真实视觉调用需要先配置密钥。")
    cls = OpenAICompatibleMultimodalProvider if kind == "multimodal" else OpenAICompatibleVisionProvider
    return cls(
        api_key=api_key,
        base_url=base_url,
        model=model,
        vendor=vendor,
    )


# ---------------------------------------------------------------------------
# 默认 Provider（环境开关决定）
# ---------------------------------------------------------------------------
def get_default_provider(kind: str = "text") -> Provider:
    """按环境开关造"当前默认 Provider"。

    默认（未设 FILMAGENT_PROVIDER）返回 mock：任何日常测试 / workflow 都是
    0 API 调用。跑真实脚本时显式设 FILMAGENT_PROVIDER=deepseek|bailian|local 再切，
    业务 Agent 代码不需要感知这种切换。
    """
    vendor = os.environ.get(FILMAGENT_PROVIDER, "").strip().lower() or None
    return create_provider(kind, vendor=vendor)


def provider_kind_vendor(p: Provider) -> tuple:
    """读出 Provider 的 (kind, vendor, model) 元信息（供 Router / 测试快速取）。"""
    return p.kind, p.vendor, p.model


__all__ = [
    "create_provider",
    "get_default_provider",
    "provider_kind_vendor",
]
