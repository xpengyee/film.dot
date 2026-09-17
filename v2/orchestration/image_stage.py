# -*- coding: utf-8 -*-
"""
V0.2 Stage 13 —— 编排层「图像段」（v2/orchestration/image_stage.py）

一句话目标（开工指令13 §4.1）：
    让每个 Shot 经 `VisualComposer` 组装 → **真实生成图像** → 版本化落盘 →
    以 `ShotAsset(kind="image")` 挂回 Shot，全程受程序化预算护栏与成本记账约束。

位置（§4.3）：`ProjectOrchestrator.run()` 的 `visual_assets` 段**之后**新增本段。

    Storyboard（已 approved）
        → VisualComposer.compose(shot) → ComposedVisualInput
        → render_prompt() / reference_images / negative_cues
        → ImageGenerationProvider.generate_image(prompt, size, reference)
        → 落盘 generated_images/<GEN_ID>.<ext>（sha256 锚点）
        → manifest.json（★Stage 14 VLM QA 的输入契约：profile_versions + reference_images）
        → 记账 image_generation_calls.jsonl（与 token 账分开，字段风格对齐）
        → Review 三态 → approved 才挂回 Shot
        → Storyboard **新版本**（原文件绝不覆盖）

三条不可退让的纪律：

1. **默认不出图**（§7.3-1）：`run_spec["generate_images"]` 缺省 False →
   零 Provider 调用、零图像文件、零记账行；run_report 如实标注 `enabled=False`。
2. **预算硬上限**（§7.3-2/3）：本次 run 上限 = `min(run_spec.max_images,
   MAX_REAL_IMAGE_REQUESTS)`；达到上限**立即拒绝**（`APIBudgetExceededError`），
   绝不发出第 N+1 次。同一上限同时下推到 `APIRequestCounter` 的 image 桶，
   在真实 HTTP 层形成第二道防线。
3. **绝不伪造出图**（铁律 11）：无 key / 未配置模型 / 预算耗尽 → 如实记
   `failed` / `rejected`，**绝不用占位图、纯色块、mock 结果冒充真实生成**。

落盘与记账分离（§7.5）：
    token 账（既有 TokenLogger）  ← 文本 / 视觉
    请求计数（APIRequestCounter） ← 真实 HTTP attempt（image 独立桶）
    图像账（本模块）              ← 每张图一行，**不含 token 字段**，不造第二套账
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from config import V2_ROOT, estimate_image_cost
from core.shot import Shot, ShotAsset, Storyboard
from core.visual_compose import ComposedVisualInput, VisualComposeError, VisualComposer
from core.token_log import new_request_id, now_iso
from llm.client import LLMError
from llm.request_counter import (
    APIBudgetExceededError,
    APIRequestCounter,
    MAX_REAL_IMAGE_REQUESTS,
    get_api_counter,
)
from llm.providers.openai_compatible_image import ImageGenerationError
from workflow.models import ReviewDecision, WorkflowContext

from orchestration.state import OrchestrationError, atomic_write_json


# ---------------------------------------------------------------------------
# 常量（§6.2 / §6.3）
# ---------------------------------------------------------------------------
GENERATED_DIR_NAME = "generated_images"
MANIFEST_FILENAME = "manifest.json"
LEDGER_FILENAME = "image_generation_calls.jsonl"

DEFAULT_IMAGE_SIZE = "1024*1024"
DEFAULT_MAX_IMAGES = MAX_REAL_IMAGE_REQUESTS

STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

#: 图像账默认目录（§6.2③；`.gitignore` 已覆盖 logs/**/image_generation_calls.jsonl）
DEFAULT_IMAGE_LOG_DIR = V2_ROOT / "logs" / "stage13_image"


# ---------------------------------------------------------------------------
# 引用串 → 图片二进制（§7.2：URL / base64 两种都要处理）
# ---------------------------------------------------------------------------
def _default_fetch(url: str, timeout: float = 60.0) -> bytes:
    """默认下载器（stdlib urllib）。下载失败 / 空内容 → 明确报错，绝不静默留空文件。"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "FilmAgent-V0.2-Stage13"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as exc:                      # 网络层异常统一翻译，不吞
        raise ImageGenerationError(
            f"下载生成图失败（{url}）：{type(exc).__name__}: {exc}") from exc
    if not data:
        raise ImageGenerationError(f"下载生成图得到空内容（{url}）。")
    return data


