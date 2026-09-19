from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

from tools.diagnostics import analyze_rt_normalization_anchors as rt_norm
from xic_extractor.alignment.rt_normalization import (
    AnchorPoint,
    apply_anchor_reference_source,
)


def test_path_style_cli_help_preserves_public_script_contract():
    repo_root = Path(__file__).resolve().parents[1]
    script = (
        repo_root
        / "tools"
        / "diagnostics"
        / "analyze_rt_normalization_anchors.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--targeted-workbook" in result.stdout
    assert "--alignment-dir" in result.stdout


def test_anchor_normalization_fits_models_and_reduces_family_rt_spread(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("d3-anchor-low", 10.0, 116.0474),
            _target("d3-anchor-high", 30.0, 116.0474),
            _target("rna-anchor", 22.0, 132.0423),
        ],
        sample_anchor_rts={
            "S1": {
                "d3-anchor-low": 10.0,
                "d3-anchor-high": 30.0,
                "rna-anchor": 22.0,
            },
            "S2": {
                "d3-anchor-low": 12.0,
                "d3-anchor-high": 34.0,
                "rna-anchor": 25.2,
            },
            "S3": {
                "d3-anchor-low": 8.5,
                "d3-anchor-high": 26.5,
                "rna-anchor": 19.3,
            },
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM001",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 20.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM001", "S1", 20.0),
            _cell_row("FAM001", "S2", 23.0),
            _cell_row("FAM001", "S3", 17.5),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
    )

    assert outputs.summary_tsv.exists()
    assert outputs.family_tsv.exists()
    assert outputs.anchor_tsv.exists()
    assert outputs.json_path.exists()
    assert result.anchor_label_count == 2
    assert result.modelled_sample_count == 3
    assert result.unmodelled_sample_count == 0
    assert result.family_count == 1
    assert result.families_improved_count == 1

    family_rows = _read_tsv(outputs.family_tsv)
    assert family_rows[0]["feature_family_id"] == "FAM001"
    assert float(family_rows[0]["raw_rt_range_min"]) == pytest.approx(5.5)
    assert float(family_rows[0]["normalized_rt_range_min"]) == pytest.approx(0.0)
    assert float(family_rows[0]["rt_range_improvement_min"]) == pytest.approx(5.5)

    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert payload["overall_status"] == "PASS"
    assert payload["anchor_label_count"] == 2


def test_main_writes_outputs_for_valid_inputs(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 5.0, 116.0474),
            _target("anchor-b", 15.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 5.0, "anchor-b": 15.0},
            "S2": {"anchor-a": 6.0, "anchor-b": 16.0},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM001",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 150.0,
                "family_center_rt": 10.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM001", "S1", 10.0),
            _cell_row("FAM001", "S2", 11.0),
        ],
    )

    code = rt_norm.main(
        [
            "--targeted-workbook",
            str(targeted),
            "--alignment-dir",
            str(alignment),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 0
    assert (output_dir / "rt_normalization_summary.tsv").exists()
    assert (output_dir / "rt_normalization_families.tsv").exists()
    assert (output_dir / "rt_normalization_anchors.tsv").exists()
    assert (output_dir / "rt_normalization.json").exists()
    assert (output_dir / "rt_normalization.md").exists()


def test_default_reference_uses_observed_anchor_medians(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 100.0, 116.0474),
            _target("anchor-b", 200.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 30.0},
            "S2": {"anchor-a": 12.0, "anchor-b": 34.0},
            "S3": {"anchor-a": 8.5, "anchor-b": 26.5},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM001",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 150.0,
                "family_center_rt": 20.0,
            },
        ],
        cell_rows=[_cell_row("FAM001", "S1", 20.0)],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
    )

    assert result.reference_source == "observed-median"
    anchor_rows = _read_tsv(outputs.anchor_tsv)
    references_by_label = {
        row["target_label"]: float(row["reference_rt_min"]) for row in anchor_rows
    }
    assert references_by_label["anchor-a"] == pytest.approx(10.0)
    assert references_by_label["anchor-b"] == pytest.approx(30.0)


