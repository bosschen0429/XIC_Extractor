from __future__ import annotations

from collections.abc import Mapping, Sequence

from xic_extractor.review_actions.models import (
    ReviewAction,
    ReviewActionApplication,
    ReviewActionApplyChangeset,
    ReviewActionApplyChangesetPlan,
    ReviewActionApplyOutput,
    ReviewActionApplyReadiness,
    ReviewActionExpectedDiffApproval,
    ReviewActionExpectedDiffTemplate,
)
from xic_extractor.review_actions.schema import (
    REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_READINESS_SCHEMA_VERSION,
    REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION,
    REVIEW_ACTION_SCHEMA_VERSION,
    REVIEW_ACTION_TYPES,
)


def summarize_review_actions(actions: Sequence[ReviewAction]) -> dict[str, object]:
    counts_by_type = {action_type: 0 for action_type in sorted(REVIEW_ACTION_TYPES)}
    product_mutating_count = 0
    for action in actions:
        counts_by_type[action.action_type] += 1
        if action.product_mutating:
            product_mutating_count += 1
    return {
        "schema_version": REVIEW_ACTION_SCHEMA_VERSION,
        "action_count": len(actions),
        "product_mutating_action_count": product_mutating_count,
        "counts_by_type": counts_by_type,
    }


def summarize_review_action_applications(
    applications: Sequence[ReviewActionApplication],
) -> dict[str, object]:
    counts_by_status: dict[str, int] = {}
    expected_diff_required_count = 0
    product_mutating_count = 0
    blocked_count = 0
    for application in applications:
        counts_by_status[application.application_status] = (
            counts_by_status.get(application.application_status, 0) + 1
        )
        if application.expected_diff_status == "required_before_apply":
            expected_diff_required_count += 1
        if application.product_mutating:
            product_mutating_count += 1
        if application.application_status.startswith("blocked"):
            blocked_count += 1
    return {
        "schema_version": REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION,
        "application_count": len(applications),
        "blocked_application_count": blocked_count,
        "product_mutating_action_count": product_mutating_count,
        "expected_diff_required_count": expected_diff_required_count,
        "counts_by_status": dict(sorted(counts_by_status.items())),
    }


def summarize_review_action_expected_diff_approvals(
    approvals: Mapping[str, ReviewActionExpectedDiffApproval],
) -> dict[str, object]:
    counts_by_action_type: dict[str, int] = {}
    counts_by_validation_tier: dict[str, int] = {}
    counts_by_matrix_value_impact: dict[str, int] = {}
    for approval in approvals.values():
        counts_by_action_type[approval.action_type] = (
            counts_by_action_type.get(approval.action_type, 0) + 1
        )
        counts_by_validation_tier[approval.validation_tier] = (
            counts_by_validation_tier.get(approval.validation_tier, 0) + 1
        )
        counts_by_matrix_value_impact[approval.expected_matrix_value_impact] = (
            counts_by_matrix_value_impact.get(
                approval.expected_matrix_value_impact,
                0,
            )
            + 1
        )
    return {
        "schema_version": REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION,
        "approval_count": len(approvals),
        "counts_by_action_type": dict(sorted(counts_by_action_type.items())),
        "counts_by_validation_tier": dict(sorted(counts_by_validation_tier.items())),
        "counts_by_matrix_value_impact": dict(
            sorted(counts_by_matrix_value_impact.items())
        ),
    }


