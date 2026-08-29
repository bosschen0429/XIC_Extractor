"""Immutable shared-core closure after the final CrossSample receipt."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairReceipt,
)
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    CellObservationRef,
    FinalGroupCellObservation,
    compose_final_group_cell_observation_population,
    validate_final_group_cell_observation_population,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    validate_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleLineageEdge,
    CrossSampleSuccessorApplication,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleProposalOutcome,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
)
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    SearchScope,
    SearchScopeContractError,
    TargetSearchScope,
    UntargetedSearchScope,
    require_capture_binding,
)
from xic_extractor.processing_identity import ProcessingRequestIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class FinalizedScientificAlignmentContractError(ValueError):
    """Raised when the shared scientific core cannot close exactly."""


_FINALIZER_MINT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class ObservedSearchSubject:
    """One owner-sealed capture with a selected scientific hypothesis."""

    candidate_id: str
    canonical_sample_identity_id: str
    trace_group_id: str
    hypothesis: PeakHypothesis
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_finalizer_mint(_mint_token)
        _validate_observed_subject(self)

    @property
    def variant(self) -> Literal["observed"]:
        return "observed"


@dataclass(frozen=True, slots=True)
class SearchedNoObservationSubject:
    """One owner-sealed completed capture without a selected hypothesis."""

    candidate_id: str
    canonical_sample_identity_id: str
    trace_group_id: str
    detection_status: Literal["not_detected"]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_finalizer_mint(_mint_token)
        _validate_searched_no_observation_subject(self)

    @property
    def variant(self) -> Literal["searched_no_observation"]:
        return "searched_no_observation"


TerminalSearchObservation: TypeAlias = (
    ObservedSearchSubject | SearchedNoObservationSubject
)


@dataclass(frozen=True, slots=True)
class FinalizedScientificAlignment:
    """One exact request's closed shared scientific result.

    This value validates and retains owner-minted inputs.  It does not rerun
    capture, CrossSample, ModeTrack, ObservationRepair, or value selection.
    """

    scope: SearchScope
    processing_request: ProcessingRequestIdentity
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    terminal_subjects: tuple[TerminalSearchObservation, ...]
    cell_observation_handoff: CrossSampleAssociationLineageHandoff
    cell_observations: tuple[FinalGroupCellObservation, ...]
    final_cross_sample_receipt: CrossSampleFinalReceipt
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _require_finalizer_mint(_mint_token)
        _validate_finalized_alignment(self)

    @property
    def processing_request_id(self) -> str:
        return self.processing_request.processing_request_id

    @property
    def final_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.final_cross_sample_receipt.final_population

    @property
    def final_application(self) -> CrossSampleSuccessorApplication:
        return self.final_cross_sample_receipt.final_application

    @property
    def lineage(self) -> tuple[CrossSampleLineageEdge, ...]:
        return self.final_application.lineage

    @property
    def repair_receipt(self) -> ObservationRepairReceipt:
        return self.final_cross_sample_receipt.repair_receipt

    @property
    def mode_track_proposal(self) -> CrossSampleProposalOutcome | None:
        return self.final_cross_sample_receipt.mode_track_proposal


def finalize_scientific_alignment(
    *,
    scope: SearchScope,
    captures: Sequence[SampleLocalCapture],
    final_cross_sample_receipt: CrossSampleFinalReceipt,
    cell_observation_handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cell_observations: Sequence[CellObservationRef],
    repair_receipts: Sequence[ObservationRepairReceipt] = (),
    repaired_observations: Sequence[OwnerBackfillPeakFactObservation] = (),
) -> FinalizedScientificAlignment:
    """Validate one complete capture population and seal its shared core."""

    _validate_scope(scope)
    if type(final_cross_sample_receipt) is not CrossSampleFinalReceipt:
        raise FinalizedScientificAlignmentContractError(
            "finalization requires an exact CrossSample final receipt"
        )
    if (
        final_cross_sample_receipt.processing_request_id
        != scope.request.processing_request_id
    ):
        raise FinalizedScientificAlignmentContractError(
            "CrossSample final receipt crossed the processing request"
        )
    if isinstance(captures, (str, bytes)):
        raise FinalizedScientificAlignmentContractError(
            "captures must be a finite sequence of owner-sealed values"
        )
    capture_tuple = tuple(captures)
    terminal_subjects = tuple(
        sorted(
            (_terminal_subject(scope, capture) for capture in capture_tuple),
            key=_terminal_sort_key,
        )
    )
    _validate_terminal_population(scope, terminal_subjects)
    canonical_samples = tuple(
        sorted(
            scope.samples,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    _validate_cell_observation_handoff(
        handoff=cell_observation_handoff,
        final_cross_sample_receipt=final_cross_sample_receipt,
        canonical_samples=canonical_samples,
        processing_request_id=scope.request.processing_request_id,
    )
    try:
        canonical_cells = compose_final_group_cell_observation_population(
            handoff=cell_observation_handoff,
            predecessor_cells=predecessor_cell_observations,
            repair_receipts=repair_receipts,
            repaired_observations=repaired_observations,
        )
    except CellObservationContractError as exc:
        raise FinalizedScientificAlignmentContractError(str(exc)) from exc
    result = FinalizedScientificAlignment(
        scope=scope,
        processing_request=scope.request,
        canonical_samples=canonical_samples,
        terminal_subjects=terminal_subjects,
        cell_observation_handoff=cell_observation_handoff,
        cell_observations=canonical_cells,
        final_cross_sample_receipt=final_cross_sample_receipt,
        _mint_token=_FINALIZER_MINT_TOKEN,
    )
    return result


def _terminal_subject(
    scope: SearchScope,
    capture: SampleLocalCapture,
) -> TerminalSearchObservation:
    if type(capture) is not SampleLocalCapture:
        raise FinalizedScientificAlignmentContractError(
            "terminal capture must be issued by the shared capture owner"
        )
    if capture.scope is not scope:
        raise FinalizedScientificAlignmentContractError(
            "terminal capture crossed its exact SearchScope"
        )
    try:
        binding = require_capture_binding(scope, capture.candidate_id)
    except SearchScopeContractError as exc:
        raise FinalizedScientificAlignmentContractError(str(exc)) from exc
    selected = capture.selected_hypothesis
    if selected is None:
        if (
            capture.detection.status != "not_detected"
            or (
                capture.detection.result is not None
                and capture.detection.result.status == "WINDOW_TOO_SHORT"
            )
        ):
            raise FinalizedScientificAlignmentContractError(
                "capture is not a closed searched-no-observation terminal"
            )
        return SearchedNoObservationSubject(
            candidate_id=capture.candidate_id,
            canonical_sample_identity_id=binding.sample_identity_id,
            trace_group_id=capture.trace_group.trace_group_id,
            detection_status="not_detected",
            _mint_token=_FINALIZER_MINT_TOKEN,
        )
    if (
        type(selected) is not PeakHypothesis
        or capture.detection.status != "detected"
    ):
        raise FinalizedScientificAlignmentContractError(
            "observed capture lacks a closed detected PeakHypothesis"
        )
    return ObservedSearchSubject(
        candidate_id=capture.candidate_id,
        canonical_sample_identity_id=binding.sample_identity_id,
        trace_group_id=capture.trace_group.trace_group_id,
        hypothesis=selected,
        _mint_token=_FINALIZER_MINT_TOKEN,
    )


def _validate_finalized_alignment(result: FinalizedScientificAlignment) -> None:
    _validate_scope(result.scope)
    if result.processing_request is not result.scope.request:
        raise FinalizedScientificAlignmentContractError(
            "finalized result crossed its exact processing request"
        )
    expected_samples = tuple(
        sorted(
            result.scope.samples,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    if result.canonical_samples != expected_samples:
        raise FinalizedScientificAlignmentContractError(
            "finalized result crossed its canonical sample universe"
        )
    if type(result.final_cross_sample_receipt) is not CrossSampleFinalReceipt:
        raise FinalizedScientificAlignmentContractError(
            "finalized result requires an exact CrossSample final receipt"
        )
    if (
        result.final_cross_sample_receipt.processing_request_id
        != result.processing_request.processing_request_id
    ):
        raise FinalizedScientificAlignmentContractError(
            "finalized result crossed the processing request"
        )
    expected_terminal_order = tuple(
        sorted(result.terminal_subjects, key=_terminal_sort_key)
    )
    if result.terminal_subjects != expected_terminal_order:
        raise FinalizedScientificAlignmentContractError(
            "terminal subjects are not canonical-sorted"
        )
    _validate_terminal_population(result.scope, result.terminal_subjects)
    _validate_cell_observation_handoff(
        handoff=result.cell_observation_handoff,
        final_cross_sample_receipt=result.final_cross_sample_receipt,
        canonical_samples=result.canonical_samples,
        processing_request_id=result.processing_request_id,
    )
    try:
        canonical_cells = validate_final_group_cell_observation_population(
            handoff=result.cell_observation_handoff,
            cell_observations=result.cell_observations,
        )
    except CellObservationContractError as exc:
        raise FinalizedScientificAlignmentContractError(str(exc)) from exc
    if result.cell_observations != canonical_cells:
        raise FinalizedScientificAlignmentContractError(
            "final cell observations are not canonical-sorted"
        )
    sample_ids = {
        sample.identity.sample_identity_id for sample in result.canonical_samples
    }
    for group in result.final_population:
        for member in group.member_subjects:
            if member.canonical_sample_identity_id not in sample_ids:
                raise FinalizedScientificAlignmentContractError(
                    "final CrossSample population crossed the canonical sample universe"
                )


def _validate_terminal_population(
    scope: SearchScope,
    terminal_subjects: tuple[TerminalSearchObservation, ...],
) -> None:
    expected = tuple(sorted(binding.candidate_id for binding in scope.capture_bindings))
    actual = tuple(sorted(subject.candidate_id for subject in terminal_subjects))
    if len(actual) != len(set(actual)):
        raise FinalizedScientificAlignmentContractError(
            "terminal capture population contains duplicate candidates"
        )
    if actual != expected:
        raise FinalizedScientificAlignmentContractError(
            "terminal capture population is missing or contains extra candidates"
        )
    for subject in terminal_subjects:
        if type(subject) is ObservedSearchSubject:
            _validate_observed_subject(subject)
        elif type(subject) is SearchedNoObservationSubject:
            _validate_searched_no_observation_subject(subject)
        else:
            raise FinalizedScientificAlignmentContractError(
                "terminal subject has an unsupported variant"
            )
        try:
            binding = require_capture_binding(scope, subject.candidate_id)
        except SearchScopeContractError as exc:
            raise FinalizedScientificAlignmentContractError(str(exc)) from exc
        if subject.canonical_sample_identity_id != binding.sample_identity_id:
            raise FinalizedScientificAlignmentContractError(
                "terminal subject crossed its canonical sample"
            )


def _validate_observed_subject(subject: ObservedSearchSubject) -> None:
    _validate_terminal_fields(
        candidate_id=subject.candidate_id,
        canonical_sample_identity_id=subject.canonical_sample_identity_id,
        trace_group_id=subject.trace_group_id,
    )
    if (
        type(subject.hypothesis) is not PeakHypothesis
        or subject.hypothesis.trace_group_id != subject.trace_group_id
    ):
        raise FinalizedScientificAlignmentContractError(
            "observed terminal subject crossed its trace-group hypothesis"
        )


def _validate_cell_observation_handoff(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    final_cross_sample_receipt: CrossSampleFinalReceipt,
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    processing_request_id: str,
) -> None:
    try:
        validate_cross_sample_association_lineage_handoff(handoff)
    except (TypeError, ValueError) as exc:
        raise FinalizedScientificAlignmentContractError(str(exc)) from exc
    if handoff.final_receipt is not final_cross_sample_receipt:
        raise FinalizedScientificAlignmentContractError(
            "cell-observation handoff substituted the final CrossSample receipt"
        )
    if (
        handoff.coverage.epoch.processing_owners.request.processing_request_id
        != processing_request_id
    ):
        raise FinalizedScientificAlignmentContractError(
            "cell-observation handoff crossed the processing request"
        )
    handoff_samples = handoff.coverage.epoch.samples
    handoff_sample_by_id = {
        sample.identity.sample_identity_id: sample for sample in handoff_samples
    }
    if len(handoff_sample_by_id) != len(canonical_samples) or any(
        handoff_sample_by_id.get(sample.identity.sample_identity_id) is not sample
        for sample in canonical_samples
    ):
        raise FinalizedScientificAlignmentContractError(
            "cell-observation handoff crossed the canonical sample universe"
        )


def _validate_searched_no_observation_subject(
    subject: SearchedNoObservationSubject,
) -> None:
    _validate_terminal_fields(
        candidate_id=subject.candidate_id,
        canonical_sample_identity_id=subject.canonical_sample_identity_id,
        trace_group_id=subject.trace_group_id,
    )
    if subject.detection_status != "not_detected":
        raise FinalizedScientificAlignmentContractError(
            "searched-no-observation subject is not a completed negative search"
        )


def _validate_terminal_fields(
    *,
    candidate_id: str,
    canonical_sample_identity_id: str,
    trace_group_id: str,
) -> None:
    values = {
        "candidate_id": candidate_id,
        "canonical_sample_identity_id": canonical_sample_identity_id,
        "trace_group_id": trace_group_id,
    }
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in values.values()
    ):
        raise FinalizedScientificAlignmentContractError(
            "terminal subject identity fields must be normalized text"
        )


def _terminal_sort_key(subject: TerminalSearchObservation) -> tuple[str, str, str]:
    if not isinstance(subject, (ObservedSearchSubject, SearchedNoObservationSubject)):
        raise FinalizedScientificAlignmentContractError(
            "terminal subject has an unsupported variant"
        )
    return (
        subject.canonical_sample_identity_id,
        subject.candidate_id,
        subject.variant,
    )


def _validate_scope(scope: object) -> None:
    if type(scope) not in (TargetSearchScope, UntargetedSearchScope):
        raise FinalizedScientificAlignmentContractError(
            "finalization requires a Target or Untargeted SearchScope"
        )


def _require_finalizer_mint(value: object) -> None:
    if value is not _FINALIZER_MINT_TOKEN:
        raise FinalizedScientificAlignmentContractError(
            "value must be minted by the scientific alignment finalizer"
        )


__all__ = (
    "FinalGroupCellObservation",
    "FinalizedScientificAlignment",
    "FinalizedScientificAlignmentContractError",
    "ObservedSearchSubject",
    "SearchedNoObservationSubject",
    "TerminalSearchObservation",
    "finalize_scientific_alignment",
)
