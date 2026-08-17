from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_backfill_expansion_full_evidence_chain import (
    CHECK_COLUMNS,
    DEFAULT_CELLS_TSV,
    DEFAULT_CHECKS_TSV,
    DEFAULT_ROW_MANIFEST_TSV,
    DEFAULT_SUMMARY_JSON,
    EXPECTED_COUNTS,
    validate_backfill_expansion_full_evidence_chain,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_full_evidence_chain_is_held() -> None:
    assert validate_backfill_expansion_full_evidence_chain() == []

    summary = json.loads(DEFAULT_SUMMARY_JSON.read_text(encoding="utf-8"))

    assert summary["validation_status"] == (
        "production_candidate_held_incomplete_chain"
    )
    assert summary["candidate_cell_count"] == 666
    assert summary["full_chain_complete"] is False
    assert summary["full_chain_pass_cell_count"] == 374
    assert summary["held_cell_count"] == 292
    assert summary["ms1_product_authorized_cell_count"] == 374
    assert summary["primary_blocker_counts"] == {
        "own_max_metric_below_threshold": 99,
        "own_max_metric_missing": 19,
        "shift_aware_gate_blocked_shift_aware_same_pattern_not_supported": 174,
    }
    assert summary["write_authority"] is False
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["raw_or_85raw_ran_by_checker"] is False


@pytest.mark.ledger_snapshot
def test_require_full_chain_blocks_current_666_packet() -> None:
    problems = validate_backfill_expansion_full_evidence_chain(
        require_full_chain=True,
    )

    assert problems == [
        "full evidence chain incomplete: 374/666 cells pass; held=292",
    ]


def test_summary_full_chain_count_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv, cells_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["full_chain_pass_cell_count"] = (
        EXPECTED_COUNTS["full_chain_pass_cell_count"] + 1
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_full_evidence_chain(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=manifest_tsv,
        cells_tsv=cells_tsv,
    )

    assert any("summary full_chain_pass_cell_count mismatch" in p for p in problems)


def test_missing_required_full_chain_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv, cells_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "ms1_product_authorized_cell_count"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = validate_backfill_expansion_full_evidence_chain(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=manifest_tsv,
        cells_tsv=cells_tsv,
    )

    assert any("checks missing required ids" in p for p in problems)


def test_missing_declared_externalized_cells_tsv_is_not_clean_checkout_blocker(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, manifest_tsv, _cells_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    relpath = (
        "output/validation/__missing_full_evidence_chain/"
        "backfill_expansion_full_evidence_chain_cells.tsv"
    )
    payload["artifacts"]["cells_tsv"]["path"] = relpath
    payload["artifacts"]["cells_tsv"]["sha256"] = "A" * 64
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    missing_cells_tsv = checker_path(relpath)
    assert not missing_cells_tsv.exists()

    problems = validate_backfill_expansion_full_evidence_chain(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=manifest_tsv,
        cells_tsv=missing_cells_tsv,
    )

    assert problems == []


def test_missing_retained_cells_tsv_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv, _cells_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    relpath = (
        "docs/superpowers/validation/"
        "__missing_full_evidence_chain_cells.tsv"
    )
    payload["artifacts"]["cells_tsv"]["path"] = relpath
    payload["artifacts"]["cells_tsv"]["sha256"] = "A" * 64
    payload["artifacts"]["cells_tsv"]["retention_decision"] = "externalize"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    missing_cells_tsv = checker_path(relpath)
    assert not missing_cells_tsv.exists()

    problems = validate_backfill_expansion_full_evidence_chain(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        row_manifest_tsv=manifest_tsv,
        cells_tsv=missing_cells_tsv,
    )

    assert f"cells TSV missing: {missing_cells_tsv}" in problems
    assert any("summary artifacts cells_tsv missing" in problem for problem in problems)


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    manifest_tsv = tmp_path / "manifest.tsv"
    cells_tsv = DEFAULT_CELLS_TSV
    shutil.copyfile(DEFAULT_SUMMARY_JSON, summary_json)
    shutil.copyfile(DEFAULT_CHECKS_TSV, checks_tsv)
    shutil.copyfile(DEFAULT_ROW_MANIFEST_TSV, manifest_tsv)
    return summary_json, checks_tsv, manifest_tsv, cells_tsv


def checker_path(relpath: str) -> Path:
    return Path(__file__).resolve().parents[1] / relpath
