from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

import xic_extractor.discovery.local_ms1_features as local_ms1_features
from xic_extractor.discovery.local_ms1_features import (
    LocalMs1FeatureSettings,
    _local_linear_baseline_area,
    _peak_regions_from_smoothed_trace,
    _validate_summary,
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"mass_detection_noise_level": float("nan")},
        {"feature_link_ppm": float("inf")},
        {"feature_link_ppm": 0.0},
        {"max_missing_scans": -1},
        {"min_observed_scans": 0},
        {"min_duration_min": -0.1},
        {"max_duration_min": 0.0},
        {"min_duration_min": 2.0, "max_duration_min": 1.0},
        {"gaussian_window_points": 0},
        {"max_ok_gap_fraction": float("nan")},
        {"max_weak_gap_fraction": 1.1},
        {"split_valley_fraction_of_smaller_apex": float("inf")},
        {"feature_link_ppm": True},
        {"feature_link_ppm": "20"},
        {"min_ok_prominence": 2.0, "weak_support_prominence": 3.0},
        {"max_ok_gap_fraction": 0.5, "max_weak_gap_fraction": 0.4},
        {"max_ok_mz_ppm_std": 20.0, "max_weak_mz_ppm_std": 10.0},
    ],
)
def test_local_ms1_feature_settings_reject_invalid_domain_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        LocalMs1FeatureSettings(**overrides)


def test_build_local_ms1_feature_table_rejects_empty_index() -> None:
    with pytest.raises(ValueError, match="at least one MS1 scan"):
        build_local_ms1_feature_table(
            (),
            sample_stem="Sample_1",
            raw_path="Sample_1.raw",
        )


