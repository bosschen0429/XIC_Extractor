from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_cid_nl_default_activation_universe_closure import (
    CHECK_COLUMNS,
    MANIFEST_COLUMNS,
    RETAINED_CHECKS_FILENAME,
    RETAINED_DOCS_DIR,
    RETAINED_MANIFEST_FILENAME,
    RETAINED_SUMMARY_FILENAME,
    check_cid_nl_default_activation_universe_closure,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_cid_nl_full_cohort_universe_closure_is_stable() -> None:
    assert check_cid_nl_default_activation_universe_closure() == []


def test_retained_full_cohort_closure_artifacts_use_lf_line_endings() -> None:
    for name in (
        RETAINED_CHECKS_FILENAME,
        RETAINED_MANIFEST_FILENAME,
    ):
        content = (RETAINED_DOCS_DIR / name).read_bytes()
        assert b"\r\n" not in content


def test_missing_required_closure_check_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "candidate_transition_partition_exact"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_cid_nl_default_activation_universe_closure(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any("checks missing required ids" in problem for problem in problems)
    assert any("summary checks_tsv sha256 mismatch" in problem for problem in problems)


def test_manifest_authority_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    rows = list(read_tsv_required(manifest_tsv, MANIFEST_COLUMNS))
    rows[0]["matrix_write_allowed"] = "TRUE"
    write_tsv(manifest_tsv, rows, MANIFEST_COLUMNS, extrasaction="raise")

    problems = check_cid_nl_default_activation_universe_closure(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any(
        "manifest write_authorized_candidate_universe matrix_write_allowed mismatch"
        in problem
        for problem in problems
    )
    assert any(
        "summary manifest_tsv sha256 mismatch" in problem for problem in problems
    )


def test_summary_must_bind_to_full_cohort_current_candidate_input(
    tmp_path: Path,
) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["input_artifact_binding"]["input_quant_matrix_tsv"] = (
        "output/discovery/stale/alignment_matrix.tsv"
    )
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_cid_nl_default_activation_universe_closure(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any(
        "must bind to FULL COHORT current candidate" in problem for problem in problems
    )


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    manifest_tsv = tmp_path / "manifest.tsv"
    shutil.copyfile(
        RETAINED_DOCS_DIR / RETAINED_SUMMARY_FILENAME,
        summary_json,
    )
    shutil.copyfile(
        RETAINED_DOCS_DIR / RETAINED_CHECKS_FILENAME,
        checks_tsv,
    )
    shutil.copyfile(
        RETAINED_DOCS_DIR / RETAINED_MANIFEST_FILENAME,
        manifest_tsv,
    )
    return summary_json, checks_tsv, manifest_tsv
