"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, asdict, dataclass, field, replace
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillAttemptTerminal,
    OwnerBackfillPlanningPosition,
    validate_owner_backfill_conservative_no_request_position,
)
from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairReceipt,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
    validate_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    HypothesisOwnerAdapterError,
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_support,
    validate_cid_nl_assessment_hypothesis_closure,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    selected_integration_authority_projection,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    CidNlMethodResult,
    CidNlTechnicalState,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    PeakHypothesisContractError,
    validate_cid_nl_peak_hypothesis_binding,
)
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingAttemptIdentity,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    retry_predecessor_attempt_ids,
    typed_identity_digest,
    validate_typed_identity_id,
)
from xic_extractor.review_records import ResolvedReviewRecordRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SCHEMA_VERSION = "cell_observation_v4"
OUTSIDE_GROUP_SCHEMA_VERSION = "cell_observation_v5"
ARTIFACT_SEMANTIC_ROLE = "diagnostic_cell_observation"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf-v1"

_ARTIFACT_CONTENT_ID_PREFIX = "xic.artifact-content.v1:"
_EVIDENCE_ARTIFACT_ID_PREFIX = "xic.evidence-artifact.v1:"
_PEAK_CONTENT_ID_PREFIX = "xic.peak-observation-content.v1:"
_PEAK_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis.v1:"
_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_METHOD_SUBJECT_ID_PREFIX = "xic.cid-nl-method-subject.v1:"
_TARGET_KEY_ID_PREFIX = "xic.target-key.v1:"
_TARGET_DEFINITION_ID_PREFIX = "xic.target-definition.v1:"
_FINAL_CELL_OBSERVATION_ID_PREFIX = "xic.final-cell-observation.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FINAL_CELL_MINT_TOKEN = object()
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)
_OUTSIDE_GROUP_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.cell_observations",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=OUTSIDE_GROUP_SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


class CellObservationContractError(ValueError):
    """Raised when one assessment cannot enter an existing group cell exactly."""


