"""Minimal sample-by-group observation closure for diagnostic evidence flow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillPlanningPosition,
)
from xic_extractor.alignment.cell_observation.group_context import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
)
from xic_extractor.alignment.cell_observation.method_evidence import (
    FinalCellEvidenceState,
)
from xic_extractor.alignment.cell_observation.records import (
    CellObservationContent,
    CellObservationContractError,
    CellObservationRef,
    OutsideGroupCellRelation,
    _direct_member_integration,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationLineageHandoff,
    CrossSampleCoveragePositionAssociation,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

TerminalObservationVariant: TypeAlias = Literal[
    "observed",
    "searched_no_observation",
]


# ---------------------------------------------------------------------------
# Exact CrossSample/Backfill handoff closure
# ---------------------------------------------------------------------------
#
# A final cell is a correlation boundary. Its inputs are the exact CrossSample
# handoff and owner-minted predecessor/completed Backfill cell evidence, not
# caller-assembled group, terminal, provider, or repair arguments.


FinalCellEvidenceAggregate: TypeAlias = FinalCellEvidenceState | Literal["mixed"]


@dataclass(frozen=True, slots=True)
class FinalGroupCellObservation:
    """One immutable final ``CrossSampleGroupIdentity × sample`` closure.

    ``position_associations`` retain every coverage lane. ``predecessor_cells``
    retain actual method evidence; a declared no-input position has no synthetic
    cell. A merge keeps every lane, while a split remainder has no associations.
    This object carries evidence only; it has no value, Matrix, writer, or
    quantifiability authority.
    """

    processing_request_id: str
    group: CrossSampleGroupIdentity
    canonical_sample: ResolvedCanonicalSampleIdentity
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    predecessor_cells: tuple[CellObservationRef, ...]
    evidence_states: tuple[FinalCellEvidenceState, ...]
    backfill_cell: CellObservationRef | None = None

    @property
    def key(self) -> tuple[CrossSampleGroupIdentity, str]:
        """The actual final group and the sample's natural key."""
        return self.group, self.sample_key

    @property
    def group_hypothesis_id(self) -> str:
        return self.group.group_hypothesis_id

    @property
    def sample_key(self) -> str:
        return self.canonical_sample.identity.sample_key

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
        return tuple(cell.method_assessment for cell in self.effective_cells)

    @property
    def effective_cells(self) -> tuple[CellObservationRef, ...]:
        return (
            (self.backfill_cell,)
            if self.backfill_cell is not None
            else self.predecessor_cells
        )

    @property
    def direct_observations(self) -> tuple[PeakObservationRef, ...]:
        return tuple(
            cell.peak_observation
            for cell in self.effective_cells
            if cell.peak_observation is not None
        )

    @property
    def direct_hypotheses(self) -> tuple[PeakHypothesis, ...]:
        return tuple(
            cell.peak_hypothesis
            for cell in self.effective_cells
            if cell.peak_hypothesis is not None
        )

    @property
    def integrations(self) -> tuple[IntegrationResult, ...]:
        direct = tuple(
            hypothesis.integration
            for hypothesis in self.direct_hypotheses
            if hypothesis.integration is not None
        )
        ms1_only = tuple(
            observation.content.integration
            for observation in self.ms1_only_backfill_observations
        )
        return direct + ms1_only

    @property
    def ms1_only_backfill_observations(self) -> tuple[PeakObservationRef, ...]:
        """The single MS1 peak a group-bounded Backfill found without CID-NL."""

        if self.evidence_state != "ms1_only_backfill_observation":
            return ()
        return self.direct_observations

    @property
    def method_subject_ids(self) -> tuple[str, ...]:
        return tuple(
            cell.group_context.method_subject_id for cell in self.effective_cells
        )

    @property
    def position_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            association.position.key for association in self.position_associations
        )


def compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: Sequence[CellObservationRef],
    backfill_cells: Sequence[CellObservationRef] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    """Compose one exact final closure for every final Group × sample key.

    The complete Backfill coverage and CrossSample handoff are the only source
    of correlation. ``predecessor_cells`` cover every searched handoff position
    exactly once. Declared no-input positions remain explicit coverage without
    materializing method evidence.
    """

    if isinstance(predecessor_cells, (str, bytes)):
        raise CellObservationContractError(
            "predecessor cells must be a finite owner-sealed sequence"
        )
    sources = tuple(predecessor_cells)
    return _compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=sources,
        backfill_cells=tuple(backfill_cells),
    )


