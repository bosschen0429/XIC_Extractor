from __future__ import annotations

from typing import Literal

REVIEW_ACTION_SCHEMA_VERSION = "review_action_v1"
REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION = "review_action_application_plan_v1"
REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION = "review_action_expected_diff_v1"
REVIEW_ACTION_APPLY_READINESS_SCHEMA_VERSION = "review_action_apply_readiness_v1"
REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION = "review_action_apply_changeset_v1"
REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION = "review_action_apply_audit_v1"
REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION = (
    "review_action_candidate_sidecar_v1"
)
REVIEW_ACTION_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "candidate_id",
    "boundary_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "comment",
    "reviewer",
    "reviewed_at",
    "expected_diff_required",
)
ReviewActionType = Literal[
    "accept_current",
    "mark_unresolved",
    "reject_current",
    "select_candidate",
    "set_manual_boundary",
]
REVIEW_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "accept_current",
        "mark_unresolved",
        "reject_current",
        "select_candidate",
        "set_manual_boundary",
    }
)
PRODUCT_MUTATING_REVIEW_ACTIONS: frozenset[str] = frozenset(
    {
        "reject_current",
        "select_candidate",
        "set_manual_boundary",
    }
)
TARGETED_REVIEW_STATE_COLUMNS: tuple[str, ...] = (
    "Product State",
    "Counted Detection",
    "Review State",
)
REVIEW_ACTION_APPLICATION_PLAN_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "application_status",
    "product_mutating",
    "current_product_state",
    "current_counted_detection",
    "current_review_state",
    "candidate_id",
    "boundary_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "expected_diff_required",
    "expected_diff_status",
    "reason",
    "reviewer",
    "reviewed_at",
    "comment",
)
REVIEW_ACTION_EXPECTED_DIFF_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "stable_row_id",
    "sample_name",
    "target_label",
    "action_type",
    "candidate_id",
    "boundary_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "expected_public_outputs_touched",
    "expected_matrix_value_impact",
    "baseline_product_state",
    "baseline_counted_detection",
    "baseline_review_state",
    "evidence_sources",
    "evidence_summary",
    "validation_tier",
    "reviewer_verdict",
    "final_label",
    "reviewer",
    "reviewed_at",
    "comment",
    "approval_notes",
)
REVIEW_ACTION_APPLY_READINESS_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "apply_readiness_status",
    "product_mutating",
    "current_product_state",
    "current_counted_detection",
    "current_review_state",
    "expected_diff_stable_row_id",
    "expected_diff_approval_status",
    "expected_diff_validation_tier",
    "expected_matrix_value_impact",
    "expected_public_outputs_touched",
    "evidence_sources",
    "evidence_summary",
    "candidate_id",
    "boundary_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "reason",
    "reviewer",
    "reviewed_at",
    "comment",
    "approval_notes",
)
REVIEW_ACTION_APPLY_CHANGESET_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "changeset_status",
    "apply_readiness_status",
    "operation",
    "product_mutating",
    "output_scope",
    "requires_expected_diff_approval",
    "expected_diff_stable_row_id",
    "expected_diff_validation_tier",
    "expected_matrix_value_impact",
    "expected_public_outputs_touched",
    "evidence_sources",
    "evidence_summary",
    "candidate_id",
    "boundary_id",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "proposed_product_state",
    "proposed_counted_detection",
    "proposed_review_state",
    "requires_area_recompute",
    "requires_candidate_sidecar",
    "reason",
    "reviewer",
    "reviewed_at",
    "comment",
    "approval_notes",
)
REVIEW_ACTION_TARGETED_APPLY_AUDIT_COLUMNS: tuple[str, ...] = (
    "Review Action Apply Status",
    "Review Action Operation",
    "Review Action Reason",
    "Review Action Reviewer",
    "Review Action Reviewed At",
    "Review Action Expected Diff Stable Row ID",
    "Review Action Requires Area Recompute",
    "Review Action Requires Candidate Sidecar",
)
REVIEW_ACTION_APPLY_AUDIT_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "operation",
    "apply_status",
    "changed_targeted_long",
    "product_mutating",
    "previous_product_state",
    "previous_counted_detection",
    "previous_review_state",
    "new_product_state",
    "new_counted_detection",
    "new_review_state",
    "requires_area_recompute",
    "requires_candidate_sidecar",
    "expected_diff_stable_row_id",
    "expected_diff_validation_tier",
    "reason",
    "reviewer",
    "reviewed_at",
    "comment",
)
PEAK_CANDIDATE_SIDECAR_REQUIRED_COLUMNS: tuple[str, ...] = (
    "sample_name",
    "target_label",
    "candidate_id",
    "selected",
)
REVIEW_ACTION_CANDIDATE_SIDECAR_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "target_label",
    "action_type",
    "candidate_id",
    "candidate_sidecar_status",
    "candidate_sidecar_reason",
    "candidate_row_sha256",
    "candidate_selected",
    "candidate_confidence",
    "candidate_rt_left_min",
    "candidate_rt_apex_min",
    "candidate_rt_right_min",
    "candidate_area_baseline_corrected",
    "reviewer",
    "reviewed_at",
    "comment",
)
REVIEW_ACTION_EXPECTED_DIFF_FINAL_LABELS = frozenset(
    {"expected_diff", "blocked_diff", "inconclusive"}
)
REVIEW_ACTION_EXPECTED_DIFF_REVIEWER_VERDICTS = frozenset(
    {"approved", "blocked", "inconclusive"}
)
REVIEW_ACTION_EXPECTED_DIFF_VALIDATION_TIERS = frozenset(
    {
        "synthetic_fixture",
        "targeted_benchmark",
        "8raw",
        "manual_eic_ms2_review",
        "not_validated",
    }
)
REVIEW_ACTION_EXPECTED_DIFF_MATRIX_VALUE_IMPACTS = frozenset(
    {"none", "area_value_changed", "presence_changed", "not_assessed"}
)
_EXPECTED_DIFF_OUTPUTS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "reject_current": ("targeted_long_csv", "workbook", "final_matrix"),
    "select_candidate": ("targeted_long_csv", "workbook", "final_matrix"),
    "set_manual_boundary": ("targeted_long_csv", "workbook", "final_matrix"),
}
_EXPECTED_DIFF_MATRIX_IMPACT_BY_ACTION: dict[str, str] = {
    "reject_current": "presence_changed",
    "select_candidate": "area_value_changed",
    "set_manual_boundary": "area_value_changed",
}
