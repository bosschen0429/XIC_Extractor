from __future__ import annotations

import numpy as np
import pytest

import xic_extractor.discovery.local_ms1_features as local_ms1_features
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    _local_linear_baseline_area,
    _peak_regions_from_smoothed_trace,
    build_local_ms1_feature_table,
)
from xic_extractor.ms1_scan_index import MS1Scan


def _scan(scan_number: int, rt: float, peaks: list[tuple[float, float]]) -> MS1Scan:
    return MS1Scan(
        scan_number=scan_number,
        rt=rt,
        masses=np.asarray([mz for mz, _intensity in peaks], dtype=np.float64),
        intensities=np.asarray(
            [intensity for _mz, intensity in peaks],
            dtype=np.float64,
        ),
    )


def _settings(**overrides: object) -> LocalMs1FeatureSettings:
    values = {
        "mass_detection_noise_level": 0.0,
        "min_trace_seed_intensity": 0.0,
        "min_consecutive_trace_intensity": 0.0,
    }
    values.update(overrides)
    return LocalMs1FeatureSettings(**values)


def test_local_ms1_feature_settings_use_lab_default_noise_level() -> None:
    assert LocalMs1FeatureSettings().mass_detection_noise_level == 25_000.0


def test_build_local_ms1_feature_table_rejects_empty_index() -> None:
    with pytest.raises(ValueError, match="at least one MS1 scan"):
        build_local_ms1_feature_table(
            (),
            raw_stem="Sample_1",
        )


@pytest.mark.parametrize("raw_stem", ["", "   "])
def test_build_local_ms1_feature_table_rejects_blank_sample_identity(
    raw_stem: str,
) -> None:
    with pytest.raises(ValueError, match="non-whitespace text"):
        build_local_ms1_feature_table(
            (_scan(1, 1.00, [(500.0000, 10.0)]),),
            raw_stem=raw_stem,
        )


