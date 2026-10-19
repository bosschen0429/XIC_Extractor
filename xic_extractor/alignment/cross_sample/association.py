"""CrossSample-owned association of Backfill coverage to final group lineage."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
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
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

CrossSamplePositionResolution: TypeAlias = Literal[
    "unchanged_identity",
    "unique_successor",
    "exact_sample_member_successor",
]

_HANDOFF_PREFIX = "xic.cross-sample-association-lineage:"
_HANDOFF_MINT_TOKEN = object()


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

    handoff_id: str
    coverage: BackfillEvaluationCoverageBatch
    predecessor_associations: tuple[CrossSamplePredecessorAssociation, ...]
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...]
    final_population: tuple[CrossSampleGroupIdentity, ...]
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _HANDOFF_MINT_TOKEN:
            raise CrossSampleAssociationContractError(
                "association handoff must be minted by CrossSample"
            )


def build_cross_sample_association_lineage_handoff(
    *,
    coverage: BackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleGroupingResult,
) -> CrossSampleAssociationLineageHandoff:
    """Compile exact coverage positions onto exact final CrossSample groups."""

    if coverage.epoch.processing_owners.request.processing_request_id != (
        final_receipt.processing_request_id
    ):
        raise CrossSampleAssociationContractError(
            "coverage and final receipt cross processing request"
        )
    predecessor_associations, position_associations = _compile_associations(
        coverage=coverage,
        final_receipt=final_receipt,
    )
    final_population = final_receipt.final_population
    basis = _handoff_basis(
        coverage=coverage,
        final_receipt=final_receipt,
        predecessor_associations=predecessor_associations,
        position_associations=position_associations,
        final_population=final_population,
    )
    return CrossSampleAssociationLineageHandoff(
        handoff_id=typed_identity_digest(_HANDOFF_PREFIX, basis),
        coverage=coverage,

        predecessor_associations=predecessor_associations,
        position_associations=position_associations,
        final_population=final_population,
        basis_bytes=basis,
        _mint_token=_HANDOFF_MINT_TOKEN,
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
        for group in coverage.epoch.group_identities
    )
    predecessor_by_id = {
        item.predecessor.group_hypothesis_id: item for item in predecessor_associations
    }
    if len(predecessor_by_id) != len(predecessor_associations):
        raise CrossSampleAssociationContractError(
            "compiled predecessor associations are incomplete or duplicate"
        )
    position_associations = tuple(
        _associate_position(
            position=position,
            predecessor_association=predecessor_by_id.get(
                position.group_identity.group_hypothesis_id
            ),
        )
        for position in coverage.epoch.positions
    )
    if len({item.position.position_id for item in position_associations}) != len(
        coverage.epoch.positions
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
    predecessor_members = {
        member.member_subject_id for member in predecessor.member_subjects
    }
    successors = tuple(
        sorted(
            (
                group
                for group in final_population
                if group.group_hypothesis_id == predecessor.group_hypothesis_id
                or predecessor_members.intersection(
                    member.member_subject_id for member in group.member_subjects
                )
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
    sample_id = position.sample.identity.sample_identity_id
    matching_members = tuple(
        member
        for member in position.group_identity.member_subjects
        if member.canonical_sample_identity_id == sample_id
    )
    if len(matching_members) != 1:
        raise CrossSampleAssociationContractError(
            f"ambiguous split position {position.position_id}: "
            "canonical sample has no exact unique predecessor member"
        )
    member = matching_members[0]
    matching_successors = tuple(
        successor for successor in successors if member in successor.member_subjects
    )
    if len(matching_successors) != 1:
        raise CrossSampleAssociationContractError(
            f"ambiguous split position {position.position_id}: "
            "member does not select one exact final successor"
        )
    return CrossSampleCoveragePositionAssociation(
        position,
        matching_successors[0],
        "exact_sample_member_successor",
    )


def _handoff_basis(
    *,
    coverage: BackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleGroupingResult,
    predecessor_associations: tuple[CrossSamplePredecessorAssociation, ...],
    position_associations: tuple[CrossSampleCoveragePositionAssociation, ...],
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_association_lineage",
            "coverage_id": coverage.coverage_id,
            "final_receipt_id": final_receipt.receipt_id,
            "predecessor_associations": [
                {
                    "predecessor_group_hypothesis_id": (
                        item.predecessor.group_hypothesis_id
                    ),
                    "final_successor_group_hypothesis_ids": [
                        successor.group_hypothesis_id for successor in item.successors
                    ],
                }
                for item in predecessor_associations
            ],
            "position_associations": [
                {
                    "position_id": item.position.position_id,
                    "final_successor_group_hypothesis_id": (
                        item.final_successor.group_hypothesis_id
                    ),
                    "resolution": item.resolution,
                }
                for item in position_associations
            ],
            "final_population_group_hypothesis_ids": [
                group.group_hypothesis_id for group in final_population
            ],
        }
    )


__all__ = [
    "CrossSampleAssociationContractError",
    "CrossSampleAssociationLineageHandoff",
    "CrossSampleCoveragePositionAssociation",
    "CrossSamplePositionResolution",
    "CrossSamplePredecessorAssociation",
    "build_cross_sample_association_lineage_handoff",
]
