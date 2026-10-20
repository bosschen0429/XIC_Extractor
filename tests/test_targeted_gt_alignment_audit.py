from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from tools.diagnostics import targeted_gt_alignment_audit as audit
from tools.diagnostics import targeted_gt_alignment_audit_io as audit_io


def test_path_style_cli_help_preserves_public_script_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "tools" / "diagnostics" / "targeted_gt_alignment_audit.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--target-workbook" in result.stdout
    assert "--alignment-run" in result.stdout
    assert "--output-dir" in result.stdout


def test_module_style_cli_help_preserves_public_module_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.diagnostics.targeted_gt_alignment_audit",
            "--help",
        ],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--target-workbook" in result.stdout
    assert "--alignment-run" in result.stdout
    assert "--output-dir" in result.stdout


def test_target_role_map_groups_requested_roles_in_one_pass() -> None:
    rows: list[dict[str, object]] = [
        {"Target": "d3-A", "Role": "Analyte", "SampleName": "S2", "RT": 9.2},
        {"Target": "d3-I", "Role": "ISTD", "SampleName": "S2", "RT": 9.0},
        {"Target": "d3-A", "Role": "Analyte", "SampleName": "S1", "RT": 8.2},
        {"Target": "unrelated", "Role": "Analyte", "SampleName": "S3", "RT": 1.0},
    ]

    grouped = audit_io._rows_by_target_role_map(
        rows,
        (("d3-A", "Analyte"), ("d3-I", "ISTD")),
    )

    assert grouped[("d3-A", "Analyte")]["S1"]["RT"] == 8.2
    assert grouped[("d3-A", "Analyte")]["S2"]["RT"] == 9.2
    assert grouped[("d3-I", "ISTD")]["S2"]["RT"] == 9.0
    assert set(grouped[("d3-I", "ISTD")]) == {"S2"}
    assert (
        audit._rows_by_target_role(rows, "d3-A", "Analyte")
        == grouped[("d3-A", "Analyte")]
    )

    with pytest.raises(ValueError, match="Missing sample for d3-A/Analyte"):
        audit_io._rows_by_target_role_map(
            [{"Target": "d3-A", "Role": "Analyte", "SampleName": ""}],
            (("d3-A", "Analyte"),),
        )


def test_targeted_gt_audit_writes_outputs_and_escapes_formula_values(
    tmp_path: Path,
) -> None:
    workbook = _write_target_workbook(
        tmp_path / "target.xlsx",
        samples=("=Sample_A",),
    )
    alignment_run = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=[
            _review_row("FAM000001", mz=242.1136, rt=11.99),
        ],
        cell_rows=[
            _cell_row("FAM000001", "=Sample_A", "detected", rt=11.99),
        ],
    )

    code = audit.main(
        [
            "--target-workbook",
            str(workbook),
            "--alignment-run",
            str(alignment_run),
            "--target-label",
            "5-medC",
            "--istd-label",
            "d3-5-medC",
            "--target-mz",
            "242.1136",
            "--ppm",
            "50",
            "--pass-rt-sec",
            "5",
            "--drift-rt-sec",
            "60",
            "--output-dir",
            str(tmp_path / "audit"),
        ],
    )

    assert code == 0
    output_dir = tmp_path / "audit"
    assert (output_dir / "gt_target.csv").exists()
    assert (output_dir / "comparison.csv").exists()
    assert (output_dir / "failure_mode_report.md").exists()
    assert (output_dir / "failure_mode_chart.svg").exists()

    comparison = _read_csv(output_dir / "comparison.csv")
    with (output_dir / "gt_target.csv").open(encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == [
            "sample_stem",
            "group",
            "target_mz",
            "target_rt_min",
            "target_peak_start_min",
            "target_peak_end_min",
            "target_peak_width_min",
            "target_area",
            "target_confidence",
            "target_nl_ok",
            "target_reason",
            "istd_rt_min",
            "istd_rt_delta_sec",
        ]
    with (output_dir / "comparison.csv").open(encoding="utf-8") as handle:
        assert csv.DictReader(handle).fieldnames == [
            "sample_stem",
            "group",
            "gt_target_rt_min",
            "gt_target_confidence",
            "gt_peak_start_min",
            "gt_peak_end_min",
            "family_count_total",
            "family_ids_all",
            "production_family_ids",
            "duplicate_family_ids",
            "production_family_count_in_gt_window",
            "production_family_ids_in_gt_window",
            "closest_family_id",
            "closest_family_mz",
            "closest_status",
            "closest_apex_rt_min",
            "closest_rt_delta_sec",
            "failure_mode",
        ]
    assert comparison[0]["sample_stem"] == "'=Sample_A"
    assert comparison[0]["failure_mode"] == "PASS"
    assert comparison[0]["closest_rt_delta_sec"] == "-0.60"
    assert "FAILURE MODE" not in (output_dir / "failure_mode_report.md").read_text(
        encoding="utf-8"
    )


def test_targeted_gt_writer_preserves_csv_contracts(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"

    audit._write_dict_csv(
        path,
        [
            {"sample_stem": "=Sample_A", "count": 1, "empty": None},
            {"sample_stem": "+Sample_B", "count": 2, "empty": ""},
        ],
    )

    with path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == [
            {"sample_stem": "'=Sample_A", "count": "1", "empty": ""},
            {"sample_stem": "'+Sample_B", "count": "2", "empty": ""},
        ]

    empty_path = tmp_path / "empty.csv"
    audit._write_dict_csv(empty_path, [])
    assert empty_path.read_text(encoding="utf-8") == ""

    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        audit._write_dict_csv(
            tmp_path / "extra.csv",
            [
                {"sample_stem": "Sample_A", "count": 1},
                {"sample_stem": "Sample_B", "count": 2, "extra": "value"},
            ],
        )


def test_targeted_gt_audit_classifies_pass(tmp_path: Path) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.0),
        ],
    )

    assert result["failure_mode"] == "PASS"
    assert result["closest_family_id"] == "FAM000001"
    assert result["production_family_count_in_gt_window"] == "1"


