# -*- coding: utf-8 -*-
"""Stage 14-B image QA orchestration.

This module is the read-only quality gate over Stage 13's manifest.  All file,
version, reference ownership, and checksum validation happens before a provider
is called.  QA results use the existing Workflow/Artifact/Review machinery;
``quality/qa_manifest.json`` is only an atomic audit/resume index.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from agents.image_qa import ImageQAAgent, ImageQAStep, validate_artifact
from config import FILMAGENT_PROVIDER, get_provider_config
from core.visual import PROFILE_CLASSES
from core.visual_registry import VisualRegistryError
from llm.request_counter import (
    APIRequestCounter,
    MAX_REAL_VISION_REQUESTS,
    get_api_counter,
)
from orchestration.state import OrchestrationError, atomic_write_json
from workflow.artifact import Artifact, ArtifactStore
from workflow.engine import WorkflowEngine
from workflow.errors import StepError
from workflow.models import ReviewDecision, StepStatus, now_iso


QA_DIR_NAME = "quality"
QA_MANIFEST_FILENAME = "qa_manifest.json"
IMAGE_MANIFEST_REL = "generated_images/manifest.json"
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_APPROVED_STEP_STATUSES = (StepStatus.APPROVED, StepStatus.APPROVED_WITH_EDIT)
_APPROVED_REVIEW_DECISIONS = (
    ReviewDecision.APPROVED, ReviewDecision.APPROVED_WITH_EDIT,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_sha256(value: Any, *, label: str) -> str:
    text = str(value or "")
    if not _SHA_RE.fullmatch(text):
        raise OrchestrationError(f"{label} 必须是 64 位十六进制 SHA256")
    return text.lower()


def safe_project_file(project_dir: Path, value: Any, *, label: str) -> Path:
    """Resolve an existing regular file while containing all links in project."""
    if not isinstance(value, (str, os.PathLike)) or not str(value):
        raise OrchestrationError(f"{label} 路径为空或类型非法")
    raw = Path(value)
    if ".." in raw.parts:
        raise OrchestrationError(f"{label} 路径包含禁止的 '..' 段")
    root = Path(project_dir).resolve(strict=True)
    candidate = raw if raw.is_absolute() else root / raw
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise OrchestrationError(f"{label} 文件不存在或无法解析") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise OrchestrationError(f"{label} 路径越出项目目录") from exc
    if not resolved.is_file():
        raise OrchestrationError(f"{label} 必须指向普通文件")
    return resolved


def project_relative(project_dir: Path, path: Path) -> str:
    try:
        return Path(path).resolve(strict=True).relative_to(
            Path(project_dir).resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise OrchestrationError("文件无法转换为项目内相对路径") from exc


def load_stage13_manifest(project_dir: Path, project_id: str) -> Tuple[dict, Path]:
    path = Path(project_dir) / IMAGE_MANIFEST_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OrchestrationError("Stage 13 manifest 不存在或无法读取") from exc
    except ValueError as exc:
        raise OrchestrationError("Stage 13 manifest 不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise OrchestrationError("Stage 13 manifest 顶层必须是对象")
    version = data.get("version", data.get("schema_version"))
    if version != "1":
        raise OrchestrationError(f"Stage 13 manifest 版本不支持: {version!r}")
    if data.get("project_id") != project_id:
        raise OrchestrationError("Stage 13 manifest project_id 与当前项目不一致")
    if not isinstance(data.get("images"), list):
        raise OrchestrationError("Stage 13 manifest.images 必须是列表")
    for index, item in enumerate(data["images"]):
        if not isinstance(item, dict):
            raise OrchestrationError(f"Stage 13 manifest.images[{index}] 必须是对象")
    return data, path


def select_generated_entries(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> List[dict]:
    generated: List[dict] = []
    by_id: Dict[str, dict] = {}
    for index, item in enumerate(manifest.get("images") or []):
        if item.get("status") != "generated":
            continue
        required = ("image_id", "shot_ref", "path", "sha256")
        if not all(isinstance(item.get(k), str) and item.get(k) for k in required):
            raise OrchestrationError(
                f"generated manifest 条目 {index} 缺少 image_id/shot_ref/path/sha256")
        image_id = item["image_id"]
        if image_id in by_id:
            raise OrchestrationError(f"generated image_id 重复: {image_id}")
        by_id[image_id] = item
        generated.append(item)

    requested = spec.get("qa_image_ids")
    if requested in (None, []):
        chosen = generated
    else:
        if not isinstance(requested, list):
            raise OrchestrationError("qa_image_ids 必须是 image_id 列表")
        ids = []
        for value in requested:
            if not isinstance(value, str) or not value:
                raise OrchestrationError("qa_image_ids 只能包含非空字符串")
            if value in ids:
                raise OrchestrationError(f"qa_image_ids 含重复 ID: {value}")
            if value not in by_id:
                raise OrchestrationError(f"qa_image_ids 含未知或非 generated ID: {value}")
            ids.append(value)
        chosen = [by_id[value] for value in ids]

    raw_max = spec.get("max_qa_images", 3)
    if not isinstance(raw_max, int) or isinstance(raw_max, bool):
        raise OrchestrationError("max_qa_images 必须是整数")
    max_images = raw_max
    if max_images < 0:
        raise OrchestrationError("max_qa_images 不能为负数")
    cap = min(max_images, MAX_REAL_VISION_REQUESTS)
    return list(chosen[:cap])


def build_reference_index(registry: Any) -> Dict[str, List[Tuple[Any, Any, Any]]]:
    """D14-REF-01: scan every immutable version; never use active_version()."""
    index: Dict[str, List[Tuple[Any, Any, Any]]] = {}
    assets = getattr(registry, "assets", None)
    if not isinstance(assets, dict):
        raise OrchestrationError("VisualAssetRegistry.assets 结构非法")
    for asset in assets.values():
        for version in getattr(asset, "versions", []) or []:
            for ref in getattr(version, "reference_images", []) or []:
                image_id = str(getattr(ref, "image_id", "") or "")
                if image_id:
                    index.setdefault(image_id, []).append((asset, version, ref))
    return index


def _exact_profiles(registry: Any, mapping: Any) -> List[dict]:
    if not isinstance(mapping, dict) or not mapping:
        raise OrchestrationError("profile_versions 必须是非空 mapping")
    profiles: List[dict] = []
    for asset_id, version_name in mapping.items():
        if not isinstance(asset_id, str) or not asset_id or \
                not isinstance(version_name, str) or not version_name:
            raise OrchestrationError("profile_versions 的键和值必须是非空字符串")
        try:
            asset = registry.get(asset_id)
        except (VisualRegistryError, KeyError) as exc:
            raise OrchestrationError(f"profile_versions 资产不存在: {asset_id}") from exc
        version = asset.get_version(version_name)
        if version is None:
            raise OrchestrationError(f"精确视觉版本不存在: {asset_id}:{version_name}")
        if version.status not in ("approved", "superseded"):
            raise OrchestrationError(
                f"精确视觉版本状态非法: {asset_id}:{version_name}:{version.status}")
        cls = PROFILE_CLASSES.get(asset.kind)
        if cls is None or version.profile is None or not isinstance(version.profile, cls):
            raise OrchestrationError(
                f"精确视觉版本缺少与 kind 匹配的 profile: {asset_id}:{version_name}")
        fields = {}
        notes = ""
        for field, _required in cls.FIELD_ORDER:
            value = str(getattr(version.profile, field, "") or "").strip()
            if field == "notes":
                notes = value
            else:
                fields[field] = value
        profiles.append({
            "asset_id": asset_id,
            "version": version_name,
            "kind": asset.kind,
            "name": asset.name,
            "fields": fields,
            "notes": notes,
        })
    return profiles


def prepare_image_qa_input(
    *,
    project_dir: Path,
    registry: Any,
    entry: Mapping[str, Any],
    reference_index: Mapping[str, Sequence[Tuple[Any, Any, Any]]],
) -> dict:
    generated_path = safe_project_file(project_dir, entry.get("path"), label="generated image")
    generated_sha = normalize_sha256(entry.get("sha256"), label="generated sha256")
    if sha256_file(generated_path) != generated_sha:
        raise OrchestrationError("generated manifest SHA256 与磁盘文件不一致")

    profiles = _exact_profiles(registry, entry.get("profile_versions"))
    refs_raw = entry.get("reference_images")
    if refs_raw is None:
        refs_raw = []
    if not isinstance(refs_raw, list):
        raise OrchestrationError("reference_images 必须是列表")
    references: List[dict] = []
    seen = set()
    for index, item in enumerate(refs_raw):
        if not isinstance(item, dict):
            raise OrchestrationError(f"reference_images[{index}] 必须是对象")
        image_id = item.get("image_id")
        if not isinstance(image_id, str) or not image_id:
            raise OrchestrationError(f"reference_images[{index}].image_id 非法")
        if image_id in seen:
            raise OrchestrationError(f"reference_images 含重复 image_id: {image_id}")
        seen.add(image_id)
        manifest_sha = normalize_sha256(
            item.get("sha256"), label=f"reference {image_id} manifest sha256")
        matches = list(reference_index.get(image_id) or [])
        if len(matches) != 1:
            raise OrchestrationError(
                f"D14-REF-01 要求 reference {image_id} 全局恰好一处命中，实际 {len(matches)}")
        asset, version, ref = matches[0]
        if version.status not in ("approved", "superseded"):
            raise OrchestrationError(
                f"reference {image_id} 所属版本状态非法: {version.status}")
        registry_sha = normalize_sha256(
            getattr(ref, "checksum", ""), label=f"reference {image_id} registry checksum")
        ref_path = safe_project_file(
            project_dir, getattr(ref, "path", ""), label=f"reference {image_id}")
        disk_sha = sha256_file(ref_path)
        if not (manifest_sha == registry_sha == disk_sha):
            raise OrchestrationError(
                f"reference {image_id} 的 manifest/registry/disk SHA256 不一致")
        references.append({
            "image_id": image_id,
            "sha256": manifest_sha,
            "path": ref_path,
            "asset_id": asset.asset_id,
            "version": version.version,
        })

    qa_input = {
        "schema_version": "1",
        "image_id": entry["image_id"],
        "shot_ref": entry["shot_ref"],
        "generated_path": generated_path,
        "generated_sha256": generated_sha,
        "ground_truth_mode": (
            "profile_and_references" if references else "profile_only"),
        "profiles": profiles,
        "reference_images": references,
        # Process-only helper; never copied to an artifact/index without conversion.
        "_generated_relative_path": project_relative(project_dir, generated_path),
    }
    # Fail before provider construction when the selected profiles contain no checks.
    from agents.image_qa import expected_checks
    expected_checks(qa_input)
    return qa_input


def _default_provider_identity() -> Tuple[str, str]:
    vendor = os.environ.get(FILMAGENT_PROVIDER, "").strip().lower() or "mock"
    if vendor == "mock":
        return "mock", "mock-multimodal"
    try:
        conf = get_provider_config(f"{vendor}_vision")
    except (KeyError, ValueError) as exc:
        raise OrchestrationError(f"没有可用的默认 vision Provider 配置: {vendor}") from exc
    model = str(conf.get("default_model", "") or "")
    if not model:
        raise OrchestrationError(
            f"默认 vision Provider {vendor!r} 未配置精确模型，QA 调用已阻止")
    return vendor, model


def _create_default_provider() -> Any:
    vendor, _model = _default_provider_identity()
    if vendor == "mock":
        from llm.providers.mock import MockMultimodalProvider
        return MockMultimodalProvider()
    from llm.providers.registry import create_provider
    # The vision implementation also satisfies MultimodalProvider.chat and uses
    # the mandated vision budget bucket.
    return create_provider("vision", vendor=vendor)


class ImageQAStage:
    def __init__(
        self,
        *,
        project_id: str,
        project_dir: Path,
        artifacts_dir: Path,
        store: ArtifactStore,
        registry: Any,
        review_handler: Callable,
        provider: Any = None,
        provider_factory: Optional[Callable[[], Any]] = None,
        provider_identity: Optional[Callable[[], Tuple[str, str]]] = None,
        counter: Optional[APIRequestCounter] = None,
        review_mode: str = "",
        max_attempts: int = 3,
    ):
        self.project_id = str(project_id)
        self.project_dir = Path(project_dir)
        self.artifacts_dir = Path(artifacts_dir)
        self.store = store
        self.registry = registry
        self.review_handler = review_handler
        self._provider = provider
        self.provider_factory = provider_factory or _create_default_provider
        self.provider_identity = provider_identity or _default_provider_identity
        self.counter = counter
        self.review_mode = str(review_mode or "")
        self.max_attempts = int(max_attempts)

    @staticmethod
    def disabled_report() -> dict:
        return {
            "enabled": False, "selected": 0, "resumed": 0, "attempted": 0,
            "pass": 0, "needs_review": 0, "fail": 0, "rejected": 0,
            "failed": 0, "budget_consumed": 0, "downstream_eligible": 0,
            "artifact_paths": [], "items": [], "qa_manifest": "",
            "provider": "", "model": "",
            "skipped_reason": "run_image_qa=False（缺省安全默认）",
        }

    def _identity(self) -> Tuple[str, str]:
        if self._provider is not None:
            return (
                str(getattr(self._provider, "vendor", "") or ""),
                str(getattr(self._provider, "model", "") or ""),
            )
        return self.provider_identity()

    def _get_provider(self) -> Any:
        if self._provider is None:
            self._provider = self.provider_factory()
        if not callable(getattr(self._provider, "chat", None)):
            raise OrchestrationError("QA Provider 不具备 MultimodalProvider.chat 调用面")
        budget_kind = getattr(self._provider, "budget_kind", None)
        vendor = str(getattr(self._provider, "vendor", "") or "").lower()
        if budget_kind is None and vendor not in ("", "mock", "spy", "fake", "test"):
            raise OrchestrationError("真实 QA Provider 必须声明 budget_kind='vision'")
        if budget_kind is not None and budget_kind != "vision":
            raise OrchestrationError(
                f"真实 QA Provider budget_kind 必须为 'vision'，实际 {budget_kind!r}")
        return self._provider

    def _load_index(self) -> dict:
        path = self.project_dir / QA_DIR_NAME / QA_MANIFEST_FILENAME
        if not path.is_file():
            return {"version": "1", "project_id": self.project_id, "images": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise OrchestrationError("QA index 无法读取或不是合法 JSON") from exc
        if not isinstance(data, dict) or data.get("version") != "1" or \
                data.get("project_id") != self.project_id or \
                not isinstance(data.get("images"), list):
            raise OrchestrationError("QA index 顶层结构非法")
        if not all(isinstance(item, dict) for item in data["images"]):
            raise OrchestrationError("QA index images 条目必须是对象")
        # ★ 重复 image_id 必须 fail-loud：禁止用 dict comprehension 静默取最后一项
        #   （否则被篡改/损坏的 index 会悄悄改变 resume 与下游资格判定）。
        seen_ids = set()
        for item in data["images"]:
            image_id = str(item.get("image_id", "") or "")
            if not image_id:
                continue
            if image_id in seen_ids:
                raise OrchestrationError(f"QA index 含重复 image_id: {image_id}")
            seen_ids.add(image_id)
        return data

    def _resume_hit(self, qa_input: dict, item: Optional[dict], identity: Tuple[str, str]) -> bool:
        if not isinstance(item, dict):
            return False
        # ★ profile_versions 必须按**有序** (asset_id, version) 列表比较：插入顺序改变会
        #   改变 expected checks 顺序与 Prompt 语义，dict 相等（忽略顺序）会误判为命中。
        expected_profiles = [(p["asset_id"], p["version"]) for p in qa_input["profiles"]]
        stored_profiles = item.get("profile_versions")
        expected_refs = [
            {"image_id": r["image_id"], "sha256": r["sha256"]}
            for r in qa_input["reference_images"]
        ]
        if item.get("generated_path") != qa_input["_generated_relative_path"] or \
                item.get("generated_sha256") != qa_input["generated_sha256"] or \
                not isinstance(stored_profiles, dict) or \
                list(stored_profiles.items()) != expected_profiles or \
                item.get("reference_images") != expected_refs or \
                item.get("provider") != identity[0] or item.get("model") != identity[1]:
            return False
        workflow = item.get("workflow") or {}
        review = item.get("review") or {}
        if workflow.get("status") != "success" or \
                workflow.get("step_status") not in _APPROVED_STEP_STATUSES or \
                review.get("decision") not in _APPROVED_REVIEW_DECISIONS:
            return False
        try:
            art_path = safe_project_file(
                self.project_dir, item.get("artifact_path"), label="QA artifact")
            if sha256_file(art_path) != normalize_sha256(
                    item.get("artifact_sha256"), label="QA artifact sha256"):
                return False
            artifact = json.loads(art_path.read_text(encoding="utf-8"))
            checked = validate_artifact(artifact, qa_input)
        except (OSError, ValueError, OrchestrationError, StepError):
            return False
        if checked.get("qa_decision") != item.get("qa_decision"):
            return False
        # ★ 不信任 index 自报的 downstream_eligible：按 Review + 本地重算 qa_decision 复算，
        #   与存储值不一致即视为 stale（重新 QA），绝不把派生字段当事实。
        expected_eligible = checked.get("qa_decision") == "pass"
        return bool(item.get("downstream_eligible")) == expected_eligible

    @staticmethod
    def _review_decision(step_status: str) -> str:
        if step_status == StepStatus.APPROVED:
            return ReviewDecision.APPROVED
        if step_status == StepStatus.APPROVED_WITH_EDIT:
            return ReviewDecision.APPROVED_WITH_EDIT
        if step_status == StepStatus.REJECTED:
            return ReviewDecision.REJECTED
        return ""

    def _index_entry(self, qa_input: dict, *, run: Any, step_result: Any,
                     artifact: Optional[Artifact], final: Optional[dict],
                     provider: Any, error: str = "") -> dict:
        step_status = str(getattr(step_result, "status", "") or "")
        review_decision = self._review_decision(step_status)
        art_path = ""
        art_sha = ""
        if artifact is not None and artifact.path.is_file():
            art_path = project_relative(self.project_dir, artifact.path)
            art_sha = sha256_file(artifact.path)
        decision = str((final or {}).get("qa_decision", "") or "")
        return {
            "image_id": qa_input["image_id"],
            "shot_ref": qa_input["shot_ref"],
            "generated_path": qa_input["_generated_relative_path"],
            "generated_sha256": qa_input["generated_sha256"],
            "profile_versions": {
                p["asset_id"]: p["version"] for p in qa_input["profiles"]},
            "reference_images": [
                {"image_id": r["image_id"], "sha256": r["sha256"]}
                for r in qa_input["reference_images"]],
            "artifact_path": art_path,
            "artifact_sha256": art_sha,
            "workflow": {
                "run_id": str(getattr(run, "run_id", "") or ""),
                "status": str(getattr(run, "status", "") or ""),
                "step_status": step_status,
            },
            "review": {
                "id": str(getattr(step_result, "review_id", "") or ""),
                "decision": review_decision,
            },
            "qa_decision": decision,
            "provider": str((final or {}).get("provider", "") or
                            getattr(provider, "vendor", "") or ""),
            "model": str((final or {}).get("model", "") or
                         getattr(provider, "model", "") or ""),
            "updated_at": now_iso(),
            "downstream_eligible": bool(
                review_decision in _APPROVED_REVIEW_DECISIONS and decision == "pass"),
            "error": str(error or ""),
        }

    def _write_index(self, index: dict, entry: dict) -> None:
        images = index["images"]
        images[:] = [item for item in images if item.get("image_id") != entry["image_id"]]
        images.append(entry)
        index["updated_at"] = now_iso()
        atomic_write_json(
            self.project_dir / QA_DIR_NAME / QA_MANIFEST_FILENAME, index)

    def run(self, spec: Optional[dict] = None) -> dict:
        spec = dict(spec or {})
        run_enabled = spec.get("run_image_qa", False)
        if not isinstance(run_enabled, bool):
            raise OrchestrationError("run_image_qa 必须是 bool")
        qa_resume = spec.get("qa_resume", True)
        if not isinstance(qa_resume, bool):
            raise OrchestrationError("qa_resume 必须是 bool")
        if not run_enabled:
            return self.disabled_report()

        report = self.disabled_report()
        report.update({
            "enabled": True,
            "skipped_reason": "",
            "review_mode": self.review_mode,
            "budget_cap": 0,
        })
        manifest, manifest_path = load_stage13_manifest(self.project_dir, self.project_id)
        selected = select_generated_entries(manifest, spec)
        report["selected"] = len(selected)
        raw_max = spec.get("max_qa_images", 3)
        report["budget_cap"] = min(raw_max, MAX_REAL_VISION_REQUESTS)
        report["manifest"] = project_relative(self.project_dir, manifest_path)
        if not selected:
            report["skipped_reason"] = "没有选中的 generated 图片或 max_qa_images=0"
            return report

        ref_index = build_reference_index(self.registry)
        index = self._load_index()
        latest = {str(item.get("image_id", "")): item for item in index["images"]}
        identity = self._identity()
        report["provider"], report["model"] = identity
        prepared: List[dict] = []
        for entry in selected:
            try:
                qa_input = prepare_image_qa_input(
                    project_dir=self.project_dir,
                    registry=self.registry,
                    entry=entry,
                    reference_index=ref_index,
                )
            except (OrchestrationError, StepError) as exc:
                report["failed"] += 1
                report["items"].append({
                    "image_id": entry.get("image_id", ""),
                    "status": "preflight_failed", "error": str(exc),
                })
                continue
            if qa_resume and self._resume_hit(
                    qa_input, latest.get(qa_input["image_id"]), identity):
                report["resumed"] += 1
                old = latest[qa_input["image_id"]]
                # ★ 不信任 index 自报值：按 Review + qa_decision 本地复算
                resumed_eligible = bool(
                    (old.get("review") or {}).get("decision") in _APPROVED_REVIEW_DECISIONS
                    and old.get("qa_decision") == "pass")
                report["downstream_eligible"] += int(resumed_eligible)
                if old.get("artifact_path"):
                    report["artifact_paths"].append(old["artifact_path"])
                report["items"].append({
                    "image_id": qa_input["image_id"], "status": "resumed",
                    "qa_decision": old.get("qa_decision", ""),
                    "downstream_eligible": resumed_eligible,
                })
                continue
            prepared.append(qa_input)

        if not prepared:
            if index["images"]:
                report["qa_manifest"] = f"{QA_DIR_NAME}/{QA_MANIFEST_FILENAME}"
            return report

        provider = self._get_provider()
        provider_identity = (
            str(getattr(provider, "vendor", "") or ""),
            str(getattr(provider, "model", "") or ""),
        )
        if provider_identity != identity:
            raise OrchestrationError("QA Provider 实例身份与预检身份不一致")
        counter = self.counter or getattr(provider, "counter", None) or get_api_counter()
        counter.set_budget("vision", report["budget_cap"])
        budget_before = counter.real_requests("vision")
        uses_real_budget = getattr(provider, "budget_kind", None) == "vision"
        agent = ImageQAAgent(provider)

        for qa_input in prepared:
            report["attempted"] += 1
            step = ImageQAStep(agent, qa_input["image_id"])
            engine = WorkflowEngine(
                [step], artifacts_dir=self.artifacts_dir,
                review_handler=self.review_handler,
            )
            run = engine.run(
                project_id=self.project_id,
                workflow_id=f"image_qa_{qa_input['image_id'].lower()}",
                inputs={"image_qa_input": qa_input},
                max_attempts=self.max_attempts,
            )
            result = run.step_result(step.step_id)
            if result is None:
                raise OrchestrationError("Workflow 未返回 Image QA Step 结果")
            artifact = result.approved_artifact
            if artifact is None and result.attempts:
                artifact = result.attempts[-1].artifact

            if result.status == StepStatus.REJECTED:
                report["rejected"] += 1
                rejected_final = None
                rejected_error = ""
                try:
                    if artifact is None:
                        raise StepError("rejected Workflow 缺少 QA Artifact")
                    rejected_final = validate_artifact(artifact.read(), qa_input)
                except (OSError, ValueError, StepError) as exc:
                    rejected_error = (
                        f"rejected artifact validation failed: {type(exc).__name__}")
                idx = self._index_entry(
                    qa_input, run=run, step_result=result, artifact=artifact,
                    final=rejected_final, provider=provider,
                    error=rejected_error)
                self._write_index(index, idx)
                report["items"].append({
                    "image_id": qa_input["image_id"], "status": "rejected",
                    "artifact_path": idx["artifact_path"],
                    "qa_decision": idx["qa_decision"],
                    "downstream_eligible": False,
                })
                continue

            if not result.is_success or result.approved_artifact is None:
                report["failed"] += 1
                idx = self._index_entry(
                    qa_input, run=run, step_result=result, artifact=artifact,
                    final=None, provider=provider,
                    error=f"workflow step status {result.status}")
                self._write_index(index, idx)
                report["items"].append({
                    "image_id": qa_input["image_id"], "status": "failed",
                    "error": f"workflow step status {result.status}",
                })
                if uses_real_budget and counter.remaining_budget("vision") == 0:
                    break
                continue

            try:
                source_artifact = result.attempts[-1].artifact if result.attempts else None
                if source_artifact is None:
                    raise StepError("Workflow 缺少审核前原始 QA Artifact")
                source = validate_artifact(source_artifact.read(), qa_input)
                trusted = {
                    key: source[key] for key in
                    ("provider", "model", "request_id", "retry_count", "created_at")
                }
                final = validate_artifact(
                    result.approved_artifact.read(), qa_input,
                    trusted_metadata=trusted)
            except (OSError, ValueError, StepError) as exc:
                report["failed"] += 1
                idx = self._index_entry(
                    qa_input, run=run, step_result=result,
                    artifact=result.approved_artifact, final=None,
                    provider=provider,
                    error=f"final artifact validation failed: {type(exc).__name__}")
                self._write_index(index, idx)
                report["items"].append({
                    "image_id": qa_input["image_id"], "status": "failed",
                    "error": "final approved artifact validation failed",
                })
                continue

            decision = final["qa_decision"]
            report[decision] += 1
            idx = self._index_entry(
                qa_input, run=run, step_result=result,
                artifact=result.approved_artifact, final=final, provider=provider)
            self._write_index(index, idx)
            report["artifact_paths"].append(idx["artifact_path"])
            report["downstream_eligible"] += int(idx["downstream_eligible"])
            report["items"].append({
                "image_id": qa_input["image_id"], "status": "approved",
                "qa_decision": decision, "artifact_path": idx["artifact_path"],
                "downstream_eligible": idx["downstream_eligible"],
            })

        report["budget_consumed"] = max(
            0, counter.real_requests("vision") - budget_before)
        if index["images"]:
            report["qa_manifest"] = f"{QA_DIR_NAME}/{QA_MANIFEST_FILENAME}"
        return report


__all__ = [
    "ImageQAStage", "QA_DIR_NAME", "QA_MANIFEST_FILENAME",
    "sha256_file", "normalize_sha256", "safe_project_file",
    "load_stage13_manifest", "select_generated_entries", "build_reference_index",
    "prepare_image_qa_input",
]
