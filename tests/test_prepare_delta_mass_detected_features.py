from __future__ import annotations

import csv
from pathlib import Path

from scripts import prepare_delta_mass_detected_features

HEADER = (
    "external_tool",
    "external_run_id",
    "external_tool_version",
    "external_adapter_version",
    "sample_id",
    "feature_id",
    "mz",
    "rt",
    "area_or_intensity",
    "area_or_intensity_semantics",
    "trace_quality",
    "shape_score",
    "mz_tolerance_unit",
    "rt_tolerance_unit",
    "duplicate_feature_policy",
    "missing_value_policy",
)


def _write_external_table(path: Path, *, semantics: str = "height") -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, delimiter="\t")
        writer.writeheader()
        writer.writerow(
            {
                "external_tool": "MZmine",
                "external_run_id": "run1",
                "external_tool_version": "4.9.14",
                "external_adapter_version": "contract_v1",
                "sample_id": "Sample_1",
                "feature_id": "F123",
                "mz": "500.0",
                "rt": "10.0",
                "area_or_intensity": "1000.0",
                "area_or_intensity_semantics": semantics,
                "trace_quality": "GOOD",
                "shape_score": "",
                "mz_tolerance_unit": "ppm",
                "rt_tolerance_unit": "minutes",
                "duplicate_feature_policy": "source_unique",
                "missing_value_policy": "blank_is_missing",
            }
        )


def _write_mzmine_table(path: Path) -> None:
    path.write_text(
        "row ID,row m/z,row retention time,Sample_A.mzML Peak area,\n"
        "10,500.0,10.0,1234.5,\n",
        encoding="utf-8",
    )


def test_prepare_cli_writes_detected_features_csv(
    tmp_path: Path,
    capsys,
) -> None:
    external_table = tmp_path / "external.tsv"
    _write_external_table(external_table)
    output_csv = tmp_path / "detected_features.csv"

    code = prepare_delta_mass_detected_features.main(
        [
            "--external-feature-table-tsv",
            str(external_table),
            "--output-csv",
            str(output_csv),
        ]
    )

    assert code == 0
    with output_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_1"
    assert rows[0]["height"] == "1000"
    assert "Detected feature rows: 1" in capsys.readouterr().out


def test_prepare_cli_accepts_area_only_table_as_intensity_proxy(
    tmp_path: Path,
    capsys,
) -> None:
    external_table = tmp_path / "external.tsv"
    _write_external_table(external_table, semantics="area")
    output_csv = tmp_path / "detected_features.csv"

    code = prepare_delta_mass_detected_features.main(
        [
            "--external-feature-table-tsv",
            str(external_table),
            "--output-csv",
            str(output_csv),
        ]
    )

    assert code == 0
    with output_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["height"] == "1000"
    assert rows[0]["area"] == "1000"
    assert "Detected feature rows: 1" in capsys.readouterr().out


def test_prepare_cli_accepts_mzmine_wide_feature_table(
    tmp_path: Path,
    capsys,
) -> None:
    mzmine_table = tmp_path / "mzmine.csv"
    _write_mzmine_table(mzmine_table)
    output_csv = tmp_path / "detected_features.csv"

    code = prepare_delta_mass_detected_features.main(
        [
            "--mzmine-feature-table-csv",
            str(mzmine_table),
            "--output-csv",
            str(output_csv),
        ]
    )

    assert code == 0
    with output_csv.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["sample_stem"] == "Sample_A"
    assert rows[0]["source_feature_row_id"] == "mzmine|row|10"
    assert rows[0]["height"] == "1234.5"
    assert rows[0]["area"] == "1234.5"
    assert rows[0]["trace_quality"] == "UNSPECIFIED"
    assert "Detected feature rows: 1" in capsys.readouterr().out
