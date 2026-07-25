from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ProjectionKind = Literal["targeted", "untargeted"]
_T = TypeVar("_T")


class LineageContractError(ValueError):
    """Raised when a no-write lineage reference is incomplete or inconsistent."""


@dataclass(frozen=True)
class AcquisitionSourceRef:
    source_id: str
    acquisition_format: str
    observation_namespace: str

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id")
        _require_text(self.acquisition_format, "acquisition_format")
        _require_text(self.observation_namespace, "observation_namespace")


@dataclass(frozen=True)
class ProcessingRequestRef:
    request_id: str
    acquisition_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.request_id, "request_id")
        _require_nonempty_unique(self.acquisition_source_ids, "acquisition_source_ids")


@dataclass(frozen=True)
class ProcessingAttemptRef:
    attempt_id: str
    request_id: str
    predecessor_attempt_id: str = ""

    def __post_init__(self) -> None:
        _require_text(self.attempt_id, "attempt_id")
        _require_text(self.request_id, "request_id")
        if self.predecessor_attempt_id == self.attempt_id:
            raise LineageContractError("an attempt cannot be its own predecessor")


@dataclass(frozen=True)
class EvidenceArtifactRef:
    artifact_id: str
    producer_attempt_id: str
    semantic_role: str
    schema_version: str
    content_sha256: str
    dependency_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "artifact_id")
        _require_text(self.producer_attempt_id, "producer_attempt_id")
        _require_text(self.semantic_role, "semantic_role")
        _require_text(self.schema_version, "schema_version")
        _require_sha256(self.content_sha256, "content_sha256")
        _require_unique(self.dependency_artifact_ids, "dependency_artifact_ids")
        if self.artifact_id in self.dependency_artifact_ids:
            raise LineageContractError("an artifact cannot depend on itself")


@dataclass(frozen=True)
class DeliveryCellKey:
    projection_kind: ProjectionKind
    sample_key: str
    projection_identity: str

    def __post_init__(self) -> None:
        if self.projection_kind not in {"targeted", "untargeted"}:
            raise LineageContractError(
                f"unsupported projection_kind {self.projection_kind!r}"
            )
        _require_text(self.sample_key, "sample_key")
        _require_text(self.projection_identity, "projection_identity")


@dataclass(frozen=True)
class DeliveryRevisionRef:
    revision_id: str
    cell_key: DeliveryCellKey
    producer_attempt_id: str
    evidence_artifact_ids: tuple[str, ...]
    authority_approved: bool = False

    def __post_init__(self) -> None:
        _require_text(self.revision_id, "revision_id")
        _require_text(self.producer_attempt_id, "producer_attempt_id")
        _require_nonempty_unique(
            self.evidence_artifact_ids,
            "evidence_artifact_ids",
        )


@dataclass(frozen=True)
class DeliverySealCandidate:
    """No-write closure proposal; validation does not grant writer authority."""

    revision_ids: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    acknowledged_artifact_ids: tuple[str, ...]
    supersedes_seal_id: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_unique(self.revision_ids, "revision_ids")
        _require_nonempty_unique(self.output_artifact_ids, "output_artifact_ids")
        _require_nonempty_unique(
            self.acknowledged_artifact_ids,
            "acknowledged_artifact_ids",
        )


@dataclass(frozen=True)
class ValidatedSealClosure:
    revisions: tuple[DeliveryRevisionRef, ...]
    artifacts: tuple[EvidenceArtifactRef, ...]


def validate_delivery_seal_candidate(
    candidate: DeliverySealCandidate,
    *,
    revisions: Mapping[str, DeliveryRevisionRef],
    artifacts: Mapping[str, EvidenceArtifactRef],
) -> ValidatedSealClosure:
    """Validate lineage closure and two-phase acknowledgement without publishing."""

    selected_revisions = tuple(
        _lookup(revisions, revision_id, "revision")
        for revision_id in candidate.revision_ids
    )
    if any(not revision.authority_approved for revision in selected_revisions):
        raise LineageContractError(
            "delivery seal requires projection-owner approval for every revision"
        )

    required_artifact_ids: set[str] = set(candidate.output_artifact_ids)
    for revision in selected_revisions:
        required_artifact_ids.update(revision.evidence_artifact_ids)

    pending_artifact_ids = list(required_artifact_ids)
    while pending_artifact_ids:
        artifact_id = pending_artifact_ids.pop()
        artifact = _lookup(artifacts, artifact_id, "artifact")
        for dependency_id in artifact.dependency_artifact_ids:
            _lookup(artifacts, dependency_id, "artifact dependency")
            if dependency_id not in required_artifact_ids:
                required_artifact_ids.add(dependency_id)
                pending_artifact_ids.append(dependency_id)
    selected_artifacts = tuple(
        _lookup(artifacts, artifact_id, "artifact")
        for artifact_id in sorted(required_artifact_ids)
    )

    missing_acknowledgements = required_artifact_ids.difference(
        candidate.acknowledged_artifact_ids
    )
    if missing_acknowledgements:
        raise LineageContractError(
            "delivery seal has unacknowledged artifacts: "
            + ", ".join(sorted(missing_acknowledgements))
        )
    unexpected_acknowledgements = set(candidate.acknowledged_artifact_ids).difference(
        artifacts
    )
    if unexpected_acknowledgements:
        raise LineageContractError(
            "delivery seal acknowledges unknown artifacts: "
            + ", ".join(sorted(unexpected_acknowledgements))
        )
    return ValidatedSealClosure(
        revisions=selected_revisions,
        artifacts=selected_artifacts,
    )


def validate_retry_attempt(
    previous: ProcessingAttemptRef,
    retry: ProcessingAttemptRef,
) -> None:
    if retry.attempt_id == previous.attempt_id:
        raise LineageContractError("retry requires a new processing attempt identity")
    if retry.request_id != previous.request_id:
        raise LineageContractError("retry must reference the same processing request")
    if retry.predecessor_attempt_id != previous.attempt_id:
        raise LineageContractError("retry must reference its predecessor attempt")


def _lookup(
    values: Mapping[str, _T],
    key: str,
    label: str,
) -> _T:
    try:
        return values[key]
    except KeyError as exc:
        raise LineageContractError(f"{label} {key!r} is missing") from exc


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise LineageContractError(f"{field} is required")


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise LineageContractError(
            f"{field} must be a lowercase SHA-256 digest"
        )


def _require_unique(values: tuple[str, ...], field: str) -> None:
    if any(not value.strip() for value in values):
        raise LineageContractError(f"{field} cannot contain blank values")
    if len(values) != len(set(values)):
        raise LineageContractError(f"{field} must be unique")


def _require_nonempty_unique(values: tuple[str, ...], field: str) -> None:
    if not values:
        raise LineageContractError(f"{field} must not be empty")
    _require_unique(values, field)
