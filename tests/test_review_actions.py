import json
from pathlib import Path

import pytest

from scripts import (
    apply_review_action_changesets,
    plan_review_action_applications,
    plan_review_action_apply_changesets,
    plan_review_action_apply_readiness,
    plan_review_action_candidate_sidecars,
    validate_review_action_expected_diffs,
    validate_review_actions,
)
from xic_extractor.review_actions import (
    REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
    REVIEW_ACTION_APPLY_READINESS_SCHEMA_VERSION,
    REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION,
    REVIEW_ACTION_COLUMNS,
    REVIEW_ACTION_EXPECTED_DIFF_COLUMNS,
    REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION,
    REVIEW_ACTION_SCHEMA_VERSION,
    ReviewAction,
    ReviewActionError,
    ReviewActionTargetState,
    apply_review_action_changeset_rows,
    load_review_action_expected_diff_approvals,
    load_review_actions,
    parse_review_actions,
    plan_review_action_expected_diff_templates,
    review_action_application_to_row,
    review_action_apply_changeset_to_row,
    review_action_apply_readiness_to_row,
    review_action_candidate_sidecar_to_row,
    review_action_expected_diff_stable_row_id,
    review_action_expected_diff_template_to_row,
    summarize_review_action_applications,
    summarize_review_action_apply_changeset_plan,
    summarize_review_action_apply_readiness_plan,
    summarize_review_action_candidate_sidecars,
    summarize_review_actions,
)
from xic_extractor.review_actions import (
    plan_review_action_applications as build_review_action_application_plan,
)
from xic_extractor.review_actions import (
    plan_review_action_apply_changesets as build_review_action_apply_changesets,
)
from xic_extractor.review_actions import (
    plan_review_action_apply_readiness as build_review_action_apply_readiness,
)
from xic_extractor.review_actions import (
    plan_review_action_candidate_sidecars as build_review_action_candidate_sidecars,
)


def test_parse_review_actions_accepts_current_and_manual_boundary() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-hmdC",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                comment="manual boundary from reviewer",
            ),
        ]
    )

    assert len(actions) == 2
    assert actions[0].product_mutating is False
    assert actions[1].product_mutating is True
    assert actions[1].rt_left_min == 8.1
    assert summarize_review_actions(actions) == {
        "schema_version": REVIEW_ACTION_SCHEMA_VERSION,
        "action_count": 2,
        "product_mutating_action_count": 1,
        "counts_by_type": {
            "accept_current": 1,
            "mark_unresolved": 0,
            "reject_current": 0,
            "select_candidate": 0,
            "set_manual_boundary": 1,
        },
    }


def test_select_candidate_requires_candidate_id_and_expected_diff() -> None:
    with pytest.raises(
        ReviewActionError,
        match="select_candidate requires candidate_id",
    ):
        parse_review_actions(
            [
                _row(
                    sample_name="S1.raw",
                    target_label="5-mdC",
                    action_type="select_candidate",
                    expected_diff_required="TRUE",
                )
            ]
        )

    with pytest.raises(
        ReviewActionError,
        match="select_candidate requires expected_diff_required",
    ):
        parse_review_actions(
            [
                _row(
                    sample_name="S1.raw",
                    target_label="5-mdC",
                    action_type="select_candidate",
                    candidate_id="S1.raw|5-mdC|local_minimum|9.10000|9.00000|9.20000",
                    expected_diff_required="FALSE",
                )
            ]
        )


def test_manual_boundary_requires_ordered_rt_values() -> None:
    with pytest.raises(ReviewActionError, match="manual boundary must satisfy"):
        parse_review_actions(
            [
                _row(
                    sample_name="S1.raw",
                    target_label="5-mdC",
                    action_type="set_manual_boundary",
                    rt_left_min="8.8",
                    rt_apex_min="8.4",
                    rt_right_min="8.1",
                    expected_diff_required="TRUE",
                )
            ]
        )


def test_load_review_actions_rejects_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "review_actions.tsv"
    _write_tsv(
        path,
        [
            _row(
                schema_version="legacy",
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            )
        ],
    )

    with pytest.raises(ReviewActionError, match="unsupported schema_version"):
        load_review_actions(path)


def test_validate_review_actions_cli_prints_summary_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "review_actions.tsv"
    _write_tsv(
        path,
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            )
        ],
    )

    assert validate_review_actions.main([str(path), "--summary-json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["action_count"] == 1


def test_validate_review_actions_cli_reports_validation_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "review_actions.tsv"
    _write_tsv(
        path,
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="unknown",
            )
        ],
    )

    assert validate_review_actions.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert "unsupported action_type" in captured.err