@dataclass(frozen=True, slots=True)
class CidNlCellGroupSupport:
    """One exact SUPPORT closure that owns membership in a provisional group."""

    sample: ResolvedCanonicalSampleIdentity
    assessment: CidNlMethodAssessmentRef
    hypothesis: PeakHypothesis

    def __post_init__(self) -> None:
        try:
            validate_cid_nl_assessment_hypothesis_closure(
                sample=self.sample,
                assessment=self.assessment,
                hypothesis=self.hypothesis,
            )
        except HypothesisOwnerAdapterError as exc:
            raise CellObservationContractError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class CidNlCellGroupContext:
    """Method-subject proof for one group and every SUPPORT owner that formed it."""

    group: CrossSamplePeakGroupHypothesis
    supports: tuple[CidNlCellGroupSupport, ...]
    config: AlignmentConfig
    processing_attempt: ProcessingAttemptIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.group, CrossSamplePeakGroupHypothesis) or not (
            self.group.owner_ids
        ):
            raise CellObservationContractError(
                "group context requires an existing provisional group"
            )
        if (
            not isinstance(self.supports, tuple)
            or not self.supports
            or any(
                not isinstance(item, CidNlCellGroupSupport) for item in self.supports
            )
        ):
            raise CellObservationContractError(
                "group context requires exact SUPPORT closures"
            )
        if not isinstance(self.config, AlignmentConfig):
            raise CellObservationContractError(
                "group context requires the exact AlignmentConfig"
            )
        if not isinstance(self.processing_attempt, ProcessingAttemptIdentity):
            raise CellObservationContractError(
                "group context requires an owner-issued processing attempt"
            )
        sample_ids = tuple(
            support.sample.identity.sample_identity_id for support in self.supports
        )
        if sample_ids != tuple(sorted(set(sample_ids))):
            raise CellObservationContractError(
                "group SUPPORT closures must be unique and canonical-sorted by sample"
            )
        method_subjects = {
            support.assessment.content.method_subject for support in self.supports
        }
        if len(method_subjects) != 1:
            raise CellObservationContractError(
                "group SUPPORT closures reference different method subjects"
            )
        expected_owners = tuple(
            owner_from_cid_nl_assessment_support(
                sample=support.sample,
                assessment=support.assessment,
                hypothesis=support.hypothesis,
            )
            for support in self.supports
        )
        expected_owner_ids = tuple(owner.owner_id for owner in expected_owners)
        if len(set(expected_owner_ids)) != len(self.supports):
            raise CellObservationContractError(
                "group SUPPORT closures contain duplicate hypotheses"
            )
        _validate_group_support_attempts(
            self.supports,
            producer_attempt=self.processing_attempt,
        )
        expected_groups = construct_cross_sample_peak_group_hypotheses(
            expected_owners,
            config=self.config,
        )
        validate_cross_sample_peak_group_identity(self.group, require_canonical=True)
        # Public family ordinals belong to the complete population's presentation.
        if (
            len(expected_groups) != 1
            or replace(expected_groups[0], public_family_id=self.group.public_family_id)
            != self.group
        ):
            raise CellObservationContractError(
                "group context does not close its exact SUPPORT owners and config"
            )

    @property
    def method_subject(self) -> CidNlMethodSubject:
        return self.supports[0].assessment.content.method_subject

    @property
    def method_subject_id(self) -> str:
        return self.method_subject.method_subject_id

    @property
    def target_key_id(self) -> str | None:
        """Targeted compatibility projection; not group identity."""

        return self.method_subject.target_key_id

    @property
    def target_definition_id(self) -> str | None:
        """Targeted compatibility projection; not group identity."""

        return self.method_subject.target_definition_id

    @property
    def support_assessment_artifact_content_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                support.assessment.artifact.content_identity.artifact_content_id
                for support in self.supports
            )
        )


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
            prefix="xic.cid-nl-method-subject.v2:"
            if self.group_method_subject_id.startswith("xic.cid-nl-method-subject.v2:")
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
        if self.method_assessment.content.method_subject != (
            self.group_context.method_subject
        ):
            raise CellObservationContractError(
                "cell assessment references a different method subject than its group"
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
        if self.artifact.semantic_contract != (
            _OUTSIDE_GROUP_ARTIFACT_SEMANTIC_CONTRACT
            if self.content.outside_group is not None
            else _ARTIFACT_SEMANTIC_CONTRACT
        ):
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


FinalCellEvidenceState: TypeAlias = Literal[
    "direct_observation",
    "repaired_observation",
    "unresolved",
    "rejected_observation",
    "searched_no_observation",
    "unavailable",
    "not_searched",
]
TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]


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
    group_context.__post_init__()
    outside_group_context.__post_init__()
    if (
        method_assessment.content.method_result != "SUPPORT"
        or outside_group_context.method_subject != group_context.method_subject
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
    if subject != group_context.method_subject:
        raise CellObservationContractError(
            "assessment references a different target or method subject than the "
            "group context"
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
    if outside_group is not None:
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
        semantic_contract=(
            _OUTSIDE_GROUP_ARTIFACT_SEMANTIC_CONTRACT
            if outside_group is not None
            else _ARTIFACT_SEMANTIC_CONTRACT
        ),
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
    if content.outside_group is None:
        values.pop("outside_group")
    payload = {
        "schema_version": (
            OUTSIDE_GROUP_SCHEMA_VERSION
            if content.outside_group is not None
            else SCHEMA_VERSION
        ),
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


def _group_support_artifacts(
    supports: tuple[CidNlCellGroupSupport, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    artifacts: list[EvidenceArtifactRef] = []
    for support in supports:
        artifacts.append(support.assessment.artifact)
        if support.hypothesis.artifact is None:
            raise CellObservationContractError(
                "group SUPPORT hypothesis is missing its immutable artifact"
            )
        artifacts.append(support.hypothesis.artifact)
    artifacts_by_id = {item.evidence_artifact_id: item for item in artifacts}
    return tuple(
        artifacts_by_id[artifact_id] for artifact_id in sorted(artifacts_by_id)
    )


def _validate_group_support_attempts(
    supports: tuple[CidNlCellGroupSupport, ...],
    *,
    producer_attempt: ProcessingAttemptIdentity,
) -> None:
    allowed_attempt_ids = {
        producer_attempt.processing_attempt_id,
        *retry_predecessor_attempt_ids(producer_attempt),
    }
    for artifact in _group_support_artifacts(supports):
        if (
            artifact.producer_processing_request_id
            != producer_attempt.processing_request_id
            or artifact.producer_processing_attempt_id not in allowed_attempt_ids
        ):
            raise CellObservationContractError(
                "group SUPPORT artifacts are outside the current attempt or retry "
                "lineage"
            )


def _canonical_typed_ids(value: object, *, prefix: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise CellObservationContractError(f"{field} must be a tuple")
    ids = tuple(
        _validate_typed_digest_id(item, prefix=prefix, field=field) for item in value
    )
    if ids != tuple(sorted(set(ids))):
        raise CellObservationContractError(
            f"{field} must be unique and canonical-sorted"
        )
    return ids


def _validate_typed_digest_id(value: object, *, prefix: str, field: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise CellObservationContractError(f"{field} must use the {prefix!r} prefix")
    if _SHA256_RE.fullmatch(value[len(prefix) :]) is None:
        raise CellObservationContractError(f"{field} must contain a SHA-256 digest")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CellObservationContractError(f"{field} must be non-empty")
    if value != value.strip():
        raise CellObservationContractError(
            f"{field} must not contain surrounding whitespace"
        )
    return value


# ---------------------------------------------------------------------------
# Exact CrossSample/Backfill handoff closure
# ---------------------------------------------------------------------------
#
# The first implementation of this module exposed a final-cell mint function
# whose caller supplied group, terminal, provider, and evidence arguments.  A
# final cell is a correlation boundary, so those arguments are not an owner
# contract: the only admissible inputs are the CrossSample handoff and the
# owner-minted predecessor cell population.  The definitions below intentionally
# supersede the legacy names above while keeping the low-level
# ``mint_cell_observation_ref`` owner API intact.


FinalCellEvidenceAggregate: TypeAlias = FinalCellEvidenceState | Literal["mixed"]


@dataclass(frozen=True, slots=True)
class FinalGroupCellObservation:
    """One immutable final ``CrossSampleGroupIdentity × sample`` closure.

    ``position_associations`` and ``predecessor_cells`` are parallel canonical
    lanes.  A merge keeps every lane, while a split remainder is represented by
    an empty lane tuple only after the complete owner handoff has proved that no
    position maps to this final key.  This object carries evidence only; it has
    no value, Matrix, writer, or quantifiability authority.
    """

    cell_observation_id: str
    processing_request_id: str
    group: CrossSampleGroupIdentity
    canonical_sample: ResolvedCanonicalSampleIdentity
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    predecessor_cells: tuple[CellObservationRef, ...]
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...]
    repair_receipts: tuple[ObservationRepairReceipt, ...]
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...]
    evidence_states: tuple[FinalCellEvidenceState, ...]
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FINAL_CELL_MINT_TOKEN:
            raise CellObservationContractError(
                "final cell observation must be minted by CellObservation"
            )
        _validate_final_group_cell_observation(self)

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def canonical_sample_identity_id(self) -> str:
        return self.canonical_sample.identity.sample_identity_id

    @property
    def evidence_state(self) -> FinalCellEvidenceAggregate:
        """Return a scalar only when every retained lane has the same state."""

        if not self.evidence_states:
            return "not_searched"
        states = set(self.evidence_states)
        if len(states) == 1:
            return self.evidence_states[0]
        return "mixed"

    @property
    def method_assessments(self) -> tuple[CidNlMethodAssessmentRef, ...]:
        return tuple(cell.method_assessment for cell in self.predecessor_cells)

    @property
    def direct_observations(self) -> tuple[PeakObservationRef, ...]:
        return tuple(
            cell.peak_observation
            for cell in self.predecessor_cells
            if cell.peak_observation is not None
        )

    @property
    def direct_hypotheses(self) -> tuple[PeakHypothesis, ...]:
        return tuple(
            cell.peak_hypothesis
            for cell in self.predecessor_cells
            if cell.peak_hypothesis is not None
        )

    @property
    def integrations(self) -> tuple[IntegrationResult, ...]:
        direct = tuple(
            hypothesis.integration
            for hypothesis in self.direct_hypotheses
            if hypothesis.integration is not None
        )
        repaired = tuple(
            observation.facts.integration
            for observation in self.repaired_observations
            if observation.facts is not None
        )
        return direct + repaired

    @property
    def provider_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.method_assessment.content.provider_id
            for cell in self.predecessor_cells
        )

    @property
    def method_subject_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.group_context.method_subject_id for cell in self.predecessor_cells
        )

    @property
    def position_ids(self) -> tuple[str, ...]:
        return tuple(
            association.position.position_id
            for association in self.position_associations
        )

    @property
    def source_cell_artifact_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.artifact.evidence_artifact_id for cell in self.predecessor_cells
        )

    @property
    def repair_provenance(
        self,
    ) -> tuple[
        tuple[ObservationRepairReceipt, ...],
        tuple[OwnerBackfillPeakFactObservation, ...],
    ]:
        return self.repair_receipts, self.repaired_observations


def compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: Sequence[CellObservationRef],
    repair_receipts: Sequence[ObservationRepairReceipt] = (),
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    """Compose one exact final closure for every final Group × sample key.

    The complete Backfill coverage and CrossSample handoff are the only source
    of correlation.  ``predecessor_cells`` must cover every handoff position
    exactly once; caller-authored mappings, incomplete populations, and scalar
    group substitutions are rejected before a final object is minted.
    """

    request_id = _validate_final_handoff(handoff)
    positions = tuple(handoff.coverage.epoch.positions)
    source_by_position = _index_predecessor_cells(
        predecessor_cells,
        positions=positions,
        processing_request_id=request_id,
    )
    for position in positions:
        validate_owner_backfill_conservative_no_request_position(
            position,
            source_cell=source_by_position[position.position_id],
            source_population=tuple(predecessor_cells),
        )
    receipt_by_position, observation_by_receipt = _index_repair_inputs(
        repair_receipts,
        repaired_observations,
        positions=positions,
        final_population=handoff.final_population,
        processing_request_id=request_id,
    )
    _validate_handoff_repair_closure(
        handoff=handoff,
        positions=positions,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        receipt_by_position=receipt_by_position,
        observation_by_receipt=observation_by_receipt,
    )
    terminals_by_position: dict[str, tuple[OwnerBackfillAttemptTerminal, ...]] = {
        position.position_id: tuple(
            terminal
            for terminal in handoff.coverage.terminals
            if terminal.position_id == position.position_id
        )
        for position in positions
    }
    cells: list[FinalGroupCellObservation] = []
    for group in handoff.final_population:
        for sample in handoff.coverage.epoch.samples:
            associations = _associations_for_final_key(
                handoff.position_associations,
                group=group,
                sample=sample,
            )
            associations = tuple(
                sorted(associations, key=lambda item: item.position.position_id)
            )
            source_lanes = tuple(
                source_by_position[item.position.position_id] for item in associations
            )
            coverage_lanes = _canonical_terminal_subset(
                associations,
                terminals_by_position,
            )
            receipts = tuple(
                sorted(
                    (
                        receipt
                        for association in associations
                        for receipt in receipt_by_position.get(
                            association.position.position_id,
                            (),
                        )
                    ),
                    key=lambda item: item.receipt_id,
                )
            )
            repaired = tuple(
                sorted(
                    (
                        observation
                        for receipt in receipts
                        for observation in observation_by_receipt.get(
                            receipt.receipt_id,
                            (),
                        )
                    ),
                    key=lambda item: item.work_unit_key,
                )
            )
            states: tuple[FinalCellEvidenceState, ...]
            if not associations:
                states = ("not_searched",)
            else:
                states = tuple(
                    _derive_lane_evidence_state(
                        association=association,
                        source_cell=source_by_position[
                            association.position.position_id
                        ],
                        coverage_terminals=terminals_by_position[
                            association.position.position_id
                        ],
                        repair_receipts=receipt_by_position.get(
                            association.position.position_id,
                            (),
                        ),
                        repaired_observations=tuple(
                            observation
                            for receipt in receipt_by_position.get(
                                association.position.position_id,
                                (),
                            )
                            for observation in observation_by_receipt.get(
                                receipt.receipt_id,
                                (),
                            )
                        ),
                        final_group=group,
                        canonical_sample=sample,
                        processing_request_id=request_id,
                    )
                    for association in associations
                )
            cells.append(
                _mint_final_group_cell_observation(
                    processing_request_id=request_id,
                    handoff=handoff,
                    group=group,
                    canonical_sample=sample,
                    position_associations=associations,
                    predecessor_cells=source_lanes,
                    coverage_terminals=coverage_lanes,
                    repair_receipts=receipts,
                    repaired_observations=repaired,
                    evidence_states=states,
                )
            )
    return tuple(sorted(cells, key=_final_cell_sort_key))


def validate_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    cell_observations: Sequence[FinalGroupCellObservation],
) -> tuple[FinalGroupCellObservation, ...]:
    """Revalidate exact handoff, source population, lanes, and final keys."""

    _validate_final_handoff(handoff)
    if isinstance(cell_observations, (str, bytes)):
        raise CellObservationContractError(
            "cell population must be a finite owner-sealed sequence"
        )
    observations = tuple(cell_observations)
    if any(type(item) is not FinalGroupCellObservation for item in observations):
        raise CellObservationContractError(
            "cell population contains a non-owner observation"
        )
    expected_keys = tuple(
        (group.group_hypothesis_id, sample.identity.sample_identity_id)
        for group in handoff.final_population
        for sample in handoff.coverage.epoch.samples
    )
    actual_keys = tuple(
        (item.group_hypothesis_id, item.canonical_sample_identity_id)
        for item in observations
    )
    if len(actual_keys) != len(set(actual_keys)):
        raise CellObservationContractError(
            "final cell population contains a duplicate Group x sample position"
        )
    if set(actual_keys) != set(expected_keys):
        raise CellObservationContractError(
            "final cell population is missing or contains an extra Group x sample"
        )
    groups_by_id = {
        group.group_hypothesis_id: group for group in handoff.final_population
    }
    samples_by_id = {
        sample.identity.sample_identity_id: sample
        for sample in handoff.coverage.epoch.samples
    }
    source_cells = tuple(
        cell for item in observations for cell in item.predecessor_cells
    )
    receipts = tuple(
        receipt for item in observations for receipt in item.repair_receipts
    )
    repaired = tuple(
        observation
        for item in observations
        for observation in item.repaired_observations
    )
    expected = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=source_cells,
        repair_receipts=receipts,
        repaired_observations=repaired,
    )
    expected_by_key = {
        (item.group_hypothesis_id, item.canonical_sample_identity_id): item
        for item in expected
    }
    ordered = tuple(sorted(observations, key=_final_cell_sort_key))
    for item in ordered:
        key = (item.group_hypothesis_id, item.canonical_sample_identity_id)
        expected_item = expected_by_key[key]
        _validate_final_cell_handoff_refs(item, handoff=handoff)
        if item != expected_item:
            raise CellObservationContractError(
                "final cell does not retain the exact owner-composed content"
            )
        _require_same_refs(item, expected_item)
        if item.group is not groups_by_id[item.group_hypothesis_id]:
            raise CellObservationContractError(
                "final cell substituted an equivalent final group"
            )
        if (
            item.canonical_sample
            is not samples_by_id[item.canonical_sample_identity_id]
        ):
            raise CellObservationContractError(
                "final cell substituted an equivalent canonical sample"
            )
    return ordered


