# -*- coding: utf-8 -*-
"""
V0.2 阶段 7 —— Scene / Script Generation Agent（v2/agents/scene_generation.py）

职责（阶段 7 开工指令 §四 / §五 / §九）：
    根据项目已确认的事实、角色、地点、动作、镜头资产，以及一个明确的场景规范
    （scene specification），由 LLM 生成"单个场景的剧本内容"——谁在哪、
    做什么、说什么、走位、镜头。这是 V0.2 接入的第二个真实业务 Agent，
    解决的是"谁 / 在哪里 / 什么时候 / 做什么 / 说什么 / 为什么做"。

边界（AGENTS.md / DEVELOPMENT.md，与 Casting Agent 完全一致）：
    - 输入必须来自 ContextAssembler 组装的 AssembledContext，绝不自己读磁盘。
    - LLM 调用必须走 LLMClient（统一错误 / 重试 / token 日志 / 脱敏）。
    - 产出交给 Workflow 引擎落盘（Artifact 版本化，scene_1.json -> scene_1_v2.json …）
      与人工审核（Review）。
    - 不写 Memory、不找 API key、不决定 Workflow 下一步。

复用原则（指令 §八 / §十一 / §二十）：
    - 输出直接复用阶段 1 的 Scene 模型（SceneInfo / Line / Action / Move / PositionRef），
      不新增平行数据结构，models.py 无需扩展。
    - 校验直接复用阶段 1 的三合一 Validator（Enum / Reference / Schema），
      不新建平行 Validator，validator.py 无需扩展。
    - 场景规范（scene_spec）由调度层放入 WorkflowContext.inputs['scene_spec']，
      ContextAssembler 本身不改。

约束兜底（指令 §九，确定性规则，Validator 兜底）：
    - 角色约束：who / speaker / action.character 只能是项目已有角色（UNKNOWN_CHARACTER）。
    - 地点约束：where 只能是资产注册表里的合法地点（INVALID_LOCATION）。
    - 事实约束：Context 只喂 approved_facts，绝不喂 candidate。
    - 跨集一致性：角色清单来自 Memory，LLM 只能引用，不能新建档案。
    - 资产约束：动作 / 镜头 / 站位点 / 走位目的地必须来自 Assets（Validator 拦截）。

结构：
    SceneGenerationAgent  业务核心：组装消息 -> 调 LLM -> 解析 -> 规范化 -> Scene
    SceneGenerationStep   Workflow Step 壳：声明 needs/needs_review，接进引擎跑
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import V2_ROOT
from core.models import Scene
from core.validator import Validator
from workflow.errors import StepError
from workflow.models import StepOutput, now_iso
from workflow.step import Step


class SceneGenerationAgent:
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
        self.llm_client = llm_client
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
        self.prompt_path = Path(prompt_path) if prompt_path else V2_ROOT / "prompts" / "v0.2" / "scene_generation.txt"

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

        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._user_payload(
                topic, facts, characters, locations, actions, shots, spec)},
        ]
        response = self.llm_client.chat(messages, step="scene_generation", agent=self.name)

        raw = self._parse_json(response.text)
        if not isinstance(raw, dict):
            raise StepError(f"Scene LLM 输出不是 JSON 对象: {type(raw).__name__}")
        return self._normalize(raw, spec)

    # ---------------- Prompt 构造 ----------------
    def _system_prompt(self) -> str:
        try:
            return self.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 防御：prompt 缺失应明确报错
            raise StepError(f"读取 Scene Generation Prompt 失败 {self.prompt_path}: {exc}") from exc

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
        facts_text = json.dumps(facts, ensure_ascii=False, indent=2) if facts else "(none)"
        characters_text = json.dumps(characters, ensure_ascii=False, indent=2) if characters else "(none)"
        locations_text = json.dumps(locations, ensure_ascii=False, indent=2) if locations else "(none)"
        actions_text = ", ".join(actions) if actions else "(none)"
        shots_text = ", ".join(shots) if shots else "(none)"
        spec_text = json.dumps(spec, ensure_ascii=False, indent=2) if spec else "(none)"
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

    # ---------------- 解析与规范化 ----------------
    def _parse_json(self, text: str) -> Any:
        text = (text or "").strip()
        # 最小容错：LLM 偶尔用 ```json ... ``` 包裹输出，剥掉代码围栏
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StepError(f"Scene LLM 输出不是合法 JSON: {exc}") from exc

    def _normalize(self, raw: dict, spec: dict) -> Scene:
        """把 LLM 原始 dict 规范化成一个 V0.2 Scene。

        确定性规则（不做内容改写，只做格式规范化）：
            1. 结构检查：scene 行列表必须存在且是 list，否则抛 StepError（可重试）。
            2. who / where 兜底：LLM 没给时，用场景规范 scene_spec 的
               characters / location 补齐（场景规范是调度层的权威输入）。
            3. 内容合法性（角色/地点/动作/镜头/位置）交给 Validator 兜底，
               Agent 不替 LLM 编剧。
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


class SceneGenerationStep(Step):
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
            step_id=step_id or f"scene_generation_{self.scene_id}",
            name=self.name,
            needs=list(self.needs),
            needs_review=needs_review if needs_review is not None else self.needs_review,
        )
        if agent is None:
            raise ValueError("SceneGenerationStep 必须注入 SceneGenerationAgent")
        self.agent = agent

    def run(self, context):
        assembled = context.inputs.get("assembled_context")
        if assembled is None:
            raise StepError(
                "调度层必须先调用 ContextAssembler.assemble()，并把结果放进 "
                "WorkflowContext.inputs['assembled_context']"
            )
        scene_spec = context.inputs.get("scene_spec") or {}
        scene = self.agent.run(assembled, scene_spec=scene_spec)
        return StepOutput(
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


__all__ = ["SceneGenerationAgent", "SceneGenerationStep"]
