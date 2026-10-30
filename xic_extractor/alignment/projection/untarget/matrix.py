"""Pure Matrix projection of completed Feature x Sample answers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleGroupIdentity,
    CrossSampleLineageEdge,
)
from xic_extractor.alignment.projection.untarget.identity import (
    GroupCellSubject,
    UntargetProjectionContractError,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import (
        CellDecisionPayload,
        ProductionCellDecision,
    )


UntargetDecisionVariant: TypeAlias = Literal[
    "numeric",
    "not_searched",
    "searched_no_observation",
    "unresolved",
    "rejected_observation",
    "not_quantifiable",
    "unavailable",
]
UntargetRowDestination: TypeAlias = Literal["matrix", "archive"]

_DECISION_VARIANTS: tuple[UntargetDecisionVariant, ...] = (
    "numeric",
    "not_searched",
    "searched_no_observation",
    "unresolved",
    "rejected_observation",
    "not_quantifiable",
    "unavailable",
)
_DECISION_VARIANT_SET = frozenset(_DECISION_VARIANTS)


def _route_for_disposition(
    disposition: CrossSampleGroupDisposition,
) -> UntargetRowDestination:
    if disposition == "established":
        return "matrix"
    if disposition in {"provisional", "unresolved", "rejected"}:
        return "archive"
    raise UntargetProjectionContractError(
        "Untarget Matrix projection received an unsupported group disposition"
    )


@dataclass(frozen=True, slots=True)
class UntargetDecisionVariantCount:
    """Count for one closed cell decision variant."""

    variant: UntargetDecisionVariant
    count: int


@dataclass(frozen=True, slots=True)
class UntargetRowDecisionCounts:
    """Complete, ordered counts for the closed cell decision variant universe."""

    by_variant: tuple[UntargetDecisionVariantCount, ...]

    @property
    def total_cells(self) -> int:
        return sum(item.count for item in self.by_variant)

    @property
    def numeric_cells(self) -> int:
        return self.count_for("numeric")

    def count_for(self, variant: UntargetDecisionVariant) -> int:
        if variant not in _DECISION_VARIANT_SET:
            raise UntargetProjectionContractError(
                "row decision count lookup uses an unsupported typed variant"
            )
        return next(item.count for item in self.by_variant if item.variant == variant)


@dataclass(frozen=True, slots=True)
class UntargetRowRoute:
    """One explicit Matrix/archive route justified by group disposition."""

    destination: UntargetRowDestination
    reason: CrossSampleGroupDisposition


@dataclass(frozen=True, slots=True)
class UntargetMatrixRow:
    """One exact Untarget row closed over every canonical-sample decision."""

    group: CrossSampleGroupIdentity
    decisions: tuple[ProductionCellDecision, ...]
    counts: UntargetRowDecisionCounts
    route: UntargetRowRoute


@dataclass(frozen=True, slots=True)
class UntargetMatrixProjection:
    """Immutable Untarget completion handoff for result finalization."""

    finalized_alignment: FinalizedScientificAlignment
    rows: tuple[UntargetMatrixRow, ...]

    @property
    def lineage(self) -> tuple[CrossSampleLineageEdge, ...]:
        return self.finalized_alignment.lineage

    @property
    def decisions(self) -> tuple[ProductionCellDecision, ...]:
        return tuple(decision for row in self.rows for decision in row.decisions)

    @property
    def included_rows(self) -> tuple[UntargetMatrixRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "matrix")

    @property
    def archive_rows(self) -> tuple[UntargetMatrixRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "archive")


def project_untarget_matrix(
    finalized_alignment: FinalizedScientificAlignment,
    decisions: Mapping[
        tuple[CrossSampleGroupIdentity, str],
        tuple[CellDecisionPayload, IntegrationResult | None],
    ],
) -> UntargetMatrixProjection:
    """Project sample order, routes and counts from the finalized groups."""

    from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision

    observations = finalized_alignment.cell_observations
    if set(decisions) != {item.key for item in observations}:
        raise UntargetProjectionContractError(
            "Group decision population is missing, extra, or crossed"
        )
    by_position = {
        (item.group_hypothesis_id, item.sample_key): item for item in observations
    }
    groups = sorted(
        finalized_alignment.final_population,
        key=lambda group: group.group_hypothesis_id,
    )
    rows = []
    for group in groups:
        members = {member.sample_key: member for member in group.member_subjects}
        projected = []
        for sample in finalized_alignment.canonical_samples:
            sample_id = sample.identity.sample_key
            observation = by_position[group.group_hypothesis_id, sample_id]
            payload, integration = decisions[observation.key]
            projected.append(
                ProductionCellDecision(
                    subject=GroupCellSubject(
                        group,
                        sample,
                        members.get(sample_id),
                    ),
                    evidence=observation,
                    payload=payload,
                    selected_integration=integration,
                )
            )
        rows.append(_mint_matrix_row(group, decisions=tuple(projected)))
    return UntargetMatrixProjection(
        finalized_alignment=finalized_alignment,
        rows=tuple(rows),
    )


def _mint_matrix_row(
    group: CrossSampleGroupIdentity,
    *,
    decisions: tuple[ProductionCellDecision, ...],
) -> UntargetMatrixRow:
    return UntargetMatrixRow(
        group=group,
        decisions=decisions,
        counts=_counts_for_decisions(decisions),
        route=UntargetRowRoute(
            destination=_route_for_disposition(group.disposition),
            reason=group.disposition,
        ),
    )


def _counts_for_decisions(
    decisions: Sequence[ProductionCellDecision],
) -> UntargetRowDecisionCounts:
    variants = Counter(item.variant for item in decisions)
    if not set(variants).issubset(_DECISION_VARIANT_SET):
        raise UntargetProjectionContractError(
            "Group decision uses an unsupported typed variant"
        )
    return UntargetRowDecisionCounts(
        by_variant=tuple(
            UntargetDecisionVariantCount(variant, variants[variant])
            for variant in _DECISION_VARIANTS
        )
    )
