"""Target-owned row completion after owner-sealed cell decisions."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment.cell_decision.owner import (
    ProductionCellDecision,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    TargetCellSubject,
    TargetPreCellProjection,
    TargetProjectionContractError,
    TargetRowBinding,
)

TargetInclusionReason: TypeAlias = Literal["fixed_target_row"]
TargetReviewQueueReason: TypeAlias = Literal[
    "unresolved_target_binding",
    "searched_no_observation",
    "not_quantifiable",
]

_POST_CELL_MINT_TOKEN = object()
_TARGET_NO_WRITE_REASONS = frozenset({"searched_no_observation", "not_quantifiable"})


@dataclass(frozen=True, slots=True)
class TargetReviewQueueReference:
    """One exact Target cell routed to the existing Review Queue."""

    subject: TargetCellSubject
    decision: ProductionCellDecision
    reasons: tuple[TargetReviewQueueReason, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_CELL_MINT_TOKEN:
            raise TargetProjectionContractError(
                "Target Review Queue references must be minted by Target projection"
            )

    @property
    def row_binding(self) -> TargetRowBinding:
        return self.subject.row_binding


@dataclass(frozen=True, slots=True)
class TargetPostCellProjection:
    """Closed Target row policy and exact cell-decision population."""

    pre_cell_projection: TargetPreCellProjection
    decisions: tuple[ProductionCellDecision, ...]
    accepted_cell_count: int
    no_write_count: int
    review_queue_references: tuple[TargetReviewQueueReference, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_CELL_MINT_TOKEN:
            raise TargetProjectionContractError(
                "Target post-cell projection must be minted by Target projection"
            )

    @property
    def row_binding(self) -> TargetRowBinding:
        return self.pre_cell_projection.row_binding

    @property
    def cell_subjects(self) -> tuple[TargetCellSubject, ...]:
        return self.pre_cell_projection.cell_subjects

    @property
    def subjects(self) -> tuple[TargetCellSubject, ...]:
        return self.cell_subjects

    @property
    def include_row(self) -> Literal[True]:
        return True

    @property
    def inclusion_reason(self) -> TargetInclusionReason:
        return "fixed_target_row"

    @property
    def decision_count(self) -> int:
        return len(self.decisions)

    @property
    def review_queue_count(self) -> int:
        return len(self.review_queue_references)


def project_target_post_cell(
    pre_cell_projection: TargetPreCellProjection,
    decisions: tuple[ProductionCellDecision, ...],
) -> TargetPostCellProjection:
    """Complete one fixed Target row from every exact owner-sealed decision."""

    owner_subjects = pre_cell_projection.cell_subjects
    ordered_decisions = _bind_complete_decision_population(
        decisions,
        owner_subjects=owner_subjects,
    )
    accepted_cell_count, no_write_count = _decision_counts(ordered_decisions)
    review_queue_references = _build_review_queue_references(
        pre_cell_projection.row_binding,
        ordered_decisions,
    )
    return TargetPostCellProjection(
        pre_cell_projection=pre_cell_projection,
        decisions=ordered_decisions,
        accepted_cell_count=accepted_cell_count,
        no_write_count=no_write_count,
        review_queue_references=review_queue_references,
        _mint_token=_POST_CELL_MINT_TOKEN,
    )


def _bind_complete_decision_population(
    decisions: tuple[ProductionCellDecision, ...],
    *,
    owner_subjects: tuple[TargetCellSubject, ...],
) -> tuple[ProductionCellDecision, ...]:
    if type(decisions) is not tuple:
        raise TargetProjectionContractError(
            "Target decisions must be an immutable tuple"
        )
    if any(type(decision) is not ProductionCellDecision for decision in decisions):
        raise TargetProjectionContractError(
            "Target decision population contains a partial or non-owner decision"
        )
    decision_by_subject: dict[int, ProductionCellDecision] = {}
    for decision in decisions:
        if type(decision.subject) is not TargetCellSubject:
            raise TargetProjectionContractError(
                "Target decision population contains a non-Target subject"
            )
        subject_key = id(decision.subject)
        if subject_key in decision_by_subject:
            raise TargetProjectionContractError(
                "Target decision population contains a duplicate subject decision"
            )
        decision_by_subject[subject_key] = decision

    owner_keys = tuple(id(subject) for subject in owner_subjects)
    if set(decision_by_subject) != set(owner_keys):
        raise TargetProjectionContractError(
            "Target decision population is missing, crossed, extra, or substituted"
        )
    return tuple(decision_by_subject[key] for key in owner_keys)


def _decision_counts(
    decisions: tuple[ProductionCellDecision, ...],
) -> tuple[int, int]:
    accepted = sum(decision.variant == "numeric" for decision in decisions)
    return accepted, len(decisions) - accepted


def _build_review_queue_references(
    row_binding: TargetRowBinding,
    decisions: tuple[ProductionCellDecision, ...],
) -> tuple[TargetReviewQueueReference, ...]:
    references: list[TargetReviewQueueReference] = []
    for decision in decisions:
        reasons = _review_reasons(row_binding, decision)
        if not reasons:
            continue
        subject = cast(TargetCellSubject, decision.subject)
        references.append(
            TargetReviewQueueReference(
                subject=subject,
                decision=decision,
                reasons=reasons,
                _mint_token=_POST_CELL_MINT_TOKEN,
            )
        )
    return tuple(references)


def _review_reasons(
    row_binding: TargetRowBinding,
    decision: ProductionCellDecision,
) -> tuple[TargetReviewQueueReason, ...]:
    reasons: list[TargetReviewQueueReason] = []
    if row_binding.variant == "unresolved_many":
        reasons.append("unresolved_target_binding")
    if decision.variant in _TARGET_NO_WRITE_REASONS:
        reasons.append(cast(TargetReviewQueueReason, decision.variant))
    elif decision.variant != "numeric":
        raise TargetProjectionContractError(
            "Target decision uses an unsupported projection variant"
        )
    return tuple(reasons)
