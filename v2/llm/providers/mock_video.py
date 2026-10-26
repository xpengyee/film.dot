# -*- coding: utf-8 -*-
"""
V0.2 Stage 15 —— Mock 图生视频 Provider（v2/llm/providers/mock_video.py）

作用（Stage 15 §15-B2）：
    让 `kind="video"` 在缺省（`FILMAGENT_PROVIDER=mock`）下走通完整异步链路：
        submit_video → 稳定 task_id → get_video_task 轮询 → 下载 → 落盘 → manifest
    且 **0 网络、0 成本、确定性、可重复**。

设计约束（§15-B2）：
    1. 完全离线、确定性：同 (prompt, media, duration, resolution, seed) → 稳定 task_id。
    2. submit 产生稳定 task_id（由确定性输入派生，绝不用随机/时钟——保证崩溃恢复后
       resume 能重新定位到"同一个任务"，而不会误认为换了任务）。
    3. poll 可脚本化 pending/running/succeeded/failed/unknown，测试可逐状态断言。
    4. 成功结果由 `video_bytes()` 返回确定性最小 MP4-like fixture：
       至少含合法 ISO BMFF `ftyp` 签名，明确标记 Mock，不能冒充真实生成。
    5. 记录 calls，便于断言顺序与数量（提交次数 / 轮询次数）。

诚实边界（铁律 11）：
    Mock 产出的"视频"是**确定性占位 MP4-like fixture**，不是真实视频画面；
    绝不在 manifest / 报告里把它描述成真实模型产出。fixture 在 `ftyp` 后带
    `mvck` 品牌 + 明确 MOCK 标记，任何真实校验器都能识别出这是测试占位。
"""
from __future__ import annotations

import hashlib
import struct
from typing import Any, Dict, List, Optional

from core.token_log import new_request_id
from llm.client import LLMRequestError
from llm.providers.base import VideoGenerationProvider, VideoTaskResult

#: 确定性 task_id 前缀（与真实 task_id 形态区分，一眼可辨是 Mock）
TASK_ID_PREFIX = "mockvid-"

#: MP4 fixture 尺寸常量（盒长度用，非真实视频分辨率）
_FIXTURE_BOX_SIZE = 16


def stable_task_id(*parts: Any) -> str:
    """由确定性输入派生稳定 task_id（崩溃恢复后 resume 仍能命中同一任务）。

    与真实厂商的随机 task_id 不同，Mock 的 task_id 是纯函数：
    同输入 → 同 task_id，使测试能断言"resume 不会重新 submit"。
    """
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return TASK_ID_PREFIX + h.hexdigest()[:24]


def encode_mp4_fixture(seed: bytes) -> bytes:
    """由 seed 确定性生成一个最小 MP4-like fixture（含合法 `ftyp` 签名 + MOCK 标记）。

    结构（ISO BMFF 最小合法形态）：
        ftyp box  major_brand='mvck'  minor_version=0
        free box  body  = 'FILMAGENT-MOCK-VIDEO' + sha256(seed)[:8]（明确占位，非真实画面）

    注意：这不是可播放的真实 MP4（没有 moov/mdat 媒体轨）；它只保证
    "容器签名 + 长度" 能被下游 `_verify_video_bytes` 识别为 MP4 形态，
    用于验证「下载 → 校验 → 落盘 → sha256」链路确实成立。绝不冒充真实生成。
    """
    major_brand = b"mvck"                      # Mock Video Container Kind
    # ftyp box: size(4) + type(4) + major_brand(4) + minor_version(4)
    ftyp_payload = major_brand + struct.pack(">I", 0)
    ftyp = struct.pack(">I", 8 + len(ftyp_payload)) + b"ftyp" + ftyp_payload

    marker = b"FILMAGENT-MOCK-VIDEO:" + hashlib.sha256(seed).digest()[:8]
    # free box（存放 MOCK 标记，明确非真实画面）
    free = struct.pack(">I", 8 + len(marker)) + b"free" + marker

    return ftyp + free


def _mp4_signature(data: bytes) -> bool:
    """最小 MP4 形态校验：非空 + 以 'ftyp' box 开头（4..7 字节为 'ftyp'）。"""
    return len(data) >= 8 and data[4:8] == b"ftyp"


