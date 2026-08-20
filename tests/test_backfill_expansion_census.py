from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_backfill_expansion_census import (
    CHECK_COLUMNS,
    DEFAULT_DOCS_DIR,
    EXPECTED_COUNTS,
    ROW_MANIFEST_COLUMNS,
    check_backfill_expansion_census,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_census_is_stable() -> None:
    assert check_backfill_expansion_census() == []

    summary = json.loads(
        (
            DEFAULT_DOCS_DIR / "backfill_expansion_census_summary.json"
        ).read_text(encoding="utf-8"),
    )

    assert summary["old_backfill_authority_cell_count"] == 511
    assert summary["cid_nl_active_discovery_cell_count"] == 95
    assert summary["new_backfill_pressure_candidate_cell_count"] == 929
    assert summary["parked_future_pressure_candidate_cell_count"] == 1010
    assert summary["backfill_writer_authority_changed_by_checker"] is False
    assert summary["default_quant_matrix_changed_by_checker"] is False
    assert summary["candidate_rows_are_matrix_rows"] is False


def test_missing_required_census_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "active_missing_pressure_count"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_census(
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

    problems = check_backfill_expansion_census(
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

    problems = check_backfill_expansion_census(
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


def test_summary_pressure_counts_are_fail_closed_contract(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["new_backfill_pressure_candidate_cell_count"] = (
        EXPECTED_COUNTS["new_backfill_pressure_candidate_cell_count"] + 1
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_census(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary new_backfill_pressure_candidate_cell_count mismatch" in problem
        for problem in problems
    )


def test_summary_input_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["input_artifacts"]["old_backfill_values_tsv"]["sha256"] = "BAD"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_census(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary input_artifacts old_backfill_values_tsv sha256 mismatch" in problem
        for problem in problems
    )


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    row_manifest_tsv = tmp_path / "row_manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "backfill_expansion_census_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "backfill_expansion_census_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "backfill_expansion_census_row_manifest.tsv",
        row_manifest_tsv,
    )
    return summary_json, checks_tsv, row_manifest_tsv
