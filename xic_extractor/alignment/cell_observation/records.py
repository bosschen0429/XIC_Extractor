"""Cell observation predecessor records and canonical artifact encoding."""

from __future__ import annotations

import json
from dataclasses import InitVar, asdict, dataclass, field, fields
from typing import Literal

from xic_extractor.alignment.cell_observation.group_context import (
    _ARTIFACT_SEMANTIC_CONTRACT,
    SCHEMA_VERSION,
    VALIDATION_STATUS,
    CellObservationContractError,
    CidNlCellGroupContext,
    TerminalObservationVariant,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_evidence_artifact_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    CidNlMethodResult,
    CidNlTechnicalState,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    retry_predecessor_attempt_ids,
)
from xic_extractor.review_records import ResolvedReviewRecordRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_CELL_OBSERVATION_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class OutsideGroupCellRelation:
    """Reference support in one exact sibling without a group-local observation."""

    supporting_group_hypothesis_id: str
    supporting_peak_hypothesis_id: str
    relation: Literal["outside_group"] = "outside_group"


@dataclass(frozen=True, slots=True)
class CellObservationContent:
    """Orthogonal axes only; no value, writer, fill, or product authority."""

    canonical_sample_identity_id: str
    group_hypothesis_id: str
    group_owner_ids: tuple[str, ...]
    group_method_subject_id: str
    group_target_key_id: str | None
    group_target_definition_id: str | None
    group_support_assessment_artifact_content_ids: tuple[str, ...]
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    peak_observation_content_id: str | None
    peak_hypothesis_id: str | None
    method_assessment_artifact_content_id: str
    evidence_artifact_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    human_disposition_ref_id: str | None
    outside_group: OutsideGroupCellRelation | None = None
    unassigned_support: bool = False


@dataclass(frozen=True, slots=True)
class CellObservationRef:
    """One exact cell closure under an already-existing provisional group."""

    content: CellObservationContent
    group_context: CidNlCellGroupContext
    method_assessment: CidNlMethodAssessmentRef
    peak_observation: PeakObservationRef | None
    peak_hypothesis: PeakHypothesis | None
    human_disposition: ResolvedReviewRecordRef | None
    artifact: EvidenceArtifactRef
    outside_group_context: CidNlCellGroupContext | None = None
    _mint_token: InitVar[object] = field(default=None, kw_only=True)


def _direct_member_integration(
    *,
    member: CrossSampleGroupMemberSubject | None,
    processing_request_id: str,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    terminal_variant: TerminalObservationVariant,
    terminal_hypothesis: PeakHypothesis | None,
    method_assessment: CidNlMethodAssessmentRef | None,
    peak_observation: PeakObservationRef | None,
    peak_hypothesis: PeakHypothesis | None,
) -> IntegrationResult | None:
    if member is None:
        return None
    canonical_sample_identity_id = canonical_sample.identity.sample_identity_id
    direct_hypothesis = peak_hypothesis
    if direct_hypothesis is None and (
        terminal_variant == "observed"
        and terminal_hypothesis is not None
        and member.subject_kind == "peak_hypothesis"
        and member.subject_id == terminal_hypothesis.hypothesis_id
    ):
        direct_hypothesis = terminal_hypothesis
    if direct_hypothesis is not None:
        if type(direct_hypothesis) is not PeakHypothesis or not (
            direct_hypothesis.is_owner_backed
        ):
            raise CellObservationContractError(
                "direct member hypothesis is not owner-backed"
            )
        if (
            member.subject_kind != "peak_hypothesis"
            or member.subject_id != direct_hypothesis.hypothesis_id
            or direct_hypothesis.artifact is None
            or direct_hypothesis.artifact.producer_processing_request_id
            != processing_request_id
            or any(
                observation.content.canonical_sample_identity_id
                != canonical_sample_identity_id
                for observation in direct_hypothesis.peak_observation_refs
            )
        ):
            raise CellObservationContractError(
                "direct hypothesis does not mint the exact final member"
            )
        if method_assessment is not None:
            if method_assessment.content.method_result != "SUPPORT":
                raise CellObservationContractError(
                    "direct member assessment must be SUPPORT"
                )
            supported_observation_ids = {
                relation.content.peak_observation_content_id
                for relation in method_assessment.individual_support_refs
            }
            if not set(member.physical_observation_ids).issubset(
                supported_observation_ids
            ):
                raise CellObservationContractError(
                    "direct member hypothesis is outside the exact SUPPORT subset"
                )
        return direct_hypothesis.integration
    if peak_observation is not None:
        # Final members bind owner-backed hypotheses; an observation alone
        # cannot mint the exact member.
        raise CellObservationContractError(
            "direct observation does not mint the exact final member"
        )
    return None


