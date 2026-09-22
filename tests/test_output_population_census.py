from __future__ import annotations

from pathlib import Path

from tools.diagnostics.output_population_census import (
    PopulationFinding,
    census_output_directory,
)


def test_reports_empty_single_row_and_constant_columns(tmp_path: Path) -> None:
    (tmp_path / "evidence.tsv").write_text("sample\tarea\n", encoding="utf-8")
    (tmp_path / "provenance.tsv").write_text(
        "row_kind\tvalue\nrun_profile\tx\n", encoding="utf-8"
    )
    (tmp_path / "review.tsv").write_text(
        "family\thas_anchor\ttag\tschema\n"
        "F1\tFALSE\t\tv1\n"
        "F2\tFALSE\t\tv1\n"
        "F3\tFALSE\t\tv1\n",
        encoding="utf-8",
    )
    (tmp_path / "matrix.csv").write_text(
        "family,S1,S2\nF1,1.0,2.0\nF2,3.0,\n", encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text("not a table\n", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "empty.tsv").write_text("a\n", encoding="utf-8")

    findings = census_output_directory(tmp_path)

    assert set(findings) == {
        PopulationFinding("evidence.tsv", "no_data_rows", "", "", 0),
        PopulationFinding("provenance.tsv", "single_data_row", "", "", 1),
        PopulationFinding("review.tsv", "constant_column", "has_anchor", "FALSE", 3),
        PopulationFinding("review.tsv", "constant_column", "tag", "", 3),
        PopulationFinding("review.tsv", "constant_column", "schema", "v1", 3),
    }
