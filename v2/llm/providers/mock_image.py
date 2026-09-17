# -*- coding: utf-8 -*-
"""
V0.2 Stage 13 —— Mock 文生图 Provider（v2/llm/providers/mock_image.py）

作用（开工指令13 §7.1）：
    让 `kind="image"` 在缺省（`FILMAGENT_PROVIDER=mock`）下就能走通**完整链路**：
        compose → generate_image → 落盘 → sha256 → manifest → ShotAsset 挂回
    且 **0 网络、0 成本、可重复**。

产出形态（本文件最关键的约定）：
    `generate_image()` 返回的 `ProviderResult.text` 是一个**图片引用串**，
    而不是"已经落盘的文件名"——文件命名与版本是 Artifact 层铁律，
    Provider 不得决定（providers/base.py ImageGenerationProvider 的既有约定）。

    引用串三种形态（由下游编排层解析，见 orchestration/image_stage.py）：
        data:image/<fmt>;base64,<...>   内嵌图片（Mock 走这条）
        http(s)://...                   远端 URL，需下载为二进制
        <本地路径>                       已是本地文件

    为什么 Mock 用 data URL 而不是直接写盘：
        1. Provider 不碰磁盘（不决定文件命名/版本）；
        2. 下游能真实解码 → 真实算 sha256 → "落盘-校验"链路不是假的；
        3. 不产生任何临时文件，测试天然无泄漏。

确定性（§十二 T03）：
    同 (prompt, size, reference) → 字节完全一致的 PNG（纯 stdlib：zlib + struct）。
    不同 prompt → 不同字节（使"多镜头出图"与"sha256 逐一相符"的断言真正有区分度，
    而不是 N 张一模一样的图蒙混过关）。

记账边界（§7.5）：
    图像调用**不进 token 账**（llm_calls.jsonl）——两者计价单位不同；
    图像账独立写在 `image_generation_calls.jsonl`，由编排层负责（本 Provider 只出图）。
"""
from __future__ import annotations

import base64
import hashlib
import struct
import zlib
from typing import Any, List, Optional

from core.token_log import TokenUsage, new_request_id
from llm.client import LLMRequestError
from llm.providers.base import ImageGenerationProvider, ProviderResult

#: Mock 图片的默认像素尺寸（"最小合法图片"量级；与业务请求的 size 参数无关——
#: size 只作为确定性种子的一部分，Mock 不真的产出 1024×1024 的图，避免测试写大文件）
DEFAULT_PIXEL_SIZE = 16


# ---------------------------------------------------------------------------
# 最小合法 PNG 编码器（纯 stdlib，确定性）
# ---------------------------------------------------------------------------
def _chunk(tag: bytes, data: bytes) -> bytes:
    """PNG chunk：长度 + 类型 + 数据 + CRC32(类型+数据)。"""
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png(seed: bytes, *, width: int = DEFAULT_PIXEL_SIZE,
               height: int = DEFAULT_PIXEL_SIZE) -> bytes:
    """由 seed 确定性地生成一张合法 PNG（8bit RGB，无依赖）。

    像素由 sha256(seed) 派生：同一 seed 必得同一字节序列；不同 seed 必然不同。
    这不是"真实出图"，而是**确定性占位图**——供下游验证"读盘 / sha256 / 落盘"
    链路确实成立；绝不用于冒充真实模型产出（铁律 11）。
    """
    digest = hashlib.sha256(seed).digest()
    raw = bytearray()
    for y in range(height):
        raw.append(0)                     # 每行的 filter type = 0 (None)
        for x in range(width):
            b = digest[(y * width + x) % len(digest)]
            raw.extend((b, (b * 3 + 7) % 256, (b * 7 + 11) % 256))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def png_data_url(png: bytes) -> str:
    """PNG 字节 → `data:image/png;base64,...` 引用串。"""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ---------------------------------------------------------------------------
# MockImageGenerationProvider
# ---------------------------------------------------------------------------
class MockImageGenerationProvider(ImageGenerationProvider):
    """确定性 Mock 文生图（0 网络 / 0 成本 / 可重复）。

    与 `MockTextProvider` 同族：不联网、不打真实账；额外提供 `calls` 观测位，
    让测试能程序化断言"到底发出去了几次生成调用"（预算护栏的硬证据，§十二 T07）。
    """

    vendor = "mock"

    def __init__(self, *, model: str = "mock-image",
                 pixel_size: int = DEFAULT_PIXEL_SIZE,
                 reference_echo: bool = True):
        self.model = str(model or "mock-image")
        self.pixel_size = max(1, int(pixel_size))
        #: 是否把 reference 计入确定性种子（关掉可测"同 prompt 同图"）
        self.reference_echo = bool(reference_echo)
        #: 观测位：每次 generate_image 追加一条（测试断言"实际调用数"）
        self.calls: List[dict] = []

    # ------------------------------------------------------------------
    def generate_image(self, prompt: str, *, size: Optional[str] = None,
                       reference: Optional[str] = None) -> ProviderResult:
        """生成一张确定性图片，返回引用串（data URL）。

        入参校验（本地、不消耗任何预算）：prompt 必须非空。
        """
        text_prompt = str(prompt or "")
        if not text_prompt.strip():
            raise LLMRequestError("MockImageGenerationProvider.generate_image(): prompt 不能为空。")

        self.calls.append({
            "prompt": text_prompt,
            "prompt_length": len(text_prompt),
            "size": str(size or ""),
            "reference": str(reference or ""),
        })

        seed_parts = [text_prompt, str(size or "")]
        if self.reference_echo:
            seed_parts.append(str(reference or ""))
        png = encode_png("|".join(seed_parts).encode("utf-8"),
                         width=self.pixel_size, height=self.pixel_size)
        return ProviderResult(
            text=png_data_url(png),
            model=self.model,
            usage=TokenUsage(),               # 图像无 token 概念（§7.5：不塞进 token 账）
            request_id=new_request_id(),
            duration_ms=1,
            retry_count=0,
        )

    # ------------------------------------------------------------------
    def image_bytes(self, prompt: str, *, size: Optional[str] = None,
                    reference: Optional[str] = None) -> bytes:
        """便捷方法（测试/文档用）：直接拿到 PNG 字节，不经过 data URL。"""
        seed_parts = [str(prompt or ""), str(size or "")]
        if self.reference_echo:
            seed_parts.append(str(reference or ""))
        return encode_png("|".join(seed_parts).encode("utf-8"),
                          width=self.pixel_size, height=self.pixel_size)


__all__ = ["MockImageGenerationProvider", "encode_png", "png_data_url",
           "DEFAULT_PIXEL_SIZE"]
