from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.build_backfill_expansion_default_product_activation import (
    CHECK_COLUMNS,
    COMPACT_MANIFEST_COLUMNS,
    DEFAULT_DOCS_DIR,
    EXPECTED_COUNTS,
    PRODUCT_AUTHORITY_SCOPE,
    validate_backfill_expansion_default_product_activation,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_backfill_expansion_default_activation_is_stable() -> None:
    assert validate_backfill_expansion_default_product_activation() == []

    summary = json.loads(
        (
            DEFAULT_DOCS_DIR
            / "backfill_expansion_default_product_activation_summary.json"
        ).read_text(encoding="utf-8"),
    )

    assert summary["validation_status"] == "production_candidate"
    assert summary["activation_label"] == "backfill_expansion_candidate_packet_held"
    assert summary["product_authority_scope"] == PRODUCT_AUTHORITY_SCOPE
    assert summary["accepted_backfill_count"] == 666
    assert summary["candidate_peak_count"] == 20
    assert summary["expected_diff_count"] == "666"
    assert summary["written_backfill_count"] == "666"
    assert summary["unused_expected_diff_count"] == "0"
    assert summary["cell_provenance_accepted_count"] == 666
    assert summary["matrix_changed_cell_count"] == 666
    assert summary["held_cell_count"] == 263
    assert summary["write_authority"] is False
    assert summary["product_writer_changed"] is False
    assert summary["default_quant_matrix_changed"] is False
    assert summary["default_matrix_files_written"] is False
    assert summary["candidate_matrix_replay_written"] is True
    assert summary["candidate_replay_written_backfill_count"] == "666"
    assert summary["public_write_blocked_cell_count"] == 666
    assert "shift-aware" in summary["authority_blocker"]
    assert "own-max" in summary["authority_blocker"]
    assert summary["workbook_or_gui_changed"] is False
    assert summary["selected_peak_area_or_counting_changed"] is False
    assert summary["broad_backfill_unparked"] is False
    assert summary["candidate_rows_are_matrix_rows"] is False
    assert "stable row/cell keys" in summary["authority_statement"]


def test_missing_required_activation_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "written_backfill_count"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any("checks missing required ids" in problem for problem in problems)


def test_summary_activation_count_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["accepted_backfill_count"] = EXPECTED_COUNTS["accepted_backfill_count"] + 1
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any(
        "summary accepted_backfill_count mismatch" in problem
        for problem in problems
    )


def test_summary_product_surface_overclaim_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["workbook_or_gui_changed"] = True
    payload["selected_peak_area_or_counting_changed"] = True
    payload["selected_area_changed"] = True
    payload["counted_detection_changed"] = True
    payload["broad_backfill_unparked"] = True
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any("summary workbook_or_gui_changed mismatch" in p for p in problems)
    assert any(
        "summary selected_peak_area_or_counting_changed mismatch" in p
        for p in problems
    )
    assert any("summary selected_area_changed mismatch" in p for p in problems)
    assert any("summary counted_detection_changed mismatch" in p for p in problems)
    assert any("summary broad_backfill_unparked mismatch" in p for p in problems)


def test_compact_manifest_scope_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    rows = read_tsv_required(compact_manifest_tsv, COMPACT_MANIFEST_COLUMNS)
    rows[0]["product_authority_scope"] = "backfill_policy_write_ready_rows"
    write_tsv(
        compact_manifest_tsv,
        rows,
        COMPACT_MANIFEST_COLUMNS,
        extrasaction="raise",
    )

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any("compact manifest authority scope mismatch" in p for p in problems)


def test_summary_artifact_hash_binding_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["artifacts"]["quant_matrix"]["path"] = (
        "docs/superpowers/validation/"
        "backfill_expansion_default_product_activation_v1/"
        "backfill_expansion_default_product_activation_summary.json"
    )
    payload["artifacts"]["quant_matrix"]["sha256"] = "BAD"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any(
        "summary artifacts quant_matrix sha256 mismatch" in problem
        for problem in problems
    )


def test_missing_externalized_activation_artifacts_do_not_block_clean_checkout(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    for label in ("expected_diff_summary", "cell_provenance", "quant_matrix"):
        payload["artifacts"][label]["path"] = (
            "output/validation/__missing_default_activation/"
            f"{label}.tsv"
        )
        payload["artifacts"][label]["sha256"] = "A" * 64
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert problems == []


def test_missing_retained_activation_artifact_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, compact_manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["artifacts"]["expected_diff_summary"]["path"] = (
        "docs/superpowers/validation/"
        "__missing_default_activation_expected_diff_summary.tsv"
    )
    payload["artifacts"]["expected_diff_summary"]["sha256"] = "A" * 64
    payload["artifacts"]["expected_diff_summary"]["retention_decision"] = (
        "externalize"
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = validate_backfill_expansion_default_product_activation(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        compact_manifest_tsv=compact_manifest_tsv,
    )

    assert any(
        "summary artifacts expected_diff_summary missing" in problem
        for problem in problems
    )


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    compact_manifest_tsv = tmp_path / "compact_manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_default_product_activation_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_default_product_activation_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR
        / "backfill_expansion_default_product_activation_manifest.tsv",
        compact_manifest_tsv,
    )
    return summary_json, checks_tsv, compact_manifest_tsv