def test_plan_review_action_applications_blocks_mutating_actions() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
                reviewer="analyst",
            ),
            _row(
                sample_name="S2.raw",
                target_label="5-hmdC",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                comment="manual boundary from reviewer",
            ),
        ]
    )

    applications = build_review_action_application_plan(
        actions,
        [
            _target_state("S1.raw", "5-mdC", product_state="counted"),
            _target_state(
                "S2.raw",
                "5-hmdC",
                product_state="review_required",
                counted_detection="FALSE",
            ),
        ],
    )

    rows = [review_action_application_to_row(item) for item in applications]
    assert rows[0]["application_status"] == "planned_no_output_change"
    assert rows[0]["expected_diff_status"] == "not_required"
    assert rows[0]["current_product_state"] == "counted"
    assert rows[1]["application_status"] == "blocked_expected_diff_review"
    assert rows[1]["expected_diff_status"] == "required_before_apply"
    assert rows[1]["reason"] == "set_manual_boundary_requires_reintegration_slice"
    assert summarize_review_action_applications(applications) == {
        "schema_version": REVIEW_ACTION_APPLICATION_PLAN_SCHEMA_VERSION,
        "application_count": 2,
        "blocked_application_count": 1,
        "product_mutating_action_count": 1,
        "expected_diff_required_count": 1,
        "counts_by_status": {
            "blocked_expected_diff_review": 1,
            "planned_no_output_change": 1,
        },
    }


def test_expected_diff_templates_only_for_blocked_mutations() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
            _row(
                sample_name="S2.raw",
                target_label="5-hmdC",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
                comment="manual boundary from reviewer",
            ),
        ]
    )
    applications = build_review_action_application_plan(
        actions,
        [
            _target_state("S1.raw", "5-mdC"),
            _target_state(
                "S2.raw",
                "5-hmdC",
                product_state="review_required",
                counted_detection="FALSE",
                review_state="needs_boundary_review",
            ),
        ],
    )

    templates = plan_review_action_expected_diff_templates(applications)
    rows = [review_action_expected_diff_template_to_row(item) for item in templates]

    assert len(rows) == 1
    assert rows[0]["schema_version"] == REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION
    assert rows[0]["stable_row_id"] == review_action_expected_diff_stable_row_id(
        actions[1]
    )
    assert rows[0]["expected_public_outputs_touched"] == (
        "targeted_long_csv;workbook;final_matrix"
    )
    assert rows[0]["expected_matrix_value_impact"] == "area_value_changed"
    assert rows[0]["baseline_product_state"] == "review_required"
    assert rows[0]["baseline_counted_detection"] == "FALSE"
    assert rows[0]["baseline_review_state"] == "needs_boundary_review"
    assert rows[0]["validation_tier"] == "not_validated"
    assert rows[0]["reviewer_verdict"] == "inconclusive"
    assert rows[0]["final_label"] == "inconclusive"


def test_review_action_expected_diff_stable_id_tracks_action_identity() -> None:
    original = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
            )
        ]
    )[0]
    same = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
            )
        ]
    )[0]
    changed = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-b",
                expected_diff_required="TRUE",
            )
        ]
    )[0]

    assert review_action_expected_diff_stable_row_id(
        original
    ) == review_action_expected_diff_stable_row_id(same)
    assert review_action_expected_diff_stable_row_id(
        original
    ) != review_action_expected_diff_stable_row_id(changed)


def test_load_review_action_expected_diff_approvals_requires_validated_approval(
    tmp_path: Path,
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
                comment="manual boundary from reviewer",
            )
        ]
    )[0]
    applications = build_review_action_application_plan(
        [action],
        [_target_state("S1.raw", "5-mdC")],
    )
    row = review_action_expected_diff_template_to_row(
        plan_review_action_expected_diff_templates(applications)[0]
    )
    row.update(
        {
            "evidence_sources": "manual_eic;8raw_parity",
            "evidence_summary": "Manual EIC review supports this boundary.",
            "validation_tier": "8raw",
            "reviewer_verdict": "approved",
            "final_label": "expected_diff",
            "approval_notes": "approved before product apply",
        }
    )
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(approval_path, [row])

    approvals = load_review_action_expected_diff_approvals(approval_path)

    approval = approvals[str(row["stable_row_id"])]
    assert approval.sample_name == "S1.raw"
    assert approval.action_type == "set_manual_boundary"
    assert approval.rt_apex_min == 8.4
    assert approval.expected_matrix_value_impact == "area_value_changed"
    assert approval.evidence_sources == ("manual_eic", "8raw_parity")


