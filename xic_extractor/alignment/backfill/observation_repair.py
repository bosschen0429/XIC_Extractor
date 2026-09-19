"""One bounded Backfill transaction that closes repaired observation evidence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillEvaluationCoverageBatch,
)
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
from xic_extractor.alignment.owner_backfill_peak_fact_projection import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.owner_backfill_request_plan import (
    OwnerBackfillLogicalRequestMember,
    OwnerBackfillRequestIdentity,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    resolve_canonical_sample_identity,
)

from .observation_repair_authorization import (
    _AUTHORIZATION_MINT_TOKEN,
    _AUTHORIZATION_PREFIX,
    _CANDIDATE_PREFIX,
    ObservationRepairAuthorization,
    ObservationRepairAuthorizationState,
    ObservationRepairCandidate,
    ObservationRepairContractError,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNotRunAuthorization,
    ObservationRepairRepairAuthorization,
    _require_current_repair_authorization,
    _validate_candidate,
    _validate_typed_id,
    authorize_observation_repair,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_observations import CellObservationRef

ObservationRepairOutcome: TypeAlias = Literal["no_repair", "repaired"]

_SUBJECT_PREFIX = "xic.observation-repair-subject.v1:"
_RECEIPT_PREFIX = "xic.observation-repair-receipt.v1:"
_CANDIDATE_RECEIPT_PREFIX = "xic.observation-repair-receipt.v2:"
_NO_REPAIR_TERMINAL_PREFIX = "xic.observation-repair-no-repair-terminal.v1:"
_PHYSICAL_ANSWER_PREFIX = "physical-answer.v1:"
_SUBJECT_MINT_TOKEN = object()
_RECEIPT_MINT_TOKEN = object()
_NO_REPAIR_TERMINAL_MINT_TOKEN = object()
_TERMINAL_RESULT_MINT_TOKEN = object()


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
    member_subject_id: str | None
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
class ObservationRepairNoRepairTerminal:
    """Exact physical terminal fact for a membership-neutral no-repair result."""

    terminal_id: str
    subject_id: str
    authorization_id: str
    candidate_id: str
    repair_epoch: int
    work_unit_key: str
    physical_answer_id: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _NO_REPAIR_TERMINAL_MINT_TOKEN:
            raise ObservationRepairContractError(
                "no-repair terminal must be minted by Backfill"
            )
        _validate_no_repair_terminal(self)


@dataclass(frozen=True, slots=True)
class ObservationRepairReceipt:
    """Terminal content-bound handoff from Backfill to its next owner."""

    receipt_id: str
    subject: ObservationRepairSubject | ObservationRepairCandidate
    subject_id: str
    repair_epoch: int
    outcome: ObservationRepairOutcome
    sample_stem: str
    evidence: ObservationRepairEvidence | None
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RECEIPT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair receipt must be minted by Backfill"
            )
        if type(self.subject) not in {
            ObservationRepairSubject,
            ObservationRepairCandidate,
        }:
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
        if self.evidence is not None and self.no_repair_terminal is not None:
            raise ObservationRepairContractError(
                "repaired receipt cannot carry a no-repair terminal"
            )
        if (
            type(self.subject) is ObservationRepairSubject
            and self.no_repair_terminal is not None
        ):
            raise ObservationRepairContractError(
                "no-repair terminal is candidate-scoped"
            )
        if self.evidence is None:
            if (
                type(self.subject) is ObservationRepairCandidate
                and self.no_repair_terminal is None
            ):
                raise ObservationRepairContractError(
                    "candidate no-repair receipt requires an exact terminal"
                )
            if self.no_repair_terminal is not None:
                if (
                    type(self.no_repair_terminal)
                    is not ObservationRepairNoRepairTerminal
                ):
                    raise TypeError("no-repair terminal must be owner-minted")
                self.no_repair_terminal.__post_init__(_NO_REPAIR_TERMINAL_MINT_TOKEN)
                if (
                    self.no_repair_terminal.subject_id != self.subject_id
                    or self.no_repair_terminal.repair_epoch != self.repair_epoch
                    or self.no_repair_terminal.authorization_id != self.authorization_id
                    or self.no_repair_terminal.candidate_id != self.candidate_id
                ):
                    raise ObservationRepairContractError(
                        "no-repair terminal crosses exact subject or epoch"
                    )
        expected = _receipt_id(
            subject_id=self.subject_id,
            repair_epoch=self.repair_epoch,
            outcome=self.outcome,
            sample_stem=self.sample_stem,
            evidence=self.evidence,
            no_repair_terminal=self.no_repair_terminal,
            authorization_id=self.authorization_id,
            candidate_id=self.candidate_id,
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

    @property
    def authorization_id(self) -> str | None:
        """Expose the candidate authorization without granting new authority."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.authorization_id
        return None

    @property
    def candidate_id(self) -> str | None:
        """Expose the membership-neutral candidate identity when present."""

        if type(self.subject) is ObservationRepairCandidate:
            return self.subject.candidate_id
        return None