def _validate_final_handoff(
    handoff: CrossSampleAssociationLineageHandoff,
) -> str:
    if type(handoff) is not CrossSampleAssociationLineageHandoff:
        raise CellObservationContractError(
            "final closure requires an exact CrossSample association handoff"
        )
    try:
        validate_cross_sample_association_lineage_handoff(handoff)
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(str(exc)) from exc
    if handoff.final_population is not handoff.final_receipt.final_population:
        raise CellObservationContractError(
            "handoff final population is not the exact retained CrossSample receipt"
        )
    for group in handoff.final_population:
        if not any(group is item for item in handoff.final_receipt.final_population):
            raise CellObservationContractError(
                "handoff final population substituted an equivalent group"
            )
    epoch_groups = handoff.coverage.epoch.group_identities
    epoch_samples = handoff.coverage.epoch.samples
    for position_association in handoff.position_associations:
        if not any(
            position_association.final_successor is group
            for group in handoff.final_population
        ):
            raise CellObservationContractError(
                "handoff position association substituted its final group"
            )
        if not any(
            position_association.position.group_identity is group
            for group in epoch_groups
        ):
            raise CellObservationContractError(
                "handoff position association substituted its predecessor group"
            )
        if not any(
            position_association.position.sample is sample for sample in epoch_samples
        ):
            raise CellObservationContractError(
                "handoff position association substituted its canonical sample"
            )
    request_id = handoff.coverage.epoch.processing_owners.request.processing_request_id
    if handoff.final_receipt.processing_request_id != request_id:
        raise CellObservationContractError(
            "handoff crossed its owner processing request"
        )
    return request_id


