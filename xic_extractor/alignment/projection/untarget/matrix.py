"""Pure Matrix projection of completed Feature x Sample answers."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import InitVar, dataclass, field, fields
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

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
    _validate_row_key_population,
)
from xic_extractor.alignment.result.scientific_alignment import (
    FinalizedScientificAlignment,
    FinalizedScientificAlignmentContractError,
    _validate_finalized_scientific_alignment_completion_receipt,
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


def _decision_contract() -> tuple[type[object], Callable[[object], None]]:
    """Load the decision audit without coupling identity packaging to projection."""

    from xic_extractor.alignment.cell_decision.owner import (
        ProductionCellDecision,
        validate_production_cell_decision,
    )

    return ProductionCellDecision, cast(
        Callable[[object], None], validate_production_cell_decision
    )


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

    def __post_init__(self) -> None:
        if self.variant not in _DECISION_VARIANT_SET:
            raise UntargetProjectionContractError(
                "decision count uses an unsupported typed variant"
            )
        if type(self.count) is not int or self.count < 0:
            raise UntargetProjectionContractError(
                "decision count requires a non-negative integer"
            )


@dataclass(frozen=True, slots=True)
class UntargetRowDecisionCounts:
    """Complete, ordered counts for the closed cell decision variant universe."""

    by_variant: tuple[UntargetDecisionVariantCount, ...]

    def __post_init__(self) -> None:
        if type(self.by_variant) is not tuple or any(
            type(item) is not UntargetDecisionVariantCount for item in self.by_variant
        ):
            raise UntargetProjectionContractError(
                "row decision counts require exact typed count values"
            )
        if tuple(item.variant for item in self.by_variant) != _DECISION_VARIANTS:
            raise UntargetProjectionContractError(
                "row decision counts must cover every typed variant exactly once"
            )

    @property
    def total_cells(self) -> int:
        return sum(item.count for item in self.by_variant)

    @property
    def numeric_cells(self) -> int:
        return self.count_for("numeric")

    @property
    def no_write_cells(self) -> int:
        return self.total_cells - self.numeric_cells

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

    def __post_init__(self) -> None:
        if self.destination != _route_for_disposition(self.reason):
            raise UntargetProjectionContractError(
                "Untarget row route conflicts with its typed disposition reason"
            )


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
        object.__setattr__(
            self,
            "_completion_seal",
            _untarget_completion_state(self),
        )

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

    @property
    def included_rows(self) -> tuple[UntargetMatrixRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "matrix")

    @property
    def archive_rows(self) -> tuple[UntargetMatrixRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "archive")

    @property
    def archive_row_keys(self) -> tuple[UntargetRowKey, ...]:
        return tuple(row.row_key for row in self.archive_rows)


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
    _validate_row_key_population(row_keys)
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


def validate_untarget_matrix_projection(
    projection: UntargetMatrixProjection,
) -> None:
    """Revalidate one completion against its retained exact owner inputs."""

    if type(projection) is not UntargetMatrixProjection:
        raise UntargetProjectionContractError(
            "completed projection must be an exact UntargetMatrixProjection"
        )
    from xic_extractor.alignment.cell_decision.owner import decide_group_cell_population

    try:
        _validate_finalized_scientific_alignment_completion_receipt(
            projection.finalized_alignment
        )
    except FinalizedScientificAlignmentContractError as exc:
        raise UntargetProjectionContractError(str(exc)) from exc
    _validate_matrix_projection(projection)
    expected = project_untarget_matrix(
        projection.finalized_alignment,
        decide_group_cell_population(projection.finalized_alignment),
    )
    if projection != expected:
        raise UntargetProjectionContractError(
            "Untarget Matrix projection was substituted"
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


def _validate_matrix_row(row: UntargetMatrixRow) -> None:
    if type(row.row_key) is not UntargetRowKey:
        raise UntargetProjectionContractError(
            "Matrix row requires an exact UntargetRowKey"
        )
    if type(row.decisions) is not tuple:
        raise UntargetProjectionContractError(
            "Matrix row decisions must be one immutable population"
        )
    decision_type, validate_decision = _decision_contract()
    sample_ids: set[str] = set()
    for decision in row.decisions:
        if type(decision) is not decision_type:
            raise UntargetProjectionContractError(
                "Matrix row requires exact ProductionCellDecision values"
            )
        try:
            validate_decision(decision)
        except (TypeError, ValueError) as exc:
            raise UntargetProjectionContractError(str(exc)) from exc
        subject = decision.subject
        if type(subject) is not GroupCellSubject or subject.row_key is not row.row_key:
            raise UntargetProjectionContractError(
                "Matrix row decision crossed its exact row subject"
            )
        sample_id = subject.canonical_sample.identity.sample_identity_id
        if sample_id in sample_ids:
            raise UntargetProjectionContractError(
                "Matrix row contains a duplicate canonical sample decision"
            )
        sample_ids.add(sample_id)
    if type(row.counts) is not UntargetRowDecisionCounts or row.counts != (
        _counts_for_decisions(row.decisions)
    ):
        raise UntargetProjectionContractError("Matrix row counts were substituted")
    if type(row.route) is not UntargetRowRoute or row.route != UntargetRowRoute(
        destination=_route_for_disposition(row.row_key.group.disposition),
        reason=row.row_key.group.disposition,
    ):
        raise UntargetProjectionContractError("Matrix row route was substituted")


def _validate_matrix_projection(
    projection: UntargetMatrixProjection,
) -> None:
    if type(projection.rows) is not tuple or any(
        type(row) is not UntargetMatrixRow for row in projection.rows
    ):
        raise UntargetProjectionContractError(
            "completed projection requires exact immutable Matrix rows"
        )
    groups = sorted(
        projection.finalized_alignment.final_population,
        key=lambda group: group.group_hypothesis_id,
    )
    if len(projection.rows) != len(groups):
        raise UntargetProjectionContractError(
            "completed projection has missing or extra rows"
        )
    for row, group in zip(projection.rows, groups, strict=True):
        if row.row_key.group is not group:
            raise UntargetProjectionContractError(
                "completed projection crossed its exact group"
            )
        _validate_matrix_row(row)
        if tuple(item.subject.canonical_sample for item in row.decisions) != (
            projection.finalized_alignment.canonical_samples
        ):
            raise UntargetProjectionContractError(
                "completed row is missing, crossed, reordered, or partial"
            )
        if len(row.decisions) != len(projection.finalized_alignment.canonical_samples):
            raise UntargetProjectionContractError(
                "completed row lacks one decision per canonical sample"
            )
