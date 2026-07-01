"""Contract tests for Pt delta-mass pattern matching (Task 1 RED phase).

These tests describe the required behavior of
``xic_extractor.discovery.delta_mass_patterns.match_delta_mass_pattern`` and the
``PT_PATTERN`` / ``DeltaMassPatternSettings`` / ``DetectedFeature`` contracts
from ``xic_extractor.discovery.detected_features`` before those modules exist.
They are expected to fail on import until Task 2 implements the real modules.
"""

from __future__ import annotations

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
PT_PLUS_1_DELTA = 1.0021
PT_PLUS_2_DELTA = 2.0023
PT_PLUS_4_DELTA = 4.0052


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
    expected_mz = base_mz + 1.0021
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
        "pt_plus_1": 1.0021,
        "pt_plus_2": 2.0023,
        "pt_plus_4": 4.0052,
    }


def test_delta_mass_pattern_settings_default_to_pt_pattern() -> None:
    settings = DeltaMassPatternSettings()

    assert settings.pattern is PT_PATTERN
    assert settings.mz_tolerance_ppm == 20.0
    assert settings.rt_tolerance_sec == 30.0