def test_load_review_action_expected_diff_approvals_rejects_template_rows(
    tmp_path: Path,
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    applications = build_review_action_application_plan(
        [action],
        [_target_state("S1.raw", "5-mdC")],
    )
    template_row = review_action_expected_diff_template_to_row(
        plan_review_action_expected_diff_templates(applications)[0]
    )
    template_row.update(
        {
            "evidence_sources": "manual_review",
            "evidence_summary": "review not approved yet",
        }
    )
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(approval_path, [template_row])

    with pytest.raises(ReviewActionError, match="approved expected_diff"):
        load_review_action_expected_diff_approvals(approval_path)


def test_validate_review_action_expected_diffs_cli_prints_summary_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    row = _approved_expected_diff_row(action)
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(approval_path, [row])

    assert (
        validate_review_action_expected_diffs.main(
            [str(approval_path), "--summary-json"]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["schema_version"] == REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION
    assert summary["approval_count"] == 1
    assert summary["counts_by_action_type"] == {"select_candidate": 1}


def test_validate_review_action_expected_diffs_cli_rejects_template(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    applications = build_review_action_application_plan(
        [action],
        [_target_state("S1.raw", "5-mdC")],
    )
    template_row = review_action_expected_diff_template_to_row(
        plan_review_action_expected_diff_templates(applications)[0]
    )
    template_path = tmp_path / "review_action_expected_diff_template.tsv"
    _write_expected_diff_tsv(template_path, [template_row])

    assert validate_review_action_expected_diffs.main([str(template_path)]) == 2
    assert "evidence_sources is required" in capsys.readouterr().err


def test_review_action_apply_readiness_consumes_approved_expected_diff(
    tmp_path: Path,
) -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC-mutating",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC-mutating|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-hmdC",
                action_type="mark_unresolved",
            ),
        ]
    )
    applications = build_review_action_application_plan(
        actions,
        [
            _target_state("S1.raw", "5-mdC", product_state="counted"),
            _target_state(
                "S1.raw",
                "5-mdC-mutating",
                product_state="review_required",
                counted_detection="FALSE",
                review_state="manual",
            ),
            _target_state("S1.raw", "5-hmdC", product_state="review_required"),
        ],
    )
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(
        approval_path,
        [
            _approved_expected_diff_row_for_action(
                actions[1],
                _target_state(
                    "S1.raw",
                    "5-mdC-mutating",
                    product_state="review_required",
                    counted_detection="FALSE",
                    review_state="manual",
                ),
            )
        ],
    )
    approvals = load_review_action_expected_diff_approvals(approval_path)

    plan = build_review_action_apply_readiness(applications, approvals)
    rows = [review_action_apply_readiness_to_row(row) for row in plan.rows]

    assert rows[0]["apply_readiness_status"] == "ready_no_output_change"
    assert rows[1]["apply_readiness_status"] == "ready_expected_diff_approved"
    assert rows[1]["expected_diff_approval_status"] == "approved"
    assert rows[1]["expected_diff_validation_tier"] == "8raw"
    assert rows[2]["apply_readiness_status"] == "ready_review_state_only"
    assert plan.unused_expected_diff_approvals == ()
    assert summarize_review_action_apply_readiness_plan(plan) == {
        "schema_version": REVIEW_ACTION_APPLY_READINESS_SCHEMA_VERSION,
        "row_count": 3,
        "ready_count": 3,
        "blocked_count": 0,
        "unused_expected_diff_approval_count": 0,
        "counts_by_status": {
            "ready_expected_diff_approved": 1,
            "ready_no_output_change": 1,
            "ready_review_state_only": 1,
        },
    }


def test_review_action_apply_readiness_blocks_stale_expected_diff_baseline(
    tmp_path: Path,
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    approval_application = build_review_action_application_plan(
        [action],
        [
            _target_state(
                "S1.raw",
                "5-mdC",
                product_state="review_required",
                counted_detection="FALSE",
                review_state="needs_review",
            )
        ],
    )
    approval_row = review_action_expected_diff_template_to_row(
        plan_review_action_expected_diff_templates(approval_application)[0]
    )
    approval_row.update(
        {
            "evidence_sources": "manual_eic;8raw_parity",
            "evidence_summary": "Manual EIC review supports this action.",
            "validation_tier": "8raw",
            "reviewer_verdict": "approved",
            "final_label": "expected_diff",
            "approval_notes": "approved before product apply",
        }
    )
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(approval_path, [approval_row])
    approvals = load_review_action_expected_diff_approvals(approval_path)
    current_applications = build_review_action_application_plan(
        [action],
        [
            _target_state(
                "S1.raw",
                "5-mdC",
                product_state="counted",
                counted_detection="TRUE",
                review_state="accepted",
            )
        ],
    )

    plan = build_review_action_apply_readiness(current_applications, approvals)
    row = review_action_apply_readiness_to_row(plan.rows[0])

    assert row["apply_readiness_status"] == "blocked_expected_diff_baseline_mismatch"
    assert row["reason"] == "expected_diff_baseline_mismatch"
    assert row["expected_diff_approval_status"] == "approved"
    assert plan.unused_expected_diff_approvals == ()


def test_review_action_apply_readiness_blocks_missing_expected_diff_baseline(
    tmp_path: Path,
) -> None:
    action = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(
        approval_path,
        [_approved_expected_diff_row_for_action(action)],
    )
    approvals = load_review_action_expected_diff_approvals(approval_path)
    current_applications = build_review_action_application_plan(
        [action],
        [_target_state("S1.raw", "5-mdC")],
    )

    plan = build_review_action_apply_readiness(current_applications, approvals)
    row = review_action_apply_readiness_to_row(plan.rows[0])

    assert row["apply_readiness_status"] == "blocked_expected_diff_baseline_missing"
    assert row["reason"] == "expected_diff_baseline_missing"
    assert row["expected_diff_approval_status"] == "approved"
    assert plan.unused_expected_diff_approvals == ()


def test_review_action_apply_readiness_blocks_missing_expected_diff() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
            )
        ]
    )
    applications = build_review_action_application_plan(
        actions,
        [_target_state("S1.raw", "5-mdC")],
    )

    plan = build_review_action_apply_readiness(applications, {})
    row = review_action_apply_readiness_to_row(plan.rows[0])

    assert row["apply_readiness_status"] == "blocked_expected_diff_missing"
    assert row["expected_diff_stable_row_id"] == (
        review_action_expected_diff_stable_row_id(actions[0])
    )
    assert row["reason"] == "approved_expected_diff_required"


