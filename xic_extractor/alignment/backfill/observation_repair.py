"""One bounded Backfill transaction that closes repaired observation evidence."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestIdentity,
)
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    resolve_canonical_sample_identity,
)

ObservationRepairOutcome: TypeAlias = Literal["no_repair", "repaired"]

_SUBJECT_PREFIX = "xic.observation-repair-subject.v1:"
_RECEIPT_PREFIX = "xic.observation-repair-receipt.v1:"
_SUBJECT_MINT_TOKEN = object()
_RECEIPT_MINT_TOKEN = object()


class ObservationRepairContractError(ValueError):
    """Raised when ObservationRepair input cannot be closed exactly."""


@dataclass(frozen=True, slots=True)
class ObservationRepairSubject:
    """Owner-minted subject for exactly one repair epoch."""

    subject_id: str
    request_identity: OwnerBackfillRequestIdentity
    logical_request: OwnerBackfillLogicalRequestMember
    predecessor_group: CrossSampleGroupIdentity
    member_subject: CrossSampleGroupMemberSubject
    sample_identity: ResolvedCanonicalSampleIdentity
    successor_application: CrossSampleSuccessorApplication
    repair_epoch: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _SUBJECT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair subject must be minted by Backfill"
            )
        _validate_subject(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairEvidence:
    """Process-safe evidence facts; this record carries no selection authority."""

    logical_request_id: str
    request_identity_id: str
    group_hypothesis_id: str
    member_subject_id: str
    sample_stem: str
    work_unit_key: str
    candidate_index: int
    candidate_phase: str
    acquisition_source_id: str
    selection_subject_id: str
    physical_question_id: str
    physical_answer_id: str
    physical_segment_id: str
    fact_content_id: str
    selected_integration_authority_content_id: str
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    height: float
    scan_support_count: int
    area_counts_seconds: float
    area_source: str


@dataclass(frozen=True, slots=True)
class ObservationRepairReceipt:
    """Terminal content-bound handoff from Backfill to its next owner."""

    receipt_id: str
    subject: ObservationRepairSubject
    subject_id: str
    repair_epoch: int
    outcome: ObservationRepairOutcome
    sample_stem: str
    evidence: ObservationRepairEvidence | None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RECEIPT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair receipt must be minted by Backfill"
            )
        if type(self.subject) is not ObservationRepairSubject:
            raise TypeError("repair receipt subject must be owner-minted")
        _validate_subject(self.subject)
        if self.subject_id != self.subject.subject_id:
            raise ObservationRepairContractError(
                "repair receipt crosses its exact subject"
            )
        if self.repair_epoch != 1:
            raise ObservationRepairContractError("repair receipt exceeds one epoch")
        if self.repair_epoch != self.subject.repair_epoch:
            raise ObservationRepairContractError("repair receipt crosses subject epoch")
        if self.sample_stem != self.subject.request_identity.sample_stem:
            raise ObservationRepairContractError(
                "repair receipt crosses subject sample"
            )
        expected_outcome = "repaired" if self.evidence is not None else "no_repair"
        if self.outcome != expected_outcome:
            raise ObservationRepairContractError(
                "repair receipt outcome crosses its terminal evidence"
            )
        expected = _receipt_id(
            subject_id=self.subject_id,
            repair_epoch=self.repair_epoch,
            outcome=self.outcome,
            sample_stem=self.sample_stem,
            evidence=self.evidence,
        )
        if self.receipt_id != expected:
            raise ObservationRepairContractError(
                "repair receipt id is not bound to its complete content"
            )

    @property
    def evidence_authority(self) -> str:
        return "observation_evidence_only"

    @property
    def membership_authority(self) -> str:
        return "none"

    @property
    def value_authority(self) -> str:
        return "none"

    @property
    def matrix_authority(self) -> str:
        return "none"

    @property
    def activation_authority(self) -> str:
        return "none"

    @property
    def writer_authority(self) -> str:
        return "none"


def mint_observation_repair_subject(
    *,
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    member_subject: CrossSampleGroupMemberSubject,
    sample_identity: ResolvedCanonicalSampleIdentity,
    successor_application: CrossSampleSuccessorApplication,
    repair_epoch: int = 1,
) -> ObservationRepairSubject:
    """Mint the only repair subject Backfill is allowed to close."""

    if type(request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("request identity must be owner-minted")
    if type(logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("logical request must be owner-minted")
    if type(predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("predecessor group must be owner-minted")
    if type(member_subject) is not CrossSampleGroupMemberSubject:
        raise TypeError("member subject must be owner-minted")
    if type(sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("sample identity must be owner-resolved")
    if type(successor_application) is not CrossSampleSuccessorApplication:
        raise TypeError("successor application must be CrossSample-minted")
    if type(repair_epoch) is not int or repair_epoch != 1:
        raise ObservationRepairContractError("repair epoch must be exactly 1")
    subject_id = _subject_id(
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=predecessor_group,
        member_subject=member_subject,
        sample_identity=sample_identity,
        successor_application=successor_application,
        repair_epoch=repair_epoch,
    )
    return ObservationRepairSubject(
        subject_id=subject_id,
        request_identity=request_identity,
        logical_request=logical_request,
        predecessor_group=predecessor_group,
        member_subject=member_subject,
        sample_identity=sample_identity,
        successor_application=successor_application,
        repair_epoch=repair_epoch,
        _mint_token=_SUBJECT_MINT_TOKEN,
    )


def close_observation_repair(
    subject: ObservationRepairSubject,
    result: OwnerBackfillSampleResult,
) -> ObservationRepairReceipt:
    """Close one complete owner result as ``no_repair`` or ``repaired``."""

    if type(subject) is not ObservationRepairSubject:
        raise TypeError("subject must be an owner-minted ObservationRepairSubject")
    _validate_subject(subject)
    if type(result) is not OwnerBackfillSampleResult:
        raise TypeError("result must be an OwnerBackfillSampleResult")
    if (
        type(result.peak_fact_observations) is not tuple
        or type(result.prepared_peak_facts) is not tuple
    ):
        raise ObservationRepairContractError(
            "repair result evidence containers must be immutable tuples"
        )
    if (
        isinstance(result.sample_index, bool)
        or not isinstance(result.sample_index, int)
        or result.sample_index < 0
    ):
        raise ObservationRepairContractError(
            "repair result sample index must be non-negative"
        )
    if result.sample_stem != subject.request_identity.sample_stem:
        raise ObservationRepairContractError("repair result crosses sample identity")

    if result.prepared_peak_facts:
        raise ObservationRepairContractError(
            "prepared Backfill facts lack exact logical request closure"
        )
    inputs = result.peak_fact_observations
    exact_live_seen = False
    exact_work_units: set[str] = set()
    normalized_list: list[ObservationRepairEvidence] = []
    for item in inputs:
        normalized = _normalize_live_evidence(subject, item)
        if (
            item.logical_request == subject.logical_request
            and item.request_identity == subject.request_identity
            and item.cell.group_hypothesis_id
            == subject.predecessor_group.group_hypothesis_id
            and item.cell.sample_stem == subject.request_identity.sample_stem
        ):
            exact_live_seen = True
            if item.work_unit_key in exact_work_units:
                raise ObservationRepairContractError(
                    "repair result contains duplicate evidence"
                )
            exact_work_units.add(item.work_unit_key)
        if normalized is not None:
            normalized_list.append(normalized)
    if not normalized_list:
        if not exact_live_seen:
            raise ObservationRepairContractError(
                "no_repair requires an exact live evaluated observation"
            )
        evidence = None
    elif len(normalized_list) != 1:
        content_ids = tuple(item.fact_content_id for item in normalized_list)
        if len(set(content_ids)) != len(content_ids):
            raise ObservationRepairContractError(
                "repair result contains duplicate evidence"
            )
        raise ObservationRepairContractError(
            "repair result must contain exactly one complete selected evidence"
        )
    else:
        evidence = normalized_list[0]

    outcome: ObservationRepairOutcome = (
        "repaired" if evidence is not None else "no_repair"
    )
    receipt_id = _receipt_id(
        subject_id=subject.subject_id,
        repair_epoch=subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
    )
    return ObservationRepairReceipt(
        receipt_id=receipt_id,
        subject=subject,
        subject_id=subject.subject_id,
        repair_epoch=subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
        _mint_token=_RECEIPT_MINT_TOKEN,
    )


def _validate_subject(subject: ObservationRepairSubject) -> None:
    if type(subject.request_identity) is not OwnerBackfillRequestIdentity:
        raise TypeError("request identity must be owner-minted")
    if type(subject.logical_request) is not OwnerBackfillLogicalRequestMember:
        raise TypeError("logical request must be owner-minted")
    if type(subject.predecessor_group) is not CrossSampleGroupIdentity:
        raise TypeError("predecessor group must be owner-minted")
    if type(subject.member_subject) is not CrossSampleGroupMemberSubject:
        raise TypeError("member subject must be owner-minted")
    if type(subject.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise TypeError("sample identity must be owner-resolved")
    if type(subject.successor_application) is not CrossSampleSuccessorApplication:
        raise TypeError("successor application must be CrossSample-minted")
    replayed_application = CrossSampleSuccessorApplication(
        status=subject.successor_application.status,
        predecessors=subject.successor_application.predecessors,
        successors=subject.successor_application.successors,
        lineage=subject.successor_application.lineage,
        reason=subject.successor_application.reason,
    )
    if replayed_application != subject.successor_application:
        raise ObservationRepairContractError(
            "repair subject successor application is not canonical"
        )
    resolved_sample = resolve_canonical_sample_identity(
        subject.sample_identity.snapshot,
        subject.sample_identity.lookup_token,
        expected_identity=subject.sample_identity.identity,
    )
    if resolved_sample != subject.sample_identity:
        raise ObservationRepairContractError(
            "repair subject sample resolution is not canonical"
        )
    replayed_group = mint_cross_sample_group_identity(
        processing_request_id=subject.predecessor_group.processing_request_id,
        member_subjects=subject.predecessor_group.member_subjects,
        disposition=subject.predecessor_group.disposition,
    )
    if replayed_group != subject.predecessor_group:
        raise ObservationRepairContractError(
            "repair subject predecessor group is not canonical"
        )
    if type(subject.repair_epoch) is not int or subject.repair_epoch != 1:
        raise ObservationRepairContractError("repair epoch must be exactly 1")
    request_binding = (
        subject.logical_request.item.sample_stem,
        subject.logical_request.item.request,
    )
    if request_binding != (
        subject.request_identity.sample_stem,
        subject.request_identity.request,
    ):
        raise ObservationRepairContractError(
            "repair subject crosses exact request identity"
        )
    if (
        subject.logical_request.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        raise ObservationRepairContractError(
            "repair subject crosses predecessor group identity"
        )
    if subject.member_subject not in subject.predecessor_group.member_subjects:
        raise ObservationRepairContractError(
            "repair subject member is absent from predecessor group"
        )
    if subject.predecessor_group not in subject.successor_application.successors:
        raise ObservationRepairContractError(
            "repair subject group is absent from successor application"
        )
    if (
        subject.sample_identity.identity.sample_identity_id
        != subject.member_subject.canonical_sample_identity_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses canonical sample identity"
        )
    if (
        subject.sample_identity.metadata.raw_stem
        != subject.request_identity.sample_stem
    ):
        raise ObservationRepairContractError(
            "repair subject sample metadata crosses exact Backfill sample"
        )
    if (
        subject.member_subject.processing_request_id
        != subject.predecessor_group.processing_request_id
    ):
        raise ObservationRepairContractError(
            "repair subject member crosses processing request"
        )
    expected = _subject_id(
        request_identity=subject.request_identity,
        logical_request=subject.logical_request,
        predecessor_group=subject.predecessor_group,
        member_subject=subject.member_subject,
        sample_identity=subject.sample_identity,
        successor_application=subject.successor_application,
        repair_epoch=subject.repair_epoch,
    )
    if subject.subject_id != expected:
        raise ObservationRepairContractError(
            "repair subject id is not bound to its complete content"
        )


def _normalize_live_evidence(
    subject: ObservationRepairSubject,
    observation: OwnerBackfillPeakFactObservation,
) -> ObservationRepairEvidence | None:
    if observation.cell.sample_stem != subject.request_identity.sample_stem:
        raise ObservationRepairContractError("repair evidence crosses sample identity")
    if (
        observation.cell.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        return None
    validate_owner_backfill_peak_fact_observation(observation)
    if observation.request_identity != subject.request_identity:
        raise ObservationRepairContractError("repair evidence crosses request identity")
    if observation.logical_request != subject.logical_request:
        return None
    if not observation.selected_for_output:
        if not observation.trace_evaluated or observation.physical_evaluation is None:
            raise ObservationRepairContractError(
                "unselected repair evidence is not a completed evaluation"
            )
        return None
    evaluation = observation.physical_evaluation
    receipt = observation.facts
    if evaluation is None:
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    if evaluation.facts is None and receipt is None:
        if _is_completed_no_signal_evaluation(observation):
            return None
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    if evaluation.facts is None or receipt is None:
        raise ObservationRepairContractError(
            "selected repair evidence is partial or unavailable"
        )
    facts = evaluation.facts
    integration_id = selected_integration_authority_projection(
        receipt.integration
    ).selected_integration_authority_content_id
    return _evidence(
        subject,
        request_identity_id=observation.request_identity.request_identity_id,
        group_hypothesis_id=observation.cell.group_hypothesis_id,
        sample_stem=observation.cell.sample_stem,
        work_unit_key=observation.work_unit_key,
        candidate_index=observation.candidate_index,
        candidate_phase=observation.candidate_phase,
        acquisition_source_id=(
            observation.request_identity.acquisition_source.acquisition_source_id
        ),
        selection_subject_id=evaluation.question.selection_subject_id,
        physical_question_id=facts.physical_question_id,
        physical_answer_id=facts.physical_answer_id,
        physical_segment_id=facts.physical_segment_id,
        fact_content_id=facts.fact_content_id,
        selected_integration_authority_content_id=integration_id,
        rt_left_min=facts.left_rt_min,
        rt_apex_min=facts.apex_rt_min,
        rt_right_min=facts.right_rt_min,
        height=facts.height,
        scan_support_count=facts.scan_support_count,
        area_counts_seconds=facts.area_counts_seconds,
        area_source=facts.area_source,
    )


def _is_completed_no_signal_evaluation(
    observation: OwnerBackfillPeakFactObservation,
) -> bool:
    evaluation = observation.physical_evaluation
    if evaluation is None:
        return False
    return (
        observation.trace_evaluated
        and evaluation.state == "unavailable"
        and evaluation.reason == "no_signal_above_baseline"
        and evaluation.prepared.state == "unavailable"
        and evaluation.prepared.reason == "no_signal_above_baseline"
        and evaluation.segment_universe.state == "unavailable"
        and evaluation.segment_universe.reason == "no_signal_above_baseline"
        and not evaluation.segment_universe.segments
        and evaluation.answer.state == "unavailable"
        and evaluation.answer.reason == "no_signal_above_baseline"
        and evaluation.answer.physical_segment_id is None
        and evaluation.operations == ("trace_evaluation",)
    )


def _evidence(
    subject: ObservationRepairSubject,
    *,
    request_identity_id: str,
    group_hypothesis_id: str,
    sample_stem: str,
    work_unit_key: str,
    candidate_index: int,
    candidate_phase: str,
    acquisition_source_id: str,
    selection_subject_id: str,
    physical_question_id: str,
    physical_answer_id: str,
    physical_segment_id: str,
    fact_content_id: str,
    selected_integration_authority_content_id: str,
    rt_left_min: float,
    rt_apex_min: float,
    rt_right_min: float,
    height: float,
    scan_support_count: int,
    area_counts_seconds: float,
    area_source: str,
) -> ObservationRepairEvidence:
    binding = (
        request_identity_id,
        group_hypothesis_id,
        sample_stem,
        acquisition_source_id,
    )
    expected = (
        subject.request_identity.request_identity_id,
        subject.predecessor_group.group_hypothesis_id,
        subject.request_identity.sample_stem,
        subject.request_identity.acquisition_source.acquisition_source_id,
    )
    if binding != expected:
        if (
            request_identity_id != expected[0]
            or acquisition_source_id != expected[3]
        ):
            raise ObservationRepairContractError(
                "repair evidence crosses request identity"
            )
        if group_hypothesis_id != expected[1]:
            raise ObservationRepairContractError(
                "repair evidence crosses group identity"
            )
        raise ObservationRepairContractError("repair evidence crosses sample identity")
    return ObservationRepairEvidence(
        logical_request_id=subject.logical_request.logical_request_id,
        request_identity_id=request_identity_id,
        group_hypothesis_id=group_hypothesis_id,
        member_subject_id=subject.member_subject.member_subject_id,
        sample_stem=sample_stem,
        work_unit_key=work_unit_key,
        candidate_index=candidate_index,
        candidate_phase=candidate_phase,
        acquisition_source_id=acquisition_source_id,
        selection_subject_id=selection_subject_id,
        physical_question_id=physical_question_id,
        physical_answer_id=physical_answer_id,
        physical_segment_id=physical_segment_id,
        fact_content_id=fact_content_id,
        selected_integration_authority_content_id=(
            selected_integration_authority_content_id
        ),
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        height=height,
        scan_support_count=scan_support_count,
        area_counts_seconds=area_counts_seconds,
        area_source=area_source,
    )


def _subject_id(
    *,
    request_identity: OwnerBackfillRequestIdentity,
    logical_request: OwnerBackfillLogicalRequestMember,
    predecessor_group: CrossSampleGroupIdentity,
    member_subject: CrossSampleGroupMemberSubject,
    sample_identity: ResolvedCanonicalSampleIdentity,
    successor_application: CrossSampleSuccessorApplication,
    repair_epoch: int,
) -> str:
    return typed_identity_digest(
        _SUBJECT_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_subject_v1",
                "request_identity_id": request_identity.request_identity_id,
                "logical_request_id": logical_request.logical_request_id,
                "predecessor_group_hypothesis_id": (
                    predecessor_group.group_hypothesis_id
                ),
                "member_subject_id": member_subject.member_subject_id,
                "canonical_sample_identity_id": (
                    sample_identity.identity.sample_identity_id
                ),
                "sample_metadata_snapshot_id": sample_identity.snapshot.snapshot_id,
                "sample_raw_stem": sample_identity.metadata.raw_stem,
                "successor_application": _successor_application_basis(
                    successor_application
                ),
                "repair_epoch": repair_epoch,
            }
        ),
    )


def _successor_application_basis(
    application: CrossSampleSuccessorApplication,
) -> dict[str, object]:
    return {
        "status": application.status,
        "reason": application.reason,
        "predecessors": [
            _group_identity_basis(group) for group in application.predecessors
        ],
        "successors": [
            _group_identity_basis(group) for group in application.successors
        ],
        "lineage": [
            {
                "lineage_edge_id": edge.lineage_edge_id,
                "predecessor_group_hypothesis_id": (
                    edge.predecessor_group_hypothesis_id
                ),
                "successor_group_hypothesis_id": edge.successor_group_hypothesis_id,
                "processing_request_id": edge.processing_request_id,
                "predecessor_identity_basis_hex": (
                    edge.predecessor_identity_basis_bytes.hex()
                ),
                "predecessor_group_hypothesis_ids": list(
                    edge.predecessor_group_hypothesis_ids
                ),
                "member_subject_ids": list(edge.member_subject_ids),
                "basis_hex": edge.basis_bytes.hex(),
            }
            for edge in application.lineage
        ],
    }


def _group_identity_basis(group: CrossSampleGroupIdentity) -> dict[str, object]:
    return {
        "group_hypothesis_id": group.group_hypothesis_id,
        "processing_request_id": group.processing_request_id,
        "member_subject_ids": [
            member.member_subject_id for member in group.member_subjects
        ],
        "basis_hex": group.basis_bytes.hex(),
        "disposition": group.disposition,
    }


def _receipt_id(
    *,
    subject_id: str,
    repair_epoch: int,
    outcome: ObservationRepairOutcome,
    sample_stem: str,
    evidence: ObservationRepairEvidence | None,
) -> str:
    evidence_basis: object = None
    if evidence is not None:
        evidence_basis = {
            name: getattr(evidence, name)
            for name in ObservationRepairEvidence.__dataclass_fields__
        }
    return typed_identity_digest(
        _RECEIPT_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_receipt_v1",
                "subject_id": subject_id,
                "repair_epoch": repair_epoch,
                "outcome": outcome,
                "sample_stem": sample_stem,
                "evidence": evidence_basis,
            }
        ),
    )


__all__ = (
    "ObservationRepairContractError",
    "ObservationRepairEvidence",
    "ObservationRepairOutcome",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
    "close_observation_repair",
    "mint_observation_repair_subject",
)
