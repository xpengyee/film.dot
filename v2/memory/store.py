# -*- coding: utf-8 -*-
"""
V0.2 阶段 4 —— Memory 存储层（v2/memory/store.py）

作用：一个项目（project_id）一套 Memory，落在：
        v2/projects/<project_id>/memory/
            facts.json        事实（candidate/approved/rejected 全记录，带 revision）
            characters.json   角色信息
            world.json        世界观 / 设定
            history.json      历史产出引用（例如上一集 artifact 引用）
    不同 project 目录互不相通，天然隔离（指令 §五）。

设计原则（架构报告 §6.5 + 指令 §四 / §六）：
    1. known_facts 是"结构化事实"，不是把 JSON 全塞进来。
    2. 事实先以 candidate 身份进入，必须经人工 approve 才能成为 approved；
       candidate 与 approved 分开存，绝不混在一起。
    3. 每次写操作立刻落盘（写入 -> 关闭程序 -> 重新读取 -> 数据仍在）。
    4. revision 每次写操作 +1，供 Context 标注"这一版事实是第几版"（可追踪）。

本模块不调用 LLM。事实由谁提出（source）、谁确认（reviewer）都记在 Fact 上。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from config import V2_ROOT
from core.io import read_json, write_json
from memory.models import Fact, FactStatus, now_iso, new_fact_id


class MemoryStore:
    """某个项目的 Memory：facts + characters + world + history，JSON 持久化。"""

    def __init__(self, project_id: str, projects_root: Optional[Path] = None):
        self.project_id = project_id
        self.projects_root = Path(projects_root) if projects_root else V2_ROOT / "projects"
        self.root = self.projects_root / project_id / "memory"
        self.facts_path = self.root / "facts.json"
        self.characters_path = self.root / "characters.json"
        self.world_path = self.root / "world.json"
        self.history_path = self.root / "history.json"

        self.revision: int = 0
        self._facts: Dict[str, Fact] = {}
        self._characters: List[dict] = []
        self._world: Dict[str, Any] = {}
        self._history: List[dict] = []
        self.load()
        self.root.mkdir(parents=True, exist_ok=True)   # 创建项目即建 Memory 目录（指令 §六）

    # ---------------- 持久化 ----------------
    def load(self) -> None:
        """从磁盘读回；文件不存在则视为空项目（自动重建）。"""
        if self.facts_path.exists():
            data = read_json(self.facts_path)
            self.revision = int(data.get("revision", 0))
            self._facts = {f["fact_id"]: Fact.from_dict(f) for f in data.get("facts", [])}
        if self.characters_path.exists():
            self._characters = list(read_json(self.characters_path).get("characters", []))
        if self.world_path.exists():
            self._world = dict(read_json(self.world_path).get("world", {}))
        if self.history_path.exists():
            self._history = list(read_json(self.history_path).get("history", []))

    def save(self) -> None:
        """把当前全部 Memory 写盘（每次写操作后调用，保证"关程序再打开仍在"）。"""
        write_json(self.facts_path, {
            "revision": self.revision,
            "facts": [f.to_dict() for f in self._facts.values()],
        })
        write_json(self.characters_path, {"characters": self._characters})
        write_json(self.world_path, {"world": self._world})
        write_json(self.history_path, {"history": self._history})

    # ---------------- 事实：candidate -> approved / rejected ----------------
    def add_candidate_fact(
        self,
        content: str,
        *,
        category: str = "general",
        source: str = "memory_writer",
    ) -> Fact:
        """LLM / Agent 提出一条候选事实（未经确认，只是 candidate）。

        :param content:  事实内容，如 "Theo left the apartment"
        :param category: plot | character | world | general
        :param source:   提出者，如 "memory_writer" / "human"
        """
        self.revision += 1
        fact = Fact(
            fact_id=new_fact_id(),
            content=content,
            status=FactStatus.CANDIDATE,
            category=category,
            source=source,
            created_at=now_iso(),
        )
        self._facts[fact.fact_id] = fact
        self.save()
        return fact

    def approve_fact(self, fact_id: str, *, reviewer: str = "human", comment: str = "") -> Fact:
        """人工确认一条候选事实，成为项目级 approved 事实。"""
        fact = self._get_candidate(fact_id, action="approve")
        self.revision += 1
        fact.status = FactStatus.APPROVED
        fact.reviewer = reviewer
        fact.comment = comment
        fact.reviewed_at = now_iso()
        self.save()
        return fact

    def reject_fact(self, fact_id: str, *, reviewer: str = "human", comment: str = "") -> Fact:
        """人工拒绝一条候选事实，绝不进入 approved 集合。"""
        fact = self._get_candidate(fact_id, action="reject")
        self.revision += 1
        fact.status = FactStatus.REJECTED
        fact.reviewer = reviewer
        fact.comment = comment
        fact.reviewed_at = now_iso()
        self.save()
        return fact

    def _get_candidate(self, fact_id: str, *, action: str) -> Fact:
        fact = self._facts.get(fact_id)
        if fact is None:
            raise KeyError(f"fact 不存在: {fact_id}")
        if fact.status != FactStatus.CANDIDATE:
            raise ValueError(f"fact {fact_id} 当前状态是 {fact.status}，不能执行 {action}（只能审核 candidate）")
        return fact

    # ---------------- 事实：查询 ----------------
    def get_fact(self, fact_id: str) -> Optional[Fact]:
        return self._facts.get(fact_id)

    def get_approved_facts(self) -> List[Fact]:
        """已确认事实（ContextAssembler / 后续 Step 真正用的）。"""
        return sorted(
            (f for f in self._facts.values() if f.status == FactStatus.APPROVED),
            key=lambda f: f.fact_id,
        )

    def get_candidate_facts(self) -> List[Fact]:
        """等待审核的候选事实。"""
        return sorted(
            (f for f in self._facts.values() if f.status == FactStatus.CANDIDATE),
            key=lambda f: f.fact_id,
        )

    def get_rejected_facts(self) -> List[Fact]:
        """被拒绝的事实（保留记录，不再进入 Context）。"""
        return sorted(
            (f for f in self._facts.values() if f.status == FactStatus.REJECTED),
            key=lambda f: f.fact_id,
        )

    # ---------------- 角色 / 世界观 / 历史产出（结构化存取） ----------------
    def set_characters(self, characters: List[dict]) -> None:
        """写入角色清单（列表里每个元素是角色的 dict，如 Character.to_dict()）。"""
        self.revision += 1
        self._characters = list(characters)
        self.save()

    def get_characters(self) -> List[dict]:
        return list(self._characters)

    def set_world(self, world: dict) -> None:
        """写入世界观 / 设定（dict，如 {"name": "上海都市", "rules": [...]}）。"""
        self.revision += 1
        self._world = dict(world)
        self.save()

    def get_world(self) -> dict:
        return dict(self._world)

    def add_history_entry(self, entry: dict) -> None:
        """追加一条历史产出引用（如 {"episode": 1, "artifact": "script_0.json", "version": 3}）。"""
        self.revision += 1
        self._history.append(dict(entry))
        self.save()

    def get_history(self) -> List[dict]:
        return list(self._history)

    # ---------------- 便于测试 / 核查 ----------------
    def __repr__(self) -> str:  # pragma: no cover - 仅调试
        return (f"MemoryStore(project={self.project_id}, revision={self.revision}, "
                f"facts={len(self._facts)})")


__all__ = ["MemoryStore"]
