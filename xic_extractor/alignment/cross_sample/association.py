"""CrossSample-owned association of Backfill coverage to final group lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupIdentity,
)

CrossSamplePositionResolution: TypeAlias = Literal[
    "unchanged_identity",
    "unique_successor",
    "exact_sample_member_successor",
]


class CrossSampleAssociationContractError(CrossSampleContractError):
    """Raised when exact owner facts cannot close one association handoff."""


@dataclass(frozen=True, slots=True)
class CrossSamplePredecessorAssociation:
    """Exact `1..N` final successors for one coverage predecessor group."""

    predecessor: CrossSampleGroupIdentity
    successors: tuple[CrossSampleGroupIdentity, ...]


@dataclass(frozen=True, slots=True)
class CrossSampleCoveragePositionAssociation:
    """One exact final successor for one sealed Backfill coverage position."""

    position: BackfillPlanningPosition
    final_successor: CrossSampleGroupIdentity
    resolution: CrossSamplePositionResolution


@dataclass(frozen=True, slots=True)
class CrossSampleAssociationLineageHandoff:
    """Immutable owner handoff from exact coverage to exact final group lineage."""

    coverage: BackfillEvaluationCoverageBatch
    predecessor_associations: tuple[CrossSamplePredecessorAssociation, ...]
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    final_population: tuple[CrossSampleGroupIdentity, ...]


def build_cross_sample_association_lineage_handoff(
    *,
    coverage: BackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleGroupingResult,
) -> CrossSampleAssociationLineageHandoff:
    """Compile exact coverage positions onto exact final CrossSample groups."""

    predecessor_associations, position_associations = _compile_associations(
        coverage=coverage,
        final_receipt=final_receipt,
    )
    final_population = final_receipt.final_population
    return CrossSampleAssociationLineageHandoff(
        coverage=coverage,
        predecessor_associations=predecessor_associations,
        position_associations=position_associations,
        final_population=final_population,
    )


def _compile_associations(
    *,
    coverage: BackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleGroupingResult,
) -> tuple[
    tuple[CrossSamplePredecessorAssociation, ...],
    tuple[CrossSampleCoveragePositionAssociation, ...],
]:
    final_population = final_receipt.final_population
    predecessor_associations = tuple(
        _associate_predecessor(predecessor=group, final_population=final_population)
        for group in coverage.group_identities
    )
    predecessor_associations_by_group = {
        item.predecessor: item for item in predecessor_associations
    }
    if len(predecessor_associations_by_group) != len(predecessor_associations):
        raise CrossSampleAssociationContractError(
            "compiled predecessor associations are incomplete or duplicate"
        )
    position_associations = tuple(
        _associate_position(
            position=position,
            predecessor_association=predecessor_associations_by_group.get(
                position.group_identity
            ),
        )
        for position in coverage.positions
    )
    if len({item.position.key for item in position_associations}) != len(
        coverage.positions
    ):
        raise CrossSampleAssociationContractError(
            "compiled position population is missing, duplicate, or extra"
        )
    return predecessor_associations, position_associations


def _associate_predecessor(
    *,
    predecessor: CrossSampleGroupIdentity,
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSamplePredecessorAssociation:
    predecessor_members = set(predecessor.member_subjects)
    successors = tuple(
        sorted(
            (
                group
                for group in final_population
                if group == predecessor
                or predecessor_members.intersection(group.member_subjects)
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    if not successors:
        raise CrossSampleAssociationContractError(
            "predecessor group has no final successor"
        )
    return CrossSamplePredecessorAssociation(predecessor, successors)


def _associate_position(
    *,
    position: BackfillPlanningPosition,
    predecessor_association: CrossSamplePredecessorAssociation | None,
) -> CrossSampleCoveragePositionAssociation:
    if predecessor_association is None or (
        predecessor_association.predecessor != position.group_identity
    ):
        raise CrossSampleAssociationContractError(
            "coverage position crosses or lacks its predecessor association"
        )
    successors = predecessor_association.successors
    if len(successors) == 1:
        successor = successors[0]
        resolution: CrossSamplePositionResolution = (
            "unchanged_identity"
            if successor.group_hypothesis_id
            == position.group_identity.group_hypothesis_id
            else "unique_successor"
        )
        return CrossSampleCoveragePositionAssociation(
            position,
            successor,
            resolution,
        )
    sample_id = position.sample.identity.sample_key
    matching_members = tuple(
        member
        for member in position.group_identity.member_subjects
        if member.sample_key == sample_id
    )
    if len(matching_members) != 1:
        raise CrossSampleAssociationContractError(
            f"ambiguous split position {position.key}: "
            "canonical sample has no exact unique predecessor member"
        )
    member = matching_members[0]
    matching_successors = tuple(
        successor for successor in successors if member in successor.member_subjects
    )
    if len(matching_successors) != 1:
        raise CrossSampleAssociationContractError(
            f"ambiguous split position {position.key}: "
            "member does not select one exact final successor"
        )
    return CrossSampleCoveragePositionAssociation(
        position,
        matching_successors[0],
        "exact_sample_member_successor",
    )


__all__ = [
    "CrossSampleAssociationContractError",
    "CrossSampleAssociationLineageHandoff",
    "CrossSampleCoveragePositionAssociation",
    "CrossSamplePositionResolution",
    "CrossSamplePredecessorAssociation",
    "build_cross_sample_association_lineage_handoff",
]