def test_auto_model_uses_piecewise_transform_for_nonlinear_anchor_drift(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
            _target("anchor-c", 30.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 20.0, "anchor-c": 30.0},
            "S2": {"anchor-a": 12.0, "anchor-b": 25.0, "anchor-c": 36.0},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_NONLINEAR",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 15.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_NONLINEAR", "S1", 15.0),
            _cell_row("FAM_NONLINEAR", "S2", 18.5),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
    )

    assert {sample.model_type for sample in result.samples} == {"piecewise"}
    family_rows = _read_tsv(outputs.family_tsv)
    assert float(family_rows[0]["normalized_rt_range_min"]) == pytest.approx(0.0)


def test_anchor_quality_gate_excludes_outlier_before_family_summary(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
            _target("anchor-c", 30.0, 116.0474),
            _target("anchor-d", 40.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {
                "anchor-a": 10.0,
                "anchor-b": 20.0,
                "anchor-c": 30.0,
                "anchor-d": 40.0,
            },
            "S2": {
                "anchor-a": 12.0,
                "anchor-b": 31.0,
                "anchor-c": 32.0,
                "anchor-d": 42.0,
            },
            "S3": {
                "anchor-a": 10.0,
                "anchor-b": 20.0,
                "anchor-c": 30.0,
                "anchor-d": 40.0,
            },
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_OUTLIER",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 25.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_OUTLIER", "S1", 25.0),
            _cell_row("FAM_OUTLIER", "S2", 27.0),
            _cell_row("FAM_OUTLIER", "S3", 25.0),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
        anchor_residual_max_min=0.5,
    )

    sample_by_name = {sample.sample_stem: sample for sample in result.samples}
    assert sample_by_name["S2"].excluded_anchor_count == 1
    anchor_rows = _read_tsv(outputs.anchor_tsv)
    excluded = [
        row
        for row in anchor_rows
        if row["sample_stem"] == "S2" and row["anchor_status"] == "excluded"
    ]
    assert [row["target_label"] for row in excluded] == ["anchor-b"]
    assert excluded[0]["used_in_model"] == "FALSE"
    family_rows = _read_tsv(outputs.family_tsv)
    assert float(family_rows[0]["normalized_rt_range_min"]) == pytest.approx(0.0)


def test_result_warns_when_normalization_worsens_family_rt_range(tmp_path: Path):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 20.0},
            "S2": {"anchor-a": 20.0, "anchor-b": 30.0},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_WORSE",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 15.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_WORSE", "S1", 15.0),
            _cell_row("FAM_WORSE", "S2", 15.1),
        ],
    )

    _outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
        reference_source="target-window",
    )

    assert result.overall_status == "WARN"
    assert result.median_rt_range_improvement_min is not None
    assert result.median_rt_range_improvement_min < 0


def test_injection_local_reference_uses_ordered_rolling_anchor_medians(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    sample_info = tmp_path / "SampleInfo.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 20.0},
            "S2": {"anchor-a": 12.0, "anchor-b": 22.0},
            "S3": {"anchor-a": 10.0, "anchor-b": 20.0},
        },
    )
    _write_sample_info(sample_info, ("S1", "S2", "S3"))
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_LOCAL_SPIKE",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 15.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_LOCAL_SPIKE", "S1", 15.0),
            _cell_row("FAM_LOCAL_SPIKE", "S2", 17.0),
            _cell_row("FAM_LOCAL_SPIKE", "S3", 15.0),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
        reference_source="injection-local-median",
        sample_info=sample_info,
        injection_window=2,
    )

    assert result.reference_source == "injection-local-median"
    family_rows = _read_tsv(outputs.family_tsv)
    assert float(family_rows[0]["raw_rt_range_min"]) == pytest.approx(2.0)
    assert float(family_rows[0]["normalized_rt_range_min"]) == pytest.approx(0.0)
    anchor_rows = _read_tsv(outputs.anchor_tsv)
    s2_references = {
        row["target_label"]: float(row["reference_rt_min"])
        for row in anchor_rows
        if row["sample_stem"] == "S2"
    }
    assert s2_references == {"anchor-a": 10.0, "anchor-b": 20.0}