def test_plan_review_action_apply_readiness_cli_writes_ready_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    actions_path = tmp_path / "review_actions.tsv"
    target_path = tmp_path / "xic_results_long.csv"
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    out_path = tmp_path / "review_action_apply_readiness.tsv"
    action = _row(
        sample_name="S1.raw",
        target_label="5-mdC",
        action_type="select_candidate",
        candidate_id="S1.raw|5-mdC|candidate-a",
        expected_diff_required="TRUE",
        reviewer="analyst",
        reviewed_at="2026-06-15T10:00:00",
    )
    _write_tsv(actions_path, [action])
    target_path.write_text(
        "SampleName,Target,Product State,Counted Detection,Review State\n"
        "S1.raw,5-mdC,review_required,FALSE,manual\n",
        encoding="utf-8",
    )
    parsed_action = parse_review_actions([action])[0]
    _write_expected_diff_tsv(
        approval_path,
        [
            _approved_expected_diff_row_for_action(
                parsed_action,
                _target_state(
                    "S1.raw",
                    "5-mdC",
                    product_state="review_required",
                    counted_detection="FALSE",
                    review_state="manual",
                ),
            )
        ],
    )

    assert (
        plan_review_action_apply_readiness.main(
            [
                "--review-actions",
                str(actions_path),
                "--targeted-long-csv",
                str(target_path),
                "--expected-diff-approvals",
                str(approval_path),
                "--output-apply-readiness-tsv",
                str(out_path),
                "--summary-json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["ready_count"] == 1
    assert "ready_expected_diff_approved" in out_path.read_text(encoding="utf-8")


def test_plan_review_action_apply_readiness_cli_rejects_unused_approval(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    actions_path = tmp_path / "review_actions.tsv"
    target_path = tmp_path / "xic_results_long.csv"
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    out_path = tmp_path / "review_action_apply_readiness.tsv"
    _write_tsv(
        actions_path,
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            )
        ],
    )
    target_path.write_text(
        "SampleName,Target,Product State,Counted Detection,Review State\n"
        "S1.raw,5-mdC,counted,TRUE,auto\n",
        encoding="utf-8",
    )
    unused_action = parse_review_actions(
        [
            _row(
                sample_name="S2.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="S2.raw|5-mdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            )
        ]
    )[0]
    _write_expected_diff_tsv(
        approval_path,
        [_approved_expected_diff_row_for_action(unused_action)],
    )

    assert (
        plan_review_action_apply_readiness.main(
            [
                "--review-actions",
                str(actions_path),
                "--targeted-long-csv",
                str(target_path),
                "--expected-diff-approvals",
                str(approval_path),
                "--output-apply-readiness-tsv",
                str(out_path),
            ]
        )
        == 2
    )
    assert "unused expected-diff approval row" in capsys.readouterr().err
    assert not out_path.exists()


def test_review_action_candidate_sidecar_plan_verifies_select_candidate() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="candidate-b",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-17T10:00:00",
                comment="switch to the stronger candidate",
            )
        ]
    )

    checks = build_review_action_candidate_sidecars(
        actions,
        [
            _peak_candidate_row("S1.raw", "5-mdC", "candidate-a", selected="TRUE"),
            _peak_candidate_row(
                "S1.raw",
                "5-mdC",
                "candidate-b",
                selected="FALSE",
                confidence="high",
                rt_left_min="8.10",
                rt_apex_min="8.42",
                rt_right_min="8.80",
                area_baseline_corrected="12345.6",
            ),
        ],
    )
    row = review_action_candidate_sidecar_to_row(checks[0])

    assert row["schema_version"] == REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION
    assert row["candidate_sidecar_status"] == "candidate_verified"
    assert row["candidate_sidecar_reason"] == "candidate_id_matched_sidecar"
    assert row["candidate_row_sha256"]
    assert row["candidate_selected"] == "FALSE"
    assert row["candidate_confidence"] == "high"
    assert row["candidate_rt_apex_min"] == "8.42"
    assert summarize_review_action_candidate_sidecars(checks) == {
        "schema_version": REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION,
        "row_count": 1,
        "verified_count": 1,
        "blocked_count": 0,
        "noop_current_selection_count": 0,
        "counts_by_status": {"candidate_verified": 1},
    }


def test_review_action_candidate_sidecar_plan_marks_current_selection_noop() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="candidate-a",
                expected_diff_required="TRUE",
            )
        ]
    )

    checks = build_review_action_candidate_sidecars(
        actions,
        [_peak_candidate_row("S1.raw", "5-mdC", "candidate-a", selected="TRUE")],
    )
    row = review_action_candidate_sidecar_to_row(checks[0])

    assert row["candidate_sidecar_status"] == "candidate_current_selection"
    assert row["candidate_sidecar_reason"] == "candidate_id_already_current_selected"
    assert row["candidate_selected"] == "TRUE"
    assert summarize_review_action_candidate_sidecars(checks) == {
        "schema_version": REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION,
        "row_count": 1,
        "verified_count": 0,
        "blocked_count": 0,
        "noop_current_selection_count": 1,
        "counts_by_status": {"candidate_current_selection": 1},
    }


