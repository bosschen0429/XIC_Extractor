from __future__ import annotations

import json
from pathlib import Path

from xic_extractor.discovery import pt_pattern_review_packet as packet
from xic_extractor.discovery import pt_pattern_trace_evidence as evidence
from xic_extractor.tabular_io import read_delimited_rows, write_delimited_rows


def _candidate_row(
    candidate_id: str,
    *,
    method_label: str = "pt_pattern",
    state: str = "pt_pattern_candidate",
) -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "method_label": method_label,
        "candidate_id": candidate_id,
        "candidate_state": state,
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": "90",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "cid_nl_base_status": "not_required",
        "cid_nl_base_join_mode": "not_required",
    }


def _summary_row(candidate_id: str, status: str) -> dict[str, str]:
    return {
        "sample_stem": "SampleA",
        "candidate_id": candidate_id,
        "scan_pattern_seed_status": "full_pattern_seeded",
        "base_mz": "500.0",
        "base_rt_apex_min": "10.0",
        "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
        "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": str(evidence.TRACE_SMOOTH_WINDOW_POINTS),
        "min_effective_smoothing_window_sec": "0.90",
        "max_trace_point_count": "31",
        "non_base_positions_supported": "3",
        "max_abs_apex_delta_sec": "2.0",
        "min_pearson_r_to_base": "0.95",
        "min_active_overlap_fraction": "0.90",
        "min_shifted_pearson_r_to_base": "0.98",
        "max_abs_shape_shift_sec_to_base": "1.0",
        "raw_max_abs_apex_delta_sec": "2.0",
        "raw_min_pearson_r_to_base": "0.80",
        "raw_min_active_overlap_fraction": "0.70",
        "trace_ambiguity_status": "review_required",
        "trace_ambiguity_flags": "shifted_only_support_present",
        "shifted_only_position_labels": "pt_plus_4",
        "trace_support_status": "supported",
        "combined_pt_pattern_status": status,
        "reason": "unit_fixture",
    }


def _metric_rows(candidate_id: str) -> list[dict[str, str]]:
    return [
        {
            "candidate_id": candidate_id,
            "position_label": label,
            "trace_decision_basis": evidence.TRACE_DECISION_BASIS,
            "trace_smoothing_method": evidence.TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": str(
                evidence.TRACE_SMOOTH_WINDOW_POINTS
            ),
            "median_rt_step_sec": "0.06",
            "effective_smoothing_window_sec": "0.90",
            "apex_delta_sec_to_base": "1.0",
            "pearson_r_to_base": "0.95",
            "active_overlap_fraction": "0.90",
            "shifted_pearson_r_to_base": "0.98",
            "shape_shift_sec_to_base": "1.0",
            "raw_apex_delta_sec_to_base": "1.0",
            "raw_smoothed_apex_delta_sec": "0.5",
            "raw_pearson_r_to_base": "0.80",
            "raw_active_overlap_fraction": "0.70",
            "trace_quality_flags": "shifted_only_support",
            "trace_support_status": "supported",
            "reason": "unit_fixture",
        }
        for label in ("pt_plus_1", "pt_plus_2", "pt_plus_4")
    ]