@pytest.mark.parametrize(
    ("reference_source", "reference_kwargs"),
    [
        ("injection-local-median", {"injection_window": 2}),
        ("injection-loess", {"loess_frac": 1.0, "loess_min_neighbors": 3}),
    ],
)
def test_injection_reference_accepts_sample_metadata_without_value_effect(
    tmp_path: Path,
    reference_source: str,
    reference_kwargs: dict[str, object],
):
    targeted = tmp_path / "targeted.xlsx"
    legacy_sample_info = tmp_path / "SampleInfo.xlsx"
    sample_metadata = tmp_path / "sample_metadata.tsv"
    alignment = tmp_path / "alignment"
    legacy_output_dir = tmp_path / "legacy_rt_normalization"
    metadata_output_dir = tmp_path / "metadata_rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 20.0},
            "S2": {"anchor-a": 12.0, "anchor-b": 22.0},
            "S3": {"anchor-a": 10.0, "anchor-b": 20.0},
        },
    )
    _write_sample_info(legacy_sample_info, ("S1", "S2", "S3"))
    _write_sample_metadata(
        sample_metadata,
        [
            {
                "sample_name": "Alpha",
                "raw_stem": "S1",
                "injection_order": "1",
                "sample_role": "study_sample",
                "batch_id": "BatchA",
                "matrix_type": "plasma",
                "group": "case",
            },
            {
                "sample_name": "Beta",
                "raw_stem": "S2",
                "injection_order": "2",
                "sample_role": "qc",
                "batch_id": "BatchB",
                "matrix_type": "urine",
                "group": "qc",
                "excluded": "TRUE",
                "exclusion_reason": "metadata-only sentinel",
            },
            {
                "sample_name": "Gamma",
                "raw_stem": "S3",
                "injection_order": "3",
                "sample_role": "blank",
                "batch_id": "BatchC",
                "matrix_type": "water",
                "group": "blank",
            },
        ],
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_LOCAL_SPIKE",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 15.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_LOCAL_SPIKE", "S1", 15.0),
            _cell_row("FAM_LOCAL_SPIKE", "S2", 17.0),
            _cell_row("FAM_LOCAL_SPIKE", "S3", 15.0),
        ],
    )

    legacy_outputs, legacy_result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=legacy_output_dir,
        reference_source=reference_source,
        sample_info=legacy_sample_info,
        **reference_kwargs,
    )
    metadata_outputs, metadata_result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=metadata_output_dir,
        reference_source=reference_source,
        sample_info=sample_metadata,
        **reference_kwargs,
    )

    assert metadata_result.reference_source == legacy_result.reference_source
    assert _read_tsv(metadata_outputs.anchor_tsv) == _read_tsv(
        legacy_outputs.anchor_tsv
    )
    assert _read_tsv(metadata_outputs.family_tsv) == _read_tsv(
        legacy_outputs.family_tsv
    )


def test_injection_loess_reference_preserves_linear_ordered_anchor_drift():
    points = tuple(
        AnchorPoint(
            sample_stem=f"S{order}",
            target_label="anchor-a",
            observed_rt_min=10.0 + (order * 0.5),
            reference_rt_min=10.0,
        )
        for order in range(1, 6)
    )
    injection_order = {f"S{order}": order for order in range(1, 6)}

    adjusted = apply_anchor_reference_source(
        points,
        "injection-loess",
        injection_order=injection_order,
        loess_frac=1.0,
        loess_min_neighbors=5,
    )

    references = {
        point.sample_stem: point.reference_rt_min
        for point in adjusted
    }
    assert references == pytest.approx(
        {
            "S1": 10.5,
            "S2": 11.0,
            "S3": 11.5,
            "S4": 12.0,
            "S5": 12.5,
        }
    )


