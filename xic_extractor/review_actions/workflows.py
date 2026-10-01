from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

from xic_extractor.review_actions.models import (
    ReviewAction,
    ReviewActionApplication,
    ReviewActionApplyChangeset,
    ReviewActionApplyChangesetPlan,
    ReviewActionApplyOutput,
    ReviewActionApplyReadiness,
    ReviewActionApplyReadinessPlan,
    ReviewActionError,
    ReviewActionExpectedDiffApproval,
    ReviewActionExpectedDiffTemplate,
    ReviewActionTargetState,
)
from xic_extractor.review_actions.rows import (
    review_action_application_to_row,
    review_action_apply_changeset_to_row,
    review_action_apply_readiness_to_row,
    review_action_expected_diff_template_to_row,
)
from xic_extractor.review_actions.schema import (
    _EXPECTED_DIFF_MATRIX_IMPACT_BY_ACTION,
    _EXPECTED_DIFF_OUTPUTS_BY_ACTION,
    PRODUCT_MUTATING_REVIEW_ACTIONS,
    REVIEW_ACTION_APPLICATION_PLAN_COLUMNS,
    REVIEW_ACTION_APPLY_AUDIT_COLUMNS,
    REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_CHANGESET_COLUMNS,
    REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_READINESS_COLUMNS,
    REVIEW_ACTION_COLUMNS,
    REVIEW_ACTION_EXPECTED_DIFF_COLUMNS,
    REVIEW_ACTION_EXPECTED_DIFF_FINAL_LABELS,
    REVIEW_ACTION_EXPECTED_DIFF_MATRIX_VALUE_IMPACTS,
    REVIEW_ACTION_EXPECTED_DIFF_REVIEWER_VERDICTS,
    REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION,
    REVIEW_ACTION_EXPECTED_DIFF_VALIDATION_TIERS,
    REVIEW_ACTION_SCHEMA_VERSION,
    REVIEW_ACTION_TARGETED_APPLY_AUDIT_COLUMNS,
    REVIEW_ACTION_TYPES,
    TARGETED_REVIEW_STATE_COLUMNS,
    ReviewActionType,
)
from xic_extractor.tabular_io import (
    read_delimited_rows,
    write_delimited_rows,
    write_tsv,
)


def load_review_actions(path: Path) -> tuple[ReviewAction, ...]:
    return parse_review_actions(
        _read_review_action_rows(path),
        source=str(path),
    )