def resolve_image_bytes(reference: str, *, fetch: Optional[Callable[[str], bytes]] = None) -> bytes:
    """图片引用串 → 二进制。

    三种形态（与 providers/base.py 的既有约定一致）：
        data:image/...;base64,...   内嵌 base64 → 直接解码
        http(s)://...               远端 URL → 下载（fetch 可注入 → 测试 0 网络）
        其他                         本地路径 → 读文件

    任一环节失败都**明确报错**（不返回空 bytes、不落空文件）。
    """
    ref = str(reference or "").strip()
    if not ref:
        raise ImageGenerationError("图像引用串为空：Provider 未返回可用的图片载体。")
    if ref.startswith("data:"):
        head, _, payload = ref.partition(",")
        if ";base64" not in head:
            raise ImageGenerationError(f"不支持的 data URL 编码（非 base64）：{head}")
        try:
            data = base64.b64decode(payload, validate=True)
        except Exception as exc:
            raise ImageGenerationError(f"data URL base64 解码失败：{exc}") from exc
        if not data:
            raise ImageGenerationError("data URL 解码得到空内容。")
        return data
    if ref.startswith(("http://", "https://")):
        return (fetch or _default_fetch)(ref)
    path = Path(ref)
    if not path.is_file():
        raise ImageGenerationError(f"图像引用既不是 data URL / http(s) URL，也不是已存在文件：{ref}")
    data = path.read_bytes()
    if not data:
        raise ImageGenerationError(f"图像文件为空：{ref}")
    return data


#: 魔数 → 扩展名（落盘用；未知按 png 处理，由下游读取器自行容错）
_MAGIC_EXT: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),
)


def guess_extension(data: bytes) -> str:
    """按魔数猜扩展名（PNG / JPEG / GIF / WEBP；未知 → .png）。"""
    for magic, ext in _MAGIC_EXT:
        if data.startswith(magic):
            return ext
    return ".png"


def sha256_of_bytes(data: bytes) -> str:
    """字节 sha256（64 hex）。与 core.visual_registry.sha256_of_file 同一算法口径。"""
    return hashlib.sha256(data).hexdigest()


def shot_ref_of(shot: Shot) -> str:
    """Shot → 与 ComposedVisualInput.shot_ref 完全一致的引用串（过滤/溯源共用）。"""
    return f"S{shot.scene_id}_B{shot.beat_id}_SH{shot.shot_id}"


# ---------------------------------------------------------------------------
# 审核主体（把 manifest 条目伪装成引擎审核回调能消费的形状）
# ---------------------------------------------------------------------------
class _ImageReviewSubject:
    """传给 review_handler 的最小鸭子类型对象（引擎只用到 `.filename` / `.read()`）。"""

    def __init__(self, entry: dict, path: Path):
        self.filename = str(entry.get("path", ""))
        self.path = Path(path)
        self.entry = dict(entry)

    def read(self) -> dict:
        return dict(self.entry)

    def to_dict(self) -> dict:
        return dict(self.entry)