def test_leave_one_anchor_out_reports_prediction_error_by_anchor(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
            _target("anchor-c", 30.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-a": 10.0, "anchor-b": 20.0, "anchor-c": 30.0},
            "S2": {"anchor-a": 12.0, "anchor-b": 24.0, "anchor-c": 36.0},
            "S3": {"anchor-a": 9.0, "anchor-b": 18.0, "anchor-c": 27.0},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM001",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 20.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM001", "S1", 20.0),
            _cell_row("FAM001", "S2", 24.0),
            _cell_row("FAM001", "S3", 18.0),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
    )

    assert outputs.leave_one_out_tsv.exists()
    rows = _read_tsv(outputs.leave_one_out_tsv)
    assert {row["target_label"] for row in rows} == {
        "anchor-a",
        "anchor-b",
        "anchor-c",
    }
    assert all(row["status"] == "PASS" for row in rows)
    assert all(float(row["p95_abs_error_min"]) <= 0.30 for row in rows)
    payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
    assert len(payload["leave_one_anchor_out"]) == 3
    assert result.leave_one_anchor_out[0].target_label == "anchor-a"


def test_family_rows_include_rt_band_anchor_scope_and_residual_window(
    tmp_path: Path,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-low", 10.0, 116.0474),
            _target("anchor-mid", 20.0, 116.0474),
            _target("anchor-high", 30.0, 116.0474),
        ],
        sample_anchor_rts={
            "S1": {"anchor-low": 10.0, "anchor-mid": 20.0, "anchor-high": 30.0},
            "S2": {"anchor-low": 12.0, "anchor-mid": 24.0, "anchor-high": 36.0},
            "S3": {"anchor-low": 9.0, "anchor-mid": 18.0, "anchor-high": 27.0},
        },
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM_INSIDE",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 20.0,
            },
            {
                "feature_family_id": "FAM_LATE",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 300.0,
                "family_center_rt": 35.0,
            },
        ],
        cell_rows=[
            _cell_row("FAM_INSIDE", "S1", 20.0),
            _cell_row("FAM_INSIDE", "S2", 24.0),
            _cell_row("FAM_INSIDE", "S3", 18.0),
            _cell_row("FAM_LATE", "S1", 35.0),
            _cell_row("FAM_LATE", "S2", 42.0),
            _cell_row("FAM_LATE", "S3", 31.5),
        ],
    )

    outputs, result = rt_norm.run_rt_normalization_anchor_diagnostic(
        targeted_workbook=targeted,
        alignment_dir=alignment,
        output_dir=output_dir,
    )

    rows = {row["feature_family_id"]: row for row in _read_tsv(outputs.family_tsv)}
    inside = rows["FAM_INSIDE"]
    assert inside["rt_band"] == "20-30"
    assert inside["anchor_support_level"] == "inside_anchor_range"
    assert inside["normalized_rt_support"] == "improved"
    assert float(inside["local_residual_window_min"]) >= 0.05

    late = rows["FAM_LATE"]
    assert late["anchor_support_level"] == "after_anchor_range"
    assert late["anchor_scope"] == "after_anchor_range"
    assert result.rt_band_summary["20-30"]["improved"] >= 1


