from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_backfill_expansion_evidence_availability import (
    CHECK_COLUMNS,
    DEFAULT_DOCS_DIR,
    EXPECTED_COUNTS,
    ROW_MANIFEST_COLUMNS,
    check_backfill_expansion_evidence_availability,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_evidence_availability_is_stable() -> None:
    assert check_backfill_expansion_evidence_availability() == []

    summary = json.loads(
        (
            DEFAULT_DOCS_DIR
            / "backfill_expansion_evidence_availability_summary.json"
        ).read_text(encoding="utf-8"),
    )

    assert summary["active_pressure_cell_count"] == 929
    assert summary["active_mechanical_covered_cell_count"] == 0
    assert summary["active_trace_recovered_cell_count"] == 0
    assert summary["active_immediate_expected_diff_ready_cell_count"] == 0
    assert summary["active_requires_new_sample_local_evidence_cell_count"] == 929
    assert summary["release_decision"] == (
        "hold_for_new_sample_local_ms1_identity_evidence"
    )
    assert summary["backfill_writer_authority_changed_by_checker"] is False
    assert summary["default_quant_matrix_changed_by_checker"] is False
    assert summary["row_or_family_evidence_projected_to_cells"] is False


def test_missing_required_availability_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "active_immediate_ready_count_zero"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_evidence_availability(
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

    problems = check_backfill_expansion_evidence_availability(
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

    problems = check_backfill_expansion_evidence_availability(
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


def test_summary_ready_count_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["active_immediate_expected_diff_ready_cell_count"] = (
        EXPECTED_COUNTS["active_immediate_expected_diff_ready_cell_count"] + 1
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_evidence_availability(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary active_immediate_expected_diff_ready_cell_count mismatch" in problem
        for problem in problems
    )


def test_summary_input_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["input_artifacts"]["mechanical_adjudication_index_tsv"]["sha256"] = (
        "BAD"
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_evidence_availability(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary input_artifacts mechanical_adjudication_index_tsv sha256 mismatch"
        in problem
        for problem in problems
    )


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    row_manifest_tsv = tmp_path / "row_manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "backfill_expansion_evidence_availability_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "backfill_expansion_evidence_availability_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_evidence_availability_row_manifest.tsv",
        row_manifest_tsv,
    )
    return summary_json, checks_tsv, row_manifest_tsv
