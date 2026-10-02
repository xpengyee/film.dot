# -*- coding: utf-8 -*-
"""Stage 14-B -- structured VLM QA for one generated image.

The orchestration layer resolves files and immutable visual versions.  This
module only consumes the in-process ``image_qa_input`` contract, calls the
injected multimodal provider, and builds a path-free, strictly validated
artifact.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from agents.base import LLMAgent
from core.visual import PROFILE_CLASSES
from llm.client import LLMError
from llm.request_counter import APIBudgetExceededError
from workflow.errors import StepError
from workflow.models import StepOutput, now_iso
from workflow.step import Step


VERDICTS = ("match", "mismatch", "uncertain", "not_visible")
QA_DECISIONS = ("pass", "fail", "needs_review")
GROUND_TRUTH_MODES = ("profile_only", "profile_and_references")

_CHECK_FIELDS = {
    "asset_id", "version", "field", "expected", "verdict", "evidence",
}
_RAW_FIELDS = {
    "checks", "summary",
    # The provider may claim these; they are deliberately ignored and replaced.
    "qa_decision", "provider", "model", "request_id", "retry_count", "created_at",
}
_ARTIFACT_FIELDS = {
    "schema_version", "image_id", "shot_ref", "generated_sha256",
    "ground_truth_mode", "profile_versions", "reference_images", "checks",
    "summary", "qa_decision", "provider", "model", "request_id",
    "retry_count", "created_at",
}


def _fail(message: str) -> StepError:
    return StepError(message)


def expected_checks(image_qa_input: Mapping[str, Any]) -> List[dict]:
    """Derive the exact ordered check set from immutable profile fields."""
    profiles = image_qa_input.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise _fail("image_qa_input.profiles 必须是非空列表")
    out: List[dict] = []
    seen = set()
    for index, item in enumerate(profiles):
        if not isinstance(item, dict):
            raise _fail(f"profiles[{index}] 必须是对象")
        asset_id = item.get("asset_id")
        version = item.get("version")
        kind = item.get("kind")
        fields = item.get("fields")
        if not all(isinstance(v, str) and v for v in (asset_id, version, kind)):
            raise _fail(f"profiles[{index}] 缺少合法 asset_id/version/kind")
        cls = PROFILE_CLASSES.get(kind)
        if cls is None:
            raise _fail(f"profiles[{index}] 的 kind 不支持评分: {kind!r}")
        if not isinstance(fields, dict):
            raise _fail(f"profiles[{index}].fields 必须是对象")
        allowed = {name for name, _ in cls.FIELD_ORDER}
        extra = set(fields) - allowed
        if extra:
            raise _fail(f"profiles[{index}].fields 含未知字段: {sorted(extra)}")
        for field, _required in cls.FIELD_ORDER:
            if field == "notes":
                continue
            value = str(fields.get(field, "") or "").strip()
            if not value:
                continue
            key = (asset_id, version, field)
            if key in seen:
                raise _fail(f"期望检查键重复: {key}")
            seen.add(key)
            out.append({
                "asset_id": asset_id,
                "version": version,
                "field": field,
                "expected": value,
            })
    if not out:
        raise _fail("精确视觉档案没有任何非空可评分字段")
    return out


def qa_decision(checks: Sequence[Mapping[str, Any]]) -> str:
    verdicts = [str(c.get("verdict", "") or "") for c in checks]
    if "mismatch" in verdicts:
        return "fail"
    if "uncertain" in verdicts or "not_visible" in verdicts:
        return "needs_review"
    return "pass"


def validate_provider_payload(raw: Any, expected: Sequence[Mapping[str, Any]]) -> dict:
    """Validate provider JSON without filling, dropping, or rewriting checks."""
    if not isinstance(raw, dict):
        raise _fail(f"VLM 输出顶层必须是对象，实际 {type(raw).__name__}")
    unknown = set(raw) - _RAW_FIELDS
    if unknown:
        raise _fail(f"VLM 输出含未知顶层字段: {sorted(unknown)}")
    checks = raw.get("checks")
    summary = raw.get("summary")
    if not isinstance(checks, list) or not checks:
        raise _fail("VLM 输出 checks 必须是非空列表")
    if not isinstance(summary, str):
        raise _fail("VLM 输出 summary 必须是字符串")

    expected_by_key = {
        (e["asset_id"], e["version"], e["field"]): str(e["expected"])
        for e in expected
    }
    actual_by_key: Dict[Tuple[str, str, str], dict] = {}
    normalized: List[dict] = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise _fail(f"checks[{index}] 必须是对象")
        if set(check) != _CHECK_FIELDS:
            missing = sorted(_CHECK_FIELDS - set(check))
            extra = sorted(set(check) - _CHECK_FIELDS)
            raise _fail(f"checks[{index}] 字段不完整（missing={missing}, extra={extra}）")
        if not all(isinstance(check.get(k), str) for k in _CHECK_FIELDS):
            raise _fail(f"checks[{index}] 的全部字段都必须是字符串")
        key = (check["asset_id"], check["version"], check["field"])
        if key in actual_by_key:
            raise _fail(f"VLM 输出检查键重复: {key}")
        if key not in expected_by_key:
            raise _fail(f"VLM 输出包含未声明检查键: {key}")
        if check["expected"] != expected_by_key[key]:
            raise _fail(f"VLM 改写了检查真值: {key}")
        verdict = check["verdict"]
        if verdict not in VERDICTS:
            raise _fail(f"checks[{index}] verdict 非法: {verdict!r}")
        evidence = check["evidence"]
        if verdict != "match" and not evidence.strip():
            raise _fail(f"checks[{index}] 非 match verdict 必须提供 evidence")
        item = {k: check[k] for k in (
            "asset_id", "version", "field", "expected", "verdict", "evidence")}
        actual_by_key[key] = item
        normalized.append(item)

    if set(actual_by_key) != set(expected_by_key):
        missing = sorted(set(expected_by_key) - set(actual_by_key))
        extra = sorted(set(actual_by_key) - set(expected_by_key))
        raise _fail(f"VLM 检查集合不等于期望集合（missing={missing}, extra={extra}）")
    # Preserve expected/profile order, not provider output order.
    ordered = [actual_by_key[(e["asset_id"], e["version"], e["field"])] for e in expected]
    return {"checks": ordered, "summary": summary}


def _anchors(image_qa_input: Mapping[str, Any]) -> dict:
    profiles = image_qa_input.get("profiles") or []
    refs = image_qa_input.get("reference_images") or []
    return {
        "schema_version": "1",
        "image_id": str(image_qa_input.get("image_id", "") or ""),
        "shot_ref": str(image_qa_input.get("shot_ref", "") or ""),
        "generated_sha256": str(image_qa_input.get("generated_sha256", "") or ""),
        "ground_truth_mode": str(image_qa_input.get("ground_truth_mode", "") or ""),
        "profile_versions": {
            str(p.get("asset_id", "")): str(p.get("version", ""))
            for p in profiles if isinstance(p, dict)
        },
        "reference_images": [
            {"image_id": str(r.get("image_id", "") or ""),
             "sha256": str(r.get("sha256", "") or "")}
            for r in refs if isinstance(r, dict)
        ],
    }


def validate_artifact(
    data: Any,
    image_qa_input: Mapping[str, Any],
    *,
    trusted_metadata: Mapping[str, Any] | None = None,
) -> dict:
    """Strictly validate a persisted/final artifact and recompute its decision."""
    if not isinstance(data, dict):
        raise _fail("Image QA Artifact 必须是对象")
    if set(data) != _ARTIFACT_FIELDS:
        missing = sorted(_ARTIFACT_FIELDS - set(data))
        extra = sorted(set(data) - _ARTIFACT_FIELDS)
        raise _fail(f"Image QA Artifact 字段不完整（missing={missing}, extra={extra}）")
    anchors = _anchors(image_qa_input)
    for key, value in anchors.items():
        if data.get(key) != value:
            raise _fail(f"Image QA Artifact 锚点被修改: {key}")
    if data.get("ground_truth_mode") not in GROUND_TRUTH_MODES:
        raise _fail("Image QA Artifact ground_truth_mode 非法")
    if not all(isinstance(data.get(k), str) for k in (
            "summary", "provider", "model", "request_id", "created_at", "qa_decision")):
        raise _fail("Image QA Artifact 字符串元数据类型非法")
    if not isinstance(data.get("retry_count"), int) or isinstance(data.get("retry_count"), bool):
        raise _fail("Image QA Artifact retry_count 必须是整数")
    if trusted_metadata is not None:
        for key in ("provider", "model", "request_id", "retry_count", "created_at"):
            if data.get(key) != trusted_metadata.get(key):
                raise _fail(f"Image QA Artifact 可信 Provider 元数据被修改: {key}")
    expected = expected_checks(image_qa_input)
    checked = validate_provider_payload(
        {"checks": data.get("checks"), "summary": data.get("summary")}, expected)
    decision = qa_decision(checked["checks"])
    if data.get("qa_decision") != decision:
        raise _fail("Image QA Artifact qa_decision 与本地重算不一致")
    return {**data, "checks": checked["checks"], "qa_decision": decision}


def _prompt_payload(image_qa_input: Mapping[str, Any], expected: Sequence[dict]) -> str:
    profiles = []
    for p in image_qa_input.get("profiles") or []:
        profiles.append({
            "asset_id": p["asset_id"], "version": p["version"],
            "kind": p["kind"], "name": p["name"],
            "notes": str(p.get("notes", "") or ""),
        })
    refs = [
        {"position": i + 1, "image_id": r["image_id"],
         "asset_id": r["asset_id"], "version": r["version"]}
        for i, r in enumerate(image_qa_input.get("reference_images") or [])
    ]
    payload = {
        "image_id": image_qa_input["image_id"],
        "shot_ref": image_qa_input["shot_ref"],
        "image_order": {
            "generated": {"position": 0, "image_id": image_qa_input["image_id"]},
            "references": refs,
        },
        "profiles": profiles,
        "expected_checks": list(expected),
        "negative_cues_rule": (
            "For field negative_cues, verdict=match means none of the prohibited cues appear."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class ImageQAAgent(LLMAgent):
    name = "image_qa"
    needs: List[str] = []

    def __init__(self, provider: Any, *, prompt_path: Path | None = None):
        super().__init__(provider, prompt_name="image_qa", prompt_path=prompt_path)
        self.provider = provider

    def evaluate(self, image_qa_input: Mapping[str, Any]) -> dict:
        expected = expected_checks(image_qa_input)
        generated = image_qa_input.get("generated_path")
        refs = image_qa_input.get("reference_images") or []
        images = (generated,) + tuple(r.get("path") for r in refs)
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": _prompt_payload(image_qa_input, expected)},
        ]
        try:
            response = self.provider.chat(
                messages, images=images, step="image_qa", agent=self.name)
        except APIBudgetExceededError as exc:
            raise StepError(str(exc), retryable=False) from exc
        except LLMError as exc:
            raise StepError(
                f"Image QA Provider 调用失败: {type(exc).__name__}",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from exc
        except Exception as exc:
            # Provider/runtime messages may contain local paths or transport
            # details.  Preserve only the exception class in persisted workflow
            # errors; the original exception remains available via chaining.
            raise StepError(
                f"Image QA Provider 非预期失败: {type(exc).__name__}",
                retryable=False,
            ) from exc
        try:
            raw = self._parse_json(response.text)
            checked = validate_provider_payload(raw, expected)
        except StepError:
            raise
        except Exception as exc:
            raise StepError(f"Image QA 响应校验失败: {type(exc).__name__}: {exc}") from exc

        configured_model = str(getattr(self.provider, "model", "") or "")
        response_model = str(getattr(response, "model", "") or "")
        if configured_model and response_model and configured_model != response_model:
            raise StepError(
                "Image QA Provider 返回的模型身份与获批配置不一致",
                retryable=False,
            )

        artifact = {
            **_anchors(image_qa_input),
            "checks": checked["checks"],
            "summary": checked["summary"],
            "qa_decision": qa_decision(checked["checks"]),
            "provider": str(getattr(self.provider, "vendor", "") or ""),
            "model": response_model or configured_model,
            "request_id": str(getattr(response, "request_id", "") or ""),
            "retry_count": int(getattr(response, "retry_count", 0) or 0),
            "created_at": now_iso(),
        }
        return validate_artifact(artifact, image_qa_input)


class ImageQAStep(Step):
    needs_review = True
    retryable = True
    needs: List[str] = []

    def __init__(self, agent: ImageQAAgent, image_id: str):
        image_id = str(image_id or "")
        super().__init__(
            step_id=f"image_qa_{image_id.lower()}",
            name=f"Image QA {image_id}",
            retryable=True,
            needs=[],
            needs_review=True,
        )
        self.agent = agent
        self.image_id = image_id

    def run(self, context) -> StepOutput:
        qa_input = context.inputs.get("image_qa_input")
        if not isinstance(qa_input, dict):
            raise StepError("调度层必须提供 context.inputs['image_qa_input']", retryable=False)
        if qa_input.get("image_id") != self.image_id:
            raise StepError("image_qa_input.image_id 与 Step 不一致", retryable=False)
        data = self.agent.evaluate(qa_input)
        return StepOutput(
            data=data,
            artifact_name=f"image_qa_{self.image_id.lower()}",
            metadata={"stage": "stage14_image_qa", "image_id": self.image_id},
        )

    def validate(self, data: Any, context) -> List[str]:
        try:
            validate_artifact(data, context.inputs.get("image_qa_input") or {})
            return []
        except StepError as exc:
            return [str(exc)]


__all__ = [
    "ImageQAAgent", "ImageQAStep", "VERDICTS", "QA_DECISIONS",
    "expected_checks", "validate_provider_payload", "validate_artifact", "qa_decision",
]