def test_build_local_ms1_feature_table_uses_one_canonical_sample_identity() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0), (501.0000, 100.0)]),
        _scan(2, 1.05, [(500.0001, 50.0), (501.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0), (501.0000, 10.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem=" Sample_1 ",
        raw_path="Sample_1.raw",
        settings=_settings(min_ok_prominence=5.0),
    )

    assert summary.sample_stem == "Sample_1"
    assert features
    assert {feature.sample_stem for feature in features} == {"Sample_1"}
    assert all(feature.feature_id.startswith("Sample_1|") for feature in features)
    assert diagnostics
    assert all(row.trace_id.startswith("Sample_1|") for row in diagnostics)


@pytest.mark.parametrize("sample_stem", ["", "   "])
def test_build_local_ms1_feature_table_rejects_blank_sample_identity(
    sample_stem: str,
) -> None:
    with pytest.raises(ValueError, match="non-whitespace text"):
        build_local_ms1_feature_table(
            (_scan(1, 1.00, [(500.0000, 10.0)]),),
            sample_stem=sample_stem,
            raw_path="Sample_1.raw",
        )


def test_build_local_ms1_feature_table_emits_ok_and_weak_support_features() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0), (501.0000, 2.0)]),
        _scan(2, 1.05, [(500.0002, 40.0), (501.0002, 5.0)]),
        _scan(3, 1.10, [(500.0001, 100.0), (501.0001, 10.0)]),
        _scan(4, 1.15, [(500.0002, 45.0), (501.0002, 5.0)]),
        _scan(5, 1.20, [(500.0000, 10.0), (501.0000, 2.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
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
    assert summary.detected_features_rows == 2
    assert summary.ok_feature_count == 1
    assert summary.weak_support_feature_count == 1
    assert summary.fail_count == len(diagnostics)
    assert summary.detected_features_rows == (
        summary.ok_feature_count + summary.weak_support_feature_count
    )


def test_consecutive_intensity_gate_fails_closed_without_scan_positions() -> None:
    trace = local_ms1_features._TraceBuilder(
        trace_id=1,
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

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Seeded",
        raw_path="Seeded.raw",
        settings=_settings(
            mass_detection_noise_level=50.0,
            min_trace_seed_intensity=100.0,
            min_consecutive_trace_intensity=50.0,
            min_ok_prominence=10.0,
            weak_support_prominence=1.0,
            gaussian_window_points=1,
        ),
    )

    assert diagnostics == ()
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

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="StrictGap",
        raw_path="StrictGap.raw",
        settings=_settings(
            max_missing_scans=0,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=20.0,
            weak_support_prominence=1.0,
        ),
    )

    assert summary.detected_features_rows == 1
    assert features[0].rt_start_min == pytest.approx(index[3].rt)
    assert features[0].rt_end_min == pytest.approx(index[-1].rt)
    assert diagnostics[0].reason == "too_few_observed_scans"


def test_seeded_builder_uses_scan_order_for_low_intensity_bridge_points() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 100.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
        _scan(4, 1.15, [(500.0001, 90.0)]),
        _scan(5, 1.20, [(500.0000, 10.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Bridge",
        raw_path="Bridge.raw",
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

    assert summary.total_traces == 1
    assert summary.total_peak_regions == 2
    assert len(features) == 2
    assert diagnostics == ()


def test_local_ms1_feature_table_rejects_edge_apex_to_diagnostics() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 100.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        settings=_settings(),
    )

    assert features == ()
    assert summary.detected_features_rows == 0
    assert summary.fail_count == 1
    assert diagnostics[0].reason == "apex_at_region_edge"


def test_peak_regions_split_only_when_valley_is_low_enough() -> None:
    settings = _settings(min_observed_scans=3)
    scan_numbers = (1, 2, 3, 4, 5)
    rts = np.asarray([1.00, 1.05, 1.10, 1.15, 1.20], dtype=float)
    mzs = np.asarray([500.0, 500.0, 500.0, 500.0, 500.0], dtype=float)
    intensities = np.asarray([0.0, 10.0, 4.0, 9.0, 0.0], dtype=float)

    split_regions = _peak_regions_from_smoothed_trace(
        trace_id="Sample_1|trace|000001",
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=intensities,
        internal_missing_scan_count=0,
        settings=settings,
    )
    no_split_regions = _peak_regions_from_smoothed_trace(
        trace_id="Sample_1|trace|000001",
        scan_numbers=scan_numbers,
        rts=rts,
        mzs=mzs,
        intensities=intensities,
        smoothed=np.asarray([0.0, 10.0, 6.0, 9.0, 0.0], dtype=float),
        internal_missing_scan_count=0,
        settings=settings,
    )

    assert len(split_regions) == 2
    assert all(region.split_peak for region in split_regions)
    assert len(no_split_regions) == 1
    assert no_split_regions[0].split_peak is False


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

    features, summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Bounded",
        raw_path="Bounded.raw",
        settings=_settings(
            gaussian_window_points=1,
            min_ok_prominence=5.0,
            weak_support_prominence=1.0,
        ),
    )

    assert diagnostics == ()
    assert summary.detected_features_rows == 1
    assert features[0].rt_start_min == pytest.approx(index[1].rt)
    assert features[0].rt_end_min == pytest.approx(index[-2].rt)
    assert features[0].rt_start_min > index[0].rt
    assert features[0].rt_end_min < index[-1].rt


def test_missing_scan_tolerance_counts_internal_gap_not_trailing_gap() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, []),
        _scan(3, 1.10, [(500.0002, 50.0)]),
        _scan(4, 1.15, []),
    )

    _features, _summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Gap",
        raw_path="Gap.raw",
        settings=_settings(
            max_missing_scans=1,
            min_observed_scans=2,
            min_ok_prominence=999.0,
            weak_support_prominence=999.0,
        ),
    )

    assert diagnostics[0].missing_scan_count == 1
    assert diagnostics[0].gap_fraction == pytest.approx(1 / 3)


def test_local_linear_baseline_area_has_numeric_contract() -> None:
    rts = np.asarray([1.0, 2.0, 3.0], dtype=float)
    smoothed = np.asarray([10.0, 30.0, 10.0], dtype=float)

    assert _local_linear_baseline_area(rts, smoothed, 0, 2) == pytest.approx(1200.0)


