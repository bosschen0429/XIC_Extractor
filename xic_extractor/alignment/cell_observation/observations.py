"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.alignment.cell_observation.group_context import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.alignment.cell_observation.records import (
    CellObservation,
    CellObservationContractError,
    OutsideGroupCellRelation,
    mint_cell_observation,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
    cid_nl_support_requires_supplement,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
    MeasuredPeakHypothesis,
)
from xic_extractor.sample_metadata import SampleMetadata

TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]


FinalCellEvidenceAggregate: TypeAlias = FinalCellEvidenceState | Literal["mixed"]


@dataclass(frozen=True, slots=True)
class FinalGroupCellObservation:
    """One immutable final ``CrossSamplePeakGroupHypothesis × sample`` closure.

    ``position_associations`` retain every coverage lane and its method evidence;
    a declared no-input position has no synthetic
    cell. A merge keeps every lane, while a split remainder has no associations.
    This object carries evidence only; it has no value, Matrix, writer, or
    quantifiability authority.
    """

    group: CrossSamplePeakGroupHypothesis
    canonical_sample: SampleMetadata
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    evidence_states: tuple[FinalCellEvidenceState, ...]
    backfill_cell: CellObservation | None = None

    @property
    def key(self) -> tuple[CrossSamplePeakGroupHypothesis, str]:
        """The actual final group and the sample's natural key."""
        return self.group, self.sample_key

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def sample_key(self) -> str:
        return self.canonical_sample.sample_name

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
    def method_assessments(self) -> tuple[CidNlMethodAssessment, ...]:
        return tuple(cell.method_assessment for cell in self.effective_cells)

    @property
    def predecessor_cells(self) -> tuple[CellObservation, ...]:
        return tuple(
            association.position.cell
            for association in self.position_associations
            if association.position.cell is not None
        )

    @property
    def effective_cells(self) -> tuple[CellObservation, ...]:
        return (
            (self.backfill_cell,)
            if self.backfill_cell is not None
            else self.predecessor_cells
        )

    @property
    def direct_observations(self) -> tuple[PeakObservation, ...]:
        return tuple(
            cell.peak_observation
            for cell in self.effective_cells
            if cell.peak_observation is not None
            and (
                cell.peak_hypothesis is None
                or cell.peak_hypothesis.method != HYPOTHESIS_METHOD_MS1_OBSERVATION
            )
        )

    @property
    def direct_hypotheses(self) -> tuple[MeasuredPeakHypothesis, ...]:
        return tuple(
            cell.peak_hypothesis
            for cell in self.effective_cells
            if cell.peak_hypothesis is not None
            and cell.peak_hypothesis.method != HYPOTHESIS_METHOD_MS1_OBSERVATION
        )

    @property
    def value_peaks(self) -> tuple[PeakObservation, ...]:
        """The peaks whose integrations can give this cell its value."""
        return (
            *(hypothesis.peak for hypothesis in self.direct_hypotheses),
            *self.ms1_only_backfill_observations,
            *self.ms1_only_group_observations,
        )

    @property
    def integrations(self) -> tuple[IntegrationResult, ...]:
        return tuple(
            result
            for peak in self.value_peaks
            if (result := peak.integration) is not None
        )

    @property
    def ms1_only_group_observations(self) -> tuple[PeakObservation, ...]:
        if "ms1_only_group_observation" not in self.evidence_states:
            return ()
        return tuple(
            cell.peak_hypothesis.peak
            for cell in self.effective_cells
            if cell.peak_hypothesis is not None
            and cell.peak_hypothesis.method == HYPOTHESIS_METHOD_MS1_OBSERVATION
        )

    @property
    def ms1_only_group_has_local_method_relation(self) -> bool:
        peaks = frozenset(self.ms1_only_group_observations)
        return bool(peaks) and any(
            any(
                ref.peak_observation in peaks
                for ref in assessment.individual_support_refs
            )
            or any(
                peaks.intersection(ref.matched_candidates)
                for ref in assessment.set_relation_refs
            )
            for assessment in self.method_assessments
        )

    @property
    def direct_observation_requires_supplement(self) -> bool:
        return any(
            cell.peak_observation is not None
            and cell.peak_hypothesis is not None
            and cell.peak_hypothesis.method != HYPOTHESIS_METHOD_MS1_OBSERVATION
            and cid_nl_support_requires_supplement(
                cell.method_assessment, cell.peak_observation
            )
            for cell in self.effective_cells
        )

    @property
    def ms1_only_backfill_observations(self) -> tuple[PeakObservation, ...]:
        """The single MS1 peak a group-bounded Backfill found without CID-NL."""

        if self.evidence_state != "ms1_only_backfill_observation":
            return ()
        return self.direct_observations

    @property
    def method_subject_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.group_context.method_subject_id for cell in self.effective_cells
        )


def compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    backfill_cells: Sequence[CellObservation] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    """Compose one exact final closure for every final Group × sample key.

    The complete Backfill coverage and CrossSample handoff are the only source
    of correlation. Each coverage position retains its searched cell.
    Declared no-input positions remain explicit coverage without
    materializing method evidence.
    """

    return _compose_final_group_cell_observation_population(
        handoff=handoff,
        backfill_cells=tuple(backfill_cells),
    )


