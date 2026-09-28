"""Target-owned row completion after owner-sealed cell decisions."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias, cast

from xic_extractor.alignment._completion_receipt import (
    retained_owner_digest,
)
from xic_extractor.alignment.cell_decision import (
    CellDecisionContractError,
    ProductionCellDecision,
    validate_production_cell_decision,
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
        _validate_review_reference(self)

    @property
    def row_binding(self) -> TargetRowBinding:
        return self.subject.row_binding


@dataclass(frozen=True, slots=True)
class TargetPostCellProjection:
    """Closed Target row policy and exact cell-decision population for #311."""

    pre_cell_projection: TargetPreCellProjection
    decisions: tuple[ProductionCellDecision, ...]
    accepted_cell_count: int
    no_write_count: int
    review_queue_references: tuple[TargetReviewQueueReference, ...]
    _completion_seal: tuple[object, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_CELL_MINT_TOKEN:
            raise TargetProjectionContractError(
                "Target post-cell projection must be minted by Target projection"
            )
        validate_target_post_cell_projection(self)
        object.__setattr__(
            self,
            "_completion_seal",
            _target_completion_state(self),
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

    owner_subjects = _validate_pre_cell_identity_boundary(pre_cell_projection)
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


def validate_target_post_cell_projection(
    projection: TargetPostCellProjection,
) -> None:
    """Revalidate a completed Target projection against its retained owners."""

    if type(projection) is not TargetPostCellProjection:
        raise TargetProjectionContractError(
            "completed Target projection must be an exact TargetPostCellProjection"
        )
    owner_subjects = _validate_pre_cell_identity_boundary(
        projection.pre_cell_projection
    )
    ordered_decisions = _bind_complete_decision_population(
        projection.decisions,
        owner_subjects=owner_subjects,
    )
    if any(
        actual is not expected
        for actual, expected in zip(
            projection.decisions,
            ordered_decisions,
            strict=True,
        )
    ):
        raise TargetProjectionContractError(
            "completed Target decisions are not in canonical subject order"
        )

    expected_accepted, expected_no_write = _decision_counts(ordered_decisions)
    if (
        projection.accepted_cell_count != expected_accepted
        or projection.no_write_count != expected_no_write
    ):
        raise TargetProjectionContractError(
            "completed Target counts do not match owner decisions"
        )

    if type(projection.review_queue_references) is not tuple:
        raise TargetProjectionContractError(
            "Target Review Queue references must be an immutable tuple"
        )
    expected_references = _build_review_queue_references(
        projection.row_binding,
        ordered_decisions,
    )
    if len(projection.review_queue_references) != len(expected_references):
        raise TargetProjectionContractError(
            "Target Review Queue references do not match owner policy"
        )
    for actual, expected in zip(
        projection.review_queue_references,
        expected_references,
        strict=True,
    ):
        if (
            type(actual) is not TargetReviewQueueReference
            or actual.subject is not expected.subject
            or actual.decision is not expected.decision
            or actual.reasons != expected.reasons
        ):
            raise TargetProjectionContractError(
                "Target Review Queue reference was crossed or substituted"
            )


def validate_target_post_cell_completion_receipt(
    projection: TargetPostCellProjection,
) -> None:
    """Verify retained completion fields without reopening decision policy."""

    if type(projection) is not TargetPostCellProjection:
        raise TargetProjectionContractError(
            "completed Target receipt requires an exact TargetPostCellProjection"
        )
    try:
        current_state = _target_completion_state(projection)
    except (AttributeError, TypeError) as exc:
        raise TargetProjectionContractError(
            "completed Target projection receipt was substituted or partial"
        ) from exc
    if (
        type(projection._completion_seal) is not tuple
        or projection._completion_seal != current_state
    ):
        raise TargetProjectionContractError(
            "completed Target projection receipt was substituted or partial"
        )


def _target_completion_state(
    projection: TargetPostCellProjection,
) -> tuple[object, ...]:
    return (
        id(projection),
        retained_owner_digest(
            projection.pre_cell_projection,
            identity_only=(projection.pre_cell_projection.finalized_alignment,),
        ),
        retained_owner_digest(projection.decisions),
        (
            type(projection.accepted_cell_count),
            projection.accepted_cell_count,
        ),
        (type(projection.no_write_count), projection.no_write_count),
        retained_owner_digest(projection.review_queue_references),
    )


def _validate_pre_cell_identity_boundary(
    projection: TargetPreCellProjection,
) -> tuple[TargetCellSubject, ...]:
    if type(projection) is not TargetPreCellProjection:
        raise TargetProjectionContractError(
            "Target post-cell projection requires an exact TargetPreCellProjection"
        )
    if type(projection.row_binding) is not TargetRowBinding:
        raise TargetProjectionContractError(
            "Target post-cell projection crossed its exact row binding"
        )
    if type(projection.cell_subjects) is not tuple:
        raise TargetProjectionContractError(
            "Target cell subjects must be an immutable tuple"
        )
    expected_samples = tuple(
        sorted(
            projection.scope.samples,
            key=lambda sample: sample.identity.sample_identity_id,
        )
    )
    actual_samples = tuple(
        subject.canonical_sample for subject in projection.cell_subjects
    )
    if actual_samples != expected_samples:
        raise TargetProjectionContractError(
            "Target post-cell projection requires the complete canonical "
            "sample population"
        )
    for subject in projection.cell_subjects:
        if (
            type(subject) is not TargetCellSubject
            or subject.target is not projection.scope.target
            or subject.processing_request is not projection.scope.request
            or subject.row_binding is not projection.row_binding
        ):
            raise TargetProjectionContractError(
                "Target post-cell projection contains a crossed or substituted subject"
            )
    return projection.cell_subjects


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
        try:
            validate_production_cell_decision(decision)
        except (CellDecisionContractError, TypeError, ValueError) as exc:
            raise TargetProjectionContractError(str(exc)) from exc
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


def _validate_review_reference(reference: TargetReviewQueueReference) -> None:
    if type(reference.subject) is not TargetCellSubject:
        raise TargetProjectionContractError(
            "Target Review Queue reference requires an exact TargetCellSubject"
        )
    if type(reference.decision) is not ProductionCellDecision:
        raise TargetProjectionContractError(
            "Target Review Queue reference requires an exact ProductionCellDecision"
        )
    if reference.decision.subject is not reference.subject:
        raise TargetProjectionContractError(
            "Target Review Queue reference crossed its exact subject decision"
        )
    if type(reference.reasons) is not tuple or not reference.reasons:
        raise TargetProjectionContractError(
            "Target Review Queue reference requires typed reasons"
        )
    expected = _review_reasons(reference.row_binding, reference.decision)
    if reference.reasons != expected:
        raise TargetProjectionContractError(
            "Target Review Queue reasons do not match owner policy"
        )