@dataclass(frozen=True, slots=True)
class ObservationRepairTerminalResult:
    """Closed owner result for one complete population authorization."""

    authorization: ObservationRepairAuthorization
    receipt: ObservationRepairReceipt | None
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _TERMINAL_RESULT_MINT_TOKEN:
            raise ObservationRepairContractError(
                "repair terminal result must be minted by Backfill"
            )
        if type(self.authorization) not in {
            ObservationRepairNotRunAuthorization,
            ObservationRepairRepairAuthorization,
            ObservationRepairMultipleEligibleAuthorization,
        }:
            raise TypeError("terminal result authorization must be owner-minted")
        self.authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        if type(self.authorization) is ObservationRepairRepairAuthorization:
            if type(self.receipt) is not ObservationRepairReceipt:
                raise ObservationRepairContractError(
                    "repair terminal result requires an exact receipt"
                )
            if (
                self.receipt.subject != self.authorization.candidate
                or self.receipt.authorization_id != self.authorization.authorization_id
                or self.receipt.candidate_id
                != self.authorization.candidate.candidate_id
            ):
                raise ObservationRepairContractError(
                    "repair terminal result receipt crosses authorization"
                )
        elif self.receipt is not None:
            raise ObservationRepairContractError(
                "blocked repair terminal result cannot carry a receipt"
            )

    @property
    def state(self) -> ObservationRepairAuthorizationState:
        return self.authorization.state

    @property
    def candidate(self) -> ObservationRepairCandidate | None:
        if type(self.authorization) is ObservationRepairRepairAuthorization:
            return self.authorization.candidate
        return None


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
    subject: (
        ObservationRepairSubject
        | ObservationRepairCandidate
        | ObservationRepairRepairAuthorization
    ),
    result: OwnerBackfillSampleResult,
    *,
    authorization: ObservationRepairRepairAuthorization | None = None,
    coverage: OwnerBackfillEvaluationCoverageBatch | None = None,
    cell_observations: tuple[CellObservationRef, ...] | None = None,
) -> ObservationRepairReceipt:
    """Close one complete owner result as ``no_repair`` or ``repaired``.

    A membership-neutral candidate must be closed against the complete current
    population.  Re-authorizing at this boundary prevents an older candidate
    from being replayed after the population cardinality or candidate changes.
    Legacy member subjects retain their existing close-only contract.
    """

    if type(subject) is ObservationRepairRepairAuthorization:
        if authorization is not None and authorization is not subject:
            raise ObservationRepairContractError(
                "repair execution received two different authorizations"
            )
        authorization = subject
        authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        effective_subject: ObservationRepairSubject | ObservationRepairCandidate = (
            subject.candidate
        )
    elif type(subject) is ObservationRepairCandidate:
        if type(authorization) is not ObservationRepairRepairAuthorization:
            raise ObservationRepairContractError(
                "repair candidate requires its exact current authorization"
            )
        authorization.__post_init__(_AUTHORIZATION_MINT_TOKEN)
        if authorization.candidate is not subject:
            raise ObservationRepairContractError(
                "repair execution candidate is not the exact authorized candidate"
            )
        effective_subject = subject
    elif type(subject) is ObservationRepairSubject:
        if (
            authorization is not None
            or coverage is not None
            or cell_observations is not None
        ):
            raise ObservationRepairContractError(
                "member repair subject cannot carry population authorization"
            )
        effective_subject = subject
    else:
        raise TypeError(
            "subject must be an owner-minted ObservationRepairSubject, candidate, "
            "or repair authorization"
        )
    if type(effective_subject) is ObservationRepairCandidate:
        _require_current_repair_authorization(
            authorization,
            candidate=effective_subject,
            coverage=coverage,
            cell_observations=cell_observations,
        )
    return _close_observation_repair_after_authorization(
        effective_subject,
        result,
    )