def test_review_action_candidate_sidecar_plan_fails_closed() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="missing-candidate",
                expected_diff_required="TRUE",
            ),
            _row(
                sample_name="S2.raw",
                target_label="5-hmdC",
                action_type="select_candidate",
                candidate_id="duplicate-candidate",
                expected_diff_required="TRUE",
            ),
            _row(
                sample_name="S3.raw",
                target_label="5-fC",
                action_type="select_candidate",
                candidate_id="no-target-rows",
                expected_diff_required="TRUE",
            ),
        ]
    )

    checks = build_review_action_candidate_sidecars(
        actions,
        [
            _peak_candidate_row("S1.raw", "5-mdC", "other-candidate"),
            _peak_candidate_row("S2.raw", "5-hmdC", "duplicate-candidate"),
            _peak_candidate_row("S2.raw", "5-hmdC", "duplicate-candidate"),
        ],
    )
    rows = [review_action_candidate_sidecar_to_row(check) for check in checks]

    assert [row["candidate_sidecar_status"] for row in rows] == [
        "candidate_missing",
        "candidate_duplicate",
        "target_candidate_rows_missing",
    ]
    assert summarize_review_action_candidate_sidecars(checks)["blocked_count"] == 3


def test_review_action_candidate_sidecar_plan_blocks_duplicate_actions() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="candidate-a",
                expected_diff_required="TRUE",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="candidate-b",
                expected_diff_required="TRUE",
            ),
        ]
    )

    checks = build_review_action_candidate_sidecars(
        actions,
        [
            _peak_candidate_row("S1.raw", "5-mdC", "candidate-a"),
            _peak_candidate_row("S1.raw", "5-mdC", "candidate-b"),
        ],
    )
    rows = [review_action_candidate_sidecar_to_row(check) for check in checks]

    assert [row["candidate_sidecar_status"] for row in rows] == [
        "action_duplicate",
        "action_duplicate",
    ]
    assert {row["candidate_sidecar_reason"] for row in rows} == {
        "multiple_select_candidate_actions_for_target"
    }
    assert summarize_review_action_candidate_sidecars(checks) == {
        "schema_version": REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION,
        "row_count": 2,
        "verified_count": 0,
        "blocked_count": 2,
        "noop_current_selection_count": 0,
        "counts_by_status": {"action_duplicate": 2},
    }


def test_plan_review_action_candidate_sidecars_cli_writes_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    actions_path = tmp_path / "review_actions.tsv"
    peak_candidates_path = tmp_path / "peak_candidates.tsv"
    output_path = tmp_path / "review_action_candidate_sidecars.tsv"
    _write_tsv(
        actions_path,
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="select_candidate",
                candidate_id="candidate-b",
                expected_diff_required="TRUE",
            )
        ],
    )
    _write_peak_candidates_tsv(
        peak_candidates_path,
        [_peak_candidate_row("S1.raw", "5-mdC", "candidate-b")],
    )

    assert (
        plan_review_action_candidate_sidecars.main(
            [
                "--review-actions",
                str(actions_path),
                "--peak-candidates-tsv",
                str(peak_candidates_path),
                "--output-candidate-sidecar-tsv",
                str(output_path),
                "--summary-json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["verified_count"] == 1
    assert REVIEW_ACTION_CANDIDATE_SIDECAR_SCHEMA_VERSION in output_path.read_text(
        encoding="utf-8"
    )


def test_review_action_apply_changesets_describe_pending_operations(
    tmp_path: Path,
) -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC-select",
                action_type="select_candidate",
                candidate_id="S1.raw|5-mdC-select|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC-boundary",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
                comment="manual boundary",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC-reject",
                action_type="reject_current",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
                comment="bad integration",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-hmdC",
                action_type="mark_unresolved",
            ),
        ]
    )
    applications = build_review_action_application_plan(
        actions,
        [
            _target_state(action.sample_name, action.target_label)
            if not action.product_mutating
            else _target_state(
                action.sample_name,
                action.target_label,
                product_state="review_required",
                counted_detection="FALSE",
                review_state="manual",
            )
            for action in actions
        ],
    )
    expected_diff_states = {
        (application.action.sample_name, application.action.target_label): (
            application.target_state
        )
        for application in applications
    }
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(
        approval_path,
        [
            _approved_expected_diff_row_for_action(
                action,
                expected_diff_states[(action.sample_name, action.target_label)],
            )
            for action in actions[1:4]
        ],
    )
    approvals = load_review_action_expected_diff_approvals(approval_path)
    readiness_plan = build_review_action_apply_readiness(applications, approvals)

    changeset_plan = build_review_action_apply_changesets(readiness_plan)
    rows = [review_action_apply_changeset_to_row(row) for row in changeset_plan.rows]

    assert rows[0]["changeset_status"] == "ready_audit_only"
    assert rows[0]["operation"] == "record_accept_current"
    assert rows[1]["operation"] == "select_candidate"
    assert rows[1]["requires_candidate_sidecar"] is True
    assert rows[2]["operation"] == "set_manual_boundary"
    assert rows[2]["requires_area_recompute"] is True
    assert rows[3]["operation"] == "reject_current"
    assert rows[3]["proposed_counted_detection"] == "FALSE"
    assert rows[4]["changeset_status"] == "ready_review_state_only"
    assert rows[4]["operation"] == "mark_unresolved"
    assert rows[4]["proposed_review_state"] == "unresolved_by_review"
    assert summarize_review_action_apply_changeset_plan(changeset_plan) == {
        "schema_version": REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
        "row_count": 5,
        "ready_count": 5,
        "blocked_count": 0,
        "requires_area_recompute_count": 1,
        "requires_candidate_sidecar_count": 1,
        "counts_by_status": {
            "ready_audit_only": 1,
            "ready_pending_product_writer": 3,
            "ready_review_state_only": 1,
        },
        "counts_by_operation": {
            "mark_unresolved": 1,
            "record_accept_current": 1,
            "reject_current": 1,
            "select_candidate": 1,
            "set_manual_boundary": 1,
        },
    }


