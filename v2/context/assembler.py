# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— ContextAssembler（v2/context/assembler.py）

作用：纯代码组件（不调 LLM），按 Step 声明的 needs 组装 Context。

设计要点（架构报告 §七 + 第四十七章问题五 + 指令 §八 / §九 / §十）：
    1. 声明式 needs：Step 自己声明 needs = ["approved_facts", "characters", ...]，
       ContextAssembler 照单取货。绝不用 if step_name == "xxx" 的硬编码。
    2. 只装需要的内容：没声明 locations 就绝不塞 Locations。
       因为 Context 越大 -> token 越高 -> 成本越高 -> 干扰越大。
    3. 每一条内容都带来源（source/source_type/source_id/version），可追踪。
    4. provider 注册表：每个 needs 名对应一个取货函数。
       新增"信息种类"只需加一个 provider，不用改组装逻辑。

数据来源（指令 §九）：
    project / memory(approved_facts, characters, world, history) /
    assets(locations, actions, shots) / previous_artifacts / workflow_state

needs 名注册表（可扩展）：
    project_info      项目身份（project_id + 标题）
    approved_facts    已确认事实（Memory）
    candidate_facts   待审核候选事实（Memory）
    characters        角色信息（Memory）
    world             世界观/设定（Memory）
    history           历史产出引用（Memory）
    previous_artifacts 已成功 Step 的产出引用（Workflow）
    workflow_state    当前 Workflow 状态快照（Workflow）
    locations         合法地点清单（Asset）
    actions           合法动作清单（Asset）
    shots             合法镜头清单（Asset）
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from context.models import (
    AssembledContext,
    ContextItem,
    ContextSource,
    char_count,
    new_context_id,
    now_iso,
)
from core.assets import AssetRegistry
from memory.store import MemoryStore