# ---------------------------------------------------------------------------
# ImageStage
# ---------------------------------------------------------------------------
class ImageStage:
    """编排层图像段（每 Shot 一张图；版本化落盘 + 挂回 Shot）。

    依赖全部显式注入（可测、不与 pipeline 循环 import）：
        project_dir      v2/projects/<pid>/（generated_images/ 落在这里）
        artifacts_dir    ArtifactStore 根（Storyboard 新版本落这里）
        store            ArtifactStore 实例
        provider         ImageGenerationProvider（mock 或真实）
        registry         VisualAssetRegistry（ComposedVisualInput 的来源）
        composer         VisualComposer（★出图 prompt 必须来自它，不得绕过）
        review_handler   引擎同款审核回调（auto_approve / scripted / …）
        counter          APIRequestCounter（image 桶预算）
        log_dir          图像账目录（测试注入 tmp_logs）
        fetch            下载器（URL 形态引用；测试注入假下载器 → 0 网络）
        state            OrchestrationState（图像进度镜像；可为 None）
    """

    def __init__(
        self,
        *,
        project_id: str,
        project_dir: Path,
        artifacts_dir: Path,
        store: Any,
        provider: Any,
        registry: Any,
        composer: VisualComposer,
        review_handler: Callable,
        review_mode: str = "",
        counter: Optional[APIRequestCounter] = None,
        log_dir: Optional[Path] = None,
        fetch: Optional[Callable[[str], bytes]] = None,
        episode: int = 1,
        state: Any = None,
    ):
        self.project_id = str(project_id)
        self.project_dir = Path(project_dir)
        self.artifacts_dir = Path(artifacts_dir)
        self.store = store
        self.provider = provider
        self.registry = registry
        self.composer = composer
        self.review_handler = review_handler
        self.review_mode = str(review_mode or "")
        self.counter = counter or get_api_counter()
        self.log_dir = Path(log_dir) if log_dir else DEFAULT_IMAGE_LOG_DIR
        self.fetch = fetch
        self.episode = int(episode)
        self.state = state
        #: 观测位：本段新增的 Storyboard 版本文件名（测试/报告引用）
        self.new_storyboard_versions: List[str] = []

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, storyboards: Sequence[Tuple[int, Storyboard]], spec: Optional[dict] = None) -> dict:
        """执行图像段，返回 run_report 的 `stages["image"]` 字典。"""
        spec = dict(spec or {})
        size = str(spec.get("image_size") or DEFAULT_IMAGE_SIZE)
        shot_filter = [str(x) for x in (spec.get("image_shot_refs") or [])]
        # 显式 None 才回落默认（`max_images=0` 是合法的"本次不出图"信号，不静默变 3）
        _raw_max = spec.get("max_images")
        max_images = DEFAULT_MAX_IMAGES if _raw_max is None else int(_raw_max)
        # ★ 纵深防御：本次 run 上限同时受"真实请求硬上限"约束（§7.3-2）
        cap = max(0, min(max_images, MAX_REAL_IMAGE_REQUESTS))

        report: Dict[str, Any] = {
            "enabled": False,
            "images_requested": 0,
            "images_generated": 0,
            "images_failed": 0,
            "images_rejected": 0,
            "images_skipped": 0,
            "budget_used": 0,
            "budget_cap": cap,
            "budget_rejections": 0,
            "max_images_requested": max_images,
            "review_mode": self.review_mode,
            "cost_total": None,
            "cost_currency": "",
            "unpriced_images": 0,
            "size": size,
            "provider": str(getattr(self.provider, "vendor", "") or ""),
            "manifest": "",
            "shots_total": 0,
            "shots_selected": 0,
            "storyboard_versions": [],
            "skipped_reason": "",
        }

        # ★ 默认不出图（§7.3-1）：不建目录、不调 Provider、不写账
        if not bool(spec.get("generate_images", False)):
            report["skipped_reason"] = (
                "generate_images=False（缺省安全默认：不出图就不花钱）")
            return report

        report["enabled"] = True
        shots = [(sid, shot) for sid, sb in (storyboards or []) if sb is not None
                 for shot in sb.flatten_shots()]
        report["shots_total"] = len(shots)
        selected = [(sid, shot) for sid, shot in shots
                    if not shot_filter or shot_ref_of(shot) in shot_filter]
        report["shots_selected"] = len(selected)
        report["images_requested"] = len(selected)

        gen_dir = self.project_dir / GENERATED_DIR_NAME
        manifest = self._load_manifest(gen_dir)
        next_index = self._next_gen_index(manifest)
        # ★ 把本次上限下推到真实 HTTP 层（第二道防线）
        self.counter.set_budget("image", cap)

        # 已完成出图的 shot（resume 用）：shot_ref → manifest 条目
        done: Dict[str, dict] = {
            str(e.get("shot_ref", "")): e
            for e in manifest["images"]
            if str(e.get("status", "")) == STATUS_GENERATED and e.get("shot_ref")
        }

        touched_sids: List[int] = []
        per_sid_generated: Dict[int, int] = {}
        entries: List[dict] = []
        dispatched = 0

        for sid, shot in selected:
            ref = shot_ref_of(shot)

            # ---- resume：本 shot 已生成过 → 不重复花钱，只补挂引用 ----
            if bool(spec.get("resume", True)) and ref in done:
                prev = done[ref]
                self._attach_shot_asset(shot, str(prev.get("image_id", "")),
                                        str(prev.get("sha256", "")), size)
                if sid not in touched_sids:
                    touched_sids.append(sid)
                report["images_skipped"] += 1
                continue

            # ---- ① compose（★必须经 VisualComposer，不得绕过自己拼 prompt）----
            try:
                ci = self.composer.compose(shot)
            except VisualComposeError as exc:
                report["images_failed"] += 1
                entries.append(self._blank_entry(
                    ref, STATUS_FAILED, size, error=f"VisualComposeError: {exc}",
                    detail="视觉档案缺口，已拦在出图之前（不硬出图）"))
                continue

            prompt = ci.render_prompt()
            reference = self._reference_string(ci)
            profile_versions = self._profile_versions(ci)
            ref_images = self._reference_images(ci)

            # ---- ② 预算检查（★绝不发出第 N+1 次）----
            if dispatched >= cap:
                report["images_rejected"] += 1
                report["budget_rejections"] += 1
                entries.append(self._blank_entry(
                    ref, STATUS_REJECTED, size, profile_versions, ref_images,
                    error=f"{APIBudgetExceededError.__name__}: "
                          f"Real image API request budget exceeded: max={cap}",
                    detail=f"本次 run 图像硬上限 {cap} 已用尽，未发出第 {dispatched + 1} 次请求"))
                continue

            # ---- ③ 生成 + 落盘（每张独立计一次业务调用）----
            dispatched += 1
            report["budget_used"] = dispatched
            image_id = f"GEN_{next_index:03d}"
            next_index += 1

            entry = self._generate_one(
                image_id=image_id, shot_ref=ref, size=size, prompt=prompt,
                reference=reference, profile_versions=profile_versions,
                ref_images=ref_images, gen_dir=gen_dir)
            entries.append(entry)
            self._append_ledger(entry)

            if entry["status"] == STATUS_FAILED:
                report["images_failed"] += 1
                continue

            # ---- ④ 审核（复用既有 Review 三态）----
            decision = self._review(entry, gen_dir)
            if decision == ReviewDecision.REJECTED:
                entry["status"] = STATUS_REJECTED
                report["images_rejected"] += 1
                continue
            if decision == ReviewDecision.APPROVED_WITH_EDIT:
                raise OrchestrationError(
                    f"图像 {image_id} 的审核决策为 approved_with_edit：本阶段不支持"
                    f"（图像是二进制产物，没有'编辑后内容'的确定性来源）。"
                    f"请改为 approved 或 rejected。")
            if decision != ReviewDecision.APPROVED:
                raise OrchestrationError(f"未知图像审核决策：{decision!r}")

            # ---- ⑤ 挂回 Shot（approved 才挂）----
            entry["status"] = STATUS_GENERATED
            report["images_generated"] += 1
            per_sid_generated[sid] = per_sid_generated.get(sid, 0) + 1
            self._attach_shot_asset(shot, image_id, entry["sha256"], size)
            if sid not in touched_sids:
                touched_sids.append(sid)

        # ---- ⑥ Storyboard 新版本（绝不覆盖原文件）----
        for sid in touched_sids:
            sb = next((s for s2, s in (storyboards or []) if s2 == sid and s is not None), None)
            if sb is None:
                continue
            art = self.store.save(
                step_id=f"image_stage_{sid}", attempt=1, name=f"storyboard_{sid}",
                data=sb.to_dict(),
                metadata={"stage": "stage13_image", "episode": self.episode})
            self.new_storyboard_versions.append(art.filename)
            if self.state is not None:
                self._record_state(sid, art.filename, report,
                                   images_generated=per_sid_generated.get(sid, 0))

        # ---- ⑦ manifest 落盘（★结构化证据；Stage 14 QA 的输入契约）----
        manifest["images"].extend(entries)
        manifest["updated_at"] = now_iso()
        if entries:
            gen_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(gen_dir / MANIFEST_FILENAME, manifest)
            report["manifest"] = str(gen_dir / MANIFEST_FILENAME)

        self._finalize_cost(report, entries)
        report["storyboard_versions"] = list(self.new_storyboard_versions)
        return report

    # ------------------------------------------------------------------
    # 单张生成
    # ------------------------------------------------------------------
    def _generate_one(self, *, image_id: str, shot_ref: str, size: str, prompt: str,
                      reference: str, profile_versions: dict, ref_images: list,
                      gen_dir: Path) -> dict:
        """一次业务调用：generate_image → 取二进制 → 落盘 → 组装 manifest 条目。"""
        started = time.monotonic()
        entry: Dict[str, Any] = {
            "image_id": image_id,
            "shot_ref": shot_ref,
            "path": "",
            "sha256": "",
            "size": size,
            "provider": str(getattr(self.provider, "vendor", "") or ""),
            "model": str(getattr(self.provider, "model", "") or ""),
            "prompt_hash": sha256_of_bytes(prompt.encode("utf-8")),
            "prompt_length": len(prompt),
            "reference_images": [dict(r) for r in ref_images],
            "profile_versions": dict(profile_versions),
            "estimated_cost": None,
            "currency": "",
            "duration_ms": 0,
            "status": STATUS_FAILED,
            "request_id": "",
            "retry_count": 0,
            "error": None,
            "created_at": now_iso(),
        }
        try:
            res = self.provider.generate_image(prompt, size=size, reference=reference)
            data = resolve_image_bytes(res.text, fetch=self.fetch)
        except (LLMError, OSError, ValueError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["duration_ms"] = int((time.monotonic() - started) * 1000)
            return entry

        ext = guess_extension(data)
        rel = f"{GENERATED_DIR_NAME}/{image_id}{ext}"
        path = gen_dir / f"{image_id}{ext}"
        if path.exists():                     # ★ 绝不覆盖
            entry["error"] = f"OrchestrationError: 目标图像文件已存在，拒绝覆盖：{path}"
            return entry
        gen_dir.mkdir(parents=True, exist_ok=True)
        try:
            path.write_bytes(data)
        except OSError as exc:
            entry["error"] = f"OSError: 写入图像失败：{exc}"
            return entry

        cost, currency = estimate_image_cost(entry["model"], size)
        entry.update({
            "path": rel,
            "sha256": sha256_of_bytes(data),
            "estimated_cost": cost,
            "currency": currency,
            "status": STATUS_GENERATED,
            "request_id": str(getattr(res, "request_id", "") or ""),
            "retry_count": int(getattr(res, "retry_count", 0) or 0),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error": None,
        })
        return entry

    # ------------------------------------------------------------------
    # 审核
    # ------------------------------------------------------------------
    def _review(self, entry: dict, gen_dir: Path) -> str:
        """走既有审核回调（三态）；返回 ReviewDecision 字符串。"""
        subject = _ImageReviewSubject(entry, gen_dir / Path(str(entry.get("path", ""))).name)
        ctx = WorkflowContext(
            project_id=self.project_id, workflow_id=f"image_{entry.get('image_id')}",
            run_id="image_stage", status="pending", inputs={}, created_at="", updated_at="")
        decision = self.review_handler(subject, ctx)
        return str(getattr(decision, "decision", decision) or "")

    # ------------------------------------------------------------------
    # 挂回 Shot
    # ------------------------------------------------------------------
    @staticmethod
    def _attach_shot_asset(shot: Shot, image_id: str, sha256: str, size: str) -> None:
        """把 `ShotAsset(kind="image")` 挂回 Shot（★最小增量：不动其它任何字段）。

        T14 硬证据：出图的 Shot 相对原 Storyboard **只多这一个 asset**；
        未出图的 Shot 序列化逐字节不变（因此这里绝不改 shot.status 等字段）。
        """
        if not image_id:
            return
        for a in shot.assets:
            if a.kind == "image" and a.ref == image_id:
                return                        # 幂等（resume 重入不重复挂）
        shot.assets.append(ShotAsset(
            kind="image", ref=image_id,
            detail=f"{size} {sha256[:12]}" if sha256 else size))

    # ------------------------------------------------------------------
    # manifest / 记账 / 状态
    # ------------------------------------------------------------------
    def _load_manifest(self, gen_dir: Path) -> dict:
        """读既有 manifest（不存在 → 空清单）。解析失败明确报错，绝不静默清零
        ——静默清零会导致 GEN_ID 复用、旧图被覆盖（§6.3 铁律）。"""
        path = gen_dir / MANIFEST_FILENAME
        if not path.is_file():
            return {"version": "1", "project_id": self.project_id, "images": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OrchestrationError(f"既有 manifest 无法解析：{path}（{exc}）") from exc
        if not isinstance(data, dict) or not isinstance(data.get("images"), list):
            raise OrchestrationError(f"既有 manifest 结构非法（缺 images 数组）：{path}")
        data.setdefault("version", "1")
        data.setdefault("project_id", self.project_id)
        return data

    @staticmethod
    def _next_gen_index(manifest: dict) -> int:
        """下一个 GEN 序号 = 既有最大序号 + 1（★单调递增、绝不复用）。"""
        top = 0
        for e in manifest.get("images") or []:
            iid = str((e or {}).get("image_id", "") or "")
            if iid.startswith("GEN_"):
                try:
                    top = max(top, int(iid[4:]))
                except ValueError:
                    continue
        return top + 1

    def _blank_entry(self, shot_ref: str, status: str, size: str,
                     profile_versions: Optional[dict] = None,
                     ref_images: Optional[list] = None,
                     error: str = "", detail: str = "") -> dict:
        """没有产出图片的 manifest 条目（failed / rejected）——如实登记，不伪造路径。"""
        return {
            "image_id": "",
            "shot_ref": str(shot_ref),
            "path": "",
            "sha256": "",
            "size": str(size),
            "provider": str(getattr(self.provider, "vendor", "") or ""),
            "model": str(getattr(self.provider, "model", "") or ""),
            "prompt_hash": "",
            "prompt_length": 0,
            "reference_images": [dict(r) for r in (ref_images or [])],
            "profile_versions": dict(profile_versions or {}),
            "estimated_cost": None,
            "currency": "",
            "duration_ms": 0,
            "status": str(status),
            "request_id": "",
            "retry_count": 0,
            "error": str(error or ""),
            "detail": str(detail or ""),
            "created_at": now_iso(),
        }

    def _append_ledger(self, entry: dict) -> None:
        """图像账追加一行（字段风格与 TokenLogger 对齐；**不含 token 概念**，§7.5）。"""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": now_iso(),
            "request_id": entry.get("request_id") or new_request_id(),
            "step": "image_generation",
            "agent": "ImageStage",
            "provider": entry.get("provider", ""),
            "model": entry.get("model", ""),
            "image_id": entry.get("image_id", ""),
            "shot_ref": entry.get("shot_ref", ""),
            "size": entry.get("size", ""),
            "prompt_hash": entry.get("prompt_hash", ""),
            "prompt_length": entry.get("prompt_length", 0),
            "reference_images": entry.get("reference_images", []),
            "profile_versions": entry.get("profile_versions", {}),
            "estimated_cost": entry.get("estimated_cost"),
            "currency": entry.get("currency", ""),
            "duration_ms": entry.get("duration_ms", 0),
            "success": entry.get("status") == STATUS_GENERATED,
            "retry_count": entry.get("retry_count", 0),
            "error": entry.get("error"),
            "status": entry.get("status", ""),
            "path": entry.get("path", ""),
            "sha256": entry.get("sha256", ""),
        }
        path = self.log_dir / LEDGER_FILENAME
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _record_state(self, sid: int, artifact: str, report: dict,
                      *, images_generated: int = 0) -> None:
        """把图像段进度镜像进 state.json（纯追加方法，不新增第二套状态机）。

        `images_generated` 是**该场景**的张数（不是 run 总数）；`budget_used/budget_cap`
        是 run 级口径（上限本身就是 run 级的，按场景无法拆分）。
        """
        try:
            self.state.record_image_stage(
                self.episode, sid, artifact=artifact,
                images_generated=int(images_generated),
                budget_used=report["budget_used"], budget_cap=report["budget_cap"])
        except AttributeError:                # state 未实现该方法时不阻断主流程
            pass

    @staticmethod
    def _finalize_cost(report: dict, entries: List[dict]) -> None:
        """成本汇总（★诚实口径）：任一条目缺价 → cost_total 记 null，绝不给残缺合计。"""
        priced = [e for e in entries if e.get("status") == STATUS_GENERATED]
        unpriced = [e for e in priced if e.get("estimated_cost") is None]
        report["unpriced_images"] = len(unpriced)
        if not priced or unpriced:
            report["cost_total"] = None
            report["cost_currency"] = ""
            return
        report["cost_total"] = round(sum(float(e["estimated_cost"]) for e in priced), 8)
        report["cost_currency"] = str(priced[0].get("currency", "") or "")

    # ------------------------------------------------------------------
    # ComposedVisualInput → Provider 入参
    # ------------------------------------------------------------------
    @staticmethod
    def _reference_string(ci: ComposedVisualInput) -> str:
        """参考图引用串：`image_id:sha256` 组合（§7.4；Provider 侧为溯源提示，非图生图输入）。"""
        return ";".join(
            f"{r.get('image_id', '')}:{r.get('checksum', '')}"
            for r in (ci.reference_images or []) if r.get("image_id"))

    @staticmethod
    def _profile_versions(ci: ComposedVisualInput) -> dict:
        """{asset_id: version} —— ★Stage 14 VLM QA 的逐字段裁决锚点（§7.6）。"""
        out: Dict[str, str] = {}
        sections = list(ci.character_sections or [])
        if ci.location_section:
            sections.append(ci.location_section)
        sections.extend(ci.prop_sections or [])
        if ci.style_section:
            sections.append(ci.style_section)
        for sec in sections:
            aid = str((sec or {}).get("asset_id", "") or "")
            if aid:
                out[aid] = str((sec or {}).get("version", "") or "")
        return out

    @staticmethod
    def _reference_images(ci: ComposedVisualInput) -> list:
        """[{"image_id","sha256"}] —— ★QA 的比对锚点（§7.6）。"""
        return [{"image_id": str(r.get("image_id", "")),
                 "sha256": str(r.get("checksum", ""))}
                for r in (ci.reference_images or []) if r.get("image_id")]


__all__ = [
    "ImageStage",
    "resolve_image_bytes",
    "guess_extension",
    "sha256_of_bytes",
    "shot_ref_of",
    "GENERATED_DIR_NAME",
    "MANIFEST_FILENAME",
    "LEDGER_FILENAME",
    "DEFAULT_IMAGE_SIZE",
    "DEFAULT_MAX_IMAGES",
    "STATUS_GENERATED",
    "STATUS_FAILED",
    "STATUS_REJECTED",
]
