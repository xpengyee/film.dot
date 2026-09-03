# -*- coding: utf-8 -*-
"""
V0.2 阶段 7 —— Scene / Script Generation Agent（v2/agents/scene_generation.py）
（阶段 8 清理：继承 agents/base.py 的 LLMAgent / LLMStep 公共骨架，行为不变）

职责（阶段 7 开工指令 §四 / §五 / §九）：
    根据项目已确认的事实、角色、地点、动作、镜头资产，以及一个明确的场景规范
    （scene specification），由 LLM 生成"单个场景的剧本内容"——谁在哪、
    做什么、说什么、走位、镜头。这是 V0.2 接入的第二个真实业务 Agent。

边界（AGENTS.md / DEVELOPMENT.md，与 Casting Agent 完全一致）：
    - 输入必须来自 ContextAssembler 组装的 AssembledContext，绝不自己读磁盘。
    - LLM 调用必须走 LLMClient（统一错误 / 重试 / token 日志 / 脱敏）。
    - 产出交给 Workflow 引擎落盘（Artifact 版本化，scene_1.json -> scene_1_v2.json …）
      与人工审核（Review）。
    - 不写 Memory、不找 API key、不决定 Workflow 下一步。

复用原则（指令 §八 / §十一 / §二十）：
    - 输出直接复用阶段 1 的 Scene 模型（SceneInfo / Line / Action / Move / PositionRef）。
    - 校验直接复用阶段 1 的三合一 Validator（Enum / Reference / Schema）。
    - 场景规范（scene_spec）由调度层放入 WorkflowContext.inputs['scene_spec']。

约束兜底（指令 §九，确定性规则，Validator 兜底）：
    角色 / 地点 / 动作 / 镜头 / 站位点 只允许用项目已有资产（Validator 拦截），
    Context 只喂 approved_facts（绝不喂 candidate），跨集一致性由角色清单保证。

结构：
    SceneGenerationAgent  业务核心（继承 LLMAgent）：生成单个场景 -> Scene
    SceneGenerationStep   Workflow Step 壳（继承 LLMStep）：声明 needs/needs_review
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import Scene
from core.validator import Validator
from workflow.errors import StepError

from agents.base import LLMAgent, LLMStep


class SceneGenerationAgent(LLMAgent):
    """Scene / Script Generation 业务核心：生成单个场景的结构化剧本。"""

    name = "scene_generation"
    needs = ["project_info", "approved_facts", "characters",
             "locations", "actions", "shots"]

    def __init__(
        self,
        llm_client,
        *,
        scene_id: int = 1,
        validator: Optional[Validator] = None,
        known_characters: Optional[List[str]] = None,
        assets=None,
        prompt_path: Optional[Path] = None,
    ):
        """
        :param llm_client:        LLMClient 实例（统一错误 / 重试 / token 日志）
        :param scene_id:          默认场景编号（scene_spec 未给时兜底；也用于落盘命名 scene_{id}）
        :param validator:         Validator（阶段 1 三合一校验器；缺省按 assets+known_characters 建）
        :param known_characters:  项目 Memory 中已有角色名（校验用；调度层提供）
        :param assets:            AssetRegistry（合法地点/动作/镜头/站位点；校验用；调度层提供）
        :param prompt_path:       V0.2 Scene Generation Prompt 路径
                                  （缺省 v2/prompts/v0.2/scene_generation.txt）
        """
        super().__init__(llm_client, prompt_name="scene_generation", prompt_path=prompt_path)
        self.scene_id = int(scene_id)
        if validator is None:
            if assets is None or known_characters is None:
                raise ValueError(
                    "不传 validator 时必须同时提供 assets（AssetRegistry）与 known_characters"
                )
            validator = Validator(assets, known_characters)
        self.validator = validator
        self.assets = assets
        self.known_characters: List[str] = [str(x) for x in (known_characters or [])]

    # ---------------- 主入口 ----------------
    def run(self, context, *, scene_spec: Optional[dict] = None) -> Scene:
        """根据组装好的 Context + 场景规范，生成一个 Scene。

        :param context:    AssembledContext（由 ContextAssembler 按 needs 组装）
        :param scene_spec: 场景规范 dict（调度层放入 inputs），可含：
                           scene_id / episode / location / characters / objective / time / constraints
        :return: Scene（V0.2 模型；Step 落盘时用 scene.to_v01()）
        :raises StepError: LLM 输出无法解析 / 结构非法（Workflow 会按可重试处理）
        """
        topic = self._topic_from(context)
        facts = self._facts_from(context)
        characters = self._characters_from(context)
        locations = self._locations_from(context)
        actions = self._actions_from(context)
        shots = self._shots_from(context)
        spec = dict(scene_spec or {})

        raw = self.chat_json(
            step="scene_generation",
            user_payload=self._user_payload(
                topic, facts, characters, locations, actions, shots, spec),
        )
        return self._normalize(raw, spec)

    # ---------------- Prompt 构造 ----------------
    def _user_payload(
        self,
        topic: str,
        facts: List[dict],
        characters: List[dict],
        locations: List[dict],
        actions: List[str],
        shots: List[str],
        spec: dict,
    ) -> str:
        facts_text = _json_dumps(facts)
        characters_text = _json_dumps(characters)
        locations_text = _json_dumps(locations)
        actions_text = ", ".join(actions) if actions else "(none)"
        shots_text = ", ".join(shots) if shots else "(none)"
        spec_text = _json_dumps(spec)
        return (
            f"## project topic\n{topic}\n\n"
            f"## approved_facts (ONLY these facts are confirmed)\n{facts_text}\n\n"
            f"## available characters (use ONLY these, by EXACT name)\n{characters_text}\n\n"
            f"## available locations (use ONLY these, with their positions)\n{locations_text}\n\n"
            f"## available actions (use ONLY these)\n{actions_text}\n\n"
            f"## available shots (use ONLY these)\n{shots_text}\n\n"
            f"## scene specification (write this scene)\n{spec_text}"
        )

    # ---------------- 从 Context 取数据 ----------------
    @staticmethod
    def _topic_from(context) -> str:
        item = context.item("project_info")
        if item and isinstance(item.data, dict):
            return str(item.data.get("project_title", ""))
        return ""

    @staticmethod
    def _facts_from(context) -> List[dict]:
        item = context.item("approved_facts")
        if not item:
            return []
        return [dict(f) if isinstance(f, dict) else {"content": str(f)} for f in (item.data or [])]

    @staticmethod
    def _characters_from(context) -> List[dict]:
        item = context.item("characters")
        if not item:
            return []
        return [dict(c) if isinstance(c, dict) else {"name": str(c)} for c in (item.data or [])]

    @staticmethod
    def _locations_from(context) -> List[dict]:
        item = context.item("locations")
        if not item:
            return []
        return [dict(l) if isinstance(l, dict) else {"name": str(l)} for l in (item.data or [])]

    @staticmethod
    def _actions_from(context) -> List[str]:
        item = context.item("actions")
        if not item:
            return []
        return [str(a) for a in (item.data or [])]

    @staticmethod
    def _shots_from(context) -> List[str]:
        item = context.item("shots")
        if not item:
            return []
        return [str(s) for s in (item.data or [])]

    # ---------------- 规范化 ----------------
    def _normalize(self, raw: dict, spec: dict) -> Scene:
        """把 LLM 原始 dict 规范化成一个 V0.2 Scene。

        确定性规则（不做内容改写，只做格式规范化）：
            1. 结构检查：scene 行列表必须存在且是 list，否则抛 StepError（可重试）。
            2. who / where 兜底：LLM 没给时，用场景规范 scene_spec 的
               characters / location 补齐（场景规范是调度层的权威输入）。
            3. 内容合法性（角色/地点/动作/镜头/位置）交给 Validator 兜底。
        """
        scene_id = int(spec.get("scene_id") or self.scene_id)

        # 认识 V0.1 的三种字段写法（scene information / scene_information / sceneinformation）
        info = None
        for key in ("scene information", "scene_information", "sceneinformation"):
            if isinstance(raw.get(key), dict):
                info = raw[key]
                break
        info = info or {}
        who = info.get("who")
        where = info.get("where")
        what = info.get("what", "")

        # who / where 兜底：场景规范优先于 LLM 的自作主张
        if not where:
            where = str(spec.get("location") or "").strip()
        if not who:
            who = [c for c in (spec.get("characters") or []) if c]

        lines = raw.get("scene")
        if not isinstance(lines, list):
            raise StepError("Scene LLM 输出缺少合法的 scene 行列表（必须是 list）")

        norm: Dict[str, Any] = dict(raw)
        norm["scene information"] = {
            "who": list(who) if who else [],
            "where": str(where) if where else "",
            "what": str(what) if what else "",
        }
        return Scene.from_v01(norm, id=scene_id)


class SceneGenerationStep(LLMStep):
    """把 SceneGenerationAgent 接入 Workflow 引擎的 Step 壳。

    run()      从 context.inputs 取 AssembledContext + scene_spec，交给 Agent，
               返回结构化产出（Scene.to_v01() dict），由引擎落盘 scene_{scene_id}.json。
    validate() 用阶段 1 的 Validator 做确定性校验；不合格 -> 引擎重试（旧版本保留）。
    needs_review = True：成功后进入人工审核节点（approved / approved_with_edit / rejected）。
    """

    step_id = "scene_generation"
    name = "scene_generation"
    needs = SceneGenerationAgent.needs
    needs_review = True

    def __init__(
        self,
        agent: SceneGenerationAgent,
        *,
        scene_id: Optional[int] = None,
        step_id: Optional[str] = None,
        needs_review: Optional[bool] = None,
    ):
        self.scene_id = int(scene_id) if scene_id is not None else 1
        # step_id 带场景编号：多场景在同一 workflow 里是各自独立的 step（resume 不串场）
        super().__init__(
            agent=agent,
            step_id=step_id or f"scene_generation_{self.scene_id}",
            name=self.name,
            needs=list(self.needs),
            needs_review=needs_review,
        )

    def run(self, context):
        assembled = self.assembled_context(context)
        scene_spec = context.inputs.get("scene_spec") or {}
        scene = self.agent.run(assembled, scene_spec=scene_spec)
        return self.make_output(
            data=scene.to_v01(),
            artifact_name=f"scene_{self.scene_id}",
            metadata={
                "scene_id": self.scene_id,
                "episode": int(scene_spec.get("episode") or 1),
                "agent": self.name,
            },
        )

    def validate(self, data: Any, context) -> List[str]:
        """确定性校验：落盘内容转回 Scene 后用 Validator 校验（错误转字符串列表）。"""
        try:
            scene = Scene.from_v01(data, id=self.scene_id)
        except Exception as exc:  # 落盘内容无法解析：明确报错，交给引擎重试
            return [f"无法把落盘内容解析成 Scene: {exc}"]
        errors = self.agent.validator.validate(scene)
        return [str(e) for e in errors]


def _json_dumps(obj, *, fallback: str = "(none)") -> str:
    """把可序列化对象转成 JSON 文本；空对象返回 fallback（保持与历史 prompt 一致）。"""
    if not obj:
        return fallback
    return json.dumps(obj, ensure_ascii=False, indent=2)


__all__ = ["SceneGenerationAgent", "SceneGenerationStep"]
