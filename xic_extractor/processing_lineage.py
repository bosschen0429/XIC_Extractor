from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeVar

from xic_extractor.review_actions_models import ImmutableReviewRecordRef
from xic_extractor.sample_metadata import SampleIdentityRef

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
class ProcessingRequestIdentityBasis:
    acquisition_sources: tuple[AcquisitionSourceRef, ...]
    normalized_query_sha256: str
    method_constraints_sha256: str
    algorithm_version: str
    schema_version: str
    contract_version: str

    def __post_init__(self) -> None:
        if not self.acquisition_sources:
            raise LineageContractError("acquisition_sources must not be empty")
        source_ids = tuple(source.source_id for source in self.acquisition_sources)
        _require_nonempty_unique(source_ids, "acquisition source IDs")
        if source_ids != tuple(sorted(source_ids)):
            raise LineageContractError(
                "acquisition_sources must use canonical source_id order"
            )
        _require_sha256(self.normalized_query_sha256, "normalized_query_sha256")
        _require_sha256(
            self.method_constraints_sha256,
            "method_constraints_sha256",
        )
        _require_text(self.algorithm_version, "algorithm_version")
        _require_text(self.schema_version, "schema_version")
        _require_text(self.contract_version, "contract_version")


@dataclass(frozen=True)
class ProcessingRequestRef:
    request_id: str
    identity_basis: ProcessingRequestIdentityBasis

    def __post_init__(self) -> None:
        if self.request_id != derive_processing_request_id(self.identity_basis):
            raise LineageContractError(
                "request_id does not match canonical processing identity basis"
            )


def build_processing_request_ref(
    identity_basis: ProcessingRequestIdentityBasis,
) -> ProcessingRequestRef:
    return ProcessingRequestRef(
        request_id=derive_processing_request_id(identity_basis),
        identity_basis=identity_basis,
    )


def derive_processing_request_id(
    identity_basis: ProcessingRequestIdentityBasis,
) -> str:
    payload = {
        "acquisition_sources": [
            {
                "acquisition_format": source.acquisition_format,
                "observation_namespace": source.observation_namespace,
                "source_id": source.source_id,
            }
            for source in identity_basis.acquisition_sources
        ],
        "algorithm_version": identity_basis.algorithm_version,
        "contract_version": identity_basis.contract_version,
        "method_constraints_sha256": identity_basis.method_constraints_sha256,
        "normalized_query_sha256": identity_basis.normalized_query_sha256,
        "schema_version": identity_basis.schema_version,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"processing-request-sha256:{hashlib.sha256(encoded).hexdigest()}"


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
    sample_ref: SampleIdentityRef
    projection_identity: str

    def __post_init__(self) -> None:
        if self.projection_kind not in {"targeted", "untargeted"}:
            raise LineageContractError(
                f"unsupported projection_kind {self.projection_kind!r}"
            )
        _require_text(self.projection_identity, "projection_identity")

    @property
    def sample_key(self) -> str:
        return self.sample_ref.sample_key


@dataclass(frozen=True)
class DeliveryRevisionRef:
    revision_id: str
    cell_key: DeliveryCellKey
    producer_attempt_id: str
    evidence_artifact_ids: tuple[str, ...]
    review_record_refs: tuple[ImmutableReviewRecordRef, ...] = ()
    authority_approved: bool = False

    def __post_init__(self) -> None:
        _require_text(self.revision_id, "revision_id")
        _require_text(self.producer_attempt_id, "producer_attempt_id")
        _require_nonempty_unique(
            self.evidence_artifact_ids,
            "evidence_artifact_ids",
        )
        review_record_ids = tuple(
            record.review_record_id for record in self.review_record_refs
        )
        _require_unique(review_record_ids, "review_record_refs")


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
    known_seal_ids: frozenset[str] = frozenset(),
) -> ValidatedSealClosure:
    """Validate lineage closure and two-phase acknowledgement without publishing."""

    selected_revisions = tuple(
        _lookup_identity(
            revisions,
            revision_id,
            "revision",
            identity_attribute="revision_id",
        )
        for revision_id in candidate.revision_ids
    )
    cell_keys = tuple(revision.cell_key for revision in selected_revisions)
    if len(cell_keys) != len(set(cell_keys)):
        raise LineageContractError(
            "delivery seal cannot contain multiple revisions for one delivery cell"
        )
    if any(not revision.authority_approved for revision in selected_revisions):
        raise LineageContractError(
            "delivery seal requires projection-owner approval for every revision"
        )

    required_artifact_ids: set[str] = set(candidate.output_artifact_ids)
    for revision in selected_revisions:
        required_artifact_ids.update(revision.evidence_artifact_ids)

    pending_artifact_ids = list(required_artifact_ids)
    visited_artifact_ids: set[str] = set()
    active_artifact_ids: set[str] = set()

    def visit_artifact(artifact_id: str) -> None:
        if artifact_id in active_artifact_ids:
            raise LineageContractError("artifact dependency graph contains a cycle")
        if artifact_id in visited_artifact_ids:
            return
        active_artifact_ids.add(artifact_id)
        artifact = _lookup_identity(
            artifacts,
            artifact_id,
            "artifact",
            identity_attribute="artifact_id",
        )
        for dependency_id in artifact.dependency_artifact_ids:
            required_artifact_ids.add(dependency_id)
            visit_artifact(dependency_id)
        active_artifact_ids.remove(artifact_id)
        visited_artifact_ids.add(artifact_id)

    while pending_artifact_ids:
        visit_artifact(pending_artifact_ids.pop())
    selected_artifacts = tuple(
        _lookup_identity(
            artifacts,
            artifact_id,
            "artifact",
            identity_attribute="artifact_id",
        )
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
        required_artifact_ids
    )
    if unexpected_acknowledgements:
        raise LineageContractError(
            "delivery seal acknowledges artifacts outside its lineage closure: "
            + ", ".join(sorted(unexpected_acknowledgements))
        )
    if (
        candidate.supersedes_seal_id
        and candidate.supersedes_seal_id not in known_seal_ids
    ):
        raise LineageContractError(
            "delivery seal supersedes an unknown prior seal"
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


def _lookup_identity(
    values: Mapping[str, _T],
    key: str,
    label: str,
    *,
    identity_attribute: str,
) -> _T:
    value = _lookup(values, key, label)
    if getattr(value, identity_attribute, None) != key:
        raise LineageContractError(
            f"{label} mapping key {key!r} does not match object identity"
        )
    return value


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