def _index_predecessor_cells(
    predecessor_cells: Sequence[CellObservationRef],
    *,
    positions: tuple[OwnerBackfillPlanningPosition, ...],
    processing_request_id: str,
) -> dict[str, CellObservationRef]:
    if isinstance(predecessor_cells, (str, bytes)):
        raise CellObservationContractError(
            "predecessor cells must be a finite owner-sealed sequence"
        )
    cells = tuple(predecessor_cells)
    if len({id(cell) for cell in cells}) != len(cells):
        raise CellObservationContractError(
            "predecessor cell population duplicates an exact source object"
        )
    result: dict[str, CellObservationRef] = {}
    for cell in cells:
        _validate_source_cell(cell, processing_request_id=processing_request_id)
        source_identity = _source_group_identity(cell)
        matches = tuple(
            position
            for position in positions
            if position.sample.identity.sample_identity_id
            == cell.content.canonical_sample_identity_id
            and source_identity == position.group_identity
        )
        if len(matches) != 1:
            raise CellObservationContractError(
                "predecessor CellObservationRef does not bind exactly one "
                "Backfill coverage position by full group identity and sample"
            )
        position = matches[0]
        if position.position_id in result:
            raise CellObservationContractError(
                "predecessor CellObservationRef population duplicates a coverage "
                "position"
            )
        result[position.position_id] = cell
    expected_ids = {position.position_id for position in positions}
    if set(result) != expected_ids:
        raise CellObservationContractError(
            "predecessor CellObservationRef population is incomplete or extra"
        )
    return result


def _validate_source_cell(
    cell: CellObservationRef,
    *,
    processing_request_id: str,
) -> None:
    if type(cell) is not CellObservationRef:
        raise CellObservationContractError(
            "predecessor population contains a non-owner CellObservationRef"
        )
    try:
        CellObservationRef(
            content=cell.content,
            group_context=cell.group_context,
            method_assessment=cell.method_assessment,
            peak_observation=cell.peak_observation,
            peak_hypothesis=cell.peak_hypothesis,
            human_disposition=cell.human_disposition,
            artifact=cell.artifact,
            outside_group_context=cell.outside_group_context,
        )
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(
            f"predecessor CellObservationRef is not revalidatable: {exc}"
        ) from exc
    if cell.group_context.group.processing_request_id != processing_request_id:
        raise CellObservationContractError(
            "predecessor CellObservationRef crossed its processing request"
        )
    _source_group_identity(cell)


def _source_group_identity(cell: CellObservationRef) -> CrossSampleGroupIdentity:
    try:
        identity = validate_cross_sample_peak_group_identity(
            cell.group_context.group,
            require_canonical=True,
        )
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(
            f"predecessor CellObservationRef lacks a canonical group identity: {exc}"
        ) from exc
    if identity is None:
        raise CellObservationContractError(
            "predecessor CellObservationRef lacks a canonical group identity"
        )
    return identity


def _index_repair_inputs(
    repair_receipts: Sequence[ObservationRepairReceipt],
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation],
    *,
    positions: tuple[OwnerBackfillPlanningPosition, ...],
    final_population: tuple[CrossSampleGroupIdentity, ...],
    processing_request_id: str,
) -> tuple[
    dict[str, tuple[ObservationRepairReceipt, ...]],
    dict[str, tuple[OwnerBackfillPeakFactObservation, ...]],
]:
    if isinstance(repair_receipts, (str, bytes)) or isinstance(
        repaired_observations,
        (str, bytes),
    ):
        raise CellObservationContractError(
            "repair evidence must be finite owner-sealed sequences"
        )
    receipts = tuple(repair_receipts)
    observations = tuple(repaired_observations)
    if any(type(receipt) is not ObservationRepairReceipt for receipt in receipts):
        raise CellObservationContractError(
            "repair population contains a non-owner receipt"
        )
    if len({receipt.receipt_id for receipt in receipts}) != len(receipts):
        raise CellObservationContractError("repair population duplicates a receipt")
    if any(
        type(observation) is not OwnerBackfillPeakFactObservation
        for observation in observations
    ):
        raise CellObservationContractError(
            "repair population contains a non-owner Backfill observation"
        )
    for observation in observations:
        try:
            validate_owner_backfill_peak_fact_observation(observation)
        except (AttributeError, TypeError, ValueError) as exc:
            raise CellObservationContractError(
                f"repaired observation is not revalidatable: {exc}"
            ) from exc
    if len({observation.work_unit_key for observation in observations}) != len(
        observations
    ):
        raise CellObservationContractError(
            "repair population duplicates a Backfill work unit"
        )
    position_by_id = {position.position_id: position for position in positions}
    receipts_by_position: dict[str, list[ObservationRepairReceipt]] = {}
    for receipt in receipts:
        subject = receipt.subject
        if subject.predecessor_group.processing_request_id != processing_request_id:
            raise CellObservationContractError(
                "repair receipt crossed its processing request"
            )
        matching_positions = tuple(
            position
            for position in positions
            if position.sample.identity.sample_identity_id
            == subject.sample_identity.identity.sample_identity_id
            and subject.predecessor_group == position.group_identity
        )
        if len(matching_positions) != 1:
            raise CellObservationContractError(
                "repair receipt does not bind exactly one coverage position"
            )
        receipts_by_position.setdefault(
            matching_positions[0].position_id,
            [],
        ).append(receipt)
    observation_by_receipt: dict[str, list[OwnerBackfillPeakFactObservation]] = {}
    for observation in observations:
        matching_receipts = tuple(
            receipt
            for receipt in receipts
            if observation.logical_request == receipt.subject.logical_request
            and observation.request_identity == receipt.subject.request_identity
            and observation.cell.group_hypothesis_id
            == receipt.subject.predecessor_group.group_hypothesis_id
            and observation.cell.sample_stem == receipt.sample_stem
        )
        if len(matching_receipts) != 1:
            raise CellObservationContractError(
                "repaired observation does not bind exactly one repair receipt"
            )
        receipt = matching_receipts[0]
        if receipt.outcome != "repaired":
            raise CellObservationContractError(
                "a no-repair receipt cannot carry repaired observation facts"
            )
        observation_by_receipt.setdefault(receipt.receipt_id, []).append(observation)
    for receipt in receipts:
        matched_observations = observation_by_receipt.get(receipt.receipt_id, ())
        if receipt.outcome == "repaired" and len(matched_observations) != 1:
            raise CellObservationContractError(
                "repaired receipt requires exactly one live Backfill observation"
            )
        if receipt.outcome == "no_repair" and matched_observations:
            raise CellObservationContractError(
                "no-repair receipt carries contradictory repaired observation facts"
            )
    if any(position_id not in position_by_id for position_id in receipts_by_position):
        raise CellObservationContractError(
            "repair receipt references an extra position"
        )
    # ``final_population`` is consumed here to make the owner boundary explicit:
    # a repair is only accepted as evidence for a CrossSample successor that is
    # actually present in the handoff; no successor is minted or inferred here.
    if not final_population:
        raise CellObservationContractError("handoff final population is empty")
    return (
        {
            key: tuple(sorted(value, key=lambda item: item.receipt_id))
            for key, value in receipts_by_position.items()
        },
        {
            key: tuple(sorted(value, key=lambda item: item.work_unit_key))
            for key, value in observation_by_receipt.items()
        },
    )


