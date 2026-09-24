from __future__ import annotations

import csv
import json
from pathlib import Path

from xic_extractor.alignment.shared_peak_identity_explanation import (
    machine_evidence_support,
    ms1_pattern_coherence,
)


def test_ms1_pattern_coherence_marks_boundary_constellation_supportive(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="3.0"),
            _cell("FAM001", "S2", rt_delta_sec="2.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "supportive"
    assert rows[0]["ms1_pattern_evidence_level"] == "sample_boundary_constellation"
    assert rows[0]["drift_compatible_status"] == "compatible"
    assert rows[0]["reference_peak_count"] == "3"
    assert rows[0]["shape_correlation_score"] == ""


def test_ms1_pattern_coherence_conflicts_when_apex_far_without_drift_policy(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", apex_rt="13.0", rt_delta_sec="240.0"),
            _cell("FAM001", "S2", rt_delta_sec="2.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "conflict"
    assert rows[0]["drift_compatible_status"] == "conflict"
    assert rows[0]["reason"] == "rt_or_matrix_drift_conflict"


def test_ms1_pattern_coherence_keeps_boundary_only_mismatch_inconclusive(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", peak_start_rt="9.99", peak_end_rt="10.01"),
            _cell("FAM001", "S2", peak_start_rt="9.7", peak_end_rt="10.3"),
            _cell("FAM001", "S3", peak_start_rt="9.7", peak_end_rt="10.3"),
        ],
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "inconclusive"
    assert rows[0]["ms1_pattern_evidence_level"] == "not_available"
    assert rows[0]["reason"] == "boundary_width_similarity_too_low"


def test_ms1_pattern_coherence_accepts_independent_drift_policy(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    matrix_drift = tmp_path / "matrix_rt_drift_policy.tsv"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", apex_rt="13.0", rt_delta_sec="240.0"),
            _cell("FAM001", "S2", rt_delta_sec="2.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_matrix_drift(
        matrix_drift,
        matrix_rt_drift_status="drift_supported",
        drift_corrected_delta_sec="5.0",
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        matrix_rt_drift_policy_tsv=matrix_drift,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "supportive"
    assert rows[0]["apex_coherence_sec"] == "5"
    assert rows[0]["drift_compatible_status"] == "compatible"


def test_ms1_pattern_coherence_enriches_raw_overlay_shape_metric(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam001_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="72.0"),
            _cell("FAM001", "S2", rt_delta_sec="70.0"),
            _cell("FAM001", "S3", rt_delta_sec="75.0"),
        ],
    )
    overlay.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "rt_min": 9.5,
                "rt_max": 10.5,
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill"
                },
                "traces": [
                    {
                        "sample_stem": "S1",
                        "cell_apex_rt": 10.0,
                        "cell_start_rt": 9.9,
                        "cell_end_rt": 10.1,
                        "cell_height": 980.0,
                        "local_window_max_intensity": 1000.0,
                        "trace_max_intensity": 1000.0,
                        "apex_aligned_shape_similarity": 0.94,
                        "local_window_to_global_max_ratio": 0.98,
                        "local_window_apex_delta_min": 0.01,
                        "global_trace_apex_delta_min": 0.02,
                        "rt": [9.7, 9.8, 9.9, 10.0, 10.1, 10.2, 10.3],
                        "intensity": [10.0, 12.0, 100.0, 1000.0, 120.0, 14.0, 11.0],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "partial_support"
    assert rows[0]["ms1_pattern_evidence_level"] == "trace_constellation"
    assert rows[0]["shape_correlation_score"] == "0.94"
    assert rows[0]["shape_metric_source"] == "family_ms1_overlay_raw_trace"
    assert rows[0]["cell_height"] == "980"
    assert rows[0]["local_window_max_intensity"] == "1000"
    assert rows[0]["trace_max_intensity"] == "1000"
    assert rows[0]["cell_to_local_window_max_ratio"] == "0.98"
    assert rows[0]["local_window_apex_delta_sec"] == "0.6"
    assert rows[0]["global_trace_apex_delta_sec"] == "1.2"
    assert rows[0]["family_ms1_overlay_trace_data_json"] == str(overlay)
    assert rows[0]["peak_quality_vector_status"] == "supportive"
    assert rows[0]["peak_quality_vector_basis"] == (
        "family_ms1_overlay_raw_trace_vector"
    )
    assert rows[0]["peak_quality_trace_point_count"] == "7"
    assert rows[0]["peak_quality_boundary_point_count"] == "3"
    assert rows[0]["peak_quality_feature_count"] == "5"
    assert rows[0]["peak_quality_vector_reason"] == (
        "raw_trace_peak_quality_vector_supportive"
    )


def test_ms1_pattern_coherence_conflicts_when_selected_peak_loses_to_family_peak(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam001_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM001",
                "S1",
                apex_rt="6.56",
                peak_start_rt="6.26",
                peak_end_rt="7.72",
                rt_delta_sec="-39.0",
            ),
            _cell("FAM001", "S2", apex_rt="7.96"),
            _cell("FAM001", "S3", apex_rt="7.98"),
            _cell("FAM001", "S4", apex_rt="8.01"),
        ],
    )
    overlay.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "rt_min": 5.0,
                "rt_max": 8.8,
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill"
                },
                "traces": [
                    {
                        "sample_stem": "S1",
                        "cell_apex_rt": 6.56,
                        "cell_start_rt": 6.26,
                        "cell_end_rt": 7.72,
                        "cell_height": 2_200_000.0,
                        "local_window_max_intensity": 2_200_000.0,
                        "trace_max_intensity": 2_200_000.0,
                        "apex_aligned_shape_similarity": 0.90,
                        "local_window_to_global_max_ratio": 1.0,
                        "local_window_apex_delta_min": 0.0,
                        "global_trace_apex_delta_min": 0.0,
                        "rt": [6.40, 6.56, 6.68, 7.70, 7.93, 8.10],
                        "intensity": [
                            1_200_000.0,
                            2_200_000.0,
                            2_000_000.0,
                            100_000.0,
                            780_000.0,
                            670_000.0,
                        ],
                    },
                    {
                        "sample_stem": "S2",
                        "cell_apex_rt": 7.96,
                        "cell_start_rt": 7.86,
                        "cell_end_rt": 8.06,
                        "cell_height": 3_000_000.0,
                        "local_window_max_intensity": 3_000_000.0,
                        "trace_max_intensity": 3_000_000.0,
                        "apex_aligned_shape_similarity": 0.96,
                        "local_window_to_global_max_ratio": 1.0,
                        "local_window_apex_delta_min": 0.0,
                        "global_trace_apex_delta_min": 0.0,
                        "rt": [7.80, 7.96, 8.10],
                        "intensity": [100_000.0, 3_000_000.0, 2_600_000.0],
                    },
                    {
                        "sample_stem": "S3",
                        "cell_apex_rt": 7.98,
                        "cell_start_rt": 7.88,
                        "cell_end_rt": 8.08,
                        "cell_height": 2_800_000.0,
                        "local_window_max_intensity": 2_800_000.0,
                        "trace_max_intensity": 2_800_000.0,
                        "apex_aligned_shape_similarity": 0.96,
                        "local_window_to_global_max_ratio": 1.0,
                        "local_window_apex_delta_min": 0.0,
                        "global_trace_apex_delta_min": 0.0,
                        "rt": [7.80, 7.98, 8.10],
                        "intensity": [100_000.0, 2_800_000.0, 2_500_000.0],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "conflict"
    assert rows[0]["ms1_pattern_evidence_level"] == "trace_constellation"
    assert rows[0]["reason"] == (
        "family_ms1_overlay_competing_peak_matches_family_consensus"
    )
    assert rows[0]["shape_correlation_score"] == "0.9"


def test_ms1_pattern_coherence_uses_detected_anchor_local_own_max_shape(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam088_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM088",
                "DET_A",
                status="detected",
                apex_rt="14.34",
                peak_start_rt="14.22",
                peak_end_rt="14.44",
            ),
            _cell(
                "FAM088",
                "DET_B",
                status="detected",
                apex_rt="14.36",
                peak_start_rt="14.24",
                peak_end_rt="14.46",
            ),
            _cell(
                "FAM088",
                "MID_RESCUE",
                status="backfilled",
                apex_rt="14.35",
                peak_start_rt="14.22",
                peak_end_rt="14.45",
            ),
            _cell(
                "FAM088",
                "EARLY_RESCUE",
                status="backfilled",
                apex_rt="12.12",
                peak_start_rt="11.95",
                peak_end_rt="12.28",
            ),
            _cell(
                "FAM088",
                "BAD_SHAPE",
                status="backfilled",
                apex_rt="14.35",
                peak_start_rt="14.22",
                peak_end_rt="14.45",
            ),
            _cell(
                "FAM088",
                "DUP_RESCUE",
                status="backfilled",
                apex_rt="14.35",
                peak_start_rt="14.22",
                peak_end_rt="14.45",
                gap_fill_state="not_filled",
                gap_fill_reason="not_requested_duplicate_loser",
            ),
        ],
    )
    overlay.write_text(
        json.dumps(
            {
                "family_id": "FAM088",
                "rt_min": 11.3,
                "rt_max": 17.3,
                "evidence_summary": {
                    "family_verdict": "review_required_neighboring_ms1_interference"
                },
                "traces": [
                    _overlay_trace(
                        "DET_A",
                        status="detected",
                        cell_apex_rt=14.34,
                        rt=[12.0, 14.10, 14.25, 14.35, 14.45, 14.60, 16.0],
                        intensity=[80.0, 20.0, 350.0, 1000.0, 360.0, 30.0, 100.0],
                    ),
                    _overlay_trace(
                        "DET_B",
                        status="detected",
                        cell_apex_rt=14.36,
                        rt=[12.0, 14.10, 14.25, 14.35, 14.45, 14.60, 16.0],
                        intensity=[70.0, 25.0, 330.0, 980.0, 390.0, 35.0, 90.0],
                    ),
                    _overlay_trace(
                        "MID_RESCUE",
                        status="backfilled",
                        cell_apex_rt=14.35,
                        rt=[12.0, 14.10, 14.25, 14.35, 14.45, 14.60, 16.0],
                        intensity=[1800.0, 18.0, 320.0, 940.0, 340.0, 22.0, 2100.0],
                    ),
                    _overlay_trace(
                        "EARLY_RESCUE",
                        status="backfilled",
                        cell_apex_rt=12.12,
                        rt=[11.95, 12.12, 12.30, 14.25, 14.35, 14.45, 16.0],
                        intensity=[900.0, 2200.0, 850.0, 120.0, 180.0, 110.0, 90.0],
                    ),
                    _overlay_trace(
                        "BAD_SHAPE",
                        status="backfilled",
                        cell_apex_rt=14.35,
                        rt=[12.0, 14.10, 14.25, 14.35, 14.45, 14.60, 16.0],
                        intensity=[100.0, 930.0, 580.0, 60.0, 560.0, 940.0, 120.0],
                    ),
                    _overlay_trace(
                        "DUP_RESCUE",
                        status="backfilled",
                        cell_apex_rt=14.35,
                        rt=[12.0, 14.10, 14.25, 14.35, 14.45, 14.60, 16.0],
                        intensity=[2000.0, 18.0, 320.0, 940.0, 340.0, 22.0, 2100.0],
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(
            ("FAM088", "MID_RESCUE"),
            ("FAM088", "EARLY_RESCUE"),
            ("FAM088", "BAD_SHAPE"),
            ("FAM088", "DUP_RESCUE"),
        ),
    )
    by_sample = {row["sample_stem"]: row for row in rows}

    assert by_sample["MID_RESCUE"]["ms1_pattern_status"] == "supportive"
    assert by_sample["MID_RESCUE"]["ms1_pattern_evidence_level"] == (
        "trace_constellation"
    )
    assert by_sample["MID_RESCUE"]["shape_metric_source"] == (
        "family_ms1_overlay_anchor_peak_own_max"
    )
    assert float(by_sample["MID_RESCUE"]["shape_correlation_score"]) > 0.5
    assert float(by_sample["MID_RESCUE"]["anchor_peak_delta_sec"]) <= 21.0
    assert by_sample["MID_RESCUE"]["reason"] == (
        "family_ms1_overlay_anchor_peak_own_max_shape_supported"
    )

    assert by_sample["EARLY_RESCUE"]["ms1_pattern_status"] == "conflict"
    assert by_sample["EARLY_RESCUE"]["reason"] == (
        "family_ms1_overlay_anchor_peak_mismatch"
    )

    assert by_sample["BAD_SHAPE"]["ms1_pattern_status"] == "inconclusive"
    assert float(by_sample["BAD_SHAPE"]["shape_correlation_score"]) < 0.5
    assert by_sample["BAD_SHAPE"]["reason"] == (
        "family_ms1_overlay_anchor_peak_shape_below_threshold"
    )

    assert by_sample["DUP_RESCUE"]["ms1_pattern_status"] == "conflict"
    assert by_sample["DUP_RESCUE"]["reason"] == "alignment_gap_fill_duplicate_loser"


def test_ms1_pattern_coherence_uses_nearest_detected_anchor_cluster(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "split_detected_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM_SPLIT",
                "DET_EARLY",
                status="detected",
                apex_rt="10.00",
                peak_start_rt="9.90",
                peak_end_rt="10.10",
            ),
            _cell(
                "FAM_SPLIT",
                "DET_LATE",
                status="detected",
                apex_rt="12.00",
                peak_start_rt="11.90",
                peak_end_rt="12.10",
            ),
            _cell(
                "FAM_SPLIT",
                "LATE_RESCUE",
                status="backfilled",
                apex_rt="12.03",
                peak_start_rt="11.93",
                peak_end_rt="12.13",
            ),
        ],
    )
    overlay.write_text(
        json.dumps(
            {
                "family_id": "FAM_SPLIT",
                "rt_min": 9.0,
                "rt_max": 13.0,
                "evidence_summary": {
                    "family_verdict": "review_required_neighboring_ms1_interference"
                },
                "traces": [
                    _overlay_trace(
                        "DET_EARLY",
                        status="detected",
                        cell_apex_rt=10.00,
                        rt=[9.70, 9.90, 10.00, 10.10, 10.30],
                        intensity=[50.0, 250.0, 900.0, 240.0, 40.0],
                    ),
                    _overlay_trace(
                        "DET_LATE",
                        status="detected",
                        cell_apex_rt=12.00,
                        rt=[11.70, 11.90, 12.00, 12.10, 12.30],
                        intensity=[45.0, 270.0, 920.0, 260.0, 45.0],
                    ),
                    _overlay_trace(
                        "LATE_RESCUE",
                        status="backfilled",
                        cell_apex_rt=12.03,
                        rt=[11.73, 11.93, 12.03, 12.13, 12.33],
                        intensity=[45.0, 255.0, 910.0, 245.0, 45.0],
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM_SPLIT", "LATE_RESCUE"),),
    )

    assert rows[0]["ms1_pattern_status"] == "supportive"
    assert rows[0]["reason"] == (
        "family_ms1_overlay_anchor_peak_own_max_shape_supported"
    )
    assert abs(float(rows[0]["anchor_peak_rt"]) - 12.0) < 0.001
    assert abs(float(rows[0]["anchor_peak_delta_sec"])) <= 21.0


def test_ms1_pattern_coherence_requires_anchor_for_backfilled_shape_support(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "no_detected_anchor_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM001",
                "S1",
                status="backfilled",
                rt_delta_sec="0.0",
                gap_fill_state="owner_backfill",
                gap_fill_reason="owner_backfill",
            ),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_overlay(
        overlay,
        shape_similarity=0.94,
        local_window_to_global_max_ratio=1.0,
        local_window_apex_delta_min=0.01,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "inconclusive"
    assert rows[0]["ms1_pattern_evidence_level"] == "trace_constellation"
    assert rows[0]["reason"] == (
        "family_ms1_overlay_anchor_peak_evidence_unavailable"
    )


def test_ms1_pattern_coherence_keeps_conflict_when_anchor_evidence_missing(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    drift = tmp_path / "matrix_drift.tsv"
    overlay = tmp_path / "no_anchor_conflict_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM001",
                "S1",
                status="backfilled",
                rt_delta_sec="0.0",
                gap_fill_state="owner_backfill",
                gap_fill_reason="owner_backfill",
            ),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_matrix_drift(
        drift,
        matrix_rt_drift_status="drift_not_supported",
        drift_corrected_delta_sec="120",
    )
    _write_overlay(
        overlay,
        shape_similarity=0.94,
        local_window_to_global_max_ratio=1.0,
        local_window_apex_delta_min=0.01,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        matrix_rt_drift_policy_tsv=drift,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "conflict"
    assert rows[0]["reason"] == "rt_or_matrix_drift_conflict"


def test_ms1_pattern_coherence_trace_window_gap_does_not_preserve_support(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "trace_window_gap_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell(
                "FAM001",
                "S1",
                status="backfilled",
                rt_delta_sec="0.0",
                gap_fill_state="owner_backfill",
                gap_fill_reason="owner_backfill",
            ),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_overlay(
        overlay,
        shape_similarity=0.94,
        local_window_to_global_max_ratio=1.0,
        local_window_apex_delta_min=0.01,
        rt_min=8.0,
        rt_max=9.0,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "inconclusive"
    assert "family_ms1_overlay_trace_window_does_not_cover_cell_apex" in rows[0][
        "reason"
    ]


def test_ms1_pattern_coherence_keeps_low_shape_with_local_peak_partial(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam001_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="0.0"),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_overlay(
        overlay,
        shape_similarity=0.16,
        local_window_to_global_max_ratio=1.0,
        local_window_apex_delta_min=0.083333,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "partial_support"
    assert rows[0]["ms1_pattern_evidence_level"] == "trace_constellation"
    assert rows[0]["shape_correlation_score"] == "0.16"
    assert rows[0]["cell_to_local_window_max_ratio"] == "0.3"
    assert rows[0]["local_window_to_global_max_ratio"] == "1"
    assert rows[0]["reason"] == (
        "family_ms1_overlay_shape_metric_inconclusive_apex_or_height"
    )


def test_ms1_pattern_coherence_conflicts_on_low_local_peak_dominance(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam001_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="0.0"),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_overlay(
        overlay,
        shape_similarity=0.94,
        local_window_to_global_max_ratio=0.2,
        local_window_apex_delta_min=0.01,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "conflict"
    assert rows[0]["ms1_pattern_evidence_level"] == "trace_constellation"
    assert rows[0]["reason"] == (
        "family_ms1_overlay_expected_window_lacks_complete_peak"
    )


def test_ms1_pattern_coherence_old_overlay_without_trace_vector_is_not_available(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    overlay = tmp_path / "fam001_overlay_trace_data.json"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="0.0"),
            _cell("FAM001", "S2", rt_delta_sec="1.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    _write_overlay(
        overlay,
        shape_similarity=0.94,
        local_window_to_global_max_ratio=1.0,
        local_window_apex_delta_min=0.01,
        include_trace_vector=False,
    )

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        family_ms1_overlay_trace_data_jsons=(overlay,),
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["shape_metric_source"] == "family_ms1_overlay_raw_trace"
    assert rows[0]["peak_quality_vector_status"] == "not_available"
    assert rows[0]["peak_quality_vector_basis"] == ""
    assert rows[0]["peak_quality_vector_reason"] == "no_raw_trace_vector"


def test_ms1_pattern_coherence_does_not_fabricate_absent_cells(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    _write_cells(cells, [_cell("FAM001", "S1", status="absent", apex_rt="")])

    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        oracle_keys=(("FAM001", "S1"),),
    )

    assert rows[0]["ms1_pattern_status"] == "not_available"
    assert rows[0]["ms1_pattern_evidence_level"] == "not_available"
    assert rows[0]["reason"] == "alignment_cell_not_present"


def test_ms1_pattern_coherence_writer_matches_consumer_contract(
    tmp_path: Path,
) -> None:
    cells = tmp_path / "alignment_cells.tsv"
    output = tmp_path / "ms1_pattern.tsv"
    _write_cells(
        cells,
        [
            _cell("FAM001", "S1", rt_delta_sec="3.0"),
            _cell("FAM001", "S2", rt_delta_sec="2.0"),
            _cell("FAM001", "S3", rt_delta_sec="-1.0"),
        ],
    )
    rows = ms1_pattern_coherence.build_ms1_pattern_coherence_rows(
        alignment_cells_tsv=cells,
        oracle_keys=(("FAM001", "S1"),),
    )

    ms1_pattern_coherence.write_ms1_pattern_coherence_rows(output, rows)
    loaded = machine_evidence_support.load_ms1_pattern_coherence_evidence(output)

    assert loaded[("FAM001", "S1")]["ms1_pattern_status"] == "supportive"


def _cell(
    feature_family_id: str,
    sample_stem: str,
    *,
    status: str = "selected",
    apex_rt: str = "10.0",
    peak_start_rt: str = "9.9",
    peak_end_rt: str = "10.1",
    rt_delta_sec: str = "0.0",
    gap_fill_state: str = "",
    gap_fill_reason: str = "",
) -> dict[str, str]:
    return {
        "feature_family_id": feature_family_id,
        "sample_stem": sample_stem,
        "status": status,
        "apex_rt": apex_rt,
        "peak_start_rt": peak_start_rt,
        "peak_end_rt": peak_end_rt,
        "rt_delta_sec": rt_delta_sec,
        "trace_quality": "clean",
        "scan_support_score": "1.0",
        "gap_fill_state": gap_fill_state,
        "gap_fill_reason": gap_fill_reason,
    }


def _write_cells(path: Path, rows: list[dict[str, str]]) -> None:
    _write_tsv(
        path,
        (
            "feature_family_id",
            "sample_stem",
            "status",
            "apex_rt",
            "peak_start_rt",
            "peak_end_rt",
            "rt_delta_sec",
            "trace_quality",
            "scan_support_score",
            "gap_fill_state",
            "gap_fill_reason",
        ),
        rows,
    )


def _write_matrix_drift(
    path: Path,
    *,
    matrix_rt_drift_status: str,
    drift_corrected_delta_sec: str,
) -> None:
    _write_tsv(
        path,
        (
            "feature_family_id",
            "sample_stem",
            "matrix_rt_drift_status",
            "drift_evidence_level",
            "raw_rt_delta_sec",
            "drift_corrected_delta_sec",
            "matrix_shift_sec",
            "drift_reference_count",
            "drift_reference_source",
            "drift_compatible_status",
            "reason",
            "diagnostic_only",
        ),
        [
            {
                "feature_family_id": "FAM001",
                "sample_stem": "S1",
                "matrix_rt_drift_status": matrix_rt_drift_status,
                "drift_evidence_level": "sample_istd_aligned",
                "raw_rt_delta_sec": "240.0",
                "drift_corrected_delta_sec": drift_corrected_delta_sec,
                "matrix_shift_sec": "235.0",
                "drift_reference_count": "85",
                "drift_reference_source": "unit_test",
                "drift_compatible_status": "compatible",
                "reason": "unit_test_drift",
                "diagnostic_only": "TRUE",
            }
        ],
    )


def _write_overlay(
    path: Path,
    *,
    shape_similarity: float,
    local_window_to_global_max_ratio: float,
    local_window_apex_delta_min: float,
    cell_height: float = 300.0,
    local_window_max_intensity: float = 1000.0,
    include_trace_vector: bool = True,
    rt_min: float = 9.5,
    rt_max: float = 10.5,
) -> None:
    trace = {
        "sample_stem": "S1",
        "cell_apex_rt": 10.0,
        "cell_start_rt": 9.9,
        "cell_end_rt": 10.1,
        "cell_height": cell_height,
        "local_window_max_intensity": local_window_max_intensity,
        "trace_max_intensity": local_window_max_intensity,
        "apex_aligned_shape_similarity": shape_similarity,
        "local_window_to_global_max_ratio": local_window_to_global_max_ratio,
        "local_window_apex_delta_min": local_window_apex_delta_min,
        "global_trace_apex_delta_min": 0.02,
    }
    if include_trace_vector:
        trace["rt"] = [9.7, 9.8, 9.9, 10.0, 10.1, 10.2, 10.3]
        trace["intensity"] = [
            10.0,
            12.0,
            cell_height * 0.10,
            local_window_max_intensity,
            cell_height * 0.12,
            14.0,
            11.0,
        ]
    path.write_text(
        json.dumps(
            {
                "family_id": "FAM001",
                "rt_min": rt_min,
                "rt_max": rt_max,
                "evidence_summary": {
                    "family_verdict": "ms1_shape_supports_family_backfill"
                },
                "traces": [trace],
            }
        ),
        encoding="utf-8",
    )


def _overlay_trace(
    sample_stem: str,
    *,
    status: str,
    cell_apex_rt: float,
    rt: list[float],
    intensity: list[float],
) -> dict[str, object]:
    return {
        "sample_stem": sample_stem,
        "status": status,
        "cell_apex_rt": cell_apex_rt,
        "cell_start_rt": cell_apex_rt - 0.1,
        "cell_end_rt": cell_apex_rt + 0.1,
        "cell_height": max(intensity),
        "local_window_max_intensity": max(intensity),
        "trace_max_intensity": max(intensity),
        "apex_aligned_shape_similarity": 0.0,
        "local_window_to_global_max_ratio": 0.1,
        "local_window_apex_delta_min": 0.0,
        "global_trace_apex_delta_min": 0.0,
        "rt": rt,
        "intensity": intensity,
    }


def _write_tsv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, delimiter="\t", fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
