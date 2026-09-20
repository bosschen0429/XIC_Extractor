"""Untarget projection closure after owner-sealed Group cell decisions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import InitVar, dataclass, field, fields
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupDisposition,
    CrossSampleLineageEdge,
)
from xic_extractor.alignment.projection.untarget.pre_cell import (
    GroupCellSubject,
    UntargetPreCellProjection,
    UntargetProjectionContractError,
    UntargetRowKey,
    project_untarget_pre_cell,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.cell_decision.owner import (
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
_POST_CELL_MINT_TOKEN = object()


def _decision_contract() -> tuple[type[object], Callable[[object], None]]:
    """Import #308 lazily so its import of ``untarget.pre_cell`` stays acyclic."""

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
        "Untarget post-cell projection received an unsupported group disposition"
    )


@dataclass(frozen=True, slots=True)
class UntargetDecisionVariantCount:
    """Count for one closed #308 decision variant."""

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
    """Complete, ordered counts for the closed #308 variant universe."""

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
class UntargetPostCellRow:
    """One exact Untarget row closed over every canonical-sample decision."""

    row_key: UntargetRowKey
    decisions: tuple[ProductionCellDecision, ...]
    counts: UntargetRowDecisionCounts
    route: UntargetRowRoute
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_CELL_MINT_TOKEN:
            raise UntargetProjectionContractError(
                "post-cell row must be minted by its owner entry point"
            )


@dataclass(frozen=True, slots=True)
class UntargetPostCellProjection:
    """Immutable Untarget completion handoff for result finalization."""

    pre_cell_projection: UntargetPreCellProjection
    rows: tuple[UntargetPostCellRow, ...]
    _completion_seal: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_CELL_MINT_TOKEN:
            raise UntargetProjectionContractError(
                "post-cell projection must be minted by its owner entry point"
            )
        object.__setattr__(
            self,
            "_completion_seal",
            _untarget_completion_state(self),
        )

    def __copy__(self) -> UntargetPostCellProjection:
        # Preserve the live-substitution oracle independently of spool restoration.
        clone = object.__new__(type(self))
        for owner_field in fields(self):
            object.__setattr__(clone, owner_field.name, getattr(self, owner_field.name))
        return clone

    def __getstate__(self) -> tuple[object, ...]:
        """Export a verified completion for private same-process storage."""
        if not hasattr(self, "_completion_seal"):
            raise UntargetProjectionContractError("Untarget projection is unsealed")
        validate_untarget_post_cell_completion_receipt(self)
        return (self.pre_cell_projection, self.rows)

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
        object.__setattr__(self, "pre_cell_projection", state[0])
        object.__setattr__(self, "rows", state[1])
        object.__setattr__(self, "_completion_seal", _untarget_completion_state(self))

    @property
    def lineage(self) -> tuple[CrossSampleLineageEdge, ...]:
        return self.pre_cell_projection.lineage

    @property
    def decisions(self) -> tuple[ProductionCellDecision, ...]:
        return tuple(decision for row in self.rows for decision in row.decisions)

    @property
    def included_rows(self) -> tuple[UntargetPostCellRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "matrix")

    @property
    def archive_rows(self) -> tuple[UntargetPostCellRow, ...]:
        return tuple(row for row in self.rows if row.route.destination == "archive")

    @property
    def archive_row_keys(self) -> tuple[UntargetRowKey, ...]:
        return tuple(row.row_key for row in self.archive_rows)


def project_untarget_post_cell(
    pre_cell_projection: UntargetPreCellProjection,
    decisions: Sequence[ProductionCellDecision],
) -> UntargetPostCellProjection:
    """Bind owner decisions once to their exact projection subjects and rows.

    The decision owner has already selected and sealed each payload. Deep
    reconstruction is reserved for the explicit projection audit entry point.
    """

    if type(pre_cell_projection) is not UntargetPreCellProjection:
        raise UntargetProjectionContractError(
            "post-cell projection requires an exact UntargetPreCellProjection"
        )
    ordered_decisions = _bind_exact_decisions(
        pre_cell_projection,
        decisions=decisions,
    )
    decisions_by_row: dict[str, list[ProductionCellDecision]] = {}
    for decision in ordered_decisions:
        row_id = cast(GroupCellSubject, decision.subject).row_key.untarget_row_key_id
        decisions_by_row.setdefault(row_id, []).append(decision)
    rows = tuple(
        _mint_post_cell_row(
            row_key,
            decisions=tuple(decisions_by_row.get(row_key.untarget_row_key_id, ())),
        )
        for row_key in pre_cell_projection.row_keys
    )
    return UntargetPostCellProjection(
        pre_cell_projection=pre_cell_projection,
        rows=rows,
        _mint_token=_POST_CELL_MINT_TOKEN,
    )


def validate_untarget_post_cell_projection(
    projection: UntargetPostCellProjection,
) -> None:
    """Revalidate one completion against its retained exact owner inputs."""

    if type(projection) is not UntargetPostCellProjection:
        raise UntargetProjectionContractError(
            "completed projection must be an exact UntargetPostCellProjection"
        )
    _validate_post_cell_projection(projection)
    expected = project_untarget_post_cell(
        projection.pre_cell_projection,
        projection.decisions,
    )
    if projection != expected:
        raise UntargetProjectionContractError(
            "Untarget post-cell projection was substituted"
        )


def validate_untarget_post_cell_completion_receipt(
    projection: UntargetPostCellProjection,
) -> None:
    """Verify retained completion fields without reopening projection policy."""

    if type(projection) is not UntargetPostCellProjection:
        raise UntargetProjectionContractError(
            "completed Untarget receipt requires an exact UntargetPostCellProjection"
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
    projection: UntargetPostCellProjection,
) -> tuple[object, ...]:
    return (
        id(projection),
        id(projection.pre_cell_projection),
        id(projection.rows),
    )