def test_build_local_ms1_feature_table_emits_ok_and_weak_support_features() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0), (501.0000, 2.0)]),
        _scan(2, 1.05, [(500.0002, 40.0), (501.0002, 5.0)]),
        _scan(3, 1.10, [(500.0001, 100.0), (501.0001, 10.0)]),
        _scan(4, 1.15, [(500.0002, 45.0), (501.0002, 5.0)]),
        _scan(5, 1.20, [(500.0000, 10.0), (501.0000, 2.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Sample_1",
        settings=_settings(
            min_ok_prominence=20.0,
            weak_support_prominence=3.0,
        ),
    )

    assert [feature.trace_quality for feature in features] == ["OK", "WEAK_SUPPORT"]
    assert features[0].feature_id == "Sample_1|local_ms1_feature|000001"
    assert features[0].source_feature_row_id is None
    assert features[0].source == "local_ms1_feature"
    assert features[0].height > 0
    assert features[0].area is not None and features[0].area > 0


def test_consecutive_intensity_gate_fails_closed_without_scan_positions() -> None:
    trace = local_ms1_features._TraceBuilder(
        points=[
            (1, 1.00, 500.0, 100.0),
            (2, 1.05, 500.0, 100.0),
            (3, 1.10, 500.0, 100.0),
        ],
    )

    assert not local_ms1_features._trace_has_min_consecutive_intensity(
        trace,
        _settings(
            min_observed_scans=3,
            min_consecutive_trace_intensity=50.0,
        ),
    )


def test_seeded_builder_requires_high_intensity_seed_before_low_points_join() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 60.0), (501.0000, 80.0)]),
        _scan(2, 1.05, [(500.0001, 150.0), (501.0001, 90.0)]),
        _scan(3, 1.10, [(500.0000, 60.0), (501.0000, 80.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Seeded",
        settings=_settings(
            mass_detection_noise_level=50.0,
            min_trace_seed_intensity=100.0,
            min_consecutive_trace_intensity=50.0,
            min_ok_prominence=10.0,
            weak_support_prominence=1.0,
            gaussian_window_points=1,
        ),
    )

    assert len(features) == 1
    assert features[0].mz == pytest.approx(500.0001)


def test_seeded_builder_respects_max_missing_scans_when_reusing_seed_range() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 40.0)]),
        _scan(3, 1.10, []),
        _scan(4, 1.15, [(500.0000, 45.0)]),
        _scan(5, 1.20, [(500.0001, 100.0)]),
        _scan(6, 1.25, [(500.0000, 45.0)]),
        _scan(7, 1.30, [(500.0001, 10.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="StrictGap",
        settings=_settings(
            max_missing_scans=0,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=20.0,
            weak_support_prominence=1.0,
        ),
    )

    assert len(features) == 1
    assert features[0].rt_start_min == pytest.approx(index[3].rt)
    assert features[0].rt_end_min == pytest.approx(index[-1].rt)


def test_seeded_builder_uses_scan_order_for_low_intensity_bridge_points() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 100.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
        _scan(4, 1.15, [(500.0001, 90.0)]),
        _scan(5, 1.20, [(500.0000, 10.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Bridge",
        settings=_settings(
            mass_detection_noise_level=1.0,
            min_trace_seed_intensity=50.0,
            max_missing_scans=0,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=5.0,
            weak_support_prominence=1.0,
        ),
    )

    assert len(features) == 2


def test_local_ms1_feature_table_rejects_edge_apex() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 100.0)]),
        _scan(2, 1.05, [(500.0001, 90.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Sample_1",
        settings=_settings(
            gaussian_window_points=1,
            min_ok_prominence=0.0,
            weak_support_prominence=0.0,
        ),
    )

    assert features == ()


def test_peak_regions_split_only_when_valley_is_low_enough() -> None:
    settings = _settings(min_observed_scans=3)
    scan_numbers = (1, 2, 3, 4, 5)
    rts = np.asarray([1.00, 1.05, 1.10, 1.15, 1.20], dtype=float)
    mzs = np.asarray([500.0, 500.0, 500.0, 500.0, 500.0], dtype=float)
    intensities = np.asarray([0.0, 10.0, 4.0, 9.0, 0.0], dtype=float)

    split_regions = _peak_regions_from_smoothed_trace(
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=intensities,
        internal_missing_scan_count=0,
        settings=settings,
    )
    no_split_regions = _peak_regions_from_smoothed_trace(
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=np.asarray([0.0, 10.0, 6.0, 9.0, 0.0], dtype=float),
        internal_missing_scan_count=0,
        settings=settings,
    )

    assert len(split_regions) == 2
    assert len(no_split_regions) == 1


def test_single_apex_peak_region_uses_nearest_tail_valleys_not_trace_edges() -> None:
    intensities = [0.1, 0.1, 1.0, 10.0, 100.0, 10.0, 1.0, 0.1, 0.1]
    index = tuple(
        _scan(
            scan_number,
            1.00 + (scan_number - 1) * 0.05,
            [(500.0, intensity)],
        )
        for scan_number, intensity in enumerate(intensities, start=1)
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Bounded",
        settings=_settings(
            gaussian_window_points=1,
            min_ok_prominence=5.0,
            weak_support_prominence=1.0,
        ),
    )

    assert len(features) == 1
    assert features[0].rt_start_min == pytest.approx(index[1].rt)
    assert features[0].rt_end_min == pytest.approx(index[-2].rt)
    assert features[0].rt_start_min > index[0].rt
    assert features[0].rt_end_min < index[-1].rt


def test_local_linear_baseline_area_has_numeric_contract() -> None:
    rts = np.asarray([1.0, 2.0, 3.0], dtype=float)
    smoothed = np.asarray([10.0, 30.0, 10.0], dtype=float)

    assert _local_linear_baseline_area(rts, smoothed, 0, 2) == pytest.approx(1200.0)


def test_build_local_ms1_feature_table_normalizes_identity_whitespace() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="  Sample_1  ",
        settings=_settings(min_ok_prominence=5.0),
    )

    assert features
    assert all(feature.raw_stem == "Sample_1" for feature in features)
    assert all(feature.feature_id.startswith("Sample_1|") for feature in features)


@pytest.mark.parametrize(
    ("max_weak_gap_fraction", "expected_rows"),
    [(0.30, 1), (0.20, 0)],
)
def test_gap_fraction_counts_internal_gap_not_trailing_gap(
    max_weak_gap_fraction: float,
    expected_rows: int,
) -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, []),
        _scan(3, 1.10, [(500.0002, 50.0)]),
        _scan(4, 1.15, [(500.0001, 10.0)]),
        _scan(5, 1.20, []),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="Gap",
        settings=_settings(
            max_missing_scans=1,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=999.0,
            weak_support_prominence=1.0,
            max_ok_gap_fraction=0.0,
            max_weak_gap_fraction=max_weak_gap_fraction,
        ),
    )

    assert len(features) == expected_rows


@pytest.mark.parametrize(
    ("max_weak_gap_fraction", "expected_apex_rts"),
    [(0.20, [1.05, 1.25]), (0.10, [1.25])],
)
def test_split_peak_child_regions_count_missing_gaps_independently(
    max_weak_gap_fraction: float,
    expected_apex_rts: list[float],
) -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 1.0)]),
        _scan(2, 1.05, [(500.0001, 10.0)]),
        _scan(3, 1.10, []),
        _scan(4, 1.15, [(500.0000, 2.0)]),
        _scan(5, 1.20, [(500.0001, 0.5)]),
        _scan(6, 1.25, [(500.0000, 9.0)]),
        _scan(7, 1.30, [(500.0001, 1.0)]),
    )

    features = build_local_ms1_feature_table(
        index,
        raw_stem="SplitGap",
        settings=_settings(
            max_missing_scans=1,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=999.0,
            weak_support_prominence=1.0,
            max_ok_gap_fraction=0.0,
            max_weak_gap_fraction=max_weak_gap_fraction,
        ),
    )

    assert sorted(feature.rt_apex_min for feature in features) == pytest.approx(
        expected_apex_rts
    )