def _close_observation_repair_after_authorization(
    subject: (ObservationRepairSubject | ObservationRepairCandidate),
    result: OwnerBackfillSampleResult,
) -> ObservationRepairReceipt:
    """Close one already-authorized owner result."""

    _validate_subject(subject)
    effective_subject = subject
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
    if result.sample_stem != effective_subject.request_identity.sample_stem:
        raise ObservationRepairContractError("repair result crosses sample identity")

    if result.prepared_peak_facts:
        raise ObservationRepairContractError(
            "prepared Backfill facts lack exact logical request closure"
        )
    inputs = result.peak_fact_observations
    exact_work_units: set[str] = set()
    exact_live: list[OwnerBackfillPeakFactObservation] = []
    normalized_list: list[ObservationRepairEvidence] = []
    for item in inputs:
        normalized = _normalize_live_evidence(effective_subject, item)
        if (
            item.logical_request == effective_subject.logical_request
            and item.request_identity == effective_subject.request_identity
            and item.cell.group_hypothesis_id
            == effective_subject.predecessor_group.group_hypothesis_id
            and item.cell.sample_stem == effective_subject.request_identity.sample_stem
        ):
            if item.work_unit_key in exact_work_units:
                raise ObservationRepairContractError(
                    "repair result contains duplicate evidence"
                )
            exact_work_units.add(item.work_unit_key)
            exact_live.append(item)
        if normalized is not None:
            normalized_list.append(normalized)
    if not normalized_list:
        evidence = None
        if type(effective_subject) is ObservationRepairCandidate:
            if len(exact_live) != 1:
                raise ObservationRepairContractError(
                    "candidate no_repair requires exactly one exact live "
                    "evaluated observation"
                )
            no_repair_terminal = _mint_no_repair_terminal(
                effective_subject,
                exact_live[0],
            )
        else:
            if not exact_live:
                raise ObservationRepairContractError(
                    "no_repair requires an exact live evaluated observation"
                )
            no_repair_terminal = None
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
        no_repair_terminal = None

    outcome: ObservationRepairOutcome = (
        "repaired" if evidence is not None else "no_repair"
    )
    receipt_id = _receipt_id(
        subject_id=effective_subject.subject_id,
        repair_epoch=effective_subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
        no_repair_terminal=no_repair_terminal,
        authorization_id=(
            effective_subject.authorization_id
            if type(effective_subject) is ObservationRepairCandidate
            else None
        ),
        candidate_id=(
            effective_subject.candidate_id
            if type(effective_subject) is ObservationRepairCandidate
            else None
        ),
    )
    return ObservationRepairReceipt(
        receipt_id=receipt_id,
        subject=effective_subject,
        subject_id=effective_subject.subject_id,
        repair_epoch=effective_subject.repair_epoch,
        outcome=outcome,
        sample_stem=result.sample_stem,
        evidence=evidence,
        no_repair_terminal=no_repair_terminal,
        _mint_token=_RECEIPT_MINT_TOKEN,
    )