class MockVideoProvider(VideoGenerationProvider):
    """确定性 Mock 图生视频（0 网络 / 0 成本 / 可脚本化轮询状态）。

    状态脚本：通过 `script` 传入轮询状态序列，每次 `get_video_task` 消费一个；
    耗尽后停留在最后一个终态（若末项非终态则升格为 succeeded）。
    无脚本时，submit 后首次 poll 直接 succeeded（最小可用默认）。
    """

    vendor = "mock"
    kind = "video"

    def __init__(self, *, model: str = "mock-video", script: Optional[List[str]] = None):
        self.model = str(model or "mock-video")
        #: 轮询状态脚本（list[str]）：每次 get_video_task 依序消费
        self.script: Optional[List[str]] = script
        self._script_cursor = 0
        #: 观测位：提交与轮询的调用记录（测试断言顺序与数量）
        self.calls: List[dict] = []
        #: task_id → 已提交任务的内存登记（含最终状态，供 poll 回答）
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._poll_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def submit_video(
        self,
        prompt: str,
        *,
        media: Any,
        duration: float,
        resolution: str,
        negative_prompt: str = "",
        prompt_extend: bool = False,
        watermark: bool = False,
        seed: Optional[int] = None,
    ) -> VideoTaskResult:
        text_prompt = str(prompt or "")
        if not text_prompt.strip():
            raise LLMRequestError(
                "MockVideoProvider.submit_video(): prompt 不能为空。")
        if not str(media or ""):
            raise LLMRequestError(
                "MockVideoProvider.submit_video(): 首帧 media 不能为空。")

        task_id = stable_task_id(
            text_prompt, str(media), float(duration), str(resolution),
            str(negative_prompt), bool(prompt_extend), bool(watermark),
            "" if seed is None else int(seed))

        self.calls.append({
            "method": "submit_video",
            "task_id": task_id,
            "prompt_length": len(text_prompt),
            "duration": float(duration),
            "resolution": str(resolution),
            "prompt_extend": bool(prompt_extend),
            "watermark": bool(watermark),
            "seed": None if seed is None else int(seed),
        })
        # 记录为已提交；首态 pending（真实 Provider 提交后亦非立即终态）
        self._tasks[task_id] = {
            "status": "pending",
            "duration": float(duration),
            "resolution": str(resolution),
            "prompt": text_prompt,
            "seed": seed,
        }
        self._poll_counts[task_id] = 0
        return VideoTaskResult(
            task_id=task_id,
            status="pending",
            model=self.model,
            request_id=new_request_id(),
            duration_ms=1,
            retry_count=0,
            usage={},
        )

    # ------------------------------------------------------------------
    def get_video_task(self, task_id: str) -> VideoTaskResult:
        task_id = str(task_id or "")
        # task_id 是确定性派生的纯函数（stable_task_id），因此跨实例/跨进程也能识别：
        #   合法 mockvid- 前缀 → 视为"已提交过的任务"，按脚本返回状态（支持崩溃恢复）；
        #   空 / 非法前缀 → 未知任务（禁止自动重提，D15-03）。
        if not task_id:
            raise LLMRequestError(
                "MockVideoProvider.get_video_task(): task_id 为空。")
        if not task_id.startswith(TASK_ID_PREFIX):
            raise LLMRequestError(
                f"MockVideoProvider.get_video_task(): 未知 task_id（{task_id!r}），"
                f"可能已过期或从未提交。")

        if task_id not in self._poll_counts:
            self._poll_counts[task_id] = 0
        self._poll_counts[task_id] += 1
        poll_n = self._poll_counts[task_id]

        # 状态脚本：有脚本按序消费；无脚本首 poll 即 succeeded
        status = "succeeded"
        if self.script is not None:
            idx = poll_n - 1
            if idx < len(self.script):
                status = str(self.script[idx])
            else:
                # 脚本耗尽：停在最后脚本状态；若末项非终态则升格 succeeded
                last = str(self.script[-1])
                status = last if last in (
                    "succeeded", "failed", "canceled", "unknown") else "succeeded"

        # 非法状态名归一化为 unknown（严格终态解析，D15-03）
        if status not in ("submitted", "pending", "running",
                          "succeeded", "failed", "canceled", "unknown"):
            status = "unknown"

        self.calls.append({
            "method": "get_video_task",
            "task_id": task_id,
            "status": status,
        })
        if task_id in self._tasks:
            self._tasks[task_id]["status"] = status

        result = VideoTaskResult(
            task_id=task_id,
            status=status,
            model=self.model,
            request_id=new_request_id(),
            duration_ms=1,
            retry_count=0,
            usage={},
        )
        if status == "succeeded":
            # 成功 URL：Mock 用受控测试 scheme（§15-B8：可通过 Config 允许测试 scheme）
            result.video_url = f"mock://video/{task_id}.mp4"
        elif status == "failed":
            result.error_code = "MOCK_FAILED"
            result.error_message = "Mock 脚本化失败（确定性，非真实厂商错误）"
        elif status == "canceled":
            result.error_code = "MOCK_CANCELED"
            result.error_message = "Mock 脚本化取消（确定性）"
        elif status == "unknown":
            result.error_code = "MOCK_UNKNOWN"
            result.error_message = "Mock 脚本化 unknown（确定性）"
        return result

    # ------------------------------------------------------------------
    def video_bytes(self, task_id: str) -> bytes:
        """便捷方法：按 task_id 派生确定性 MP4-like fixture 字节（测试/下载校验用）。"""
        task_id = str(task_id or "")
        return encode_mp4_fixture(("|".join([task_id])).encode("utf-8"))

    # ------------------------------------------------------------------
    # 兼容便捷接口（VideoStage 不使用）
    # ------------------------------------------------------------------
    def generate_video(self, prompt: str, *, shots: Optional[list] = None,
                       duration: Optional[float] = None):
        raise NotImplementedError(
            "MockVideoProvider 使用异步契约 submit_video()/get_video_task()，"
            "不支持同步 generate_video()。"
        )


__all__ = [
    "MockVideoProvider",
    "stable_task_id",
    "encode_mp4_fixture",
    "TASK_ID_PREFIX",
]
