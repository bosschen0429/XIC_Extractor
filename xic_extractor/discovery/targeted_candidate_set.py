"""Discovery-owned receipt for one complete Targeted candidate universe."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from xic_extractor.artifact_lineage import EvidenceArtifactClosureError
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.extraction.cid_nl_evidence_projection import (
    TargetedCidNlProjectionRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    validate_owner_backed_peak_hypothesis_instance,
)
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    canonical_current_attempt_artifact_closure,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

TargetedCandidateSetStatus = Literal["complete", "failed"]

SCHEMA_VERSION = "targeted_candidate_set_v1"
VALIDATION_STATUS = "diagnostic_only"
ARTIFACT_SEMANTIC_ROLE = "targeted_candidate_set_receipt"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.discovery.targeted_candidate_set",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)
_PROCESSING_REQUEST_PREFIX = "xic.processing-request.v1:"
_PROCESSING_ATTEMPT_PREFIX = "xic.processing-attempt.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_TARGET_SNAPSHOT_PREFIX = "xic.target-list-snapshot.v1:"
_TARGET_KEY_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_PREFIX = "xic.target-definition.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TargetedCandidateSetError(ValueError):
    """Raised when Discovery cannot prove one exact Targeted candidate set."""


@dataclass(frozen=True, slots=True)
class TargetedCandidateSetMember:
    """One workflow-neutral hypothesis paired with its exact Targeted projection."""

    hypothesis: PeakHypothesis
    targeted_cid_nl_projection: TargetedCidNlProjectionRef

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis, PeakHypothesis):
            raise TargetedCandidateSetError(
                "candidate hypothesis must use the existing PeakHypothesis model"
            )
        if not isinstance(
            self.targeted_cid_nl_projection,
            TargetedCidNlProjectionRef,
        ):
            raise TargetedCandidateSetError(
                "candidate projection must be an owner-minted Targeted CID-NL ref"
            )


@dataclass(frozen=True, slots=True)
class TargetedCandidateSetRef:
    """Immutable Discovery completion receipt with full in-memory lineage closure."""

    processing_attempt: ProcessingAttemptIdentity
    processing_request_id: str
    processing_attempt_id: str
    canonical_sample_identity_id: str
    target_snapshot_id: str
    target_key_id: str
    target_definition_id: str
    status: TargetedCandidateSetStatus
    failure_reason: str
    candidates: tuple[TargetedCandidateSetMember, ...]
    lineage_artifacts: tuple[EvidenceArtifactRef, ...]
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise TargetedCandidateSetError(
                "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        _validate_typed_id(
            self.processing_request_id,
            prefix=_PROCESSING_REQUEST_PREFIX,
            field="processing_request_id",
        )
        _validate_typed_id(
            self.processing_attempt_id,
            prefix=_PROCESSING_ATTEMPT_PREFIX,
            field="processing_attempt_id",
        )
        if (
            self.processing_request_id
            != self.processing_attempt.processing_request_id
            or self.processing_attempt_id
            != self.processing_attempt.processing_attempt_id
        ):
            raise TargetedCandidateSetError(
                "candidate-set processing IDs must bind the owner-issued attempt"
            )
        _validate_typed_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _validate_typed_id(
            self.target_snapshot_id,
            prefix=_TARGET_SNAPSHOT_PREFIX,
            field="target_snapshot_id",
        )
        _validate_typed_id(
            self.target_key_id,
            prefix=_TARGET_KEY_PREFIX,
            field="target_key_id",
        )
        _validate_typed_id(
            self.target_definition_id,
            prefix=_TARGET_DEFINITION_PREFIX,
            field="target_definition_id",
        )
        candidates = _canonical_member_tuple(self.candidates)
        if candidates != self.candidates:
            raise TargetedCandidateSetError(
                "candidate members must be unique and canonical-sorted"
            )
        _validate_terminal_state(
            status=self.status,
            failure_reason=self.failure_reason,
            candidates=candidates,
        )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise TargetedCandidateSetError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise TargetedCandidateSetError(
                "artifact semantic contract is not owned by Discovery candidate set"
            )
        if (
            self.artifact.producer_processing_request_id
            != self.processing_request_id
            or self.artifact.producer_processing_attempt_id
            != self.processing_attempt_id
        ):
            raise TargetedCandidateSetError(
                "candidate-set artifact references a different processing attempt"
        )
        try:
            closure = canonical_current_attempt_artifact_closure(
                producer_attempt=self.processing_attempt,
                artifacts=self.lineage_artifacts,
            )
        except EvidenceArtifactClosureError as exc:
            raise TargetedCandidateSetError(str(exc)) from exc
        if closure != self.lineage_artifacts:
            raise TargetedCandidateSetError(
                "lineage_artifacts must be unique and canonical-sorted"
            )
        by_id = {item.evidence_artifact_id: item for item in closure}
        if by_id.get(self.artifact.evidence_artifact_id) != self.artifact:
            raise TargetedCandidateSetError(
                "lineage_artifacts must contain the candidate-set artifact"
            )
        upstream = tuple(
            item
            for item in closure
            if item.evidence_artifact_id != self.artifact.evidence_artifact_id
        )
        if self.artifact.upstream_artifact_ids != tuple(
            item.evidence_artifact_id for item in upstream
        ):
            raise TargetedCandidateSetError(
                "candidate-set artifact must bind every upstream lineage artifact"
            )
        _validate_exact_candidate_lineage(candidates, upstream)
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_targeted_candidate_set_bytes(self),
            )
        except ValueError as exc:
            raise TargetedCandidateSetError(
                "candidate-set content mismatches its immutable artifact"
            ) from exc

    @property
    def candidate_hypotheses(self) -> tuple[PeakHypothesis, ...]:
        return tuple(member.hypothesis for member in self.candidates)

    @property
    def targeted_cid_nl_projections(self) -> tuple[TargetedCidNlProjectionRef, ...]:
        return tuple(member.targeted_cid_nl_projection for member in self.candidates)

    @property
    def candidate_hypothesis_ids(self) -> tuple[str, ...]:
        return tuple(item.hypothesis.hypothesis_id for item in self.candidates)

    @property
    def candidate_hypothesis_content_ids(self) -> tuple[str, ...]:
        return tuple(
            _hypothesis_artifact(item.hypothesis).content_identity.artifact_content_id
            for item in self.candidates
        )

    @property
    def targeted_projection_artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            item.targeted_cid_nl_projection.artifact.evidence_artifact_id
            for item in self.candidates
        )


def mint_targeted_candidate_set_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    candidates: tuple[TargetedCandidateSetMember, ...],
    lineage_artifacts: tuple[EvidenceArtifactRef, ...],
    failure_reason: str = "",
) -> TargetedCandidateSetRef:
    """Mint the sole Discovery-owned completeness receipt for one sample-target."""

    _validate_owner_context(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
    )
    members = _canonical_member_tuple(candidates)
    normalized_failure = _optional_reason(failure_reason)
    status: TargetedCandidateSetStatus = "failed" if normalized_failure else "complete"
    _validate_terminal_state(
        status=status,
        failure_reason=normalized_failure,
        candidates=members,
    )
    for member in members:
        _validate_member_binding(
            member,
            processing_owners=processing_owners,
            sample=sample,
            target=target,
        )
    try:
        upstream = canonical_current_attempt_artifact_closure(
            producer_attempt=processing_owners.attempt,
            artifacts=lineage_artifacts,
        )
    except EvidenceArtifactClosureError as exc:
        raise TargetedCandidateSetError(str(exc)) from exc
    _validate_exact_candidate_lineage(members, upstream)

    payload = _targeted_candidate_set_bytes(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        target_snapshot_id=target.snapshot.snapshot_id,
        target_key_id=target.definition.target_key.target_key_id,
        target_definition_id=target.definition.target_definition_id,
        status=status,
        failure_reason=normalized_failure,
        candidates=members,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload,
        upstream_artifacts=upstream,
    )
    try:
        full_closure = canonical_current_attempt_artifact_closure(
            producer_attempt=processing_owners.attempt,
            artifacts=(*upstream, artifact),
        )
    except EvidenceArtifactClosureError as exc:  # pragma: no cover - owner invariant
        raise TargetedCandidateSetError(str(exc)) from exc
    return TargetedCandidateSetRef(
        processing_attempt=processing_owners.attempt,
        processing_request_id=processing_owners.request.processing_request_id,
        processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        target_snapshot_id=target.snapshot.snapshot_id,
        target_key_id=target.definition.target_key.target_key_id,
        target_definition_id=target.definition.target_definition_id,
        status=status,
        failure_reason=normalized_failure,
        candidates=members,
        lineage_artifacts=full_closure,
        artifact=artifact,
    )


def canonical_targeted_candidate_set_bytes(reference: TargetedCandidateSetRef) -> bytes:
    """Return the exact producer payload for a candidate-set receipt."""

    if not isinstance(reference, TargetedCandidateSetRef):
        raise TargetedCandidateSetError(
            "canonical serialization requires TargetedCandidateSetRef"
        )
    return _targeted_candidate_set_bytes(
        processing_request_id=reference.processing_request_id,
        processing_attempt_id=reference.processing_attempt_id,
        canonical_sample_identity_id=reference.canonical_sample_identity_id,
        target_snapshot_id=reference.target_snapshot_id,
        target_key_id=reference.target_key_id,
        target_definition_id=reference.target_definition_id,
        status=reference.status,
        failure_reason=reference.failure_reason,
        candidates=reference.candidates,
    )


def _validate_owner_context(
    *,
    processing_owners: object,
    sample: object,
    target: object,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise TargetedCandidateSetError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise TargetedCandidateSetError(
            "sample must be an owner-resolved canonical identity"
        )
    if sample not in processing_owners.sample_resolutions:
        raise TargetedCandidateSetError(
            "sample is outside the processing request closure"
        )
    if not isinstance(target, ResolvedTargetDefinition):
        raise TargetedCandidateSetError(
            "target must be an owner-resolved target definition"
        )
    if processing_owners.target_snapshot is None:
        raise TargetedCandidateSetError(
            "candidate set requires the request-bound target snapshot"
        )
    if target.snapshot.snapshot_id != processing_owners.target_snapshot.snapshot_id:
        raise TargetedCandidateSetError(
            "target comes from a different target-list snapshot"
        )


def _validate_member_binding(
    member: TargetedCandidateSetMember,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> None:
    try:
        validate_owner_backed_peak_hypothesis_instance(member.hypothesis)
    except ValueError as exc:
        raise TargetedCandidateSetError(str(exc)) from exc
    hypothesis_artifact = _hypothesis_artifact(member.hypothesis)
    _validate_attempt(
        hypothesis_artifact,
        processing_owners=processing_owners,
        subject="candidate hypothesis",
    )
    observation = member.hypothesis.peak_observation_refs[0]
    if (
        observation.content.canonical_sample_identity_id
        != sample.identity.sample_identity_id
    ):
        raise TargetedCandidateSetError(
            "candidate hypothesis references a different canonical sample"
        )
    projection = member.targeted_cid_nl_projection
    _validate_attempt(
        projection.artifact,
        processing_owners=processing_owners,
        subject="candidate projection",
    )
    if projection.canonical_sample_identity_id != sample.identity.sample_identity_id:
        raise TargetedCandidateSetError(
            "candidate projection references a different canonical sample"
        )
    if (
        projection.target_key_id != target.definition.target_key.target_key_id
        or projection.target_definition_id != target.definition.target_definition_id
    ):
        raise TargetedCandidateSetError(
            "candidate projection references a different target definition"
        )


def _validate_attempt(
    artifact: EvidenceArtifactRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    subject: str,
) -> None:
    if (
        artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise TargetedCandidateSetError(
            f"{subject} references a different processing request"
        )
    if (
        artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise TargetedCandidateSetError(
            f"{subject} references a different processing attempt"
        )


def _canonical_member_tuple(
    values: object,
) -> tuple[TargetedCandidateSetMember, ...]:
    if not isinstance(values, tuple):
        raise TargetedCandidateSetError("candidates must be an immutable tuple")
    if any(not isinstance(item, TargetedCandidateSetMember) for item in values):
        raise TargetedCandidateSetError(
            "candidates must contain TargetedCandidateSetMember values"
        )
    ordered = tuple(sorted(values, key=lambda item: item.hypothesis.hypothesis_id))
    hypothesis_ids = tuple(item.hypothesis.hypothesis_id for item in ordered)
    projection_ids = tuple(
        item.targeted_cid_nl_projection.artifact.evidence_artifact_id
        for item in ordered
    )
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise TargetedCandidateSetError(
            "candidate set contains duplicate hypothesis identities"
        )
    if len(projection_ids) != len(set(projection_ids)):
        raise TargetedCandidateSetError(
            "candidate set contains duplicate Targeted projection identities"
        )
    return ordered


def _validate_terminal_state(
    *,
    status: object,
    failure_reason: object,
    candidates: tuple[TargetedCandidateSetMember, ...],
) -> None:
    if status not in {"complete", "failed"}:
        raise TargetedCandidateSetError("candidate-set status is unsupported")
    reason = _optional_reason(failure_reason)
    if status == "complete" and reason:
        raise TargetedCandidateSetError(
            "complete candidate set cannot carry a failure_reason"
        )
    if status == "failed" and (not reason or candidates):
        raise TargetedCandidateSetError(
            "failed candidate set requires zero candidates and one failure_reason"
        )


def _validate_exact_candidate_lineage(
    candidates: tuple[TargetedCandidateSetMember, ...],
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> None:
    by_id = {artifact.evidence_artifact_id: artifact for artifact in artifacts}
    roots = tuple(
        root
        for candidate in candidates
        for root in (
            _hypothesis_artifact(candidate.hypothesis),
            candidate.targeted_cid_nl_projection.artifact,
        )
    )
    root_ids = {root.evidence_artifact_id for root in roots}
    missing_roots = sorted(root_ids - set(by_id))
    if missing_roots:
        raise TargetedCandidateSetError(
            "candidate lineage is missing hypothesis or projection roots"
        )

    reachable: set[str] = set()

    def visit(artifact_id: str) -> None:
        if artifact_id in reachable:
            return
        artifact = by_id.get(artifact_id)
        if artifact is None:
            raise TargetedCandidateSetError(
                "candidate lineage has a missing upstream dependency"
            )
        reachable.add(artifact_id)
        for upstream_id in artifact.upstream_artifact_ids:
            visit(upstream_id)

    for root_id in root_ids:
        visit(root_id)
    if reachable != set(by_id):
        raise TargetedCandidateSetError(
            "candidate lineage contains artifacts outside the exact candidate closure"
        )

    for candidate in candidates:
        hypothesis_ancestors: set[str] = set()

        def visit_hypothesis(artifact_id: str) -> None:
            if artifact_id in hypothesis_ancestors:
                return
            hypothesis_ancestors.add(artifact_id)
            for upstream_id in by_id[artifact_id].upstream_artifact_ids:
                visit_hypothesis(upstream_id)

        visit_hypothesis(_hypothesis_artifact(candidate.hypothesis).evidence_artifact_id)
        canonical_evidence_id = (
            candidate.targeted_cid_nl_projection.canonical_evidence_artifact_id
        )
        if canonical_evidence_id not in hypothesis_ancestors:
            raise TargetedCandidateSetError(
                "candidate projection does not belong to its paired hypothesis"
            )


def _hypothesis_artifact(hypothesis: PeakHypothesis) -> EvidenceArtifactRef:
    if not isinstance(hypothesis.artifact, EvidenceArtifactRef):
        raise TargetedCandidateSetError(
            "candidate hypothesis must carry its immutable artifact"
        )
    return hypothesis.artifact


def _targeted_candidate_set_bytes(
    *,
    processing_request_id: str,
    processing_attempt_id: str,
    canonical_sample_identity_id: str,
    target_snapshot_id: str,
    target_key_id: str,
    target_definition_id: str,
    status: TargetedCandidateSetStatus,
    failure_reason: str,
    candidates: tuple[TargetedCandidateSetMember, ...],
) -> bytes:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "processing_request_id": processing_request_id,
        "processing_attempt_id": processing_attempt_id,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "target_snapshot_id": target_snapshot_id,
        "target_key_id": target_key_id,
        "target_definition_id": target_definition_id,
        "status": status,
        "failure_reason": failure_reason,
        "candidates": [
            {
                "hypothesis_id": member.hypothesis.hypothesis_id,
                "hypothesis_artifact_content_id": (
                    _hypothesis_artifact(member.hypothesis)
                    .content_identity.artifact_content_id
                ),
                "targeted_cid_nl_projection_artifact_id": (
                    member.targeted_cid_nl_projection.artifact.evidence_artifact_id
                ),
                "targeted_cid_nl_projection_artifact_content_id": (
                    member.targeted_cid_nl_projection.artifact.content_identity.artifact_content_id
                ),
            }
            for member in candidates
        ],
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _validate_typed_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise TargetedCandidateSetError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise TargetedCandidateSetError(f"{field} must contain a SHA-256 digest")
    return value


def _optional_reason(value: object) -> str:
    if value == "":
        return ""
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedCandidateSetError(
            "failure_reason must be non-empty normalized text"
        )
    return value


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "SCHEMA_VERSION",
    "TargetedCandidateSetError",
    "TargetedCandidateSetMember",
    "TargetedCandidateSetRef",
    "TargetedCandidateSetStatus",
    "VALIDATION_STATUS",
    "canonical_targeted_candidate_set_bytes",
    "mint_targeted_candidate_set_ref",
]
