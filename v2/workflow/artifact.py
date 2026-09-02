# -*- coding: utf-8 -*-
"""
V0.2 阶段 3 —— Artifact 版本化与轨迹保留（v2/workflow/artifact.py）

作用：统一管理 Step 产出的落盘。核心承诺只有一个：

    "某个结果来自哪个 Step、哪个 run、哪个 attempt" 永远可查。
    （开工指令 §九 的核心要求）

命名规则（开工指令 §十 / §十一，稳定、可预测、可恢复）：
    同一个 Step 产出基础名 name="scenes_5"：
        磁盘上还没有任何版本 -> version 1 -> scenes_5.json
        磁盘上已有 version 1 -> version 2 -> scenes_5_v2.json
        磁盘上已有 version 2 -> version 3 -> scenes_5_v3.json
        ...
    version 由 ArtifactStore 在写入时按"磁盘上已存在的版本数 + 1"决定，
    所以：
        - 单次 run 内重试（attempt 1/2/3）        -> v1/v2/v3 全保留；
        - 整个 workflow 重新运行（新 run 重跑同一 Step）-> 仍递增到下一个版本，
          绝不覆盖上一轮的历史文件。
    version 1 不带后缀（scenes_5.json），version >= 2 带 _vN 后缀
    （scenes_5_v2.json）——与阶段 1 锁定的命名一致。

注意：Artifact.version（磁盘实际版本）在单次 run 内与 attempt 一一对应；
跨 run 重跑时 version 会继续递增，attempt 则从 1 重新开始。二者都记录在 Artifact 上。

读写复用 core/io.py（V0.1 迁移的容错行为，不重复实现）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.io import read_json, write_json
from workflow.errors import ArtifactError
from workflow.models import now_iso


@dataclass
class Artifact:
    """一个落盘的产出版本文件。"""

    artifact_id: str               # 唯一 ID（如 "scenes_5#3"）
    step_id: str                   # 来自哪个 Step
    attempt: int                   # 本次 run 内第几次尝试
    name: str                      # 基础名（如 "scenes_5"）
    version: int                   # 磁盘实际版本号（1 = 无后缀）
    filename: str                  # 文件名（如 "scenes_5_v2.json"）
    path: Path                     # 绝对路径
    type: str = "json"             # "json" | "text"
    created_at: str = ""
    approved: bool = False         # 是否被采纳为最终版本
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "step_id": self.step_id,
            "attempt": self.attempt,
            "name": self.name,
            "version": self.version,
            "filename": self.filename,
            "path": str(self.path),
            "type": self.type,
            "created_at": self.created_at,
            "approved": self.approved,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            step_id=str(data.get("step_id", "")),
            attempt=int(data.get("attempt", 1)),
            name=str(data.get("name", "")),
            version=int(data.get("version", 1)),
            filename=str(data.get("filename", "")),
            path=Path(str(data.get("path", ""))),
            type=str(data.get("type", "json")),
            created_at=str(data.get("created_at", "")),
            approved=bool(data.get("approved", False)),
            metadata=dict(data.get("metadata") or {}),
        )

    def read(self) -> Any:
        """读回本版本的文件内容（测试 / 后续阶段恢复用）。"""
        if self.type == "text":
            return self.path.read_text(encoding="utf-8")
        return read_json(self.path)


class ArtifactStore:
    """Artifact 的落盘管理：命名 -> 探测版本 -> 写盘 -> 返回 Artifact 记录。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    # ---------------- 命名 ----------------
    @staticmethod
    def version_filename(name: str, version: int, type: str = "json") -> str:
        """按版本号算文件名：v1 无后缀，v2+ 带 _vN 后缀。"""
        ext = ".txt" if type == "text" else ".json"
        if version <= 1:
            return f"{name}{ext}"
        return f"{name}_v{version}{ext}"

    def next_version(self, name: str, type: str = "json") -> int:
        """探测磁盘上该 name 已存在的最大版本号，返回下一个版本号（绝不覆盖）。"""
        if not self.root.exists():
            return 1
        ext = ".txt" if type == "text" else ".json"
        max_v = 0
        for p in self.root.glob(f"{name}*{ext}"):
            stem = p.stem
            if stem == name:
                max_v = max(max_v, 1)
            elif stem.startswith(name + "_v"):
                suffix = stem[len(name) + 2:]
                try:
                    max_v = max(max_v, int(suffix))
                except ValueError:
                    pass  # 非本命名规则的相似文件，忽略
        return max_v + 1

    # ---------------- 写盘 ----------------
    def save(
        self,
        *,
        step_id: str,
        attempt: int,
        name: str,
        data: Any,
        type: str = "json",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Artifact:
        """把一份产出写成版本化文件，返回 Artifact 记录。

        写盘前先探测版本，保证即使同名文件已存在也绝不覆盖。
        """
        try:
            version = self.next_version(name, type)
            filename = self.version_filename(name, version, type)
            path = self.root / filename
            self.root.mkdir(parents=True, exist_ok=True)
            if type == "text":
                path.write_text(str(data), encoding="utf-8")
            else:
                write_json(path, data)
        except OSError as exc:
            raise ArtifactError(f"写入 artifact 失败 {name}: {exc}") from exc
        return Artifact(
            artifact_id=f"{name}#{version}",
            step_id=step_id,
            attempt=attempt,
            name=name,
            version=version,
            filename=filename,
            path=path,
            type=type,
            created_at=now_iso(),
            approved=False,
            metadata=dict(metadata or {}),
        )

    # ---------------- 读取 / 列出 ----------------
    def load(self, artifact: Artifact) -> Any:
        """按 Artifact 记录读回内容。"""
        return artifact.read()

    def list_files(self, name: str, type: str = "json") -> List[Path]:
        """列出磁盘上该 name 的所有版本文件（用于测试 / 人工核查）。"""
        ext = ".txt" if type == "text" else ".json"
        if not self.root.exists():
            return []
        return sorted(self.root.glob(f"{name}*{ext}"))


__all__ = ["Artifact", "ArtifactStore"]
