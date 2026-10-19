"""Pure Matrix projection of completed Feature x Sample answers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field, fields
from typing import TYPE_CHECKING, Literal, TypeAlias

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleLineageEdge,
)
from xic_extractor.alignment.projection.untarget.identity import (
    _PROJECTION_MINT_TOKEN,
    GroupCellSubject,
    UntargetProjectionContractError,
    UntargetRowKey,
    _mint_untarget_row_key,
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
_MATRIX_MINT_TOKEN = object()


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

    row_key: UntargetRowKey
    decisions: tuple[ProductionCellDecision, ...]
    counts: UntargetRowDecisionCounts
    route: UntargetRowRoute
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MATRIX_MINT_TOKEN:
            raise UntargetProjectionContractError(
                "Matrix row must be minted by its owner entry point"
            )


@dataclass(frozen=True, slots=True)
class UntargetMatrixProjection:
    """Immutable Untarget completion handoff for result finalization."""

    finalized_alignment: FinalizedScientificAlignment
    rows: tuple[UntargetMatrixRow, ...]
    _completion_seal: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _MATRIX_MINT_TOKEN:
            raise UntargetProjectionContractError(
                "Matrix projection must be minted by its owner entry point"
            )
        object.__setattr__(self, "_completion_seal", _untarget_completion_state(self))

    def __copy__(self) -> UntargetMatrixProjection:
        # Preserve the live-substitution oracle independently of spool restoration.
        clone = object.__new__(type(self))
        for owner_field in fields(self):
            object.__setattr__(clone, owner_field.name, getattr(self, owner_field.name))
        return clone

    def __getstate__(self) -> tuple[object, ...]:
        """Export a verified completion for private same-process storage."""
        if not hasattr(self, "_completion_seal"):
            raise UntargetProjectionContractError("Untarget projection is unsealed")
        validate_untarget_matrix_completion_receipt(self)
        return (self.finalized_alignment, self.rows)

    def __setstate__(self, state: tuple[object, ...]) -> None:
        """Restore direct bindings from the private same-process spool."""
        if (
            hasattr(self, "_completion_seal")
            or type(state) is not tuple
            or len(state) != 2
        ):
            raise UntargetProjectionContractError(
                "invalid Untarget projection restoration"
            )
        object.__setattr__(self, "finalized_alignment", state[0])
        object.__setattr__(self, "rows", state[1])
        object.__setattr__(self, "_completion_seal", _untarget_completion_state(self))

    @property
    def lineage(self) -> tuple[CrossSampleLineageEdge, ...]:
        return self.finalized_alignment.lineage

    @property
    def decisions(self) -> tuple[ProductionCellDecision, ...]:
        return tuple(decision for row in self.rows for decision in row.decisions)


def project_untarget_matrix(
    finalized_alignment: FinalizedScientificAlignment,
    decisions: Mapping[str, tuple[CellDecisionPayload, IntegrationResult | None]],
) -> UntargetMatrixProjection:
    """Assign output identities, sample order, routes and counts exactly once."""

    from xic_extractor.alignment.cell_decision.owner import _mint_decision

    observations = finalized_alignment.cell_observations
    if set(decisions) != {item.cell_observation_id for item in observations}:
        raise UntargetProjectionContractError(
            "Group decision population is missing, extra, or crossed"
        )
    by_position = {
        (item.group_hypothesis_id, item.canonical_sample_identity_id): item
        for item in observations
    }
    row_keys = tuple(
        _mint_untarget_row_key(group)
        for group in sorted(
            finalized_alignment.final_population,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    rows = []
    for row_key in row_keys:
        members = {
            member.canonical_sample_identity_id: member
            for member in row_key.group.member_subjects
        }
        projected = []
        for sample in finalized_alignment.canonical_samples:
            sample_id = sample.identity.sample_identity_id
            observation = by_position[row_key.group_hypothesis_id, sample_id]
            payload, integration = decisions[observation.cell_observation_id]
            projected.append(
                _mint_decision(
                    subject=GroupCellSubject(
                        row_key,
                        sample,
                        members.get(sample_id),
                        _mint_token=_PROJECTION_MINT_TOKEN,
                    ),
                    evidence=observation,
                    payload=payload,
                    selected_integration=integration,
                )
            )
        rows.append(_mint_matrix_row(row_key, decisions=tuple(projected)))
    return UntargetMatrixProjection(
        finalized_alignment=finalized_alignment,
        rows=tuple(rows),
        _mint_token=_MATRIX_MINT_TOKEN,
    )


def validate_untarget_matrix_completion_receipt(
    projection: UntargetMatrixProjection,
) -> None:
    """Verify retained completion fields without reopening projection policy."""

    if type(projection) is not UntargetMatrixProjection:
        raise UntargetProjectionContractError(
            "completed Untarget receipt requires an exact UntargetMatrixProjection"
        )
    try:
        current_state = _untarget_completion_state(projection)
    except (AttributeError, TypeError) as exc:
        raise UntargetProjectionContractError(
            "completed Untarget projection receipt was substituted or partial"
        ) from exc
    if (
        type(projection._completion_seal) is not tuple
        or projection._completion_seal != current_state
    ):
        raise UntargetProjectionContractError(
            "completed Untarget projection receipt was substituted or partial"
        )


def _untarget_completion_state(
    projection: UntargetMatrixProjection,
) -> tuple[object, ...]:
    return (
        id(projection),
        id(projection.finalized_alignment),
        id(projection.rows),
    )


def _mint_matrix_row(
    row_key: UntargetRowKey,
    *,
    decisions: tuple[ProductionCellDecision, ...],
) -> UntargetMatrixRow:
    return UntargetMatrixRow(
        row_key=row_key,
        decisions=decisions,
        counts=_counts_for_decisions(decisions),
        route=UntargetRowRoute(
            destination=_route_for_disposition(row_key.group.disposition),
            reason=row_key.group.disposition,
        ),
        _mint_token=_MATRIX_MINT_TOKEN,
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