def test_summary_invariant_failure_raises() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )
    _features, summary, _diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        settings=_settings(min_ok_prominence=5.0),
    )

    with pytest.raises(ValueError, match="summary count invariant"):
        _validate_summary(
            replace(
                summary,
                detected_features_rows=summary.detected_features_rows + 1,
            )
        )


@pytest.mark.parametrize(
    ("sample_stem", "raw_path", "message"),
    [
        ("   ", "Sample_1.raw", "sample_stem"),
        ("Sample_1", "   ", "raw_path"),
    ],
)
def test_build_local_ms1_feature_table_rejects_blank_identity_before_scan_io(
    sample_stem: str,
    raw_path: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        build_local_ms1_feature_table(
            (),
            sample_stem=sample_stem,
            raw_path=raw_path,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_file_size_bytes", True),
        ("raw_file_size_bytes", -1),
        ("raw_file_size_bytes", 1.5),
        ("raw_file_mtime_ns", True),
        ("raw_file_mtime_ns", -1),
        ("raw_file_mtime_ns", 1.5),
    ],
)
def test_build_local_ms1_feature_table_rejects_invalid_raw_provenance_before_scan_io(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        build_local_ms1_feature_table(
            (),
            sample_stem="Sample_1",
            raw_path="Sample_1.raw",
            **{field: value},
        )


def test_build_local_ms1_feature_table_normalizes_identity_whitespace() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    features, summary, _diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="  Sample_1  ",
        raw_path="  Sample_1.raw  ",
        settings=_settings(min_ok_prominence=5.0),
    )

    assert summary.sample_stem == "Sample_1"
    assert summary.raw_path == "Sample_1.raw"
    assert all(feature.sample_stem == "Sample_1" for feature in features)
    assert all(feature.feature_id.startswith("Sample_1|") for feature in features)


def test_build_local_ms1_feature_table_normalizes_integer_raw_provenance() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 10.0)]),
        _scan(2, 1.05, [(500.0001, 50.0)]),
        _scan(3, 1.10, [(500.0000, 10.0)]),
    )

    _features, summary, _diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="Sample_1",
        raw_path="Sample_1.raw",
        raw_file_size_bytes=np.int64(123),
        raw_file_mtime_ns=np.int64(456),
        settings=_settings(min_ok_prominence=5.0),
    )

    assert summary.raw_file_size_bytes == 123
    assert type(summary.raw_file_size_bytes) is int
    assert summary.raw_file_mtime_ns == 456
    assert type(summary.raw_file_mtime_ns) is int


def test_split_peak_child_regions_count_missing_gaps_independently() -> None:
    index = (
        _scan(1, 1.00, [(500.0000, 1.0)]),
        _scan(2, 1.05, [(500.0001, 10.0)]),
        _scan(3, 1.10, []),
        _scan(4, 1.15, [(500.0000, 2.0)]),
        _scan(5, 1.20, [(500.0001, 0.5)]),
        _scan(6, 1.25, [(500.0000, 9.0)]),
        _scan(7, 1.30, [(500.0001, 1.0)]),
    )

    features, _summary, diagnostics = build_local_ms1_feature_table(
        index,
        sample_stem="SplitGap",
        raw_path="SplitGap.raw",
        settings=_settings(
            max_missing_scans=1,
            min_observed_scans=3,
            gaussian_window_points=1,
            min_ok_prominence=999.0,
            weak_support_prominence=999.0,
        ),
    )

    assert features == ()
    assert len(diagnostics) == 2
    assert all(row.split_peak for row in diagnostics)
    assert diagnostics[0].missing_scan_count == 1
    assert diagnostics[0].gap_fraction == pytest.approx(1 / 5)
    assert diagnostics[1].missing_scan_count == 0
    assert diagnostics[1].gap_fraction == 0.0
