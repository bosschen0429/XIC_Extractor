"""Cell observation predecessor records and canonical artifact encoding."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairReceipt,
)
from xic_extractor.alignment.cell_group_context import (
    _ARTIFACT_CONTENT_ID_PREFIX,
    _ARTIFACT_SEMANTIC_CONTRACT,
    _EVIDENCE_ARTIFACT_ID_PREFIX,
    _METHOD_SUBJECT_ID_PREFIX,
    _PEAK_CONTENT_ID_PREFIX,
    _PEAK_HYPOTHESIS_ID_PREFIX,
    _SAMPLE_ID_PREFIX,
    _TARGET_DEFINITION_ID_PREFIX,
    _TARGET_KEY_ID_PREFIX,
    SCHEMA_VERSION,
    VALIDATION_STATUS,
    CellObservationContractError,
    CidNlCellGroupContext,
    TerminalObservationVariant,
    _canonical_typed_ids,
    _group_support_artifacts,
    _required_text,
    _validate_group_support_attempts,
    _validate_typed_digest_id,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_projection import (
    selected_integration_authority_projection,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    CidNlMethodResult,
    CidNlTechnicalState,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    validate_cid_nl_peak_hypothesis_binding,
)
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    retry_predecessor_attempt_ids,
    validate_typed_identity_id,
)
from xic_extractor.review_records import ResolvedReviewRecordRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


@dataclass(frozen=True, slots=True)
class OutsideGroupCellRelation:
    """Reference support in one exact sibling without a group-local observation."""

    supporting_group_hypothesis_id: str
    supporting_peak_hypothesis_id: str
    relation: Literal["outside_group"] = "outside_group"

    def __post_init__(self) -> None:
        if self.relation != "outside_group":
            raise CellObservationContractError(
                "outside-group relation is not canonical"
            )
        _required_text(
            self.supporting_group_hypothesis_id, field="supporting_group_hypothesis_id"
        )
        _validate_typed_digest_id(
            self.supporting_peak_hypothesis_id,
            prefix=_PEAK_HYPOTHESIS_ID_PREFIX,
            field="supporting_peak_hypothesis_id",
        )


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

    def __post_init__(self) -> None:
        _validate_typed_digest_id(
            self.canonical_sample_identity_id,
            prefix=_SAMPLE_ID_PREFIX,
            field="canonical_sample_identity_id",
        )
        _required_text(self.group_hypothesis_id, field="group_hypothesis_id")
        if not self.group_owner_ids:
            raise CellObservationContractError("group_owner_ids must be non-empty")
        _canonical_typed_ids(
            self.group_owner_ids,
            prefix=_PEAK_HYPOTHESIS_ID_PREFIX,
            field="group_owner_ids",
        )
        _validate_typed_digest_id(
            self.group_method_subject_id,
            prefix="xic.cid-nl-method-subject:"
            if self.group_method_subject_id.startswith("xic.cid-nl-method-subject:")
            else _METHOD_SUBJECT_ID_PREFIX,
            field="group_method_subject_id",
        )
        target_ids = (
            self.group_target_key_id,
            self.group_target_definition_id,
        )
        if any(value is None for value in target_ids) and any(
            value is not None for value in target_ids
        ):
            raise CellObservationContractError(
                "group Targeted compatibility identity must be complete or absent"
            )
        if self.group_target_key_id is not None:
            _validate_typed_digest_id(
                self.group_target_key_id,
                prefix=_TARGET_KEY_ID_PREFIX,
                field="group_target_key_id",
            )
            assert self.group_target_definition_id is not None
            _validate_typed_digest_id(
                self.group_target_definition_id,
                prefix=_TARGET_DEFINITION_ID_PREFIX,
                field="group_target_definition_id",
            )
        _canonical_typed_ids(
            self.group_support_assessment_artifact_content_ids,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="group_support_assessment_artifact_content_ids",
        )
        if self.peak_observation_content_id is not None:
            _validate_typed_digest_id(
                self.peak_observation_content_id,
                prefix=_PEAK_CONTENT_ID_PREFIX,
                field="peak_observation_content_id",
            )
        if self.peak_hypothesis_id is not None:
            _validate_typed_digest_id(
                self.peak_hypothesis_id,
                prefix=_PEAK_HYPOTHESIS_ID_PREFIX,
                field="peak_hypothesis_id",
            )
        _validate_typed_digest_id(
            self.method_assessment_artifact_content_id,
            prefix=_ARTIFACT_CONTENT_ID_PREFIX,
            field="method_assessment_artifact_content_id",
        )
        _canonical_typed_ids(
            self.evidence_artifact_ids,
            prefix=_EVIDENCE_ARTIFACT_ID_PREFIX,
            field="evidence_artifact_ids",
        )
        if (
            not isinstance(self.reason_codes, tuple)
            or not self.reason_codes
            or any(
                not isinstance(item, str) or not item.strip()
                for item in self.reason_codes
            )
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
        ):
            raise CellObservationContractError(
                "reason_codes must be non-empty, unique, and canonical-sorted"
            )
        if self.human_disposition_ref_id is not None:
            _required_text(
                self.human_disposition_ref_id,
                field="human_disposition_ref_id",
            )
        if self.technical_state not in {
            "evaluated",
            "unsupported",
            "failed",
            "not_evaluated",
        }:
            raise CellObservationContractError(
                "technical_state must be evaluated, unsupported, failed, or "
                "not_evaluated"
            )
        if self.method_result is not None and self.method_result not in {
            "SUPPORT",
            "HOLD",
            "REJECT",
        }:
            raise CellObservationContractError(
                "method_result must be SUPPORT, HOLD, REJECT, or None"
            )
        if self.technical_state != "evaluated" and self.method_result is not None:
            raise CellObservationContractError(
                "non-evaluated technical state requires method_result=None"
            )
        if self.technical_state == "evaluated" and self.method_result is None:
            raise CellObservationContractError(
                "evaluated technical state requires a named method result"
            )
        if self.outside_group is not None:
            if type(self.outside_group) is not OutsideGroupCellRelation:
                raise CellObservationContractError(
                    "outside-group relation has the wrong owner type"
                )
            self.outside_group.__post_init__()
            if (
                self.method_result != "SUPPORT"
                or self.peak_observation_content_id is not None
                or self.peak_hypothesis_id is not None
                or self.outside_group.supporting_group_hypothesis_id
                == self.group_hypothesis_id
                or self.outside_group.supporting_peak_hypothesis_id
                in self.group_owner_ids
            ):
                raise CellObservationContractError(
                    "outside-group SUPPORT cannot carry a group-local observation"
                )
        elif self.unassigned_support:
            if (
                self.method_result != "SUPPORT"
                or self.peak_observation_content_id is not None
                or self.peak_hypothesis_id is not None
            ):
                raise CellObservationContractError(
                    "unassigned SUPPORT cannot supply a local value"
                )
        elif self.method_result == "SUPPORT":
            if (
                self.peak_observation_content_id is None
                or self.peak_hypothesis_id is None
            ):
                raise CellObservationContractError(
                    "SUPPORT requires an observation and owner-backed hypothesis"
                )
        elif self.peak_hypothesis_id is not None:
            raise CellObservationContractError(
                "only SUPPORT may carry an individual PeakHypothesis"
            )


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

    def __post_init__(self) -> None:
        if not isinstance(self.content, CellObservationContent):
            raise CellObservationContractError(
                "content must be an owner-normalized CellObservation"
            )
        if not isinstance(self.method_assessment, CidNlMethodAssessmentRef):
            raise CellObservationContractError(
                "method_assessment must be an owner-backed reference"
            )
        if not isinstance(self.group_context, CidNlCellGroupContext):
            raise CellObservationContractError(
                "cell requires an owner-validated group context"
            )
        group = self.group_context.group
        if (
            self.content.group_hypothesis_id != group.group_hypothesis_id
            or self.content.group_owner_ids != tuple(sorted(group.owner_ids))
        ):
            raise CellObservationContractError(
                "cell group identity does not match its exact group context"
            )
        if (
            self.content.group_method_subject_id != self.group_context.method_subject_id
            or self.content.group_target_key_id != self.group_context.target_key_id
            or self.content.group_target_definition_id
            != self.group_context.target_definition_id
            or self.content.group_support_assessment_artifact_content_ids
            != self.group_context.support_assessment_artifact_content_ids
        ):
            raise CellObservationContractError(
                "cell group method subject or SUPPORT closure was substituted"
            )
        exact_context_support = any(
            support.assessment is self.method_assessment
            and support.sample.identity.sample_identity_id
            == self.content.canonical_sample_identity_id
            for support in self.group_context.supports
        )
        if (
            self.method_assessment.content.method_subject
            != self.group_context.method_subject
            and not exact_context_support
        ):
            raise CellObservationContractError(
                "cell assessment differs from the recovery subject without an "
                "exact group SUPPORT binding"
            )
        if self.content.canonical_sample_identity_id != (
            self.method_assessment.content.canonical_sample_identity_id
        ):
            raise CellObservationContractError(
                "cell and method assessment reference different samples"
            )
        if (
            self.content.technical_state
            != (self.method_assessment.content.technical_state)
            or self.content.method_result
            != self.method_assessment.content.method_result
        ):
            raise CellObservationContractError(
                "cell rewrites technical or method-result axes"
            )
        if self.content.reason_codes != self.method_assessment.content.reason_codes:
            raise CellObservationContractError(
                "cell rewrites method-assessment reason codes"
            )
        expected_relation = _outside_group_relation(
            group_context=self.group_context,
            method_assessment=self.method_assessment,
            outside_group_context=self.outside_group_context,
        )
        if self.content.outside_group != expected_relation:
            raise CellObservationContractError(
                "outside-group SUPPORT closure was substituted"
            )
        if self.content.unassigned_support and (
            exact_context_support
            or self.outside_group_context is not None
            or any(
                member.canonical_sample_identity_id
                == self.content.canonical_sample_identity_id
                for member in group.member_subjects
            )
        ):
            raise CellObservationContractError(
                "unassigned SUPPORT cannot replace a group member"
            )
        expected_evidence_ids = tuple(
            sorted(
                evidence.artifact.evidence_artifact_id
                for evidence in self.method_assessment.evidence_refs
            )
        )
        if self.content.evidence_artifact_ids != expected_evidence_ids:
            raise CellObservationContractError(
                "cell evidence refs do not match the method assessment"
            )
        if self.content.method_assessment_artifact_content_id != (
            self.method_assessment.artifact.content_identity.artifact_content_id
        ):
            raise CellObservationContractError(
                "cell references a different method assessment"
            )
        observation_content_id = (
            None
            if self.peak_observation is None
            else self.peak_observation.content.peak_observation_content_id
        )
        if self.content.peak_observation_content_id != observation_content_id:
            raise CellObservationContractError(
                "cell peak observation reference was substituted"
            )
        hypothesis_id = (
            None if self.peak_hypothesis is None else self.peak_hypothesis.hypothesis_id
        )
        if self.content.peak_hypothesis_id != hypothesis_id:
            raise CellObservationContractError(
                "cell PeakHypothesis reference was substituted"
            )
        if self.peak_hypothesis is not None and not any(
            support.hypothesis is self.peak_hypothesis
            for support in self.group_context.supports
        ):
            raise CellObservationContractError(
                "SUPPORT cell does not reuse an exact group SUPPORT hypothesis"
            )
        human_id = (
            None
            if self.human_disposition is None
            else self.human_disposition.reference.review_record_id
        )
        if self.content.human_disposition_ref_id != human_id:
            raise CellObservationContractError(
                "cell human disposition reference was substituted"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise CellObservationContractError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise CellObservationContractError(
                "artifact semantic contract is not owned by CellObservation"
            )
        expected_upstream_ids = tuple(
            artifact.evidence_artifact_id
            for artifact in _cell_upstream_artifacts(
                group_context=self.group_context,
                method_assessment=self.method_assessment,
                peak_observation=self.peak_observation,
                peak_hypothesis=self.peak_hypothesis,
                outside_group_context=self.outside_group_context,
            )
        )
        if self.artifact.upstream_artifact_ids != expected_upstream_ids:
            raise CellObservationContractError(
                "cell upstream artifact closure was substituted"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                canonical_cell_observation_bytes(self.content),
            )
        except ValueError as exc:
            raise CellObservationContractError(
                "cell observation mismatches its immutable artifact"
            ) from exc


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
            if (
                direct_hypothesis.evidence.cid_nl_chromatographic_support_ref
                is not None
            ):
                try:
                    validate_cid_nl_assessment_hypothesis_closure(
                        sample=canonical_sample,
                        assessment=method_assessment,
                        hypothesis=direct_hypothesis,
                    )
                except HypothesisOwnerAdapterError as exc:
                    raise CellObservationContractError(str(exc)) from exc
        return direct_hypothesis.integration
    if peak_observation is not None:
        if type(peak_observation) is not PeakObservationRef:
            raise CellObservationContractError(
                "direct member observation is not owner-backed"
            )
        observation_id = peak_observation.content.peak_observation_content_id
        if (
            member.subject_kind != "physical_observation"
            or member.subject_id != observation_id
            or peak_observation.artifact.producer_processing_request_id
            != processing_request_id
            or peak_observation.content.canonical_sample_identity_id
            != canonical_sample_identity_id
        ):
            raise CellObservationContractError(
                "direct observation does not mint the exact final member"
            )
        if method_assessment is not None:
            if (
                method_assessment.content.method_result != "SUPPORT"
                or peak_observation not in method_assessment.candidate_peaks
                or not any(
                    relation.content.peak_observation_content_id == observation_id
                    for relation in method_assessment.individual_support_refs
                )
            ):
                raise CellObservationContractError(
                    "direct member observation is outside the exact SUPPORT subset"
                )
        return peak_observation.content.integration
    return None


def _repaired_member_integration(
    *,
    member: CrossSampleGroupMemberSubject | None,
    group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    repair_receipt: ObservationRepairReceipt | None,
    repaired_observation: OwnerBackfillPeakFactObservation | None,
) -> IntegrationResult | None:
    if repair_receipt is None and repaired_observation is None:
        return None
    if repair_receipt is None or repaired_observation is None:
        raise CellObservationContractError(
            "repaired member requires both receipt and live owner observation"
        )
    if type(repair_receipt) is not ObservationRepairReceipt:
        raise CellObservationContractError("repair receipt is not owner-minted")
    if member is None:
        return None
    subject = repair_receipt.subject
    evidence = repair_receipt.evidence
    if (
        repair_receipt.outcome != "repaired"
        or evidence is None
        or subject.member_subject != member
        or member not in group.member_subjects
        or subject.sample_identity != canonical_sample
        or evidence.member_subject_id != member.member_subject_id
        or evidence.group_hypothesis_id != subject.predecessor_group.group_hypothesis_id
    ):
        raise CellObservationContractError(
            "repair receipt does not authorize the exact final member"
        )
    try:
        validate_owner_backfill_peak_fact_observation(repaired_observation)
    except (AttributeError, TypeError, ValueError) as exc:
        raise CellObservationContractError(str(exc)) from exc
    facts = repaired_observation.facts
    evaluation_facts = (
        None
        if repaired_observation.physical_evaluation is None
        else repaired_observation.physical_evaluation.facts
    )
    if (
        facts is None
        or evaluation_facts is None
        or not repaired_observation.selected_for_output
        or repaired_observation.request_identity != subject.request_identity
        or repaired_observation.logical_request != subject.logical_request
        or repaired_observation.cell.sample_stem != subject.request_identity.sample_stem
        or repaired_observation.cell.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
        or evidence.fact_content_id != evaluation_facts.fact_content_id
    ):
        raise CellObservationContractError(
            "repaired observation crossed receipt provenance"
        )
    try:
        authority_id = selected_integration_authority_projection(
            facts.integration
        ).selected_integration_authority_content_id
    except (AttributeError, TypeError, ValueError) as exc:
        raise CellObservationContractError(
            "repair integration is not revalidatable"
        ) from exc
    if authority_id != evidence.selected_integration_authority_content_id:
        raise CellObservationContractError(
            "repair integration authority content ID was substituted"
        )
    return facts.integration


def _processing_request_id(value: object) -> str:
    try:
        return validate_typed_identity_id(
            value,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
    except ValueError as exc:
        raise CellObservationContractError(str(exc)) from exc


def _terminal_trace_group_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise CellObservationContractError(
            "terminal_trace_group_id must be normalized text"
        )
    return value


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

    if not isinstance(group_context, CidNlCellGroupContext):
        raise CellObservationContractError(
            "CellObservation requires an owner-validated group context"
        )
    allowed_group_attempt_ids = {
        processing_owners.attempt.processing_attempt_id,
        *retry_predecessor_attempt_ids(processing_owners.attempt),
    }
    if (
        group_context.processing_attempt.processing_request_id
        != processing_owners.request.processing_request_id
        or group_context.processing_attempt.processing_attempt_id
        not in allowed_group_attempt_ids
    ):
        raise CellObservationContractError(
            "group SUPPORT closure is outside the current attempt or retry lineage"
        )
    _validate_group_support_attempts(
        group_context.supports,
        producer_attempt=processing_owners.attempt,
    )
    group = group_context.group
    subject = method_assessment.content.method_subject
    exact_context_support = any(
        support.assessment is method_assessment
        and support.sample.identity.sample_identity_id
        == sample.identity.sample_identity_id
        for support in group_context.supports
    )
    if subject != group_context.method_subject and not exact_context_support:
        raise CellObservationContractError(
            "assessment differs from the group recovery subject without an exact "
            "SUPPORT binding"
        )
    try:
        if target is None:
            validate_cid_nl_method_assessment_binding(
                method_assessment,
                processing_owners=processing_owners,
                sample=sample,
                method_subject=subject,
            )
        else:
            validate_cid_nl_method_assessment_binding(
                method_assessment,
                processing_owners=processing_owners,
                sample=sample,
                target=target,
            )
    except ValueError as exc:
        raise CellObservationContractError(str(exc)) from exc
    method_result = method_assessment.content.method_result
    outside_group = _outside_group_relation(
        group_context=group_context,
        method_assessment=method_assessment,
        outside_group_context=outside_group_context,
    )
    if unassigned_support and not (
        method_result == "SUPPORT"
        and outside_group is None
        and peak_observation is None
        and peak_hypothesis is None
        and not any(
            member.canonical_sample_identity_id == sample.identity.sample_identity_id
            for member in group.member_subjects
        )
    ):
        raise CellObservationContractError(
            "unassigned SUPPORT cannot replace a group member"
        )
    if unassigned_support:
        pass
    elif outside_group is not None:
        if peak_observation is not None or peak_hypothesis is not None:
            raise CellObservationContractError(
                "outside-group SUPPORT cannot carry a group-local observation"
            )
    elif method_result == "SUPPORT":
        if peak_observation is None or peak_hypothesis is None:
            raise CellObservationContractError(
                "SUPPORT cell requires the exact observation and hypothesis"
            )
        support = peak_hypothesis.evidence.cid_nl_chromatographic_support_ref
        if support is None:
            raise CellObservationContractError(
                "SUPPORT hypothesis is missing chromatographic support"
            )
        try:
            validate_cid_nl_assessment_hypothesis_closure(
                sample=sample,
                assessment=method_assessment,
                hypothesis=peak_hypothesis,
            )
            validate_cid_nl_peak_hypothesis_binding(
                peak_hypothesis,
                processing_owners=processing_owners,
                sample=sample,
                peak_observation=peak_observation,
                chromatographic_support=support,
            )
        except (HypothesisOwnerAdapterError, PeakHypothesisContractError) as exc:
            raise CellObservationContractError(str(exc)) from exc
        if peak_hypothesis.hypothesis_id not in group.owner_ids:
            raise CellObservationContractError(
                "SUPPORT hypothesis is not a member of the existing group"
            )
        if not any(
            group_support.hypothesis is peak_hypothesis
            and group_support.assessment is method_assessment
            and group_support.sample is sample
            for group_support in group_context.supports
        ):
            raise CellObservationContractError(
                "SUPPORT cell does not reuse its exact group SUPPORT closure"
            )
    else:
        if peak_hypothesis is not None:
            raise CellObservationContractError(
                "HOLD, REJECT, and technical states cannot carry a fake hypothesis"
            )
        if peak_observation is not None and peak_observation not in (
            method_assessment.candidate_peaks
        ):
            raise CellObservationContractError(
                "cell observation is outside the method candidate universe"
            )
    if human_disposition is not None:
        if target is None:
            raise CellObservationContractError(
                "Untargeted method subjects have no Targeted review-record adapter"
            )
        if not isinstance(human_disposition, ResolvedReviewRecordRef):
            raise CellObservationContractError(
                "human_disposition must be an owner-resolved review record"
            )
        if processing_owners.review_record != human_disposition:
            raise CellObservationContractError(
                "human disposition is outside the processing owner closure"
            )
        review_reference = human_disposition.reference
        if (
            review_reference.processing_request_id
            != processing_owners.request.processing_request_id
        ):
            raise CellObservationContractError(
                "human disposition references a different processing request"
            )
        if (
            review_reference.canonical_sample_identity_id
            != sample.identity.sample_identity_id
        ):
            raise CellObservationContractError(
                "human disposition references a different sample"
            )
        if (
            review_reference.target_key_id != target.definition.target_key.target_key_id
            or review_reference.target_definition_id
            != target.definition.target_definition_id
        ):
            raise CellObservationContractError(
                "human disposition references a different target definition"
            )
        if method_assessment.artifact.evidence_artifact_id not in (
            review_reference.evidence_artifact_ids
        ):
            raise CellObservationContractError(
                "human disposition does not reference the current method assessment"
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
        group_owner_ids=tuple(sorted(group.owner_ids)),
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
    artifact = _mint_evidence_artifact_ref(
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
    )


def canonical_cell_observation_bytes(content: CellObservationContent) -> bytes:
    if not isinstance(content, CellObservationContent):
        raise CellObservationContractError(
            "canonical serialization requires CellObservation content"
        )
    values = asdict(content)
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
        artifacts.extend(_group_support_artifacts(outside_group_context.supports))
    for support in group_context.supports:
        artifacts.append(support.assessment.artifact)
        if support.hypothesis.artifact is None:
            raise CellObservationContractError(
                "group SUPPORT hypothesis is missing its immutable artifact"
            )
        artifacts.append(support.hypothesis.artifact)
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
