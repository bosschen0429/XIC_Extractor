from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_backfill_expansion_raw_overlay_trace_identity import (
    CHECK_COLUMNS,
    DEFAULT_DOCS_DIR,
    EXPECTED_COUNTS,
    ROW_MANIFEST_COLUMNS,
    check_backfill_expansion_raw_overlay_trace_identity,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_raw_overlay_trace_identity_is_stable() -> None:
    assert check_backfill_expansion_raw_overlay_trace_identity() == []

    summary = json.loads(
        (
            DEFAULT_DOCS_DIR
            / "backfill_expansion_raw_overlay_trace_identity_summary.json"
        ).read_text(encoding="utf-8"),
    )

    assert summary["active_pressure_cell_count"] == 929
    assert summary["alignment_cell_evidence_present_cell_count"] == 675
    assert summary["alignment_cell_evidence_missing_cell_count"] == 254
    assert summary["overlay_batch_success_row_count"] == 20
    assert summary["overlay_batch_support_row_count"] == 20
    assert summary["raw_trace_row_present_cell_count"] == 675
    assert summary["raw_trace_observed_cell_count"] == 666
    assert summary["raw_trace_absent_cell_count"] == 9
    assert summary["expected_diff_design_candidate_cell_count"] == 666
    assert summary["held_cell_count"] == 263
    assert summary["raw_or_85raw_ran"] is True
    assert summary["raw_alignment_rerun"] is False
    assert summary["backfill_writer_authority_changed_by_checker"] is False
    assert summary["default_quant_matrix_changed_by_checker"] is False


def test_missing_required_trace_identity_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "raw_trace_observed_count"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any("checks missing required ids" in problem for problem in problems)
    assert any("summary checks_tsv sha256 mismatch" in problem for problem in problems)


def test_row_manifest_authority_overclaim_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = list(read_tsv_required(row_manifest_tsv, ROW_MANIFEST_COLUMNS))
    rows[0]["product_authority_effect"] = "write_backfill_now"
    write_tsv(row_manifest_tsv, rows, ROW_MANIFEST_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any("product_authority_effect mismatch" in problem for problem in problems)
    assert any(
        "summary row_manifest_tsv sha256 mismatch" in problem
        for problem in problems
    )


def test_summary_writer_or_matrix_overclaim_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["backfill_writer_authority_changed_by_checker"] = True
    payload["default_quant_matrix_changed_by_checker"] = True
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary backfill_writer_authority_changed_by_checker mismatch" in problem
        for problem in problems
    )
    assert any(
        "summary default_quant_matrix_changed_by_checker mismatch" in problem
        for problem in problems
    )


def test_summary_trace_observed_count_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["raw_trace_observed_cell_count"] = (
        EXPECTED_COUNTS["raw_trace_observed_cell_count"] + 1
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary raw_trace_observed_cell_count mismatch" in problem
        for problem in problems
    )


def test_summary_overlay_input_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["input_artifacts"]["sample_local_checks_tsv"]["sha256"] = "BAD"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary input_artifacts sample_local_checks_tsv sha256 mismatch"
        in problem
        for problem in problems
    )


def test_summary_trace_artifact_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    first_family = sorted(payload["overlay_trace_summary_artifacts"])[0]
    trace_artifact = tmp_path / "trace-summary.json"
    trace_artifact.write_text("{}\n", encoding="utf-8")
    payload["overlay_trace_summary_artifacts"][first_family]["path"] = str(
        trace_artifact,
    )
    payload["overlay_trace_summary_artifacts"][first_family]["sha256"] = "BAD"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_raw_overlay_trace_identity(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any("overlay trace artifact" in problem for problem in problems)
    assert any("sha256 mismatch" in problem for problem in problems)


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    row_manifest_tsv = tmp_path / "row_manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_raw_overlay_trace_identity_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_raw_overlay_trace_identity_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_raw_overlay_trace_identity_row_manifest.tsv",
        row_manifest_tsv,
    )
    return summary_json, checks_tsv, row_manifest_tsv
