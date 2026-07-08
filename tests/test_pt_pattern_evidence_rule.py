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
        "pt_v2_fullpattern_apex_le_0p6s_or_1p25scan_pearson_ge_0p9_overlap_ge_0p5"
        "_shiftedreviewonly_shiftedpearson_ge_0p85_shapeshift_le_45s"
    )


def test_pt_pattern_evidence_rule_accepts_one_scan_apex_jitter() -> None:
    row = _row(
        max_abs_apex_delta_sec="1.30413",
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
        "pt_v2_fullpattern_apex_le_2s_or_1p25scan_pearson_ge_0p99_overlap_ge_0p9"
        "_shiftedreviewonly_shiftedpearson_ge_0p98_shapeshift_le_12s"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required_combined_status": ""},
        {"min_non_base_positions_supported": 0},
        {"max_abs_apex_delta_sec": 0.0},
        {"max_abs_apex_delta_scan_step_multiplier": 0.0},
        {"min_pearson_r_to_base": -0.1},
        {"min_pearson_r_to_base": 1.1},
        {"min_active_overlap_fraction": -0.1},
        {"min_active_overlap_fraction": 1.1},
        {"min_shifted_pearson_r_to_base": -0.1},
        {"min_shifted_pearson_r_to_base": 1.1},
        {"max_abs_shape_shift_sec_to_base": 0.0},
    ],
)
def test_pt_pattern_evidence_thresholds_reject_invalid_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PtPatternEvidenceThresholds(**kwargs)
