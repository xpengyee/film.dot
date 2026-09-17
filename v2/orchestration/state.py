# -*- coding: utf-8 -*-
"""
V0.2 Stage 12-B —— 编排层进度状态（v2/orchestration/state.py）

作用（开工指令12 §6.3 / §7.5 + stage12_plan §2.6 / §2.7）：

    编排层需要一份**自己的进度记录**，回答"这一集跑到哪了、能不能 resume"。
    落盘位置：`v2/projects/<pid>/orchestration/state.json`

    {
      "version": "1",
      "project_id": "...",
      "updated_at": "...",
      "episodes": {
        "1": {
          "casting": {"status": "approved|failed|pending", "artifact": "casting.json",
                      "attempts": 1, "run_id": "...", "workflow_id": "casting_ep1"},
          "scenes": {
            "1": {"scene":      {"status": "approved", "artifact": "scene_1.json", ...},
                  "storyboard": {"status": "approved", "artifact": "storyboard_1.json", ...}}
          }
        }
      },
      "visual_assets": {"registered": ["CHAR_001", "LOC_001"], "pending": ["prop:old key"]}
    }

**关键纪律（指令 §6.3）**：状态文件是**编排进度的记录**，
**不是**第二套审核状态机 —— 每个条目的 `status` 是对既有
Review / Artifact 结果的**镜像**，不是新判据：
    approved  ⇐ 该步 StepResult.is_success 且 approved_artifact 非空
    failed    ⇐ StepResult.status ∈ {FAILED, REJECTED}
    pending   ⇐ 未执行 / SKIPPED / 磁盘产物缺失（见 should_skip）

三个实现要点：

1. **原子写自实现**（总控裁决 A-4）：临时文件 + `os.replace`。
   序列化行为对齐 `core.io.write_json`（`ensure_ascii=False` / `indent=2`）。
   为什么不自实现不越界：`core.io.write_json` 实测**不是**原子写
   （`path.open("w")` 直写，无 `os.replace`），崩溃会留半截 state.json；
   而 `core/` 属禁改清单 → 原子写在编排层内部自实现（不碰 core/）。
2. **resume 跳过前必须校验磁盘 artifact 仍存在**（stage12_plan 要点 H / R12）：
   "状态说齐备、磁盘没有" → 静默不齐备。缺失即视为 pending 重跑。
3. `OrchestrationError` 定义在本文件（包内最低层），由 pipeline 复用同一类型，
   避免新增第 5 个文件，也避免包内循环导入。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.io import read_json
from workflow.models import now_iso


# ---------------------------------------------------------------------------
# 错误家族（指令 §十 铁律 9：新增 OrchestrationError 家族；
# 下面各层的错误原样透传，不吞、不改写）
# ---------------------------------------------------------------------------
class OrchestrationError(Exception):
    """编排层错误家族基类。

    用途：编排层的**调度前置条件不满足** / **设计冲突** / **不支持的分支**
    （如视觉资产遇到 approved_with_edit）。下面各层的错误（StepError /
    WorkflowError / LLMError …）**原样透传**，不包成这个类型。
    """


# ---------------------------------------------------------------------------
# 原子写（编排层内部实现；不碰 core/）
# ---------------------------------------------------------------------------
def atomic_write_json(path: Path, data: Any) -> Path:
    """原子写 JSON：临时文件 + `os.replace`（同目录内替换，POSIX/Windows 都原子）。

    序列化与 `core.io.write_json` 对齐：`ensure_ascii=False` / `indent=2`。
    失败时清理临时文件，绝不留下半截文件、也不残留 *.tmp。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)          # ★ 原子替换（同目录）
    except BaseException:
        try:
            if tmp.exists():
                os.unlink(tmp)
        except OSError:                # pragma: no cover - 清理失败不再掩盖原异常
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# 状态规范化
# ---------------------------------------------------------------------------
_STATUSES = ("approved", "failed", "pending")


def _normalize(data: Optional[dict]) -> Dict[str, Any]:
    """把任意来源的 dict 规范成状态结构（幂等：normalize(normalize(x)) == normalize(x)）。"""
    d: Dict[str, Any] = dict(data or {})
    d["version"] = str(d.get("version") or "1")
    d["project_id"] = str(d.get("project_id") or "")
    d["updated_at"] = str(d.get("updated_at") or "")
    episodes = d.get("episodes")
    d["episodes"] = dict(episodes) if isinstance(episodes, dict) else {}
    va = d.get("visual_assets")
    va = dict(va) if isinstance(va, dict) else {}
    va["registered"] = list(va.get("registered") or [])
    va["pending"] = list(va.get("pending") or [])
    d["visual_assets"] = va
    return d