def execute_observation_repair(
    *,
    processing_owners: ProcessingOwnerReferences,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    cell_observations: tuple[CellObservationRef, ...],
    execute: Callable[[ObservationRepairCandidate], OwnerBackfillSampleResult],
) -> ObservationRepairTerminalResult:
    """Authorize and execute at most one bounded repair transaction.

    The callback is invoked only for the single eligible candidate.  The
    ``not_run`` and ``multiple_eligible`` branches are terminal blocks and
    cannot produce a repair receipt.
    """

    authorization = authorize_observation_repair(
        processing_owners=processing_owners,
        coverage=coverage,
        cell_observations=cell_observations,
    )
    if type(authorization) is not ObservationRepairRepairAuthorization:
        return ObservationRepairTerminalResult(
            authorization=authorization,
            receipt=None,
            _mint_token=_TERMINAL_RESULT_MINT_TOKEN,
        )
    if not callable(execute):
        raise TypeError("execute must be a callable owner repair operation")
    result = execute(authorization.candidate)
    receipt = _close_observation_repair_after_authorization(
        authorization.candidate,
        result,
    )
    return ObservationRepairTerminalResult(
        authorization=authorization,
        receipt=receipt,
        _mint_token=_TERMINAL_RESULT_MINT_TOKEN,
    )


def _validate_subject(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
) -> None:
    if type(subject) is ObservationRepairCandidate:
        _validate_candidate(subject)
        return
    if type(subject) is not ObservationRepairSubject:
        raise TypeError("subject must be owner-minted")
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
    subject: ObservationRepairSubject | ObservationRepairCandidate,
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


def _mint_no_repair_terminal(
    subject: ObservationRepairCandidate,
    observation: OwnerBackfillPeakFactObservation,
) -> ObservationRepairNoRepairTerminal:
    """Project one exact live terminal without recomputing scientific facts."""

    validate_owner_backfill_peak_fact_observation(observation)
    evaluation = observation.physical_evaluation
    if not observation.trace_evaluated or evaluation is None:
        raise ObservationRepairContractError(
            "no_repair terminal requires a completed physical evaluation"
        )
    if observation.selected_for_output and not _is_completed_no_signal_evaluation(
        observation
    ):
        raise ObservationRepairContractError(
            "selected no_repair terminal is not a completed no-signal evaluation"
        )
    terminal_id = _no_repair_terminal_id(
        subject_id=subject.subject_id,
        authorization_id=subject.authorization_id,
        candidate_id=subject.candidate_id,
        repair_epoch=subject.repair_epoch,
        work_unit_key=observation.work_unit_key,
        physical_answer_id=evaluation.answer.physical_answer_id,
    )
    return ObservationRepairNoRepairTerminal(
        terminal_id=terminal_id,
        subject_id=subject.subject_id,
        authorization_id=subject.authorization_id,
        candidate_id=subject.candidate_id,
        repair_epoch=subject.repair_epoch,
        work_unit_key=observation.work_unit_key,
        physical_answer_id=evaluation.answer.physical_answer_id,
        _mint_token=_NO_REPAIR_TERMINAL_MINT_TOKEN,
    )


