"""Immutable shared-core closure after the final CrossSample receipt."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.conservative import (
    ConservativeBackfillCellDecision,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservation,
    CellObservationContractError,
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
    CrossSampleLineageEdge,
    CrossSamplePartitionTransition,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import MeasuredPeakHypothesis
from xic_extractor.peak_detection.search_scope import SearchScope
from xic_extractor.sample_metadata import SampleMetadata


class FinalizedScientificAlignmentContractError(ValueError):
    """Raised when the shared scientific core cannot close exactly."""


@dataclass(frozen=True, slots=True)
class ObservedSearchSubject:
    """One searched candidate with its selected scientific hypothesis."""

    candidate_id: str
    raw_stem: str
    hypothesis: MeasuredPeakHypothesis

    @property
    def variant(self) -> Literal["observed"]:
        return "observed"


@dataclass(frozen=True, slots=True)
class SearchedNoObservationSubject:
    """One searched candidate without a selected hypothesis."""

    candidate_id: str
    raw_stem: str

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
    canonical_samples: tuple[SampleMetadata, ...]
    terminal_subjects: tuple[TerminalSearchObservation, ...]
    cell_observation_handoff: CrossSampleAssociationLineageHandoff | None
    cell_observations: tuple[FinalGroupCellObservation, ...]
    grouping_result: CrossSampleGroupingResult
    targeted_backfill_decisions: tuple[ConservativeBackfillCellDecision, ...] = ()

    @property
    def final_population(self) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
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
    terminal_subjects: Sequence[TerminalSearchObservation],
    grouping_result: CrossSampleGroupingResult,
    cell_observation_handoff: CrossSampleAssociationLineageHandoff | None,
    backfill_cell_observations: Sequence[CellObservation] = (),
    targeted_backfill_decisions: tuple[ConservativeBackfillCellDecision, ...] = (),
) -> FinalizedScientificAlignment:
    """Close one complete terminal population into its shared core."""

    terminals = tuple(sorted(terminal_subjects, key=_terminal_sort_key))
    _require_terminal_population(scope, terminals)
    canonical_samples: tuple[SampleMetadata, ...]
    samples = scope.samples
    if type(samples) is tuple and all(
        samples[i].raw_stem <= samples[i + 1].raw_stem for i in range(len(samples) - 1)
    ):
        canonical_samples = samples
    else:
        canonical_samples = tuple(
            sorted(
                samples,
                key=lambda sample: sample.raw_stem,
            )
        )
    canonical_cells: tuple[FinalGroupCellObservation, ...]
    if cell_observation_handoff is None:
        if backfill_cell_observations:
            raise FinalizedScientificAlignmentContractError(
                "repair-free finalization cannot carry repair lineage"
            )
        canonical_cells = ()
    else:
        try:
            canonical_cells = compose_final_group_cell_observation_population(
                handoff=cell_observation_handoff,
                backfill_cells=backfill_cell_observations,
            )
        except CellObservationContractError as exc:
            raise FinalizedScientificAlignmentContractError(str(exc)) from exc
    result = FinalizedScientificAlignment(
        scope=scope,
        canonical_samples=canonical_samples,
        terminal_subjects=terminals,
        cell_observation_handoff=cell_observation_handoff,
        cell_observations=canonical_cells,
        grouping_result=grouping_result,
        targeted_backfill_decisions=targeted_backfill_decisions,
    )
    return result


def _require_terminal_population(
    scope: SearchScope, terminal_subjects: tuple[TerminalSearchObservation, ...]
) -> None:
    """Require one attributed terminal for every capture in the owner scope."""
    expected = Counter(
        ((binding.candidate_id, binding.raw_stem) for binding in scope.capture_bindings)
    )
    actual = Counter(
        ((subject.candidate_id, subject.raw_stem) for subject in terminal_subjects)
    )
    if actual != expected:
        raise FinalizedScientificAlignmentContractError(
            "terminal capture population is missing, duplicate, extra, or crossed"
        )


def _terminal_sort_key(subject: TerminalSearchObservation) -> tuple[str, str, str]:
    return (
        subject.raw_stem,
        subject.candidate_id,
        subject.variant,
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