def _validate_handoff_repair_closure(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    positions: tuple[OwnerBackfillPlanningPosition, ...],
    repair_receipts: Sequence[ObservationRepairReceipt],
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation],
    receipt_by_position: dict[str, tuple[ObservationRepairReceipt, ...]],
    observation_by_receipt: dict[str, tuple[OwnerBackfillPeakFactObservation, ...]],
) -> None:
    """Require an applicable final receipt's complete repair provenance.

    CrossSample's final receipt carries the one repair transaction that closed
    the handoff.  Supplied repair evidence must therefore belong to that exact
    receipt object; a caller cannot add a stale transaction, omit an applicable
    repaired receipt, or replace it with an equal-looking receipt.
    """

    final_receipt = handoff.final_receipt.repair_receipt
    if final_receipt is None:
        if repair_receipts or repaired_observations:
            raise CellObservationContractError(
                "receipt-free terminal cannot carry repair evidence"
            )
        return
    if any(receipt is not final_receipt for receipt in repair_receipts):
        raise CellObservationContractError(
            "repair evidence does not match the exact final repair receipt"
        )
    subject = final_receipt.subject
    sample_identity_id = subject.sample_identity.identity.sample_identity_id
    applicable_positions = tuple(
        position
        for position in positions
        if position.sample.identity.sample_identity_id == sample_identity_id
        and position.group_identity == subject.predecessor_group
    )
    if final_receipt.outcome != "repaired" or not applicable_positions:
        return
    if len(applicable_positions) != 1:
        raise CellObservationContractError(
            "handoff final repair receipt binds multiple coverage positions"
        )
    exact_receipts = tuple(
        receipt for receipt in repair_receipts if receipt is final_receipt
    )
    if len(exact_receipts) != 1:
        raise CellObservationContractError(
            "applicable repaired handoff requires its exact final repair receipt"
        )
    position_id = applicable_positions[0].position_id
    position_receipts = receipt_by_position.get(position_id, ())
    if not any(receipt is final_receipt for receipt in position_receipts):
        raise CellObservationContractError(
            "final repair receipt is not retained on its exact coverage position"
        )
    live_observations = observation_by_receipt.get(final_receipt.receipt_id, ())
    if len(live_observations) != 1 or not any(
        observation is live_observations[0] for observation in repaired_observations
    ):
        raise CellObservationContractError(
            "applicable repaired handoff requires its exact live observation"
        )


def _associations_for_final_key(
    associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    *,
    group: CrossSampleGroupIdentity,
    sample: ResolvedCanonicalSampleIdentity,
) -> tuple[CrossSampleCoveragePositionAssociation, ...]:
    sample_id = sample.identity.sample_identity_id
    result = tuple(
        association
        for association in associations
        if association.final_successor is group
        and association.position.sample is sample
    )
    if any(
        association.final_successor != group
        or association.position.sample.identity.sample_identity_id != sample_id
        for association in result
    ):
        raise CellObservationContractError(
            "position association crosses the exact final Group x sample key"
        )
    return result


def _canonical_terminal_subset(
    associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    terminals_by_position: dict[str, tuple[OwnerBackfillAttemptTerminal, ...]],
) -> tuple[OwnerBackfillAttemptTerminal, ...]:
    position_rank = {
        association.position.position_id: index
        for index, association in enumerate(associations)
    }
    terminals = tuple(
        terminal
        for association in associations
        for terminal in terminals_by_position.get(association.position.position_id, ())
    )
    return tuple(
        sorted(
            terminals,
            key=lambda item: (
                position_rank.get(item.position_id, len(position_rank)),
                item.candidate_phase,
                item.candidate_index,
                item.logical_request.logical_request_id,
                item.request_identity.request_identity_id,
                item.terminal_id,
            ),
        )
    )