def _evidence(
    subject: ObservationRepairSubject | ObservationRepairCandidate,
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
        if request_identity_id != expected[0] or acquisition_source_id != expected[3]:
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
        member_subject_id=(
            None
            if subject.member_subject is None
            else subject.member_subject.member_subject_id
        ),
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


def _no_repair_terminal_id(
    *,
    subject_id: str,
    authorization_id: str,
    candidate_id: str,
    repair_epoch: int,
    work_unit_key: str,
    physical_answer_id: str,
) -> str:
    return typed_identity_digest(
        _NO_REPAIR_TERMINAL_PREFIX,
        canonical_identity_basis_bytes(
            {
                "schema_version": "observation_repair_no_repair_terminal_v1",
                "subject_id": subject_id,
                "authorization_id": authorization_id,
                "candidate_id": candidate_id,
                "repair_epoch": repair_epoch,
                "work_unit_key": work_unit_key,
                "physical_answer_id": physical_answer_id,
            }
        ),
    )


def _validate_no_repair_terminal(
    terminal: ObservationRepairNoRepairTerminal,
) -> None:
    if terminal.authorization_id is None or terminal.candidate_id is None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    _validate_typed_id(
        terminal.terminal_id,
        _NO_REPAIR_TERMINAL_PREFIX,
        "no-repair terminal_id",
    )
    _validate_typed_id(
        terminal.authorization_id,
        _AUTHORIZATION_PREFIX,
        "no-repair authorization_id",
    )
    _validate_typed_id(
        terminal.candidate_id,
        _CANDIDATE_PREFIX,
        "no-repair candidate_id",
    )
    if terminal.subject_id != terminal.candidate_id:
        raise ObservationRepairContractError(
            "candidate no-repair terminal crosses its subject"
        )
    if terminal.repair_epoch != 1:
        raise ObservationRepairContractError(
            "no-repair terminal repair epoch must be exactly 1"
        )
    if type(terminal.work_unit_key) is not str or not terminal.work_unit_key:
        raise ObservationRepairContractError(
            "no-repair terminal work_unit_key is required"
        )
    _validate_typed_id(
        terminal.physical_answer_id,
        _PHYSICAL_ANSWER_PREFIX,
        "no-repair physical_answer_id",
    )
    expected = _no_repair_terminal_id(
        subject_id=terminal.subject_id,
        authorization_id=terminal.authorization_id,
        candidate_id=terminal.candidate_id,
        repair_epoch=terminal.repair_epoch,
        work_unit_key=terminal.work_unit_key,
        physical_answer_id=terminal.physical_answer_id,
    )
    if terminal.terminal_id != expected:
        raise ObservationRepairContractError(
            "no-repair terminal id is not bound to its complete content"
        )


def _receipt_id(
    *,
    subject_id: str,
    repair_epoch: int,
    outcome: ObservationRepairOutcome,
    sample_stem: str,
    evidence: ObservationRepairEvidence | None,
    no_repair_terminal: ObservationRepairNoRepairTerminal | None = None,
    authorization_id: str | None = None,
    candidate_id: str | None = None,
) -> str:
    if (authorization_id is None) != (candidate_id is None):
        raise ObservationRepairContractError(
            "candidate receipt identity requires authorization and candidate ids"
        )
    candidate_receipt = candidate_id is not None
    if not candidate_receipt and no_repair_terminal is not None:
        raise ObservationRepairContractError("no-repair terminal is candidate-scoped")
    evidence_basis: object = None
    if evidence is not None:
        evidence_basis = {
            name: getattr(evidence, name)
            for name in ObservationRepairEvidence.__dataclass_fields__
        }
    payload: dict[str, object] = {
        "schema_version": (
            "observation_repair_receipt_v2"
            if candidate_receipt
            else "observation_repair_receipt_v1"
        ),
        "subject_id": subject_id,
        "repair_epoch": repair_epoch,
        "outcome": outcome,
        "sample_stem": sample_stem,
        "evidence": evidence_basis,
    }
    if candidate_receipt:
        payload["authorization_id"] = authorization_id
        payload["candidate_id"] = candidate_id
    if no_repair_terminal is not None:
        payload["no_repair_terminal"] = {
            "terminal_id": no_repair_terminal.terminal_id,
            "subject_id": no_repair_terminal.subject_id,
            "authorization_id": no_repair_terminal.authorization_id,
            "candidate_id": no_repair_terminal.candidate_id,
            "repair_epoch": no_repair_terminal.repair_epoch,
            "work_unit_key": no_repair_terminal.work_unit_key,
            "physical_answer_id": no_repair_terminal.physical_answer_id,
        }
    return typed_identity_digest(
        _CANDIDATE_RECEIPT_PREFIX if candidate_receipt else _RECEIPT_PREFIX,
        canonical_identity_basis_bytes(payload),
    )


__all__ = (
    "ObservationRepairAuthorization",
    "ObservationRepairAuthorizationState",
    "ObservationRepairContractError",
    "ObservationRepairCandidate",
    "ObservationRepairEvidence",
    "ObservationRepairMultipleEligibleAuthorization",
    "ObservationRepairNoRepairTerminal",
    "ObservationRepairNotRunAuthorization",
    "ObservationRepairOutcome",
    "ObservationRepairRepairAuthorization",
    "ObservationRepairReceipt",
    "ObservationRepairSubject",
    "ObservationRepairTerminalResult",
    "authorize_observation_repair",
    "close_observation_repair",
    "execute_observation_repair",
    "mint_observation_repair_subject",
)