def _outside_group_relation(
    *,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessmentRef,
    outside_group_context: CidNlCellGroupContext | None,
) -> OutsideGroupCellRelation | None:
    if outside_group_context is None:
        return None
    if type(outside_group_context) is not CidNlCellGroupContext:
        raise CellObservationContractError(
            "outside-group context has the wrong owner type"
        )
    if (
        method_assessment.content.method_result != "SUPPORT"
        or outside_group_context.processing_attempt != group_context.processing_attempt
        or outside_group_context.config != group_context.config
        or outside_group_context.group.processing_request_id
        != group_context.group.processing_request_id
        or outside_group_context.group.group_hypothesis_id
        == group_context.group.group_hypothesis_id
    ):
        raise CellObservationContractError(
            "outside-group context crossed its sibling owner boundary"
        )
    supports = tuple(
        support
        for support in outside_group_context.supports
        if support.assessment is method_assessment
        and support.sample.identity.sample_identity_id
        == method_assessment.content.canonical_sample_identity_id
    )
    if len(supports) != 1:
        raise CellObservationContractError(
            "outside-group context lost its exact sample SUPPORT"
        )
    support = supports[0]
    if support.hypothesis.hypothesis_id in group_context.group.owner_ids:
        raise CellObservationContractError(
            "outside-group hypothesis belongs to the local group"
        )
    return OutsideGroupCellRelation(
        supporting_group_hypothesis_id=outside_group_context.group.group_hypothesis_id,
        supporting_peak_hypothesis_id=support.hypothesis.hypothesis_id,
    )


