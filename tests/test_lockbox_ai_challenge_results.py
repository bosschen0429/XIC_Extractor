import json
from pathlib import Path

import pytest

from scripts.build_lockbox_ai_challenge_pack import (
    AI_CHALLENGE_QUEUE,
    AUTHORITY_FIELDS,
    NO_AUTHORITY,
    TEMPLATE_HEADER,
)
from scripts.check_lockbox_ai_challenge_results import (
    AI_CHALLENGE_RESULT_LOG,
    AI_CHALLENGE_RESULT_SUMMARY,
    build_lockbox_ai_challenge_result_summary,
    check_lockbox_ai_challenge_results,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_lockbox_ai_challenge_results_validate() -> None:
    assert check_lockbox_ai_challenge_results() == []


@pytest.mark.ledger_snapshot
def test_current_ai_challenge_result_summary_is_non_authoritative() -> None:
    rows = list(read_tsv_required(AI_CHALLENGE_RESULT_LOG, TEMPLATE_HEADER))
    summary = json.loads(AI_CHALLENGE_RESULT_SUMMARY.read_text(encoding="utf-8"))

    assert len(rows) == 72
    assert summary["case_counts"] == {
        "total_cases": 72,
        "visual_challenge_cases": 53,
        "route_or_evidence_integrity_cases": 19,
        "flagged_cases": 0,
        "product_authority_rows": 0,
    }
    assert summary["decision"] == "ai_challenge_no_owner_recheck_required"
    assert summary["challenge_result_counts"] == {
        "no_issue": 72,
    }
    assert summary["challenge_reason_counts"][
        "owner_rule_detected_left_peak_resolved"
    ] == 1
    assert summary["flagged_cases"] == []
    assert all(value is False for value in summary["authority_rules"].values())
    owner_rule_row = next(
        row
        for row in rows
        if row["lockbox_case_id"] == "LOCKBOXV1_60CEB35837FAF38CC4DE9021"
    )
    assert owner_rule_row["challenge_result"] == "no_issue"
    assert (
        owner_rule_row["challenge_reason_code"]
        == "owner_rule_detected_left_peak_resolved"
    )
    assert "cell_apex_rt=15.1553" in owner_rule_row["challenge_notes"]
    assert "right_peak_rt=15.4366" in owner_rule_row["challenge_notes"]
    for row in rows:
        for field in AUTHORITY_FIELDS:
            assert row[field] == NO_AUTHORITY


def test_ai_challenge_results_reject_nonvisual_visual_contradiction(
    tmp_path: Path,
) -> None:
    result_log, result_summary = _copy_current_results(tmp_path)
    queue_rows = list(
        read_tsv_required(
            AI_CHALLENGE_QUEUE,
            required_columns=("lockbox_case_id", "challenge_scope"),
        ),
    )
    nonvisual_case_id = next(
        row["lockbox_case_id"]
        for row in queue_rows
        if row["challenge_scope"] != "visual_contradiction_challenge"
    )
    rows = list(read_tsv_required(result_log, TEMPLATE_HEADER))
    for row in rows:
        if row["lockbox_case_id"] == nonvisual_case_id:
            row["challenge_result"] = "visual_contradiction_suspected"
            row["challenge_reason_code"] = "visual_contradiction_suspected"
            break
    write_tsv(result_log, rows, TEMPLATE_HEADER)

    problems = check_lockbox_ai_challenge_results(
        ai_challenge_result_log_path=result_log,
        ai_challenge_result_summary_path=result_summary,
    )

    assert any("not allowed for case" in problem for problem in problems)
    assert any(
        "visual_contradiction_suspected is visual-scope only" in problem
        for problem in problems
    )


def test_ai_challenge_results_reject_authority_flag(tmp_path: Path) -> None:
    result_log, result_summary = _copy_current_results(tmp_path)
    rows = list(read_tsv_required(result_log, TEMPLATE_HEADER))
    rows[0]["may_feed_product_writer"] = "TRUE"
    write_tsv(result_log, rows, TEMPLATE_HEADER)

    problems = check_lockbox_ai_challenge_results(
        ai_challenge_result_log_path=result_log,
        ai_challenge_result_summary_path=result_summary,
    )

    assert any(
        "may_feed_product_writer must be FALSE" in problem for problem in problems
    )


def test_ai_challenge_results_reject_generic_owner_rule_reason(
    tmp_path: Path,
) -> None:
    result_log, result_summary = _copy_current_results(tmp_path)
    rows = list(read_tsv_required(result_log, TEMPLATE_HEADER))
    generic_row = next(
        row
        for row in rows
        if row["lockbox_case_id"] != "LOCKBOXV1_60CEB35837FAF38CC4DE9021"
    )
    generic_row["challenge_reason_code"] = "owner_rule_detected_left_peak_resolved"
    generic_row["challenge_notes"] = "left peak accepted"
    write_tsv(result_log, rows, TEMPLATE_HEADER)

    problems = check_lockbox_ai_challenge_results(
        ai_challenge_result_log_path=result_log,
        ai_challenge_result_summary_path=result_summary,
    )

    assert any(
        "owner_rule_detected_left_peak_resolved is case-specific" in p
        for p in problems
    )
    assert any("missing note token cell_apex_rt=15.1553" in p for p in problems)


def test_ai_challenge_results_reject_stale_summary_authority(
    tmp_path: Path,
) -> None:
    result_log, result_summary = _copy_current_results(tmp_path)
    payload = json.loads(result_summary.read_text(encoding="utf-8"))
    payload["authority_rules"]["may_touch_matrix"] = True
    result_summary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    problems = check_lockbox_ai_challenge_results(
        ai_challenge_result_log_path=result_log,
        ai_challenge_result_summary_path=result_summary,
    )

    assert any("AI challenge result summary JSON is stale" in p for p in problems)
    assert any(
        "AI challenge result summary authority_rules.may_touch_matrix must be false"
        in p
        for p in problems
    )


def _copy_current_results(tmp_path: Path) -> tuple[Path, Path]:
    result_log = tmp_path / "result_log.tsv"
    result_summary = tmp_path / "result_summary.json"
    rows = list(read_tsv_required(AI_CHALLENGE_RESULT_LOG, TEMPLATE_HEADER))
    write_tsv(result_log, rows, TEMPLATE_HEADER)
    build_lockbox_ai_challenge_result_summary(
        ai_challenge_result_log_path=result_log,
        ai_challenge_result_summary_path=result_summary,
    )
    return result_log, result_summary
