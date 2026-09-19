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

SCHEMA_VERSION = "targeted_candidate_set"
COMPLETION_SCHEMA_VERSION = "targeted_candidate_discovery_completion"
VALIDATION_STATUS = "diagnostic_only"
ARTIFACT_SEMANTIC_ROLE = "targeted_candidate_set_receipt"
COMPLETION_ARTIFACT_SEMANTIC_ROLE = "targeted_candidate_discovery_completion"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.discovery.targeted_candidate_set",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)
_COMPLETION_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.discovery.targeted_candidate_set",
    semantic_role=COMPLETION_ARTIFACT_SEMANTIC_ROLE,
    schema_version=COMPLETION_SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)
_PROCESSING_REQUEST_PREFIX = "xic.processing-request:"
_PROCESSING_ATTEMPT_PREFIX = "xic.processing-attempt:"
_SAMPLE_ID_PREFIX = "xic.sample:"
_TARGET_SNAPSHOT_PREFIX = "xic.target-list-snapshot:"
_TARGET_KEY_PREFIX = "xic.target-key:"
_TARGET_DEFINITION_PREFIX = "xic.target-definition:"
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
class TargetedCandidateDiscoveryCompletion:
    """Immutable result of exhausting one explicitly bounded Discovery input."""

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
        _validate_processing_attempt_binding(
            processing_attempt=self.processing_attempt,
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
        )
        _validate_bound_identity_ids(
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            target_snapshot_id=self.target_snapshot_id,
            target_key_id=self.target_key_id,
            target_definition_id=self.target_definition_id,
        )
        candidates = _canonical_member_tuple(self.candidates)
        if candidates != self.candidates:
            raise TargetedCandidateSetError(
                "bounded completion candidates must be unique and canonical-sorted"
            )
        _validate_terminal_state(
            status=self.status,
            failure_reason=self.failure_reason,
            candidates=candidates,
        )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise TargetedCandidateSetError(
                "completion artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _COMPLETION_ARTIFACT_SEMANTIC_CONTRACT:
            raise TargetedCandidateSetError(
                "completion artifact semantic contract is not owned by Discovery"
            )
        if (
            self.artifact.producer_processing_request_id
            != self.processing_request_id
            or self.artifact.producer_processing_attempt_id
            != self.processing_attempt_id
        ):
            raise TargetedCandidateSetError(
                "completion artifact references a different processing attempt"
            )
        closure = _canonical_lineage(
            processing_attempt=self.processing_attempt,
            artifacts=self.lineage_artifacts,
        )
        if closure != self.lineage_artifacts:
            raise TargetedCandidateSetError(
                "completion lineage_artifacts must be unique and canonical-sorted"
            )
        by_id = {item.evidence_artifact_id: item for item in closure}
        if by_id.get(self.artifact.evidence_artifact_id) != self.artifact:
            raise TargetedCandidateSetError(
                "completion lineage must contain the completion artifact"
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
                "completion artifact must bind every candidate lineage artifact"
            )
        _validate_exact_candidate_lineage(candidates, upstream)
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                _targeted_candidate_completion_bytes(
                    processing_request_id=self.processing_request_id,
                    processing_attempt_id=self.processing_attempt_id,
                    canonical_sample_identity_id=self.canonical_sample_identity_id,
                    target_snapshot_id=self.target_snapshot_id,
                    target_key_id=self.target_key_id,
                    target_definition_id=self.target_definition_id,
                    status=self.status,
                    failure_reason=self.failure_reason,
                    candidates=candidates,
                ),
            )
        except ValueError as exc:
            raise TargetedCandidateSetError(
                "bounded Discovery completion content mismatches its immutable artifact"
            ) from exc


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
        _validate_processing_attempt_binding(
            processing_attempt=self.processing_attempt,
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
        )
        _validate_bound_identity_ids(
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            target_snapshot_id=self.target_snapshot_id,
            target_key_id=self.target_key_id,
            target_definition_id=self.target_definition_id,
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
        _validate_completion_backed_candidate_lineage(
            processing_request_id=self.processing_request_id,
            processing_attempt_id=self.processing_attempt_id,
            canonical_sample_identity_id=self.canonical_sample_identity_id,
            target_snapshot_id=self.target_snapshot_id,
            target_key_id=self.target_key_id,
            target_definition_id=self.target_definition_id,
            status=self.status,
            failure_reason=self.failure_reason,
            candidates=candidates,
            artifacts=upstream,
        )
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

    def validate_for_diagnostic_selection(self) -> None:
        """Re-run Discovery-owned receipt integrity at the inner selection boundary."""

        self.__post_init__()


def complete_targeted_candidate_discovery(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    hypotheses: tuple[PeakHypothesis, ...],
    targeted_cid_nl_projections: tuple[TargetedCidNlProjectionRef, ...],
    lineage_artifacts: tuple[EvidenceArtifactRef, ...],
    failure_reason: str = "",
) -> TargetedCandidateDiscoveryCompletion:
    """Exhaust and seal all candidates in one explicit no-RAW bounded input."""

    _validate_owner_context(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
    )
    bounded_hypotheses = _canonical_hypothesis_tuple(hypotheses)
    bounded_projections = _canonical_projection_tuple(targeted_cid_nl_projections)
    normalized_failure = _optional_reason(failure_reason)
    status: TargetedCandidateSetStatus = "failed" if normalized_failure else "complete"
    if normalized_failure and (bounded_hypotheses or bounded_projections):
        raise TargetedCandidateSetError(
            "failed bounded Discovery completion cannot carry candidate inputs"
        )
    upstream = _canonical_lineage(
        processing_attempt=processing_owners.attempt,
        artifacts=lineage_artifacts,
    )
    members = (
        ()
        if normalized_failure
        else _pair_bounded_candidate_inputs(
            hypotheses=bounded_hypotheses,
            projections=bounded_projections,
            artifacts=upstream,
        )
    )
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
    _validate_exact_candidate_lineage(members, upstream)

    payload = _targeted_candidate_completion_bytes(
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
        semantic_contract=_COMPLETION_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload,
        upstream_artifacts=upstream,
    )
    full_closure = _canonical_lineage(
        processing_attempt=processing_owners.attempt,
        artifacts=(*upstream, artifact),
    )
    return TargetedCandidateDiscoveryCompletion(
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


def mint_targeted_candidate_set_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
    completion: TargetedCandidateDiscoveryCompletion,
) -> TargetedCandidateSetRef:
    """Mint a receipt from one intact Discovery-owned bounded completion."""

    _validate_owner_context(
        processing_owners=processing_owners,
        sample=sample,
        target=target,
    )
    if not isinstance(completion, TargetedCandidateDiscoveryCompletion):
        raise TargetedCandidateSetError(
            "completion must be a Discovery-owned bounded completion result"
        )
    completion.__post_init__()
    if (
        completion.processing_attempt != processing_owners.attempt
        or completion.processing_request_id
        != processing_owners.request.processing_request_id
        or completion.processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise TargetedCandidateSetError(
            "bounded completion references a different processing attempt"
        )
    if completion.canonical_sample_identity_id != sample.identity.sample_identity_id:
        raise TargetedCandidateSetError(
            "bounded completion references a different canonical sample"
        )
    if (
        completion.target_snapshot_id != target.snapshot.snapshot_id
        or completion.target_key_id != target.definition.target_key.target_key_id
        or completion.target_definition_id
        != target.definition.target_definition_id
    ):
        raise TargetedCandidateSetError(
            "bounded completion references a different target definition"
        )

    payload = _targeted_candidate_set_bytes(
        processing_request_id=completion.processing_request_id,
        processing_attempt_id=completion.processing_attempt_id,
        canonical_sample_identity_id=completion.canonical_sample_identity_id,
        target_snapshot_id=completion.target_snapshot_id,
        target_key_id=completion.target_key_id,
        target_definition_id=completion.target_definition_id,
        status=completion.status,
        failure_reason=completion.failure_reason,
        candidates=completion.candidates,
    )
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=completion.processing_request_id,
        producer_processing_attempt_id=completion.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload,
        upstream_artifacts=completion.lineage_artifacts,
    )
    full_closure = _canonical_lineage(
        processing_attempt=completion.processing_attempt,
        artifacts=(*completion.lineage_artifacts, artifact),
    )
    return TargetedCandidateSetRef(
        processing_attempt=completion.processing_attempt,
        processing_request_id=completion.processing_request_id,
        processing_attempt_id=completion.processing_attempt_id,
        canonical_sample_identity_id=completion.canonical_sample_identity_id,
        target_snapshot_id=completion.target_snapshot_id,
        target_key_id=completion.target_key_id,
        target_definition_id=completion.target_definition_id,
        status=completion.status,
        failure_reason=completion.failure_reason,
        candidates=completion.candidates,
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


def _canonical_lineage(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    try:
        return canonical_current_attempt_artifact_closure(
            producer_attempt=processing_attempt,
            artifacts=artifacts,
        )
    except EvidenceArtifactClosureError as exc:
        raise TargetedCandidateSetError(str(exc)) from exc


def _validate_processing_attempt_binding(
    *,
    processing_attempt: object,
    processing_request_id: str,
    processing_attempt_id: str,
) -> None:
    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TargetedCandidateSetError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    if (
        processing_request_id != processing_attempt.processing_request_id
        or processing_attempt_id != processing_attempt.processing_attempt_id
    ):
        raise TargetedCandidateSetError(
            "candidate-set processing IDs must bind the owner-issued attempt"
        )


def _canonical_hypothesis_tuple(values: object) -> tuple[PeakHypothesis, ...]:
    if not isinstance(values, tuple):
        raise TargetedCandidateSetError("hypotheses must be an immutable tuple")
    if any(not isinstance(item, PeakHypothesis) for item in values):
        raise TargetedCandidateSetError(
            "hypotheses must contain existing PeakHypothesis values"
        )
    ordered = tuple(sorted(values, key=lambda item: item.hypothesis_id))
    hypothesis_ids = tuple(item.hypothesis_id for item in ordered)
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise TargetedCandidateSetError(
            "bounded Discovery input contains duplicate hypothesis identities"
        )
    return ordered


def _canonical_projection_tuple(
    values: object,
) -> tuple[TargetedCidNlProjectionRef, ...]:
    if not isinstance(values, tuple):
        raise TargetedCandidateSetError(
            "targeted_cid_nl_projections must be an immutable tuple"
        )
    if any(not isinstance(item, TargetedCidNlProjectionRef) for item in values):
        raise TargetedCandidateSetError(
            "targeted_cid_nl_projections must contain owner-minted refs"
        )
    ordered = tuple(
        sorted(values, key=lambda item: item.artifact.evidence_artifact_id)
    )
    projection_ids = tuple(
        item.artifact.evidence_artifact_id for item in ordered
    )
    if len(projection_ids) != len(set(projection_ids)):
        raise TargetedCandidateSetError(
            "bounded Discovery input contains duplicate projection identities"
        )
    return ordered


def _pair_bounded_candidate_inputs(
    *,
    hypotheses: tuple[PeakHypothesis, ...],
    projections: tuple[TargetedCidNlProjectionRef, ...],
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[TargetedCandidateSetMember, ...]:
    if len(hypotheses) != len(projections):
        raise TargetedCandidateSetError(
            "bounded Discovery completion requires one projection per hypothesis"
        )
    by_id = {artifact.evidence_artifact_id: artifact for artifact in artifacts}
    projection_matches: dict[str, list[PeakHypothesis]] = {
        projection.artifact.evidence_artifact_id: [] for projection in projections
    }
    for hypothesis in hypotheses:
        hypothesis_artifact = _hypothesis_artifact(hypothesis)
        ancestors = _artifact_ancestor_ids(
            hypothesis_artifact.evidence_artifact_id,
            by_id=by_id,
        )
        for projection in projections:
            if projection.canonical_evidence_artifact_id in ancestors:
                projection_matches[projection.artifact.evidence_artifact_id].append(
                    hypothesis
                )

    members: list[TargetedCandidateSetMember] = []
    for projection in projections:
        matches = projection_matches[projection.artifact.evidence_artifact_id]
        if len(matches) != 1:
            raise TargetedCandidateSetError(
                "each bounded projection must belong to exactly one discovered "
                "hypothesis"
            )
        members.append(
            TargetedCandidateSetMember(
                hypothesis=matches[0],
                targeted_cid_nl_projection=projection,
            )
        )
    return _canonical_member_tuple(tuple(members))


def _artifact_ancestor_ids(
    artifact_id: str,
    *,
    by_id: dict[str, EvidenceArtifactRef],
) -> set[str]:
    ancestors: set[str] = set()

    def visit(current_id: str) -> None:
        if current_id in ancestors:
            return
        artifact = by_id.get(current_id)
        if artifact is None:
            raise TargetedCandidateSetError(
                "candidate lineage has a missing upstream dependency"
            )
        ancestors.add(current_id)
        for upstream_id in artifact.upstream_artifact_ids:
            visit(upstream_id)

    visit(artifact_id)
    return ancestors


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


def _validate_completion_backed_candidate_lineage(
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
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> None:
    completion_artifacts = tuple(
        artifact
        for artifact in artifacts
        if artifact.semantic_contract == _COMPLETION_ARTIFACT_SEMANTIC_CONTRACT
    )
    if len(completion_artifacts) != 1:
        raise TargetedCandidateSetError(
            "candidate-set receipt requires exactly one bounded Discovery "
            "completion artifact"
        )
    completion_artifact = completion_artifacts[0]
    candidate_lineage = tuple(
        artifact
        for artifact in artifacts
        if artifact.evidence_artifact_id
        != completion_artifact.evidence_artifact_id
    )
    if completion_artifact.upstream_artifact_ids != tuple(
        artifact.evidence_artifact_id for artifact in candidate_lineage
    ):
        raise TargetedCandidateSetError(
            "bounded Discovery completion must close the exact candidate lineage"
        )
    _validate_exact_candidate_lineage(candidates, candidate_lineage)
    try:
        verify_evidence_artifact_payload(
            completion_artifact,
            _targeted_candidate_completion_bytes(
                processing_request_id=processing_request_id,
                processing_attempt_id=processing_attempt_id,
                canonical_sample_identity_id=canonical_sample_identity_id,
                target_snapshot_id=target_snapshot_id,
                target_key_id=target_key_id,
                target_definition_id=target_definition_id,
                status=status,
                failure_reason=failure_reason,
                candidates=candidates,
            ),
        )
    except ValueError as exc:
        raise TargetedCandidateSetError(
            "candidate-set receipt mismatches its bounded completion content"
        ) from exc


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
    return _targeted_candidate_payload_bytes(
        schema_version=SCHEMA_VERSION,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        canonical_sample_identity_id=canonical_sample_identity_id,
        target_snapshot_id=target_snapshot_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
        status=status,
        failure_reason=failure_reason,
        candidates=candidates,
    )


def _targeted_candidate_completion_bytes(
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
    return _targeted_candidate_payload_bytes(
        schema_version=COMPLETION_SCHEMA_VERSION,
        processing_request_id=processing_request_id,
        processing_attempt_id=processing_attempt_id,
        canonical_sample_identity_id=canonical_sample_identity_id,
        target_snapshot_id=target_snapshot_id,
        target_key_id=target_key_id,
        target_definition_id=target_definition_id,
        status=status,
        failure_reason=failure_reason,
        candidates=candidates,
    )


def _targeted_candidate_payload_bytes(
    *,
    schema_version: str,
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
        "schema_version": schema_version,
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


def _validate_bound_identity_ids(
    *,
    processing_request_id: object,
    processing_attempt_id: object,
    canonical_sample_identity_id: object,
    target_snapshot_id: object,
    target_key_id: object,
    target_definition_id: object,
) -> None:
    _validate_typed_id(
        processing_request_id,
        prefix=_PROCESSING_REQUEST_PREFIX,
        field="processing_request_id",
    )
    _validate_typed_id(
        processing_attempt_id,
        prefix=_PROCESSING_ATTEMPT_PREFIX,
        field="processing_attempt_id",
    )
    _validate_typed_id(
        canonical_sample_identity_id,
        prefix=_SAMPLE_ID_PREFIX,
        field="canonical_sample_identity_id",
    )
    _validate_typed_id(
        target_snapshot_id,
        prefix=_TARGET_SNAPSHOT_PREFIX,
        field="target_snapshot_id",
    )
    _validate_typed_id(
        target_key_id,
        prefix=_TARGET_KEY_PREFIX,
        field="target_key_id",
    )
    _validate_typed_id(
        target_definition_id,
        prefix=_TARGET_DEFINITION_PREFIX,
        field="target_definition_id",
    )


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
    "COMPLETION_ARTIFACT_SEMANTIC_ROLE",
    "COMPLETION_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "TargetedCandidateDiscoveryCompletion",
    "TargetedCandidateSetError",
    "TargetedCandidateSetMember",
    "TargetedCandidateSetRef",
    "TargetedCandidateSetStatus",
    "VALIDATION_STATUS",
    "canonical_targeted_candidate_set_bytes",
    "complete_targeted_candidate_discovery",
    "mint_targeted_candidate_set_ref",
]