def _compose_final_group_cell_observation_population(
    *,
    handoff: CrossSampleAssociationLineageHandoff,
    predecessor_cells: tuple[CellObservationRef, ...],
    backfill_cells: tuple[CellObservationRef, ...] = (),
) -> tuple[FinalGroupCellObservation, ...]:
    request_id = handoff.coverage.processing_owners.request.processing_request_id
    backfilled_by_key: dict[tuple[str, str], CellObservationRef] = {}
    for backfill_ref in backfill_cells:
        key = (
            backfill_ref.content.group_hypothesis_id,
            backfill_ref.content.sample_key,
        )
        if key in backfilled_by_key:
            raise CellObservationContractError("duplicate completed backfill cell")
        backfilled_by_key[key] = backfill_ref
    positions = tuple(handoff.coverage.positions)
    source_by_position = _index_predecessor_cells(
        predecessor_cells,
        positions=positions,
        processing_request_id=request_id,
    )
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
            source_lanes = tuple(
                source_by_position[item.position.key]
                for item in associations
                if item.position.key in source_by_position
            )
            states: tuple[FinalCellEvidenceState, ...]
            recovered = backfilled_by_key.pop(
                (group.group_hypothesis_id, sample.identity.sample_key), None
            )
            if recovered is not None:
                if (
                    len(associations) != 1
                    or recovered.group_context.group_identity != group
                ):
                    raise CellObservationContractError(
                        "backfill must extend one exact predecessor"
                    )
                predecessor = associations[0].position.group_identity
                if not set(predecessor.member_subjects).issubset(group.member_subjects):
                    raise CellObservationContractError(
                        "backfill changed original group membership"
                    )
                if (
                    _member_for_sample(predecessor, sample.identity.sample_key)
                    is not None
                ):
                    raise CellObservationContractError(
                        "backfill cannot replace an existing sample member"
                    )
                if (
                    recovered.content.unassigned_support
                    or recovered.content.outside_group is not None
                ):
                    states = ("unresolved",)
                else:
                    states = (
                        recovered.method_evidence.recovered_state(
                            recovered.peak_hypothesis
                        ),
                    )
            elif not associations:
                states = ("not_searched",)
            else:
                states = tuple(
                    _derive_lane_evidence_state(
                        association=association,
                        source_cell=source_by_position.get(association.position.key),
                        final_group=group,
                        canonical_sample=sample,
                        processing_request_id=request_id,
                    )
                    for association in associations
                )
            cells.append(
                FinalGroupCellObservation(
                    processing_request_id=request_id,
                    group=group,
                    canonical_sample=sample,
                    position_associations=associations,
                    predecessor_cells=source_lanes,
                    evidence_states=states,
                    backfill_cell=recovered,
                )
            )
    if backfilled_by_key:
        raise CellObservationContractError(
            "backfill cells are outside the final population"
        )
    return tuple(sorted(cells, key=_final_cell_sort_key))


def _index_predecessor_cells(
    predecessor_cells: Sequence[CellObservationRef],
    *,
    positions: tuple[BackfillPlanningPosition, ...],
    processing_request_id: str,
) -> dict[tuple[str, str], CellObservationRef]:
    if isinstance(predecessor_cells, (str, bytes)):
        raise CellObservationContractError(
            "predecessor cells must be a finite owner-sealed sequence"
        )
    cells = tuple(predecessor_cells)
    if len({id(cell) for cell in cells}) != len(cells):
        raise CellObservationContractError(
            "predecessor cell population duplicates an exact source object"
        )
    result: dict[tuple[str, str], CellObservationRef] = {}
    positions_by_key: dict[tuple[str, str], list[BackfillPlanningPosition]] = {}
    for position in positions:
        key = (
            position.sample.identity.sample_key,
            position.group_identity.group_hypothesis_id,
        )
        positions_by_key.setdefault(key, []).append(position)
    for cell in cells:
        source_identity = cell.group_context.group_identity
        matches = tuple(
            position
            for position in positions_by_key.get(
                (
                    cell.content.sample_key,
                    source_identity.group_hypothesis_id,
                ),
                (),
            )
            if source_identity == position.group_identity
        )
        if len(matches) != 1:
            raise CellObservationContractError(
                "predecessor CellObservationRef does not bind exactly one "
                "Backfill coverage position by full group identity and sample"
            )
        position = matches[0]
        if position.key in result:
            raise CellObservationContractError(
                "predecessor CellObservationRef population duplicates a coverage "
                "position"
            )
        result[position.key] = cell
    expected_ids = {
        position.key
        for position in positions
        if position.planning_reason != "no_discovery_input"
    }
    if set(result) != expected_ids:
        raise CellObservationContractError(
            "predecessor CellObservationRef population is incomplete or extra"
        )
    return result


