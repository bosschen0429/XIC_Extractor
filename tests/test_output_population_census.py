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


def test_required_population_fails_only_on_default_or_missing_output(
    tmp_path: Path,
) -> None:
    from tools.diagnostics.output_population_census import main

    (tmp_path / "evidence.tsv").write_text("sample\tarea\n", encoding="utf-8")
    (tmp_path / "review.tsv").write_text(
        "family\thas_anchor\ttag\tanchored\n"
        "F1\tFALSE\tcid_nl:116\tTRUE\n"
        "F2\tFALSE\tcid_nl:116\tFALSE\n",
        encoding="utf-8",
    )
    (tmp_path / "single.tsv").write_text(
        "family\thas_anchor\ttag\nF1\tFALSE\tcid_nl:116\n",
        encoding="utf-8",
    )
    (tmp_path / "short.tsv").write_text("family\thas_anchor\nF1\n", encoding="utf-8")
    report = tmp_path / "report" / "census.tsv"

    def run(*required: str) -> int:
        arguments = [str(tmp_path), "--output-tsv", str(report)]
        for item in required:
            arguments += ["--require-populated", item]
        return main(arguments)

    # A constant real value and a varying column are populated.
    assert run("review.tsv", "review.tsv:tag", "review.tsv:anchored") == 0
    # A column that only ever holds its default, an empty artifact, a missing
    # column or artifact each fail the promoted check.
    assert run("review.tsv:has_anchor") == 1
    assert run("single.tsv:has_anchor") == 1
    assert run("single.tsv:tag") == 0
    assert run("short.tsv:has_anchor") == 1
    assert run("evidence.tsv") == 1
    assert run("review.tsv:product_mz") == 1
    assert run("provenance.tsv") == 1