def summarize_review_action_apply_changeset_plan(
    plan: ReviewActionApplyChangesetPlan,
) -> dict[str, object]:
    counts_by_status: dict[str, int] = {}
    counts_by_operation: dict[str, int] = {}
    ready_count = 0
    blocked_count = 0
    area_recompute_count = 0
    candidate_sidecar_count = 0
    for row in plan.rows:
        counts_by_status[row.changeset_status] = (
            counts_by_status.get(row.changeset_status, 0) + 1
        )
        if row.operation:
            counts_by_operation[row.operation] = (
                counts_by_operation.get(row.operation, 0) + 1
            )
        if row.changeset_status.startswith("ready"):
            ready_count += 1
        if row.changeset_status.startswith("blocked"):
            blocked_count += 1
        if row.requires_area_recompute:
            area_recompute_count += 1
        if row.requires_candidate_sidecar:
            candidate_sidecar_count += 1
    return {
        "schema_version": REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
        "row_count": len(plan.rows),
        "ready_count": ready_count,
        "blocked_count": blocked_count,
        "requires_area_recompute_count": area_recompute_count,
        "requires_candidate_sidecar_count": candidate_sidecar_count,
        "counts_by_status": dict(sorted(counts_by_status.items())),
        "counts_by_operation": dict(sorted(counts_by_operation.items())),
    }


def summarize_review_action_apply_output(
    output: ReviewActionApplyOutput,
) -> dict[str, object]:
    counts_by_status: dict[str, int] = {}
    applied_count = 0
    audit_only_count = 0
    deferred_count = 0
    blocked_count = 0
    for row in output.audit_rows:
        status = str(row["apply_status"])
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if status.startswith("applied_"):
            applied_count += 1
        elif status == "audit_recorded":
            audit_only_count += 1
        elif status.startswith("deferred_"):
            deferred_count += 1
        elif status.startswith("blocked"):
            blocked_count += 1
    return {
        "schema_version": REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
        "targeted_row_count": len(output.targeted_rows),
        "audit_row_count": len(output.audit_rows),
        "applied_count": applied_count,
        "audit_only_count": audit_only_count,
        "deferred_count": deferred_count,
        "blocked_count": blocked_count,
        "counts_by_status": dict(sorted(counts_by_status.items())),
    }


def review_action_application_to_row(
    application: ReviewActionApplication,
) -> dict[str, object]:
    action = application.action
    target_state = application.target_state
    return {
        "schema_version": REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION,
        "sample_name": action.sample_name,
        "target_label": action.target_label,
        "action_type": action.action_type,
        "application_status": application.application_status,
        "product_mutating": action.product_mutating,
        "current_product_state": target_state.product_state if target_state else "",
        "current_counted_detection": (
            target_state.counted_detection if target_state else ""
        ),
        "current_review_state": target_state.review_state if target_state else "",
        "candidate_id": action.candidate_id,
        "boundary_id": action.boundary_id,
        "rt_left_min": action.rt_left_min,
        "rt_apex_min": action.rt_apex_min,
        "rt_right_min": action.rt_right_min,
        "expected_diff_required": action.expected_diff_required,
        "expected_diff_status": application.expected_diff_status,
        "reason": application.reason,
        "reviewer": action.reviewer,
        "reviewed_at": action.reviewed_at,
        "comment": action.comment,
    }


def review_action_apply_changeset_to_row(
    changeset: ReviewActionApplyChangeset,
) -> dict[str, object]:
    readiness = changeset.readiness
    application = readiness.application
    action = application.action
    approval = readiness.expected_diff_approval
    expected_public_outputs_touched = (
        ";".join(approval.expected_public_outputs_touched) if approval else ""
    )
    evidence_sources = ";".join(approval.evidence_sources) if approval else ""
    return {
        "schema_version": REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
        "sample_name": action.sample_name,
        "target_label": action.target_label,
        "action_type": action.action_type,
        "changeset_status": changeset.changeset_status,
        "apply_readiness_status": readiness.apply_readiness_status,
        "operation": changeset.operation,
        "product_mutating": action.product_mutating,
        "output_scope": ";".join(changeset.output_scope),
        "requires_expected_diff_approval": action.product_mutating,
        "expected_diff_stable_row_id": readiness.expected_diff_stable_row_id,
        "expected_diff_validation_tier": approval.validation_tier if approval else "",
        "expected_matrix_value_impact": (
            approval.expected_matrix_value_impact if approval else ""
        ),
        "expected_public_outputs_touched": expected_public_outputs_touched,
        "evidence_sources": evidence_sources,
        "evidence_summary": approval.evidence_summary if approval else "",
        "candidate_id": action.candidate_id,
        "boundary_id": action.boundary_id,
        "rt_left_min": action.rt_left_min,
        "rt_apex_min": action.rt_apex_min,
        "rt_right_min": action.rt_right_min,
        "proposed_product_state": changeset.proposed_product_state,
        "proposed_counted_detection": changeset.proposed_counted_detection,
        "proposed_review_state": changeset.proposed_review_state,
        "requires_area_recompute": changeset.requires_area_recompute,
        "requires_candidate_sidecar": changeset.requires_candidate_sidecar,
        "reason": changeset.reason,
        "reviewer": action.reviewer,
        "reviewed_at": action.reviewed_at,
        "comment": action.comment,
        "approval_notes": approval.approval_notes if approval else "",
    }


