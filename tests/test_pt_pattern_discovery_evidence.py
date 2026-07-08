from __future__ import annotations

from xic_extractor.discovery.delta_mass_patterns import PtPatternCandidate
from xic_extractor.discovery.pt_pattern_discovery_evidence import (
    build_pt_pattern_evidence_rows,
    load_pt_trace_summary_rows,
)


def _candidate(**overrides: object) -> PtPatternCandidate:
    values: dict[str, object] = {
        "schema_version": "pt_pattern_candidates.v1",
        "sample_stem": "Sample_1",
        "method_label": "pt_pattern",
        "pattern_id": "pt",
        "candidate_id": "Sample_1@pt@base-1",
        "candidate_state": "pt_pattern_candidate",
        "review_priority": "HIGH",
        "evidence_tier": "A",
        "evidence_score": 90,
        "base_feature_id": "base-1",
        "base_mz": 500.0,
        "base_rt_apex_min": 10.00,
        "base_height": 1000.0,
        "base_area": 500.0,
        "position_count": 4,
        "required_position_count": 4,
        "rt_span_sec": 5.0,
        "pattern_rt_status": "within_30s",
        "local_signal_support_status": "supported",
        "cross_sample_support_status": "not_evaluated",
        "ratio_basis": "both",
        "cid_nl_base_status": "not_required",
        "cid_nl_base_candidate_id": "",
        "cid_nl_base_join_mode": "not_required",
        "reason": "full Pt pattern within tolerance",
        "pt_plus_1_feature_id": "plus1-1",
        "pt_plus_1_mz": 501.0021,
        "pt_plus_1_expected_mz": 501.0021,
        "pt_plus_1_mz_error_ppm": 0.0,
        "pt_plus_1_delta_error_da": 0.0,
        "pt_plus_1_rt_delta_sec": 1.0,
        "pt_plus_1_height_ratio_to_base": 0.8,
        "pt_plus_1_area_ratio_to_base": 0.8,
        "pt_plus_2_feature_id": "plus2-1",
        "pt_plus_2_mz": 502.0023,
        "pt_plus_2_expected_mz": 502.0023,
        "pt_plus_2_mz_error_ppm": 0.0,
        "pt_plus_2_delta_error_da": 0.0,
        "pt_plus_2_rt_delta_sec": 2.0,
        "pt_plus_2_height_ratio_to_base": 0.2,
        "pt_plus_2_area_ratio_to_base": 0.2,
        "pt_plus_4_feature_id": "plus4-1",
        "pt_plus_4_mz": 504.0052,
        "pt_plus_4_expected_mz": 504.0052,
        "pt_plus_4_mz_error_ppm": 0.0,
        "pt_plus_4_delta_error_da": 0.0,
        "pt_plus_4_rt_delta_sec": 3.0,
        "pt_plus_4_height_ratio_to_base": 0.05,
        "pt_plus_4_area_ratio_to_base": 0.05,
    }
    values.update(overrides)
    return PtPatternCandidate(**values)  # type: ignore[arg-type]


def _trace_summary_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "sample_stem": "Sample_1",
        "candidate_id": "Sample_1@pt@base-1",
        "scan_pattern_seed_status": "full_pattern_seeded",
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": 3,
        "max_abs_apex_delta_sec": 0.4,
        "min_pearson_r_to_base": 0.95,
        "min_active_overlap_fraction": 0.70,
    }
    row.update(overrides)
    return row


def test_discovery_evidence_keeps_missing_trace_as_not_evaluated() -> None:
    rows = build_pt_pattern_evidence_rows([_candidate()])

    assert len(rows) == 1
    row = rows[0]
    assert row["scan_pattern_seed_status"] == "full_pattern_seeded"
    assert row["trace_evidence_status"] == "not_evaluated"
    assert row["trace_rule_call"] == "not_evaluated"
    assert row["trace_detection_status"] == "not_evaluated"
    assert row["trace_rule_reason"] == "trace_summary_not_provided"
    assert row["matrix_write_authority"] is False
    assert row["counted_detection_authority"] is False
    assert row["export_activation_authority"] is False


def test_discovery_evidence_applies_shared_rule_when_trace_summary_matches() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[_trace_summary_row()],
    )

    row = rows[0]
    assert row["trace_evidence_status"] == "evaluated"
    assert row["trace_rule_call"] == "accept"
    assert row["trace_detection_status"] == "detected"
    assert row["trace_rule_reason"] == "full_pattern_close_apex_shape_overlap"
    assert row["combined_pt_pattern_status"] == "pt_pattern_detected_diagnostic"
    assert row["min_pearson_r_to_base"] == 0.95


