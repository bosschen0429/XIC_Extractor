"""CID-NL cell predecessor records and compatible canonical artifact encoding."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Literal

from xic_extractor.alignment.cell_observation.cid_nl_evidence import (
    CidNlCellEvidence,
)
from xic_extractor.alignment.cell_observation.group_context import (
    _ARTIFACT_SEMANTIC_CONTRACT,
    SCHEMA_VERSION,
    VALIDATION_STATUS,
    CellObservationContractError,
    CidNlCellGroupContext,
    TerminalObservationVariant,
)
from xic_extractor.alignment.cell_observation.method_evidence import CellMethodEvidence
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
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


@dataclass(frozen=True, slots=True)
class OutsideGroupCellRelation:
    """Reference support in one exact sibling without a group-local observation."""

    supporting_group_hypothesis_id: str
    supporting_peak_hypothesis_id: str
    relation: Literal["outside_group"] = "outside_group"


@dataclass(frozen=True, slots=True)
class CellObservationContent:
    """Orthogonal axes only; no value, writer, fill, or product authority."""

    sample_key: str
    group_hypothesis_id: str
    group_owner_ids: tuple[str, ...]
    group_method_subject_id: str
    group_target_label: str | None
    group_support_assessment_artifact_content_ids: tuple[str, ...]
    technical_state: CidNlTechnicalState
    method_result: CidNlMethodResult | None
    peak_observation_content_id: str | None
    peak_hypothesis_id: str | None
    method_assessment_artifact_content_id: str
    evidence_artifact_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    outside_group: OutsideGroupCellRelation | None = None
    unassigned_support: bool = False


@dataclass(frozen=True, slots=True)
class CellObservationRef:
    """One exact cell closure under an already-existing provisional group."""

    content: CellObservationContent
    group_context: CidNlCellGroupContext
    method_assessment: CidNlMethodAssessmentRef
    method_evidence: CellMethodEvidence
    peak_observation: PeakObservationRef | None
    peak_hypothesis: PeakHypothesis | None
    artifact: EvidenceArtifactRef
    outside_group_context: CidNlCellGroupContext | None = None


def _direct_member_integration(
    *,
    member: CrossSampleGroupMemberSubject | None,
    processing_request_id: str,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    terminal_variant: TerminalObservationVariant,
    terminal_hypothesis: PeakHypothesis | None,
    method_evidence: CellMethodEvidence | None,
    peak_observation: PeakObservationRef | None,
    peak_hypothesis: PeakHypothesis | None,
) -> IntegrationResult | None:
    if member is None:
        return None
    sample_key = canonical_sample.identity.sample_key
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
            or any(
                observation.content.sample_key != sample_key
                for observation in direct_hypothesis.peak_observation_refs
            )
        ):
            raise CellObservationContractError(
                "direct hypothesis does not mint the exact final member"
            )
        if method_evidence is not None:
            supported_observation_ids = method_evidence.supported_observation_ids
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
        and support.sample.identity.sample_key == method_assessment.content.sample_key
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
    if method_assessment.content.sample_key != (sample.identity.sample_key):
        raise CellObservationContractError("assessment references a different sample")
    support_binding = any(
        support.assessment.artifact.evidence_artifact_id
        == method_assessment.artifact.evidence_artifact_id
        and support.sample.identity.sample_key == sample.identity.sample_key
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
        sample_key=sample.identity.sample_key,
        group_hypothesis_id=group.group_hypothesis_id,
        group_owner_ids=group_context.sorted_owner_ids,
        group_method_subject_id=group_context.method_subject_id,
        group_target_label=group_context.target_label,
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
        outside_group=outside_group,
        unassigned_support=unassigned_support,
    )
    upstreams = _cell_upstream_artifacts(
        group_context=group_context,
        method_assessment=method_assessment,
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
        method_evidence=CidNlCellEvidence(method_assessment),
        peak_observation=peak_observation,
        peak_hypothesis=peak_hypothesis,
        artifact=artifact,
        outside_group_context=outside_group_context,
    )


def canonical_cell_observation_bytes(content: CellObservationContent) -> bytes:
    if not isinstance(content, CellObservationContent):
        raise CellObservationContractError(
            "canonical serialization requires CellObservation content"
        )
    # JSON reads immutable tuples directly; copying every group-support ID for
    # every sample adds a second population traversal without changing bytes.
    values = {item.name: getattr(content, item.name) for item in fields(content)}
    # This null slot is part of the persisted content/identity encoding.
    values["human_disposition_ref_id"] = None
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
    outside_group_context: CidNlCellGroupContext | None = None,
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts = [method_assessment.artifact]
    if outside_group_context is not None:
        artifacts.extend(outside_group_context.support_artifacts)
    artifacts.extend(group_context.support_artifacts)
    artifacts_by_id = {item.evidence_artifact_id: item for item in artifacts}
    return tuple(
        artifacts_by_id[artifact_id] for artifact_id in sorted(artifacts_by_id)
    )