def test_build_review_packet_rows_samples_per_status_and_keeps_pending_oracle() -> None:
    rows = packet.build_review_packet_rows(
        candidate_rows=[
            _candidate_row("cand-supported-a"),
            _candidate_row("cand-supported-b"),
            _candidate_row("cand-partial"),
        ],
        summary_rows=[
            _summary_row("cand-supported-a", "pt_pattern_detected_diagnostic"),
            _summary_row("cand-supported-b", "pt_pattern_detected_diagnostic"),
            _summary_row("cand-partial", "pt_pattern_candidate_partial"),
        ],
        metric_rows=[
            *_metric_rows("cand-supported-a"),
            *_metric_rows("cand-supported-b"),
            *_metric_rows("cand-partial"),
        ],
        packet_label="unit_packet",
        source_label="pt_only",
        max_per_status=1,
    )

    assert [row["candidate_id"] for row in rows] == [
        "cand-supported-a",
        "cand-partial",
    ]
    assert rows[0]["schema_version"] == packet.SCHEMA_VERSION
    assert rows[0]["readiness_tier"] == "diagnostic_only"
    assert rows[0]["evidence_owner"] == packet.EVIDENCE_OWNER
    assert rows[0]["review_bucket"] == "detected_diagnostic_review"
    assert rows[0]["human_review_decision"] == ""
    assert rows[0]["oracle_status"] == "pending_human_review"
    assert rows[0]["pt_plus_4_trace_support_status"] == "supported"
    assert rows[0]["trace_decision_basis"] == evidence.TRACE_DECISION_BASIS
    assert rows[0]["min_shifted_pearson_r_to_base"] == "0.98"
    assert rows[0]["max_abs_shape_shift_sec_to_base"] == "1.0"
    assert rows[0]["trace_ambiguity_status"] == "review_required"
    assert rows[0]["trace_ambiguity_flags"] == "shifted_only_support_present"
    assert rows[0]["shifted_only_position_labels"] == "pt_plus_4"
    assert rows[0]["min_effective_smoothing_window_sec"] == "0.90"
    assert rows[0]["max_trace_point_count"] == "31"
    assert rows[0]["raw_min_pearson_r_to_base"] == "0.80"
    assert rows[0]["pt_plus_4_trace_quality_flags"] == "shifted_only_support"
    assert rows[0]["pt_plus_4_effective_smoothing_window_sec"] == "0.90"
    assert rows[0]["pt_plus_4_raw_smoothed_apex_delta_sec"] == "0.5"
    assert rows[0]["pt_plus_4_shifted_pearson_r_to_base"] == "0.98"
    assert rows[0]["pt_plus_4_shape_shift_sec_to_base"] == "1.0"
    assert rows[0]["pt_plus_4_raw_active_overlap_fraction"] == "0.70"


def test_review_packet_cli_writes_csv_and_sidecar(tmp_path: Path) -> None:
    candidates_csv = tmp_path / "pt_pattern_candidates.csv"
    summary_csv = tmp_path / "pt_pattern_trace_summary.csv"
    metrics_csv = tmp_path / "pt_pattern_trace_metrics.csv"

    write_delimited_rows(
        candidates_csv,
        [_candidate_row("cand-supported")],
        packet.CANDIDATE_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        summary_csv,
        [_summary_row("cand-supported", "pt_pattern_detected_diagnostic")],
        packet.SUMMARY_REQUIRED_COLUMNS,
    )
    write_delimited_rows(
        metrics_csv,
        _metric_rows("cand-supported"),
        packet.METRIC_REQUIRED_COLUMNS,
    )

    from tools.diagnostics import pt_pattern_review_packet as cli

    assert cli.main(
        [
            "--candidates-csv",
            str(candidates_csv),
            "--trace-summary-csv",
            str(summary_csv),
            "--trace-metrics-csv",
            str(metrics_csv),
            "--output-dir",
            str(tmp_path / "review_packet"),
            "--packet-label",
            "unit_packet",
            "--source-label",
            "pt_only",
        ]
    ) == 0

    rows = read_delimited_rows(
        tmp_path / "review_packet" / "pt_pattern_manual_review_packet.csv",
        required_columns=packet.PACKET_COLUMNS,
        delimiter=",",
    )
    assert rows[0]["human_review_decision"] == ""
    assert rows[0]["oracle_status"] == "pending_human_review"

    sidecar = json.loads(
        (tmp_path / "review_packet" / "pt_pattern_manual_review_packet.json")
        .read_text(encoding="utf-8")
    )
    assert sidecar["schema_version"] == packet.SCHEMA_VERSION
    assert sidecar["readiness_tier"] == "diagnostic_only"
    assert sidecar["packet_row_count"] == 1
    assert sidecar["human_review_decision_values"] == [
        "accepted",
        "rejected",
        "uncertain",
    ]