def test_discovery_evidence_records_effective_scan_aware_apex_threshold() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(
                max_abs_apex_delta_sec=1.30413,
                min_effective_smoothing_window_sec=19.7353,
                trace_smoothing_window_points=15,
                min_pearson_r_to_base=0.982299,
                min_active_overlap_fraction=0.952381,
            )
        ],
    )

    row = rows[0]
    assert row["trace_rule_call"] == "accept"
    assert row["max_abs_apex_delta_threshold_sec"] == 1.6446083333333332


def test_discovery_evidence_keeps_shifted_shape_review_only() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(
                max_abs_apex_delta_sec=25.0,
                min_pearson_r_to_base=0.75,
                min_shifted_pearson_r_to_base=0.85,
                max_abs_shape_shift_sec_to_base=44.9,
            )
        ],
    )

    row = rows[0]
    assert row["trace_rule_call"] == "reject"
    assert row["trace_detection_status"] == "non_detected"
    assert row["trace_rule_reason"] == "shifted_shape_review_only"
    assert row["min_shifted_pearson_r_to_base_threshold"] == 0.85
    assert row["max_abs_shape_shift_sec_to_base_threshold"] == 45.0


def test_discovery_evidence_loads_legacy_trace_summary_without_shifted_columns(
    tmp_path,
) -> None:
    legacy_trace_summary = tmp_path / "legacy_trace_summary.csv"
    legacy_trace_summary.write_text(
        "\n".join(
            [
                (
                    "schema_version,sample_stem,candidate_id,"
                    "scan_pattern_seed_status,base_mz,base_rt_apex_min,"
                    "rt_half_window_sec,ppm,mz_source,trace_decision_basis,"
                    "trace_smoothing_method,trace_smoothing_window_points,"
                    "min_effective_smoothing_window_sec,"
                    "positions_requested,positions_with_points,"
                    "positions_with_signal,non_base_positions_supported,"
                    "max_abs_apex_delta_sec,min_pearson_r_to_base,"
                    "min_active_overlap_fraction,raw_max_abs_apex_delta_sec,"
                    "raw_min_pearson_r_to_base,raw_min_active_overlap_fraction,"
                    "trace_support_status,combined_pt_pattern_status,reason"
                ),
                (
                    "pt_pattern_trace_diagnosis.v2,Sample_1,Sample_1@pt@base-1,"
                    "full_pattern_seeded,500,10,30,20,observed,"
                    "gaussian15_smoothed_trace,gaussian15,15,19.5,4,4,4,3,0.4,0.95,"
                    "0.70,0.4,0.95,0.70,supported,"
                    "pt_pattern_detected_diagnostic,legacy close-apex row"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    trace_rows = load_pt_trace_summary_rows(legacy_trace_summary)
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=trace_rows,
    )

    row = rows[0]
    assert row["trace_rule_call"] == "accept"
    assert row["trace_rule_reason"] == "full_pattern_close_apex_shape_overlap"
    assert row["min_shifted_pearson_r_to_base"] == ""
    assert row["trace_ambiguity_status"] == ""


def test_discovery_evidence_records_reject_without_dropping_seed_candidate() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[_trace_summary_row(min_pearson_r_to_base=0.84)],
    )

    row = rows[0]
    assert row["candidate_state"] == "pt_pattern_candidate"
    assert row["trace_rule_call"] == "reject"
    assert row["trace_detection_status"] == "non_detected"
    assert row["trace_rule_reason"] == "pearson_below_threshold"


def test_discovery_evidence_marks_missing_candidate_trace_row() -> None:
    rows = build_pt_pattern_evidence_rows(
        [_candidate()],
        trace_summary_rows=[
            _trace_summary_row(candidate_id="Sample_1@pt@other-base")
        ],
    )

    row = rows[0]
    assert row["trace_rule_call"] == "not_evaluated"
    assert row["trace_rule_reason"] == "trace_summary_missing_for_candidate"


def test_discovery_evidence_rejects_duplicate_trace_summary_candidate_ids() -> None:
    try:
        build_pt_pattern_evidence_rows(
            [_candidate()],
            trace_summary_rows=[_trace_summary_row(), _trace_summary_row()],
        )
    except ValueError as error:
        assert "duplicate trace summary candidate_id" in str(error)
    else:  # pragma: no cover - defensive assertion style keeps py3.10 simple
        raise AssertionError("expected duplicate candidate_id to raise ValueError")
