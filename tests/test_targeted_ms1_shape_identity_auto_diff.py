from __future__ import annotations

import csv
from pathlib import Path

from xic_extractor.diagnostics.targeted_ms1_shape_identity import (
    TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
)
from xic_extractor.diagnostics.targeted_ms1_shape_identity_auto_diff import (
    write_targeted_ms1_shape_identity_auto_diff_artifacts,
)


def test_auto_diff_artifacts_pass_limited_expected_diff_gate(tmp_path: Path) -> None:
    baseline_dir = tmp_path / "baseline"
    optin_dir = tmp_path / "optin"
    output_dir = tmp_path / "diff"
    support_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    _write_long_csv(
        baseline_dir / "xic_results_long.csv",
        [
            _long_row(
                rt="ND",
                area="ND",
                intensity="ND",
                peak_start="ND",
                peak_end="ND",
                peak_width="ND",
                confidence="VERY_LOW",
                product_state="not_counted",
                counted_detection="FALSE",
                reason=(
                    "decision: not_counted; support: paired_area_ratio_support; "
                    "not_counted: analyte_nl_fail_requires_policy"
                ),
                support_reasons="paired_area_ratio_support",
                not_counted_reasons="analyte_nl_fail_requires_policy",
            )
        ],
    )
    _write_long_csv(
        optin_dir / "xic_results_long.csv",
        [
            _long_row(
                rt="9.10",
                area="12345.67",
                intensity="100",
                peak_start="8.90",
                peak_end="9.30",
                peak_width="0.40",
                confidence="MEDIUM",
                product_state="detected_flagged",
                counted_detection="TRUE",
                reason=(
                    "decision: detected_flagged; support: paired_area_ratio_support, "
                    "own_max_same_peak_support"
                ),
                support_reasons=(
                    "paired_area_ratio_support; own_max_same_peak_support"
                ),
                not_counted_reasons="",
            )
        ],
    )
    _write_wide_csv(
        baseline_dir / "xic_results.csv",
        {"5-hmdC_RT": "ND", "5-hmdC_Area": "ND"},
    )
    _write_wide_csv(
        optin_dir / "xic_results.csv",
        {"5-hmdC_RT": "9.10", "5-hmdC_Area": "12345.67"},
    )
    _write_support_tsv(support_tsv)

    outputs = write_targeted_ms1_shape_identity_auto_diff_artifacts(
        baseline_output_dir=baseline_dir,
        optin_output_dir=optin_dir,
        support_tsv=support_tsv,
        output_dir=output_dir,
    )

    assert outputs.gate_status == "pass"
    assert outputs.expected_diff_row_count == 1
    assert outputs.matrix_diff_cell_count == 2
    assert outputs.expected_diff_summary_tsv.is_file()
    assert outputs.matrix_diff_summary_tsv.is_file()
    gate_rows = _read_tsv(outputs.expected_diff_gate_summary_tsv)
    assert {"metric": "long_changed_rows", "value": "1"} in gate_rows
    assert {"metric": "matrix_changed_cells", "value": "2"} in gate_rows
    assert {"metric": "support_tsv_supported_rows", "value": "1"} in gate_rows


def _long_row(
    *,
    rt: str,
    area: str,
    intensity: str,
    peak_start: str,
    peak_end: str,
    peak_width: str,
    confidence: str,
    product_state: str,
    counted_detection: str,
    reason: str,
    support_reasons: str,
    not_counted_reasons: str,
) -> dict[str, str]:
    return {
        "SampleName": "SampleA",
        "Target": "5-hmdC",
        "Role": "Analyte",
        "RT": rt,
        "Area": area,
        "Int": intensity,
        "PeakStart": peak_start,
        "PeakEnd": peak_end,
        "PeakWidth": peak_width,
        "Confidence": confidence,
        "NL": "NL_FAIL",
        "Reason": reason,
        "Product State": product_state,
        "Counted Detection": counted_detection,
        "Projection Support Reasons": support_reasons,
        "Projection Not Counted Reasons": not_counted_reasons,
    }


def _write_long_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_wide_csv(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"SampleName": "SampleA", **values}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(row))
        writer.writeheader()
        writer.writerow(row)


def _write_support_tsv(path: Path) -> None:
    row = {
        "schema_version": "targeted_ms1_shape_identity",
        "validation_label": "diagnostic_only",
        "decision_authority": "diagnostic_only_no_product_write",
        "sample_name": "SampleA",
        "target_name": "5-hmdC",
        "target_role": "analyte",
        "paired_istd": "d3-5-hmdC",
        "source_row_id": "SampleA|5-hmdC",
        "candidate_state": "NL_FAIL",
        "reference_source": "unit",
        "candidate_rt_min": "9.10",
        "reference_rt_min": "9.10",
        "candidate_anchor_rt_delta_min": "0",
        "paired_istd_rt_min": "9.00",
        "candidate_pair_rt_delta_min": "0.10",
        "target_window_status": "candidate_inside_target_window",
        "own_max_same_peak_status": "own_max_same_peak_supported",
        "own_max_same_peak_supported": "TRUE",
        "own_max_same_peak_support_reason": "own_max_same_peak_support",
        "own_max_same_peak_similarity": "0.95",
        "own_max_compared_point_count": "31",
        "strongest_peak_rt_min": "9.10",
        "strongest_peak_own_max_ratio": "1.0",
        "strongest_competing_peak_rt_min": "",
        "strongest_competing_peak_own_max_ratio": "",
        "competing_peak_status": "no_competing_peak_observed",
        "reason": "own max same-peak support",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=TARGETED_MS1_SHAPE_IDENTITY_COLUMNS,
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(row)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