def _derive_lane_evidence_state(
    *,
    association: CrossSampleCoveragePositionAssociation,
    source_cell: CellObservationRef | None,
    final_group: CrossSampleGroupIdentity,
    canonical_sample: ResolvedCanonicalSampleIdentity,
    processing_request_id: str,
) -> FinalCellEvidenceState:
    if source_cell is None:
        if (
            association.position.planning_reason != "no_discovery_input"
            or association.position.sample is not canonical_sample
            or association.final_successor is not final_group
        ):
            raise CellObservationContractError(
                "missing source cell requires exact no-input coverage"
            )
        return "not_searched"
    source_identity = source_cell.group_context.group_identity
    if source_identity != association.position.group_identity:
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact predecessor group identity"
        )
    if (
        source_cell.content.sample_key
        != association.position.sample.identity.sample_key
        or association.position.sample is not canonical_sample
        or association.final_successor is not final_group
    ):
        raise CellObservationContractError(
            "source CellObservationRef crossed the exact canonical sample"
        )
    source_member = _member_for_sample(
        source_identity,
        association.position.sample.identity.sample_key,
    )
    final_member = _member_for_sample(
        final_group,
        canonical_sample.identity.sample_key,
    )
    if source_member is not None and source_member not in final_group.member_subjects:
        raise CellObservationContractError(
            "CrossSample successor dropped the exact source member evidence"
        )
    if source_cell.content.unassigned_support:
        if final_member is not None:
            raise CellObservationContractError(
                "unassigned support cannot supply a final member"
            )
        return "unresolved"
    if source_cell.content.outside_group is not None:
        if (
            source_member is not None
            or source_cell.peak_observation is not None
            or source_cell.peak_hypothesis is not None
            or association.position.planning_reason != "local_owner_not_supersedable"
        ):
            raise CellObservationContractError(
                "outside-group lane cannot supply local observation evidence"
            )
        # A merged sibling may supply a direct lane; this lane adds no value.
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
    state = source_cell.method_evidence.local_state(
        source_cell.peak_observation, source_cell.peak_hypothesis
    )
    if state == "direct_observation":
        if final_member is None:
            raise CellObservationContractError(
                "SUPPORT evidence has no exact final CrossSample member"
            )
        _direct_member_integration(
            member=final_member,
            processing_request_id=processing_request_id,
            canonical_sample=canonical_sample,
            terminal_variant="observed",
            terminal_hypothesis=source_cell.peak_hypothesis,
            method_evidence=source_cell.method_evidence,
            peak_observation=source_cell.peak_observation,
            peak_hypothesis=source_cell.peak_hypothesis,
        )
    elif final_member is not None and state != "unavailable":
        raise CellObservationContractError(
            "final CrossSample member cannot carry a HOLD or REJECT method lane"
        )
    return state


def _member_for_sample(
    group: CrossSampleGroupIdentity,
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
    "CellObservationContent",
    "CellObservationContractError",
    "CellObservationRef",
    "FinalCellEvidenceAggregate",
    "FinalCellEvidenceState",
    "FinalGroupCellObservation",
    "OutsideGroupCellRelation",
    "TerminalObservationVariant",
    "compose_final_group_cell_observation_population",
    "mint_cell_observation_ref",
]