def _entry(status: str, artifact: str = "", attempts: int = 0,
           run_id: str = "", workflow_id: str = "") -> Dict[str, Any]:
    st = str(status or "pending")
    if st not in _STATUSES:
        st = "pending"
    return {
        "status": st,
        "artifact": str(artifact or ""),
        "attempts": int(attempts or 0),
        "run_id": str(run_id or ""),
        "workflow_id": str(workflow_id or ""),
    }


# ---------------------------------------------------------------------------
# OrchestrationState
# ---------------------------------------------------------------------------
class OrchestrationState:
    """编排进度模型：读写 `state.json` + resume 判定。"""

    VERSION = "1"

    def __init__(
        self,
        project_id: str,
        *,
        path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
        data: Optional[dict] = None,
    ):
        self.project_id = str(project_id or "")
        self.path = Path(path) if path else None
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.data: Dict[str, Any] = _normalize(data)
        self.data["project_id"] = self.project_id
        self.data["version"] = self.VERSION

    # ---------------- 序列化 ----------------
    def to_dict(self) -> dict:
        """返回状态快照（深拷贝，调用方改动不影响内部）。"""
        return json.loads(json.dumps(self.data, ensure_ascii=False))

    @classmethod
    def from_dict(
        cls,
        data: dict,
        *,
        project_id: Optional[str] = None,
        path: Optional[Path] = None,
        artifacts_dir: Optional[Path] = None,
    ) -> "OrchestrationState":
        """从 dict 还原（与 to_dict 往返一致）。"""
        return cls(
            project_id if project_id is not None else str((data or {}).get("project_id") or ""),
            path=path,
            artifacts_dir=artifacts_dir,
            data=data,
        )

    # ---------------- 持久化 ----------------
    def touch(self, updated_at: Optional[str] = None) -> None:
        self.data["updated_at"] = str(updated_at or now_iso())

    def save(self, path: Optional[Path] = None) -> Path:
        """原子写盘。未指定 path 且构造时也未给 → 明确报错（不静默）。"""
        target = Path(path) if path else self.path
        if target is None:
            raise OrchestrationError(
                "OrchestrationState 未指定保存路径（构造时 path=None），无法落盘"
            )
        self.touch()
        atomic_write_json(target, self.data)
        return target

    @classmethod
    def load(
        cls,
        project_id: str,
        path: Path,
        *,
        artifacts_dir: Optional[Path] = None,
    ) -> "OrchestrationState":
        """读回状态；文件不存在 → 空状态（首次运行）。"""
        p = Path(path)
        data = read_json(p) if p.exists() else {}
        return cls(project_id, path=p, artifacts_dir=artifacts_dir, data=data)

    # ---------------- 集 / 段 访问 ----------------
    def _episode(self, episode: int, *, create: bool = False) -> Dict[str, Any]:
        key = str(int(episode))
        eps: Dict[str, Any] = self.data.setdefault("episodes", {})
        if key not in eps:
            if not create:
                return {}
            eps[key] = {"casting": None, "scenes": {}}
        ep = eps[key]
        if not isinstance(ep, dict):          # pragma: no cover - 防御脏数据
            ep = {"casting": None, "scenes": {}}
            eps[key] = ep
        ep.setdefault("casting", None)
        if not isinstance(ep.get("scenes"), dict):
            ep["scenes"] = {}
        return ep

    def casting_entry(self, episode: int) -> Optional[dict]:
        entry = self._episode(episode).get("casting")
        return dict(entry) if isinstance(entry, dict) else None

    def record_casting(self, episode: int, *, status: str, artifact: str = "",
                       attempts: int = 0, run_id: str = "",
                       workflow_id: str = "") -> dict:
        ep = self._episode(episode, create=True)
        ep["casting"] = _entry(status, artifact, attempts, run_id, workflow_id)
        self.touch()
        return dict(ep["casting"])

    def scene_entry(self, episode: int, scene_id: int, kind: str) -> Optional[dict]:
        scenes = self._episode(episode).get("scenes") or {}
        slot = scenes.get(str(int(scene_id))) or {}
        entry = slot.get(kind) if isinstance(slot, dict) else None
        return dict(entry) if isinstance(entry, dict) else None

    def record_scene(self, episode: int, scene_id: int, kind: str, *, status: str,
                     artifact: str = "", attempts: int = 0, run_id: str = "",
                     workflow_id: str = "") -> dict:
        if kind not in ("scene", "storyboard"):
            raise OrchestrationError(f"未知场景段落 kind: {kind!r}（scene / storyboard）")
        ep = self._episode(episode, create=True)
        slot = ep["scenes"].setdefault(str(int(scene_id)), {})
        slot[kind] = _entry(status, artifact, attempts, run_id, workflow_id)
        self.touch()
        return dict(slot[kind])

    def scene_ids(self, episode: int) -> List[int]:
        """本集已记录的场景号（升序）。"""
        scenes = self._episode(episode).get("scenes") or {}
        out: List[int] = []
        for key in scenes:
            try:
                out.append(int(key))
            except (TypeError, ValueError):    # pragma: no cover - 防御脏数据
                continue
        return sorted(out)

    # ---------------- Stage 13：图像段进度镜像（纯追加） ----------------
    def record_image_stage(self, episode: int, scene_id: int, *, artifact: str = "",
                           images_generated: int = 0, budget_used: int = 0,
                           budget_cap: int = 0, status: str = "approved") -> dict:
        """记录某场景 Storyboard 的"图像段"进度（★纯追加，不新建第二套状态机）。

        与 `record_scene` 同一纪律：`status` 只是对既有 Artifact / Review 结果的
        **镜像**，不是新判据。`artifact` 是图像段产出的 **Storyboard 新版本**文件名
        （原 Storyboard 版本不动）。
        """
        ep = self._episode(episode, create=True)
        slot = ep["scenes"].setdefault(str(int(scene_id)), {})
        entry = _entry(status, artifact)
        entry["images_generated"] = int(images_generated or 0)
        entry["budget_used"] = int(budget_used or 0)
        entry["budget_cap"] = int(budget_cap or 0)
        slot["images"] = entry
        self.touch()
        return dict(entry)

    def image_entry(self, episode: int, scene_id: int) -> Optional[dict]:
        """读某场景的图像段进度条目；不存在返回 None。"""
        scenes = self._episode(episode).get("scenes") or {}
        slot = scenes.get(str(int(scene_id))) or {}
        entry = slot.get("images") if isinstance(slot, dict) else None
        return dict(entry) if isinstance(entry, dict) else None

    # ---------------- 视觉资产进度镜像 ----------------
    def record_visual_asset(self, kind: str, name: str, *, status: str,
                            asset_id: str = "", version: str = "",
                            artifact: str = "") -> None:
        """记录一个视觉实体的进度（registered = 已写回 registry 的 asset_id；
        pending = 未齐备实体的 "kind:name"）。"""
        va: Dict[str, Any] = self.data.setdefault(
            "visual_assets", {"registered": [], "pending": []})
        registered: List[str] = [x for x in (va.get("registered") or [])]
        pending: List[str] = [x for x in (va.get("pending") or [])]
        key = f"{kind}:{name}"
        if str(status) == "approved" and asset_id:
            if asset_id not in registered:
                registered.append(asset_id)
            pending = [x for x in pending if x != key]
        else:
            if key not in pending:
                pending.append(key)
            if asset_id and asset_id in registered:
                registered = [x for x in registered if x != asset_id]
        va["registered"] = registered
        va["pending"] = pending
        self.touch()

    # ---------------- resume 判定 ----------------
    def should_skip(self, entry: Optional[dict], *,
                    artifacts_dir: Optional[Path] = None) -> bool:
        """该条目能否跳过（resume）。

        规则（stage12_plan §2.6 要点 H / R12）：
            approved 且**磁盘 artifact 仍存在** → True
            failed / 缺失 / 非 approved            → False（重跑）
            approved 但磁盘 artifact 已不存在       → False（视为 pending 重跑）
        """
        if not isinstance(entry, dict):
            return False
        if str(entry.get("status", "")) != "approved":
            return False
        artifact = str(entry.get("artifact", "") or "")
        if not artifact:
            return False
        base = Path(artifacts_dir) if artifacts_dir else self.artifacts_dir
        if base is None:
            # 没有 artifacts_dir 信息：只按状态判定（调用方应尽量注入）
            return True
        return (Path(base) / artifact).is_file()

    def should_skip_casting(self, episode: int, *,
                            artifacts_dir: Optional[Path] = None) -> bool:
        return self.should_skip(self.casting_entry(episode), artifacts_dir=artifacts_dir)

    def should_skip_scene(self, episode: int, scene_id: int, kind: str, *,
                          artifacts_dir: Optional[Path] = None) -> bool:
        return self.should_skip(self.scene_entry(episode, scene_id, kind),
                                artifacts_dir=artifacts_dir)


__all__ = ["OrchestrationError", "OrchestrationState", "atomic_write_json"]
