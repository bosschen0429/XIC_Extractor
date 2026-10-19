"""Immutable shared-core closure after the final CrossSample receipt."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field, fields
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.conservative import (
    ConservativeBackfillCellDecision,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservationContractError,
    CellObservationRef,
    FinalGroupCellObservation,
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleLineageEdge,
    CrossSamplePartitionTransition,
)
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    SearchScope,
    SearchScopeContractError,
    require_capture_binding,
)
from xic_extractor.processing_identity import ProcessingRequestIdentity
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


class FinalizedScientificAlignmentContractError(ValueError):
    """Raised when the shared scientific core cannot close exactly."""


_FINALIZER_MINT_TOKEN = object()
_FRESH_CELL_POPULATION_MINT_TOKEN = object()


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
    capture, CrossSample, ModeTrack, ObservationRevisit, or value selection.
    """

    scope: SearchScope
    processing_request: ProcessingRequestIdentity
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    terminal_subjects: tuple[TerminalSearchObservation, ...]
    cell_observation_handoff: CrossSampleAssociationLineageHandoff | None
    cell_observations: tuple[FinalGroupCellObservation, ...]
    grouping_result: CrossSampleGroupingResult
    _completion_seal: tuple[int, ...] = field(init=False, repr=False, compare=False)
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)
    targeted_backfill_decisions: tuple[ConservativeBackfillCellDecision, ...] = ()

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FRESH_CELL_POPULATION_MINT_TOKEN:
            _require_finalizer_mint(_mint_token)
        object.__setattr__(self, "_completion_seal", _scientific_completion_state(self))

    def __copy__(self) -> FinalizedScientificAlignment:
        # A live clone is still a substitution, not an owner-restored spool read.
        clone = object.__new__(type(self))
        for owner_field in fields(self):
            object.__setattr__(clone, owner_field.name, getattr(self, owner_field.name))
        return clone

    def __getstate__(self) -> tuple[object, ...]:
        """Export a verified completion for private same-process storage."""
        if not hasattr(self, "_completion_seal"):
            raise FinalizedScientificAlignmentContractError(
                "scientific core is unsealed"
            )
        _validate_finalized_scientific_alignment_completion_receipt(self)
        return tuple(
            getattr(self, owner_field.name)
            for owner_field in fields(self)
            if owner_field.name != "_completion_seal"
        )

    def __setstate__(self, state: tuple[object, ...]) -> None:
        """Restore direct bindings from the private same-process spool."""
        owner_fields = tuple(
            owner_field
            for owner_field in fields(self)
            if owner_field.name != "_completion_seal"
        )
        if (
            hasattr(self, "_completion_seal")
            or type(state) is not tuple
            or len(state) != len(owner_fields)
        ):
            raise FinalizedScientificAlignmentContractError(
                "invalid scientific core restoration"
            )
        for owner_field, value in zip(owner_fields, state, strict=True):
            object.__setattr__(self, owner_field.name, value)
        object.__setattr__(
            self,
            "_completion_seal",
            _scientific_completion_state(self),
        )

    @property
    def processing_request_id(self) -> str:
        return self.processing_request.processing_request_id

    @property
    def final_population(self) -> tuple[CrossSampleGroupIdentity, ...]:
        return self.grouping_result.final_population

    @property
    def partition_transition(self) -> CrossSamplePartitionTransition:
        return self.grouping_result.partition_transition

    @property
    def lineage(self) -> tuple[CrossSampleLineageEdge, ...]:
        return self.partition_transition.lineage


def finalize_scientific_alignment(
    *,
    scope: SearchScope,
    captures: Sequence[SampleLocalCapture],
    grouping_result: CrossSampleGroupingResult,
    cell_observation_handoff: CrossSampleAssociationLineageHandoff | None,
    predecessor_cell_observations: Sequence[CellObservationRef],
    backfill_cell_observations: Sequence[CellObservationRef] = (),
    targeted_backfill_decisions: tuple[ConservativeBackfillCellDecision, ...] = (),
) -> FinalizedScientificAlignment:
    """Validate one complete capture population and seal its shared core."""

    if type(grouping_result) is not CrossSampleGroupingResult:
        raise FinalizedScientificAlignmentContractError(
            "finalization requires an exact CrossSample final receipt"
        )
    if grouping_result.processing_request_id != scope.request.processing_request_id:
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
    _require_terminal_population(scope, terminal_subjects)
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    samples = scope.samples
    if type(samples) is tuple and all(
        samples[i].identity.sample_identity_id
        <= samples[i + 1].identity.sample_identity_id
        for i in range(len(samples) - 1)
    ):
        canonical_samples = samples
    else:
        canonical_samples = tuple(
            sorted(
                samples,
                key=lambda sample: sample.identity.sample_identity_id,
            )
        )
    canonical_cells: tuple[FinalGroupCellObservation, ...]
    if cell_observation_handoff is None:
        if predecessor_cell_observations or backfill_cell_observations:
            raise FinalizedScientificAlignmentContractError(
                "repair-free finalization cannot carry repair lineage"
            )
        canonical_cells = ()
    else:
        try:
            canonical_cells = compose_final_group_cell_observation_population(
                handoff=cell_observation_handoff,
                predecessor_cells=predecessor_cell_observations,
                backfill_cells=backfill_cell_observations,
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
        grouping_result=grouping_result,
        targeted_backfill_decisions=targeted_backfill_decisions,
        # The composer just closed these cells; no callback precedes this mint.
        _mint_token=_FRESH_CELL_POPULATION_MINT_TOKEN,
    )
    return result


def _validate_finalized_scientific_alignment_completion_receipt(
    result: FinalizedScientificAlignment,
) -> None:
    """Validate one retained scientific-core completion receipt."""

    if type(result) is not FinalizedScientificAlignment:
        raise FinalizedScientificAlignmentContractError(
            "scientific-core validation requires an exact finalized alignment"
        )
    try:
        state = _scientific_completion_state(result)
        seal = result._completion_seal
    except AttributeError as exc:
        raise FinalizedScientificAlignmentContractError(
            "scientific core completion receipt was substituted or partial"
        ) from exc
    if type(seal) is not tuple or seal != state:
        raise FinalizedScientificAlignmentContractError(
            "scientific core completion receipt was substituted or partial"
        )


def _scientific_completion_state(
    result: FinalizedScientificAlignment,
) -> tuple[int, ...]:
    """Bind immutable owner answers; their constructors own scientific validation."""
    return (
        id(result),
        id(result.scope),
        id(result.processing_request),
        id(result.canonical_samples),
        id(result.terminal_subjects),
        id(result.cell_observation_handoff),
        id(result.cell_observations),
        id(result.grouping_result),
        id(result.targeted_backfill_decisions),
    )


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
        if capture.detection.status != "not_detected" or (
            capture.detection.result is not None
            and capture.detection.result.status == "WINDOW_TOO_SHORT"
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
    if type(selected) is not PeakHypothesis or capture.detection.status != "detected":
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


def _require_terminal_population(
    scope: SearchScope, terminal_subjects: tuple[TerminalSearchObservation, ...]
) -> None:
    """Require one attributed terminal for every capture in the owner scope."""
    expected = Counter(
        (
            (binding.candidate_id, binding.sample_identity_id)
            for binding in scope.capture_bindings
        )
    )
    actual = Counter(
        (
            (subject.candidate_id, subject.canonical_sample_identity_id)
            for subject in terminal_subjects
        )
    )
    if actual != expected:
        raise FinalizedScientificAlignmentContractError(
            "terminal capture population is missing, duplicate, extra, or crossed"
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