def test_targeted_gt_audit_classifies_split_for_multiple_production_families(
    tmp_path: Path,
) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        review_rows=[
            _review_row("FAM000001", mz=242.1136, rt=12.0),
            _review_row("FAM000002", mz=242.1140, rt=12.01),
        ],
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.0),
            _cell_row("FAM000002", "Sample_A", "backfilled", rt=12.01),
        ],
    )

    assert result["failure_mode"] == "SPLIT"
    assert result["production_family_count_in_gt_window"] == "2"
    assert result["production_family_ids_in_gt_window"] == "FAM000001;FAM000002"


def test_targeted_gt_audit_uses_new_schema_decision_before_raw_backfilled_status(
    tmp_path: Path,
) -> None:
    review_row = _review_row("FAM000001", mz=242.1136, rt=12.0)
    review_row.update(
        {
            "accepted_cell_count": "0",
            "include_in_primary_matrix": "FALSE",
        },
    )

    result = _run_single_sample_audit(
        tmp_path,
        review_rows=[review_row],
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "backfilled", rt=12.0),
        ],
    )

    assert result["production_family_count_in_gt_window"] == "0"
    assert result["production_family_ids"] == ""
    assert result["failure_mode"] == "MISS"


def test_targeted_gt_audit_does_not_count_provisional_discovery_as_production(
    tmp_path: Path,
) -> None:
    review_row = _review_row("FAM000001", mz=242.1136, rt=12.0)
    review_row.update(
        {
            "accepted_cell_count": "1",
            "include_in_primary_matrix": "TRUE",
            "identity_decision": "provisional_discovery",
        },
    )

    result = _run_single_sample_audit(
        tmp_path,
        review_rows=[review_row],
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.0),
        ],
    )

    assert result["production_family_count_in_gt_window"] == "0"
    assert result["production_family_ids"] == ""
    assert result["failure_mode"] == "MISS"


def test_targeted_gt_audit_does_not_count_duplicate_assigned_as_split(
    tmp_path: Path,
) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        review_rows=[
            _review_row("FAM000001", mz=242.1136, rt=12.0),
            _review_row("FAM000002", mz=242.1140, rt=12.01),
        ],
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.0),
            _cell_row("FAM000002", "Sample_A", "duplicate_assigned", rt=12.01),
        ],
    )

    assert result["failure_mode"] == "PASS"
    assert result["production_family_count_in_gt_window"] == "1"
    assert result["duplicate_family_ids"] == "FAM000002"


def test_targeted_gt_audit_classifies_drift(tmp_path: Path) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.5),
        ],
        peak_start=11.95,
        peak_end=12.05,
    )

    assert result["failure_mode"] == "DRIFT"
    assert result["closest_rt_delta_sec"] == "30.00"


def test_targeted_gt_audit_classifies_duplicate(tmp_path: Path) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "duplicate_assigned", rt=12.0),
        ],
    )

    assert result["failure_mode"] == "DUPLICATE"
    assert result["closest_status"] == "duplicate_assigned"


