from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.diagnostics import targeted_ms1_shape_identity_expected_diff_gate as tool
from xic_extractor.diagnostics.targeted_ms1_shape_identity_expected_diff import (
    evaluate_limited_targeted_ms1_shape_identity_expected_diff,
)
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)


def test_limited_expected_diff_gate_accepts_hmdc_medc_flagged_rows() -> None:
    summary = evaluate_limited_targeted_ms1_shape_identity_expected_diff(
        [
            _expected_diff_row("S1", "5-hmdC"),
            _expected_diff_row("S2", "5-medC"),
        ],
        [
            _matrix_diff_row("S1", "5-hmdC_RT"),
            _matrix_diff_row("S1", "5-hmdC_Area"),
            _matrix_diff_row("S2", "5-medC_RT"),
            _matrix_diff_row("S2", "5-medC_Area"),
        ],
        expected_long_row_count=2,
        expected_matrix_cell_count=4,
        support_tsv_rows=[
            _support_row("S1", "5-hmdC"),
            _support_row("S2", "5-medC"),
        ],
    )

    assert summary.gate_status == "pass"
    assert summary.long_changed_rows == 2
    assert summary.matrix_changed_cells == 4
    assert summary.target_counts == {"5-hmdC": 1, "5-medC": 1}
    assert summary.support_tsv_supported_rows == 2
    assert summary.support_tsv_target_counts == {"5-hmdC": 1, "5-medC": 1}


def test_limited_expected_diff_gate_rejects_rows_outside_scope() -> None:
    with pytest.raises(ValueError, match="outside limited_5hmdc_5medc scope"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-fC")],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
        )


def test_limited_expected_diff_gate_rejects_clean_detected_output() -> None:
    row = _expected_diff_row("S1", "5-hmdC")
    row["optin_product_state"] = "detected_clean"

    with pytest.raises(ValueError, match="optin_product_state"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [row],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
        )


def test_limited_expected_diff_gate_rejects_unexpected_matrix_column() -> None:
    with pytest.raises(ValueError, match="unexpected matrix measurement"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-hmdC")],
            [_matrix_diff_row("S1", "5-hmdC_ReviewState")],
        )


def test_limited_expected_diff_gate_requires_matching_matrix_keys() -> None:
    with pytest.raises(ValueError, match="missing sample/target keys"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [
                _expected_diff_row("S1", "5-hmdC"),
                _expected_diff_row("S2", "5-medC"),
            ],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
        )

    with pytest.raises(ValueError, match="keys outside expected diff"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-hmdC")],
            [
                _matrix_diff_row("S1", "5-hmdC_RT"),
                _matrix_diff_row("S2", "5-medC_RT"),
            ],
        )


def test_limited_expected_diff_gate_requires_matching_support_keys() -> None:
    with pytest.raises(ValueError, match="missing sample/target keys"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-hmdC")],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
            support_tsv_rows=[_support_row("S2", "5-medC")],
        )

    with pytest.raises(ValueError, match="did not change output"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-hmdC")],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
            support_tsv_rows=[
                _support_row("S1", "5-hmdC"),
                _support_row("S2", "5-medC"),
            ],
        )


def test_limited_expected_diff_gate_requires_support_rows() -> None:
    with pytest.raises(ValueError, match="support TSV rows are required"):
        evaluate_limited_targeted_ms1_shape_identity_expected_diff(
            [_expected_diff_row("S1", "5-hmdC")],
            [_matrix_diff_row("S1", "5-hmdC_RT")],
        )


def test_limited_expected_diff_gate_cli_writes_summary(tmp_path: Path) -> None:
    expected_diff = tmp_path / "expected_diff_summary.tsv"
    matrix_diff = tmp_path / "matrix_diff_summary.tsv"
    summary_tsv = tmp_path / "gate_summary.tsv"
    _write_tsv(expected_diff, [_expected_diff_row("S1", "5-hmdC")])
    _write_tsv(matrix_diff, [_matrix_diff_row("S1", "5-hmdC_RT")])
    support_tsv = tmp_path / "targeted_ms1_shape_identity.tsv"
    _write_tsv(support_tsv, [_support_row("S1", "5-hmdC")])

    assert (
        tool.main(
            [
                "--expected-diff-summary-tsv",
                str(expected_diff),
                "--matrix-diff-summary-tsv",
                str(matrix_diff),
                "--support-tsv",
                str(support_tsv),
                "--summary-tsv",
                str(summary_tsv),
                "--expected-long-row-count",
                "1",
                "--expected-matrix-cell-count",
                "1",
            ]
        )
        == 0
    )

    rows = _read_tsv(summary_tsv)
    assert rows[0] == {"metric": "gate_status", "value": "pass"}
    assert {"metric": "long_changed_rows", "value": "1"} in rows
    assert {"metric": "support_tsv_supported_rows", "value": "1"} in rows


