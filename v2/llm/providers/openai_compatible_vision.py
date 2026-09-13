# -*- coding: utf-8 -*-
"""
V0.2 Stage 10-B —— OpenAI 兼容视觉 Provider（v2/llm/providers/openai_compatible_vision.py）

作用（stage10_plan §13/§22/§25）：第一个"真实视觉"实现类。任何兼容
OpenAI /chat/completions 图文协议的视觉模型（百炼 DashScope 兼容模式的
qwen-vl 系列、其他云厂商、本地 vLLM/Ollama 兼容端点……）都是同一实现类的
不同 Config 档——厂商差异收敛成 Config，不写死任何模型名。

实现策略：复用 Stage2 的 LLMClient 作为执行器（retry / 错误翻译 / TokenLogger
直接继承，不重写第二遍）。本类做四件事：
    1. understand() / chat()：把图片引用转成 OpenAI 兼容 content parts
       （本地文件 → base64 data URL；http(s)/data URL 原样透传）；
    2. **真实请求计数点**（本阶段硬性要求，补充指令 §2/§6/§7/§10/§11）：
       覆盖 _invoke()——每个 HTTP attempt 恰好经过一次，原子完成
       "检查预算 → 计数 +1 → 真正发送"。retry 也计数（按实际 HTTP 请求次数
       消耗预算，不按业务任务数）；本地校验失败（没发 HTTP）不计数；
       预算上限 MAX_REAL_VISION_REQUESTS=3，第 4 次请求被立即拒绝
       （APIBudgetExceededError，不可重试）。
    3. 入参本地校验（图存在 / prompt 非空）全部发生在计数点**之前**；
    4. 审计最小集（timestamp/provider/model/modality/request_id/attempt/is_real）
       进 APIRequestCounter——绝不含 API key、prompt 正文、图片内容；
       token/cost 仍走既有 TokenLogger（与计数完全分离）。

接口约定（providers/base.py，0 改）：image 以"已落盘图片引用 / content 描述"
表达，不在接口层设计二进制传输——路径引用天然满足。
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, List, Optional, Tuple

from core.token_log import new_request_id
from llm.client import LLMClient, LLMRequestError
from llm.providers.base import ProviderResult, VisionProvider, MultimodalProvider
from llm.request_counter import APIRequestCounter, get_api_counter

# 本地图片文件 → MIME（data URL 用；未知后缀按 png 处理，由后端自行容错）
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


class OpenAICompatibleVisionProvider(LLMClient, VisionProvider, MultimodalProvider):
    """OpenAI 兼容图文执行器 + Vision/Multimodal 抽象身份。

    构造参数与 LLMClient 一致；额外可选 vendor / counter。
    构造不联网；网络调用只发生在 understand()/chat() 被调用时，且每次真实
    HTTP 请求都会先经预算检查并计数（含 retry）。

    线程约定：计数器本身线程安全；provider 实例内的 attempt 跟踪按项目现状
    （Agent 单线程串行调用）设计，不承诺同一实例跨线程并发安全。
    """

    kind = "vision"
    vendor = "openai-compatible"     # 子类/工厂覆盖为具体厂商（bailian/...）
    budget_kind = "vision"           # 预算桶（Multimodal 子类改 "multimodal"）

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1.0,
        logger: Optional[object] = None,
        transport=None,
        vendor: Optional[str] = None,
        counter: Optional[APIRequestCounter] = None,
        structured_supported: bool = False,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            logger=logger,
            transport=transport,
        )
        if vendor is not None:
            self.vendor = vendor
        self.provider_name = f"{self.vendor}_{self.kind}"
        self.structured_supported = bool(structured_supported)
        # 计数器：默认进程级单例；测试/真实脚本可注入独立账本
        self.counter = counter or get_api_counter()
        # 业务调用内 attempt 跟踪（每次 understand()/chat() 入口清零）
        self._attempt = 0
        self._business_request_id = ""

    # ------------------------------------------------------------------
    # 业务入口：VisionProvider.understand
    # ------------------------------------------------------------------
    def understand(self, image: Any, prompt: str, *,
                   step: str = "", agent: str = "") -> ProviderResult:
        """一张图 + 一个提示 → 文字理解（提取/问答）。

        本地校验失败（图不存在 / prompt 为空）抛 LLMRequestError——
        发生在计数点之前，不消耗真实请求预算。
        """
        if image is None:
            raise LLMRequestError("understand(): 必须提供 image（路径引用 / URL / data URL）。")
        if not str(prompt or "").strip():
            raise LLMRequestError("understand(): prompt 不能为空。")
        self._begin_business_call()
        messages: List[dict] = [{"role": "user", "content": [
            {"type": "text", "text": str(prompt)},
            self._image_part(image),
        ]}]
        resp = LLMClient.chat(self, messages, step=step, agent=agent)
        return ProviderResult.from_llm_response(resp)

    # ------------------------------------------------------------------
    # 业务入口：MultimodalProvider.chat（图文混合；未来 VLM QA 同协议族复用）
    # ------------------------------------------------------------------
    def chat(self, messages: List[dict], *, images: tuple = (),
             step: str = "", agent: str = "") -> ProviderResult:
        """图文混合对话：images 追加到最后一条 user 消息（无则新建一条）。"""
        if not messages or not images:
            raise LLMRequestError(
                "chat(): 必须同时提供 messages 与 images（与 Mock 同一入参契约）。")
        self._begin_business_call()
        msgs = self._attach_images(messages, images)
        resp = LLMClient.chat(self, msgs, step=step, agent=agent)
        return ProviderResult.from_llm_response(resp)

    # ------------------------------------------------------------------
    # 真实请求计数点（补充指令 §2/§6/§7/§10/§11 的唯一落点）
    # ------------------------------------------------------------------
    def _invoke(self, messages: List[dict], model: str,
                temperature: Optional[float], max_tokens: Optional[int],
                timeout: float, structured: Optional[dict]) -> Tuple[str, Any]:
        """每次真正要发起 HTTP 请求前经过这里（LLMClient.chat 的每个 attempt
        恰好一次）：检查预算 → 计数 +1 → 真正发送。retry 一样计数。"""
        self._attempt += 1
        self.counter.consume_real_request(
            self.budget_kind,
            provider=self.vendor,
            model=model,
            request_id=self._business_request_id,
            attempt=self._attempt,
            note=f"{type(self).__name__}.attempt",
        )
        if (structured is not None and self.structured_supported
                and self._uses_default_transport):
            return self._default_transport(messages, model, temperature, max_tokens,
                                           timeout, structured=structured)
        return self._transport(messages, model, temperature, max_tokens, timeout)

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _begin_business_call(self) -> None:
        """业务调用入口：重置 attempt、生成本次业务调用 ID（串审计用）。"""
        self._attempt = 0
        self._business_request_id = new_request_id()

    def _image_part(self, image: Any) -> dict:
        """图片引用 → OpenAI 兼容 content part（本地文件 base64；URL/data 透传）。"""
        if isinstance(image, (str, Path)):
            s = str(image)
            if s.startswith(("data:", "http://", "https://")):
                return {"type": "image_url", "image_url": {"url": s}}
            path = Path(s)
            if not path.is_file():
                raise LLMRequestError(f"参考图文件不存在: {s}")
            mime = _IMAGE_MIME.get(path.suffix.lower(), "image/png")
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        raise LLMRequestError(
            f"不支持的 image 引用类型: {type(image).__name__}"
            f"（支持 路径 / http(s) URL / data URL）。")

    def _attach_images(self, messages: List[dict], images: tuple) -> List[dict]:
        parts = [self._image_part(img) for img in images]
        msgs = [dict(m) for m in messages]
        for m in reversed(msgs):
            if m.get("role") == "user":
                content = m.get("content")
                if isinstance(content, str):
                    m["content"] = [{"type": "text", "text": content}] + parts
                elif isinstance(content, list):
                    m["content"] = list(content) + parts
                else:
                    m["content"] = parts
                return msgs
        msgs.append({"role": "user", "content": parts})
        return msgs


class OpenAICompatibleMultimodalProvider(OpenAICompatibleVisionProvider):
    """Multimodal 身份的同一实现（协议同族；预算独立桶，默认不限）。"""

    kind = "multimodal"
    budget_kind = "multimodal"


__all__ = ["OpenAICompatibleVisionProvider", "OpenAICompatibleMultimodalProvider"]
