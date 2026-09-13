# -*- coding: utf-8 -*-
"""
V0.2 Stage 10 —— Visual Composer（core/visual_compose.py）

作用（stage10_plan §15，Token/Context 控制的核心）：
    ❌ Shot 001 → 复制一份完整人物描述；Shot 002 → 再复制一遍……
    ✅ profile 在 registry 存一份；Shot 只存名字引用（现状 0 改）；
       调用模型之前，Composer 在"那一刻"才展开：

        Shot + VisualAssetRegistry
            → 解析 characters / location / props / style / references
            → ComposedVisualInput（结构化 dict + render_prompt() 紧凑文本）

紧凑序列化：profile 渲染为定长顺序的 'key: value' 行（非空才输出），
一个角色约 100~200 token——不是 JSON dump（省引号/括号/重复键名）。

缺口策略（宁停不错）：参与画面的角色/地点/道具没有 approved 视觉档案 →
默认 allow_partial=False 时明确报错并列出缺口清单；绝不静默用残缺视觉生成。

项目默认风格解析（确定性，不靠 LLM，stage10_plan §7）：
    style_override（显式 asset_id）> registry 构造时指定的 default_style_asset_id
    > 唯一 approved 的 STYLE 资产自动生效 > 都没有 → 明确报错（不静默裸奔）。

硬验收相关承诺：Composer 只读 Shot，绝不改写 Shot/Storyboard——注册视觉资产
前后 Storyboard.to_dict() 逐字节不变（Token 增长只发生在真正调用模型那一刻）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.shot import Shot
from core.visual_registry import VisualAssetRegistry


class VisualComposeError(ValueError):
    """组装失败（视觉缺口 / 无默认风格 / 资产未生效）。message 自带缺口清单。"""


@dataclass
class ComposedVisualInput:
    """一个镜头的"模型输入组装结果"（stage10_plan §15 结构）。

    结构化字段供未来 Image/Video/VLM QA 链按字段消费；
    render_prompt() 输出紧凑文本（未来 ImageGenerationProvider.generate_image
    的 prompt 直接来自它——签名 0 改，见 stage10_plan §16）。
    """
    shot_ref: str = ""                                  # "S{scene}_B{beat}_SH{shot}"
    subject: str = ""
    character_sections: List[Dict[str, Any]] = field(default_factory=list)
    location_section: Optional[Dict[str, Any]] = None
    prop_sections: List[Dict[str, Any]] = field(default_factory=list)
    style_section: Optional[Dict[str, Any]] = None
    shot_instructions: Dict[str, str] = field(default_factory=dict)
    negative_cues: List[str] = field(default_factory=list)
    reference_images: List[Dict[str, str]] = field(default_factory=list)
    camera: Optional[Dict[str, str]] = None             # CameraPlan 序列化（video 链用）
    duration: float = 0.0                               # shot.duration（video 链用）
    gaps: List[str] = field(default_factory=list)       # allow_partial 时记录的缺口

    # ------------------------------------------------------------------
    def section_lines(self, section: Optional[Dict[str, Any]], header: str) -> List[str]:
        """一个 section → [头行, 'key: value' ...]（profile_text 已是紧凑行）。"""
        if not section:
            return []
        out = [f"[{header} {section['asset_id']} {section['version']} name={section['name']}]"]
        out.extend(section["profile_text"])
        return out

    def render_prompt(self) -> str:
        """紧凑文本形态（每行一条信息，无 JSON 语法开销）。"""
        out: List[str] = [f"[SHOT {self.shot_ref}]"]
        if self.subject:
            out.append(f"subject: {self.subject}")
        for sec in self.character_sections:
            out.extend(self.section_lines(sec, "CHARACTER"))
        out.extend(self.section_lines(self.location_section, "LOCATION"))
        for sec in self.prop_sections:
            out.extend(self.section_lines(sec, "PROP"))
        out.extend(self.section_lines(self.style_section, "STYLE"))
        if self.shot_instructions:
            out.append("[SHOT INSTRUCTIONS]")
            for k, v in self.shot_instructions.items():
                if v:
                    out.append(f"{k}: {v}")
        if self.camera:
            cam = self.camera
            out.append("camera: " + "|".join(
                str(cam.get(k, "") or "") for k in ("shot_size", "camera_angle", "camera_movement")))
        if self.duration:
            out.append(f"duration: {self.duration:g}s")
        if self.negative_cues:
            out.append("[NEGATIVE] " + "; ".join(self.negative_cues))
        if self.reference_images:
            out.append("[REFERENCES]")
            for r in self.reference_images:
                out.append(f"{r['image_id']}: {r['path']} (sha256:{r['checksum'][:12]})")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {
            "shot_ref": self.shot_ref,
            "subject": self.subject,
            "character_sections": [dict(s) for s in self.character_sections],
            "location_section": dict(self.location_section) if self.location_section else None,
            "prop_sections": [dict(s) for s in self.prop_sections],
            "style_section": dict(self.style_section) if self.style_section else None,
            "shot_instructions": dict(self.shot_instructions),
            "negative_cues": list(self.negative_cues),
            "reference_images": [dict(r) for r in self.reference_images],
            "camera": dict(self.camera) if self.camera else None,
            "duration": self.duration,
            "gaps": list(self.gaps),
        }


class VisualComposer:
    """Shot → ComposedVisualInput（只读解析层，不改 Shot Schema）。"""

    def __init__(self, registry: VisualAssetRegistry, *,
                 default_style_asset_id: str = ""):
        """
        :param default_style_asset_id: 项目级默认风格资产（registry 显式指定）。
            留空时回落到"唯一 approved 的 STYLE 资产自动生效"。
        """
        self.registry = registry
        self.default_style_asset_id = str(default_style_asset_id or "")

    # ------------------------------------------------------------------
    def compose(self, shot: Shot, *, style_override: str = "",
                extra_refs: Optional[List[str]] = None,
                allow_partial: bool = False) -> ComposedVisualInput:
        """组装一个镜头的视觉输入。

        :param style_override: 镜头级风格覆盖（STYLE 资产 asset_id，未来多风格项目用）。
        :param extra_refs: 额外并入的独立参考束名（REF 资产 name）。
        :param allow_partial: False（默认）= 缺 approved 档案直接报错列缺口；
                              True = 跳过缺口并记录在 gaps（仅限明确允许的场景）。
        """
        gaps: List[str] = []
        out = ComposedVisualInput(
            shot_ref=f"S{shot.scene_id}_B{shot.beat_id}_SH{shot.shot_id}",
            subject=str(shot.subject or ""),
            duration=float(shot.duration or 0.0),
        )
        neg: List[str] = []
        refs: List[Dict[str, str]] = []
        out.camera = shot.camera.to_dict() if shot.camera is not None else None

        # ---- 角色 ----
        for name in shot.characters:
            hit = self.registry.active_profile("character", name)
            if hit is None:
                gaps.append(f"character:{name}")
                continue
            asset_id, version, ver = hit
            out.character_sections.append(self._section(asset_id, version, name, ver))
            neg.extend(self._negatives(ver))
            refs.extend(self._refs(ver))

        # ---- 地点 ----
        loc = str(shot.location or "").strip()
        if loc:
            hit = self.registry.active_profile("location", loc)
            if hit is None:
                gaps.append(f"location:{loc}")
            else:
                asset_id, version, ver = hit
                out.location_section = self._section(asset_id, version, loc, ver)
                neg.extend(self._negatives(ver))
                refs.extend(self._refs(ver))

        # ---- 道具（Shot.assets 里 kind="prop" 的名字引用）----
        for a in shot.assets:
            if a.kind == "prop":
                hit = self.registry.active_profile("prop", a.ref)
                if hit is None:
                    gaps.append(f"prop:{a.ref}")
                    continue
                asset_id, version, ver = hit
                out.prop_sections.append(self._section(asset_id, version, a.ref, ver))
                neg.extend(self._negatives(ver))
                refs.extend(self._refs(ver))
            elif a.kind == "reference":
                hit = self.registry.active_profile("reference", a.ref)
                if hit is None:
                    gaps.append(f"reference:{a.ref}")
                else:
                    refs.extend(self._refs(hit[2]))

        # ---- 独立参考束（extra_refs 传入的 REF 资产名）----
        for ref_name in (extra_refs or []):
            hit = self.registry.active_profile("reference", ref_name)
            if hit is None:
                gaps.append(f"reference:{ref_name}")
            else:
                refs.extend(self._refs(hit[2]))

        # ---- 风格（确定性三级解析；都没有 → 明确报错）----
        style = self._resolve_style(style_override, gaps)
        if style is None:
            if not allow_partial:
                raise VisualComposeError(
                    f"镜头 {out.shot_ref} 无法解析项目默认风格（style_override / "
                    f"default_style_asset_id / 唯一 approved STYLE 均缺失）。")
            gaps.append("style:default")
        else:
            asset_id, version, ver = style
            out.style_section = self._section(asset_id, version, self.registry.get(asset_id).name, ver)
            neg.extend(self._negatives(ver))
            refs.extend(self._refs(ver))

        # ---- 汇总 ----
        out.negative_cues = list(dict.fromkeys(n.strip() for n in neg if n.strip()))
        # 参考图按 image_id 去重（同一版本被多个 section 引用时只保留一份）
        out.reference_images = list({r["image_id"]: r for r in refs}.values())
        out.gaps = list(dict.fromkeys(gaps))
        if out.gaps and not allow_partial:
            raise VisualComposeError(
                f"镜头 {out.shot_ref} 存在 {len(out.gaps)} 个视觉缺口（无 approved "
                f"档案）：{out.gaps}。请先建档并审批，或显式 allow_partial=True。")
        return out

    # ------------------------------------------------------------------
    def _section(self, asset_id: str, version: str, name: str, ver) -> Dict[str, Any]:
        profile = ver.profile
        return {
            "asset_id": asset_id,
            "version": version,
            "name": name,
            "profile_text": profile.render_lines() if profile is not None else [],
            "visual_prompt": str(ver.visual_prompt or ""),
            "reference_images": [r.to_dict() for r in ver.reference_images],
        }

    @staticmethod
    def _negatives(ver) -> List[str]:
        if ver.profile is None:
            return []
        nc = ver.profile.get_negative_cues()
        if not nc:
            return []
        parts = nc.replace("；", ";").replace(",", ";").replace("，", ";").split(";")
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _refs(ver) -> List[Dict[str, str]]:
        return [{"image_id": r.image_id, "path": r.path,
                 "checksum": r.checksum, "kind": r.kind}
                for r in ver.reference_images]

    def _resolve_style(self, style_override: str, gaps: List[str]):
        """三级风格解析（§7）：override > 显式默认 > 唯一 approved STYLE。"""
        if style_override:
            asset = self.registry.assets.get(str(style_override))
            if asset is None or asset.kind != "style":
                raise VisualComposeError(
                    f"style_override {style_override!r} 不是 STYLE 资产。")
            ver = self.registry.active_version(asset.asset_id)
            if ver is None:
                raise VisualComposeError(
                    f"style_override {asset.asset_id} 没有 approved 版本。")
            return asset.asset_id, ver.version, ver
        if self.default_style_asset_id:
            asset = self.registry.assets.get(self.default_style_asset_id)
            if asset is None or asset.kind != "style":
                raise VisualComposeError(
                    f"default_style_asset_id {self.default_style_asset_id!r} 不是 STYLE 资产。")
            ver = self.registry.active_version(asset.asset_id)
            if ver is None:
                raise VisualComposeError(
                    f"默认风格 {asset.asset_id} 没有 approved 版本。")
            return asset.asset_id, ver.version, ver
        approved = self.registry.approved_style_assets()
        if len(approved) == 1:
            asset = approved[0]
            ver = asset.latest_approved()
            return asset.asset_id, ver.version, ver
        if len(approved) > 1:
            raise VisualComposeError(
                f"存在 {len(approved)} 个 approved STYLE 资产（{[a.asset_id for a in approved]}），"
                f"无法自动决定默认风格；请显式指定 default_style_asset_id 或 style_override。")
        return None


__all__ = ["VisualComposer", "ComposedVisualInput", "VisualComposeError"]