def test_limited_expected_diff_gate_cli_requires_support_tsv(
    tmp_path: Path,
) -> None:
    expected_diff = tmp_path / "expected_diff_summary.tsv"
    matrix_diff = tmp_path / "matrix_diff_summary.tsv"
    _write_tsv(expected_diff, [_expected_diff_row("S1", "5-hmdC")])
    _write_tsv(matrix_diff, [_matrix_diff_row("S1", "5-hmdC_RT")])

    with pytest.raises(SystemExit) as exc_info:
        tool.main(
            [
                "--expected-diff-summary-tsv",
                str(expected_diff),
                "--matrix-diff-summary-tsv",
                str(matrix_diff),
            ],
        )
    assert exc_info.value.code == 2


def _expected_diff_row(sample_name: str, target_name: str) -> dict[str, str]:
    return {
        "sample_name": sample_name,
        "target_name": target_name,
        "role": "Analyte",
        "changed_fields": (
            "Product State;Counted Detection;RT;Area;Int;PeakStart;PeakEnd;"
            "PeakWidth;Confidence;Reason;Projection Support Reasons;"
            "Projection Not Counted Reasons"
        ),
        "baseline_product_state": "not_counted",
        "optin_product_state": "detected_flagged",
        "baseline_counted_detection": "FALSE",
        "optin_counted_detection": "TRUE",
        "baseline_rt": "ND",
        "optin_rt": "9.10",
        "baseline_area": "ND",
        "optin_area": "12345.67",
        "baseline_nl": "NL_FAIL",
        "optin_nl": "NL_FAIL",
        "baseline_reason": (
            "decision: not_counted; "
            "not_counted: analyte_nl_fail_requires_policy"
        ),
        "optin_reason": (
            "decision: detected_flagged; support: own_max_same_peak_support"
        ),
    }


def _matrix_diff_row(
    row_key: str,
    column: str,
    *,
    optin_value: str = "9.10",
) -> dict[str, str]:
    return {
        "row_key": row_key,
        "column": column,
        "baseline_value": "ND",
        "optin_value": optin_value,
    }


def _support_row(sample_name: str, target_name: str) -> dict[str, str]:
    return {
        "schema_version": "targeted_ms1_shape_identity",
        "validation_label": "diagnostic_only",
        "decision_authority": "diagnostic_only_no_product_write",
        "sample_name": sample_name,
        "target_name": target_name,
        "target_role": "analyte",
        "paired_istd": "d3-5-hmdC",
        "source_row_id": f"{sample_name}|{target_name}",
        "candidate_state": "NL_FAIL",
        "reference_source": "representative_counted_reference:Ref|detected_clean",
        "candidate_rt_min": "9.12",
        "reference_rt_min": "9.11",
        "candidate_anchor_rt_delta_min": "0.01",
        "paired_istd_rt_min": "9.03",
        "candidate_pair_rt_delta_min": "0.09",
        "target_window_status": "candidate_inside_target_window",
        "own_max_same_peak_status": "own_max_same_peak_supported",
        "own_max_same_peak_supported": "TRUE",
        "own_max_same_peak_support_reason": OWN_MAX_SAME_PEAK_SUPPORT_REASON,
        "own_max_same_peak_similarity": "0.95",
        "own_max_compared_point_count": "165",
        "strongest_peak_rt_min": "9.12",
        "strongest_peak_own_max_ratio": "1",
        "strongest_competing_peak_rt_min": "",
        "strongest_competing_peak_own_max_ratio": "",
        "competing_peak_status": "no_competing_peak_observed",
        "reason": (
            "diagnostic_only;candidate_inside_target_window;"
            "own_max_same_peak_supported;no_competing_peak_observed"
        ),
    }


def _write_tsv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))