def mint_cell_observation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition | None = None,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessmentRef,
    peak_observation: PeakObservationRef | None = None,
    peak_hypothesis: PeakHypothesis | None = None,
    human_disposition: ResolvedReviewRecordRef | None = None,
    outside_group_context: CidNlCellGroupContext | None = None,
    unassigned_support: bool = False,
) -> CellObservationRef:
    """Attach a completed assessment to an existing group without creating it."""

    allowed_attempts = {
        processing_owners.attempt.processing_attempt_id,
        *retry_predecessor_attempt_ids(processing_owners.attempt),
    }
    if (
        group_context.processing_attempt.processing_request_id
        != processing_owners.request.processing_request_id
        or group_context.processing_attempt.processing_attempt_id
        not in allowed_attempts
    ):
        raise CellObservationContractError(
            "group SUPPORT closure is outside the current attempt or retry lineage"
        )
    group = group_context.group
    method_result = method_assessment.content.method_result
    if method_assessment.content.canonical_sample_identity_id != (
        sample.identity.sample_identity_id
    ):
        raise CellObservationContractError("assessment references a different sample")
    support_binding = any(
        support.assessment.artifact.evidence_artifact_id
        == method_assessment.artifact.evidence_artifact_id
        and support.sample.identity.sample_identity_id
        == sample.identity.sample_identity_id
        for support in group_context.supports
    )
    if method_assessment.content.method_subject != group_context.method_subject and (
        not support_binding
    ):
        raise CellObservationContractError(
            "assessment differs from the group recovery subject"
        )
    if peak_hypothesis is not None and not any(
        support.hypothesis.hypothesis_id == peak_hypothesis.hypothesis_id
        and support.assessment.artifact.evidence_artifact_id
        == method_assessment.artifact.evidence_artifact_id
        for support in group_context.supports
    ):
        raise CellObservationContractError(
            "assessment relations do not bind the group SUPPORT hypothesis"
        )
    if human_disposition is not None:
        reference = human_disposition.reference
        if reference.canonical_sample_identity_id != sample.identity.sample_identity_id:
            raise CellObservationContractError(
                "human disposition references a different sample"
            )
        if (
            target is None
            or reference.processing_request_id
            != processing_owners.request.processing_request_id
            or reference.target_key_id != target.definition.target_key.target_key_id
            or reference.target_definition_id != target.definition.target_definition_id
            or method_assessment.artifact.evidence_artifact_id
            not in reference.evidence_artifact_ids
        ):
            raise CellObservationContractError(
                "human disposition is not bound to the current target evidence"
            )
    outside_group = _outside_group_relation(
        group_context=group_context,
        method_assessment=method_assessment,
        outside_group_context=outside_group_context,
    )
    evidence_artifact_ids = tuple(
        sorted(
            evidence.artifact.evidence_artifact_id
            for evidence in method_assessment.evidence_refs
        )
    )
    content = CellObservationContent(
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        group_hypothesis_id=group.group_hypothesis_id,
        group_owner_ids=group_context.sorted_owner_ids,
        group_method_subject_id=group_context.method_subject_id,
        group_target_key_id=group_context.target_key_id,
        group_target_definition_id=group_context.target_definition_id,
        group_support_assessment_artifact_content_ids=(
            group_context.support_assessment_artifact_content_ids
        ),
        technical_state=method_assessment.content.technical_state,
        method_result=method_result,
        peak_observation_content_id=(
            None
            if peak_observation is None
            else peak_observation.content.peak_observation_content_id
        ),
        peak_hypothesis_id=(
            None if peak_hypothesis is None else peak_hypothesis.hypothesis_id
        ),
        method_assessment_artifact_content_id=(
            method_assessment.artifact.content_identity.artifact_content_id
        ),
        evidence_artifact_ids=evidence_artifact_ids,
        reason_codes=method_assessment.content.reason_codes,
        human_disposition_ref_id=(
            None
            if human_disposition is None
            else human_disposition.reference.review_record_id
        ),
        outside_group=outside_group,
        unassigned_support=unassigned_support,
    )
    upstreams = _cell_upstream_artifacts(
        group_context=group_context,
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        outside_group_context=outside_group_context,
    )
    artifact = mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_cell_observation_bytes(content),
        upstream_artifacts=upstreams,
    )
    return CellObservationRef(
        content=content,
        group_context=group_context,
        method_assessment=method_assessment,
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        human_disposition=human_disposition,
        artifact=artifact,
        outside_group_context=outside_group_context,
        _mint_token=_CELL_OBSERVATION_MINT_TOKEN,
    )


def canonical_cell_observation_bytes(content: CellObservationContent) -> bytes:
    if not isinstance(content, CellObservationContent):
        raise CellObservationContractError(
            "canonical serialization requires CellObservation content"
        )
    # JSON reads immutable tuples directly; copying every group-support ID for
    # every sample adds a second population traversal without changing bytes.
    values = {item.name: getattr(content, item.name) for item in fields(content)}
    if content.outside_group is not None:
        values["outside_group"] = asdict(content.outside_group)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "content": values,
    }
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(
            "CellObservation payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


def _cell_upstream_artifacts(
    *,
    group_context: CidNlCellGroupContext,
    method_assessment: CidNlMethodAssessmentRef,
    peak_observation: PeakObservationRef | None,
    peak_hypothesis: PeakHypothesis | None,
    outside_group_context: CidNlCellGroupContext | None = None,
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts = [method_assessment.artifact]
    if outside_group_context is not None:
        artifacts.extend(outside_group_context.support_artifacts)
    artifacts.extend(group_context.support_artifacts)
    if peak_observation is not None:
        artifacts.append(peak_observation.artifact)
    if peak_hypothesis is not None:
        if peak_hypothesis.artifact is None:
            raise CellObservationContractError(
                "owner-backed hypothesis is missing its immutable artifact"
            )
        artifacts.append(peak_hypothesis.artifact)
    artifacts_by_id = {item.evidence_artifact_id: item for item in artifacts}
    return tuple(
        artifacts_by_id[artifact_id] for artifact_id in sorted(artifacts_by_id)
    )