def review_action_apply_readiness_to_row(
    readiness: ReviewActionApplyReadiness,
) -> dict[str, object]:
    application = readiness.application
    action = application.action
    target_state = application.target_state
    approval = readiness.expected_diff_approval
    expected_public_outputs_touched = (
        ";".join(approval.expected_public_outputs_touched) if approval else ""
    )
    evidence_sources = ";".join(approval.evidence_sources) if approval else ""
    return {
        "schema_version": REVIEW_ACTION_APPLY_READINESS_SCHEMA_VERSION,
        "sample_name": action.sample_name,
        "target_label": action.target_label,
        "action_type": action.action_type,
        "apply_readiness_status": readiness.apply_readiness_status,
        "product_mutating": action.product_mutating,
        "current_product_state": target_state.product_state if target_state else "",
        "current_counted_detection": (
            target_state.counted_detection if target_state else ""
        ),
        "current_review_state": target_state.review_state if target_state else "",
        "expected_diff_stable_row_id": readiness.expected_diff_stable_row_id,
        "expected_diff_approval_status": (
            approval.reviewer_verdict if approval else ""
        ),
        "expected_diff_validation_tier": approval.validation_tier if approval else "",
        "expected_matrix_value_impact": (
            approval.expected_matrix_value_impact if approval else ""
        ),
        "expected_public_outputs_touched": expected_public_outputs_touched,
        "evidence_sources": evidence_sources,
        "evidence_summary": approval.evidence_summary if approval else "",
        "candidate_id": action.candidate_id,
        "boundary_id": action.boundary_id,
        "rt_left_min": action.rt_left_min,
        "rt_apex_min": action.rt_apex_min,
        "rt_right_min": action.rt_right_min,
        "reason": readiness.reason,
        "reviewer": action.reviewer,
        "reviewed_at": action.reviewed_at,
        "comment": action.comment,
        "approval_notes": approval.approval_notes if approval else "",
    }


def review_action_expected_diff_template_to_row(
    template: ReviewActionExpectedDiffTemplate,
) -> dict[str, object]:
    action = template.application.action
    target_state = template.application.target_state
    return {
        "schema_version": REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION,
        "stable_row_id": template.stable_row_id,
        "sample_name": action.sample_name,
        "target_label": action.target_label,
        "action_type": action.action_type,
        "candidate_id": action.candidate_id,
        "boundary_id": action.boundary_id,
        "rt_left_min": action.rt_left_min,
        "rt_apex_min": action.rt_apex_min,
        "rt_right_min": action.rt_right_min,
        "expected_public_outputs_touched": ";".join(
            template.expected_public_outputs_touched
        ),
        "expected_matrix_value_impact": template.expected_matrix_value_impact,
        "baseline_product_state": target_state.product_state if target_state else "",
        "baseline_counted_detection": (
            target_state.counted_detection if target_state else ""
        ),
        "baseline_review_state": target_state.review_state if target_state else "",
        "evidence_sources": "",
        "evidence_summary": "",
        "validation_tier": "not_validated",
        "reviewer_verdict": "inconclusive",
        "final_label": "inconclusive",
        "reviewer": action.reviewer,
        "reviewed_at": action.reviewed_at,
        "comment": action.comment,
        "approval_notes": "",
    }
