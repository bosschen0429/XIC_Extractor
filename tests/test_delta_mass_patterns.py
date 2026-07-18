"""Contract tests for Pt delta-mass pattern matching (Task 1 RED phase).

These tests describe the required behavior of
``xic_extractor.discovery.delta_mass_patterns.match_delta_mass_pattern`` and the
``PT_PATTERN`` / ``DeltaMassPatternSettings`` / ``DetectedFeature`` contracts
from ``xic_extractor.discovery.detected_features`` before those modules exist.
They are expected to fail on import until Task 2 implements the real modules.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from xic_extractor.discovery.delta_mass_patterns import (
    PT_PATTERN,
    is_within_ppm,
    match_delta_mass_pattern,
)
from xic_extractor.discovery.detected_features import (
    DeltaMassPatternSettings,
    DetectedFeature,
)

BASE_MZ = 500.0
PT_PLUS_1_DELTA = 1.002111
PT_PLUS_2_DELTA = 2.002271
PT_PLUS_4_DELTA = 4.005213


def _feature(
    *,
    feature_id: str,
    mz: float,
    rt_apex_min: float,
    height: float = 1000.0,
    area: float | None = 500.0,
    trace_quality: str = "GOOD",
    shape_score: float | None = 0.90,
    sample_stem: str = "Sample_1",
    source_feature_row_id: str | None = None,
    source: str = "targeted",
) -> DetectedFeature:
    return DetectedFeature(
        sample_stem=sample_stem,
        feature_id=feature_id,
        source_feature_row_id=source_feature_row_id,
        mz=mz,
        rt_apex_min=rt_apex_min,
        rt_start_min=rt_apex_min - 0.05,
        rt_end_min=rt_apex_min + 0.05,
        height=height,
        area=area,
        trace_quality=trace_quality,
        shape_score=shape_score,
        source=source,
    )


def _features_with_pt_plus_1(plus_one_mz: float) -> list[DetectedFeature]:
    return [
        _feature(feature_id="base", mz=BASE_MZ, rt_apex_min=10.00),
        _feature(feature_id="plus1", mz=plus_one_mz, rt_apex_min=10.00),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.00,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.00,
        ),
    ]


def test_matcher_pt_plus_one_window_uses_expected_mz_denominator() -> None:
    base_mz = 500.0
    expected_mz = base_mz + PT_PLUS_1_DELTA
    inside = expected_mz + expected_mz * 20e-6
    outside = expected_mz + expected_mz * 20.1e-6

    assert len(match_delta_mass_pattern(_features_with_pt_plus_1(inside))) == 1
    assert len(match_delta_mass_pattern(_features_with_pt_plus_1(outside))) == 0


def test_is_within_ppm_helper_is_supporting_coverage_only() -> None:
    expected_mz = BASE_MZ + PT_PLUS_1_DELTA
    inside_mz = expected_mz + expected_mz * 20e-6
    outside_mz = expected_mz + expected_mz * 20.1e-6

    assert is_within_ppm(inside_mz, expected_mz, tolerance_ppm=20.0) is True
    assert is_within_ppm(outside_mz, expected_mz, tolerance_ppm=20.0) is False


def test_all_four_positions_within_tolerance_and_rt_span_produce_candidate() -> None:
    features = [
        _feature(feature_id="base", mz=BASE_MZ, rt_apex_min=10.000),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.001,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.002,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.003,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1


def test_exact_pt_deltas_recover_high_mz_boundary_candidate() -> None:
    """Regression for high-m/z cases lost by rounded Pt delta constants."""

    features = [
        _feature(
            feature_id="base_1612p6",
            mz=1612.6,
            rt_apex_min=14.6627,
            height=318154.0,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1_1613p6",
            mz=1613.6,
            rt_apex_min=14.7060,
            height=180417.0,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2_1614p57",
            mz=1614.57,
            rt_apex_min=14.6407,
            height=209912.0,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4_1616p58",
            mz=1616.58,
            rt_apex_min=14.7498,
            height=126864.0,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.base_feature_id == "base_1612p6"
    assert candidate.pt_plus_2_mz_error_ppm == pytest.approx(-19.986966)


def test_scan_proxy_candidates_use_only_same_group_base_anchor() -> None:
    shifted_base_mz = BASE_MZ + PT_PLUS_1_DELTA
    features = [
        _feature(
            feature_id="Sample_1|scan_proxy|group1|base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        _feature(
            feature_id="Sample_1|scan_proxy|group1|pt_plus_1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        _feature(
            feature_id="Sample_1|scan_proxy|group1|pt_plus_2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        _feature(
            feature_id="Sample_1|scan_proxy|group1|pt_plus_4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        # Without scan-proxy group/label constraints, group1|pt_plus_1 can be
        # incorrectly treated as a shifted base and mixed with these decoys.
        _feature(
            feature_id="Sample_1|scan_proxy|group2|pt_plus_1",
            mz=shifted_base_mz + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        _feature(
            feature_id="Sample_1|scan_proxy|group2|pt_plus_2",
            mz=shifted_base_mz + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
        _feature(
            feature_id="Sample_1|scan_proxy|group2|pt_plus_4",
            mz=shifted_base_mz + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="raw_scan_proxy_v1",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.base_feature_id == "Sample_1|scan_proxy|group1|base"
    assert candidate.pt_plus_1_feature_id == "Sample_1|scan_proxy|group1|pt_plus_1"
    assert candidate.pt_plus_2_feature_id == "Sample_1|scan_proxy|group1|pt_plus_2"
    assert candidate.pt_plus_4_feature_id == "Sample_1|scan_proxy|group1|pt_plus_4"


def test_missing_plus_4_position_yields_no_candidate() -> None:
    features = [
        _feature(feature_id="base", mz=BASE_MZ, rt_apex_min=10.000),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.001,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.002,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 0


def test_rt_span_over_30_sec_yields_no_candidate() -> None:
    # 31 seconds = 31/60 min beyond the base RT.
    over_ceiling_min = 31.0 / 60.0
    features = [
        _feature(feature_id="base", mz=BASE_MZ, rt_apex_min=10.000),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000 + over_ceiling_min,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 0


def test_missing_local_signal_support_yields_no_candidate() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="FAIL",
            shape_score=0.10,
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="FAIL",
            shape_score=0.10,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="FAIL",
            shape_score=0.10,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="FAIL",
            shape_score=0.10,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 0


def test_external_feature_source_without_trace_label_can_seed_candidate() -> None:
    source = "external_feature_table:MZmine:mzmine_wide_area_proxy_v1"
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="UNSPECIFIED",
            shape_score=None,
            source=source,
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="UNSPECIFIED",
            shape_score=None,
            source=source,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="UNSPECIFIED",
            shape_score=None,
            source=source,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="UNSPECIFIED",
            shape_score=None,
            source=source,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].base_feature_id == "base"
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"
    assert candidates[0].evidence_score == 70


def test_local_ms1_feature_all_ok_pattern_stays_feature_source_detected() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"
    assert candidates[0].evidence_score == 70


def test_local_ms1_feature_weak_support_can_match_non_base_only() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"


def test_local_ms1_feature_weak_base_is_rejected_even_with_shape_score() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=None,
            source="local_ms1_feature",
        ),
    ]

    assert match_delta_mass_pattern(features) == ()


def test_local_ms1_feature_unknown_quality_bypassing_loader_is_fail_closed() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="GOOD",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
    ]

    with pytest.raises(ValueError, match="trace_quality"):
        match_delta_mass_pattern(features)


def test_local_ms1_feature_shape_score_cannot_upgrade_tier() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    assert candidates[0].local_signal_support_status == "feature_source_detected"
    assert candidates[0].evidence_tier == "B"
    assert candidates[0].evidence_score == 70


def test_non_local_base_cannot_use_local_ms1_feature_support() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            trace_quality="GOOD",
            shape_score=0.99,
            source="targeted",
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            trace_quality="WEAK_SUPPORT",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            trace_quality="OK",
            shape_score=0.99,
            source="local_ms1_feature",
        ),
    ]

    assert match_delta_mass_pattern(features) == ()


def test_both_height_and_area_produce_both_ratio_kinds() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            height=1000.0,
            area=500.0,
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            height=800.0,
            area=400.0,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            height=200.0,
            area=100.0,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            height=50.0,
            area=25.0,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pt_plus_1_height_ratio_to_base == 0.8
    assert candidate.pt_plus_1_area_ratio_to_base == 0.8
    assert candidate.pt_plus_2_height_ratio_to_base == 0.2
    assert candidate.pt_plus_2_area_ratio_to_base == 0.2


def test_blank_area_yields_height_ratio_and_blank_area_ratio_without_failing() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            height=1000.0,
            area=None,
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            height=800.0,
            area=None,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            height=200.0,
            area=None,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            height=50.0,
            area=None,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pt_plus_1_height_ratio_to_base == 0.8
    assert candidate.pt_plus_1_area_ratio_to_base is None
    assert candidate.pt_plus_2_height_ratio_to_base == 0.2
    assert candidate.pt_plus_2_area_ratio_to_base is None


def test_zero_base_area_yields_blank_area_ratios_without_failing() -> None:
    features = [
        _feature(
            feature_id="base",
            mz=BASE_MZ,
            rt_apex_min=10.000,
            height=1000.0,
            area=0.0,
        ),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.000,
            height=800.0,
            area=400.0,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.000,
            height=200.0,
            area=100.0,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.000,
            height=50.0,
            area=25.0,
        ),
    ]

    candidates = match_delta_mass_pattern(features)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pt_plus_1_height_ratio_to_base == 0.8
    assert candidate.pt_plus_1_area_ratio_to_base is None
    assert candidate.pt_plus_2_area_ratio_to_base is None
    assert candidate.pt_plus_4_area_ratio_to_base is None


def test_candidate_provenance_reflects_custom_rt_tolerance() -> None:
    settings = DeltaMassPatternSettings(rt_tolerance_sec=45.0, ratio_basis="both")
    features = [
        _feature(feature_id="base", mz=BASE_MZ, rt_apex_min=10.000),
        _feature(
            feature_id="plus1",
            mz=BASE_MZ + PT_PLUS_1_DELTA,
            rt_apex_min=10.650,
        ),
        _feature(
            feature_id="plus2",
            mz=BASE_MZ + PT_PLUS_2_DELTA,
            rt_apex_min=10.640,
        ),
        _feature(
            feature_id="plus4",
            mz=BASE_MZ + PT_PLUS_4_DELTA,
            rt_apex_min=10.630,
        ),
    ]

    candidates = match_delta_mass_pattern(features, settings=settings)

    assert len(candidates) == 1
    assert candidates[0].rt_span_sec == pytest.approx(39.0)
    assert candidates[0].pattern_rt_status == "within_45s"
    assert candidates[0].ratio_basis == "both"


def test_matcher_rejects_unimplemented_direct_ratio_basis() -> None:
    with pytest.raises(ValueError, match="ratio_basis"):
        match_delta_mass_pattern(
            _features_with_pt_plus_1(BASE_MZ + PT_PLUS_1_DELTA),
            settings=DeltaMassPatternSettings(ratio_basis="height_only"),
        )


def test_matcher_rejects_duplicate_sample_local_feature_identity_before_id_collision(
) -> None:
    features = _features_with_pt_plus_1(BASE_MZ + PT_PLUS_1_DELTA)
    features.append(
        _feature(
            feature_id="base",
            mz=BASE_MZ + 0.0000004,
            rt_apex_min=10.0000004,
        )
    )

    with pytest.raises(ValueError, match="duplicate detected feature identity"):
        match_delta_mass_pattern(features)


def test_matcher_rejects_non_pt_pattern_until_candidate_model_is_generic() -> None:
    custom_pattern = replace(PT_PATTERN, pattern_id="custom")

    with pytest.raises(ValueError, match="only the built-in PT_PATTERN"):
        match_delta_mass_pattern(
            _features_with_pt_plus_1(BASE_MZ + PT_PLUS_1_DELTA),
            settings=DeltaMassPatternSettings(
                pattern=custom_pattern,
                pattern_id="custom",
            ),
        )


def test_pt_pattern_constant_defines_expected_positions_and_tolerances() -> None:
    assert PT_PATTERN.pattern_id == "pt"
    assert PT_PATTERN.charge == 1
    assert PT_PATTERN.polarity == "positive"
    assert PT_PATTERN.mz_tolerance_ppm == 20.0
    assert PT_PATTERN.rt_tolerance_sec == 30.0
    position_deltas = {
        position.label: position.delta_da for position in PT_PATTERN.positions
    }
    assert position_deltas == {
        "base": 0.0,
        "pt_plus_1": 1.002111,
        "pt_plus_2": 2.002271,
        "pt_plus_4": 4.005213,
    }


def test_delta_mass_pattern_settings_default_to_pt_pattern() -> None:
    settings = DeltaMassPatternSettings()

    assert settings.pattern is PT_PATTERN
    assert settings.mz_tolerance_ppm == 20.0
    assert settings.rt_tolerance_sec == 30.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"pattern_id": ""},
        {"method_label": ""},
        {"mz_tolerance_ppm": 0.0},
        {"mz_tolerance_ppm": float("nan")},
        {"rt_tolerance_sec": float("inf")},
        {"mz_tolerance_ppm": True},
        {"rt_tolerance_sec": "30"},
    ],
)
def test_delta_mass_pattern_settings_reject_invalid_domain_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        DeltaMassPatternSettings(**overrides)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mz", float("nan")),
        ("rt_apex_min", float("inf")),
        ("height", True),
        ("area", float("nan")),
    ],
)
def test_matcher_rejects_invalid_direct_detected_features(
    field: str,
    value: object,
) -> None:
    features = _features_with_pt_plus_1(BASE_MZ + PT_PLUS_1_DELTA)
    features[0] = replace(features[0], **{field: value})

    with pytest.raises(ValueError, match=field):
        match_delta_mass_pattern(features)
