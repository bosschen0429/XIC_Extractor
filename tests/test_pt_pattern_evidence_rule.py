from __future__ import annotations

import pytest

from xic_extractor.discovery.pt_pattern_evidence_rule import (
    PtPatternEvidenceThresholds,
    effective_max_abs_apex_delta_sec,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": "3",
        "max_abs_apex_delta_sec": "0.5",
        "min_pearson_r_to_base": "0.95",
        "min_active_overlap_fraction": "0.80",
        "min_shifted_pearson_r_to_base": "0.95",
        "max_abs_shape_shift_sec_to_base": "2.0",
    }
    row.update(overrides)
    return row


def test_pt_pattern_evidence_rule_accepts_full_supported_pattern() -> None:
    decision = evaluate_pt_pattern_evidence(_row())

    assert decision.call == "accept"
    assert decision.detection_status == "detected"
    assert decision.reason == "full_pattern_close_apex_shape_overlap"
    assert pt_pattern_rule_id() == (
        "pt_v2_fullpattern_status_pt_pattern_detected_diagnostic_positions_ge_3"
        "_apex_floor_le_0p6s_or_scan_multiplier_1p25_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )


def test_rule_id_changes_for_every_decision_affecting_threshold() -> None:
    baseline = pt_pattern_rule_id()

    assert (
        pt_pattern_rule_id(
            PtPatternEvidenceThresholds(min_non_base_positions_supported=2)
        )
        != baseline
    )
    assert (
        pt_pattern_rule_id(
            PtPatternEvidenceThresholds(
                required_combined_status="pt_pattern_candidate_partial"
            )
        )
        != baseline
    )
    assert (
        pt_pattern_rule_id(
            PtPatternEvidenceThresholds(max_abs_apex_delta_scan_step_multiplier=2.0)
        )
        != baseline
    )


def test_rule_id_distinguishes_close_decision_affecting_floats() -> None:
    lower = PtPatternEvidenceThresholds(min_pearson_r_to_base=0.9000001)
    higher = PtPatternEvidenceThresholds(min_pearson_r_to_base=0.9000002)
    row = _row(min_pearson_r_to_base="0.90000015")

    assert pt_pattern_rule_id(lower) != pt_pattern_rule_id(higher)
    assert evaluate_pt_pattern_evidence(row, thresholds=lower).call == "accept"
    assert evaluate_pt_pattern_evidence(row, thresholds=higher).call == "reject"


def test_pt_pattern_evidence_rule_accepts_one_scan_apex_jitter() -> None:
    row = _row(
        max_abs_apex_delta_sec="1.30413",
        min_median_rt_step_sec="1.31568667",
        min_effective_smoothing_window_sec="19.7353",
        trace_smoothing_window_points="15",
        min_pearson_r_to_base="0.982299",
        min_active_overlap_fraction="0.952381",
    )

    decision = evaluate_pt_pattern_evidence(row)

    assert effective_max_abs_apex_delta_sec(row) == pytest.approx(1.6446083)
    assert decision.call == "accept"
    assert decision.detection_status == "detected"
    assert decision.reason == "full_pattern_close_apex_shape_overlap"


def test_pt_pattern_evidence_rule_uses_observed_step_for_clipped_short_trace() -> None:
    row = _row(
        max_abs_apex_delta_sec="1.0",
        min_effective_smoothing_window_sec="6.0",
        trace_smoothing_window_points="15",
        pt_plus_1_median_rt_step_sec="1.2",
        pt_plus_2_median_rt_step_sec="1.2",
        pt_plus_4_median_rt_step_sec="1.2",
    )

    decision = evaluate_pt_pattern_evidence(row)

    assert effective_max_abs_apex_delta_sec(row) == pytest.approx(1.5)
    assert decision.call == "accept"


def test_pt_pattern_evidence_rule_rejects_conflicting_timing_provenance() -> None:
    row = _row(
        max_abs_apex_delta_sec="50",
        min_median_rt_step_sec="100",
        pt_plus_1_median_rt_step_sec="1",
        pt_plus_2_median_rt_step_sec="1",
        pt_plus_4_median_rt_step_sec="1",
    )

    with pytest.raises(ValueError, match="timing provenance"):
        evaluate_pt_pattern_evidence(row)


def test_pt_pattern_evidence_rule_validates_all_supplied_timing_fields() -> None:
    row = _row(
        min_median_rt_step_sec="1",
        pt_plus_1_median_rt_step_sec="garbage",
    )

    with pytest.raises(ValueError, match="pt_plus_1_median_rt_step_sec"):
        evaluate_pt_pattern_evidence(row)


def test_pt_pattern_evidence_rule_does_not_infer_step_from_configured_window() -> None:
    row = _row(
        max_abs_apex_delta_sec="1.0",
        min_effective_smoothing_window_sec="6.0",
        trace_smoothing_window_points="15",
    )

    decision = evaluate_pt_pattern_evidence(row)

    assert effective_max_abs_apex_delta_sec(row) == pytest.approx(0.6)
    assert decision.call == "reject"


def test_pt_pattern_evidence_rule_keeps_shifted_shape_review_only() -> None:
    decision = evaluate_pt_pattern_evidence(
        _row(
            max_abs_apex_delta_sec="30.0",
            min_pearson_r_to_base="0.10",
            min_shifted_pearson_r_to_base="0.95",
        )
    )

    assert decision.call == "reject"
    assert decision.detection_status == "non_detected"
    assert decision.reason == "shifted_shape_review_only"


def test_pt_pattern_evidence_rule_rejects_shifted_shape_above_hard_cap() -> None:
    decision = evaluate_pt_pattern_evidence(
        _row(
            max_abs_apex_delta_sec="75.0",
            min_pearson_r_to_base="0.10",
            min_shifted_pearson_r_to_base="0.98",
            max_abs_shape_shift_sec_to_base="75.0",
        )
    )

    assert decision.call == "reject"
    assert decision.detection_status == "non_detected"
    assert decision.reason == "shape_shift_above_threshold"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"combined_pt_pattern_status": "pt_pattern_candidate_partial"},
            "combined_status_not_detected",
        ),
        ({"min_pearson_r_to_base": ""}, "missing_required_metric"),
        ({"non_base_positions_supported": "2"}, "insufficient_non_base_positions"),
        (
            {
                "max_abs_apex_delta_sec": "0.7",
                "min_shifted_pearson_r_to_base": "0.10",
            },
            "apex_delta_above_threshold",
        ),
        (
            {
                "min_pearson_r_to_base": "0.80",
                "min_shifted_pearson_r_to_base": "0.10",
            },
            "pearson_below_threshold",
        ),
        (
            {
                "min_active_overlap_fraction": "0.25",
                "min_shifted_pearson_r_to_base": "0.10",
            },
            "overlap_below_threshold",
        ),
    ],
)
def test_pt_pattern_evidence_rule_rejects_expected_failure_modes(
    overrides: dict[str, object],
    reason: str,
) -> None:
    decision = evaluate_pt_pattern_evidence(_row(**overrides))

    assert decision.call == "reject"
    assert decision.detection_status == "non_detected"
    assert decision.reason == reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("non_base_positions_supported", "-1"),
        ("non_base_positions_supported", "3.5"),
        ("non_base_positions_supported", "4"),
        ("max_abs_apex_delta_sec", "-0.1"),
        ("min_pearson_r_to_base", "-1.1"),
        ("min_pearson_r_to_base", "1.1"),
        ("min_active_overlap_fraction", "-0.1"),
        ("min_active_overlap_fraction", "1.1"),
        ("min_shifted_pearson_r_to_base", "-1.1"),
        ("min_shifted_pearson_r_to_base", "1.1"),
        ("max_abs_shape_shift_sec_to_base", "-0.1"),
    ],
)
def test_pt_pattern_evidence_rule_rejects_illegal_metric_domains(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        evaluate_pt_pattern_evidence(_row(**{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_abs_apex_delta_sec", "garbage"),
        ("min_shifted_pearson_r_to_base", True),
        ("max_abs_shape_shift_sec_to_base", float("nan")),
        ("min_median_rt_step_sec", "garbage"),
        ("pt_plus_1_median_rt_step_sec", "0"),
    ],
)
def test_pt_pattern_evidence_rule_rejects_malformed_supplied_metrics(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        evaluate_pt_pattern_evidence(_row(**{field: value}))


def test_pt_pattern_evidence_rule_applies_custom_thresholds() -> None:
    thresholds = PtPatternEvidenceThresholds(
        max_abs_apex_delta_sec=2.0,
        min_pearson_r_to_base=0.99,
        min_active_overlap_fraction=0.90,
        min_shifted_pearson_r_to_base=0.98,
        max_abs_shape_shift_sec_to_base=12.0,
    )

    decision = evaluate_pt_pattern_evidence(
        _row(min_pearson_r_to_base="0.95"),
        thresholds=thresholds,
    )

    assert decision.reason == "pearson_below_threshold"
    assert pt_pattern_rule_id(thresholds) == (
        "pt_v2_fullpattern_status_pt_pattern_detected_diagnostic_positions_ge_3"
        "_apex_floor_le_2s_or_scan_multiplier_1p25_pearson_ge_0p99_overlap_ge_0p9"
        "_shiftedreviewonly_shiftedpearson_ge_0p98_shapeshift_le_12s"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required_combined_status": ""},
        {"required_combined_status": "   "},
        {"required_combined_status": "other_status"},
        {
            "required_combined_status": (
                " pt_pattern_detected_diagnostic "
            )
        },
        {"required_combined_status": True},
        {"min_non_base_positions_supported": 0},
        {"min_non_base_positions_supported": 4},
        {"max_abs_apex_delta_sec": 0.0},
        {"max_abs_apex_delta_scan_step_multiplier": 0.0},
        {"min_pearson_r_to_base": -0.1},
        {"min_pearson_r_to_base": 1.1},
        {"min_active_overlap_fraction": -0.1},
        {"min_active_overlap_fraction": 1.1},
        {"min_shifted_pearson_r_to_base": -0.1},
        {"min_shifted_pearson_r_to_base": 1.1},
        {"max_abs_shape_shift_sec_to_base": 0.0},
        {"max_abs_apex_delta_sec": float("nan")},
        {"max_abs_apex_delta_scan_step_multiplier": float("inf")},
        {"min_pearson_r_to_base": float("nan")},
        {"min_active_overlap_fraction": float("inf")},
        {"min_shifted_pearson_r_to_base": float("nan")},
        {"max_abs_shape_shift_sec_to_base": float("inf")},
        {"min_non_base_positions_supported": True},
        {"max_abs_apex_delta_sec": True},
        {"min_active_overlap_fraction": "0.5"},
    ],
)
def test_pt_pattern_evidence_thresholds_reject_invalid_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PtPatternEvidenceThresholds(**kwargs)
