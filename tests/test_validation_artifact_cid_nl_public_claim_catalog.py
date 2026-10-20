from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts import check_cid_nl_public_output_claim_catalog as claim_catalog


@pytest.mark.ledger_snapshot
def test_current_cid_nl_public_output_claim_catalog_is_valid() -> None:
    assert claim_catalog.check_cid_nl_public_output_claim_catalog() == []


def test_claim_catalog_rejects_characterization_as_authority(
    tmp_path: Path,
) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    rows[0]["existing_authority_owner"] = (
        "docs/agent/architecture-public-contracts.md#Public Contracts; "
        "docs/superpowers/validation/"
        "cid_nl_targeted_csv_contract/README.md"
    )
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any(
        "characterization evidence cannot be an authority owner" in problem
        for problem in problems
    )


def test_claim_catalog_rejects_missing_executable_gate(tmp_path: Path) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    rows[0]["executable_gate"] = (
        "tests/test_csv_writers.py::test_gate_that_does_not_exist"
    )
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any("executable gate does not resolve" in problem for problem in problems)


def test_claim_catalog_rejects_missing_python_owner_symbol(tmp_path: Path) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    rows[0]["existing_authority_owner"] = (
        "xic_extractor/output/csv_writers.py::owner_that_does_not_exist"
    )
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any(
        "authority owner anchor does not resolve" in problem for problem in problems
    )


def test_claim_catalog_rejects_missing_markdown_heading(tmp_path: Path) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    rows[0]["evidence_or_fixture"] = (
        "docs/superpowers/validation/cid_nl_targeted_csv_contract/"
        "README.md#Missing Heading"
    )
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any(
        "evidence or fixture anchor does not resolve" in problem for problem in problems
    )


def test_claim_catalog_marks_matrix_boundary_as_reference_only(
    tmp_path: Path,
) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    matrix_row = next(
        row
        for row in rows
        if row["claim_id"] == "untargeted_matrix.authority_boundary_ref"
    )
    matrix_row["public_surface"] = "hard_exact: targeted Matrix authority"
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any(
        "authority references must be marked reference_only" in problem
        for problem in problems
    )


@pytest.mark.ledger_snapshot
def test_reference_rows_preserve_existing_owner_boundaries() -> None:
    rows = {
        row["claim_id"]: row for row in _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    }
    selected = rows["targeted_output.selected_value_authority_ref"]
    counted = rows["targeted_output.counted_detection_authority_ref"]
    matrix = rows["untargeted_matrix.authority_boundary_ref"]

    for row in (selected, counted, matrix):
        assert row["public_surface"].startswith("reference_only:")
        assert row["disposition"] == "retain"
    for row in (selected, counted):
        assert "docs/product/decision-policy.md" in row["existing_authority_owner"]
        assert "docs/product/targeted-selection.md" in row["existing_authority_owner"]
    assert "docs/product/quant-matrix.md" in matrix["existing_authority_owner"]
    assert (
        "productization_authority_manifest.json" in matrix["existing_authority_owner"]
    )
    assert "targeted-selection.md" not in matrix["existing_authority_owner"]


def test_claim_catalog_rejects_repo_external_reference(tmp_path: Path) -> None:
    rows = _read_rows(claim_catalog.DEFAULT_CATALOG_PATH)
    rows[0]["existing_authority_owner"] = "../outside.md"
    catalog_path = tmp_path / "claim_catalog.tsv"
    _write_rows(catalog_path, rows)

    problems = claim_catalog.check_cid_nl_public_output_claim_catalog(
        catalog_path=catalog_path
    )

    assert any(
        "is not a repo-relative clean-checkout path" in problem for problem in problems
    )


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=claim_catalog.CATALOG_COLUMNS,
            delimiter="\t",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
