from __future__ import annotations

import csv
from pathlib import Path

import pytest

from xic_extractor.diagnostics.shared_peak_identity_explanation import (
    machine_evidence_support,
    matrix_rt_drift_policy,
)


def test_matrix_rt_drift_policy_marks_close_rt_as_rt_close(tmp_path: Path) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="12.5")

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "rt_close"
    assert rows[0]["drift_evidence_level"] == "family_consensus_aligned"
    assert rows[0]["drift_compatible_status"] == "compatible"
    assert rows[0]["reason"] == "alignment_rt_within_preferred_window"


def test_matrix_rt_drift_policy_reads_excel_escaped_negative_rt_delta(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="'-51.2")

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "rt_close"
    assert rows[0]["raw_rt_delta_sec"] == "51.2"


def test_matrix_rt_drift_policy_uses_owner_edge_corrected_delta(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    owner_edge = tmp_path / "owner_edge_evidence.tsv"
    _write_owner_edges(
        owner_edge,
        [
            _owner_edge_row(
                rt_raw_delta_sec="93.0",
                rt_drift_corrected_delta_sec="4.0",
                drift_prior_source="targeted_istd_trend",
            )
        ],
    )

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        owner_edge_evidence_tsv=owner_edge,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "drift_supported"
    assert rows[0]["drift_evidence_level"] == "sample_istd_aligned"
    assert rows[0]["drift_corrected_delta_sec"] == "4"
    assert rows[0]["matrix_shift_sec"] == "91"
    assert rows[0]["drift_reference_source"] == (
        "owner_edge_evidence:targeted_istd_trend"
    )


def test_matrix_rt_drift_policy_owner_edge_contradiction_fails_closed(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    owner_edge = tmp_path / "owner_edge_evidence.tsv"
    _write_owner_edges(
        owner_edge,
        [
            _owner_edge_row(
                rt_raw_delta_sec="40.0",
                rt_drift_corrected_delta_sec="75.0",
                drift_prior_source="batch_istd_trend",
            )
        ],
    )

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        owner_edge_evidence_tsv=owner_edge,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "drift_not_supported"
    assert rows[0]["drift_compatible_status"] == "conflict"
    assert rows[0]["reason"] == "owner_edge_drift_contradictory"


def test_matrix_rt_drift_policy_uses_rt_normalization_family_improvement(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    rt_norm = tmp_path / "rt_normalization_families.tsv"
    _write_rt_normalization_families(
        rt_norm,
        [
            {
                "feature_family_id": "FAM001",
                "modelled_cell_count": "5",
                "raw_rt_range_min": "1.8",
                "normalized_rt_range_min": "0.4",
                "rt_range_improvement_min": "1.3",
                "normalized_rt_support": "improved",
                "anchor_support_level": "inside_anchor_range",
                "local_residual_window_min": "0.05",
            }
        ],
    )

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        rt_normalization_families_tsv=rt_norm,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "drift_supported"
    assert rows[0]["drift_evidence_level"] == "matrix_reference_aligned"
    assert rows[0]["drift_corrected_delta_sec"] == "17"
    assert rows[0]["drift_reference_count"] == "5"
    assert rows[0]["drift_reference_source"] == "rt_normalization_families"


def test_matrix_rt_drift_policy_uses_targeted_anchor_local_trend(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    targeted_summary = tmp_path / "targeted_istd_benchmark_summary.tsv"
    leave_one_out = tmp_path / "rt_normalization_leave_one_anchor_out.tsv"
    _write_targeted_istd_summary(
        targeted_summary,
        targeted_positive_count="85",
        coverage_denominator_count="85",
        sample_rt_p95_abs_delta_min="0.12",
    )
    _write_rt_normalization_leave_one_anchor_out(
        leave_one_out,
        evaluated_count="85",
        p95_abs_error_min="0.05",
        status="PASS",
    )

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        targeted_istd_benchmark_summary_tsv=targeted_summary,
        rt_normalization_leave_one_anchor_out_tsv=leave_one_out,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "drift_supported"
    assert rows[0]["drift_evidence_level"] == "sample_istd_aligned"
    assert rows[0]["drift_corrected_delta_sec"] == "7.2"
    assert rows[0]["matrix_shift_sec"] == "87.8"
    assert rows[0]["drift_reference_count"] == "85"
    assert rows[0]["drift_reference_source"] == (
        "targeted_istd_benchmark+rt_normalization_leave_one_anchor_out"
    )
    assert rows[0]["reason"] == "targeted_istd_anchor_local_trend_supported"


def test_matrix_rt_drift_policy_carries_istd_trend_provenance(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    targeted_summary = tmp_path / "targeted_istd_benchmark_summary.tsv"
    leave_one_out = tmp_path / "rt_normalization_leave_one_anchor_out.tsv"
    rt_trend = tmp_path / "d3_n6_meda_rt_by_injection_order.tsv"
    phase_summary = tmp_path / "d3_n6_meda_injection_phase_summary.tsv"
    _write_targeted_istd_summary(
        targeted_summary,
        targeted_positive_count="85",
        coverage_denominator_count="85",
        sample_rt_p95_abs_delta_min="0.12",
    )
    _write_rt_normalization_leave_one_anchor_out(
        leave_one_out,
        evaluated_count="85",
        p95_abs_error_min="0.05",
        status="PASS",
    )
    _write_istd_rt_trend(rt_trend)
    _write_istd_phase_summary(phase_summary)

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        targeted_istd_benchmark_summary_tsv=targeted_summary,
        rt_normalization_leave_one_anchor_out_tsv=leave_one_out,
        istd_rt_trend_tsv=rt_trend,
        istd_phase_summary_tsv=phase_summary,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "drift_supported"
    assert rows[0]["istd_trend_sample_count"] == "3"
    assert rows[0]["istd_trend_injection_order_span"] == "1-91"
    assert "early:n=1,median=24.6903,iqr=0.6349" in rows[0][
        "istd_phase_summary"
    ]
    assert str(rt_trend) in rows[0]["drift_reference_artifacts"]
    assert str(phase_summary) in rows[0]["drift_reference_artifacts"]


def test_matrix_rt_drift_policy_rejects_anchor_local_trend_from_small_sample_smoke(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    targeted_summary = tmp_path / "targeted_istd_benchmark_summary.tsv"
    leave_one_out = tmp_path / "rt_normalization_leave_one_anchor_out.tsv"
    _write_targeted_istd_summary(
        targeted_summary,
        targeted_positive_count="8",
        coverage_denominator_count="8",
        sample_rt_p95_abs_delta_min="0.12",
    )
    _write_rt_normalization_leave_one_anchor_out(
        leave_one_out,
        evaluated_count="8",
        p95_abs_error_min="0.05",
        status="PASS",
    )

    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        targeted_istd_benchmark_summary_tsv=targeted_summary,
        rt_normalization_leave_one_anchor_out_tsv=leave_one_out,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["matrix_rt_drift_status"] == "inconclusive"
    assert rows[0]["reason"] == "no_supportive_matrix_rt_drift_artifact"


def test_matrix_rt_drift_policy_requires_complete_anchor_local_trend_pair(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="95.0")
    targeted_summary = tmp_path / "targeted_istd_benchmark_summary.tsv"
    _write_targeted_istd_summary(
        targeted_summary,
        targeted_positive_count="85",
        coverage_denominator_count="85",
        sample_rt_p95_abs_delta_min="0.12",
    )

    with pytest.raises(ValueError, match="requires both"):
        matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
            alignment_cells_tsv=inputs["cells"],
            alignment_review_tsv=inputs["review"],
            targeted_istd_benchmark_summary_tsv=targeted_summary,
            oracle_keys=(("FAM001", "S1"),),
        )


def test_matrix_rt_drift_policy_writer_matches_consumer_contract(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path, rt_delta_sec="12.5")
    rows = matrix_rt_drift_policy.build_matrix_rt_drift_policy_rows(
        alignment_cells_tsv=inputs["cells"],
        alignment_review_tsv=inputs["review"],
        oracle_keys=(("FAM001", "S1"),),
    )
    path = tmp_path / "matrix_rt_drift_policy.tsv"

    matrix_rt_drift_policy.write_matrix_rt_drift_policy_rows(path, rows)
    loaded = machine_evidence_support.load_matrix_rt_drift_policy_evidence(path)

    assert loaded[("FAM001", "S1")]["matrix_rt_drift_status"] == "rt_close"


def _write_inputs(tmp_path: Path, *, rt_delta_sec: str) -> dict[str, Path]:
    cells = tmp_path / "alignment_cells.tsv"
    review = tmp_path / "alignment_review.tsv"
    _write_tsv(
        cells,
        (
            "feature_family_id",
            "sample_stem",
            "apex_rt",
            "rt_delta_sec",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "apex_rt": "8.0",
                "rt_delta_sec": rt_delta_sec,
            }
        ],
    )
    _write_tsv(
        review,
        (
            "feature_family_id",
            "family_center_mz",
            "family_center_rt",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "family_center_mz": "291.1543",
                "family_center_rt": "6.4",
            }
        ],
    )
    return {"cells": cells, "review": review}


def _owner_edge_row(
    *,
    rt_raw_delta_sec: str,
    rt_drift_corrected_delta_sec: str,
    drift_prior_source: str,
) -> dict[str, str]:
    return {
        "left_sample_stem": "S1",
        "right_sample_stem": "QC5",
        "left_precursor_mz": "291.1543",
        "right_precursor_mz": "291.1545",
        "left_rt_min": "8.0",
        "right_rt_min": "6.45",
        "decision": "strong_edge",
        "rt_raw_delta_sec": rt_raw_delta_sec,
        "rt_drift_corrected_delta_sec": rt_drift_corrected_delta_sec,
        "drift_prior_source": drift_prior_source,
        "reason": "unit_test_edge",
    }


def _write_owner_edges(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        (
            "left_sample_stem",
            "right_sample_stem",
            "left_precursor_mz",
            "right_precursor_mz",
            "left_rt_min",
            "right_rt_min",
            "decision",
            "rt_raw_delta_sec",
            "rt_drift_corrected_delta_sec",
            "drift_prior_source",
            "reason",
        ),
        rows,
    )


def _write_rt_normalization_families(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    _write_tsv(
        path,
        (
            "feature_family_id",
            "modelled_cell_count",
            "raw_rt_range_min",
            "normalized_rt_range_min",
            "rt_range_improvement_min",
            "normalized_rt_support",
            "anchor_support_level",
            "local_residual_window_min",
        ),
        rows,
    )


def _write_targeted_istd_summary(
    path: Path,
    *,
    targeted_positive_count: str,
    coverage_denominator_count: str,
    sample_rt_p95_abs_delta_min: str,
) -> None:
    _write_tsv(
        path,
        (
            "target_label",
            "role",
            "active_tag",
            "targeted_positive_count",
            "coverage_denominator_count",
            "primary_match_count",
            "selected_feature_id",
            "sample_rt_p95_abs_delta_min",
        ),
        [
            {
                "target_label": "d3-N6-medA",
                "role": "ISTD",
                "active_tag": "TRUE",
                "targeted_positive_count": targeted_positive_count,
                "coverage_denominator_count": coverage_denominator_count,
                "primary_match_count": "1",
                "selected_feature_id": "FAM001",
                "sample_rt_p95_abs_delta_min": sample_rt_p95_abs_delta_min,
            }
        ],
    )


def _write_rt_normalization_leave_one_anchor_out(
    path: Path,
    *,
    evaluated_count: str,
    p95_abs_error_min: str,
    status: str,
) -> None:
    _write_tsv(
        path,
        (
            "target_label",
            "evaluated_count",
            "p95_abs_error_min",
            "status",
        ),
        [
            {
                "target_label": "d3-N6-medA",
                "evaluated_count": evaluated_count,
                "p95_abs_error_min": p95_abs_error_min,
                "status": status,
            }
        ],
    )


def _write_istd_rt_trend(path: Path) -> None:
    _write_tsv(
        path,
        (
            "target_label",
            "sample_stem",
            "injection_order",
            "injection_phase",
            "observed_rt_min",
        ),
        [
            {
                "target_label": "d3-N6-medA",
                "sample_stem": "QC1",
                "injection_order": "1",
                "injection_phase": "early",
                "observed_rt_min": "24.1827",
            },
            {
                "target_label": "d3-N6-medA",
                "sample_stem": "NormalBC2312_DNA",
                "injection_order": "52",
                "injection_phase": "mid",
                "observed_rt_min": "25.7525",
            },
            {
                "target_label": "d3-N6-medA",
                "sample_stem": "TumorBC2264_DNA",
                "injection_order": "91",
                "injection_phase": "late",
                "observed_rt_min": "26.3365",
            },
        ],
    )


def _write_istd_phase_summary(path: Path) -> None:
    _write_tsv(
        path,
        (
            "target_label",
            "injection_phase",
            "sample_count",
            "injection_order_min",
            "injection_order_max",
            "observed_rt_min_min",
            "observed_rt_median_min",
            "observed_rt_max_min",
            "observed_rt_iqr_min",
        ),
        [
            {
                "target_label": "d3-N6-medA",
                "injection_phase": "early",
                "sample_count": "1",
                "injection_order_min": "1",
                "injection_order_max": "31",
                "observed_rt_min_min": "24.1827",
                "observed_rt_median_min": "24.6903",
                "observed_rt_max_min": "25.9377",
                "observed_rt_iqr_min": "0.6349",
            },
            {
                "target_label": "d3-N6-medA",
                "injection_phase": "overall",
                "sample_count": "3",
                "injection_order_min": "1",
                "injection_order_max": "91",
                "observed_rt_min_min": "24.1827",
                "observed_rt_median_min": "25.7525",
                "observed_rt_max_min": "26.3365",
                "observed_rt_iqr_min": "1.1149",
            },
        ],
    )


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