class ContextAssembler:
    """根据 Step 声明的 needs 组装 Context（纯代码，不调 LLM）。"""

    def __init__(self, memory: MemoryStore, assets: Optional[AssetRegistry] = None):
        self.memory = memory
        self.assets = assets
        # provider 注册表：needs 名 -> 取货函数
        self._providers: Dict[str, Callable[[Any, Dict[str, str]], ContextItem]] = {
            "project_info": self._provide_project_info,
            "approved_facts": self._provide_approved_facts,
            "candidate_facts": self._provide_candidate_facts,
            "characters": self._provide_characters,
            "world": self._provide_world,
            "history": self._provide_history,
            "previous_artifacts": self._provide_previous_artifacts,
            "workflow_state": self._provide_workflow_state,
            "locations": self._provide_locations,
            "actions": self._provide_actions,
            "shots": self._provide_shots,
        }

    # ---------------- 对外接口 ----------------
    def available_keys(self) -> List[str]:
        """当前支持的 needs 名（供 Step 声明时对照）。"""
        return sorted(self._providers.keys())

    def assemble(
        self,
        step,
        context,
        *,
        project_title: str = "",
    ) -> AssembledContext:
        """按 step.needs 组装 Context。

        :param step:           任意带 .step_id 和 .needs（list[str]）的对象（Step 基类满足）
        :param context:        WorkflowContext（提供项目标识 / 已成功 Step 产出）
        :param project_title:  项目标题（project_info 用）
        :return: AssembledContext
        :raises ValueError: Step 声明了未注册的 needs（宁可明确报错，不静默跳过）
        """
        needs = list(getattr(step, "needs", []) or [])
        unknown = [k for k in needs if k not in self._providers]
        if unknown:
            raise ValueError(
                f"Step {getattr(step, 'step_id', '?')} 声明了未注册的 needs: {unknown}。"
                f"可用: {self.available_keys()}"
            )
        meta = {"project_title": project_title}
        items = [self._providers[k](context, meta) for k in needs]
        return AssembledContext(
            context_id=new_context_id(),
            project_id=self.memory.project_id,
            step_id=getattr(step, "step_id", ""),
            items=items,
            created_at=now_iso(),
        )

    # ---------------- 取货函数（provider） ----------------
    def _item(self, key: str, data: Any, source: ContextSource) -> ContextItem:
        return ContextItem(key=key, data=data, source=source, char_count=char_count(data))

    def _provide_project_info(self, context, meta: Dict[str, str]) -> ContextItem:
        data = {"project_id": self.memory.project_id, "project_title": meta.get("project_title", "")}
        return self._item("project_info", data, ContextSource(
            source="project", source_type="project_info",
            source_id=self.memory.project_id, version="1"))

    def _provide_approved_facts(self, context, meta: Dict[str, str]) -> ContextItem:
        facts = [{"fact_id": f.fact_id, "content": f.content, "category": f.category}
                 for f in self.memory.get_approved_facts()]
        return self._item("approved_facts", facts, ContextSource(
            source="memory", source_type="approved_fact",
            source_id=self.memory.project_id, version=f"rev{self.memory.revision}"))

    def _provide_candidate_facts(self, context, meta: Dict[str, str]) -> ContextItem:
        facts = [{"fact_id": f.fact_id, "content": f.content, "category": f.category,
                  "source": f.source} for f in self.memory.get_candidate_facts()]
        return self._item("candidate_facts", facts, ContextSource(
            source="memory", source_type="candidate_fact",
            source_id=self.memory.project_id, version=f"rev{self.memory.revision}"))

    def _provide_characters(self, context, meta: Dict[str, str]) -> ContextItem:
        return self._item("characters", self.memory.get_characters(), ContextSource(
            source="memory", source_type="character",
            source_id=self.memory.project_id, version=f"rev{self.memory.revision}"))

    def _provide_world(self, context, meta: Dict[str, str]) -> ContextItem:
        return self._item("world", self.memory.get_world(), ContextSource(
            source="memory", source_type="world",
            source_id=self.memory.project_id, version=f"rev{self.memory.revision}"))

    def _provide_history(self, context, meta: Dict[str, str]) -> ContextItem:
        return self._item("history", self.memory.get_history(), ContextSource(
            source="memory", source_type="history",
            source_id=self.memory.project_id, version=f"rev{self.memory.revision}"))

    def _provide_previous_artifacts(self, context, meta: Dict[str, str]) -> ContextItem:
        # context.artifacts: step_id -> artifact(dict)，来自 WorkflowEngine 的记录
        artifacts = dict(getattr(context, "artifacts", {}) or {})
        summary = [
            {"step_id": sid, "filename": art.get("filename", ""), "version": art.get("version", 1),
             "approved": art.get("approved", False)}
            for sid, art in artifacts.items()
        ]
        return self._item("previous_artifacts", summary, ContextSource(
            source="workflow", source_type="artifact",
            source_id=getattr(context, "run_id", self.memory.project_id), version="1"))

    def _provide_workflow_state(self, context, meta: Dict[str, str]) -> ContextItem:
        state = {
            "project_id": getattr(context, "project_id", ""),
            "workflow_id": getattr(context, "workflow_id", ""),
            "run_id": getattr(context, "run_id", ""),
            "status": getattr(context, "status", ""),
            "completed_steps": sorted(getattr(context, "artifacts", {}).keys()),
        }
        return self._item("workflow_state", state, ContextSource(
            source="workflow", source_type="workflow_state",
            source_id=getattr(context, "run_id", ""), version="1"))

    def _provide_locations(self, context, meta: Dict[str, str]) -> ContextItem:
        self._require_assets("locations")
        names = self.assets.location_names()
        data = [{"name": n, "positions": [p.id for p in self.assets.get_positions(n)]}
                for n in names]
        return self._item("locations", data, ContextSource(
            source="asset", source_type="location",
            source_id="assets/locations", version="1"))

    def _provide_actions(self, context, meta: Dict[str, str]) -> ContextItem:
        self._require_assets("actions")
        return self._item("actions", self.assets.action_names(), ContextSource(
            source="asset", source_type="action",
            source_id="assets/locations/actions.json", version="1"))

    def _provide_shots(self, context, meta: Dict[str, str]) -> ContextItem:
        self._require_assets("shots")
        return self._item("shots", self.assets.shot_names(), ContextSource(
            source="asset", source_type="shot",
            source_id="assets/locations/shots.json", version="1"))

    def _require_assets(self, key: str) -> None:
        if self.assets is None:
            raise ValueError(
                f"Step 需要 {key}，但 ContextAssembler 没有注入 AssetRegistry（assets=None）。"
            )


__all__ = ["ContextAssembler"]
