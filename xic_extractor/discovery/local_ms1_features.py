from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from xic_extractor.discovery.detected_features import (
    LOCAL_MS1_FEATURE_SOURCE,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    DetectedFeature,
)
from xic_extractor.mass_accuracy import mz_window, signed_ppm_errors
from xic_extractor.ms1_scan_index import MS1Scan, validate_ms1_scan_index
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
)


@dataclass(frozen=True)
class LocalMs1FeatureSettings:
    mass_detection_noise_level: float = 25_000.0
    min_trace_seed_intensity: float = 100_000.0
    min_consecutive_trace_intensity: float = 50_000.0
    feature_link_ppm: float = 20.0
    max_missing_scans: int = 1
    min_observed_scans: int = 3
    min_duration_min: float = 0.03
    max_duration_min: float = 1.5
    gaussian_window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS
    min_ok_prominence: float = 20.0
    weak_support_prominence: float = 3.0
    max_ok_gap_fraction: float = 0.20
    max_weak_gap_fraction: float = 0.35
    max_ok_mz_ppm_std: float = 10.0
    max_weak_mz_ppm_std: float = 20.0
    split_valley_fraction_of_smaller_apex: float = 0.50


@dataclass
class _TraceBuilder:
    points: list[tuple[int, float, float, float]]
    scan_positions: list[int] = field(default_factory=list)
    internal_missing_scan_count: int = 0
    internal_missing_gaps: list[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class _SeededMzRange:
    lower_mz: float
    upper_mz: float
    traces: list[_TraceBuilder]
    points: list[tuple[int, int, float, float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class _PeakRegion:
    scan_numbers: tuple[int, ...]
    rts: np.ndarray
    mzs: np.ndarray
    intensities: np.ndarray
    smoothed: np.ndarray
    apex_index: int
    left_index: int
    right_index: int
    missing_scan_count: int


def build_local_ms1_feature_table(
    index: tuple[MS1Scan, ...],
    *,
    raw_stem: str,
    settings: LocalMs1FeatureSettings | None = None,
) -> tuple[DetectedFeature, ...]:
    """Build chromatographic features from centroid ions, not profile samples."""
    raw_stem = raw_stem.strip()
    if not raw_stem:
        raise ValueError("sample_stem must contain non-whitespace text")
    resolved = settings or LocalMs1FeatureSettings()
    validate_ms1_scan_index(index, require_nonempty=True)
    traces = _build_seeded_traces(index, resolved)
    features: list[DetectedFeature] = []

    for trace in traces:
        if len(trace.points) < resolved.min_observed_scans:
            continue
        if not _trace_has_min_consecutive_intensity(trace, resolved):
            continue
        scan_numbers = tuple(point[0] for point in trace.points)
        rts = np.asarray([point[1] for point in trace.points], dtype=float)
        mzs = np.asarray([point[2] for point in trace.points], dtype=float)
        intensities = np.asarray([point[3] for point in trace.points], dtype=float)
        smoothed = gaussian15_morphology_trace(
            intensities,
            window_points=resolved.gaussian_window_points,
        )
        regions = _peak_regions_from_smoothed_trace(
            scan_numbers=scan_numbers,
            rts=rts,
            mzs=mzs,
            intensities=intensities,
            smoothed=smoothed,
            internal_missing_scan_count=trace.internal_missing_scan_count,
            internal_missing_gaps=tuple(trace.internal_missing_gaps),
            settings=resolved,
        )
        for region in regions:
            quality = _classify_region(region, resolved)
            if quality == "FAIL":
                continue
            feature_index = len(features) + 1
            feature_id = f"{raw_stem}|local_ms1_feature|{feature_index:06d}"
            features.append(
                DetectedFeature(
                    raw_stem=raw_stem,
                    feature_id=feature_id,
                    source_feature_row_id=None,
                    mz=float(region.mzs[region.apex_index]),
                    rt_apex_min=float(region.rts[region.apex_index]),
                    rt_start_min=float(region.rts[region.left_index]),
                    rt_end_min=float(region.rts[region.right_index]),
                    height=float(region.smoothed[region.apex_index]),
                    area=_local_linear_baseline_area(
                        region.rts,
                        region.smoothed,
                        region.left_index,
                        region.right_index,
                    ),
                    trace_quality=quality,
                    shape_score=None,
                    source=LOCAL_MS1_FEATURE_SOURCE,
                )
            )

    features.sort(
        key=lambda item: (
            item.raw_stem,
            item.rt_apex_min,
            item.mz,
            item.feature_id,
        )
    )
    sorted_features: list[DetectedFeature] = []
    for index_id, feature in enumerate(features, start=1):
        feature_id = f"{raw_stem}|local_ms1_feature|{index_id:06d}"
        sorted_features.append(
            DetectedFeature(
                raw_stem=feature.raw_stem,
                feature_id=feature_id,
                source_feature_row_id=None,
                mz=feature.mz,
                rt_apex_min=feature.rt_apex_min,
                rt_start_min=feature.rt_start_min,
                rt_end_min=feature.rt_end_min,
                height=feature.height,
                area=feature.area,
                trace_quality=feature.trace_quality,
                shape_score=feature.shape_score,
                source=feature.source,
            )
        )

    return tuple(sorted_features)


def _local_linear_baseline_area(
    rts: np.ndarray,
    smoothed: np.ndarray,
    left: int,
    right: int,
) -> float:
    if right <= left:
        return 0.0
    left_value = float(smoothed[left])
    right_value = float(smoothed[right])
    segment_rt = rts[left : right + 1]
    if len(segment_rt) < 2:
        return 0.0
    baseline = np.linspace(left_value, right_value, len(segment_rt))
    residual = np.maximum(smoothed[left : right + 1] - baseline, 0.0)
    return float(np.trapezoid(residual, segment_rt)) * 60.0


def _boundary_quality(
    smoothed: np.ndarray,
    apex_index: int,
    left: int,
    right: int,
) -> float:
    apex = float(smoothed[apex_index])
    if apex <= 0:
        return 0.0
    edge = max(float(smoothed[left]), float(smoothed[right]))
    return max(0.0, 1.0 - edge / apex)


def _prominence(smoothed: np.ndarray, apex_index: int, left: int, right: int) -> float:
    apex = float(smoothed[apex_index])
    edge = max(float(smoothed[left]), float(smoothed[right]))
    return max(0.0, apex - edge)


def _peak_regions_from_smoothed_trace(
    *,
    scan_numbers: tuple[int, ...],
    rts: np.ndarray,
    mzs: np.ndarray,
    intensities: np.ndarray,
    smoothed: np.ndarray,
    internal_missing_scan_count: int,
    internal_missing_gaps: Sequence[tuple[int, int]] = (),
    settings: LocalMs1FeatureSettings,
) -> tuple[_PeakRegion, ...]:
    if len(smoothed) < settings.min_observed_scans:
        return ()
    apices = _local_apex_indices(smoothed)
    if not apices:
        apices = (int(np.argmax(smoothed)),)
    boundaries = _split_boundaries(smoothed, apices, settings)
    regions: list[_PeakRegion] = []
    for left, apex_index, right in boundaries:
        if right - left + 1 < settings.min_observed_scans:
            continue
        regions.append(
            _PeakRegion(
                scan_numbers=scan_numbers[left : right + 1],
                rts=rts[left : right + 1],
                mzs=mzs[left : right + 1],
                intensities=intensities[left : right + 1],
                smoothed=smoothed[left : right + 1],
                apex_index=apex_index - left,
                left_index=0,
                right_index=right - left,
                missing_scan_count=_region_missing_scan_count(
                    internal_missing_scan_count=internal_missing_scan_count,
                    internal_missing_gaps=internal_missing_gaps,
                    left=left,
                    right=right,
                    trace_length=len(smoothed),
                ),
            )
        )
    return tuple(regions)


def _local_apex_indices(smoothed: np.ndarray) -> tuple[int, ...]:
    return tuple(
        index
        for index in range(1, len(smoothed) - 1)
        if (
            smoothed[index] >= smoothed[index - 1]
            and smoothed[index] > smoothed[index + 1]
        )
    )


def _split_boundaries(
    smoothed: np.ndarray,
    apices: tuple[int, ...],
    settings: LocalMs1FeatureSettings,
) -> tuple[tuple[int, int, int], ...]:
    if len(apices) <= 1:
        apex_index = apices[0]
        return (_bounded_apex_region(smoothed, apex_index),)

    boundaries: list[tuple[int, int, int]] = []
    left = _left_peak_boundary(smoothed, apices[0])
    current_apex = apices[0]
    for next_apex in apices[1:]:
        valley_index = current_apex + int(
            np.argmin(smoothed[current_apex : next_apex + 1])
        )
        valley = float(smoothed[valley_index])
        smaller_apex = min(float(smoothed[current_apex]), float(smoothed[next_apex]))
        should_split = (
            valley <= smaller_apex * settings.split_valley_fraction_of_smaller_apex
        )
        if should_split:
            boundaries.append((left, current_apex, valley_index))
            left = valley_index
            current_apex = next_apex
        elif smoothed[next_apex] > smoothed[current_apex]:
            current_apex = next_apex
    boundaries.append(
        (left, current_apex, _right_peak_boundary(smoothed, current_apex))
    )
    return tuple(boundaries)


def _bounded_apex_region(
    smoothed: np.ndarray,
    apex_index: int,
) -> tuple[int, int, int]:
    return (
        _left_peak_boundary(smoothed, apex_index),
        apex_index,
        _right_peak_boundary(smoothed, apex_index),
    )


def _left_peak_boundary(smoothed: np.ndarray, apex_index: int) -> int:
    apex = float(smoothed[apex_index])
    index = apex_index
    while (
        index > 0
        and math.isclose(float(smoothed[index - 1]), float(smoothed[index]))
        and math.isclose(float(smoothed[index]), apex)
    ):
        index -= 1
    while index > 0 and float(smoothed[index - 1]) < float(smoothed[index]):
        index -= 1
    return index


def _right_peak_boundary(smoothed: np.ndarray, apex_index: int) -> int:
    apex = float(smoothed[apex_index])
    index = apex_index
    last = len(smoothed) - 1
    while (
        index < last
        and math.isclose(float(smoothed[index + 1]), float(smoothed[index]))
        and math.isclose(float(smoothed[index]), apex)
    ):
        index += 1
    while index < last and float(smoothed[index + 1]) < float(smoothed[index]):
        index += 1
    return index


def _region_missing_scan_count(
    *,
    internal_missing_scan_count: int,
    internal_missing_gaps: Sequence[tuple[int, int]],
    left: int,
    right: int,
    trace_length: int,
) -> int:
    if not internal_missing_gaps:
        if left == 0 and right == trace_length - 1:
            return internal_missing_scan_count
        return 0
    return sum(
        count
        for gap_after_index, count in internal_missing_gaps
        if left <= gap_after_index < right
    )


def _build_seeded_traces(
    index: tuple[MS1Scan, ...],
    settings: LocalMs1FeatureSettings,
) -> list[_TraceBuilder]:
    """Build chromatogram traces from high-intensity m/z seeds.

    This follows the same shape as MZmine's chromatogram builder: start traces
    from sufficiently high points, let lower mass-detected points join existing
    non-overlapping m/z ranges, then leave chromatographic quality filters to
    the common local MS1 feature classifier.
    """

    data_points: list[tuple[float, float, int, int, float]] = []
    for scan_index, scan in enumerate(index):
        for mz, intensity in _mass_detected_scan_peaks(scan, settings):
            data_points.append(
                (
                    intensity,
                    mz,
                    scan_index,
                    int(scan.scan_number),
                    float(scan.rt),
                )
            )
    data_points.sort(key=lambda item: item[0], reverse=True)

    ranges: list[_SeededMzRange] = []
    range_lowers: list[float] = []

    for intensity, mz, _scan_index, _scan_number, _rt in data_points:
        if intensity < settings.min_trace_seed_intensity:
            break
        range_index = bisect_right(range_lowers, mz) - 1
        if (
            range_index >= 0
            and ranges[range_index].lower_mz <= mz < ranges[range_index].upper_mz
        ):
            continue

        lower_mz, upper_mz, insert_at = _seed_range(
            mz,
            ranges,
            range_lowers,
            settings,
        )
        if lower_mz >= upper_mz:
            continue

        ranges.insert(insert_at, _SeededMzRange(lower_mz, upper_mz, []))
        range_lowers.insert(insert_at, lower_mz)

    for intensity, mz, scan_index, scan_number, rt in data_points:
        range_index = bisect_right(range_lowers, mz) - 1
        if (
            range_index < 0
            or not ranges[range_index].lower_mz <= mz < ranges[range_index].upper_mz
        ):
            continue
        ranges[range_index].points.append((scan_index, scan_number, rt, mz, intensity))

    traces: list[_TraceBuilder] = []
    for seed_range in ranges:
        _build_seed_range_traces(seed_range, settings=settings)
        traces.extend(seed_range.traces)

    for trace in traces:
        _finalize_seeded_trace(trace)

    return traces


def _build_seed_range_traces(
    seed_range: _SeededMzRange,
    *,
    settings: LocalMs1FeatureSettings,
) -> None:
    point_by_scan: dict[int, tuple[int, float, float, float]] = {}
    for scan_index, scan_number, rt, mz, intensity in seed_range.points:
        current = point_by_scan.get(scan_index)
        if current is None or intensity > current[3]:
            point_by_scan[scan_index] = (scan_number, rt, mz, intensity)

    segment_positions: list[int] = []
    segment_points: list[tuple[int, float, float, float]] = []
    previous_scan_index: int | None = None

    for scan_index in sorted(point_by_scan):
        if (
            previous_scan_index is not None
            and scan_index - previous_scan_index - 1 > settings.max_missing_scans
        ):
            _append_seeded_trace_segment(
                seed_range,
                scan_positions=segment_positions,
                points=segment_points,
                settings=settings,
            )
            segment_positions = []
            segment_points = []
        segment_positions.append(scan_index)
        segment_points.append(point_by_scan[scan_index])
        previous_scan_index = scan_index

    _append_seeded_trace_segment(
        seed_range,
        scan_positions=segment_positions,
        points=segment_points,
        settings=settings,
    )


def _append_seeded_trace_segment(
    seed_range: _SeededMzRange,
    *,
    scan_positions: Sequence[int],
    points: Sequence[tuple[int, float, float, float]],
    settings: LocalMs1FeatureSettings,
) -> None:
    if not scan_positions:
        return
    if not any(point[3] >= settings.min_trace_seed_intensity for point in points):
        return
    seed_range.traces.append(
        _TraceBuilder(points=list(points), scan_positions=list(scan_positions))
    )


def _mass_detected_scan_peaks(
    scan: MS1Scan,
    settings: LocalMs1FeatureSettings,
) -> list[tuple[float, float]]:
    masses = np.asarray(scan.masses, dtype=np.float64)
    intensities = np.asarray(scan.intensities, dtype=np.float64)
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        return []
    mask = (
        np.isfinite(masses)
        & np.isfinite(intensities)
        & (intensities >= settings.mass_detection_noise_level)
    )
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    order = np.argsort(masses[indices])
    return [
        (float(masses[index]), float(intensities[index])) for index in indices[order]
    ]


def _seed_range(
    mz: float,
    ranges: Sequence[_SeededMzRange],
    range_lowers: Sequence[float],
    settings: LocalMs1FeatureSettings,
) -> tuple[float, float, int]:
    lower_mz, upper_mz = mz_window(mz, settings.feature_link_ppm)
    insert_at = bisect_left(range_lowers, lower_mz)
    if insert_at > 0 and ranges[insert_at - 1].upper_mz > lower_mz:
        lower_mz = ranges[insert_at - 1].upper_mz
    if insert_at < len(ranges) and ranges[insert_at].lower_mz < upper_mz:
        upper_mz = ranges[insert_at].lower_mz
    return lower_mz, upper_mz, insert_at


def _finalize_seeded_trace(trace: _TraceBuilder) -> None:
    if not trace.scan_positions:
        return
    ordered = sorted(
        zip(trace.scan_positions, trace.points, strict=True),
        key=lambda item: item[0],
    )
    trace.scan_positions = [scan_index for scan_index, _point in ordered]
    trace.points = [point for _scan_index, point in ordered]
    trace.internal_missing_gaps.clear()
    trace.internal_missing_scan_count = 0
    for point_index, (left, right) in enumerate(
        zip(trace.scan_positions, trace.scan_positions[1:], strict=False),
    ):
        gap = int(right) - int(left) - 1
        if gap <= 0:
            continue
        trace.internal_missing_gaps.append((point_index, gap))
        trace.internal_missing_scan_count += gap


def _trace_has_min_consecutive_intensity(
    trace: _TraceBuilder,
    settings: LocalMs1FeatureSettings,
) -> bool:
    threshold = settings.min_consecutive_trace_intensity
    if threshold <= 0:
        return True
    if not trace.scan_positions:
        return False
    consecutive = 0
    previous_position: int | None = None
    for scan_position, point in zip(trace.scan_positions, trace.points, strict=True):
        if previous_position is not None and scan_position != previous_position + 1:
            consecutive = 0
        if point[3] >= threshold:
            consecutive += 1
            if consecutive >= settings.min_observed_scans:
                return True
        else:
            consecutive = 0
        previous_position = scan_position
    return False


def _classify_region(
    region: _PeakRegion,
    settings: LocalMs1FeatureSettings,
) -> str:
    smoothed = region.smoothed
    apex = float(smoothed[region.apex_index])
    if apex <= 0:
        return "FAIL"
    if region.apex_index in {region.left_index, region.right_index}:
        return "FAIL"
    area = _local_linear_baseline_area(
        region.rts,
        smoothed,
        region.left_index,
        region.right_index,
    )
    if area <= 0:
        return "FAIL"
    duration = float(region.rts[region.right_index] - region.rts[region.left_index])
    if duration < settings.min_duration_min:
        return "FAIL"
    if duration > settings.max_duration_min:
        return "FAIL"
    gap_fraction = region.missing_scan_count / max(
        1,
        len(region.scan_numbers) + region.missing_scan_count,
    )
    ppm_std = _mz_ppm_std(region.mzs)
    prominence = _prominence(
        smoothed,
        region.apex_index,
        region.left_index,
        region.right_index,
    )
    boundary = _boundary_quality(
        smoothed,
        region.apex_index,
        region.left_index,
        region.right_index,
    )
    if (
        prominence >= settings.min_ok_prominence
        and gap_fraction <= settings.max_ok_gap_fraction
        and ppm_std <= settings.max_ok_mz_ppm_std
        and boundary > 0
    ):
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_OK
    if (
        prominence >= settings.weak_support_prominence
        and gap_fraction <= settings.max_weak_gap_fraction
        and ppm_std <= settings.max_weak_mz_ppm_std
    ):
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT
    return "FAIL"


def _mz_ppm_std(mzs: np.ndarray) -> float:
    mean_mz = float(np.mean(mzs))
    if mean_mz <= 0:
        return math.inf
    return float(np.std(signed_ppm_errors(mzs, mean_mz)))
