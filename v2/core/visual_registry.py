# -*- coding: utf-8 -*-
"""
V0.2 Stage 10 —— Visual Asset Registry（core/visual_registry.py）

作用（stage10_plan §3/§9/§10/§22）：视觉身份的唯一事实源。
    - 身份分配：kind 前缀计数（CHAR_001…），(kind, name) 永久绑定；
    - 版本管理：单调递增、绝不复用号、绝不覆盖内容；换参考图/审核编辑 = 新版本；
    - 生命周期：不是第三套审核状态机，而是对既有 Review 结果（三态）的
      **确定性派生**（stage10_plan §10.2 映射表，无 LLM 参与）：
          submit_for_review(draft → reviewing) / approve(reviewing → approved，
          旧 approved 自动 superseded) / reject(reviewing → rejected) /
          archive(资产级，全部版本 → archived)
    - 持久化：v2/projects/<pid>/visual_assets/registry.json（core.io.write_json）
    - 参考图登记：计算文件 sha256 作 checksum（本层是唯一碰磁盘的 visual 模块）

边界：
    - 不调 LLM；审批决定来自 Review/人工，本层只做指针算术与状态派生；
    - Memory/Review/Workflow 0 改动（stage10_plan §24）；artifact 是过程轨迹、
      registry 是当前状态，职责不同不算双写。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.io import write_json, read_json, ensure_dir
from core.visual import (
    VISUAL_ASSET_KINDS, LEGAL_STATUS_TRANSITIONS,
    VisualAsset, VisualAssetVersion, ReferenceImage, new_profile,
    asset_id_for, is_valid_asset_id, version_number,
)

# Review 三态决策（stage10_plan §10.2；与 review 服务的决策词表对齐）
DECISION_APPROVED = "APPROVED"
DECISION_APPROVED_WITH_EDIT = "APPROVED_WITH_EDIT"
DECISION_REJECTED = "REJECTED"


class VisualRegistryError(ValueError):
    """Registry 变更违规（重名 / 非法转移 / 资产不存在等）。调用方按明确报错处理。"""


def sha256_of_file(path: Path) -> str:
    """文件 sha256（64 hex）。文件不存在抛 FileNotFoundError（明确报错，不编造）。"""
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class VisualAssetRegistry:
    """项目级视觉资产注册表（v2/projects/<pid>/visual_assets/registry.json）。"""

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.visual_dir = ensure_dir(self.project_dir / "visual_assets")
        self.images_dir = ensure_dir(self.visual_dir / "images")
        self.registry_path = self.visual_dir / "registry.json"
        self.assets: Dict[str, VisualAsset] = {}
        self._next_number: Dict[str, int] = {k: 1 for k in VISUAL_ASSET_KINDS}
        self._next_image_number: int = 1
        self.load()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "assets": {aid: a.to_dict() for aid, a in self.assets.items()},
            "next_number": dict(self._next_number),
            "next_image_number": self._next_image_number,
        }

    def save(self) -> None:
        write_json(self.registry_path, self.to_dict())

    def load(self) -> None:
        """从 registry.json 恢复（不存在 = 空表；解析失败明确报错，绝不静默清空）。"""
        if not self.registry_path.exists():
            return
        data = read_json(self.registry_path)
        self.assets = {}
        for aid, a in (data.get("assets") or {}).items():
            asset = VisualAsset.from_dict(a)
            if asset is not None:
                self.assets[str(aid)] = asset
        nn = data.get("next_number") or {}
        self._next_number = {k: int(nn.get(k, 1)) for k in VISUAL_ASSET_KINDS}
        self._next_image_number = int(data.get("next_image_number", 1) or 1)

    # ------------------------------------------------------------------
    # 身份：注册与绑定（(kind, name) 唯一且永久）
    # ------------------------------------------------------------------
    def register(self, kind: str, name: str, *, purpose: str = "",
                 asset_id: str = "") -> VisualAsset:
        """注册一个新视觉资产（0 版本，随后用 new_version 建草稿）。

        :raises VisualRegistryError: (kind, name) 已被绑定 / kind 或 name 为空。
        """
        if kind not in VISUAL_ASSET_KINDS:
            raise VisualRegistryError(
                f"未知视觉资产 kind: {kind!r}（支持 {list(VISUAL_ASSET_KINDS)}）")
        name = str(name or "").strip()
        if not name:
            raise VisualRegistryError("视觉资产名（语义锚点）不能为空。")
        if self.find(kind, name) is not None:
            raise VisualRegistryError(
                f"(kind={kind!r}, name={name!r}) 已绑定资产，禁止重复建档。"
                f"更新视觉请在该资产上产生新版本。")
        aid = str(asset_id or "").strip() or self._allocate_id(kind)
        if not is_valid_asset_id(aid, kind):
            raise VisualRegistryError(f"asset_id 非法或前缀与 kind 不匹配: {aid!r}")
        if aid in self.assets:
            raise VisualRegistryError(f"asset_id 已存在: {aid!r}")
        from core.token_log import now_iso
        asset = VisualAsset(asset_id=aid, kind=kind, name=name, purpose=purpose,
                            created_at=now_iso(), updated_at=now_iso())
        self.assets[aid] = asset
        self.save()
        return asset

    def _allocate_id(self, kind: str) -> str:
        """kind 前缀计数分配 ID（CHAR_001…；_next_number 持久化防回退复用）。"""
        aid = asset_id_for(kind, self._next_number[kind])
        self._next_number[kind] += 1
        return aid

    def find(self, kind: str, name: str) -> Optional[VisualAsset]:
        """(kind, name) → 资产；未绑定返回 None。"""
        for asset in self.assets.values():
            if asset.kind == kind and asset.name == name:
                return asset
        return None

    def get(self, asset_id: str) -> VisualAsset:
        """按 ID 取资产；不存在明确报错（调用方据此列缺口）。"""
        asset = self.assets.get(str(asset_id))
        if asset is None:
            raise VisualRegistryError(f"视觉资产不存在: {asset_id!r}")
        return asset

    # ------------------------------------------------------------------
    # 版本：只追加，绝不覆盖
    # ------------------------------------------------------------------
    def new_version(self, asset_id: str, *, profile=None, reference_images=None,
                    visual_prompt: str = "", source: str = "text_planned",
                    notes: str = "", status: str = "draft") -> VisualAssetVersion:
        """在资产上追加一个新版本（默认 draft）。

        profile 可传 dict（走确定性规范化 new_profile）或 Profile 实例或 None
        （reference 类没有 profile）。
        """
        from core.token_log import now_iso
        asset = self.get(asset_id)
        if asset.archived:
            raise VisualRegistryError(f"资产 {asset_id} 已退役（archived），不能再出新版本。")
        if isinstance(profile, dict):
            profile = new_profile(asset.kind, profile)
        version = VisualAssetVersion(
            version=asset.next_version(),
            status=status if status in ("draft", "generated") else "draft",
            profile=profile,
            reference_images=list(reference_images or []),
            visual_prompt=str(visual_prompt or ""),
            source=str(source or "text_planned"),
            notes=str(notes or ""),
            created_at=now_iso(),
        )
        asset.versions.append(version)
        asset.current_version = version.version   # 无 approved 时指针指向最新 draft（未生效）
        asset.updated_at = now_iso()
        self.save()
        return version

    def add_reference_image(self, asset_id: str, version: str, *, path: Path,
                            kind: str, note: str = "") -> ReferenceImage:
        """登记参考图：计算 sha256、分配 IMG_XXX、挂到指定版本（换图 = 新版本的入口）。

        图片文件本体由接线/人工放置在 visual_assets/images/ 下；本方法只登记
        引用与 checksum，不复制/移动文件。
        """
        from core.token_log import now_iso
        asset = self.get(asset_id)
        ver = asset.get_version(version)
        if ver is None:
            raise VisualRegistryError(f"资产 {asset_id} 不存在版本 {version!r}")
        path = Path(path)
        if not path.is_absolute() and not path.exists():
            # 允许相对 images_dir；相对注册表目录的都按 project 相对处理
            candidate = self.images_dir / path
            path = candidate if candidate.exists() else (self.project_dir / path)
        checksum = sha256_of_file(path)           # 不存在 → FileNotFoundError，明确报错
        img = ReferenceImage(
            image_id=f"IMG_{self._next_image_number:03d}",
            path=str(path),
            kind=str(kind),
            note=str(note or ""),
            checksum=checksum,
            created_at=now_iso(),
        )
        self._next_image_number += 1
        ver.reference_images.append(img)
        asset.updated_at = img.created_at
        self.save()
        return img

    # ------------------------------------------------------------------
    # 生命周期：对 Review 结果的确定性派生（§10.2 映射表，无 LLM）
    # ------------------------------------------------------------------
    def submit_for_review(self, asset_id: str, version: str) -> VisualAssetVersion:
        """draft|generated → reviewing（对应 ReviewService.submit_for_review）。"""
        return self._transition(asset_id, version, "reviewing")

    def approve(self, asset_id: str, version: str) -> VisualAssetVersion:
        """reviewing → approved；current_version 指向它；旧 approved → superseded。"""
        asset = self.get(asset_id)
        ver = self._transition(asset_id, version, "approved")
        for other in asset.versions:
            if other is not ver and other.status == "approved":
                other.status = "superseded"       # 内容与记录原样保留，绝不删除
        asset.current_version = ver.version
        from core.token_log import now_iso
        asset.updated_at = now_iso()
        self.save()
        return ver

    def reject(self, asset_id: str, version: str) -> VisualAssetVersion:
        """reviewing → rejected（保留记录，可再出新版本重审）。"""
        return self._transition(asset_id, version, "rejected")

    def apply_review_decision(self, asset_id: str, version: str, decision: str) -> VisualAssetVersion:
        """消费 Review 三态决策（§10.2 映射表的单一入口）。

        APPROVED            → approve()
        REJECTED            → reject()
        APPROVED_WITH_EDIT  → 编辑内容必须先落成新版本（Artifact 版本化铁律：
                              原文件不动），然后对新版本 approve()——本方法
                              不接受"原地改"，直接报错指路。
        """
        if decision == DECISION_APPROVED:
            return self.approve(asset_id, version)
        if decision == DECISION_REJECTED:
            return self.reject(asset_id, version)
        if decision == DECISION_APPROVED_WITH_EDIT:
            raise VisualRegistryError(
                "APPROVED_WITH_EDIT：请先把编辑后的档案落成新版本（new_version），"
                "再对新版本 approve()——原版本内容绝不覆盖。")
        raise VisualRegistryError(f"未知 Review 决策: {decision!r}")

    def archive(self, asset_id: str) -> VisualAsset:
        """资产级退役：全部版本 → archived（内容保留，只读）。"""
        asset = self.get(asset_id)
        for ver in asset.versions:
            ver.status = "archived"
        asset.archived = True
        from core.token_log import now_iso
        asset.updated_at = now_iso()
        self.save()
        return asset

    def _transition(self, asset_id: str, version: str, target: str) -> VisualAssetVersion:
        asset = self.get(asset_id)
        ver = asset.get_version(version)
        if ver is None:
            raise VisualRegistryError(f"资产 {asset_id} 不存在版本 {version!r}")
        legal = LEGAL_STATUS_TRANSITIONS.get(ver.status, ())
        if target not in legal:
            raise VisualRegistryError(
                f"非法状态转移: {asset_id}:{version} {ver.status} → {target}"
                f"（合法 {list(legal)}）")
        ver.status = target
        from core.token_log import now_iso
        asset.updated_at = now_iso()
        self.save()
        return ver

    # ------------------------------------------------------------------
    # 查询：生效视觉身份（Composer 消费）
    # ------------------------------------------------------------------
    def active_version(self, asset_id: str) -> Optional[VisualAssetVersion]:
        """当前生效（approved）版本；无 approved 返回 None——绝不拿 draft 充数。"""
        return self.get(asset_id).latest_approved()

    def active_profile(self, kind: str, name: str
                       ) -> Optional[Tuple[str, str, VisualAssetVersion]]:
        """(kind, name) → (asset_id, version, 生效版本)；未建档/未生效返回 None。"""
        asset = self.find(kind, name)
        if asset is None:
            return None
        ver = self.active_version(asset.asset_id)
        if ver is None:
            return None
        return asset.asset_id, ver.version, ver

    def approved_style_assets(self) -> List[VisualAsset]:
        return [a for a in self.assets.values()
                if a.kind == "style" and a.latest_approved() is not None and not a.archived]

    def summary(self) -> dict:
        """资产/版本计数速览（报告用）。"""
        by_kind: Dict[str, int] = {}
        for a in self.assets.values():
            by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        return {
            "assets": len(self.assets),
            "by_kind": by_kind,
            "versions": sum(len(a.versions) for a in self.assets.values()),
            "active": sum(1 for a in self.assets.values()
                          if a.latest_approved() is not None),
        }


__all__ = [
    "DECISION_APPROVED", "DECISION_APPROVED_WITH_EDIT", "DECISION_REJECTED",
    "VisualRegistryError", "sha256_of_file", "VisualAssetRegistry",
]