@pytest.mark.parametrize(
    "reference_source",
    ["injection-local-median", "injection-loess"],
)
def test_injection_reference_requires_sample_info(
    tmp_path: Path,
    reference_source: str,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    _write_targeted_workbook(
        targeted,
        targets=[
            _target("anchor-a", 10.0, 116.0474),
            _target("anchor-b", 20.0, 116.0474),
        ],
        sample_anchor_rts={"S1": {"anchor-a": 10.0, "anchor-b": 20.0}},
    )
    _write_alignment_run(
        alignment,
        review_rows=[
            {
                "feature_family_id": "FAM001",
                "include_in_primary_matrix": "TRUE",
                "family_center_mz": 250.0,
                "family_center_rt": 15.0,
            },
        ],
        cell_rows=[_cell_row("FAM001", "S1", 15.0)],
    )

    with pytest.raises(ValueError, match="sample_info"):
        rt_norm.run_rt_normalization_anchor_diagnostic(
            targeted_workbook=targeted,
            alignment_dir=alignment,
            output_dir=tmp_path / "rt_normalization",
            reference_source=reference_source,
        )


def test_main_reports_missing_required_workbook_column(
    tmp_path: Path,
    capsys,
):
    targeted = tmp_path / "targeted.xlsx"
    alignment = tmp_path / "alignment"
    output_dir = tmp_path / "rt_normalization"
    _write_targeted_workbook(
        targeted,
        targets=[_target("anchor-a", 5.0, 116.0474)],
        sample_anchor_rts={"S1": {"anchor-a": 5.0}},
        omit_target_columns={"NL (Da)"},
    )
    _write_alignment_run(alignment, review_rows=[], cell_rows=[])

    code = rt_norm.main(
        [
            "--targeted-workbook",
            str(targeted),
            "--alignment-dir",
            str(alignment),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert code == 2
    assert "NL (Da)" in capsys.readouterr().err


def _target(label: str, rt_center: float, nl: float) -> dict[str, object]:
    return {
        "Label": label,
        "Role": "ISTD",
        "RT min": rt_center - 0.1,
        "RT max": rt_center + 0.1,
        "NL (Da)": nl,
    }


def _write_targeted_workbook(
    path: Path,
    *,
    targets: list[dict[str, object]],
    sample_anchor_rts: dict[str, dict[str, float]],
    omit_target_columns: set[str] | None = None,
) -> None:
    omit_target_columns = omit_target_columns or set()
    workbook = Workbook()
    targets_sheet = workbook.active
    targets_sheet.title = "Targets"
    target_header = ["Label", "Role", "RT min", "RT max", "NL (Da)"]
    target_header = [
        column for column in target_header if column not in omit_target_columns
    ]
    targets_sheet.append(target_header)
    for target in targets:
        targets_sheet.append([target.get(column) for column in target_header])

    results = workbook.create_sheet("XIC Results")
    results.append(["SampleName", "Target", "Role", "RT", "Area"])
    for sample, rts_by_label in sample_anchor_rts.items():
        first = True
        for target in targets:
            label = str(target["Label"])
            rt = rts_by_label[label]
            results.append(
                [
                    sample if first else None,
                    label,
                    target["Role"],
                    rt,
                    1000.0,
                ]
            )
            first = False
    workbook.save(path)


def _write_sample_info(path: Path, samples: tuple[str, ...]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Sample_Name", "Injection_Order"])
    for order, sample in enumerate(samples, start=1):
        sheet.append([sample, order])
    workbook.save(path)


def _write_sample_metadata(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    fieldnames = (
        "schema_version",
        "sample_name",
        "raw_stem",
        "injection_order",
        "sample_role",
        "batch_id",
        "prep_batch_id",
        "matrix_type",
        "group",
        "excluded",
        "exclusion_reason",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            payload = {key: "" for key in fieldnames}
            payload.update(row)
            payload["schema_version"] = "sample_metadata"
            payload.setdefault("excluded", "FALSE")
            writer.writerow(payload)


def _write_alignment_run(
    path: Path,
    *,
    review_rows: list[dict[str, object]],
    cell_rows: list[dict[str, object]],
) -> None:
    path.mkdir(parents=True)
    _write_tsv(
        path / "alignment_review.tsv",
        review_rows,
        (
            "feature_family_id",
            "include_in_primary_matrix",
            "family_center_mz",
            "family_center_rt",
        ),
    )
    _write_tsv(
        path / "alignment_cells.tsv",
        cell_rows,
        ("feature_family_id", "sample_stem", "status", "area", "apex_rt"),
    )


def _cell_row(family_id: str, sample: str, apex_rt: float) -> dict[str, object]:
    return {
        "feature_family_id": family_id,
        "sample_stem": sample,
        "status": "detected",
        "area": 1000.0,
        "apex_rt": apex_rt,
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _write_tsv(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: tuple[str, ...],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
