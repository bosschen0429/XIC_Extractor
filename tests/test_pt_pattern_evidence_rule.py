from __future__ import annotations

import pytest

from xic_extractor.discovery.pt_pattern_evidence_rule import (
    PtPatternEvidenceThresholds,
    evaluate_pt_pattern_evidence,
    pt_pattern_rule_id,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "combined_pt_pattern_status": "pt_pattern_detected_diagnostic",
        "non_base_positions_supported": "3",
        "max_abs_apex_delta_sec": "1.0",
        "min_pearson_r_to_base": "0.95",
        "min_active_overlap_fraction": "0.80",
    }
    row.update(overrides)
    return row


def test_pt_pattern_evidence_rule_accepts_full_supported_pattern() -> None:
    decision = evaluate_pt_pattern_evidence(_row())

    assert decision.call == "accept"
    assert decision.detection_status == "detected"
    assert decision.reason == "full_pattern_close_apex_shape_overlap"
    assert pt_pattern_rule_id() == (
        "pt_v2_fullpattern_apex_le_10s_pearson_ge_0p9_overlap_ge_0p5"
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"combined_pt_pattern_status": "pt_pattern_candidate_partial"},
            "combined_status_not_detected",
        ),
        ({"min_pearson_r_to_base": ""}, "missing_required_metric"),
        ({"non_base_positions_supported": "2"}, "insufficient_non_base_positions"),
        ({"max_abs_apex_delta_sec": "12.0"}, "apex_delta_above_threshold"),
        ({"min_pearson_r_to_base": "0.80"}, "pearson_below_threshold"),
        ({"min_active_overlap_fraction": "0.25"}, "overlap_below_threshold"),
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
    )

    decision = evaluate_pt_pattern_evidence(
        _row(min_pearson_r_to_base="0.95"),
        thresholds=thresholds,
    )

    assert decision.reason == "pearson_below_threshold"
    assert pt_pattern_rule_id(thresholds) == (
        "pt_v2_fullpattern_apex_le_2s_pearson_ge_0p99_overlap_ge_0p9"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"required_combined_status": ""},
        {"min_non_base_positions_supported": 0},
        {"max_abs_apex_delta_sec": 0.0},
        {"min_pearson_r_to_base": -0.1},
        {"min_pearson_r_to_base": 1.1},
        {"min_active_overlap_fraction": -0.1},
        {"min_active_overlap_fraction": 1.1},
    ],
)
def test_pt_pattern_evidence_thresholds_reject_invalid_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        PtPatternEvidenceThresholds(**kwargs)
