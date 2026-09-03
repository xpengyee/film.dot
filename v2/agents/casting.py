# -*- coding: utf-8 -*-
"""
V0.2 阶段 6 —— Casting Agent（v2/agents/casting.py）
（阶段 8 清理：继承 agents/base.py 的 LLMAgent / LLMStep 公共骨架，行为不变）

职责（阶段 6 开工指令 §三 / §四 / §五）：
    根据项目已提供的剧情上下文 / 已确认事实 / 角色信息，生成本集需要的 Casting
    结果：哪些角色登场、已有角色直接复用、新角色建档。

边界（AGENTS.md / DEVELOPMENT.md）：
    - 输入必须来自 ContextAssembler 组装的 AssembledContext，绝不自己读磁盘。
    - LLM 调用必须走 LLMClient（统一错误 / 重试 / token 日志 / 脱敏）。
    - 产出交给 Workflow 引擎落盘（Artifact 版本化）与人工审核（Review）。
    - 不写 Memory、不找 API key、不决定 Workflow 下一步。

跨集决策（阶段 1 锁定，指令 §七）：
    老角色直接复用（引用已有 name，不新建档案）；只给新角色建档。
    existing / new 的判定是确定性规则：
        名字命中已有角色 -> existing（忽略 LLM 自创档案，防改档）
        否则             -> new   （必须带完整档案）
    CastingValidator 再兜底检查：existing 引用合法、new 不重名、字段齐全。

结构：
    CastingAgent  业务核心（继承 LLMAgent）：组装消息 -> chat_json -> 规范化 -> CastingResult
    CastingStep   Workflow Step 壳（继承 LLMStep）：声明 needs/needs_review，接进引擎跑
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from core.models import Character, CastingEntry, CastingResult
from core.validator import CastingValidator, format_errors
from workflow.errors import StepError
from workflow.models import now_iso

from agents.base import LLMAgent, LLMStep


class CastingAgent(LLMAgent):
    """Casting 业务核心：根据组装好的 Context 提出本集 Casting 结果。"""

    name = "casting"
    needs = ["project_info", "approved_facts", "characters", "world"]

    def __init__(
        self,
        llm_client,
        *,
        known_characters: Optional[List[str]] = None,
        validator: Optional[CastingValidator] = None,
        prompt_path: Optional[Path] = None,
        character_limit: int = 4,
    ):
        """
        :param llm_client:        LLMClient 实例（统一错误 / 重试 / token 日志）
        :param known_characters:  项目 Memory 中已有的角色名（复用判定 + 校验用）
        :param validator:         CastingValidator（缺省按 known_characters 建）
        :param prompt_path:       V0.2 Casting Prompt 路径（缺省 v2/prompts/v0.2/casting.txt）
        :param character_limit:   本集最多主要角色数（沿用 V0.1 的 4）
        """
        super().__init__(llm_client, prompt_name="casting", prompt_path=prompt_path)
        self.known_characters: List[str] = [str(x) for x in (known_characters or [])]
        self.validator = validator or CastingValidator(self.known_characters)
        self.character_limit = character_limit

    # ---------------- 主入口 ----------------
    def run(self, context) -> CastingResult:
        """根据组装好的 Context 提出本次 Casting 结果。

        :param context: AssembledContext（由 ContextAssembler 按 needs 组装）
        :return: CastingResult（规范化后：已有角色 -> existing，新角色 -> new）
        :raises StepError: LLM 输出无法解析 / 结构非法（Workflow 会按可重试处理）
        """
        topic = self._topic_from(context)
        existing = self._existing_characters_from(context)
        facts = self._facts_from(context)
        world = self._world_from(context)

        raw = self.chat_json(
            step="casting",
            user_payload=self._user_payload(topic, existing, facts, world),
        )
        entries = self._normalize(raw)
        result = CastingResult(
            project_id=context.project_id,
            episode=int(raw.get("episode") or 1),
            topic=str(raw.get("topic") or topic),
            entries=entries,
            created_at=now_iso(),
        )
        return result

    # ---------------- Prompt 构造 ----------------
    def _user_payload(self, topic: str, existing: List[str], facts: List[dict], world: dict) -> str:
        existing_text = ", ".join(existing) if existing else "(none yet - this is the first episode)"
        facts_text = _json_dumps(facts, fallback="(none)")
        world_text = _json_dumps(world, fallback="(empty)")
        return (
            f"topic: {topic}\n\n"
            f"existing_characters: {existing_text}\n\n"
            f"approved_facts: {facts_text}\n\n"
            f"world: {world_text}\n\n"
            f"character_limit: {self.character_limit}"
        )

    # ---------------- 从 Context 取数据 ----------------
    @staticmethod
    def _topic_from(context) -> str:
        item = context.item("project_info")
        if item and isinstance(item.data, dict):
            return str(item.data.get("project_title", ""))
        return ""

    @staticmethod
    def _existing_characters_from(context) -> List[str]:
        item = context.item("characters")
        if not item:
            return []
        names = []
        for c in item.data or []:
            if isinstance(c, dict):
                name = str(c.get("name", "")).strip()
                if name:
                    names.append(name)
        return names

    @staticmethod
    def _facts_from(context) -> List[dict]:
        item = context.item("approved_facts")
        if not item:
            return []
        return [dict(f) if isinstance(f, dict) else {"content": str(f)} for f in (item.data or [])]

    @staticmethod
    def _world_from(context) -> dict:
        item = context.item("world")
        if not item:
            return {}
        return dict(item.data or {})

    # ---------------- 规范化 ----------------
    def _normalize(self, raw: dict) -> List[CastingEntry]:
        """把 LLM 的原始 entries 规范成 CastingEntry 列表。

        确定性规则：名字命中已有角色 -> existing（复用，不取 LLM 档案）；
        否则 -> new（必须带完整档案）。这一步是"跨集不重复建档"的第一道保障。
        """
        raw_entries = raw.get("entries")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise StepError("Casting LLM 输出缺少非空 entries 列表")
        known = set(self.known_characters)
        entries: List[CastingEntry] = []
        for e in raw_entries:
            if not isinstance(e, dict):
                raise StepError(f"Casting entry 不是对象: {type(e).__name__}")
            name = str(e.get("name", "")).strip()
            if not name:
                raise StepError("Casting entry 缺少非空 name")
            role = str(e.get("role", "") or "")
            if name in known:
                # 已有角色：直接复用，绝不新建档案 / 改名
                entries.append(CastingEntry(name=name, status="existing", role=role))
            else:
                char_data = e.get("character")
                character = (
                    Character.from_v01(char_data)
                    if isinstance(char_data, dict)
                    else Character(name=name)
                )
                entries.append(
                    CastingEntry(name=name, status="new", role=role, character=character)
                )
        return entries


class CastingStep(LLMStep):
    """把 CastingAgent 接入 Workflow 引擎的 Step 壳。

    run()      把调度层放入 inputs 的 AssembledContext 交给 CastingAgent，
               返回结构化产出（CastingResult.to_dict()），由引擎落盘 artifact。
    validate() 用 CastingValidator 做确定性校验；不合格 -> 引擎重试（旧版本保留）。
    needs_review = True：成功后进入人工审核节点（approved / approved_with_edit / rejected）。
    """

    step_id = "casting"
    name = "casting"
    needs = CastingAgent.needs
    needs_review = True

    def __init__(self, agent=None, *, step_id: Optional[str] = None,
                 needs_review: Optional[bool] = None):
        super().__init__(
            agent=agent,
            step_id=step_id,
            name=self.name,
            needs=list(self.needs),
            needs_review=needs_review,
        )

    def run(self, context):
        assembled = self.assembled_context(context)
        result = self.agent.run(assembled)
        return self.make_output(
            data=result.to_dict(),
            artifact_name="casting",
            metadata={"episode": result.episode, "agent": self.name},
        )

    def validate(self, data: Any, context) -> List[str]:
        """确定性校验：CastingValidator 的错误转成字符串列表（引擎按此决定重试）。"""
        errors = self.agent.validator.validate_dict(data)
        return [str(e) for e in errors]


def _json_dumps(obj, *, fallback: str = "(none)") -> str:
    """把可序列化对象转成 JSON 文本；空对象返回 fallback（保持与历史 prompt 一致）。"""
    import json
    if not obj:
        return fallback
    return json.dumps(obj, ensure_ascii=False, indent=2)


__all__ = ["CastingAgent", "CastingStep"]
