# -*- coding: utf-8 -*-
"""
V0.2 Stage 10 —— Visual Asset Agent（v2/agents/visual_asset.py）

职责（stage10_plan §13/§22）：把"视觉档案的产出"接成 Agent + Step：
    plan 模式    语义数据（Casting 档案/剧本要点）→ 结构化 Visual Profile
                 —— 走 TextProvider（chat_json，与 Casting 同一骨架）
    extract 模式 参考图 + 语义锚点 → 结构化 Visual Profile
                 —— 走 VisionProvider.understand（真实调用受计数器预算硬上限保护）

两种模式的规范化都是**确定性代码**（normalize_profile_fields）：
    - 只保留该 kind 的已知字段（LLM/VLM 多说的键直接丢弃）；
    - 值统一 str + strip，缺失 → 空串（空 = 未指定，不编造）；
    - 0 次额外文本 API 调用（stage10_plan §19：不让文本侧消耗预算）。

边界（与既有 Agent 完全一致）：
    - 输入来自 WorkflowContext.inputs['visual_asset_spec']（调度层放好，
      Stage 7 scene_spec 同一模式，不加 needs key、不改 ContextAssembler）；
    - LLM/Vision 调用只走注入的 Provider（测试全 Mock，0 API）；
    - 产出只交 StepOutput（data = 版本快照 dict）；落盘/审批/写回 registry
      由接线层负责——Agent 不读写磁盘、不碰 Memory、不自己发真实请求计数。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import V2_ROOT
from core.validator import VisualAssetValidator, format_errors
from core.visual import (
    VISUAL_ASSET_KINDS, PROFILE_CLASSES, normalize_profile_fields,
)
from workflow.errors import StepError
from workflow.models import now_iso

from agents.base import LLMAgent, LLMStep


# 各 kind 的字段契约文本（prompt 里的 field_contract 占位符；确定性生成，不调 LLM）
def field_contract_for(kind: str) -> str:
    cls = PROFILE_CLASSES.get(kind)
    if cls is None:
        return "(reference assets have no profile structure)"
    lines = []
    for name, required in cls.FIELD_ORDER:
        lines.append(f"- {name} ({'required' if required else 'optional'}): "
                     f"{_FIELD_HINTS.get(name, 'short visual description')}")
    return "\n".join(lines)


_FIELD_HINTS = {
    "appearance": "overall visual read of the subject, one sentence",
    "face": "face shape, features, skin tone",
    "hair": "hair style and color",
    "clothing": "default costume / outfit of this production",
    "body": "build and height impression",
    "color_palette": "dominant colors associated with this subject",
    "distinguishing_features": "signature visual marks: glasses, scar, jewelry",
    "visual_style": "style note for this subject only; usually empty",
    "negative_cues": "elements that must NOT appear; semicolon separated; empty if none",
    "environment": "interior/exterior, mood, era feel",
    "lighting": "base lighting setup: sources, direction, warm/cool",
    "palette": "color tone of the place",
    "architecture": "building / spatial structure features",
    "time_of_day": "default time of day; shots may override",
    "form": "physical shape of the object",
    "color": "main color of the object",
    "material": "material / surface finish",
    "size": "relative scale",
    "condition": "wear / state",
    "medium": "2D anime / 3D / realistic / cel shading",
    "rendering": "rendering, line and coloring traits",
    "lighting_style": "light look tendency",
    "camera_style": "camera language tendency",
    "notes": "anything else worth keeping",
}


class VisualAssetAgent(LLMAgent):
    """视觉档案业务核心：plan（文本规划）/ extract（视觉提取）两模式。

    :param llm_client:   TextProvider（plan 模式用；extract 模式不需要但保持注入一致）
    :param vision:       VisionProvider（extract 模式用；None 时 extract 明确报错）
    :param validator:    VisualAssetValidator（缺省新建）
    """

    name = "visual_asset"
    # needs 为空：输入走 inputs['visual_asset_spec']（scene_spec 模式），
    # 不新增 ContextAssembler 需求键（stage10_plan §12）
    needs: List[str] = []

    def __init__(
        self,
        llm_client=None,
        *,
        vision=None,
        validator: Optional[VisualAssetValidator] = None,
        plan_prompt_path: Optional[Path] = None,
        extract_prompt_path: Optional[Path] = None,
    ):
        super().__init__(llm_client, prompt_name="visual_asset_plan",
                         prompt_path=plan_prompt_path)
        self.vision = vision
        self.validator = validator or VisualAssetValidator()
        self.extract_prompt_path = (extract_prompt_path
                                    or V2_ROOT / "prompts" / "v0.2" / "visual_asset_extract.txt")

    # ------------------------------------------------------------------
    # 两个业务入口
    # ------------------------------------------------------------------
    def run_plan(self, *, kind: str, name: str, semantic_data: Any) -> dict:
        """plan 模式：语义数据 → 结构化 profile（走 TextProvider.chat_json）。"""
        self._check_kind(kind)
        self._require_prompt(self.extract_prompt_path)   # 两个模板都要求存在（缺一报错）
        raw = self.chat_json(
            step="visual_asset_plan",
            user_payload=self._plan_payload(kind, name, semantic_data),
        )
        return self.normalize(kind, raw, source="text_planned")

    def run_extract(self, *, kind: str, name: str, image: str,
                    semantic_data: Any = None) -> dict:
        """extract 模式：参考图 → 结构化 profile（走 VisionProvider.understand）。

        :param image: 参考图引用（项目内路径 / URL / data URL——Provider 契约）
        真实 Provider 的每次 HTTP（含 retry）都经计数点并消耗预算（≤3）；
        Mock 全程 0 API。
        """
        self._check_kind(kind)
        if self.vision is None:
            raise StepError("extract 模式需要注入 VisionProvider（当前为 None）。")
        try:
            prompt = self.extract_prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StepError(f"读取 Prompt 失败 {self.extract_prompt_path}: {exc}") from exc
        prompt = prompt.replace("{kind}", kind).replace("{name}", name)
        prompt = prompt.replace("{semantic_data}", _dumps(semantic_data))
        prompt = prompt.replace("{field_contract}", field_contract_for(kind))
        resp = self.vision.understand(image, prompt,
                                      step="visual_asset_extract", agent=self.name)
        raw = self._parse_json(resp.text)
        if not isinstance(raw, dict):
            raise StepError(f"{self.name} Vision 输出不是 JSON 对象: {type(raw).__name__}")
        return self.normalize(kind, raw, source="vision_extracted")

    # ------------------------------------------------------------------
    # 确定性规范化（stage10_plan §19：0 次文本 API，全部本地规则）
    # ------------------------------------------------------------------
    def normalize(self, kind: str, raw: Any, *, source: str) -> dict:
        """LLM/VLM 原始 dict → 版本快照 dict（供 Step 落 artifact / 接线层写 registry）。

        只保留已知字段、值 str+strip、缺失空串；产出结构：
            {"kind", "source", "profile", "visual_prompt", "created_at",
             "complete"(必填字段是否齐全，送审门槛)}
        """
        profile = normalize_profile_fields(kind, raw)
        prompt_bits = [f"{k}: {v}" for k, v in profile.items() if v]
        cls = PROFILE_CLASSES.get(kind)
        complete = True if cls is None else not cls.from_dict(profile).missing_required()
        return {
            "kind": kind,
            "source": source,
            "profile": profile,
            "visual_prompt": "; ".join(prompt_bits[:6]),
            "created_at": now_iso(),
            "complete": complete,
        }

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in VISUAL_ASSET_KINDS:
            raise StepError(f"未知视觉资产 kind: {kind!r}（支持 {list(VISUAL_ASSET_KINDS)}）")

    def _plan_payload(self, kind: str, name: str, semantic_data: Any) -> str:
        """plan 模式 user 消息。system 模板的 {kind}/{name}/{semantic_data}/
        {field_contract} 占位符替换结果缓存在 _plan_system_cache（观测位，
        chat_json 仍用原始模板——LLM 需要完整契约上下文）。"""
        system = self.system_prompt()
        self._plan_system_cache = (system
                                   .replace("{kind}", kind)
                                   .replace("{name}", name)
                                   .replace("{semantic_data}", _dumps(semantic_data))
                                   .replace("{field_contract}", field_contract_for(kind)))
        return (
            f"Plan the visual profile for this asset now.\n\n"
            f"kind: {kind}\nname: {name}\n"
            f"semantic_data: {_dumps(semantic_data)}\n"
            f"field_contract:\n{field_contract_for(kind)}"
        )

    @staticmethod
    def _require_prompt(path: Path) -> None:
        if not path.is_file():
            raise StepError(f"Prompt 文件缺失: {path}")


def _dumps(data: Any, fallback: str = "(none)") -> str:
    if data in (None, "", [], {}):
        return fallback
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(data)


class VisualAssetStep(LLMStep):
    """Visual Asset 的 Workflow Step 壳（scene_spec 模式：inputs['visual_asset_spec']）。

    spec 形状（调度层放好）：
        {
          "mode": "plan" | "extract",
          "kind": "character" | ...,
          "name": "Mercy",
          "image": "visual_assets/images/mercy_a.png",   # extract 必填
          "semantic_data": {...},                        # 可选
        }
    产出 data = agent.normalize 的版本快照（+name）。
    """

    step_id = "visual_asset"
    name = "visual_asset"
    needs: List[str] = []
    needs_review = True

    def __init__(self, agent: VisualAssetAgent, *, step_id: Optional[str] = None,
                 name: Optional[str] = None):
        super().__init__(agent, step_id=step_id, name=name)

    def run(self, context):
        spec = context.inputs.get("visual_asset_spec")
        if not isinstance(spec, dict):
            raise StepError(
                "调度层必须把 visual_asset_spec 放进 WorkflowContext.inputs"
                "（Stage 7 scene_spec 同一模式）。")
        mode = str(spec.get("mode", "")).lower()
        kind = str(spec.get("kind", "") or "")
        name = str(spec.get("name", "") or "")
        if not kind or not name:
            raise StepError(f"visual_asset_spec 缺 kind/name: {spec!r}")
        if mode == "plan":
            snapshot = self.agent.run_plan(kind=kind, name=name,
                                           semantic_data=spec.get("semantic_data"))
        elif mode == "extract":
            image = str(spec.get("image", "") or "")
            if not image:
                raise StepError("extract 模式 visual_asset_spec 必须带 image。")
            snapshot = self.agent.run_extract(kind=kind, name=name, image=image,
                                              semantic_data=spec.get("semantic_data"))
        else:
            raise StepError(f"visual_asset_spec.mode 非法: {mode!r}（plan/extract）")
        snapshot["name"] = name
        artifact = f"visual_asset_{kind}_{name}".replace(" ", "_")
        return self.make_output(
            snapshot,
            artifact_name=artifact,
            metadata={"mode": mode, "kind": kind, "name": name},
        )

    def validate(self, data, context) -> List[str]:
        if not isinstance(data, dict):
            return ["产出必须是 dict（版本快照）"]
        errors = self.agent.validator.validate_profile_dict(
            str(data.get("kind", "")), data.get("profile") or {},
            require_complete=True, path="data.profile")
        return [format_errors(errors)] if errors else []


__all__ = ["VisualAssetAgent", "VisualAssetStep", "field_contract_for"]