def _compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    backfill_cells: tuple[CellObservation, ...] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    backfilled_by_key: dict[tuple[str, str], CellObservation] = {}
    for backfill_ref in backfill_cells:
        key = (
            backfill_ref.group_hypothesis_id,
            backfill_ref.sample_key,
        )
        if key in backfilled_by_key:
            raise CellObservationContractError("duplicate completed backfill cell")
        backfilled_by_key[key] = backfill_ref
    associations_by_key: dict[
        tuple[int, int], list[CrossSampleCoveragePositionAssociation]
    ] = {}
    for association in handoff.position_associations:
        associations_by_key.setdefault(
            (id(association.final_successor), id(association.position.sample)), []
        ).append(association)
    cells: list[FinalGroupCellObservation] = []
    for group in handoff.final_population:
        for sample in handoff.coverage.samples:
            associations = tuple(
                sorted(
                    associations_by_key.get((id(group), id(sample)), ()),
                    key=lambda item: item.position.key,
                )
            )
            states: tuple[FinalCellEvidenceState, ...]
            recovered = backfilled_by_key.pop(
                (group.group_hypothesis_id, sample.sample_name), None
            )
            if recovered is not None:
                if len(associations) != 1 or recovered.group_context.group != group:
                    raise CellObservationContractError(
                        "backfill must extend one exact predecessor"
                    )
                predecessor = associations[0].position.group
                if not set(predecessor.member_subjects).issubset(group.member_subjects):
                    raise CellObservationContractError(
                        "backfill changed original group membership"
                    )
                if _member_for_sample(predecessor, sample.sample_name) is not None:
                    raise CellObservationContractError(
                        "backfill cannot replace an existing sample member"
                    )
                if recovered.unassigned_support or recovered.outside_group is not None:
                    states = ("unresolved",)
                else:
                    states = (recovered.recovered_evidence_state(),)
            elif not associations:
                states = ("not_searched",)
            else:
                states = tuple(
                    _derive_lane_evidence_state(
                        association=association,
                        source_cell=association.position.cell,
                        final_group=group,
                        canonical_sample=sample,
                    )
                    for association in associations
                )
            cells.append(
                FinalGroupCellObservation(
                    group=group,
                    canonical_sample=sample,
                    position_associations=associations,
                    evidence_states=states,
                    backfill_cell=recovered,
                )
            )
    if backfilled_by_key:
        raise CellObservationContractError(
            "backfill cells are outside the final population"
        )
    return tuple(sorted(cells, key=_final_cell_sort_key))


def _derive_lane_evidence_state(
    *,
    association: CrossSampleCoveragePositionAssociation,
    source_cell: CellObservation | None,
    final_group: CrossSamplePeakGroupHypothesis,
    canonical_sample: SampleMetadata,
) -> FinalCellEvidenceState:
    if source_cell is None:
        return "not_searched"
    source_group = source_cell.group_context.group
    source_member = _member_for_sample(
        source_group,
        association.position.sample.sample_name,
    )
    final_member = _member_for_sample(
        final_group,
        canonical_sample.sample_name,
    )
    if source_member is not None and source_member not in final_group.member_subjects:
        raise CellObservationContractError(
            "CrossSample successor dropped the exact source member evidence"
        )
    hypothesis = source_cell.peak_hypothesis
    if (
        hypothesis is not None
        and hypothesis.method == HYPOTHESIS_METHOD_MS1_OBSERVATION
    ):
        if final_member is None or final_member.subject_id != hypothesis.hypothesis_id:
            raise CellObservationContractError(
                "MS1-only group cell lost its exact member"
            )
        return "ms1_only_group_observation"
    if source_cell.unassigned_support:
        if final_member is not None:
            raise CellObservationContractError(
                "unassigned support cannot supply a final member"
            )
        return "unresolved"
    if source_cell.outside_group is not None:
        if (
            source_member is not None
            or source_cell.peak_observation is not None
            or source_cell.peak_hypothesis is not None
            or association.position.planning_reason != "local_owner_not_supersedable"
        ):
            raise CellObservationContractError(
                "outside-group lane cannot supply local observation evidence"
            )
        return "not_searched"
    no_request_decision = association.position.no_request_decision
    if association.position.planning_reason == "backfill_disabled":
        return "not_searched"
    if no_request_decision is not None:
        if final_member is not None:
            raise CellObservationContractError(
                "conservative no-request lane cannot carry physical evidence"
            )
        return (
            "not_searched"
            if no_request_decision.variant == "threshold_not_met"
            else "unavailable"
        )
    if association.position.planning_reason == "retained_unresolved_candidates":
        if final_member is not None:
            raise CellObservationContractError(
                "final CrossSample member cannot carry a HOLD or REJECT method lane"
            )
        return "unresolved"
    state = source_cell.evidence_state()
    if state == "direct_observation":
        if final_member is None:
            raise CellObservationContractError(
                "SUPPORT evidence has no exact final CrossSample member"
            )
    elif final_member is not None and state != "unavailable":
        raise CellObservationContractError(
            "final CrossSample member cannot carry a HOLD or REJECT method lane"
        )
    return state


def _member_for_sample(
    group: CrossSamplePeakGroupHypothesis,
    sample_key: str,
) -> CrossSampleGroupMemberSubject | None:
    matches = tuple(
        member for member in group.member_subjects if member.sample_key == sample_key
    )
    if len(matches) > 1:
        raise CellObservationContractError(
            "CrossSample group has duplicate members for one canonical sample"
        )
    return matches[0] if matches else None


def _final_cell_sort_key(
    cell: FinalGroupCellObservation,
) -> tuple[str, str]:
    return cell.group_hypothesis_id, cell.sample_key


__all__ = [
    "CidNlCellGroupContext",
    "CidNlCellGroupSupport",
    "CellObservation",
    "CellObservationContractError",
    "FinalCellEvidenceAggregate",
    "FinalCellEvidenceState",
    "FinalGroupCellObservation",
    "OutsideGroupCellRelation",
    "TerminalObservationVariant",
    "compose_final_group_cell_observation_population",
    "mint_cell_observation",
]
