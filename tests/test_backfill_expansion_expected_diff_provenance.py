from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_backfill_expansion_expected_diff_provenance import (
    CHECK_COLUMNS,
    DEFAULT_DOCS_DIR,
    EXPECTED_COUNTS,
    ROW_MANIFEST_COLUMNS,
    check_backfill_expansion_expected_diff_provenance,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_expected_diff_provenance_is_stable() -> None:
    assert check_backfill_expansion_expected_diff_provenance() == []

    summary = json.loads(
        (
            DEFAULT_DOCS_DIR
            / "backfill_expansion_expected_diff_provenance_summary.json"
        ).read_text(encoding="utf-8"),
    )

    assert summary["validation_status"] == "production_candidate_contract_only"
    assert summary["candidate_cell_count"] == 666
    assert summary["candidate_peak_count"] == 20
    assert summary["baseline_blank_cell_count"] == 666
    assert summary["manifest_row_count"] == 666
    assert summary["expected_diff_count"] == 666
    assert summary["dry_run_written_backfill_count"] == 666
    assert summary["unused_expected_diff_count"] == 0
    assert summary["cell_provenance_accepted_count"] == 666
    assert summary["matrix_changed_cell_count"] == 666
    assert summary["held_cell_count"] == 263
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["validation_dry_run_matrix_written"] is True


def test_missing_required_expected_diff_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "dry_run_written_backfill_count"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any("checks missing required ids" in problem for problem in problems)


def test_summary_public_writer_overclaim_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["product_writer_changed"] = True
    payload["default_quant_matrix_changed"] = True
    payload["default_matrix_files_written"] = True
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary product_writer_changed mismatch" in problem for problem in problems
    )
    assert any(
        "summary default_quant_matrix_changed mismatch" in problem
        for problem in problems
    )
    assert any(
        "summary default_matrix_files_written mismatch" in problem
        for problem in problems
    )


def test_summary_candidate_count_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["candidate_cell_count"] = EXPECTED_COUNTS["candidate_cell_count"] + 1
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary candidate_cell_count mismatch" in problem for problem in problems
    )


def test_row_manifest_active_authority_overclaim_fails_closed(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    rows = list(read_tsv_required(row_manifest_tsv, ROW_MANIFEST_COLUMNS))
    rows[0]["product_authority_effect"] = "active_writer_lane"
    write_tsv(row_manifest_tsv, rows, ROW_MANIFEST_COLUMNS, extrasaction="raise")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "row manifest product_authority_effect mismatch" in problem
        for problem in problems
    )


def test_summary_artifact_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["artifacts"]["expected_diff"]["path"] = (
        "docs/superpowers/validation/"
        "backfill_expansion_expected_diff_provenance_v1/"
        "backfill_expansion_expected_diff_provenance_summary.json"
    )
    payload["artifacts"]["expected_diff"]["sha256"] = "BAD"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary artifacts expected_diff sha256 mismatch" in problem
        for problem in problems
    )


def test_missing_externalized_expected_diff_artifacts_do_not_block_clean_checkout(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    for label in (
        "production_acceptance_manifest",
        "expected_diff",
        "expected_diff_summary",
        "cell_provenance",
    ):
        payload["artifacts"][label]["path"] = (
            "output/validation/__missing_expected_diff_provenance/"
            f"{label}.tsv"
        )
        payload["artifacts"][label]["sha256"] = "A" * 64
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert problems == []


def test_missing_retained_expected_diff_artifact_fails_closed(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, row_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["artifacts"]["expected_diff"]["path"] = (
        "docs/superpowers/validation/"
        "__missing_expected_diff_provenance_expected_diff.tsv"
    )
    payload["artifacts"]["expected_diff"]["sha256"] = "A" * 64
    payload["artifacts"]["expected_diff"]["retention_decision"] = "externalize"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_backfill_expansion_expected_diff_provenance(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=row_manifest_tsv,
    )

    assert any(
        "summary artifacts expected_diff missing" in problem
        for problem in problems
    )


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    row_manifest_tsv = tmp_path / "row_manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_expected_diff_provenance_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_expected_diff_provenance_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_expected_diff_provenance_row_manifest.tsv",
        row_manifest_tsv,
    )
    return summary_json, checks_tsv, row_manifest_tsv
