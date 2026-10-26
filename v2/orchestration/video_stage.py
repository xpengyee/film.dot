# -*- coding: utf-8 -*-
"""
V0.2 Stage 15 —— 编排层「视频段」（v2/orchestration/video_stage.py）

一句话目标（Stage 15 §3）：
    让每张「通过 Stage 14 首帧图 QA」的分镜图，经确定性运动 Prompt → 异步视频 Provider
    （submit / poll）→ 受控下载校验 → Review → 以 `ShotAsset(kind="video")` 挂回 Shot，
    全程受程序化预算护栏、异步 task_id 持久化与 resume 保护。

链路（§3.2）：
    Stage 13 image + Stage 14 approved-like Review/qa_decision==pass + Shot/CameraPlan
        → VideoStage preflight（重新验证准入，不信任 downstream_eligible）
        → deterministic motion prompt（不额外调用文本模型）
        → VideoGenerationProvider.submit_video()
        → 立即原子保存 task_id
        → VideoGenerationProvider.get_video_task()（resume 只轮询原任务）
        → 短期 URL → 受控下载 → 本地 MP4 + SHA256
        → Review
        → generated_videos/manifest.json + ShotAsset(kind="video") + Storyboard 新版本

五条不可退让的纪律：

1. **默认不生成视频**（§9.2）：`run_spec["generate_videos"]` 缺省 False → 零 Provider
   构造、零文件、零目录、零 manifest 写入。
2. **Stage 14 准入必须本地重算**（D15-02）：不信任 QA index 的 `downstream_eligible`；
   对每个候选图重新验证 Stage 13 manifest 唯一性、磁盘 SHA、Stage 14 QA index 唯一性、
   QA Artifact containment + hash、严格 validate_artifact 重算 `qa_decision`、
   Workflow/Step/Review approved-like。失败发生在 Provider 构造与 submit 之前。
3. **submit 后 task_id 先落盘再 poll**（D15-03）：进程中断后 resume 只能 poll 已保存的
   task_id，绝不自动重新 POST；unknown/expired/ambiguous 一律标 failed/stale 要求人工。
4. **提交/轮询/下载是同一计数器的不同 kind**（D15-04）：video（真实提交，硬上限 1）、
   video_poll（轮询，有界）、video_download（下载，单任务最多 1 次成功流程）。
5. **Review 三态**（D15-07）：approved 才挂回 Shot；rejected 保留证据不 attach；
   approved_with_edit fail-loud（视频是二进制，无确定性"编辑后内容"来源）。

落盘与记账（复用 Stage 13 模式）：
    token 账（TokenLogger）          ← 文本/视觉（视频不涉及 token）
    请求计数（APIRequestCounter）     ← 真实 HTTP attempt（video/video_poll/video_download）
    视频账（本模块 video_generation_calls.jsonl）← 每段视频一行，不含 token/签名 URL/key
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from config import estimate_video_cost
from core.shot import Shot, ShotAsset, Storyboard
from core.token_log import new_request_id, now_iso
from llm.client import LLMError
from llm.request_counter import (
    APIBudgetExceededError,
    APIRequestCounter,
    MAX_REAL_VIDEO_SUBMISSIONS,
    get_api_counter,
)
from workflow.models import ReviewDecision, WorkflowContext

from orchestration.state import OrchestrationError, atomic_write_json
from orchestration.image_qa_stage import (
    build_reference_index,
    load_stage13_manifest,
    normalize_sha256,
    prepare_image_qa_input,
    project_relative,
    safe_project_file,
    select_generated_entries,
    sha256_file,
)


# ---------------------------------------------------------------------------
# 常量（§9.5 / §9.6）
# ---------------------------------------------------------------------------
VIDEO_DIR_NAME = "generated_videos"
VIDEO_MANIFEST_FILENAME = "manifest.json"
VIDEO_LEDGER_FILENAME = "video_generation_calls.jsonl"

#: 视频 manifest 顶层版本
VIDEO_MANIFEST_VERSION = "1"

#: 视频条目状态（§9.5）
VIDEO_STATUS_SUBMITTED = "submitted"
VIDEO_STATUS_RUNNING = "running"
VIDEO_STATUS_GENERATED = "generated"
VIDEO_STATUS_REJECTED = "rejected"
VIDEO_STATUS_FAILED = "failed"
VIDEO_STATUS_STALE = "stale"
_VIDEO_STATUSES = (
    VIDEO_STATUS_SUBMITTED, VIDEO_STATUS_RUNNING, VIDEO_STATUS_GENERATED,
    VIDEO_STATUS_REJECTED, VIDEO_STATUS_FAILED, VIDEO_STATUS_STALE,
)

#: 默认最大真实提交（cap=min(max_videos, MAX_REAL_VIDEO_SUBMISSIONS)=1）
DEFAULT_MAX_VIDEOS = MAX_REAL_VIDEO_SUBMISSIONS

#: 默认最大 poll 次数（有界；真实值走 Config poll_interval）
DEFAULT_MAX_POLLS = 60

#: 视频账默认目录（测试注入 tmp_logs）
DEFAULT_VIDEO_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "stage15_video"

#: 下载安全上限（默认 64 MiB；可通过 Config/入参覆盖，仅收严不收宽）
DEFAULT_MAX_VIDEO_BYTES = 64 * 1024 * 1024

#: MP4 ftyp 签名长度（容器校验：data[4:8] == b'ftyp'）
_FTYP_OFFSET = 4

#: 允许的下载 scheme（默认仅 https；测试可通过 allowed_schemes 注入 mock://）
_DEFAULT_ALLOWED_SCHEMES = ("https",)

#: 视为"离线 / 非真实"的 vendor：既不消耗真实预算，也不受"真实 Provider 必须显式
#: 选择 image_id"护栏约束（§9.2 / §15-B6 / T03）。
_MOCK_LIKE_VENDORS = ("", "mock", "spy", "fake", "test")


# ---------------------------------------------------------------------------
# 确定性 Prompt（§15-B6 / D15-06）
# ---------------------------------------------------------------------------
def render_motion_prompt(
    *,
    shot: Shot,
    negative_cues: Sequence[str] = (),
    resolution: str = "",
    duration: float = 0.0,
) -> str:
    """由 Canonical Shot/CameraPlan/continuity + Stage 14 negative_cues 确定性组装
    运动提示词。**绝不调用文本模型润色**（D15-06）。

    固定约束段（保持首帧身份/服装/地点/构图/风格，只描述运动与镜头变化）：
        "Preserve the first-frame character identity, clothing, location,
         composition and style. Describe only motion and camera change."
    """
    cam = shot.camera
    lines: List[str] = []

    subject = str(getattr(shot, "subject", "") or "").strip()
    if subject:
        lines.append(f"Subject: {subject}")

    visual = str(getattr(shot, "visual_description", "") or "").strip()
    if visual:
        lines.append(f"Visual: {visual}")

    actions = [str(a.to_v01().get("name", "") or "").strip()
               for a in (getattr(shot, "actions", None) or []) if hasattr(a, "to_v01")]
    actions = [a for a in actions if a]
    if actions:
        lines.append("Actions: " + "; ".join(actions))

    # CameraPlan：shot_size / camera_angle / camera_movement
    if cam is not None:
        parts = []
        if getattr(cam, "shot_size", ""):
            parts.append(f"shot_size={cam.shot_size}")
        if getattr(cam, "camera_angle", ""):
            parts.append(f"angle={cam.camera_angle}")
        if getattr(cam, "camera_movement", ""):
            parts.append(f"movement={cam.camera_movement}")
        if parts:
            lines.append("Camera: " + ", ".join(parts))

    composition = str(getattr(shot, "composition", "") or "").strip()
    if composition:
        lines.append(f"Composition: {composition}")

    continuity = str(getattr(shot, "continuity", "") or "").strip()
    if continuity:
        lines.append(f"Continuity: {continuity}")

    if resolution:
        lines.append(f"Resolution: {resolution}")
    if duration and duration > 0:
        lines.append(f"Duration: {duration:g}s")

    # 固定约束（身份/服装/地点/构图/风格保持一致）
    lines.append(
        "Constraint: Preserve the first-frame character identity, clothing, "
        "location, composition and style. Describe only motion and camera change."
    )

    prompt = "\n".join(lines)
    if negative_cues:
        prompt += "\nNegative: " + "; ".join(negative_cues)
    return prompt


def motion_input_hash(*, shot_ref: str, shot: Shot, negative_cues: Sequence[str]) -> str:
    """由运动输入（不含 resolution/duration）派生稳定 hash，用于 resume 锚点。"""
    cam = shot.camera
    parts = [
        shot_ref,
        str(getattr(shot, "subject", "") or ""),
        str(getattr(shot, "visual_description", "") or ""),
        ";".join(str(a.to_v01().get("name", "") or "") for a in (shot.actions or [])
                 if hasattr(a, "to_v01")),
        getattr(cam, "shot_size", "") if cam else "",
        getattr(cam, "camera_angle", "") if cam else "",
        getattr(cam, "camera_movement", "") if cam else "",
        str(getattr(shot, "composition", "") or ""),
        str(getattr(shot, "continuity", "") or ""),
        ";".join(negative_cues),
    ]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shot_ref_of(shot: Shot) -> str:
    return f"S{shot.scene_id}_B{shot.beat_id}_SH{shot.shot_id}"


# ---------------------------------------------------------------------------
# 时长规范化（§9.3）
# ---------------------------------------------------------------------------
def normalize_duration(value: Any, *, label: str = "duration") -> float:
    """规范化时长：必须是有限正数；NaN/inf/非正 → OrchestrationError。"""
    try:
        d = float(value)
    except (TypeError, ValueError):
        raise OrchestrationError(f"{label} 不是有限正数：{value!r}")
    if not math.isfinite(d) or d <= 0:
        raise OrchestrationError(f"{label} 必须是有限正数：{value!r}")
    return d


def half_up_round(d: float) -> int:
    """明确 half-up 规则规范化为整数（§9.3）。"""
    return int(math.floor(d + 0.5))


# ---------------------------------------------------------------------------
# 默认下载器（URL → bytes；失败明确报错，不静默留空）
# ---------------------------------------------------------------------------
def _default_fetch(url: str, timeout: float = 60.0,
                   max_bytes: int = DEFAULT_MAX_VIDEO_BYTES) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "FilmAgent-V0.2-Stage15"})
    limit = int(max_bytes)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # ★ 受控读取（§15-B8 / T10）：最多多读 1 字节以判定是否超限，
            #   绝不把超大响应整体读进内存。
            data = resp.read(limit + 1)
    except Exception as exc:
        raise LLMError(f"下载视频失败（{_safe_url(url)}）：{type(exc).__name__}") from exc
    if not data:
        raise LLMError("下载视频得到空内容。")
    if len(data) > limit:
        raise LLMError(f"下载视频超过大小上限（>{limit} 字节）。")
    return data


def _safe_url(url: str) -> str:
    """脱敏 URL：仅保留 scheme + host（绝不泄漏签名 query / path token）。"""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(str(url))
        return f"{parts.scheme}://{parts.netloc}/…"
    except Exception:
        return "<url>"


# ---------------------------------------------------------------------------
# 视频审核主体（复用 Stage 13 鸭子类型形状）
# ---------------------------------------------------------------------------
class _VideoReviewSubject:
    def __init__(self, entry: dict, path: Path):
        self.filename = str(entry.get("path", ""))
        self.path = Path(path)
        self.entry = dict(entry)

    def read(self) -> dict:
        return dict(self.entry)

    def to_dict(self) -> dict:
        return dict(self.entry)


# ---------------------------------------------------------------------------
# VideoStage
# ---------------------------------------------------------------------------
class VideoStage:
    """编排层视频段（每 Shot 一个 clip；异步 submit/poll + 下载校验 + Review 回挂）。"""

    def __init__(
        self,
        *,
        project_id: str,
        project_dir: Path,
        artifacts_dir: Path,
        store: Any,
        provider: Any,
        registry: Any,
        review_handler: Callable,
        review_mode: str = "",
        counter: Optional[APIRequestCounter] = None,
        log_dir: Optional[Path] = None,
        fetch: Optional[Callable[[str], bytes]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        episode: int = 1,
        state: Any = None,
        max_video_bytes: int = DEFAULT_MAX_VIDEO_BYTES,
        allowed_schemes: Sequence[str] = _DEFAULT_ALLOWED_SCHEMES,
        provider_factory: Optional[Callable[[], Any]] = None,
    ):
        self.project_id = str(project_id)
        self.project_dir = Path(project_dir)
        self.artifacts_dir = Path(artifacts_dir)
        self.store = store
        self.provider = provider
        self.provider_factory = provider_factory
        self.registry = registry
        self.review_handler = review_handler
        self.review_mode = str(review_mode or "")
        self.counter = counter or get_api_counter()
        self.log_dir = Path(log_dir) if log_dir else DEFAULT_VIDEO_LOG_DIR
        self.fetch = fetch
        self.sleep = sleep or time.sleep
        self.episode = int(episode)
        self.state = state
        self.max_video_bytes = int(max_video_bytes)
        self.allowed_schemes = tuple(allowed_schemes)
        #: 观测位：本段新增的 Storyboard 版本文件名
        self.new_storyboard_versions: List[str] = []
        #: 惰性构造的 provider 实例（provider=None 且 generate_videos=True 时才造）
        self._built_provider = False

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def run(self, storyboards: Sequence[Tuple[int, Storyboard]],
            spec: Optional[dict] = None) -> dict:
        spec = dict(spec or {})

        report: Dict[str, Any] = {
            "enabled": False,
            "videos_requested": 0,
            "videos_submitted": 0,
            "videos_generated": 0,
            "videos_failed": 0,
            "videos_rejected": 0,
            "videos_skipped": 0,
            "videos_stale": 0,
            "budget_used": 0,
            "budget_cap": 0,
            "submit_attempts": 0,
            "poll_requests": 0,
            "download_requests": 0,
            "cost_total": None,
            "cost_currency": "",
            "unpriced_videos": 0,
            "provider": "",
            "model": "",
            "manifest": "",
            "storyboard_versions": [],
            "skipped_reason": "",
        }

        # ★ 默认不生成（§9.2）：不读输入、不建目录、不构造 Provider、不写 manifest
        if not bool(spec.get("generate_videos", False)):
            report["skipped_reason"] = (
                "generate_videos=False（缺省安全默认：不生成视频就不花钱）")
            return report

        report["enabled"] = True

        # ---- 解析 run_spec 视频字段（§9.2）----
        video_image_ids = self._resolve_video_image_ids(spec)
        max_videos = self._resolve_max_videos(spec)
        resolution = self._resolve_resolution(spec)
        duration_overrides = self._resolve_duration_overrides(spec)
        video_resume = bool(spec.get("video_resume", True))
        max_polls = self._resolve_max_polls(spec)
        poll_interval = float(spec.get("video_poll_interval_seconds", 0.0) or 0.0)
        prompt_extend = bool(spec.get("video_prompt_extend", False))
        watermark = bool(spec.get("video_watermark", False))
        seed = spec.get("video_seed")

        report["budget_cap"] = max_videos

        # ★ 真实 Provider 必须显式选择 image_id（§9.2 / §15-B6 / T03）：
        #   禁止对"全部生成图"隐式发起真实（可能计费）生成。此护栏在预检与
        #   Provider 构造**之前**触发（用 _effective_vendor 免构造推断档位）。
        eff_vendor = self._effective_vendor()
        if eff_vendor is not None and eff_vendor not in _MOCK_LIKE_VENDORS \
                and not video_image_ids:
            raise OrchestrationError(
                f"真实视频 Provider（vendor={eff_vendor!r}）必须显式提供非空 "
                f"video_image_ids（§9.2）；不允许隐式对全部生成图提交真实生成。")

        # ---- 预检：Stage 14 准入重算（在 Provider 构造之前）----
        eligible = self._preflight(
            storyboards, video_image_ids, duration_overrides, report)

        if not eligible:
            return report

        # ---- 惰性构造 Provider（预检通过后）----
        provider = self._get_provider()
        report["provider"] = str(getattr(provider, "vendor", "") or "")
        report["model"] = str(getattr(provider, "model", "") or "")

        # ---- 下推提交预算到真实 HTTP 层（纵深防御）----
        self.counter.set_budget("video", max_videos)
        budget_before = self.counter.real_requests("video")
        self._poll_before = self.counter.real_requests("video_poll")
        self._download_before = self.counter.real_requests("video_download")

        # ---- 视频 manifest 加载 + resume 判定 ----
        video_dir = self.project_dir / VIDEO_DIR_NAME
        manifest = self._load_manifest(video_dir)
        next_index = self._next_video_index(manifest)

        # ---- 逐候选执行 ----
        touched_sids: List[int] = []
        per_sid_generated: Dict[int, int] = {}
        entries: List[dict] = []
        submitted = 0

        for cand in eligible:
            shot = cand["shot"]
            sid = cand["sid"]
            ref = shot_ref_of(shot)

            # ---- resume：完整锚点命中 → 零 submit/poll/download ----
            hit = None
            if video_resume:
                hit = self._resume_hit(
                    manifest, cand, provider, resolution, duration_overrides)
            if hit is not None:
                if hit["status"] == VIDEO_STATUS_GENERATED:
                    report["videos_skipped"] += 1
                    self._attach_shot_asset(shot, hit["video_id"],
                                            hit.get("sha256", ""), hit.get("resolution", ""))
                    if sid not in touched_sids:
                        touched_sids.append(sid)
                    entries.append(dict(hit))
                else:
                    # 未完成但锚点命中（submitted/running）→ 继续 poll 原任务
                    entry = dict(hit)
                    entry = self._poll_and_finish(
                        provider, entry, cand, video_dir, manifest,
                        max_polls, poll_interval, report, resolution, watermark, seed)
                    if entry["status"] == VIDEO_STATUS_GENERATED:
                        report["videos_generated"] += 1
                        per_sid_generated[sid] = per_sid_generated.get(sid, 0) + 1
                        self._attach_shot_asset(shot, entry["video_id"],
                                                entry.get("sha256", ""), entry.get("resolution", ""))
                        if sid not in touched_sids:
                            touched_sids.append(sid)
                    entries.append(entry)
                continue

            # ---- 预算检查（发送前拒绝；绝不出第 N+1 次）----
            if submitted >= max_videos:
                report["videos_rejected"] += 1
                entries.append(self._blank_entry(
                    cand, status=VIDEO_STATUS_REJECTED,
                    error=f"{APIBudgetExceededError.__name__}: Real video API "
                          f"request budget exceeded: max={max_videos}",
                    detail=f"本次 run 视频硬上限 {max_videos} 已用尽，未发出第 {submitted + 1} 次提交"))
                continue

            # ---- 生成 ----
            submitted += 1
            report["videos_submitted"] += 1
            report["budget_used"] = submitted
            report["submit_attempts"] += 1
            video_id = f"VID_{next_index:03d}"
            next_index += 1

            entry = self._submit_one(
                provider, video_id, cand, resolution, duration_overrides,
                prompt_extend, watermark, seed)
            # ★ submit 成功后立即原子落盘 submitted 条目（D15-03）
            manifest["videos"].append(entry)
            self._write_manifest(video_dir, manifest)
            self._append_ledger(entry)

            if entry["status"] in (VIDEO_STATUS_FAILED, VIDEO_STATUS_STALE):
                report["videos_failed"] += 1
                entries.append(entry)
                continue

            # ---- poll → 下载 → review ----
            entry = self._poll_and_finish(
                provider, entry, cand, video_dir, manifest,
                max_polls, poll_interval, report, resolution, watermark, seed)

            if entry["status"] == VIDEO_STATUS_GENERATED:
                report["videos_generated"] += 1
                per_sid_generated[sid] = per_sid_generated.get(sid, 0) + 1
                self._attach_shot_asset(shot, entry["video_id"],
                                        entry.get("sha256", ""), entry.get("resolution", ""))
                if sid not in touched_sids:
                    touched_sids.append(sid)
            elif entry["status"] == VIDEO_STATUS_REJECTED:
                report["videos_rejected"] += 1
            elif entry["status"] in (VIDEO_STATUS_FAILED, VIDEO_STATUS_STALE):
                report["videos_failed"] += 1

            entries.append(entry)

        # ---- Storyboard 新版本（绝不覆盖原文件）----
        for sid in touched_sids:
            sb = next((s for s2, s in (storyboards or []) if s2 == sid and s is not None), None)
            if sb is None:
                continue
            art = self.store.save(
                step_id=f"video_stage_{sid}", attempt=1, name=f"storyboard_{sid}",
                data=sb.to_dict(),
                metadata={"stage": "stage15_video", "episode": self.episode})
            self.new_storyboard_versions.append(art.filename)
            if self.state is not None:
                self._record_state(sid, art.filename, report,
                                   videos_generated=per_sid_generated.get(sid, 0))

        # ---- manifest 最终落盘 + 成本 ----
        manifest["updated_at"] = now_iso()
        self._write_manifest(video_dir, manifest)
        report["manifest"] = str(video_dir / VIDEO_MANIFEST_FILENAME)

        report["poll_requests"] = max(
            0, self.counter.real_requests("video_poll") - self._poll_before)
        report["download_requests"] = max(
            0, self.counter.real_requests("video_download") - self._download_before)
        # 用计数器口径对齐（提交数）
        report["submit_attempts"] = max(
            0, self.counter.real_requests("video") - budget_before)

        self._finalize_cost(report, entries)
        report["storyboard_versions"] = list(self.new_storyboard_versions)
        return report

    # ------------------------------------------------------------------
    # run_spec 解析
    # ------------------------------------------------------------------
    def _resolve_video_image_ids(self, spec: dict) -> List[str]:
        raw = spec.get("video_image_ids")
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise OrchestrationError("video_image_ids 必须是 image_id 列表")
        ids: List[str] = []
        for v in raw:
            if not isinstance(v, str) or not v:
                raise OrchestrationError("video_image_ids 只能包含非空字符串")
            if v in ids:
                raise OrchestrationError(f"video_image_ids 含重复 ID: {v}")
            ids.append(v)
        return ids

    def _resolve_max_videos(self, spec: dict) -> int:
        raw = spec.get("max_videos", DEFAULT_MAX_VIDEOS)
        if isinstance(raw, bool):
            raise OrchestrationError("max_videos 必须是整数")
        try:
            max_videos = int(raw)
        except (TypeError, ValueError):
            raise OrchestrationError(f"max_videos 必须是整数：{raw!r}")
        if max_videos < 0:
            raise OrchestrationError("max_videos 不能为负数")
        # ★ cap = min(value, MAX_REAL_VIDEO_SUBMISSIONS)（§9.2 / D15-04）
        return max(0, min(max_videos, MAX_REAL_VIDEO_SUBMISSIONS))

    def _resolve_resolution(self, spec: dict) -> str:
        raw = spec.get("video_resolution")
        if raw is None or str(raw) == "":
            # 缺省来自 Config capabilities 的第一个声明值
            return self._default_resolution()
        res = str(raw)
        self._check_resolution_capable(res)
        return res

    def _default_resolution(self) -> str:
        try:
            from config import get_provider_config
            conf = get_provider_config("bailian_video")
            caps = conf.get("capabilities") or {}
            resolutions = caps.get("resolutions") or []
            if resolutions:
                return str(resolutions[0])
        except (KeyError, ValueError):
            pass
        raise OrchestrationError(
            "video_resolution 未提供且 Config capabilities 无声明分辨率。")

    def _check_resolution_capable(self, res: str) -> None:
        try:
            from config import get_provider_config
            conf = get_provider_config("bailian_video")
            caps = conf.get("capabilities") or {}
            allowed = caps.get("resolutions") or []
        except (KeyError, ValueError):
            allowed = []
        if allowed and res not in allowed:
            raise OrchestrationError(
                f"video_resolution={res!r} 不在 Config capabilities.resolutions 声明内"
                f"（允许 {allowed}）。首版只允许声明值。")

    def _resolve_duration_overrides(self, spec: dict) -> Dict[str, float]:
        raw = spec.get("video_duration_overrides") or {}
        if not isinstance(raw, dict):
            raise OrchestrationError("video_duration_overrides 必须是 dict")
        out: Dict[str, float] = {}
        for k, v in raw.items():
            out[str(k)] = normalize_duration(v, label=f"duration_overrides[{k}]")
        return out

    def _resolve_max_polls(self, spec: dict) -> int:
        raw = spec.get("video_max_polls")
        if raw is None:
            return DEFAULT_MAX_POLLS
        if isinstance(raw, bool):
            raise OrchestrationError("video_max_polls 必须是整数")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise OrchestrationError(f"video_max_polls 必须是整数：{raw!r}")
        if n < 0:
            raise OrchestrationError("video_max_polls 不能为负数")
        if n > 1000:
            raise OrchestrationError("video_max_polls 超过代码安全上限（1000）")
        return n

    # ------------------------------------------------------------------
    # Stage 14 准入重算（D15-02；Provider 构造之前）
    # ------------------------------------------------------------------
    def _preflight(self, storyboards: Sequence[Tuple[int, Storyboard]],
                   video_image_ids: List[str], duration_overrides: Dict[str, float],
                   report: dict) -> List[dict]:
        """返回 eligible 候选列表（每项含 shot/sid/image entry/qa_input/negative_cues/qa_item）。"""
        # 1) Stage 13 manifest
        manifest, _ = load_stage13_manifest(self.project_dir, self.project_id)
        # 2) 选 generated 图片（保持 video_image_ids 顺序；缺省 = 全部）
        if video_image_ids:
            generated = select_generated_entries(manifest, {"qa_image_ids": video_image_ids,
                                                            "max_qa_images": 1 << 30})
        else:
            generated = select_generated_entries(manifest, {"max_qa_images": 1 << 30})

        report["videos_requested"] = len(generated)
        if not generated:
            report["skipped_reason"] = "没有 eligible 候选图（无 generated 图片）"
            return []

        # 3) Stage 14 QA index
        qa_index = self._load_qa_index()
        qa_latest = {str(item.get("image_id", "")): item for item in qa_index.get("images", [])}

        # 4) reference index（D14-REF-01）
        ref_index = build_reference_index(self.registry)

        # 5) Shot 映射（shot_ref → Shot 恰好一次）
        shots = self._shots_from_storyboards(storyboards)

        eligible: List[dict] = []
        seen_shot_refs: Dict[str, str] = {}
        for entry in generated:
            image_id = str(entry.get("image_id", ""))
            shot_ref = str(entry.get("shot_ref", ""))
            shot = shots.get(shot_ref)
            if shot is None:
                raise OrchestrationError(
                    f"image_id {image_id} 的 shot_ref {shot_ref!r} 无法映射到 Shot")
            if shot_ref in seen_shot_refs and seen_shot_refs[shot_ref] != image_id:
                raise OrchestrationError(
                    f"shot_ref {shot_ref!r} 映射到多个 image_id（歧义）")
            seen_shot_refs[shot_ref] = image_id

            # 6) 复用 Stage 14 严格校验：prepare_image_qa_input
            try:
                qa_input = prepare_image_qa_input(
                    project_dir=self.project_dir,
                    registry=self.registry,
                    entry=entry,
                    reference_index=ref_index,
                )
            except OrchestrationError:
                report["videos_failed"] += 1
                continue

            # 7) QA index 唯一性 + 锚点一致 + Artifact 校验 + 本地重算
            qa_item = qa_latest.get(image_id)
            if not self._verify_qa_item(qa_item, qa_input, entry):
                report["videos_failed"] += 1
                continue

            # 8) negative_cues 提取
            negative_cues = self._negative_cues_from(qa_input)

            eligible.append({
                "sid": shot.scene_id,
                "shot": shot,
                "image_id": image_id,
                "image_entry": entry,
                "qa_input": qa_input,
                "qa_item": qa_item,
                "negative_cues": negative_cues,
            })
        return eligible

    def _shots_from_storyboards(self, storyboards: Sequence[Tuple[int, Storyboard]]) -> Dict[str, Shot]:
        shots: Dict[str, Shot] = {}
        for _sid, sb in (storyboards or []):
            if sb is None:
                continue
            for shot in sb.flatten_shots():
                shots[shot_ref_of(shot)] = shot
        return shots

    def _verify_qa_item(self, qa_item: Optional[dict], qa_input: dict,
                        image_entry: dict) -> bool:
        """严格验证 QA index 条目 + 本地重算 qa_decision（D15-02 第 3-9 条）。"""
        if not isinstance(qa_item, dict):
            return False
        # index 的 generated path/SHA/shot_ref 与 Stage 13 一致
        if qa_item.get("generated_path") != qa_input.get("_generated_relative_path"):
            return False
        if str(qa_item.get("generated_sha256", "")).lower() != qa_input.get("generated_sha256"):
            return False
        if qa_item.get("shot_ref") != qa_input.get("shot_ref"):
            return False
        # Workflow / Step / Review 均为 approved-like
        workflow = qa_item.get("workflow") or {}
        review = qa_item.get("review") or {}
        if workflow.get("status") != "success":
            return False
        if workflow.get("step_status") not in ("APPROVED", "APPROVED_WITH_EDIT"):
            return False
        if review.get("decision") not in ("approved", "approved_with_edit"):
            return False
        # QA Artifact 路径 containment + 存在 + hash 一致
        try:
            art_path = safe_project_file(self.project_dir, qa_item.get("artifact_path"),
                                         label="QA artifact")
        except OrchestrationError:
            return False
        try:
            if sha256_file(art_path) != normalize_sha256(
                    qa_item.get("artifact_sha256"), label="QA artifact sha256"):
                return False
            artifact = json.loads(art_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, OrchestrationError):
            return False
        # 用 Stage 14 严格校验函数重新验证 Artifact，重算 qa_decision
        try:
            from agents.image_qa import validate_artifact
            checked = validate_artifact(artifact, qa_input)
        except Exception:
            return False
        # 本地重算 qa_decision == "pass"
        if checked.get("qa_decision") != "pass":
            return False
        # index 自报 downstream_eligible 若与重算不一致，fail-loud（§15-B5）
        expected_eligible = True  # qa_decision == "pass" 且 approved-like
        if bool(qa_item.get("downstream_eligible")) != expected_eligible:
            return False
        return True

    def _load_qa_index(self) -> dict:
        path = self.project_dir / "quality" / "qa_manifest.json"
        if not path.is_file():
            raise OrchestrationError("Stage 14 QA index 不存在（quality/qa_manifest.json）")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OrchestrationError(f"QA index 无法解析：{path}（{exc}）") from exc
        if not isinstance(data, dict) or data.get("version") != "1" or \
                data.get("project_id") != self.project_id or \
                not isinstance(data.get("images"), list):
            raise OrchestrationError("QA index 顶层结构非法")
        # 重复 image_id fail-loud
        seen = set()
        for item in data["images"]:
            image_id = str(item.get("image_id", "") or "")
            if image_id in seen:
                raise OrchestrationError(f"QA index 含重复 image_id: {image_id}")
            seen.add(image_id)
        return data

    def _negative_cues_from(self, qa_input: dict) -> List[str]:
        """从 Stage 14 精确 profiles 提取非空 negative_cues（去重保序）。"""
        cues: List[str] = []
        for p in (qa_input.get("profiles") or []):
            fields = p.get("fields") or {}
            nc = str(fields.get("negative_cues", "") or "").strip()
            if nc:
                # negative_cues 可能是分号分隔的多条
                for part in nc.split(";"):
                    part = part.strip()
                    if part and part not in cues:
                        cues.append(part)
        return cues

    # ------------------------------------------------------------------
    # Provider 惰性构造
    # ------------------------------------------------------------------
    def _effective_vendor(self) -> Optional[str]:
        """在不构造 Provider 的前提下推断将使用的 vendor（用于真实档护栏，§15-B6）。

        - 已注入 provider → 直接读其 vendor；
        - 有 provider_factory → 无法预判（返回 None，跳过该护栏；仅测试用钩子）；
        - 否则按环境开关 FILMAGENT_PROVIDER 推断（缺省 mock）。
        """
        if self.provider is not None:
            return str(getattr(self.provider, "vendor", "") or "").lower()
        if self.provider_factory is not None:
            return None
        return (os.environ.get("FILMAGENT_PROVIDER", "").strip().lower() or "mock")

    def _get_provider(self) -> Any:
        if self.provider is not None:
            return self.provider
        if self.provider_factory is not None:
            return self.provider_factory()
        from llm.providers.registry import get_default_provider
        return get_default_provider("video")

    # ------------------------------------------------------------------
    # 提交 + 落盘
    # ------------------------------------------------------------------
    def _submit_one(self, provider, video_id: str, cand: dict, resolution: str,
                    duration_overrides: Dict[str, float], prompt_extend: bool,
                    watermark: bool, seed: Optional[int]) -> dict:
        shot = cand["shot"]
        ref = shot_ref_of(shot)

        try:
            plan = self._compute_plan(cand, resolution, duration_overrides)
        except OrchestrationError as exc:
            return self._blank_entry(cand, status=VIDEO_STATUS_FAILED, error=str(exc))

        requested_duration = plan["requested_duration"]
        # capability 越界 → 调用前失败（禁止静默 clamp）
        if not self._duration_in_capability(requested_duration):
            return self._blank_entry(
                cand, status=VIDEO_STATUS_FAILED,
                error=f"requested_duration={requested_duration} 超出 Config capabilities.duration")

        prompt = plan["prompt"]
        prompt_hash = plan["prompt_hash"]
        mih = plan["motion_input_hash"]
        negative_prompt = plan["negative_prompt"]
        source_duration = plan["source_duration"]

        media = str(cand["image_entry"].get("path", "") or "")
        source_image_path = media
        source_image_sha = str(cand["image_entry"].get("sha256", "") or "")
        qa_item = cand.get("qa_item") or {}
        qa_artifact_path = str(qa_item.get("artifact_path", "") or "")
        qa_artifact_sha = str(qa_item.get("artifact_sha256", "") or "")

        started = time.monotonic()
        try:
            result = provider.submit_video(
                prompt,
                media=str(self.project_dir / media) if media else "",
                duration=float(requested_duration),
                resolution=resolution,
                negative_prompt=negative_prompt,
                prompt_extend=prompt_extend,
                watermark=watermark,
                seed=seed,
            )
        except APIBudgetExceededError as exc:
            return self._blank_entry(
                cand, status=VIDEO_STATUS_REJECTED, error=f"{type(exc).__name__}: {exc}")
        except LLMError as exc:
            return self._blank_entry(
                cand, status=VIDEO_STATUS_FAILED,
                error=f"{type(exc).__name__}: {exc}")

        duration_ms = int((time.monotonic() - started) * 1000)

        base = self._build_entry(
            cand, provider, video_id, ref, prompt_hash, len(prompt), mih,
            source_image_path, source_image_sha, qa_artifact_path, qa_artifact_sha,
            resolution, source_duration, requested_duration, seed,
            prompt_extend, watermark, status=VIDEO_STATUS_SUBMITTED,
            task_id=result.task_id, request_id=result.request_id,
            duration_ms=duration_ms, error="", error_code="")

        if not result.task_id:
            # ambiguous submit：无法确认任务是否创建 → stale，要求人工
            base["status"] = VIDEO_STATUS_STALE
            base["task_id"] = ""
            base["error"] = result.error_message or "提交未返回 task_id（ambiguous，禁止自动重提）"
            base["error_code"] = result.error_code
            return base

        if result.status in ("failed", "unknown"):
            base["status"] = VIDEO_STATUS_FAILED
            base["error"] = result.error_message
            base["error_code"] = result.error_code
            base["completed_at"] = now_iso()
            return base

        # submitted/pending/running → 先落 submitted 条目，再 poll
        return base

    def _poll_and_finish(self, provider, entry: dict, cand: dict, video_dir: Path,
                         manifest: dict, max_polls: int, poll_interval: float,
                         report: dict, resolution: str, watermark: bool,
                         seed: Optional[int]) -> dict:
        """poll → 下载 → 校验 → review → 落盘/更新 manifest。"""
        task_id = str(entry.get("task_id", "") or "")
        if not task_id:
            # 无 task_id（ambiguous/stale）→ 直接返回原条目
            return entry

        # ---- poll 循环（有界）----
        final_result = None
        polls_done = 0
        status = str(entry.get("status", "") or "")
        while polls_done < max_polls:
            if status in ("succeeded", "failed", "canceled", "unknown"):
                break
            if poll_interval > 0:
                self.sleep(poll_interval)
            try:
                result = provider.get_video_task(task_id)
            except LLMError as exc:
                entry["status"] = VIDEO_STATUS_RUNNING
                entry["error"] = f"{type(exc).__name__}: {exc}"
                entry["updated_at"] = now_iso()
                self._update_manifest_entry(video_dir, manifest, entry)
                return entry
            polls_done += 1
            status = result.status
            if result.is_terminal:
                final_result = result
                break
            # 更新 running 状态并落盘
            entry["status"] = VIDEO_STATUS_RUNNING
            entry["updated_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)

        if final_result is None:
            # poll 超时：保留 task_id + running，供下次 resume
            entry["status"] = VIDEO_STATUS_RUNNING
            entry["error"] = f"poll 超时（{max_polls} 次）"
            entry["updated_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        if final_result.status != "succeeded":
            # failed/canceled/unknown：记录安全 code/message，不自动重提
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error_code"] = final_result.error_code
            entry["error"] = final_result.error_message or f"任务 {final_result.status}"
            entry["completed_at"] = now_iso()
            entry["updated_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        # ---- 下载（succeeded；单任务最多 1 次成功下载流程）----
        video_url = final_result.video_url
        if not video_url:
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error"] = "任务 succeeded 但无 video_url（Schema 非法）"
            entry["completed_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry
        if not self._url_allowed(video_url):
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error"] = f"视频 URL scheme 不允许：{_safe_url(video_url)}"
            entry["completed_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        # 下载计数（独立 kind；Mock 走 mock 账，真实走 real 账，均进同一审计）
        vendor = str(getattr(provider, "vendor", "") or "").lower()
        if vendor in _MOCK_LIKE_VENDORS:
            self.counter.count_mock_request(
                "video_download",
                provider=str(getattr(provider, "vendor", "") or ""),
                model=str(getattr(provider, "model", "") or ""),
                note="video_download")
        else:
            self.counter.consume_real_request(
                "video_download",
                provider=str(getattr(provider, "vendor", "") or ""),
                model=str(getattr(provider, "model", "") or ""),
                request_id=entry.get("request_id", ""),
                attempt=1, note="video_download")
        try:
            data = self._download(video_url)
        except LLMError as exc:
            # 下载失败：保留 succeeded task_id，resume 只重试下载，不重新生成
            entry["status"] = VIDEO_STATUS_RUNNING
            entry["error"] = f"下载失败（task_id 保留，可 resume 重试下载）：{type(exc).__name__}"
            entry["updated_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        # ---- 校验（非空 / 大小 / MP4 ftyp）----
        try:
            self._verify_video_bytes(data)
        except LLMError as exc:
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error"] = f"视频校验失败：{exc}"
            entry["completed_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        # ---- 原子落盘 + SHA256 ----
        sha = sha256_of_bytes(data)
        rel = f"{VIDEO_DIR_NAME}/{entry['video_id']}.mp4"
        path = video_dir / f"{entry['video_id']}.mp4"
        if path.exists():
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error"] = f"目标视频文件已存在，拒绝覆盖：{path}"
            entry["completed_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry
        video_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._atomic_write_bytes(path, data)
        except OSError as exc:
            entry["status"] = VIDEO_STATUS_FAILED
            entry["error"] = f"OSError: 写入视频失败：{exc}"
            entry["completed_at"] = now_iso()
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry

        entry["path"] = rel
        entry["sha256"] = sha
        entry["mime_type"] = "video/mp4"
        entry["bytes"] = len(data)
        entry["status"] = VIDEO_STATUS_GENERATED
        entry["completed_at"] = now_iso()
        entry["updated_at"] = now_iso()
        cost, currency = estimate_video_cost(
            str(entry.get("model", "") or ""),
            float(entry.get("requested_duration", 0) or 0),
            entry.get("resolution", ""))
        entry["estimated_cost"] = cost
        entry["currency"] = currency

        # ---- Review 三态（D15-07）----
        decision = self._review(entry, video_dir)
        if decision == ReviewDecision.REJECTED:
            entry["status"] = VIDEO_STATUS_REJECTED
            self._update_manifest_entry(video_dir, manifest, entry)
            return entry
        if decision == ReviewDecision.APPROVED_WITH_EDIT:
            raise OrchestrationError(
                f"视频 {entry['video_id']} 的审核决策为 approved_with_edit："
                f"本阶段不支持（视频是二进制，没有'编辑后内容'的确定性来源）。"
                f"请改为 approved 或 rejected。")
        if decision != ReviewDecision.APPROVED:
            raise OrchestrationError(f"未知视频审核决策：{decision!r}")

        # approved：保留 generated 状态
        self._update_manifest_entry(video_dir, manifest, entry)
        return entry

    def _download(self, url: str) -> bytes:
        if self.fetch is not None:
            return self.fetch(url)
        return _default_fetch(url, max_bytes=self.max_video_bytes)

    def _url_allowed(self, url: str) -> bool:
        for scheme in self.allowed_schemes:
            if url.startswith(scheme + "://"):
                return True
        return False

    def _verify_video_bytes(self, data: bytes) -> None:
        if not data:
            raise LLMError("视频内容为空。")
        if len(data) > self.max_video_bytes:
            raise LLMError(f"视频超过大小上限（{len(data)} > {self.max_video_bytes}）。")
        if len(data) < 8 or data[_FTYP_OFFSET:_FTYP_OFFSET + 4] != b"ftyp":
            raise LLMError("视频不是合法 MP4（缺少 ftyp 容器签名）。")

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        import tempfile
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                        prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                if tmp.exists():
                    os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Review
    # ------------------------------------------------------------------
    def _review(self, entry: dict, video_dir: Path) -> str:
        subject = _VideoReviewSubject(entry, video_dir / Path(str(entry.get("path", ""))).name)
        ctx = WorkflowContext(
            project_id=self.project_id, workflow_id=f"video_{entry.get('video_id')}",
            run_id="video_stage", status="pending", inputs={},
            created_at="", updated_at="")
        decision = self.review_handler(subject, ctx)
        return str(getattr(decision, "decision", decision) or "")

    # ------------------------------------------------------------------
    # 挂回 Shot
    # ------------------------------------------------------------------
    @staticmethod
    def _attach_shot_asset(shot: Shot, video_id: str, sha256: str, resolution: str) -> None:
        if not video_id:
            return
        for a in shot.assets:
            if a.kind == "video" and a.ref == video_id:
                return
        shot.assets.append(ShotAsset(
            kind="video", ref=video_id,
            detail=f"{resolution} {sha256[:12]}" if sha256 else resolution))

    # ------------------------------------------------------------------
    # manifest 读写
    # ------------------------------------------------------------------
    def _load_manifest(self, video_dir: Path) -> dict:
        path = video_dir / VIDEO_MANIFEST_FILENAME
        if not path.is_file():
            return {"version": VIDEO_MANIFEST_VERSION,
                    "project_id": self.project_id, "videos": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OrchestrationError(f"视频 manifest 无法解析：{path}（{exc}）") from exc
        if not isinstance(data, dict) or data.get("version") != VIDEO_MANIFEST_VERSION or \
                data.get("project_id") != self.project_id or \
                not isinstance(data.get("videos"), list):
            raise OrchestrationError(f"视频 manifest 顶层结构非法：{path}")
        # 重复 video_id / 非法状态 fail-loud
        seen = set()
        for item in data["videos"]:
            if not isinstance(item, dict):
                raise OrchestrationError("视频 manifest 条目必须是对象")
            vid = str(item.get("video_id", "") or "")
            if vid in seen:
                raise OrchestrationError(f"视频 manifest 含重复 video_id: {vid}")
            seen.add(vid)
            if item.get("status") not in _VIDEO_STATUSES:
                raise OrchestrationError(
                    f"视频 manifest 条目 {vid} 状态非法：{item.get('status')!r}")
        return data

    def _write_manifest(self, video_dir: Path, manifest: dict) -> None:
        manifest["updated_at"] = now_iso()
        video_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(video_dir / VIDEO_MANIFEST_FILENAME, manifest)

    def _update_manifest_entry(self, video_dir: Path, manifest: dict, entry: dict) -> None:
        for i, item in enumerate(manifest["videos"]):
            if item.get("video_id") == entry.get("video_id"):
                manifest["videos"][i] = dict(entry)
                break
        else:
            manifest["videos"].append(dict(entry))
        self._write_manifest(video_dir, manifest)

    @staticmethod
    def _next_video_index(manifest: dict) -> int:
        top = 0
        for e in manifest.get("videos") or []:
            vid = str((e or {}).get("video_id", "") or "")
            if vid.startswith("VID_"):
                try:
                    top = max(top, int(vid[4:]))
                except ValueError:
                    continue
        return top + 1

    # ------------------------------------------------------------------
    # resume 命中判定（D15-08：完整锚点）
    # ------------------------------------------------------------------
    def _resume_hit(self, manifest: dict, cand: dict, provider,
                    resolution: str, duration_overrides: Dict[str, float]) -> Optional[dict]:
        ref = shot_ref_of(cand["shot"])
        negative_cues = cand.get("negative_cues", [])
        # 与 _submit_one 用完全一致的参数重算 plan（含 resolution/duration）
        plan = self._compute_plan(cand, resolution, duration_overrides)
        prompt_hash = plan["prompt_hash"]
        mih = plan["motion_input_hash"]
        image_id = cand["image_id"]
        image_entry = cand["image_entry"]
        source_sha = str(image_entry.get("sha256", "") or "")

        provider_name = str(getattr(provider, "vendor", "") or "")
        model = str(getattr(provider, "model", "") or "")

        for item in manifest.get("videos") or []:
            if item.get("source_image_id") != image_id:
                continue
            if str(item.get("source_image_sha256", "")).lower() != source_sha.lower():
                continue
            if item.get("shot_ref") != ref:
                continue
            if item.get("motion_input_hash") != mih:
                continue
            if item.get("prompt_hash") != prompt_hash:
                continue
            if item.get("provider") != provider_name or item.get("model") != model:
                continue
            # 分辨率 / 请求时长必须一致（§9.5 独立锚点）
            if item.get("resolution") != resolution:
                continue
            if int(item.get("requested_duration", 0) or 0) != plan["requested_duration"]:
                continue
            # completed 项还需验证 output path/SHA 仍存在且一致
            if item.get("status") == VIDEO_STATUS_GENERATED:
                try:
                    out_path = safe_project_file(
                        self.project_dir, item.get("path"), label="video output")
                except OrchestrationError:
                    continue
                if sha256_file(out_path) != str(item.get("sha256", "")).lower():
                    continue
                return dict(item)
            # 非 completed（submitted/running）→ 返回供继续 poll（不重新 submit）
            if item.get("status") in (VIDEO_STATUS_SUBMITTED, VIDEO_STATUS_RUNNING):
                if item.get("task_id"):
                    return dict(item)
        return None

    # ------------------------------------------------------------------
    # 计算一个候选的完整生成计划（submit 与 resume 共用，保证 hash 一致）
    # ------------------------------------------------------------------
    def _compute_plan(self, cand: dict, resolution: str,
                      duration_overrides: Dict[str, float]) -> dict:
        shot = cand["shot"]
        ref = shot_ref_of(shot)
        negative_cues = cand.get("negative_cues", [])
        negative_prompt = "; ".join(negative_cues)

        raw_duration = duration_overrides.get(cand["image_id"])
        if raw_duration is None:
            raw_duration = shot.duration
        source_duration = normalize_duration(raw_duration, label=f"duration[{ref}]")
        requested_duration = half_up_round(source_duration)

        prompt = render_motion_prompt(
            shot=shot, negative_cues=negative_cues,
            resolution=resolution, duration=float(requested_duration))
        return {
            "shot_ref": ref,
            "negative_cues": list(negative_cues),
            "negative_prompt": negative_prompt,
            "source_duration": source_duration,
            "requested_duration": requested_duration,
            "prompt": prompt,
            "prompt_hash": sha256_of_bytes(prompt.encode("utf-8")),
            "motion_input_hash": motion_input_hash(
                shot_ref=ref, shot=shot, negative_cues=negative_cues),
        }

    # ------------------------------------------------------------------
    # 条目构造
    # ------------------------------------------------------------------
    def _build_entry(self, cand, provider, video_id, ref, prompt_hash, prompt_length, mih,
                     source_image_path, source_image_sha, qa_artifact_path,
                     qa_artifact_sha, resolution, source_duration, requested_duration,
                     seed, prompt_extend, watermark, *, status, task_id="",
                     request_id="", duration_ms=0, error="", error_code="") -> dict:
        return {
            "video_id": video_id,
            "shot_ref": ref,
            "source_image_id": cand["image_id"],
            "source_image_path": source_image_path,
            "source_image_sha256": source_image_sha,
            "qa_artifact_path": qa_artifact_path,
            "qa_artifact_sha256": qa_artifact_sha,
            "motion_input_hash": mih,
            "prompt_hash": prompt_hash,
            "prompt_length": int(prompt_length),
            "provider": str(getattr(provider, "vendor", "") or ""),
            "model": str(getattr(provider, "model", "") or ""),
            "protocol": str(getattr(provider, "protocol", "") or ""),
            "resolution": resolution,
            "source_duration": float(source_duration),
            "requested_duration": int(requested_duration),
            "seed": None if seed is None else int(seed),
            "prompt_extend": bool(prompt_extend),
            "watermark": bool(watermark),
            "task_id": task_id,
            "request_id": request_id,
            "path": "",
            "sha256": "",
            "mime_type": "",
            "bytes": 0,
            "estimated_cost": None,
            "currency": "",
            "submit_attempts": 1,
            "poll_requests": 0,
            "download_requests": 0,
            "submitted_at": now_iso(),
            "completed_at": "",
            "review": "",
            "status": status,
            "error": error,
            "error_code": error_code,
            "duration_ms": int(duration_ms),
            "updated_at": now_iso(),
        }

    def _blank_entry(self, cand, *, status, error="", detail="") -> dict:
        ref = shot_ref_of(cand["shot"])
        image_entry = cand["image_entry"]
        return {
            "video_id": "",
            "shot_ref": ref,
            "source_image_id": cand["image_id"],
            "source_image_path": str(image_entry.get("path", "") or ""),
            "source_image_sha256": str(image_entry.get("sha256", "") or ""),
            "qa_artifact_path": "",
            "qa_artifact_sha256": "",
            "motion_input_hash": "",
            "prompt_hash": "",
            "prompt_length": 0,
            "provider": "",
            "model": "",
            "protocol": "",
            "resolution": "",
            "source_duration": 0.0,
            "requested_duration": 0,
            "seed": None,
            "prompt_extend": False,
            "watermark": False,
            "task_id": "",
            "request_id": "",
            "path": "",
            "sha256": "",
            "mime_type": "",
            "bytes": 0,
            "estimated_cost": None,
            "currency": "",
            "submit_attempts": 0,
            "poll_requests": 0,
            "download_requests": 0,
            "submitted_at": "",
            "completed_at": "",
            "review": "",
            "status": status,
            "error": str(error or ""),
            "error_code": "",
            "duration_ms": 0,
            "detail": str(detail or ""),
            "updated_at": now_iso(),
        }

    def _append_ledger(self, entry: dict) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        rec = {
            "timestamp": now_iso(),
            "request_id": entry.get("request_id") or new_request_id(),
            "step": "video_generation",
            "agent": "VideoStage",
            "provider": entry.get("provider", ""),
            "model": entry.get("model", ""),
            "video_id": entry.get("video_id", ""),
            "shot_ref": entry.get("shot_ref", ""),
            "prompt_hash": entry.get("prompt_hash", ""),
            "prompt_length": entry.get("prompt_length", 0),
            "resolution": entry.get("resolution", ""),
            "requested_duration": entry.get("requested_duration", 0),
            "estimated_cost": entry.get("estimated_cost"),
            "currency": entry.get("currency", ""),
            "status": entry.get("status", ""),
            "task_id": entry.get("task_id", ""),
            "error": entry.get("error"),
        }
        path = self.log_dir / VIDEO_LEDGER_FILENAME
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _record_state(self, sid: int, artifact: str, report: dict, *,
                      videos_generated: int = 0) -> None:
        try:
            self.state.record_video_stage(
                self.episode, sid, artifact=artifact,
                videos_generated=int(videos_generated),
                budget_used=report["budget_used"], budget_cap=report["budget_cap"])
        except AttributeError:
            pass

    @staticmethod
    def _finalize_cost(report: dict, entries: List[dict]) -> None:
        priced = [e for e in entries if e.get("status") == VIDEO_STATUS_GENERATED]
        unpriced = [e for e in priced if e.get("estimated_cost") is None]
        report["unpriced_videos"] = len(unpriced)
        if not priced or unpriced:
            report["cost_total"] = None
            report["cost_currency"] = ""
            return
        report["cost_total"] = round(sum(float(e["estimated_cost"]) for e in priced), 8)
        report["cost_currency"] = str(priced[0].get("currency", "") or "")

    def _duration_in_capability(self, requested_duration: int) -> bool:
        try:
            from config import get_provider_config
            conf = get_provider_config("bailian_video")
            caps = conf.get("capabilities") or {}
            dur = caps.get("duration") or {}
            lo = dur.get("min")
            hi = dur.get("max")
        except (KeyError, ValueError):
            return True
        if lo is not None and requested_duration < int(lo):
            return False
        if hi is not None and requested_duration > int(hi):
            return False
        return True


__all__ = [
    "VideoStage",
    "render_motion_prompt",
    "motion_input_hash",
    "normalize_duration",
    "half_up_round",
    "sha256_of_bytes",
    "shot_ref_of",
    "VIDEO_DIR_NAME",
    "VIDEO_MANIFEST_FILENAME",
    "VIDEO_LEDGER_FILENAME",
    "VIDEO_MANIFEST_VERSION",
    "DEFAULT_MAX_VIDEOS",
    "DEFAULT_MAX_POLLS",
    "DEFAULT_MAX_VIDEO_BYTES",
]
