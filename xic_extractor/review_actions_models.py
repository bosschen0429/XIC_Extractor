from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.review_actions_schema import (
    PRODUCT_MUTATING_REVIEW_ACTIONS,
    ReviewActionType,
)


@dataclass(frozen=True)
class ReviewAction:
    sample_name: str
    target_label: str
    action_type: ReviewActionType
    candidate_id: str = ""
    boundary_id: str = ""
    rt_left_min: float | None = None
    rt_apex_min: float | None = None
    rt_right_min: float | None = None
    comment: str = ""
    reviewer: str = ""
    reviewed_at: str = ""
    expected_diff_required: bool = False

    @property
    def product_mutating(self) -> bool:
        return self.action_type in PRODUCT_MUTATING_REVIEW_ACTIONS


@dataclass(frozen=True)
class ReviewActionTargetState:
    sample_name: str
    target_label: str
    product_state: str = ""
    counted_detection: str = ""
    review_state: str = ""


@dataclass(frozen=True)
class ReviewActionApplication:
    action: ReviewAction
    application_status: str
    expected_diff_status: str
    reason: str
    target_state: ReviewActionTargetState | None = None

    @property
    def product_mutating(self) -> bool:
        return self.action.product_mutating


@dataclass(frozen=True)
class ReviewActionExpectedDiffTemplate:
    application: ReviewActionApplication
    stable_row_id: str
    expected_public_outputs_touched: tuple[str, ...]
    expected_matrix_value_impact: str


@dataclass(frozen=True)
class ReviewActionExpectedDiffApproval:
    stable_row_id: str
    sample_name: str
    target_label: str
    action_type: ReviewActionType
    candidate_id: str
    boundary_id: str
    rt_left_min: float | None
    rt_apex_min: float | None
    rt_right_min: float | None
    expected_public_outputs_touched: tuple[str, ...]
    expected_matrix_value_impact: str
    baseline_product_state: str
    baseline_counted_detection: str
    baseline_review_state: str
    evidence_sources: tuple[str, ...]
    evidence_summary: str
    validation_tier: str
    reviewer_verdict: str
    final_label: str
    reviewer: str
    reviewed_at: str
    comment: str
    approval_notes: str


@dataclass(frozen=True)
class ReviewActionApplyReadiness:
    application: ReviewActionApplication
    apply_readiness_status: str
    reason: str
    expected_diff_stable_row_id: str = ""
    expected_diff_approval: ReviewActionExpectedDiffApproval | None = None


@dataclass(frozen=True)
class ReviewActionApplyReadinessPlan:
    rows: tuple[ReviewActionApplyReadiness, ...]
    unused_expected_diff_approvals: tuple[ReviewActionExpectedDiffApproval, ...]


@dataclass(frozen=True)
class ReviewActionCandidateSidecarCheck:
    action: ReviewAction
    candidate_sidecar_status: str
    candidate_sidecar_reason: str
    candidate_row_sha256: str = ""
    candidate_selected: str = ""
    candidate_confidence: str = ""
    candidate_rt_left_min: str = ""
    candidate_rt_apex_min: str = ""
    candidate_rt_right_min: str = ""
    candidate_area_baseline_corrected: str = ""


@dataclass(frozen=True)
class ReviewActionApplyChangeset:
    readiness: ReviewActionApplyReadiness
    changeset_status: str
    operation: str
    output_scope: tuple[str, ...]
    proposed_product_state: str = ""
    proposed_counted_detection: str = ""
    proposed_review_state: str = ""
    requires_area_recompute: bool = False
    requires_candidate_sidecar: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ReviewActionApplyChangesetPlan:
    rows: tuple[ReviewActionApplyChangeset, ...]


@dataclass(frozen=True)
class ReviewActionApplyOutput:
    targeted_rows: tuple[dict[str, str], ...]
    targeted_fieldnames: tuple[str, ...]
    audit_rows: tuple[dict[str, object], ...]

    @property
    def summary(self) -> dict[str, object]:
        from xic_extractor.review_actions_rows import (
            summarize_review_action_apply_output,
        )

        return summarize_review_action_apply_output(self)


class ReviewActionError(ValueError):
    """Raised when a review action import cannot be trusted."""