def _validate_pre_cell_projection(
    projection: UntargetPreCellProjection,
) -> None:
    if type(projection) is not UntargetPreCellProjection:
        raise UntargetProjectionContractError(
            "post-cell projection requires an exact UntargetPreCellProjection"
        )
    expected = project_untarget_pre_cell(projection.finalized_alignment)
    if projection != expected:
        raise UntargetProjectionContractError(
            "Untarget pre-cell projection is partial, crossed, or substituted"
        )


def _bind_exact_decisions(
    projection: UntargetPreCellProjection,
    *,
    decisions: Sequence[ProductionCellDecision],
) -> tuple[ProductionCellDecision, ...]:
    try:
        requested = tuple(decisions)
    except TypeError as exc:
        raise UntargetProjectionContractError(
            "Group decisions must be one finite complete population"
        ) from exc
    # Subjects are private-minted by pre_cell. Exact object binding is sufficient;
    # reserializing their semantic IDs would repeat the upstream identity work.
    owner_subjects = projection.cell_subjects
    owner_ids = {id(item) for item in owner_subjects}
    decision_type, _validate_decision = _decision_contract()
    by_subject: dict[int, ProductionCellDecision] = {}
    for decision in requested:
        if type(decision) is not decision_type:
            raise UntargetProjectionContractError(
                "post-cell projection requires exact ProductionCellDecision values"
            )
        subject = decision.subject
        if type(subject) is not GroupCellSubject:
            raise UntargetProjectionContractError(
                "Untarget post-cell decision crossed the Target subject lane"
            )
        owner_id = id(subject)
        if owner_id not in owner_ids:
            raise UntargetProjectionContractError(
                "Group decision crossed or substituted its exact projection subject"
            )
        if owner_id in by_subject:
            raise UntargetProjectionContractError(
                "Group decision population contains a duplicate subject"
            )
        by_subject[owner_id] = decision

    if len(requested) != len(owner_subjects):
        raise UntargetProjectionContractError(
            "Group decision population is missing, partial, or extra"
        )
    return tuple(by_subject[id(item)] for item in owner_subjects)


def _mint_post_cell_row(
    row_key: UntargetRowKey,
    *,
    decisions: tuple[ProductionCellDecision, ...],
) -> UntargetPostCellRow:
    return UntargetPostCellRow(
        row_key=row_key,
        decisions=decisions,
        counts=_counts_for_decisions(decisions),
        route=UntargetRowRoute(
            destination=_route_for_disposition(row_key.group.disposition),
            reason=row_key.group.disposition,
        ),
        _mint_token=_POST_CELL_MINT_TOKEN,
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


def _validate_post_cell_row(row: UntargetPostCellRow) -> None:
    if type(row.row_key) is not UntargetRowKey:
        raise UntargetProjectionContractError(
            "post-cell row requires an exact UntargetRowKey"
        )
    if type(row.decisions) is not tuple:
        raise UntargetProjectionContractError(
            "post-cell row decisions must be one immutable population"
        )
    decision_type, validate_decision = _decision_contract()
    sample_ids: set[str] = set()
    for decision in row.decisions:
        if type(decision) is not decision_type:
            raise UntargetProjectionContractError(
                "post-cell row requires exact ProductionCellDecision values"
            )
        try:
            validate_decision(decision)
        except (TypeError, ValueError) as exc:
            raise UntargetProjectionContractError(str(exc)) from exc
        subject = decision.subject
        if type(subject) is not GroupCellSubject or subject.row_key is not row.row_key:
            raise UntargetProjectionContractError(
                "post-cell row decision crossed its exact row subject"
            )
        sample_id = subject.canonical_sample.identity.sample_identity_id
        if sample_id in sample_ids:
            raise UntargetProjectionContractError(
                "post-cell row contains a duplicate canonical sample decision"
            )
        sample_ids.add(sample_id)
    if type(row.counts) is not UntargetRowDecisionCounts or row.counts != (
        _counts_for_decisions(row.decisions)
    ):
        raise UntargetProjectionContractError("post-cell row counts were substituted")
    if type(row.route) is not UntargetRowRoute or row.route != UntargetRowRoute(
        destination=_route_for_disposition(row.row_key.group.disposition),
        reason=row.row_key.group.disposition,
    ):
        raise UntargetProjectionContractError("post-cell row route was substituted")


def _validate_post_cell_projection(
    projection: UntargetPostCellProjection,
) -> None:
    _validate_pre_cell_projection(projection.pre_cell_projection)
    if type(projection.rows) is not tuple or any(
        type(row) is not UntargetPostCellRow for row in projection.rows
    ):
        raise UntargetProjectionContractError(
            "completed projection requires exact immutable post-cell rows"
        )
    row_keys = projection.pre_cell_projection.row_keys
    if len(projection.rows) != len(row_keys):
        raise UntargetProjectionContractError(
            "completed projection has missing or extra rows"
        )
    for row, row_key in zip(projection.rows, row_keys, strict=True):
        if row.row_key is not row_key:
            raise UntargetProjectionContractError(
                "completed projection crossed or substituted an exact row key"
            )
        _validate_post_cell_row(row)
        expected_subjects = tuple(
            subject
            for subject in projection.pre_cell_projection.cell_subjects
            if subject.row_key is row_key
        )
        if tuple(item.subject for item in row.decisions) != expected_subjects:
            raise UntargetProjectionContractError(
                "completed row is missing, crossed, reordered, or partial"
            )
        if len(row.decisions) != len(projection.pre_cell_projection.canonical_samples):
            raise UntargetProjectionContractError(
                "completed row lacks one decision per canonical sample"
            )