def load_review_action_target_states(path: Path) -> tuple[ReviewActionTargetState, ...]:
    if not path.is_file():
        raise ReviewActionError(f"{path}: targeted output file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    try:
        rows = read_delimited_rows(
            path,
            required_columns=("SampleName", "Target"),
            delimiter=delimiter,
            encoding="utf-8-sig",
        )
    except ValueError as exc:
        raise ReviewActionError(str(exc)) from exc
    return tuple(_target_state_from_row(row) for row in rows)


def plan_review_action_applications(
    actions: Sequence[ReviewAction],
    target_states: Sequence[ReviewActionTargetState],
) -> tuple[ReviewActionApplication, ...]:
    state_by_key = _index_target_states(target_states)
    action_counts_by_key: dict[tuple[str, str], int] = {}
    for action in actions:
        key = _action_key(action)
        action_counts_by_key[key] = action_counts_by_key.get(key, 0) + 1

    applications: list[ReviewActionApplication] = []
    for action in actions:
        key = _action_key(action)
        target_state = state_by_key.get(key)
        if target_state is None:
            applications.append(
                ReviewActionApplication(
                    action=action,
                    application_status="blocked",
                    expected_diff_status=_expected_diff_status(action),
                    reason="target_row_missing",
                    target_state=None,
                )
            )
            continue
        if action_counts_by_key[key] > 1:
            applications.append(
                ReviewActionApplication(
                    action=action,
                    application_status="blocked",
                    expected_diff_status=_expected_diff_status(action),
                    reason="multiple_actions_for_target",
                    target_state=target_state,
                )
            )
            continue
        applications.append(_plan_single_application(action, target_state))
    return tuple(applications)


def write_review_action_application_plan(
    path: Path,
    applications: Sequence[ReviewActionApplication],
) -> None:
    write_tsv(
        path,
        [review_action_application_to_row(application) for application in applications],
        REVIEW_ACTION_APPLICATION_PLAN_COLUMNS,
        lineterminator="\n",
    )


def plan_review_action_expected_diff_templates(
    applications: Sequence[ReviewActionApplication],
) -> tuple[ReviewActionExpectedDiffTemplate, ...]:
    templates: list[ReviewActionExpectedDiffTemplate] = []
    for application in applications:
        if application.application_status != "blocked_expected_diff_review":
            continue
        action = application.action
        templates.append(
            ReviewActionExpectedDiffTemplate(
                application=application,
                stable_row_id=review_action_expected_diff_stable_row_id(action),
                expected_public_outputs_touched=_expected_diff_outputs(action),
                expected_matrix_value_impact=_expected_diff_matrix_impact(action),
            )
        )
    return tuple(templates)


def write_review_action_expected_diff_template(
    path: Path,
    templates: Sequence[ReviewActionExpectedDiffTemplate],
) -> None:
    write_tsv(
        path,
        [
            review_action_expected_diff_template_to_row(template)
            for template in templates
        ],
        REVIEW_ACTION_EXPECTED_DIFF_COLUMNS,
        lineterminator="\n",
    )


def load_review_action_expected_diff_approvals(
    path: Path,
) -> dict[str, ReviewActionExpectedDiffApproval]:
    if not path.is_file():
        raise ReviewActionError(f"{path}: review action expected-diff file not found")
    try:
        rows = read_delimited_rows(
            path,
            required_columns=REVIEW_ACTION_EXPECTED_DIFF_COLUMNS,
            delimiter="\t",
            encoding="utf-8-sig",
        )
    except ValueError as exc:
        raise ReviewActionError(str(exc)) from exc

    approvals: dict[str, ReviewActionExpectedDiffApproval] = {}
    for row_number, row in enumerate(rows, start=2):
        approval = _parse_expected_diff_approval_row(
            row,
            source=str(path),
            row_number=row_number,
        )
        if approval.stable_row_id in approvals:
            raise ReviewActionError(
                f"{path}:{row_number}: duplicate stable_row_id "
                f"{approval.stable_row_id!r}"
            )
        approvals[approval.stable_row_id] = approval
    return approvals


def plan_review_action_apply_readiness(
    applications: Sequence[ReviewActionApplication],
    expected_diff_approvals: Mapping[str, ReviewActionExpectedDiffApproval],
) -> ReviewActionApplyReadinessPlan:
    rows: list[ReviewActionApplyReadiness] = []
    used_approval_ids: set[str] = set()
    for application in applications:
        if application.application_status == "blocked_expected_diff_review":
            stable_row_id = review_action_expected_diff_stable_row_id(
                application.action
            )
            approval = expected_diff_approvals.get(stable_row_id)
            if approval is not None:
                used_approval_ids.add(stable_row_id)
                baseline_problem = _expected_diff_approval_baseline_problem(
                    approval,
                    application.target_state,
                )
                if baseline_problem is not None:
                    rows.append(
                        ReviewActionApplyReadiness(
                            application=application,
                            apply_readiness_status=(
                                f"blocked_expected_diff_baseline_{baseline_problem}"
                            ),
                            reason=f"expected_diff_baseline_{baseline_problem}",
                            expected_diff_stable_row_id=stable_row_id,
                            expected_diff_approval=approval,
                        )
                    )
                    continue
                rows.append(
                    ReviewActionApplyReadiness(
                        application=application,
                        apply_readiness_status="ready_expected_diff_approved",
                        reason="approved_expected_diff_available",
                        expected_diff_stable_row_id=stable_row_id,
                        expected_diff_approval=approval,
                    )
                )
                continue
            rows.append(
                ReviewActionApplyReadiness(
                    application=application,
                    apply_readiness_status="blocked_expected_diff_missing",
                    reason="approved_expected_diff_required",
                    expected_diff_stable_row_id=stable_row_id,
                )
            )
            continue
        if application.application_status == "planned_no_output_change":
            rows.append(
                ReviewActionApplyReadiness(
                    application=application,
                    apply_readiness_status="ready_no_output_change",
                    reason="accept_current_has_no_product_mutation",
                )
            )
            continue
        if application.application_status == "planned_review_state_only":
            rows.append(
                ReviewActionApplyReadiness(
                    application=application,
                    apply_readiness_status="ready_review_state_only",
                    reason="review_state_write_contract_available",
                )
            )
            continue
        rows.append(
            ReviewActionApplyReadiness(
                application=application,
                apply_readiness_status="blocked_application_plan",
                reason=application.reason,
            )
        )
    unused = tuple(
        approval
        for stable_row_id, approval in sorted(expected_diff_approvals.items())
        if stable_row_id not in used_approval_ids
    )
    return ReviewActionApplyReadinessPlan(
        rows=tuple(rows),
        unused_expected_diff_approvals=unused,
    )


def _expected_diff_approval_baseline_problem(
    approval: ReviewActionExpectedDiffApproval,
    target_state: ReviewActionTargetState | None,
) -> str | None:
    if target_state is None:
        return "missing"
    if not (
        approval.baseline_product_state
        and approval.baseline_counted_detection
        and approval.baseline_review_state
        and target_state.product_state
        and target_state.counted_detection
        and target_state.review_state
    ):
        return "missing"
    if (
        approval.baseline_product_state != target_state.product_state
        or approval.baseline_counted_detection != target_state.counted_detection
        or approval.baseline_review_state != target_state.review_state
    ):
        return "mismatch"
    return None


def write_review_action_apply_readiness_plan(
    path: Path,
    plan: ReviewActionApplyReadinessPlan,
) -> None:
    write_tsv(
        path,
        [review_action_apply_readiness_to_row(row) for row in plan.rows],
        REVIEW_ACTION_APPLY_READINESS_COLUMNS,
        lineterminator="\n",
    )


def plan_review_action_apply_changesets(
    readiness_plan: ReviewActionApplyReadinessPlan,
) -> ReviewActionApplyChangesetPlan:
    return ReviewActionApplyChangesetPlan(
        rows=tuple(
            _changeset_from_readiness(readiness)
            for readiness in readiness_plan.rows
        )
    )


def write_review_action_apply_changeset_plan(
    path: Path,
    plan: ReviewActionApplyChangesetPlan,
) -> None:
    write_tsv(
        path,
        [review_action_apply_changeset_to_row(row) for row in plan.rows],
        REVIEW_ACTION_APPLY_CHANGESET_COLUMNS,
        lineterminator="\n",
    )


def load_review_action_apply_changeset_rows(
    path: Path,
) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise ReviewActionError(f"{path}: review action changeset file not found")
    try:
        rows = read_delimited_rows(
            path,
            required_columns=REVIEW_ACTION_APPLY_CHANGESET_COLUMNS,
            delimiter="\t",
            encoding="utf-8-sig",
        )
    except ValueError as exc:
        raise ReviewActionError(str(exc)) from exc
    return tuple(rows)


def load_targeted_long_rows(
    path: Path,
) -> tuple[dict[str, str], ...]:
    if not path.is_file():
        raise ReviewActionError(f"{path}: targeted long file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    try:
        rows = read_delimited_rows(
            path,
            required_columns=(
                "SampleName",
                "Target",
                "Product State",
                "Counted Detection",
                "Review State",
            ),
            delimiter=delimiter,
            encoding="utf-8-sig",
        )
    except ValueError as exc:
        raise ReviewActionError(str(exc)) from exc
    return tuple(rows)


def apply_review_action_changeset_rows(
    targeted_rows: Sequence[Mapping[str, object]],
    changeset_rows: Sequence[Mapping[str, object]],
    *,
    allow_blocked: bool = False,
) -> ReviewActionApplyOutput:
    rows = [_string_row(row) for row in targeted_rows]
    fieldnames = _applied_targeted_fieldnames(rows)
    target_index = _index_targeted_rows(rows)
    applied_keys: set[tuple[str, str]] = set()
    audit_rows: list[dict[str, object]] = []

    for row_number, changeset in enumerate(changeset_rows, start=2):
        normalized = _normalize_changeset_row(changeset)
        _validate_changeset_row(normalized, source="<changeset>", row_number=row_number)
        key = (normalized["sample_name"], normalized["target_label"])
        if normalized["changeset_status"] == "blocked" and not allow_blocked:
            raise ReviewActionError(
                "<changeset>:"
                f"{row_number}: blocked changeset row cannot be applied"
            )
        target_row = target_index.get(key)
        if target_row is None:
            sample_name, target_label = key
            raise ReviewActionError(
                "<changeset>:"
                f"{row_number}: target row missing for sample_name={sample_name!r}, "
                f"target_label={target_label!r}"
            )
        if key in applied_keys:
            sample_name, target_label = key
            raise ReviewActionError(
                "<changeset>:"
                f"{row_number}: multiple apply changesets for sample_name="
                f"{sample_name!r}, target_label={target_label!r}"
            )
        applied_keys.add(key)
        audit_rows.append(_apply_single_changeset(target_row, normalized))

    return ReviewActionApplyOutput(
        targeted_rows=tuple(rows),
        targeted_fieldnames=fieldnames,
        audit_rows=tuple(audit_rows),
    )


def write_review_action_applied_targeted_long(
    path: Path,
    output: ReviewActionApplyOutput,
) -> None:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    write_delimited_rows(
        path,
        output.targeted_rows,
        output.targeted_fieldnames,
        delimiter=delimiter,
        lineterminator="\n",
    )


def write_review_action_apply_audit(
    path: Path,
    output: ReviewActionApplyOutput,
) -> None:
    write_tsv(
        path,
        output.audit_rows,
        REVIEW_ACTION_APPLY_AUDIT_COLUMNS,
        lineterminator="\n",
    )


def parse_review_actions(
    rows: Iterable[Mapping[str, object]],
    *,
    source: str = "<memory>",
) -> tuple[ReviewAction, ...]:
    parsed: list[ReviewAction] = []
    for index, row in enumerate(rows, start=2):
        normalized = _normalize_row(row)
        if not any(normalized.values()):
            continue
        parsed.append(_parse_action_row(normalized, source=source, row_number=index))
    return tuple(parsed)


def _string_row(row: Mapping[str, object]) -> dict[str, str]:
    return {str(key): _clean_text(value) for key, value in row.items()}


def _applied_targeted_fieldnames(
    rows: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(str(key))
                fieldnames.append(str(key))
    for column in REVIEW_ACTION_TARGETED_APPLY_AUDIT_COLUMNS:
        if column not in seen:
            fieldnames.append(column)
            seen.add(column)
    return tuple(fieldnames)


def _index_targeted_rows(
    rows: Sequence[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    indexed: dict[tuple[str, str], dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=2):
        sample_name = _clean_text(row.get("SampleName", ""))
        target_label = _clean_text(row.get("Target", ""))
        if not sample_name or not target_label:
            raise ReviewActionError(
                f"<targeted_long>:{row_number}: SampleName and Target are required"
            )
        for column in TARGETED_REVIEW_STATE_COLUMNS:
            if column not in row:
                raise ReviewActionError(
                    f"<targeted_long>:{row_number}: missing required column "
                    f"{column!r}"
                )
        key = (sample_name, target_label)
        if key in indexed:
            raise ReviewActionError(
                "targeted output contains duplicate review target rows: "
                f"sample_name={sample_name!r}, target_label={target_label!r}"
            )
        indexed[key] = row
    return indexed


def _normalize_changeset_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        header: _clean_text(row.get(header, ""))
        for header in REVIEW_ACTION_APPLY_CHANGESET_COLUMNS
    }


def _validate_changeset_row(
    row: Mapping[str, str],
    *,
    source: str,
    row_number: int,
) -> None:
    if row["schema_version"] != REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION:
        raise ReviewActionError(
            f"{source}:{row_number}: unsupported schema_version "
            f"{row['schema_version']!r}"
        )
    _required_text(row, "sample_name", source, row_number)
    _required_text(row, "target_label", source, row_number)
    operation = row["operation"]
    if row["changeset_status"] != "blocked" and not operation:
        raise ReviewActionError(f"{source}:{row_number}: operation is required")
    if operation and operation not in {
        "record_accept_current",
        "mark_unresolved",
        "reject_current",
        "select_candidate",
        "set_manual_boundary",
    }:
        raise ReviewActionError(
            f"{source}:{row_number}: unsupported operation {operation!r}"
        )


def _apply_single_changeset(
    target_row: dict[str, str],
    changeset: Mapping[str, str],
) -> dict[str, object]:
    previous_product_state = target_row.get("Product State", "")
    previous_counted_detection = target_row.get("Counted Detection", "")
    previous_review_state = target_row.get("Review State", "")
    operation = changeset["operation"]
    apply_status = _apply_status_for_changeset(changeset)
    changed_targeted_long = False

    if apply_status == "applied_review_state":
        target_row["Review State"] = changeset["proposed_review_state"]
        changed_targeted_long = True
    elif apply_status == "applied_product_state":
        target_row["Product State"] = changeset["proposed_product_state"]
        target_row["Counted Detection"] = changeset["proposed_counted_detection"]
        if changeset["proposed_review_state"]:
            target_row["Review State"] = changeset["proposed_review_state"]
        changed_targeted_long = True

    _write_apply_columns(target_row, changeset, apply_status)
    return {
        "schema_version": REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
        "sample_name": changeset["sample_name"],
        "target_label": changeset["target_label"],
        "action_type": changeset["action_type"],
        "operation": operation,
        "apply_status": apply_status,
        "changed_targeted_long": changed_targeted_long,
        "product_mutating": changeset["product_mutating"],
        "previous_product_state": previous_product_state,
        "previous_counted_detection": previous_counted_detection,
        "previous_review_state": previous_review_state,
        "new_product_state": target_row.get("Product State", ""),
        "new_counted_detection": target_row.get("Counted Detection", ""),
        "new_review_state": target_row.get("Review State", ""),
        "requires_area_recompute": changeset["requires_area_recompute"],
        "requires_candidate_sidecar": changeset["requires_candidate_sidecar"],
        "expected_diff_stable_row_id": changeset["expected_diff_stable_row_id"],
        "expected_diff_validation_tier": changeset["expected_diff_validation_tier"],
        "reason": changeset["reason"],
        "reviewer": changeset["reviewer"],
        "reviewed_at": changeset["reviewed_at"],
        "comment": changeset["comment"],
    }


def _apply_status_for_changeset(row: Mapping[str, str]) -> str:
    operation = row["operation"]
    if row["changeset_status"] == "blocked":
        return "blocked"
    if operation == "record_accept_current":
        return "audit_recorded"
    if operation == "mark_unresolved":
        return "applied_review_state"
    if operation == "reject_current":
        return "applied_product_state"
    if operation == "select_candidate":
        return "deferred_candidate_sidecar"
    if operation == "set_manual_boundary":
        return "deferred_area_recompute"
    raise ReviewActionError(f"unsupported operation {operation!r}")


def _write_apply_columns(
    target_row: dict[str, str],
    changeset: Mapping[str, str],
    apply_status: str,
) -> None:
    target_row["Review Action Apply Status"] = apply_status
    target_row["Review Action Operation"] = changeset["operation"]
    target_row["Review Action Reason"] = changeset["reason"]
    target_row["Review Action Reviewer"] = changeset["reviewer"]
    target_row["Review Action Reviewed At"] = changeset["reviewed_at"]
    target_row["Review Action Expected Diff Stable Row ID"] = changeset[
        "expected_diff_stable_row_id"
    ]
    target_row["Review Action Requires Area Recompute"] = changeset[
        "requires_area_recompute"
    ]
    target_row["Review Action Requires Candidate Sidecar"] = changeset[
        "requires_candidate_sidecar"
    ]


def _target_state_from_row(row: Mapping[str, str]) -> ReviewActionTargetState:
    return ReviewActionTargetState(
        sample_name=_clean_text(row.get("SampleName", "")),
        target_label=_clean_text(row.get("Target", "")),
        product_state=_clean_text(row.get("Product State", "")),
        counted_detection=_clean_text(row.get("Counted Detection", "")),
        review_state=_clean_text(row.get("Review State", "")),
    )


def _index_target_states(
    target_states: Sequence[ReviewActionTargetState],
) -> dict[tuple[str, str], ReviewActionTargetState]:
    indexed: dict[tuple[str, str], ReviewActionTargetState] = {}
    for target_state in target_states:
        key = (target_state.sample_name, target_state.target_label)
        if key in indexed:
            sample_name, target_label = key
            raise ReviewActionError(
                "targeted output contains duplicate review target rows: "
                f"sample_name={sample_name!r}, target_label={target_label!r}"
            )
        indexed[key] = target_state
    return indexed


def _action_key(action: ReviewAction) -> tuple[str, str]:
    return (action.sample_name, action.target_label)


def _plan_single_application(
    action: ReviewAction,
    target_state: ReviewActionTargetState,
) -> ReviewActionApplication:
    if action.action_type == "accept_current":
        return ReviewActionApplication(
            action=action,
            application_status="planned_no_output_change",
            expected_diff_status="not_required",
            reason="accept_current_records_review_only",
            target_state=target_state,
        )
    if action.action_type == "mark_unresolved":
        return ReviewActionApplication(
            action=action,
            application_status="planned_review_state_only",
            expected_diff_status="not_required",
            reason="mark_unresolved_requires_audit_before_product_write",
            target_state=target_state,
        )
    return ReviewActionApplication(
        action=action,
        application_status="blocked_expected_diff_review",
        expected_diff_status="required_before_apply",
        reason=f"{action.action_type}_requires_reintegration_slice",
        target_state=target_state,
    )


def _expected_diff_status(action: ReviewAction) -> str:
    if action.product_mutating:
        return "required_before_apply"
    return "not_required"


def _expected_diff_outputs(action: ReviewAction) -> tuple[str, ...]:
    return _EXPECTED_DIFF_OUTPUTS_BY_ACTION[action.action_type]


def _expected_diff_matrix_impact(action: ReviewAction) -> str:
    return _EXPECTED_DIFF_MATRIX_IMPACT_BY_ACTION[action.action_type]


def review_action_expected_diff_stable_row_id(action: ReviewAction) -> str:
    digest_source = "|".join(
        (
            action.sample_name,
            action.target_label,
            action.action_type,
            action.candidate_id,
            action.boundary_id,
            _stable_float(action.rt_left_min),
            _stable_float(action.rt_apex_min),
            _stable_float(action.rt_right_min),
        )
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:16]
    return f"review_action_expected_diff:{digest}"


def _read_review_action_rows(path: Path) -> list[Mapping[str, object]]:
    if not path.is_file():
        raise ReviewActionError(f"{path}: review action file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise ReviewActionError(f"{path}: missing review action header")
        missing = [
            header
            for header in REVIEW_ACTION_COLUMNS
            if header not in reader.fieldnames
        ]
        if missing:
            raise ReviewActionError(
                f"{path}: missing review action columns: {', '.join(missing)}"
            )
        return list(reader)


def _parse_action_row(
    row: Mapping[str, str],
    *,
    source: str,
    row_number: int,
) -> ReviewAction:
    schema_version = row["schema_version"]
    if schema_version != REVIEW_ACTION_SCHEMA_VERSION:
        raise ReviewActionError(
            f"{source}:{row_number}: unsupported schema_version {schema_version!r}"
        )
    sample_name = _required_text(row, "sample_name", source, row_number)
    target_label = _required_text(row, "target_label", source, row_number)
    action_type = _required_text(row, "action_type", source, row_number)
    if action_type not in REVIEW_ACTION_TYPES:
        raise ReviewActionError(
            f"{source}:{row_number}: unsupported action_type {action_type!r}"
        )
    candidate_id = row["candidate_id"]
    boundary_id = row["boundary_id"]
    rt_left_min = _optional_float(row["rt_left_min"], "rt_left_min", source, row_number)
    rt_apex_min = _optional_float(row["rt_apex_min"], "rt_apex_min", source, row_number)
    rt_right_min = _optional_float(
        row["rt_right_min"],
        "rt_right_min",
        source,
        row_number,
    )
    expected_diff_required = _parse_bool(
        row["expected_diff_required"],
        "expected_diff_required",
        source,
        row_number,
    )

    _validate_action_requirements(
        action_type,
        candidate_id=candidate_id,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        comment=row["comment"],
        expected_diff_required=expected_diff_required,
        source=source,
        row_number=row_number,
    )
    return ReviewAction(
        sample_name=sample_name,
        target_label=target_label,
        action_type=action_type,  # type: ignore[arg-type]
        candidate_id=candidate_id,
        boundary_id=boundary_id,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        comment=row["comment"],
        reviewer=row["reviewer"],
        reviewed_at=row["reviewed_at"],
        expected_diff_required=expected_diff_required,
    )


def _parse_expected_diff_approval_row(
    row: Mapping[str, str],
    *,
    source: str,
    row_number: int,
) -> ReviewActionExpectedDiffApproval:
    schema_version = row["schema_version"]
    if schema_version != REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION:
        raise ReviewActionError(
            f"{source}:{row_number}: unsupported schema_version {schema_version!r}"
        )

    stable_row_id = _required_text(row, "stable_row_id", source, row_number)
    action_type = _required_text(row, "action_type", source, row_number)
    if action_type not in PRODUCT_MUTATING_REVIEW_ACTIONS:
        raise ReviewActionError(
            f"{source}:{row_number}: expected-diff approvals only support "
            "product-mutating review actions"
        )
    sample_name = _required_text(row, "sample_name", source, row_number)
    target_label = _required_text(row, "target_label", source, row_number)
    candidate_id = row["candidate_id"]
    boundary_id = row["boundary_id"]
    rt_left_min = _optional_float(row["rt_left_min"], "rt_left_min", source, row_number)
    rt_apex_min = _optional_float(row["rt_apex_min"], "rt_apex_min", source, row_number)
    rt_right_min = _optional_float(
        row["rt_right_min"],
        "rt_right_min",
        source,
        row_number,
    )
    expected_public_outputs_touched = _split_multi(
        _required_text(
            row,
            "expected_public_outputs_touched",
            source,
            row_number,
        )
    )
    expected_matrix_value_impact = _required_enum(
        row,
        "expected_matrix_value_impact",
        REVIEW_ACTION_EXPECTED_DIFF_MATRIX_VALUE_IMPACTS,
        source,
        row_number,
    )
    baseline_product_state = row["baseline_product_state"]
    baseline_counted_detection = row["baseline_counted_detection"]
    baseline_review_state = row["baseline_review_state"]
    evidence_sources = _split_multi(
        _required_text(row, "evidence_sources", source, row_number)
    )
    evidence_summary = _required_text(row, "evidence_summary", source, row_number)
    validation_tier = _required_enum(
        row,
        "validation_tier",
        REVIEW_ACTION_EXPECTED_DIFF_VALIDATION_TIERS,
        source,
        row_number,
    )
    reviewer_verdict = _required_enum(
        row,
        "reviewer_verdict",
        REVIEW_ACTION_EXPECTED_DIFF_REVIEWER_VERDICTS,
        source,
        row_number,
    )
    final_label = _required_enum(
        row,
        "final_label",
        REVIEW_ACTION_EXPECTED_DIFF_FINAL_LABELS,
        source,
        row_number,
    )
    reviewer = _required_text(row, "reviewer", source, row_number)
    reviewed_at = _required_text(row, "reviewed_at", source, row_number)
    comment = row["comment"]

    action = ReviewAction(
        sample_name=sample_name,
        target_label=target_label,
        action_type=cast(ReviewActionType, action_type),
        candidate_id=candidate_id,
        boundary_id=boundary_id,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        comment=comment,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        expected_diff_required=True,
    )
    _validate_action_requirements(
        action_type,
        candidate_id=candidate_id,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        comment=comment,
        expected_diff_required=True,
        source=source,
        row_number=row_number,
    )
    expected_stable_row_id = review_action_expected_diff_stable_row_id(action)
    if stable_row_id != expected_stable_row_id:
        raise ReviewActionError(
            f"{source}:{row_number}: stable_row_id does not match review action "
            "identity"
        )
    _validate_approved_expected_diff_row(
        source,
        row_number,
        final_label=final_label,
        reviewer_verdict=reviewer_verdict,
        validation_tier=validation_tier,
        expected_public_outputs_touched=expected_public_outputs_touched,
        expected_matrix_value_impact=expected_matrix_value_impact,
        evidence_sources=evidence_sources,
        evidence_summary=evidence_summary,
    )
    return ReviewActionExpectedDiffApproval(
        stable_row_id=stable_row_id,
        sample_name=sample_name,
        target_label=target_label,
        action_type=cast(ReviewActionType, action_type),
        candidate_id=candidate_id,
        boundary_id=boundary_id,
        rt_left_min=rt_left_min,
        rt_apex_min=rt_apex_min,
        rt_right_min=rt_right_min,
        expected_public_outputs_touched=expected_public_outputs_touched,
        expected_matrix_value_impact=expected_matrix_value_impact,
        baseline_product_state=baseline_product_state,
        baseline_counted_detection=baseline_counted_detection,
        baseline_review_state=baseline_review_state,
        evidence_sources=evidence_sources,
        evidence_summary=evidence_summary,
        validation_tier=validation_tier,
        reviewer_verdict=reviewer_verdict,
        final_label=final_label,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        comment=comment,
        approval_notes=row["approval_notes"],
    )


def _validate_approved_expected_diff_row(
    source: str,
    row_number: int,
    *,
    final_label: str,
    reviewer_verdict: str,
    validation_tier: str,
    expected_public_outputs_touched: tuple[str, ...],
    expected_matrix_value_impact: str,
    evidence_sources: tuple[str, ...],
    evidence_summary: str,
) -> None:
    if final_label != "expected_diff" or reviewer_verdict != "approved":
        raise ReviewActionError(
            f"{source}:{row_number}: expected-diff approval rows must be "
            "approved expected_diff records"
        )
    if validation_tier == "not_validated":
        raise ReviewActionError(
            f"{source}:{row_number}: expected-diff approval is not validated"
        )
    if not evidence_sources or not evidence_summary.strip():
        raise ReviewActionError(
            f"{source}:{row_number}: expected-diff approval lacks evidence"
        )
    if "final_matrix" in expected_public_outputs_touched:
        if expected_matrix_value_impact == "not_assessed":
            raise ReviewActionError(
                f"{source}:{row_number}: matrix-affecting expected diff must "
                "assess matrix impact"
            )
        if validation_tier == "synthetic_fixture":
            raise ReviewActionError(
                f"{source}:{row_number}: matrix-affecting expected diff "
                "requires real-data validation"
            )


def _validate_action_requirements(
    action_type: str,
    *,
    candidate_id: str,
    rt_left_min: float | None,
    rt_apex_min: float | None,
    rt_right_min: float | None,
    comment: str,
    expected_diff_required: bool,
    source: str,
    row_number: int,
) -> None:
    if action_type == "select_candidate" and not candidate_id:
        raise ReviewActionError(
            f"{source}:{row_number}: select_candidate requires candidate_id"
        )
    if action_type == "reject_current" and not comment:
        raise ReviewActionError(
            f"{source}:{row_number}: reject_current requires comment"
        )
    if action_type == "set_manual_boundary":
        if rt_left_min is None or rt_apex_min is None or rt_right_min is None:
            raise ReviewActionError(
                f"{source}:{row_number}: set_manual_boundary requires "
                "rt_left_min, rt_apex_min, and rt_right_min"
            )
        if not rt_left_min <= rt_apex_min <= rt_right_min:
            raise ReviewActionError(
                f"{source}:{row_number}: manual boundary must satisfy "
                "rt_left_min <= rt_apex_min <= rt_right_min"
            )
    if action_type in PRODUCT_MUTATING_REVIEW_ACTIONS and not expected_diff_required:
        raise ReviewActionError(
            f"{source}:{row_number}: {action_type} requires expected_diff_required"
        )


def _normalize_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        header: _clean_text(row.get(header, ""))
        for header in REVIEW_ACTION_COLUMNS
    }


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _required_text(
    row: Mapping[str, str],
    key: str,
    source: str,
    row_number: int,
) -> str:
    value = row[key]
    if not value:
        raise ReviewActionError(f"{source}:{row_number}: {key} is required")
    return value


def _optional_float(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ReviewActionError(
            f"{source}:{row_number}: {key} must be numeric"
        ) from exc


def _parse_bool(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> bool:
    if not value:
        return False
    normalized = value.upper()
    if normalized == "TRUE":
        return True
    if normalized == "FALSE":
        return False
    raise ReviewActionError(f"{source}:{row_number}: {key} must be TRUE or FALSE")


def _required_enum(
    row: Mapping[str, str],
    key: str,
    allowed: frozenset[str],
    source: str,
    row_number: int,
) -> str:
    value = _required_text(row, key, source, row_number)
    if value not in allowed:
        raise ReviewActionError(
            f"{source}:{row_number}: {key} must be one of "
            f"{', '.join(sorted(allowed))}"
        )
    return value


def _split_multi(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _stable_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.8g}"


def _changeset_from_readiness(
    readiness: ReviewActionApplyReadiness,
) -> ReviewActionApplyChangeset:
    action = readiness.application.action
    if readiness.apply_readiness_status == "ready_no_output_change":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="ready_audit_only",
            operation="record_accept_current",
            output_scope=("audit_trail",),
            proposed_review_state="accepted_current",
            reason="records_review_intent_without_product_mutation",
        )
    if readiness.apply_readiness_status == "ready_review_state_only":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="ready_review_state_only",
            operation="mark_unresolved",
            output_scope=(
                "review_state",
                "targeted_long_csv",
                "workbook",
                "audit_trail",
            ),
            proposed_review_state="unresolved_by_review",
            reason="records_unresolved_review_state_without_area_or_matrix_change",
        )
    if readiness.apply_readiness_status != "ready_expected_diff_approved":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="blocked",
            operation="",
            output_scope=(),
            reason=readiness.reason,
        )
    if action.action_type == "reject_current":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="ready_pending_product_writer",
            operation="reject_current",
            output_scope=(
                "product_state",
                "counted_detection",
                "targeted_long_csv",
                "workbook",
                "final_matrix",
                "audit_trail",
            ),
            proposed_product_state="rejected_by_review",
            proposed_counted_detection="FALSE",
            proposed_review_state="rejected_by_review",
            reason="approved_expected_diff_allows_future_reject_current",
        )
    if action.action_type == "select_candidate":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="ready_pending_product_writer",
            operation="select_candidate",
            output_scope=(
                "selected_candidate",
                "selected_peak",
                "targeted_long_csv",
                "workbook",
                "final_matrix",
                "audit_trail",
            ),
            requires_candidate_sidecar=True,
            reason="approved_expected_diff_allows_future_candidate_switch",
        )
    if action.action_type == "set_manual_boundary":
        return ReviewActionApplyChangeset(
            readiness=readiness,
            changeset_status="ready_pending_product_writer",
            operation="set_manual_boundary",
            output_scope=(
                "selected_boundary",
                "area",
                "targeted_long_csv",
                "workbook",
                "final_matrix",
                "audit_trail",
            ),
            requires_area_recompute=True,
            reason="approved_expected_diff_allows_future_manual_boundary",
        )
    return ReviewActionApplyChangeset(
        readiness=readiness,
        changeset_status="blocked",
        operation="",
        output_scope=(),
        reason="unsupported_ready_review_action",
    )
