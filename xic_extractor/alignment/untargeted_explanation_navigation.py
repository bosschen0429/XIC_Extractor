"""Internal navigation receipts for the Untargeted explanation prototype."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from xic_extractor.alignment.untargeted_explanation_contract import (
    _LOCATOR_REQUEST_MINT_TOKEN,
    ExplanationLocatorKind,
    ExplanationLocatorRequest,
    ExplanationTopic,
    _require_portable_locator_path,
)
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    fingerprint_file_content,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_LOCATOR_BINDING_SCHEMA_VERSION = "untargeted_explanation_locator_binding_v1"


def explanation_locator_relative_path(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    peak_hypothesis_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    topic: ExplanationTopic,
    artifact_name: str,
) -> str:
    """Return the deterministic route namespace for one navigation artifact."""

    _validate_route_inputs(
        processing_attempt=processing_attempt,
        peak_hypothesis_id=peak_hypothesis_id,
        sample=sample,
        topic=topic,
    )
    _require_portable_locator_path(artifact_name)
    if len(PurePosixPath(artifact_name).parts) != 1:
        raise ValueError("locator artifact_name must be one portable file name")
    basis = {
        "schema_version": _LOCATOR_BINDING_SCHEMA_VERSION,
        "processing_attempt_id": processing_attempt.processing_attempt_id,
        "peak_hypothesis_id": peak_hypothesis_id,
        "canonical_sample_identity_id": sample.identity.sample_identity_id,
        "topic": topic.value,
    }
    digest = hashlib.sha256(
        json.dumps(
            basis,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"explanation-assets/{digest}/{artifact_name}"


def mint_explanation_locator_request(
    *,
    navigation_root: Path,
    artifact_path: Path,
    processing_attempt: ProcessingAttemptIdentity,
    peak_hypothesis_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    topic: ExplanationTopic,
    kind: ExplanationLocatorKind,
) -> ExplanationLocatorRequest:
    """Verify one local artifact and mint its exact route-bound locator receipt."""

    if not isinstance(navigation_root, Path) or not isinstance(artifact_path, Path):
        raise TypeError("navigation_root and artifact_path must be Path values")
    if not isinstance(kind, ExplanationLocatorKind):
        raise TypeError("kind must be an ExplanationLocatorKind")
    relative_path = explanation_locator_relative_path(
        processing_attempt=processing_attempt,
        peak_hypothesis_id=peak_hypothesis_id,
        sample=sample,
        topic=topic,
        artifact_name=artifact_path.name,
    )
    root = navigation_root.resolve()
    expected = root.joinpath(*PurePosixPath(relative_path).parts)
    if not artifact_path.is_file():
        raise ValueError("explanation locator artifact is missing")
    resolved_artifact = artifact_path.resolve(strict=True)
    if not resolved_artifact.is_relative_to(root):
        raise ValueError("explanation locator artifact must stay under navigation_root")
    if expected.resolve(strict=True) != resolved_artifact:
        raise ValueError(
            "explanation locator artifact does not use its route-bound namespace"
        )
    return ExplanationLocatorRequest(
        kind=kind,
        topic=topic,
        relative_path=relative_path,
        processing_attempt_id=processing_attempt.processing_attempt_id,
        peak_hypothesis_id=peak_hypothesis_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        artifact_fingerprint=fingerprint_file_content(resolved_artifact),
        _mint_token=_LOCATOR_REQUEST_MINT_TOKEN,
    )


def _validate_route_inputs(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    peak_hypothesis_id: str,
    sample: ResolvedCanonicalSampleIdentity,
    topic: ExplanationTopic,
) -> None:
    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TypeError("processing_attempt must be a ProcessingAttemptIdentity")
    if not isinstance(peak_hypothesis_id, str) or not peak_hypothesis_id.strip():
        raise ValueError("peak_hypothesis_id must be non-empty text")
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise TypeError("sample must be a ResolvedCanonicalSampleIdentity")
    if not isinstance(topic, ExplanationTopic):
        raise TypeError("topic must be an ExplanationTopic")


__all__ = ()
