from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.check_cid_nl_discovery_full_scope_classification import (
    CHECK_COLUMNS,
    DEFAULT_DOCS_DIR,
    MANIFEST_COLUMNS,
    check_cid_nl_discovery_full_scope_classification,
)
from xic_extractor.tabular_io import read_tsv_required, write_tsv


@pytest.mark.ledger_snapshot
def test_current_cid_nl_discovery_full_scope_classification_is_stable() -> None:
    assert check_cid_nl_discovery_full_scope_classification() == []


@pytest.mark.ledger_snapshot
def test_retained_full_scope_artifacts_use_lf_line_endings() -> None:
    for name in (
        "cid_nl_discovery_full_scope_classification_checks.tsv",
        "cid_nl_discovery_full_scope_classification_manifest.tsv",
    ):
        content = (DEFAULT_DOCS_DIR / name).read_bytes()
        assert b"\r\n" not in content


def test_missing_required_check_id_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    rows = [
        row
        for row in read_tsv_required(checks_tsv, CHECK_COLUMNS)
        if row["check_id"] != "target_300_184_source_context_preserved"
    ]
    write_tsv(checks_tsv, rows, CHECK_COLUMNS, extrasaction="raise")

    problems = check_cid_nl_discovery_full_scope_classification(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any("checks missing required ids" in problem for problem in problems)
    assert any("summary checks_tsv sha256 mismatch" in problem for problem in problems)


def test_manifest_authority_drift_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    rows = list(read_tsv_required(manifest_tsv, MANIFEST_COLUMNS))
    rows[0]["matrix_authority"] = "product_writer_authority"
    write_tsv(manifest_tsv, rows, MANIFEST_COLUMNS, extrasaction="raise")

    problems = check_cid_nl_discovery_full_scope_classification(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any(
        "manifest accepted_primary_supported matrix_authority mismatch" in problem
        for problem in problems
    )
    assert any(
        "summary manifest_tsv sha256 mismatch" in problem for problem in problems
    )


def test_stale_summary_hash_fails_closed(tmp_path: Path) -> None:
    summary_json, checks_tsv, manifest_tsv = _copy_contract(tmp_path)
    payload = json.loads(summary_json.read_text(encoding="utf-8"))
    payload["artifacts"]["checks_tsv"]["sha256"] = "STALE"
    summary_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    problems = check_cid_nl_discovery_full_scope_classification(
        summary_json=summary_json,
        checks_tsv=checks_tsv,
        manifest_tsv=manifest_tsv,
    )

    assert any("summary checks_tsv sha256 mismatch" in problem for problem in problems)


def _copy_contract(tmp_path: Path) -> tuple[Path, Path, Path]:
    summary_json = tmp_path / "summary.json"
    checks_tsv = tmp_path / "checks.tsv"
    manifest_tsv = tmp_path / "manifest.tsv"
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "cid_nl_discovery_full_scope_classification_summary.json",
        summary_json,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "cid_nl_discovery_full_scope_classification_checks.tsv",
        checks_tsv,
    )
    shutil.copyfile(
        DEFAULT_DOCS_DIR / "cid_nl_discovery_full_scope_classification_manifest.tsv",
        manifest_tsv,
    )
    return summary_json, checks_tsv, manifest_tsv