def _derive_lane_evidence_state(
    *,
    association: CrossSampleCoveragePositionAssociation,
    source_cell: CellObservationRef,
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    final_group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    processing_request_id: str,
) -> FinalCellEvidenceState:
    _validate_source_cell(source_cell, processing_request_id=processing_request_id)
    source_identity = _source_group_identity(source_cell)
    if source_identity != association.position.group_identity:
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact predecessor group identity"
        )
    if (
        source_cell.content.canonical_sample_identity_id
        != association.position.sample.identity.sample_identity_id
        or association.position.sample is not canonical_sample
        or association.final_successor is not final_group
    ):
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact canonical sample"
        )
    source_member = _member_for_sample(
        source_identity,
        association.position.sample.identity.sample_identity_id,
    )
    final_member = _member_for_sample(
        final_group,
        canonical_sample.identity.sample_identity_id,
    )
    if source_member is not None and source_member not in final_group.member_subjects:
        raise CellObservationContractError(
            "CrossSample successor dropped the exact source member evidence"
        )
    if len(repair_receipts) > 1:
        raise CellObservationContractError(
            "one exact predecessor position has multiple repair receipts"
        )
    if len(repaired_observations) > 1:
        raise CellObservationContractError(
            "one exact repair receipt has multiple live observations"
        )
    assessment = source_cell.method_assessment
    technical_state = assessment.content.technical_state
    method_result = assessment.content.method_result
    if source_cell.content.outside_group is not None:
        if (
            source_member is not None
            or source_cell.peak_observation is not None
            or source_cell.peak_hypothesis is not None
            or repair_receipts
            or repaired_observations
            or coverage_terminals
            or association.position.planning_state != "not_planned"
            or association.position.planning_reason != "local_owner_not_supersedable"
        ):
            raise CellObservationContractError(
                "outside-group lane cannot supply local observation or repair evidence"
            )
        # A merged sibling may supply a direct lane; this lane adds no value.
        return "not_searched"
    no_request_proof = validate_owner_backfill_conservative_no_request_position(
        association.position, source_cell=source_cell
    )
    if no_request_proof is not None:
        if (
            coverage_terminals
            or repair_receipts
            or repaired_observations
            or final_member is not None
        ):
            raise CellObservationContractError(
                "conservative no-request lane cannot carry physical or repair evidence"
            )
        return (
            "not_searched"
            if no_request_proof.variant == "threshold_not_met"
            else "unavailable"
        )
    if final_member is not None and method_result in {"HOLD", "REJECT"}:
        raise CellObservationContractError(
            "final CrossSample member cannot carry a HOLD or REJECT method lane"
        )
    if repair_receipts:
        receipt = repair_receipts[0]
        if receipt.outcome == "repaired":
            if not repaired_observations:
                raise CellObservationContractError(
                    "repaired receipt lacks its exact live observation"
                )
            if final_member is None:
                raise CellObservationContractError(
                    "repair evidence cannot create CrossSample membership"
                )
            _repaired_member_integration(
                member=final_member,
                group=final_group,
                canonical_sample=canonical_sample,
                repair_receipt=receipt,
                repaired_observation=repaired_observations[0],
            )
            if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
                raise CellObservationContractError(
                    "one evidence lane cannot claim direct and repaired observation"
                )
            if method_result == "REJECT":
                raise CellObservationContractError(
                    "repaired evidence cannot override a rejected method lane"
                )
            return "repaired_observation"
        if repaired_observations:
            raise CellObservationContractError(
                "no-repair lane contains contradictory live observation facts"
            )
    retained_local_owner = (
        association.position.planning_state == "not_planned"
        and association.position.planning_reason == "retained_local_owner"
    )
    if (
        association.position.planning_state == "not_planned"
        and not retained_local_owner
    ):
        if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
            raise CellObservationContractError(
                "not-planned lane cannot carry direct observation evidence"
            )
        if technical_state in {"unsupported", "failed"}:
            return "unavailable"
        if technical_state != "not_evaluated" or method_result is not None:
            raise CellObservationContractError(
                "not-planned coverage position crossed an evaluated source cell"
            )
        return "not_searched"
    if not retained_local_owner and not coverage_terminals:
        raise CellObservationContractError(
            "planned coverage position has no exact retained terminal"
        )
    coverage_unavailable = any(
        terminal.candidate_phase
        in {"primary_fallback", "validation_unavailable", "validation_fallback"}
        or (
            terminal.physical_evaluation is not None
            and terminal.physical_evaluation.state in {"unavailable", "failed"}
        )
        for terminal in coverage_terminals
    )
    if technical_state in {"unsupported", "failed"}:
        if source_cell.peak_observation is not None or source_cell.peak_hypothesis:
            raise CellObservationContractError(
                "unavailable lane cannot carry direct observation evidence"
            )
        return "unavailable"
    if technical_state == "not_evaluated":
        if not coverage_unavailable:
            raise CellObservationContractError(
                "evaluated Backfill coverage contradicts a not-evaluated source cell"
            )
        return "unavailable"
    if method_result == "SUPPORT":
        if final_member is None:
            raise CellObservationContractError(
                "SUPPORT evidence has no exact final CrossSample member"
            )
        if source_cell.peak_observation is None or source_cell.peak_hypothesis is None:
            raise CellObservationContractError(
                "SUPPORT source cell lacks its exact observation and hypothesis"
            )
        _direct_member_integration(
            member=final_member,
            processing_request_id=processing_request_id,
            canonical_sample=canonical_sample,
            terminal_variant="observed",
            terminal_hypothesis=source_cell.peak_hypothesis,
            method_assessment=assessment,
            peak_observation=source_cell.peak_observation,
            peak_hypothesis=source_cell.peak_hypothesis,
        )
        return "direct_observation"
    if method_result == "REJECT":
        if source_cell.peak_hypothesis is not None:
            raise CellObservationContractError(
                "REJECT lane cannot carry a SUPPORT hypothesis"
            )
        return "rejected_observation"
    if method_result != "HOLD":
        raise CellObservationContractError(
            "evaluated source cell requires SUPPORT, HOLD, or REJECT"
        )
    if source_cell.peak_hypothesis is not None:
        raise CellObservationContractError(
            "HOLD lane cannot carry a SUPPORT hypothesis"
        )
    if not assessment.candidate_peaks and source_cell.peak_observation is None:
        return "searched_no_observation"
    if (
        source_cell.peak_observation is not None
        and source_cell.peak_observation not in assessment.candidate_peaks
    ):
        raise CellObservationContractError(
            "unresolved source evidence is outside the exact candidate universe"
        )
    return "unresolved"


def _member_for_sample(
    group: CrossSampleGroupIdentity,
    sample_identity_id: str,
) -> CrossSampleGroupMemberSubject | None:
    matches = tuple(
        member
        for member in group.member_subjects
        if member.canonical_sample_identity_id == sample_identity_id
    )
    if len(matches) > 1:
        raise CellObservationContractError(
            "CrossSample group has duplicate members for one canonical sample"
        )
    return matches[0] if matches else None


def _mint_final_group_cell_observation(
    *,
    processing_request_id: str,
    handoff: CrossSampleAssociationLineageHandoff,
    group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    predecessor_cells: tuple[CellObservationRef, ...],
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
) -> FinalGroupCellObservation:
    basis = _final_closure_basis(
        processing_request_id=processing_request_id,
        handoff=handoff,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        evidence_states=evidence_states,
    )
    return FinalGroupCellObservation(
        cell_observation_id=typed_identity_digest(
            _FINAL_CELL_OBSERVATION_ID_PREFIX,
            basis,
        ),
        processing_request_id=processing_request_id,
        group=group,
        canonical_sample=canonical_sample,
        position_associations=position_associations,
        predecessor_cells=predecessor_cells,
        coverage_terminals=coverage_terminals,
        repair_receipts=repair_receipts,
        repaired_observations=repaired_observations,
        evidence_states=evidence_states,
        basis_bytes=basis,
        _mint_token=_FINAL_CELL_MINT_TOKEN,
    )