def test_plan_review_action_apply_changesets_cli_writes_changeset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    actions_path = tmp_path / "review_actions.tsv"
    target_path = tmp_path / "xic_results_long.csv"
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    out_path = tmp_path / "review_action_apply_changeset.tsv"
    action = _row(
        sample_name="S1.raw",
        target_label="5-mdC",
        action_type="set_manual_boundary",
        rt_left_min="8.1",
        rt_apex_min="8.4",
        rt_right_min="8.8",
        expected_diff_required="TRUE",
        reviewer="analyst",
        reviewed_at="2026-06-15T10:00:00",
        comment="manual boundary",
    )
    _write_tsv(actions_path, [action])
    target_path.write_text(
        "SampleName,Target,Product State,Counted Detection,Review State\n"
        "S1.raw,5-mdC,review_required,FALSE,manual\n",
        encoding="utf-8",
    )
    parsed_action = parse_review_actions([action])[0]
    _write_expected_diff_tsv(
        approval_path,
        [
            _approved_expected_diff_row_for_action(
                parsed_action,
                _target_state(
                    "S1.raw",
                    "5-mdC",
                    product_state="review_required",
                    counted_detection="FALSE",
                    review_state="manual",
                ),
            )
        ],
    )

    assert (
        plan_review_action_apply_changesets.main(
            [
                "--review-actions",
                str(actions_path),
                "--targeted-long-csv",
                str(target_path),
                "--expected-diff-approvals",
                str(approval_path),
                "--output-changeset-tsv",
                str(out_path),
                "--summary-json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["requires_area_recompute_count"] == 1
    output = out_path.read_text(encoding="utf-8")
    assert REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION in output
    assert "set_manual_boundary" in output
    assert "ready_pending_product_writer" in output


def test_apply_review_action_changesets_writes_audited_output_copy(
    tmp_path: Path,
) -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="accept",
                action_type="accept_current",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            ),
            _row(
                sample_name="S1.raw",
                target_label="unresolved",
                action_type="mark_unresolved",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:01:00",
                comment="needs manual science review",
            ),
            _row(
                sample_name="S1.raw",
                target_label="reject",
                action_type="reject_current",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:02:00",
                comment="bad integration",
            ),
            _row(
                sample_name="S1.raw",
                target_label="boundary",
                action_type="set_manual_boundary",
                rt_left_min="8.1",
                rt_apex_min="8.4",
                rt_right_min="8.8",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:03:00",
                comment="manual boundary",
            ),
        ]
    )
    target_states = [
        _target_state(action.sample_name, action.target_label)
        if not action.product_mutating
        else _target_state(
            action.sample_name,
            action.target_label,
            product_state="review_required",
            counted_detection="FALSE",
            review_state="manual",
        )
        for action in actions
    ]
    applications = build_review_action_application_plan(actions, target_states)
    approval_path = tmp_path / "review_action_expected_diff.tsv"
    _write_expected_diff_tsv(
        approval_path,
        [
            _approved_expected_diff_row_for_action(
                action,
                applications[index].target_state,
            )
            for index, action in enumerate(actions)
            if action.product_mutating
        ],
    )
    readiness_plan = build_review_action_apply_readiness(
        applications,
        load_review_action_expected_diff_approvals(approval_path),
    )
    changeset_plan = build_review_action_apply_changesets(readiness_plan)
    changeset_rows = [
        review_action_apply_changeset_to_row(row)
        for row in changeset_plan.rows
    ]
    targeted_rows = [
        {
            "SampleName": "S1.raw",
            "Target": "accept",
            "Product State": "accepted",
            "Counted Detection": "TRUE",
            "Review State": "auto",
        },
        {
            "SampleName": "S1.raw",
            "Target": "unresolved",
            "Product State": "review_required",
            "Counted Detection": "FALSE",
            "Review State": "manual",
        },
        {
            "SampleName": "S1.raw",
            "Target": "reject",
            "Product State": "review_required",
            "Counted Detection": "FALSE",
            "Review State": "manual",
        },
        {
            "SampleName": "S1.raw",
            "Target": "boundary",
            "Product State": "review_required",
            "Counted Detection": "FALSE",
            "Review State": "manual",
        },
    ]

    result = apply_review_action_changeset_rows(targeted_rows, changeset_rows)

    by_target = {row["Target"]: row for row in result.targeted_rows}
    assert by_target["accept"]["Review State"] == "auto"
    assert by_target["accept"]["Review Action Apply Status"] == "audit_recorded"
    assert by_target["unresolved"]["Review State"] == "unresolved_by_review"
    assert by_target["unresolved"]["Review Action Apply Status"] == (
        "applied_review_state"
    )
    assert by_target["reject"]["Product State"] == "rejected_by_review"
    assert by_target["reject"]["Counted Detection"] == "FALSE"
    assert by_target["reject"]["Review State"] == "rejected_by_review"
    assert by_target["reject"]["Review Action Apply Status"] == "applied_product_state"
    assert by_target["boundary"]["Review State"] == "manual"
    assert by_target["boundary"]["Review Action Apply Status"] == (
        "deferred_area_recompute"
    )
    assert result.summary == {
        "schema_version": REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION,
        "targeted_row_count": 4,
        "audit_row_count": 4,
        "applied_count": 2,
        "audit_only_count": 1,
        "deferred_count": 1,
        "blocked_count": 0,
        "counts_by_status": {
            "applied_product_state": 1,
            "applied_review_state": 1,
            "audit_recorded": 1,
            "deferred_area_recompute": 1,
        },
    }