def test_targeted_gt_audit_classifies_miss(tmp_path: Path) -> None:
    result = _run_single_sample_audit(
        tmp_path,
        review_rows=[
            _review_row("FAM000001", mz=260.0, rt=12.0),
        ],
        cell_rows=[
            _cell_row("FAM000001", "Sample_A", "detected", rt=12.0, mz=260.0),
        ],
    )

    assert result["failure_mode"] == "MISS"
    assert result["closest_family_id"] == ""


def _run_single_sample_audit(
    tmp_path: Path,
    *,
    review_rows: list[dict[str, object]] | None = None,
    cell_rows: list[dict[str, object]],
    peak_start: float = 11.95,
    peak_end: float = 12.05,
) -> dict[str, str]:
    workbook = _write_target_workbook(
        tmp_path / "target.xlsx",
        peak_start=peak_start,
        peak_end=peak_end,
    )
    alignment_run = _write_alignment_run(
        tmp_path / "alignment",
        review_rows=review_rows
        if review_rows is not None
        else [_review_row("FAM000001", mz=242.1136, rt=12.0)],
        cell_rows=cell_rows,
    )
    output_dir = tmp_path / "audit"

    assert (
        audit.main(
            [
                "--target-workbook",
                str(workbook),
                "--alignment-run",
                str(alignment_run),
                "--target-label",
                "5-medC",
                "--istd-label",
                "d3-5-medC",
                "--target-mz",
                "242.1136",
                "--ppm",
                "50",
                "--pass-rt-sec",
                "5",
                "--drift-rt-sec",
                "60",
                "--output-dir",
                str(output_dir),
            ],
        )
        == 0
    )
    return _read_csv(output_dir / "comparison.csv")[0]


def _write_target_workbook(
    path: Path,
    *,
    samples: tuple[str, ...] = ("Sample_A",),
    peak_start: float = 11.95,
    peak_end: float = 12.05,
) -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "XIC Results"
    worksheet.append(
        [
            "SampleName",
            "Group",
            "Target",
            "Role",
            "ISTD Pair",
            "RT",
            "Area",
            "NL",
            "Int",
            "PeakStart",
            "PeakEnd",
            "PeakWidth",
            "Confidence",
            "Reason",
        ],
    )
    for sample in samples:
        worksheet.append(
            [
                sample,
                "Tumor",
                "5-medC",
                "Analyte",
                "d3-5-medC",
                12.0,
                1000.0,
                "OK",
                100.0,
                peak_start,
                peak_end,
                peak_end - peak_start,
                "HIGH",
                "targeted reason",
            ],
        )
        worksheet.append(
            [
                "",
                "",
                "d3-5-medC",
                "ISTD",
                "",
                12.0,
                2000.0,
                "OK",
                200.0,
                peak_start,
                peak_end,
                peak_end - peak_start,
                "HIGH",
                "istd reason",
            ],
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _write_alignment_run(
    path: Path,
    *,
    review_rows: list[dict[str, object]],
    cell_rows: list[dict[str, object]],
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _write_csv(path / "alignment_review.tsv", review_rows, delimiter="\t")
    _write_csv(path / "alignment_cells.tsv", cell_rows, delimiter="\t")
    return path


def _review_row(feature_id: str, *, mz: float, rt: float) -> dict[str, object]:
    return {
        "feature_family_id": feature_id,
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": mz,
        "family_center_rt": rt,
        "has_anchor": "TRUE",
        "detected_count": "1",
        "duplicate_assigned_count": "0",
        "warning": "",
    }


def _cell_row(
    feature_id: str,
    sample: str,
    status: str,
    *,
    rt: float,
    mz: float = 242.1136,
) -> dict[str, object]:
    return {
        "feature_family_id": feature_id,
        "sample_stem": sample,
        "status": status,
        "area": "1000",
        "apex_rt": rt,
        "height": "100",
        "peak_start_rt": rt - 0.04,
        "peak_end_rt": rt + 0.04,
        "rt_delta_sec": "0",
        "trace_quality": "test",
        "scan_support_score": "",
        "source_candidate_id": f"{sample}#{feature_id}",
        "neutral_loss_tag": "DNA_dR",
        "family_center_mz": mz,
        "family_center_rt": rt,
        "reason": "cell reason",
    }


def _write_csv(
    path: Path,
    rows: list[dict[str, object]],
    *,
    delimiter: str,
) -> None:
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