def _validate_final_group_cell_observation(
    cell: FinalGroupCellObservation,
) -> None:
    request_id = _processing_request_id(cell.processing_request_id)
    _validate_typed_digest_id(
        cell.cell_observation_id,
        prefix=_FINAL_CELL_OBSERVATION_ID_PREFIX,
        field="cell_observation_id",
    )
    if type(cell.group) is not CrossSampleGroupIdentity:
        raise CellObservationContractError("final cell group is not owner-backed")
    try:
        validate_cross_sample_group_population((cell.group,))
    except (TypeError, ValueError) as exc:
        raise CellObservationContractError(str(exc)) from exc
    if cell.group.processing_request_id != request_id:
        raise CellObservationContractError(
            "final cell group crossed the processing request"
        )
    if type(cell.canonical_sample) is not ResolvedCanonicalSampleIdentity:
        raise CellObservationContractError("final cell sample is not owner-resolved")
    if not isinstance(cell.position_associations, tuple):
        raise CellObservationContractError(
            "final cell position associations must be an immutable tuple"
        )
    if any(
        type(item) is not CrossSampleCoveragePositionAssociation
        for item in cell.position_associations
    ):
        raise CellObservationContractError(
            "final cell contains a non-owner position association"
        )
    if cell.position_associations != tuple(
        sorted(cell.position_associations, key=lambda item: item.position.position_id)
    ):
        raise CellObservationContractError(
            "final cell position associations are not canonical-sorted"
        )
    position_ids = tuple(
        item.position.position_id for item in cell.position_associations
    )
    if len(position_ids) != len(set(position_ids)):
        raise CellObservationContractError(
            "final cell duplicates a position association"
        )
    if not isinstance(cell.predecessor_cells, tuple) or len(
        cell.predecessor_cells
    ) != len(cell.position_associations):
        raise CellObservationContractError(
            "final cell source-cell lanes do not match position associations"
        )
    for association, source_cell in zip(
        cell.position_associations,
        cell.predecessor_cells,
        strict=True,
    ):
        _validate_source_cell(source_cell, processing_request_id=request_id)
        if source_cell.content.canonical_sample_identity_id != (
            association.position.sample.identity.sample_identity_id
        ):
            raise CellObservationContractError(
                "final cell source lane crossed its canonical sample"
            )
        if _source_group_identity(source_cell) != association.position.group_identity:
            raise CellObservationContractError(
                "final cell source lane crossed its predecessor group identity"
            )
        if association.final_successor != cell.group:
            raise CellObservationContractError(
                "final cell position association crossed its final group"
            )
        if association.position.sample.identity.sample_identity_id != (
            cell.canonical_sample.identity.sample_identity_id
        ):
            raise CellObservationContractError(
                "final cell position association crossed its canonical sample"
            )
    if not isinstance(cell.coverage_terminals, tuple):
        raise CellObservationContractError(
            "final cell coverage terminals must be an immutable tuple"
        )
    if any(
        type(item) is not OwnerBackfillAttemptTerminal
        for item in cell.coverage_terminals
    ):
        raise CellObservationContractError(
            "final cell contains a non-owner coverage terminal"
        )
    if any(item.position_id not in position_ids for item in cell.coverage_terminals):
        raise CellObservationContractError(
            "final cell coverage terminal crossed its retained position lanes"
        )
    if not isinstance(cell.repair_receipts, tuple) or any(
        type(item) is not ObservationRepairReceipt for item in cell.repair_receipts
    ):
        raise CellObservationContractError(
            "final cell repair receipts must be an immutable owner tuple"
        )
    if not isinstance(cell.repaired_observations, tuple) or any(
        type(item) is not OwnerBackfillPeakFactObservation
        for item in cell.repaired_observations
    ):
        raise CellObservationContractError(
            "final cell repaired observations must be an immutable owner tuple"
        )
    if not isinstance(cell.evidence_states, tuple) or not cell.evidence_states:
        raise CellObservationContractError(
            "final cell must retain one state per lane, or explicit zero-lane state"
        )
    if len(cell.evidence_states) != max(1, len(cell.position_associations)):
        raise CellObservationContractError(
            "final cell evidence states do not match retained lane cardinality"
        )
    if any(
        state
        not in {
            "direct_observation",
            "repaired_observation",
            "unresolved",
            "rejected_observation",
            "searched_no_observation",
            "unavailable",
            "not_searched",
        }
        for state in cell.evidence_states
    ):
        raise CellObservationContractError("final cell evidence state is not closed")
    expected_basis = _final_closure_basis(
        processing_request_id=request_id,
        handoff_id=_handoff_id_for_basis(cell),
        group=cell.group,
        canonical_sample=cell.canonical_sample,
        position_associations=cell.position_associations,
        predecessor_cells=cell.predecessor_cells,
        coverage_terminals=cell.coverage_terminals,
        repair_receipts=cell.repair_receipts,
        repaired_observations=cell.repaired_observations,
        evidence_states=cell.evidence_states,
    )
    if cell.basis_bytes != expected_basis:
        raise CellObservationContractError(
            "final cell basis bytes do not bind its complete owner content"
        )
    if cell.cell_observation_id != typed_identity_digest(
        _FINAL_CELL_OBSERVATION_ID_PREFIX,
        expected_basis,
    ):
        raise CellObservationContractError(
            "final cell identity does not bind its complete owner content"
        )


def _handoff_id_for_basis(cell: FinalGroupCellObservation) -> str:
    # The handoff ID is encoded in the closure basis.  Revalidation extracts it
    # without trusting any caller-provided scalar mapping.
    marker = b'"handoff_id":"'
    start = cell.basis_bytes.find(marker)
    if start < 0:
        raise CellObservationContractError("final cell basis omits exact handoff")
    start += len(marker)
    end = cell.basis_bytes.find(b'"', start)
    if end < 0:
        raise CellObservationContractError("final cell basis has malformed handoff")
    return cell.basis_bytes[start:end].decode("utf-8")


def _final_closure_basis(
    *,
    processing_request_id: str,
    handoff: CrossSampleAssociationLineageHandoff | None = None,
    handoff_id: str | None = None,
    group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    predecessor_cells: tuple[CellObservationRef, ...],
    coverage_terminals: tuple[OwnerBackfillAttemptTerminal, ...],
    repair_receipts: tuple[ObservationRepairReceipt, ...],
    repaired_observations: tuple[OwnerBackfillPeakFactObservation, ...],
    evidence_states: tuple[FinalCellEvidenceState, ...],
) -> bytes:
    resolved_handoff_id = handoff_id or (
        handoff.handoff_id if handoff is not None else None
    )
    if resolved_handoff_id is None:
        raise CellObservationContractError("final cell basis requires exact handoff")
    payload = {
        "schema_version": "final_group_cell_observation_v2",
        "processing_request_id": processing_request_id,
        "handoff_id": resolved_handoff_id,
        "group": {
            "group_hypothesis_id": group.group_hypothesis_id,
            "processing_request_id": group.processing_request_id,
            "member_subject_ids": [
                member.member_subject_id for member in group.member_subjects
            ],
            "basis_sha256": hashlib.sha256(group.basis_bytes).hexdigest(),
            "disposition": group.disposition,
        },
        "canonical_sample_identity_id": canonical_sample.identity.sample_identity_id,
        "position_associations": [
            {
                "position_id": item.position.position_id,
                "predecessor_group_hypothesis_id": (
                    item.position.group_identity.group_hypothesis_id
                ),
                "predecessor_group_basis_sha256": hashlib.sha256(
                    item.position.group_identity.basis_bytes
                ).hexdigest(),
                "sample_identity_id": item.position.sample.identity.sample_identity_id,
                "final_successor_group_hypothesis_id": (
                    item.final_successor.group_hypothesis_id
                ),
                "resolution": item.resolution,
            }
            for item in position_associations
        ],
        "predecessor_cells": [
            {
                "artifact_id": cell.artifact.evidence_artifact_id,
                "content_sha256": hashlib.sha256(
                    canonical_cell_observation_bytes(cell.content)
                ).hexdigest(),
                "group_hypothesis_id": cell.group_context.group.group_hypothesis_id,
                "sample_identity_id": cell.content.canonical_sample_identity_id,
                "method_assessment_artifact_content_id": (
                    cell.method_assessment.artifact.content_identity.artifact_content_id
                ),
                "peak_observation_content_id": (
                    None
                    if cell.peak_observation is None
                    else cell.peak_observation.content.peak_observation_content_id
                ),
                "peak_hypothesis_id": (
                    None
                    if cell.peak_hypothesis is None
                    else cell.peak_hypothesis.hypothesis_id
                ),
            }
            for cell in predecessor_cells
        ],
        "coverage_terminals": [item.terminal_id for item in coverage_terminals],
        "repair_receipts": [item.receipt_id for item in repair_receipts],
        "repaired_observations": [
            {
                "work_unit_key": item.work_unit_key,
                "fact_content_id": (
                    None
                    if item.physical_evaluation is None
                    or item.physical_evaluation.facts is None
                    else item.physical_evaluation.facts.fact_content_id
                ),
            }
            for item in repaired_observations
        ],
        "evidence_states": list(evidence_states),
    }
    return canonical_identity_basis_bytes(payload)