def test_apply_review_action_changesets_cli_writes_copy_and_audit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    changeset_path = tmp_path / "review_action_apply_changeset.tsv"
    target_path = tmp_path / "xic_results_long.csv"
    output_path = tmp_path / "xic_results_long.review_applied.csv"
    audit_path = tmp_path / "review_action_apply_audit.tsv"
    target_path.write_text(
        "SampleName,Target,Product State,Counted Detection,Review State\n"
        "S1.raw,5-mdC,review_required,FALSE,manual\n",
        encoding="utf-8",
    )
    changeset_path.write_text(
        "\t".join(
            [
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
            ]
        )
        + "\n"
        + "\t".join(
            [
                REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
                "S1.raw",
                "5-mdC",
                "reject_current",
                "ready_pending_product_writer",
                "ready_expected_diff_approved",
                "reject_current",
                "TRUE",
                "product_state;counted_detection;targeted_long_csv;audit_trail",
                "TRUE",
                "review_action_expected_diff:abc",
                "8raw",
                "presence_changed",
                "targeted_long_csv;workbook;final_matrix",
                "manual_review",
                "reviewer confirmed bad peak",
                "",
                "",
                "",
                "",
                "",
                "rejected_by_review",
                "FALSE",
                "rejected_by_review",
                "FALSE",
                "FALSE",
                "approved_expected_diff_allows_future_reject_current",
                "analyst",
                "2026-06-15T10:00:00",
                "bad integration",
                "approved",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert (
        apply_review_action_changesets.main(
            [
                "--targeted-long-csv",
                str(target_path),
                "--changeset-tsv",
                str(changeset_path),
                "--output-targeted-long-csv",
                str(output_path),
                "--output-audit-tsv",
                str(audit_path),
                "--summary-json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["applied_count"] == 1
    assert "rejected_by_review" in output_path.read_text(encoding="utf-8")
    assert REVIEW_ACTION_APPLY_AUDIT_SCHEMA_VERSION in audit_path.read_text(
        encoding="utf-8"
    )


def test_apply_review_action_changesets_rejects_blocked_rows() -> None:
    with pytest.raises(ReviewActionError, match="blocked changeset row"):
        apply_review_action_changeset_rows(
            [
                {
                    "SampleName": "S1.raw",
                    "Target": "5-mdC",
                    "Product State": "review_required",
                    "Counted Detection": "FALSE",
                    "Review State": "manual",
                }
            ],
            [
                {
                    "schema_version": REVIEW_ACTION_APPLY_CHANGESET_SCHEMA_VERSION,
                    "sample_name": "S1.raw",
                    "target_label": "5-mdC",
                    "action_type": "reject_current",
                    "changeset_status": "blocked",
                    "operation": "",
                    "reason": "expected_diff_missing",
                }
            ],
        )


def test_plan_review_action_applications_blocks_missing_or_duplicate_targets() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="mark_unresolved",
            ),
            _row(
                sample_name="missing.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
        ]
    )

    applications = build_review_action_application_plan(
        actions,
        [_target_state("S1.raw", "5-mdC")],
    )

    assert applications[0].application_status == "planned_review_state_only"
    assert applications[1].application_status == "blocked"
    assert applications[1].reason == "target_row_missing"

    with pytest.raises(ReviewActionError, match="duplicate review target rows"):
        build_review_action_application_plan(
            actions[:1],
            [
                _target_state("S1.raw", "5-mdC"),
                _target_state("S1.raw", "5-mdC"),
            ],
        )


def test_plan_review_action_applications_blocks_multiple_actions_per_target() -> None:
    actions = parse_review_actions(
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
            ),
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="mark_unresolved",
            ),
        ]
    )

    applications = build_review_action_application_plan(
        actions,
        [_target_state("S1.raw", "5-mdC")],
    )

    assert {item.reason for item in applications} == {"multiple_actions_for_target"}
    assert {item.application_status for item in applications} == {"blocked"}


def test_plan_review_action_applications_cli_writes_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    actions_path = tmp_path / "review_actions.tsv"
    target_path = tmp_path / "xic_results_long.csv"
    out_path = tmp_path / "review_action_application_plan.tsv"
    expected_diff_path = tmp_path / "review_action_expected_diff_template.tsv"
    _write_tsv(
        actions_path,
        [
            _row(
                sample_name="S1.raw",
                target_label="5-mdC",
                action_type="accept_current",
                reviewer="analyst",
            ),
            _row(
                sample_name="S2.raw",
                target_label="5-hmdC",
                action_type="select_candidate",
                candidate_id="S2.raw|5-hmdC|candidate-a",
                expected_diff_required="TRUE",
                reviewer="analyst",
                reviewed_at="2026-06-15T10:00:00",
            ),
        ],
    )
    target_path.write_text(
        "SampleName,Target,Product State,Counted Detection,Review State\n"
        "S1.raw,5-mdC,counted,TRUE,auto\n"
        "S2.raw,5-hmdC,review_required,FALSE,manual\n",
        encoding="utf-8",
    )

    assert (
        plan_review_action_applications.main(
            [
                "--review-actions",
                str(actions_path),
                "--targeted-long-csv",
                str(target_path),
                "--output-plan-tsv",
                str(out_path),
                "--expected-diff-template-tsv",
                str(expected_diff_path),
                "--summary-json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    assert summary["application_count"] == 2
    assert summary["expected_diff_template_count"] == 1
    assert "planned_no_output_change" in out_path.read_text(encoding="utf-8")
    assert (
        REVIEW_ACTION_EXPECTED_DIFF_SCHEMA_VERSION
        in expected_diff_path.read_text(encoding="utf-8")
    )


def _row(**overrides: str) -> dict[str, str]:
    row = {header: "" for header in REVIEW_ACTION_COLUMNS}
    row["schema_version"] = REVIEW_ACTION_SCHEMA_VERSION
    row.update(overrides)
    return row


def _target_state(
    sample_name: str,
    target_label: str,
    *,
    product_state: str = "",
    counted_detection: str = "",
    review_state: str = "",
) -> ReviewActionTargetState:
    return ReviewActionTargetState(
        sample_name=sample_name,
        target_label=target_label,
        product_state=product_state,
        counted_detection=counted_detection,
        review_state=review_state,
    )


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    path.write_text(
        "\t".join(REVIEW_ACTION_COLUMNS)
        + "\n"
        + "\n".join(
            "\t".join(row.get(header, "") for header in REVIEW_ACTION_COLUMNS)
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def _approved_expected_diff_row(action: ReviewAction) -> dict[str, object]:
    return _approved_expected_diff_row_for_action(action)


def _approved_expected_diff_row_for_action(
    action: ReviewAction,
    target_state: ReviewActionTargetState | None = None,
) -> dict[str, object]:
    applications = build_review_action_application_plan(
        [action],
        [target_state or _target_state(action.sample_name, action.target_label)],
    )
    row = review_action_expected_diff_template_to_row(
        plan_review_action_expected_diff_templates(applications)[0]
    )
    row.update(
        {
            "evidence_sources": "manual_eic;8raw_parity",
            "evidence_summary": "Manual EIC review supports this action.",
            "validation_tier": "8raw",
            "reviewer_verdict": "approved",
            "final_label": "expected_diff",
            "approval_notes": "approved before product apply",
        }
    )
    return row


def _write_expected_diff_tsv(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "\t".join(REVIEW_ACTION_EXPECTED_DIFF_COLUMNS)
        + "\n"
        + "\n".join(
            "\t".join(
                _cell(row.get(header, ""))
                for header in REVIEW_ACTION_EXPECTED_DIFF_COLUMNS
            )
            for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def _peak_candidate_row(
    sample_name: str,
    target_label: str,
    candidate_id: str,
    *,
    selected: str = "FALSE",
    confidence: str = "medium",
    rt_left_min: str = "8.0",
    rt_apex_min: str = "8.4",
    rt_right_min: str = "8.8",
    area_baseline_corrected: str = "1000",
) -> dict[str, str]:
    return {
        "sample_name": sample_name,
        "target_label": target_label,
        "candidate_id": candidate_id,
        "selected": selected,
        "confidence": confidence,
        "rt_left_min": rt_left_min,
        "rt_apex_min": rt_apex_min,
        "rt_right_min": rt_right_min,
        "area_baseline_corrected": area_baseline_corrected,
    }


def _write_peak_candidates_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    headers = (
        "sample_name",
        "target_label",
        "candidate_id",
        "selected",
        "confidence",
        "rt_left_min",
        "rt_apex_min",
        "rt_right_min",
        "area_baseline_corrected",
    )
    path.write_text(
        "\t".join(headers)
        + "\n"
        + "\n".join(
            "\t".join(row.get(header, "") for header in headers) for row in rows
        )
        + "\n",
        encoding="utf-8",
    )


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value)
