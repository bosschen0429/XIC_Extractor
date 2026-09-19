"""CrossSample-owned association of Backfill coverage to final group lineage."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillEvaluationCoverageBatch,
    OwnerBackfillPlanningPosition,
    validate_owner_backfill_evaluation_coverage,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleContractError,
    CrossSampleGroupIdentity,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
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

    def __post_init__(self) -> None:
        if type(self.predecessor) is not CrossSampleGroupIdentity:
            raise CrossSampleAssociationContractError(
                "predecessor association requires an exact CrossSample group"
            )
        if type(self.successors) is not tuple or any(
            type(group) is not CrossSampleGroupIdentity for group in self.successors
        ):
            raise CrossSampleAssociationContractError(
                "predecessor successors must be an exact typed tuple"
            )
        if not self.successors:
            raise CrossSampleAssociationContractError(
                "predecessor has no exact final successor"
            )
        if self.successors != tuple(
            sorted(self.successors, key=lambda group: group.group_hypothesis_id)
        ):
            raise CrossSampleAssociationContractError(
                "predecessor successors are not canonical-sorted"
            )
        if len({group.group_hypothesis_id for group in self.successors}) != len(
            self.successors
        ):
            raise CrossSampleAssociationContractError(
                "predecessor association duplicates a final successor"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleCoveragePositionAssociation:
    """One exact final successor for one sealed Backfill coverage position."""

    position: OwnerBackfillPlanningPosition
    final_successor: CrossSampleGroupIdentity
    resolution: CrossSamplePositionResolution

    def __post_init__(self) -> None:
        if type(self.position) is not OwnerBackfillPlanningPosition:
            raise CrossSampleAssociationContractError(
                "position association requires an exact coverage position"
            )
        if type(self.final_successor) is not CrossSampleGroupIdentity:
            raise CrossSampleAssociationContractError(
                "position association requires an exact final successor"
            )
        if self.resolution not in {
            "unchanged_identity",
            "unique_successor",
            "exact_sample_member_successor",
        }:
            raise CrossSampleAssociationContractError(
                "position association resolution is not closed"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleAssociationLineageHandoff:
    """Immutable owner handoff from exact coverage to exact final group lineage."""

    handoff_id: str
    coverage: OwnerBackfillEvaluationCoverageBatch
    final_receipt: CrossSampleFinalReceipt
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
        _validate_handoff(self)

    @property
    def evidence_authority(self) -> str:
        return "cross_sample_association_lineage_evidence_only"

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


def build_cross_sample_association_lineage_handoff(
    *,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleFinalReceipt,
) -> CrossSampleAssociationLineageHandoff:
    """Compile exact coverage positions onto exact final CrossSample groups."""

    _validate_inputs(coverage=coverage, final_receipt=final_receipt)
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
        final_receipt=final_receipt,
        predecessor_associations=predecessor_associations,
        position_associations=position_associations,
        final_population=final_population,
        basis_bytes=basis,
        _mint_token=_HANDOFF_MINT_TOKEN,
    )


def validate_cross_sample_association_lineage_handoff(
    handoff: CrossSampleAssociationLineageHandoff,
) -> None:
    """Revalidate the complete typed handoff without reminting owner facts."""

    if type(handoff) is not CrossSampleAssociationLineageHandoff:
        raise TypeError("handoff must be an exact CrossSample association handoff")
    _validate_handoff(handoff)


def _validate_inputs(
    *,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleFinalReceipt,
) -> None:
    if type(coverage) is not OwnerBackfillEvaluationCoverageBatch:
        raise CrossSampleAssociationContractError(
            "coverage must be an exact Owner Backfill coverage batch"
        )
    try:
        validate_owner_backfill_evaluation_coverage(coverage)
    except (TypeError, ValueError) as exc:
        raise CrossSampleAssociationContractError(
            f"coverage is not owner-closed: {exc}"
        ) from exc
    if type(final_receipt) is not CrossSampleFinalReceipt:
        raise CrossSampleAssociationContractError(
            "final receipt must be an exact CrossSample final receipt"
        )
    request_id = coverage.epoch.processing_owners.request.processing_request_id
    if final_receipt.processing_request_id != request_id:
        raise CrossSampleAssociationContractError(
            "coverage and final receipt cross processing request"
        )
    _validate_initial_population(coverage=coverage, final_receipt=final_receipt)


def _validate_initial_population(
    *,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleFinalReceipt,
) -> None:
    application = final_receipt.post_mt0_application
    if application.status != "assessable" or not application.successors:
        raise CrossSampleAssociationContractError(
            "post-MT0 application has no exact successor population"
        )
    successor_ids = {group.group_hypothesis_id for group in application.successors}
    cs2_by_id = _population_by_id(final_receipt.cs2_state.population, field="CS2")
    if not successor_ids <= set(cs2_by_id):
        raise CrossSampleAssociationContractError(
            "post-MT0 successor is missing from the CS2 population"
        )
    reconstructed = {
        group_id: group
        for group_id, group in cs2_by_id.items()
        if group_id not in successor_ids
    }
    for predecessor in application.predecessors:
        if predecessor.group_hypothesis_id in reconstructed:
            raise CrossSampleAssociationContractError(
                "reconstructed initial population duplicates a predecessor"
            )
        reconstructed[predecessor.group_hypothesis_id] = predecessor
    coverage_by_id = _population_by_id(
        coverage.epoch.group_identities,
        field="coverage epoch",
    )
    if reconstructed != coverage_by_id:
        raise CrossSampleAssociationContractError(
            "coverage groups do not equal the exact reconstructed initial population"
        )


def _compile_associations(
    *,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleFinalReceipt,
) -> tuple[
    tuple[CrossSamplePredecessorAssociation, ...],
    tuple[CrossSampleCoveragePositionAssociation, ...],
]:
    final_population = final_receipt.final_population
    try:
        validate_cross_sample_group_population(final_population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleAssociationContractError(
            f"final population is not owner-closed: {exc}"
        ) from exc
    predecessor_associations = tuple(
        _associate_predecessor(predecessor=group, final_population=final_population)
        for group in coverage.epoch.group_identities
    )
    predecessor_by_id = {
        item.predecessor.group_hypothesis_id: item
        for item in predecessor_associations
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
    return CrossSamplePredecessorAssociation(predecessor, successors)


def _associate_position(
    *,
    position: OwnerBackfillPlanningPosition,
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
        successor
        for successor in successors
        if member in successor.member_subjects
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


def _validate_handoff(handoff: CrossSampleAssociationLineageHandoff) -> None:
    _validate_inputs(coverage=handoff.coverage, final_receipt=handoff.final_receipt)
    expected_predecessors, expected_positions = _compile_associations(
        coverage=handoff.coverage,
        final_receipt=handoff.final_receipt,
    )
    if handoff.predecessor_associations != expected_predecessors:
        raise CrossSampleAssociationContractError(
            "handoff predecessor associations are missing, duplicate, extra, "
            "partial, or reordered"
        )
    if handoff.position_associations != expected_positions:
        raise CrossSampleAssociationContractError(
            "handoff position associations are missing, duplicate, extra, "
            "partial, or reordered"
        )
    if handoff.final_population != handoff.final_receipt.final_population:
        raise CrossSampleAssociationContractError(
            "handoff final population crosses its final receipt"
        )
    expected_basis = _handoff_basis(
        coverage=handoff.coverage,
        final_receipt=handoff.final_receipt,
        predecessor_associations=handoff.predecessor_associations,
        position_associations=handoff.position_associations,
        final_population=handoff.final_population,
    )
    if handoff.basis_bytes != expected_basis:
        raise CrossSampleAssociationContractError(
            "handoff basis bytes do not bind its complete association population"
        )
    if handoff.handoff_id != typed_identity_digest(_HANDOFF_PREFIX, expected_basis):
        raise CrossSampleAssociationContractError(
            "handoff id does not bind its complete content"
        )


def _handoff_basis(
    *,
    coverage: OwnerBackfillEvaluationCoverageBatch,
    final_receipt: CrossSampleFinalReceipt,
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
                        successor.group_hypothesis_id
                        for successor in item.successors
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


def _population_by_id(
    population: tuple[CrossSampleGroupIdentity, ...],
    *,
    field: str,
) -> dict[str, CrossSampleGroupIdentity]:
    try:
        validate_cross_sample_group_population(population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleAssociationContractError(
            f"{field} population is not owner-closed: {exc}"
        ) from exc
    result = {group.group_hypothesis_id: group for group in population}
    if len(result) != len(population):
        raise CrossSampleAssociationContractError(
            f"{field} population duplicates a group identity"
        )
    return result


__all__ = [
    "CrossSampleAssociationContractError",
    "CrossSampleAssociationLineageHandoff",
    "CrossSampleCoveragePositionAssociation",
    "CrossSamplePositionResolution",
    "CrossSamplePredecessorAssociation",
    "build_cross_sample_association_lineage_handoff",
    "validate_cross_sample_association_lineage_handoff",
]
