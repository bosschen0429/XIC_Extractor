from __future__ import annotations

import hashlib
import json
import math
import time
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from xic_extractor.discovery.detected_features import (
    LOCAL_MS1_FEATURE_SOURCE,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_OK,
    LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT,
    DetectedFeature,
    write_detected_features_csv,
)
from xic_extractor.ms1_scan_index import MS1Scan, validate_ms1_scan_index
from xic_extractor.numeric_validation import require_finite_real, require_integer
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
    morphology_trace_effective_points,
)
from xic_extractor.tabular_io import write_delimited_rows

SUMMARY_SCHEMA_VERSION = "local_ms1_feature_summary.v1"
DIAGNOSTICS_COLUMNS = (
    "trace_id",
    "mz_apex",
    "rt_apex_min",
    "rt_start_min",
    "rt_end_min",
    "observed_scan_count",
    "missing_scan_count",
    "gap_fraction",
    "mz_ppm_std",
    "prominence",
    "boundary_quality",
    "assignment_mode",
    "component_size",
    "component_edge_density",
    "assignment_degraded_large_component",
    "split_peak",
    "overlap_suspected",
    "reason",
)


@dataclass(frozen=True)
class LocalMs1FeatureSettings:
    trace_builder_mode: str = "mzmine_seeded"
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
    optimal_component_max_size: int = 12
    optimal_component_max_edge_density: float = 0.35
    optimal_component_max_edges: int = 24
    split_valley_fraction_of_smaller_apex: float = 0.50

    def __post_init__(self) -> None:
        if self.trace_builder_mode not in {"mzmine_seeded", "scan_link"}:
            raise ValueError(
                "trace_builder_mode must be 'mzmine_seeded' or 'scan_link'"
            )
        for name in (
            "mass_detection_noise_level",
            "min_trace_seed_intensity",
            "min_consecutive_trace_intensity",
            "min_duration_min",
            "min_ok_prominence",
            "weak_support_prominence",
            "max_ok_mz_ppm_std",
            "max_weak_mz_ppm_std",
        ):
            value = require_finite_real(getattr(self, name), name=name)
            if value < 0:
                raise ValueError(f"{name} must be finite and >= 0")
        for name in ("feature_link_ppm", "max_duration_min"):
            value = require_finite_real(getattr(self, name), name=name)
            if value <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        for name in (
            "max_ok_gap_fraction",
            "max_weak_gap_fraction",
            "optimal_component_max_edge_density",
            "split_valley_fraction_of_smaller_apex",
        ):
            value = require_finite_real(getattr(self, name), name=name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        for name in ("max_missing_scans",):
            value = require_integer(getattr(self, name), name=name)
            if value < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        for name in (
            "min_observed_scans",
            "gaussian_window_points",
            "optimal_component_max_size",
            "optimal_component_max_edges",
        ):
            value = require_integer(getattr(self, name), name=name)
            if value <= 0:
                raise ValueError(f"{name} must be an integer > 0")
        if self.max_duration_min < self.min_duration_min:
            raise ValueError("max_duration_min must be >= min_duration_min")
        if self.min_ok_prominence < self.weak_support_prominence:
            raise ValueError(
                "min_ok_prominence must be >= weak_support_prominence"
            )
        if self.max_ok_gap_fraction > self.max_weak_gap_fraction:
            raise ValueError(
                "max_ok_gap_fraction must be <= max_weak_gap_fraction"
            )
        if self.max_ok_mz_ppm_std > self.max_weak_mz_ppm_std:
            raise ValueError("max_ok_mz_ppm_std must be <= max_weak_mz_ppm_std")


@dataclass(frozen=True)
class LocalMs1FeatureSummary:
    schema_version: str
    raw_path: str
    raw_file_name: str
    raw_file_size_bytes: int | None
    raw_file_mtime_ns: int | None
    sample_stem: str
    extraction_backend: str
    ms1_scan_selection_rule: str
    generator_version: str
    trace_builder_mode: str
    parameters: Mapping[str, object]
    parameter_hash: str
    wall_time_sec: float
    smoothing_kernel_name: str
    smoothing_kernel_scans: int
    median_ms1_scan_interval_sec: float | None
    effective_smoothing_seconds: float | None
    ms1_scan_count: int
    centroid_peak_count: int
    candidate_edge_count: int
    conflict_component_count: int
    degraded_assignment_component_count: int
    degraded_assignment_component_fraction: float
    total_traces: int
    total_peak_regions: int
    ok_feature_count: int
    weak_support_feature_count: int
    fail_count: int
    detected_features_rows: int
    diagnostics_row_count: int
    height_semantics: str
    area_semantics: str
    area_contract: str
    quant_ready: bool


@dataclass(frozen=True)
class LocalMs1FeatureDiagnosticRow:
    trace_id: str
    mz_apex: float | None
    rt_apex_min: float | None
    rt_start_min: float | None
    rt_end_min: float | None
    observed_scan_count: int
    missing_scan_count: int
    gap_fraction: float
    mz_ppm_std: float | None
    prominence: float | None
    boundary_quality: float | None
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool
    split_peak: bool
    overlap_suspected: bool
    reason: str


@dataclass(frozen=True)
class LocalMs1FeatureOutputs:
    detected_features_csv: Path
    summary_json: Path
    diagnostics_csv: Path


@dataclass
class _TraceBuilder:
    trace_id: int
    points: list[tuple[int, float, float, float]]
    scan_positions: list[int] = field(default_factory=list)
    scan_position_set: set[int] = field(default_factory=set)
    trailing_missing_scans: int = 0
    internal_missing_scan_count: int = 0
    internal_missing_gaps: list[tuple[int, int]] = field(default_factory=list)
    assignment_mode: str = "new_trace"
    component_size: int = 1
    component_edge_density: float = 0.0
    assignment_degraded_large_component: bool = False

    @property
    def last_mz(self) -> float:
        return self.points[-1][2]

    @property
    def last_scan_number(self) -> int:
        return self.points[-1][0]


@dataclass(frozen=True)
class _AssignmentEdge:
    ppm_error_abs: float
    trace_index: int
    peak_index: int
    intensity: float
    scan_gap: int
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool


@dataclass(frozen=True)
class _SeededMzRange:
    lower_mz: float
    upper_mz: float
    traces: list[_TraceBuilder]
    points: list[tuple[int, int, float, float, float]] = field(default_factory=list)


@dataclass(frozen=True)
class _PeakRegion:
    trace_id: str
    scan_numbers: tuple[int, ...]
    rts: np.ndarray
    mzs: np.ndarray
    intensities: np.ndarray
    smoothed: np.ndarray
    apex_index: int
    left_index: int
    right_index: int
    missing_scan_count: int
    candidate_edge_count: int
    assignment_mode: str
    component_size: int
    component_edge_density: float
    assignment_degraded_large_component: bool
    split_peak: bool
    overlap_suspected: bool


def build_local_ms1_feature_table(
    index: tuple[MS1Scan, ...],
    *,
    sample_stem: str,
    raw_path: str,
    raw_file_size_bytes: int | None = None,
    raw_file_mtime_ns: int | None = None,
    settings: LocalMs1FeatureSettings | None = None,
) -> tuple[
    tuple[DetectedFeature, ...],
    LocalMs1FeatureSummary,
    tuple[LocalMs1FeatureDiagnosticRow, ...],
]:
    resolved = settings or LocalMs1FeatureSettings()
    validate_ms1_scan_index(index, require_nonempty=True)
    start = time.perf_counter()
    if resolved.trace_builder_mode == "mzmine_seeded":
        traces, edge_count, component_count, degraded_count = _build_seeded_traces(
            index,
            resolved,
        )
    elif resolved.trace_builder_mode == "scan_link":
        traces, edge_count, component_count, degraded_count = _link_traces(
            index,
            resolved,
        )
    else:
        raise ValueError(
            "unsupported local MS1 trace builder mode: "
            f"{resolved.trace_builder_mode}",
        )
    features: list[DetectedFeature] = []
    diagnostics: list[LocalMs1FeatureDiagnosticRow] = []
    peak_region_count = 0

    for trace in traces:
        trace_id = f"{sample_stem}|trace|{trace.trace_id:06d}"
        if len(trace.points) < resolved.min_observed_scans:
            diagnostics.append(
                _diagnostic_from_trace(
                    trace_id,
                    trace,
                    "too_few_observed_scans",
                )
            )
            peak_region_count += 1
            continue
        if not _trace_has_min_consecutive_intensity(trace, resolved):
            diagnostics.append(
                _diagnostic_from_trace(
                    trace_id,
                    trace,
                    "too_few_consecutive_intense_scans",
                )
            )
            peak_region_count += 1
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
            trace_id=trace_id,
            scan_numbers=scan_numbers,
            rts=rts,
            mzs=mzs,
            intensities=intensities,
            smoothed=smoothed,
            internal_missing_scan_count=trace.internal_missing_scan_count,
            internal_missing_gaps=tuple(trace.internal_missing_gaps),
            candidate_edge_count=edge_count,
            assignment_mode=trace.assignment_mode,
            component_size=trace.component_size,
            component_edge_density=trace.component_edge_density,
            assignment_degraded_large_component=trace.assignment_degraded_large_component,
            settings=resolved,
        )
        if not regions:
            diagnostics.append(
                _diagnostic_from_trace(
                    trace_id,
                    trace,
                    "no_bounded_peak_region",
                )
            )
            peak_region_count += 1
            continue
        for region in regions:
            peak_region_count += 1
            quality, reason = _classify_region(region, resolved)
            if quality == "FAIL":
                diagnostics.append(_diagnostic_from_region(region, reason))
                continue
            feature_index = len(features) + 1
            feature_id = f"{sample_stem}|local_ms1_feature|{feature_index:06d}"
            features.append(
                DetectedFeature(
                    sample_stem=sample_stem,
                    feature_id=feature_id,
                    # The local detector ID is not comparable to CID-NL row
                    # identity. ``feature_id`` already preserves provenance;
                    # leaving the cross-provider pointer blank keeps the
                    # bounded composite m/z/RT fallback available.
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
            item.sample_stem,
            item.rt_apex_min,
            item.mz,
            item.feature_id,
        )
    )
    sorted_features: list[DetectedFeature] = []
    for index_id, feature in enumerate(features, start=1):
        feature_id = f"{sample_stem}|local_ms1_feature|{index_id:06d}"
        sorted_features.append(
            DetectedFeature(
                sample_stem=feature.sample_stem,
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

    ok_count = sum(
        1
        for feature in sorted_features
        if feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_OK
    )
    weak_count = sum(
        1
        for feature in sorted_features
        if feature.trace_quality == LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT
    )
    fail_count = len(diagnostics)
    interval = _median_scan_interval_sec(index)
    summary = LocalMs1FeatureSummary(
        schema_version=SUMMARY_SCHEMA_VERSION,
        raw_path=raw_path,
        raw_file_name=Path(raw_path).name,
        raw_file_size_bytes=raw_file_size_bytes,
        raw_file_mtime_ns=raw_file_mtime_ns,
        sample_stem=sample_stem,
        extraction_backend="local_ms1_scan_index",
        ms1_scan_selection_rule="MSOrder == Ms",
        generator_version="local_ms1_feature_generator.v1",
        trace_builder_mode=resolved.trace_builder_mode,
        parameters=asdict(resolved),
        parameter_hash=_parameter_hash(resolved),
        wall_time_sec=time.perf_counter() - start,
        smoothing_kernel_name="gaussian_15",
        smoothing_kernel_scans=resolved.gaussian_window_points,
        median_ms1_scan_interval_sec=interval,
        effective_smoothing_seconds=(
            interval
            * morphology_trace_effective_points(
                len(index),
                method="gaussian_15",
                window_points=resolved.gaussian_window_points,
            )
            if interval is not None
            else None
        ),
        ms1_scan_count=len(index),
        centroid_peak_count=sum(len(scan.masses) for scan in index),
        candidate_edge_count=edge_count,
        conflict_component_count=component_count,
        degraded_assignment_component_count=degraded_count,
        degraded_assignment_component_fraction=(
            degraded_count / component_count if component_count else 0.0
        ),
        total_traces=len(traces),
        total_peak_regions=peak_region_count,
        ok_feature_count=ok_count,
        weak_support_feature_count=weak_count,
        fail_count=fail_count,
        detected_features_rows=len(sorted_features),
        diagnostics_row_count=len(diagnostics),
        height_semantics="screening_smoothed_height",
        area_semantics="screening_smoothed_baseline_corrected_area",
        area_contract="screening_only",
        quant_ready=False,
    )
    _validate_summary(summary)
    return tuple(sorted_features), summary, tuple(diagnostics)


def _ppm_error(observed_mz: float, expected_mz: float) -> float:
    return (observed_mz - expected_mz) / expected_mz * 1e6


def _parameter_hash(settings: LocalMs1FeatureSettings) -> str:
    payload = json.dumps(asdict(settings), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _median_scan_interval_sec(index: tuple[MS1Scan, ...]) -> float | None:
    if len(index) < 2:
        return None
    diffs = np.diff(np.asarray([scan.rt for scan in index], dtype=float)) * 60.0
    if len(diffs) == 0:
        return None
    return float(np.median(diffs))


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
    trace_id: str,
    scan_numbers: tuple[int, ...],
    rts: np.ndarray,
    mzs: np.ndarray,
    intensities: np.ndarray,
    smoothed: np.ndarray,
    internal_missing_scan_count: int,
    internal_missing_gaps: Sequence[tuple[int, int]] = (),
    candidate_edge_count: int,
    assignment_mode: str,
    component_size: int,
    component_edge_density: float,
    assignment_degraded_large_component: bool,
    settings: LocalMs1FeatureSettings,
) -> tuple[_PeakRegion, ...]:
    if len(smoothed) < settings.min_observed_scans:
        return ()
    apices = _local_apex_indices(smoothed)
    if not apices:
        apices = (int(np.argmax(smoothed)),)
    boundaries = _split_boundaries(smoothed, apices, settings)
    regions: list[_PeakRegion] = []
    split_peak = len(boundaries) > 1
    for left, apex_index, right in boundaries:
        if right - left + 1 < settings.min_observed_scans:
            continue
        regions.append(
            _PeakRegion(
                trace_id=trace_id,
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
                candidate_edge_count=candidate_edge_count,
                assignment_mode=assignment_mode,
                component_size=component_size,
                component_edge_density=component_edge_density,
                assignment_degraded_large_component=assignment_degraded_large_component,
                split_peak=split_peak,
                overlap_suspected=not split_peak and len(apices) > 1,
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
) -> tuple[list[_TraceBuilder], int, int, int]:
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
    next_trace_id = 1
    for seed_range in ranges:
        next_trace_id = _build_seed_range_traces(
            seed_range,
            next_trace_id=next_trace_id,
            settings=settings,
        )
        traces.extend(seed_range.traces)

    for trace in traces:
        _finalize_seeded_trace(trace)

    return (
        traces,
        len(data_points),
        len(ranges),
        0,
    )


def _build_seed_range_traces(
    seed_range: _SeededMzRange,
    *,
    next_trace_id: int,
    settings: LocalMs1FeatureSettings,
) -> int:
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
            next_trace_id = _append_seeded_trace_segment(
                seed_range,
                next_trace_id=next_trace_id,
                scan_positions=segment_positions,
                points=segment_points,
                settings=settings,
            )
            segment_positions = []
            segment_points = []
        segment_positions.append(scan_index)
        segment_points.append(point_by_scan[scan_index])
        previous_scan_index = scan_index

    return _append_seeded_trace_segment(
        seed_range,
        next_trace_id=next_trace_id,
        scan_positions=segment_positions,
        points=segment_points,
        settings=settings,
    )


def _append_seeded_trace_segment(
    seed_range: _SeededMzRange,
    *,
    next_trace_id: int,
    scan_positions: Sequence[int],
    points: Sequence[tuple[int, float, float, float]],
    settings: LocalMs1FeatureSettings,
) -> int:
    if not scan_positions:
        return next_trace_id
    if not any(point[3] >= settings.min_trace_seed_intensity for point in points):
        return next_trace_id
    trace = _TraceBuilder(
        trace_id=next_trace_id,
        points=list(points),
        scan_positions=list(scan_positions),
        scan_position_set=set(scan_positions),
        assignment_mode="mzmine_seeded_chromatogram",
    )
    seed_range.traces.append(trace)
    return next_trace_id + 1


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
        (float(masses[index]), float(intensities[index]))
        for index in indices[order]
    ]


def _seed_range(
    mz: float,
    ranges: Sequence[_SeededMzRange],
    range_lowers: Sequence[float],
    settings: LocalMs1FeatureSettings,
) -> tuple[float, float, int]:
    tolerance = mz * settings.feature_link_ppm * 1e-6
    lower_mz = mz - tolerance
    upper_mz = mz + tolerance
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
    trace.scan_position_set = set(trace.scan_positions)
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


def _link_traces(
    index: tuple[MS1Scan, ...],
    settings: LocalMs1FeatureSettings,
) -> tuple[list[_TraceBuilder], int, int, int]:
    active: list[_TraceBuilder] = []
    complete: list[_TraceBuilder] = []
    next_trace_id = 1
    candidate_edge_count = 0
    conflict_component_count = 0
    degraded_component_count = 0

    for scan_position, scan in enumerate(index):
        peaks = _positive_scan_peaks(scan)
        peak_mzs = np.asarray([mz for mz, _intensity in peaks], dtype=np.float64)
        assigned_traces: set[int] = set()
        assigned_peaks: set[int] = set()
        edges: list[_AssignmentEdge] = []
        if len(peak_mzs):
            for trace_index, trace in enumerate(active):
                tolerance = trace.last_mz * settings.feature_link_ppm * 1e-6
                lower_mz = trace.last_mz - tolerance
                upper_mz = trace.last_mz + tolerance
                start = int(np.searchsorted(peak_mzs, lower_mz, side="left"))
                stop = int(np.searchsorted(peak_mzs, upper_mz, side="right"))
                for peak_index in range(start, stop):
                    mz, intensity = peaks[peak_index]
                    candidate_edge_count += 1
                    edges.append(
                        _AssignmentEdge(
                            ppm_error_abs=abs(_ppm_error(mz, trace.last_mz)),
                            trace_index=trace_index,
                            peak_index=peak_index,
                            intensity=intensity,
                            scan_gap=max(
                                1,
                                int(scan.scan_number) - trace.last_scan_number,
                            ),
                            assignment_mode="unresolved",
                            component_size=0,
                            component_edge_density=0.0,
                            assignment_degraded_large_component=False,
                        )
                    )

        components = _edge_components(edges)
        conflict_component_count += len(components)
        for component in components:
            component_trace_count = len({edge.trace_index for edge in component})
            component_peak_count = len({edge.peak_index for edge in component})
            component_size = component_trace_count + component_peak_count
            component_edge_density = _component_edge_density(
                component,
                component_trace_count=component_trace_count,
                component_peak_count=component_peak_count,
            )
            use_optimal = (
                len(component) <= settings.optimal_component_max_edges
                and (
                    component_size <= settings.optimal_component_max_size
                    or component_edge_density
                    <= settings.optimal_component_max_edge_density
                )
            )
            if use_optimal:
                assignment_mode = "optimal_component"
                degraded = False
                chosen_edges = _optimal_component_assignment(component)
            else:
                assignment_mode = (
                    "greedy_edge_count_limited_component"
                    if len(component) > settings.optimal_component_max_edges
                    else "greedy_large_dense_component"
                )
                degraded = True
                degraded_component_count += 1
                chosen_edges = _greedy_component_assignment(component)
            for edge in chosen_edges:
                if (
                    edge.trace_index in assigned_traces
                    or edge.peak_index in assigned_peaks
                ):
                    continue
                trace = active[edge.trace_index]
                mz, intensity = peaks[edge.peak_index]
                if trace.trailing_missing_scans:
                    trace.internal_missing_gaps.append(
                        (len(trace.points) - 1, trace.trailing_missing_scans)
                    )
                    trace.internal_missing_scan_count += trace.trailing_missing_scans
                trace.points.append((scan.scan_number, scan.rt, mz, intensity))
                trace.scan_positions.append(scan_position)
                trace.scan_position_set.add(scan_position)
                trace.trailing_missing_scans = 0
                trace.assignment_mode = assignment_mode
                trace.component_size = component_size
                trace.component_edge_density = component_edge_density
                trace.assignment_degraded_large_component = degraded
                assigned_traces.add(edge.trace_index)
                assigned_peaks.add(edge.peak_index)

        still_active: list[_TraceBuilder] = []
        for trace_index, trace in enumerate(active):
            if trace_index in assigned_traces:
                still_active.append(trace)
                continue
            trace.trailing_missing_scans += 1
            if trace.trailing_missing_scans <= settings.max_missing_scans:
                still_active.append(trace)
            else:
                complete.append(trace)
        for peak_index, (mz, intensity) in enumerate(peaks):
            if peak_index in assigned_peaks:
                continue
            still_active.append(
                _TraceBuilder(
                    trace_id=next_trace_id,
                    points=[(scan.scan_number, scan.rt, mz, intensity)],
                    scan_positions=[scan_position],
                    scan_position_set={scan_position},
                )
            )
            next_trace_id += 1
        active = still_active

    complete.extend(active)
    return (
        complete,
        candidate_edge_count,
        conflict_component_count,
        degraded_component_count,
    )


def _positive_scan_peaks(scan: MS1Scan) -> list[tuple[float, float]]:
    masses = np.asarray(scan.masses, dtype=np.float64)
    intensities = np.asarray(scan.intensities, dtype=np.float64)
    if masses.ndim != 1 or intensities.ndim != 1 or masses.shape != intensities.shape:
        return []
    peaks = [
        (float(mz), float(intensity))
        for mz, intensity in zip(masses, intensities, strict=True)
        if (
            float(intensity) > 0
            and math.isfinite(float(mz))
            and math.isfinite(float(intensity))
        )
    ]
    peaks.sort(key=lambda item: item[0])
    return peaks


def _edge_components(edges: Sequence[_AssignmentEdge]) -> list[list[_AssignmentEdge]]:
    remaining = list(edges)
    components: list[list[_AssignmentEdge]] = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        trace_ids = {seed.trace_index}
        peak_ids = {seed.peak_index}
        changed = True
        while changed:
            changed = False
            kept: list[_AssignmentEdge] = []
            for edge in remaining:
                if edge.trace_index in trace_ids or edge.peak_index in peak_ids:
                    component.append(edge)
                    trace_ids.add(edge.trace_index)
                    peak_ids.add(edge.peak_index)
                    changed = True
                else:
                    kept.append(edge)
            remaining = kept
        components.append(component)
    return components


def _edge_sort_key(edge: _AssignmentEdge) -> tuple[float, int, float]:
    return (edge.ppm_error_abs, edge.scan_gap, -edge.intensity)


def _component_edge_density(
    component: Sequence[_AssignmentEdge],
    *,
    component_trace_count: int,
    component_peak_count: int,
) -> float:
    capacity = max(1, component_trace_count * component_peak_count)
    return float(len(component)) / capacity


def _greedy_component_assignment(
    component: Sequence[_AssignmentEdge],
) -> tuple[_AssignmentEdge, ...]:
    selected: list[_AssignmentEdge] = []
    used_traces: set[int] = set()
    used_peaks: set[int] = set()
    for edge in sorted(component, key=_edge_sort_key):
        if edge.trace_index in used_traces or edge.peak_index in used_peaks:
            continue
        selected.append(edge)
        used_traces.add(edge.trace_index)
        used_peaks.add(edge.peak_index)
    return tuple(selected)


def _optimal_component_assignment(
    component: Sequence[_AssignmentEdge],
) -> tuple[_AssignmentEdge, ...]:
    ordered = tuple(sorted(component, key=_edge_sort_key))
    best: (
        tuple[tuple[float, float, float, float], tuple[_AssignmentEdge, ...]] | None
    ) = None

    def _search(
        index: int,
        selected: tuple[_AssignmentEdge, ...],
        used_traces: frozenset[int],
        used_peaks: frozenset[int],
    ) -> None:
        nonlocal best
        if index >= len(ordered):
            score = (
                -float(len(selected)),
                sum(edge.ppm_error_abs for edge in selected),
                float(sum(edge.scan_gap for edge in selected)),
                -sum(edge.intensity for edge in selected),
            )
            candidate = (score, selected)
            if best is None or candidate[0] < best[0]:
                best = candidate
            return

        edge = ordered[index]
        _search(index + 1, selected, used_traces, used_peaks)
        if edge.trace_index in used_traces or edge.peak_index in used_peaks:
            return
        _search(
            index + 1,
            (*selected, edge),
            frozenset((*used_traces, edge.trace_index)),
            frozenset((*used_peaks, edge.peak_index)),
        )

    _search(0, (), frozenset(), frozenset())
    return best[1] if best is not None else ()


def _classify_region(
    region: _PeakRegion,
    settings: LocalMs1FeatureSettings,
) -> tuple[str, str]:
    smoothed = region.smoothed
    apex = float(smoothed[region.apex_index])
    if apex <= 0:
        return "FAIL", "non_positive_height"
    if region.apex_index in {region.left_index, region.right_index}:
        return "FAIL", "apex_at_region_edge"
    area = _local_linear_baseline_area(
        region.rts,
        smoothed,
        region.left_index,
        region.right_index,
    )
    if area <= 0:
        return "FAIL", "non_positive_area"
    duration = float(region.rts[region.right_index] - region.rts[region.left_index])
    if duration < settings.min_duration_min:
        return "FAIL", "duration_below_min"
    if duration > settings.max_duration_min:
        return "FAIL", "duration_above_max"
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
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_OK, "ok"
    if (
        prominence >= settings.weak_support_prominence
        and gap_fraction <= settings.max_weak_gap_fraction
        and ppm_std <= settings.max_weak_mz_ppm_std
    ):
        return LOCAL_MS1_FEATURE_TRACE_QUALITY_WEAK_SUPPORT, "weak_support"
    return "FAIL", "quality_below_weak_support"


def _mz_ppm_std(mzs: np.ndarray) -> float:
    mean_mz = float(np.mean(mzs))
    if mean_mz <= 0:
        return math.inf
    return float(np.std((mzs - mean_mz) / mean_mz * 1e6))


def _diagnostic_from_trace(
    trace_id: str,
    trace: _TraceBuilder,
    reason: str,
) -> LocalMs1FeatureDiagnosticRow:
    mzs = np.asarray([point[2] for point in trace.points], dtype=float)
    rts = np.asarray([point[1] for point in trace.points], dtype=float)
    return LocalMs1FeatureDiagnosticRow(
        trace_id=trace_id,
        mz_apex=float(mzs[0]) if len(mzs) else None,
        rt_apex_min=float(rts[0]) if len(rts) else None,
        rt_start_min=float(rts[0]) if len(rts) else None,
        rt_end_min=float(rts[-1]) if len(rts) else None,
        observed_scan_count=len(trace.points),
        missing_scan_count=trace.internal_missing_scan_count,
        gap_fraction=trace.internal_missing_scan_count
        / max(1, len(trace.points) + trace.internal_missing_scan_count),
        mz_ppm_std=_mz_ppm_std(mzs) if len(mzs) else None,
        prominence=None,
        boundary_quality=None,
        assignment_mode=trace.assignment_mode,
        component_size=trace.component_size,
        component_edge_density=trace.component_edge_density,
        assignment_degraded_large_component=trace.assignment_degraded_large_component,
        split_peak=False,
        overlap_suspected=False,
        reason=reason,
    )


def _diagnostic_from_region(
    region: _PeakRegion,
    reason: str,
) -> LocalMs1FeatureDiagnosticRow:
    return LocalMs1FeatureDiagnosticRow(
        trace_id=region.trace_id,
        mz_apex=float(region.mzs[region.apex_index]),
        rt_apex_min=float(region.rts[region.apex_index]),
        rt_start_min=float(region.rts[region.left_index]),
        rt_end_min=float(region.rts[region.right_index]),
        observed_scan_count=len(region.scan_numbers),
        missing_scan_count=region.missing_scan_count,
        gap_fraction=region.missing_scan_count
        / max(1, len(region.scan_numbers) + region.missing_scan_count),
        mz_ppm_std=_mz_ppm_std(region.mzs),
        prominence=_prominence(
            region.smoothed,
            region.apex_index,
            region.left_index,
            region.right_index,
        ),
        boundary_quality=_boundary_quality(
            region.smoothed,
            region.apex_index,
            region.left_index,
            region.right_index,
        ),
        assignment_mode=region.assignment_mode,
        component_size=region.component_size,
        component_edge_density=region.component_edge_density,
        assignment_degraded_large_component=region.assignment_degraded_large_component,
        split_peak=region.split_peak,
        overlap_suspected=region.overlap_suspected,
        reason=reason,
    )


def _validate_summary(summary: LocalMs1FeatureSummary) -> None:
    feature_count = summary.ok_feature_count + summary.weak_support_feature_count
    if summary.detected_features_rows != feature_count:
        raise ValueError("local MS1 feature summary count invariant failed")
    if summary.diagnostics_row_count != summary.fail_count:
        raise ValueError("local MS1 feature diagnostics count invariant failed")
    if summary.total_peak_regions != (
        summary.ok_feature_count
        + summary.weak_support_feature_count
        + summary.fail_count
    ):
        raise ValueError("local MS1 feature peak-region count invariant failed")
    expected_fraction = (
        summary.degraded_assignment_component_count / summary.conflict_component_count
        if summary.conflict_component_count
        else 0.0
    )
    if not math.isclose(
        summary.degraded_assignment_component_fraction,
        expected_fraction,
    ):
        raise ValueError(
            "local MS1 feature degraded assignment fraction invariant failed"
        )


def write_local_ms1_feature_outputs(
    output_dir: Path,
    features: Sequence[DetectedFeature],
    summary: LocalMs1FeatureSummary,
    diagnostics: Sequence[LocalMs1FeatureDiagnosticRow],
) -> LocalMs1FeatureOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    detected_features_csv = write_detected_features_csv(
        output_dir / "detected_features.csv",
        features,
    )
    summary_json = output_dir / "local_ms1_feature_summary.json"
    summary_json.write_text(
        json.dumps(asdict(summary), indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    diagnostics_csv = output_dir / "local_ms1_feature_diagnostics.csv"
    write_delimited_rows(
        diagnostics_csv,
        (asdict(row) for row in diagnostics),
        DIAGNOSTICS_COLUMNS,
        extrasaction="raise",
    )
    return LocalMs1FeatureOutputs(
        detected_features_csv=detected_features_csv,
        summary_json=summary_json,
        diagnostics_csv=diagnostics_csv,
    )