def _final_cell_sort_key(
    cell: FinalGroupCellObservation,
) -> tuple[str, str]:
    return cell.group_hypothesis_id, cell.canonical_sample_identity_id


def _validate_final_cell_handoff_refs(
    cell: FinalGroupCellObservation,
    *,
    handoff: CrossSampleAssociationLineageHandoff,
) -> None:
    groups = {group.group_hypothesis_id: group for group in handoff.final_population}
    samples = {
        sample.identity.sample_identity_id: sample
        for sample in handoff.coverage.epoch.samples
    }
    group = groups.get(cell.group_hypothesis_id)
    sample = samples.get(cell.canonical_sample_identity_id)
    if group is None or sample is None:
        raise CellObservationContractError(
            "final cell key is outside the exact handoff population"
        )
    if cell.group is not group or cell.canonical_sample is not sample:
        raise CellObservationContractError(
            "final cell substituted an equivalent handoff group or sample"
        )
    handoff_associations = handoff.position_associations
    handoff_terminals = handoff.coverage.terminals
    for association in cell.position_associations:
        if not any(association is item for item in handoff_associations):
            raise CellObservationContractError(
                "final cell position association is not the exact handoff object"
            )
    for terminal in cell.coverage_terminals:
        if not any(terminal is item for item in handoff_terminals):
            raise CellObservationContractError(
                "final cell terminal is not the exact coverage object"
            )


def _require_same_refs(
    actual: FinalGroupCellObservation,
    expected: FinalGroupCellObservation,
) -> None:
    if (
        actual.group is not expected.group
        or actual.canonical_sample is not expected.canonical_sample
    ):
        raise CellObservationContractError(
            "final cell did not retain exact group/sample owner references"
        )
    for actual_association, expected_association in zip(
        actual.position_associations,
        expected.position_associations,
        strict=True,
    ):
        if actual_association is not expected_association:
            raise CellObservationContractError(
                "final cell did not retain exact position association references"
            )
    for actual_cell, expected_cell in zip(
        actual.predecessor_cells,
        expected.predecessor_cells,
        strict=True,
    ):
        if actual_cell is not expected_cell:
            raise CellObservationContractError(
                "final cell did not retain exact predecessor cell references"
            )
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.coverage_terminals,
            expected.coverage_terminals,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact coverage terminal references"
        )
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.repair_receipts,
            expected.repair_receipts,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact repair receipt references"
        )
    if any(
        actual_ref is not expected_ref
        for actual_ref, expected_ref in zip(
            actual.repaired_observations,
            expected.repaired_observations,
            strict=True,
        )
    ):
        raise CellObservationContractError(
            "final cell did not retain exact repaired observation references"
        )


__all__ = [
    "ARTIFACT_SEMANTIC_ROLE",
    "CidNlCellGroupContext",
    "CidNlCellGroupSupport",
    "CellObservationContent",
    "CellObservationContractError",
    "CellObservationRef",
    "FinalCellEvidenceAggregate",
    "FinalCellEvidenceState",
    "FinalGroupCellObservation",
    "OutsideGroupCellRelation",
    "OUTSIDE_GROUP_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "TerminalObservationVariant",
    "canonical_cell_observation_bytes",
    "compose_final_group_cell_observation_population",
    "compose_cid_nl_predecessor_cells",
    "mint_cell_observation_ref",
    "validate_final_group_cell_observation_population",
]


def compose_cid_nl_predecessor_cells(
    *,
    processing_owners: ProcessingOwnerReferences,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    recovery_assessments_by_group: Mapping[tuple[str, str], CidNlMethodAssessmentRef]
    | None = None,
) -> tuple[CellObservationRef, ...]:
    """Attach original assessments to the existing ModeTrack predecessor groups."""
    sample_assessments = tuple(
        sorted(
            dict.fromkeys(sample_assessments),
            key=lambda pair: pair[0].identity.sample_identity_id,
        )
    )
    supports = tuple(
        CidNlCellGroupSupport(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
        for sample, assessment in sample_assessments
        for hypothesis in compose_peak_hypotheses_from_assessment_universe(
            processing_owners=processing_owners,
            sample=sample,
            assessment=assessment,
        )
        if any(hypothesis.hypothesis_id in group.owner_ids for group in groups)
    )
    contexts = tuple(
        CidNlCellGroupContext(
            group=group,
            supports=tuple(
                support
                for support in supports
                if support.hypothesis.hypothesis_id in group.owner_ids
            ),
            config=alignment_config,
            processing_attempt=processing_owners.attempt,
        )
        for group in groups
    )
    assessments_by_sample: dict[str, list[CidNlMethodAssessmentRef]] = {}
    samples = {}
    for sample, assessment in sample_assessments:
        sample_id = sample.identity.sample_identity_id
        samples[sample_id] = sample
        refs = assessments_by_sample.setdefault(sample_id, [])
        if assessment not in refs:
            refs.append(assessment)
    cells = []
    for context in contexts:
        for sample_id, refs in assessments_by_sample.items():
            sample = samples[sample_id]
            local_refs = tuple(
                support.assessment
                for support in context.supports
                if support.sample.identity.sample_identity_id == sample_id
            )
            recovered = (recovery_assessments_by_group or {}).get(
                (context.group.group_hypothesis_id, sample_id)
            )
            if recovered is not None and not any(recovered is ref for ref in refs):
                raise CellObservationContractError(
                    "group recovery is outside the assessment population"
                )
            assessment = (
                local_refs[0]
                if local_refs
                else recovered
                if recovered is not None
                else refs[0]
            )
            local = tuple(
                support
                for support in context.supports
                if support.sample is sample and support.assessment is assessment
            )
            outside_context = None
            observation = None
            hypothesis = None
            if local:
                (support,) = local
                hypothesis = support.hypothesis
                (observation,) = hypothesis.peak_observation_refs
            elif assessment.content.method_result == "SUPPORT":
                siblings = tuple(
                    sibling
                    for sibling in contexts
                    if sibling is not context
                    and any(
                        support.sample is sample and support.assessment is assessment
                        for support in sibling.supports
                    )
                )
                if not siblings:
                    raise ValueError(
                        "outside-group cell requires one exact sibling SUPPORT context"
                    )
                outside_context = min(
                    siblings, key=lambda sibling: sibling.group.group_hypothesis_id
                )
            elif (
                assessment.content.method_result == "HOLD"
                and len(assessment.candidate_peaks) == 1
            ):
                observation = assessment.candidate_peaks[0]
            cells.append(
                mint_cell_observation_ref(
                    processing_owners=processing_owners,
                    sample=sample,
                    group_context=context,
                    method_assessment=assessment,
                    peak_observation=observation,
                    peak_hypothesis=hypothesis,
                    outside_group_context=outside_context,
                )
            )
    return tuple(cells)
