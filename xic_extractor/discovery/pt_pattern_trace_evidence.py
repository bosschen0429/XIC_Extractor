"""Trace-level Pt pattern evidence helpers.

This module owns the reusable chromatography evidence semantics for
``delta_mass(pattern_id=pt)`` candidates. It does not open RAW files, write
diagnostic outputs, or grant final quant authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    gaussian_smooth_values,
)
from xic_extractor.tabular_io import optional_float, read_delimited_rows
from xic_extractor.xic_models import XICRequest, XICTrace

SCHEMA_VERSION = "pt_pattern_trace_diagnosis.v2"
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_trace_evidence"
READINESS_TIER = "diagnostic_only"
TRACE_DECISION_BASIS = "gaussian15_smoothed_trace"
RAW_TRACE_BASIS = "raw_vendor_trace"
TRACE_SMOOTH_METHOD = "gaussian15"
TRACE_SMOOTH_WINDOW_POINTS = DEFAULT_OWN_MAX_SMOOTH_POINTS
TRACE_FLAG_NONE = "none"
RAW_SMOOTHED_APEX_MISMATCH_SEC = 10.0
LARGE_SHAPE_SHIFT_SEC = 20.0
LOW_SCAN_DENSITY_POINT_COUNT = TRACE_SMOOTH_WINDOW_POINTS
MIN_EFFECTIVE_SMOOTHING_WINDOW_SEC = 8.0
PLUS4_WINDOW_DOMINANCE_MIN_AREA_RATIO = 1.60
PLUS4_WINDOW_DOMINANCE_MIN_SPAN25_RATIO = 1.50
PLUS4_WINDOW_DOMINANCE_MIN_CENTROID_SHIFT_SEC = 15.0

POSITION_LABELS = ("base", "pt_plus_1", "pt_plus_2", "pt_plus_4")
NON_BASE_POSITION_LABELS = ("pt_plus_1", "pt_plus_2", "pt_plus_4")
REQUIRED_CANDIDATE_COLUMNS = (
    "sample_stem",
    "candidate_id",
    "base_mz",
    "base_rt_apex_min",
    "pt_plus_1_mz",
    "pt_plus_1_expected_mz",
    "pt_plus_2_mz",
    "pt_plus_2_expected_mz",
    "pt_plus_4_mz",
    "pt_plus_4_expected_mz",
)

ANCHOR_SEGMENTATION_SUMMARY_COLUMNS = (
    "anchor_segmentation_status",
    "anchor_segmentation_reason",
    "anchor_segmentation_common_rt_start_min",
    "anchor_segmentation_common_rt_end_min",
    "anchor_segmentation_common_duration_sec",
    "anchor_segmentation_common_start_owner_labels",
    "anchor_segmentation_common_end_owner_labels",
    "anchor_segmentation_boundary_review_status",
    "anchor_segmentation_boundary_review_position_labels",
    "anchor_segmentation_max_left_clip_sec",
    "anchor_segmentation_max_right_clip_sec",
    "anchor_segmentation_min_common_retained_fraction",
    "anchor_segmentation_max_common_left_residual_fraction",
    "anchor_segmentation_max_common_right_residual_fraction",
    "anchor_segmentation_descending_at_common_right_position_labels",
)

ANCHOR_SEGMENTATION_METRIC_COLUMNS = (
    "anchor_segment_rt_start_min",
    "anchor_segment_rt_end_min",
    "anchor_segment_duration_sec",
    "anchor_segment_class",
    "anchor_segment_boundary_stop_reason",
    "anchor_segment_owns_common_start",
    "anchor_segment_owns_common_end",
    "anchor_segment_left_clip_sec",
    "anchor_segment_right_clip_sec",
    "anchor_segment_common_retained_fraction",
    "anchor_segment_common_left_residual_fraction",
    "anchor_segment_common_right_residual_fraction",
    "anchor_segment_common_left_trend",
    "anchor_segment_common_right_trend",
)

SUMMARY_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "scan_pattern_seed_status",
    "base_mz",
    "base_rt_apex_min",
    "rt_half_window_sec",
    "ppm",
    "mz_source",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "min_effective_smoothing_window_sec",
    "max_trace_point_count",
    "positions_requested",
    "positions_with_points",
    "positions_with_signal",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "min_shifted_pearson_r_to_base",
    "max_abs_shape_shift_sec_to_base",
    "raw_max_abs_apex_delta_sec",
    "raw_min_pearson_r_to_base",
    "raw_min_active_overlap_fraction",
    "trace_ambiguity_status",
    "trace_ambiguity_flags",
    "shifted_only_position_labels",
    "trace_support_status",
    "combined_pt_pattern_status",
    "reason",
)

METRIC_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "position_label",
    "target_mz",
    "rt_min",
    "rt_max",
    "ppm",
    "trace_decision_basis",
    "trace_smoothing_method",
    "trace_smoothing_window_points",
    "point_count",
    "median_rt_step_sec",
    "effective_smoothing_window_sec",
    "raw_max_intensity",
    "raw_apex_rt_min",
    "raw_apex_delta_sec_to_base",
    "raw_smoothed_apex_delta_sec",
    "raw_height_ratio_to_base",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "max_intensity",
    "apex_rt_min",
    "apex_delta_sec_to_base",
    "height_ratio_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
    "shifted_pearson_r_to_base",
    "shape_shift_sec_to_base",
    "trace_quality_flags",
    "trace_support_status",
    "reason",
)

TRACE_POINT_COLUMNS = (
    "schema_version",
    "sample_stem",
    "candidate_id",
    "position_label",
    "intensity_basis",
    "rt_min",
    "intensity",
)

DEFAULT_PPM = 20.0
DEFAULT_RT_HALF_WINDOW_SEC = 30.0
DEFAULT_ACTIVE_FRACTION = 0.10
DEFAULT_MIN_PEARSON_R = 0.70
DEFAULT_MAX_APEX_DELTA_SEC = 30.0
DEFAULT_LIMIT = 25


@dataclass(frozen=True)
class TraceRequestItem:
    sample_stem: str
    candidate_id: str
    position_label: str
    target_mz: float
    request: XICRequest


@dataclass(frozen=True)
class TraceMetric:
    position_label: str
    target_mz: float
    rt_min: float
    rt_max: float
    ppm: float
    point_count: int
    median_rt_step_sec: float | None
    effective_smoothing_window_sec: float | None
    raw_max_intensity: float | None
    raw_apex_rt_min: float | None
    raw_apex_delta_sec_to_base: float | None
    raw_smoothed_apex_delta_sec: float | None
    raw_height_ratio_to_base: float | None
    raw_pearson_r_to_base: float | None
    raw_active_overlap_fraction: float | None
    max_intensity: float | None
    apex_rt_min: float | None
    apex_delta_sec_to_base: float | None
    height_ratio_to_base: float | None
    pearson_r_to_base: float | None
    active_overlap_fraction: float | None
    shifted_pearson_r_to_base: float | None
    shape_shift_sec_to_base: float | None
    trace_quality_flags: str
    trace_support_status: str
    reason: str


@dataclass(frozen=True)
class _NormalizedWindowShape:
    area: float
    span25_sec: float
    centroid_rt_min: float


@dataclass(frozen=True)
class CandidateTraceDiagnosis:
    summary_row: dict[str, object]
    metric_rows: tuple[dict[str, object], ...]
    trace_point_rows: tuple[dict[str, object], ...]


def load_candidate_rows(
    path: Path,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[dict[str, str], ...]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    rows = read_delimited_rows(
        path,
        required_columns=REQUIRED_CANDIDATE_COLUMNS,
        delimiter=",",
        encoding="utf-8-sig",
    )
    return tuple(rows[offset : offset + limit])


def build_trace_requests_for_candidate(
    row: Mapping[str, str],
    *,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
) -> tuple[TraceRequestItem, ...]:
    sample_stem = _required_text(row, "sample_stem")
    candidate_id = _required_text(row, "candidate_id")
    base_rt = _required_float(row, "base_rt_apex_min", label=candidate_id)
    rt_half_window_min = rt_half_window_sec / 60.0
    rt_min = max(0.0, base_rt - rt_half_window_min)
    rt_max = base_rt + rt_half_window_min

    items: list[TraceRequestItem] = []
    for position_label in POSITION_LABELS:
        target_mz = _position_target_mz(
            row,
            position_label=position_label,
            mz_source=mz_source,
            label=candidate_id,
        )
        items.append(
            TraceRequestItem(
                sample_stem=sample_stem,
                candidate_id=candidate_id,
                position_label=position_label,
                target_mz=target_mz,
                request=XICRequest(
                    mz=target_mz,
                    rt_min=rt_min,
                    rt_max=rt_max,
                    ppm_tol=ppm,
                ),
            )
        )
    return tuple(items)


def diagnose_candidate_traces(
    row: Mapping[str, str],
    trace_by_position: Mapping[str, XICTrace],
    *,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
    include_trace_points: bool = False,
) -> CandidateTraceDiagnosis:
    sample_stem = _required_text(row, "sample_stem")
    candidate_id = _required_text(row, "candidate_id")
    base_mz = _required_float(row, "base_mz", label=candidate_id)
    base_rt = _required_float(row, "base_rt_apex_min", label=candidate_id)

    base_trace = trace_by_position.get("base", XICTrace.empty())
    base_raw_stats = _trace_stats(base_trace, intensity_basis="raw")
    base_stats = _trace_stats(base_trace, intensity_basis="decision")
    metrics: list[TraceMetric] = []
    for position_label in POSITION_LABELS:
        trace = trace_by_position.get(position_label, XICTrace.empty())
        target_mz = _position_target_mz(
            row,
            position_label=position_label,
            mz_source=mz_source,
            label=candidate_id,
        )
        metrics.append(
            _trace_metric(
                position_label=position_label,
                target_mz=target_mz,
                trace=trace,
                base_trace=base_trace,
                base_raw_stats=base_raw_stats,
                base_stats=base_stats,
                ppm=ppm,
                active_fraction=active_fraction,
                min_pearson_r=min_pearson_r,
                max_apex_delta_sec=max_apex_delta_sec,
            )
        )

    metrics = _with_family_quality_flags(metrics, trace_by_position)
    non_base_metrics = [
        metric
        for metric in metrics
        if metric.position_label in NON_BASE_POSITION_LABELS
    ]
    supported_count = sum(
        metric.trace_support_status == "supported" for metric in non_base_metrics
    )
    positions_with_points = sum(metric.point_count > 0 for metric in metrics)
    max_trace_point_count = max((metric.point_count for metric in metrics), default=0)
    effective_smoothing_windows = [
        metric.effective_smoothing_window_sec
        for metric in metrics
        if metric.effective_smoothing_window_sec is not None
    ]
    positions_with_signal = sum(
        metric.max_intensity is not None and metric.max_intensity > 0
        for metric in metrics
    )
    apex_deltas = [
        abs(metric.apex_delta_sec_to_base)
        for metric in non_base_metrics
        if metric.apex_delta_sec_to_base is not None
    ]
    correlations = [
        metric.pearson_r_to_base
        for metric in non_base_metrics
        if metric.pearson_r_to_base is not None
    ]
    overlaps = [
        metric.active_overlap_fraction
        for metric in non_base_metrics
        if metric.active_overlap_fraction is not None
    ]
    shifted_correlations = [
        metric.shifted_pearson_r_to_base
        for metric in non_base_metrics
        if metric.shifted_pearson_r_to_base is not None
    ]
    shape_shifts = [
        abs(metric.shape_shift_sec_to_base)
        for metric in non_base_metrics
        if metric.shape_shift_sec_to_base is not None
    ]
    raw_apex_deltas = [
        abs(metric.raw_apex_delta_sec_to_base)
        for metric in non_base_metrics
        if metric.raw_apex_delta_sec_to_base is not None
    ]
    raw_correlations = [
        metric.raw_pearson_r_to_base
        for metric in non_base_metrics
        if metric.raw_pearson_r_to_base is not None
    ]
    raw_overlaps = [
        metric.raw_active_overlap_fraction
        for metric in non_base_metrics
        if metric.raw_active_overlap_fraction is not None
    ]
    trace_ambiguity_status, trace_ambiguity_flags = _summary_ambiguity(
        non_base_metrics
    )
    shifted_only_position_labels = _shifted_only_position_labels(non_base_metrics)
    summary_status, summary_reason = _summary_status(
        supported_count=supported_count,
        positions_with_signal=positions_with_signal,
        base_stats=base_stats,
    )
    metric_rows = tuple(
        _metric_row(
            metric,
            sample_stem=sample_stem,
            candidate_id=candidate_id,
        )
        for metric in metrics
    )
    trace_point_rows = (
        _trace_point_rows(
            sample_stem=sample_stem,
            candidate_id=candidate_id,
            trace_by_position=trace_by_position,
        )
        if include_trace_points
        else ()
    )
    return CandidateTraceDiagnosis(
        summary_row={
            "schema_version": SCHEMA_VERSION,
            "sample_stem": sample_stem,
            "candidate_id": candidate_id,
            "scan_pattern_seed_status": "full_pattern_seeded",
            "base_mz": base_mz,
            "base_rt_apex_min": base_rt,
            "rt_half_window_sec": rt_half_window_sec,
            "ppm": ppm,
            "mz_source": mz_source,
            "trace_decision_basis": TRACE_DECISION_BASIS,
            "trace_smoothing_method": TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": TRACE_SMOOTH_WINDOW_POINTS,
            "min_effective_smoothing_window_sec": (
                min(effective_smoothing_windows)
                if effective_smoothing_windows
                else None
            ),
            "max_trace_point_count": max_trace_point_count,
            "positions_requested": len(POSITION_LABELS),
            "positions_with_points": positions_with_points,
            "positions_with_signal": positions_with_signal,
            "non_base_positions_supported": supported_count,
            "max_abs_apex_delta_sec": max(apex_deltas) if apex_deltas else None,
            "min_pearson_r_to_base": min(correlations) if correlations else None,
            "min_active_overlap_fraction": min(overlaps) if overlaps else None,
            "min_shifted_pearson_r_to_base": (
                min(shifted_correlations) if shifted_correlations else None
            ),
            "max_abs_shape_shift_sec_to_base": (
                max(shape_shifts) if shape_shifts else None
            ),
            "raw_max_abs_apex_delta_sec": (
                max(raw_apex_deltas) if raw_apex_deltas else None
            ),
            "raw_min_pearson_r_to_base": (
                min(raw_correlations) if raw_correlations else None
            ),
            "raw_min_active_overlap_fraction": (
                min(raw_overlaps) if raw_overlaps else None
            ),
            "trace_ambiguity_status": trace_ambiguity_status,
            "trace_ambiguity_flags": trace_ambiguity_flags,
            "shifted_only_position_labels": shifted_only_position_labels,
            "trace_support_status": summary_status,
            "combined_pt_pattern_status": _combined_pt_pattern_status(summary_status),
            "reason": summary_reason,
        },
        metric_rows=metric_rows,
        trace_point_rows=trace_point_rows,
    )


def combined_pt_pattern_status(trace_support_status: str) -> str:
    return _combined_pt_pattern_status(trace_support_status)


def _with_family_quality_flags(
    metrics: list[TraceMetric],
    trace_by_position: Mapping[str, XICTrace],
) -> list[TraceMetric]:
    plus4_metric = next(
        (metric for metric in metrics if metric.position_label == "pt_plus_4"),
        None,
    )
    if plus4_metric is None or not _is_plus4_window_tail_dominant(
        trace_by_position,
    ):
        return metrics

    updated: list[TraceMetric] = []
    for metric in metrics:
        if metric.position_label != "pt_plus_4":
            updated.append(metric)
            continue

        flags = _split_flags(metric.trace_quality_flags)
        flags.add("plus4_window_tail_dominance")
        updated.append(
            replace(
                metric,
                trace_quality_flags=_join_flags(flags),
            )
        )
    return updated


def _is_plus4_window_tail_dominant(
    trace_by_position: Mapping[str, XICTrace],
) -> bool:
    plus4_shape = _normalized_window_shape(
        trace_by_position.get("pt_plus_4", XICTrace.empty())
    )
    if plus4_shape is None:
        return False

    reference_shapes = tuple(
        shape
        for label in ("base", "pt_plus_1", "pt_plus_2")
        if (
            shape := _normalized_window_shape(
                trace_by_position.get(label, XICTrace.empty())
            )
        )
        is not None
    )
    if len(reference_shapes) < 2:
        return False

    reference_area = float(
        np.median([shape.area for shape in reference_shapes])
    )
    reference_span25 = float(
        np.median([shape.span25_sec for shape in reference_shapes])
    )
    reference_centroid = float(
        np.median([shape.centroid_rt_min for shape in reference_shapes])
    )
    if reference_area <= 0 or reference_span25 <= 0:
        return False

    return (
        plus4_shape.area / reference_area
        >= PLUS4_WINDOW_DOMINANCE_MIN_AREA_RATIO
        and plus4_shape.span25_sec / reference_span25
        >= PLUS4_WINDOW_DOMINANCE_MIN_SPAN25_RATIO
        and (plus4_shape.centroid_rt_min - reference_centroid) * 60.0
        >= PLUS4_WINDOW_DOMINANCE_MIN_CENTROID_SHIFT_SEC
    )


def _normalized_window_shape(trace: XICTrace) -> _NormalizedWindowShape | None:
    rt, intensity = _arrays_for_basis(trace, intensity_basis="decision")
    if rt.size < 2 or intensity.size < 2:
        return None

    max_intensity = float(np.max(intensity))
    if not math.isfinite(max_intensity) or max_intensity <= 0:
        return None

    normalized = intensity / max_intensity
    area = float(np.trapezoid(normalized, rt))
    if not math.isfinite(area) or area <= 0:
        return None

    above_quarter = rt[normalized >= 0.25]
    span25_sec = (
        float((above_quarter[-1] - above_quarter[0]) * 60.0)
        if above_quarter.size >= 2
        else 0.0
    )
    centroid_rt_min = float(np.trapezoid(rt * normalized, rt) / area)
    if not math.isfinite(centroid_rt_min):
        return None
    return _NormalizedWindowShape(
        area=area,
        span25_sec=span25_sec,
        centroid_rt_min=centroid_rt_min,
    )


def _trace_metric(
    *,
    position_label: str,
    target_mz: float,
    trace: XICTrace,
    base_trace: XICTrace,
    base_raw_stats: tuple[int, float | None, float | None],
    base_stats: tuple[int, float | None, float | None],
    ppm: float,
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
) -> TraceMetric:
    point_count, raw_max_intensity, raw_apex_rt_min = _trace_stats(
        trace,
        intensity_basis="raw",
    )
    trace_rt, _trace_intensity = _finite_sorted_arrays(trace)
    median_rt_step_sec = _median_step_sec(trace_rt) if trace_rt.size >= 2 else None
    effective_smoothing_window_sec = _effective_smoothing_window_sec(
        point_count=point_count,
        median_rt_step_sec=median_rt_step_sec,
    )
    _base_raw_point_count, base_raw_max_intensity, base_raw_apex_rt_min = (
        base_raw_stats
    )
    _decision_point_count, max_intensity, apex_rt_min = _trace_stats(
        trace,
        intensity_basis="decision",
    )
    _base_point_count, base_max_intensity, base_apex_rt_min = base_stats
    rt_min = float(np.nanmin(trace.rt)) if point_count else None
    rt_max = float(np.nanmax(trace.rt)) if point_count else None

    if position_label == "base":
        raw_supported = raw_max_intensity is not None and raw_max_intensity > 0
        status = (
            "supported"
            if max_intensity and max_intensity > 0
            else "missing_signal"
        )
        return TraceMetric(
            position_label=position_label,
            target_mz=target_mz,
            rt_min=rt_min if rt_min is not None else math.nan,
            rt_max=rt_max if rt_max is not None else math.nan,
            ppm=ppm,
            point_count=point_count,
            median_rt_step_sec=median_rt_step_sec,
            effective_smoothing_window_sec=effective_smoothing_window_sec,
            raw_max_intensity=raw_max_intensity,
            raw_apex_rt_min=raw_apex_rt_min,
            raw_apex_delta_sec_to_base=0.0 if raw_apex_rt_min is not None else None,
            raw_smoothed_apex_delta_sec=(
                0.0
                if raw_apex_rt_min is not None and apex_rt_min is not None
                else None
            ),
            raw_height_ratio_to_base=1.0 if raw_supported else None,
            raw_pearson_r_to_base=1.0 if raw_supported else None,
            raw_active_overlap_fraction=1.0 if raw_supported else None,
            max_intensity=max_intensity,
            apex_rt_min=apex_rt_min,
            apex_delta_sec_to_base=0.0 if apex_rt_min is not None else None,
            height_ratio_to_base=1.0 if max_intensity and max_intensity > 0 else None,
            pearson_r_to_base=1.0 if max_intensity and max_intensity > 0 else None,
            active_overlap_fraction=(
                1.0 if max_intensity and max_intensity > 0 else None
            ),
            shifted_pearson_r_to_base=(
                1.0 if max_intensity and max_intensity > 0 else None
            ),
            shape_shift_sec_to_base=(
                0.0 if max_intensity and max_intensity > 0 else None
            ),
            trace_quality_flags=_quality_flags(
                point_count=point_count,
                effective_smoothing_window_sec=effective_smoothing_window_sec,
                raw_smoothed_apex_delta_sec=0.0
                if raw_apex_rt_min is not None and apex_rt_min is not None
                else None,
                position_label=position_label,
                reason=status,
                raw_pearson_r=1.0 if raw_supported else None,
                pearson_r=1.0 if max_intensity and max_intensity > 0 else None,
                shape_shift_sec=0.0 if max_intensity and max_intensity > 0 else None,
                min_pearson_r=min_pearson_r,
            ),
            trace_support_status=status,
            reason=status,
        )

    raw_pearson = _pearson_r_to_base(base_trace, trace, intensity_basis="raw")
    raw_active_overlap = _active_overlap_fraction(
        base_trace,
        trace,
        active_fraction=active_fraction,
        intensity_basis="raw",
    )
    raw_apex_delta = (
        (raw_apex_rt_min - base_raw_apex_rt_min) * 60.0
        if raw_apex_rt_min is not None and base_raw_apex_rt_min is not None
        else None
    )
    raw_smoothed_apex_delta = (
        (apex_rt_min - raw_apex_rt_min) * 60.0
        if raw_apex_rt_min is not None and apex_rt_min is not None
        else None
    )
    raw_height_ratio = (
        raw_max_intensity / base_raw_max_intensity
        if raw_max_intensity is not None
        and base_raw_max_intensity is not None
        and base_raw_max_intensity > 0
        else None
    )
    pearson = _pearson_r_to_base(base_trace, trace, intensity_basis="decision")
    active_overlap = _active_overlap_fraction(
        base_trace,
        trace,
        active_fraction=active_fraction,
        intensity_basis="decision",
    )
    shifted_pearson, shape_shift_sec = _shifted_pearson_r_to_base(
        base_trace,
        trace,
        intensity_basis="decision",
    )
    apex_delta = (
        (apex_rt_min - base_apex_rt_min) * 60.0
        if apex_rt_min is not None and base_apex_rt_min is not None
        else None
    )
    height_ratio = (
        max_intensity / base_max_intensity
        if max_intensity is not None
        and base_max_intensity is not None
        and base_max_intensity > 0
        else None
    )
    status, reason = _position_support_status(
        point_count=point_count,
        max_intensity=max_intensity,
        base_max_intensity=base_max_intensity,
        apex_delta_sec=apex_delta,
        pearson_r=pearson,
        active_overlap_fraction=active_overlap,
        shifted_pearson_r=shifted_pearson,
        min_pearson_r=min_pearson_r,
        max_apex_delta_sec=max_apex_delta_sec,
    )
    return TraceMetric(
        position_label=position_label,
        target_mz=target_mz,
        rt_min=rt_min if rt_min is not None else math.nan,
        rt_max=rt_max if rt_max is not None else math.nan,
        ppm=ppm,
        point_count=point_count,
        median_rt_step_sec=median_rt_step_sec,
        effective_smoothing_window_sec=effective_smoothing_window_sec,
        raw_max_intensity=raw_max_intensity,
        raw_apex_rt_min=raw_apex_rt_min,
        raw_apex_delta_sec_to_base=raw_apex_delta,
        raw_smoothed_apex_delta_sec=raw_smoothed_apex_delta,
        raw_height_ratio_to_base=raw_height_ratio,
        raw_pearson_r_to_base=raw_pearson,
        raw_active_overlap_fraction=raw_active_overlap,
        max_intensity=max_intensity,
        apex_rt_min=apex_rt_min,
        apex_delta_sec_to_base=apex_delta,
        height_ratio_to_base=height_ratio,
        pearson_r_to_base=pearson,
        active_overlap_fraction=active_overlap,
        shifted_pearson_r_to_base=shifted_pearson,
        shape_shift_sec_to_base=shape_shift_sec,
        trace_quality_flags=_quality_flags(
            point_count=point_count,
            effective_smoothing_window_sec=effective_smoothing_window_sec,
            raw_smoothed_apex_delta_sec=raw_smoothed_apex_delta,
            position_label=position_label,
            reason=reason,
            raw_pearson_r=raw_pearson,
            pearson_r=pearson,
            shape_shift_sec=shape_shift_sec,
            min_pearson_r=min_pearson_r,
        ),
        trace_support_status=status,
        reason=reason,
    )


def _trace_stats(
    trace: XICTrace,
    *,
    intensity_basis: Literal["raw", "decision"],
) -> tuple[int, float | None, float | None]:
    rt, intensity = _arrays_for_basis(trace, intensity_basis=intensity_basis)
    return _array_stats(rt, intensity)


def _array_stats(
    rt: NDArray[np.float64],
    intensity: NDArray[np.float64],
) -> tuple[int, float | None, float | None]:
    if rt.size == 0:
        return 0, None, None
    max_index = int(np.argmax(intensity))
    max_intensity = float(intensity[max_index])
    return int(rt.size), max_intensity, float(rt[max_index])


def _pearson_r_to_base(
    base_trace: XICTrace,
    trace: XICTrace,
    *,
    intensity_basis: Literal["raw", "decision"],
) -> float | None:
    base_rt, base_intensity = _arrays_for_basis(
        base_trace,
        intensity_basis=intensity_basis,
    )
    rt, intensity = _arrays_for_basis(trace, intensity_basis=intensity_basis)
    if base_rt.size < 2 or rt.size < 2:
        return None
    common_base_rt, common_base_intensity, common_intensity = _interpolate_to_base_grid(
        base_rt,
        base_intensity,
        rt,
        intensity,
    )
    if common_base_rt.size < 2:
        return None
    if float(np.std(common_base_intensity)) <= 0.0:
        return None
    if float(np.std(common_intensity)) <= 0.0:
        return None
    return float(np.corrcoef(common_base_intensity, common_intensity)[0, 1])


def _active_overlap_fraction(
    base_trace: XICTrace,
    trace: XICTrace,
    *,
    active_fraction: float,
    intensity_basis: Literal["raw", "decision"],
) -> float | None:
    base_rt, base_intensity = _arrays_for_basis(
        base_trace,
        intensity_basis=intensity_basis,
    )
    rt, intensity = _arrays_for_basis(trace, intensity_basis=intensity_basis)
    if base_rt.size == 0 or rt.size == 0:
        return None
    common_base_rt, common_base_intensity, common_intensity = _interpolate_to_base_grid(
        base_rt,
        base_intensity,
        rt,
        intensity,
    )
    if common_base_rt.size == 0:
        return None
    base_max = float(np.max(common_base_intensity))
    target_max = float(np.max(common_intensity))
    if base_max <= 0.0 or target_max <= 0.0:
        return None
    base_active = common_base_intensity >= base_max * active_fraction
    if not bool(np.any(base_active)):
        return None
    target_active = common_intensity >= target_max * active_fraction
    return float(
        np.count_nonzero(base_active & target_active) / np.count_nonzero(base_active)
    )


def _shifted_pearson_r_to_base(
    base_trace: XICTrace,
    trace: XICTrace,
    *,
    intensity_basis: Literal["raw", "decision"],
) -> tuple[float | None, float | None]:
    base_rt, base_intensity = _arrays_for_basis(
        base_trace,
        intensity_basis=intensity_basis,
    )
    rt, intensity = _arrays_for_basis(trace, intensity_basis=intensity_basis)
    if base_rt.size < 2 or rt.size < 2:
        return None, None
    common_base_rt, common_base_intensity, common_intensity = _interpolate_to_base_grid(
        base_rt,
        base_intensity,
        rt,
        intensity,
    )
    if common_base_rt.size < 2:
        return None, None
    step_sec = _median_step_sec(common_base_rt)
    best: tuple[float, int] | None = None
    min_points = max(2, int(math.ceil(common_base_rt.size * 0.40)))
    max_lag = common_base_rt.size - min_points
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            base_segment = common_base_intensity[:lag]
            target_segment = common_intensity[-lag:]
        elif lag > 0:
            base_segment = common_base_intensity[lag:]
            target_segment = common_intensity[:-lag]
        else:
            base_segment = common_base_intensity
            target_segment = common_intensity
        value = _pearson_arrays(base_segment, target_segment)
        if value is None:
            continue
        if best is None or value > best[0] or (
            math.isclose(value, best[0]) and abs(lag) < abs(best[1])
        ):
            best = (value, lag)
    if best is None:
        return None, None
    return best[0], best[1] * step_sec


def _arrays_for_basis(
    trace: XICTrace,
    *,
    intensity_basis: Literal["raw", "decision"],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    rt, intensity = _finite_sorted_arrays(trace)
    if intensity_basis == "raw" or intensity.size < 3:
        return rt, intensity
    smoothed: NDArray[np.float64] = np.asarray(
        gaussian_smooth_values(
            tuple(float(value) for value in intensity),
            points=TRACE_SMOOTH_WINDOW_POINTS,
        ),
        dtype=np.float64,
    )
    return rt, np.maximum(smoothed, 0.0)


def _interpolate_to_base_grid(
    base_rt: NDArray[np.float64],
    base_intensity: NDArray[np.float64],
    rt: NDArray[np.float64],
    intensity: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    start = max(float(base_rt[0]), float(rt[0]))
    stop = min(float(base_rt[-1]), float(rt[-1]))
    if start > stop:
        empty = np.array([], dtype=float)
        return empty, empty, empty
    mask = (base_rt >= start) & (base_rt <= stop)
    common_base_rt = base_rt[mask]
    common_base_intensity = base_intensity[mask]
    common_intensity = np.interp(common_base_rt, rt, intensity)
    return common_base_rt, common_base_intensity, common_intensity


def _pearson_arrays(
    left: NDArray[np.float64],
    right: NDArray[np.float64],
) -> float | None:
    if left.size < 2 or right.size < 2 or left.size != right.size:
        return None
    if float(np.std(left)) <= 0.0 or float(np.std(right)) <= 0.0:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _median_step_sec(rt: NDArray[np.float64]) -> float:
    if rt.size < 2:
        return 0.0
    diffs = np.diff(rt)
    finite_positive = diffs[np.isfinite(diffs) & (diffs > 0)]
    if finite_positive.size == 0:
        return 0.0
    return float(np.median(finite_positive) * 60.0)


def _effective_smoothing_window_sec(
    *,
    point_count: int,
    median_rt_step_sec: float | None,
) -> float | None:
    if (
        point_count < 3
        or median_rt_step_sec is None
        or median_rt_step_sec <= 0.0
    ):
        return None
    window = min(TRACE_SMOOTH_WINDOW_POINTS, point_count)
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return None
    return float(window * median_rt_step_sec)


def _finite_sorted_arrays(
    trace: XICTrace,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    finite = np.isfinite(trace.rt) & np.isfinite(trace.intensity)
    rt = trace.rt[finite]
    intensity = trace.intensity[finite]
    if rt.size == 0:
        empty = np.array([], dtype=float)
        return empty, empty
    order = np.argsort(rt)
    rt = rt[order]
    intensity = intensity[order]
    unique_rt, first_indexes = np.unique(rt, return_index=True)
    if unique_rt.size == rt.size:
        return rt, intensity
    reduced_intensity = np.empty_like(unique_rt)
    for index, start in enumerate(first_indexes):
        stop = first_indexes[index + 1] if index + 1 < first_indexes.size else rt.size
        reduced_intensity[index] = float(np.max(intensity[start:stop]))
    return unique_rt, reduced_intensity


def _position_support_status(
    *,
    point_count: int,
    max_intensity: float | None,
    base_max_intensity: float | None,
    apex_delta_sec: float | None,
    pearson_r: float | None,
    active_overlap_fraction: float | None,
    shifted_pearson_r: float | None,
    min_pearson_r: float,
    max_apex_delta_sec: float,
) -> tuple[str, str]:
    if point_count == 0:
        return "missing_points", "no_trace_points"
    if max_intensity is None or max_intensity <= 0.0:
        return "missing_signal", "no_positive_signal"
    if base_max_intensity is None or base_max_intensity <= 0.0:
        return "base_missing_signal", "base_has_no_positive_signal"
    if apex_delta_sec is None:
        return "review", "missing_apex_delta"
    if pearson_r is not None and pearson_r >= min_pearson_r:
        return "supported", "rt_and_shape_supported"
    if active_overlap_fraction is not None and active_overlap_fraction >= 0.80:
        return "supported", "rt_and_active_overlap_supported"
    if shifted_pearson_r is not None and shifted_pearson_r >= min_pearson_r:
        return "supported", "shifted_shape_supported"
    if abs(apex_delta_sec) > max_apex_delta_sec:
        return "review", "apex_rt_outside_threshold"
    return "review", "rt_supported_shape_review"


def _quality_flags(
    *,
    point_count: int,
    effective_smoothing_window_sec: float | None,
    raw_smoothed_apex_delta_sec: float | None,
    position_label: str,
    reason: str,
    raw_pearson_r: float | None,
    pearson_r: float | None,
    shape_shift_sec: float | None,
    min_pearson_r: float,
) -> str:
    flags: list[str] = []
    if 0 < point_count < LOW_SCAN_DENSITY_POINT_COUNT:
        flags.append("low_scan_density")
    if (
        point_count >= TRACE_SMOOTH_WINDOW_POINTS
        and effective_smoothing_window_sec is not None
        and effective_smoothing_window_sec < MIN_EFFECTIVE_SMOOTHING_WINDOW_SEC
    ):
        flags.append("short_effective_smoothing_window")
    if (
        raw_smoothed_apex_delta_sec is not None
        and abs(raw_smoothed_apex_delta_sec) > RAW_SMOOTHED_APEX_MISMATCH_SEC
    ):
        flags.append("raw_smoothed_apex_mismatch")
    if reason == "shifted_shape_supported":
        flags.append("shifted_only_support")
        if raw_pearson_r is None or raw_pearson_r < min_pearson_r:
            flags.append("raw_shape_weak_for_shifted_support")
        if pearson_r is None or pearson_r < min_pearson_r:
            flags.append("smoothed_shape_weak_for_shifted_support")
        if (
            shape_shift_sec is not None
            and abs(shape_shift_sec) > LARGE_SHAPE_SHIFT_SEC
        ):
            flags.append("large_shape_shift")
        if position_label == "pt_plus_4":
            flags.append("plus4_shifted_only_support")
    return _join_flags(flags)


def _summary_ambiguity(
    non_base_metrics: list[TraceMetric],
) -> tuple[str, str]:
    flags: set[str] = set()
    shifted_only_positions: list[str] = []
    shifted_values: list[float] = []
    for metric in non_base_metrics:
        metric_flags = _split_flags(metric.trace_quality_flags)
        if "low_scan_density" in metric_flags:
            flags.add("low_scan_density")
        if "short_effective_smoothing_window" in metric_flags:
            flags.add("short_effective_smoothing_window")
        if "raw_smoothed_apex_mismatch" in metric_flags:
            flags.add("raw_smoothed_apex_mismatch")
        if "shifted_only_support" in metric_flags:
            flags.add("shifted_only_support_present")
            shifted_only_positions.append(metric.position_label)
        if "raw_shape_weak_for_shifted_support" in metric_flags:
            flags.add("raw_shape_weak_for_shifted_support")
        if "smoothed_shape_weak_for_shifted_support" in metric_flags:
            flags.add("smoothed_shape_weak_for_shifted_support")
        if "plus4_shifted_only_support" in metric_flags:
            flags.add("plus4_shifted_only_support")
        if "plus4_window_tail_dominance" in metric_flags:
            flags.add("plus4_window_tail_dominance")
        if "large_shape_shift" in metric_flags:
            flags.add("large_shape_shift")
        if metric.shape_shift_sec_to_base is not None:
            shifted_values.append(metric.shape_shift_sec_to_base)

    if _has_mixed_shift_direction(shifted_values):
        flags.add("position_shift_direction_mixed")
    if _has_isolated_position_shift(non_base_metrics):
        flags.add("isolated_position_shift")
    if shifted_only_positions:
        flags.add(
            "shifted_only_positions="
            + ",".join(sorted(set(shifted_only_positions)))
        )

    joined = _join_flags(flags)
    status = "review_required" if joined != TRACE_FLAG_NONE else "clear"
    return status, joined


def _shifted_only_position_labels(non_base_metrics: list[TraceMetric]) -> str:
    labels = [
        metric.position_label
        for metric in non_base_metrics
        if "shifted_only_support" in _split_flags(metric.trace_quality_flags)
    ]
    return ",".join(labels) if labels else TRACE_FLAG_NONE


def _has_mixed_shift_direction(values: list[float]) -> bool:
    positive = any(value > 0 for value in values)
    negative = any(value < 0 for value in values)
    return positive and negative


def _has_isolated_position_shift(non_base_metrics: list[TraceMetric]) -> bool:
    shifts = [
        metric.shape_shift_sec_to_base
        for metric in non_base_metrics
        if metric.shape_shift_sec_to_base is not None
    ]
    if len(shifts) < 3:
        return False
    for index, value in enumerate(shifts):
        others = shifts[:index] + shifts[index + 1 :]
        if abs(value) <= LARGE_SHAPE_SHIFT_SEC:
            continue
        if all(abs(other) <= RAW_SMOOTHED_APEX_MISMATCH_SEC for other in others):
            return True
    return False


def _split_flags(flags: str) -> set[str]:
    if not flags or flags == TRACE_FLAG_NONE:
        return set()
    return {flag for flag in flags.split(";") if flag}


def _join_flags(flags: list[str] | set[str]) -> str:
    unique = sorted(set(flags))
    return ";".join(unique) if unique else TRACE_FLAG_NONE


def _summary_status(
    *,
    supported_count: int,
    positions_with_signal: int,
    base_stats: tuple[int, float | None, float | None],
) -> tuple[str, str]:
    _point_count, base_max_intensity, _base_apex = base_stats
    if base_max_intensity is None or base_max_intensity <= 0:
        return "base_missing_signal", "base_has_no_positive_signal"
    if positions_with_signal < len(POSITION_LABELS):
        return "incomplete_trace_signal", "one_or_more_positions_missing_signal"
    if supported_count == len(NON_BASE_POSITION_LABELS):
        return "supported", "all_non_base_positions_trace_supported"
    if supported_count > 0:
        return "partial_support", "some_non_base_positions_trace_supported"
    return "review", "no_non_base_positions_trace_supported"


def _combined_pt_pattern_status(trace_support_status: str) -> str:
    if trace_support_status == "supported":
        return "pt_pattern_detected_diagnostic"
    if trace_support_status == "partial_support":
        return "pt_pattern_candidate_partial"
    if trace_support_status == "incomplete_trace_signal":
        return "pt_pattern_candidate_incomplete"
    if trace_support_status == "base_missing_signal":
        return "pt_pattern_candidate_base_missing"
    return "pt_pattern_candidate_review"


def _metric_row(
    metric: TraceMetric,
    *,
    sample_stem: str,
    candidate_id: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_stem": sample_stem,
        "candidate_id": candidate_id,
        "position_label": metric.position_label,
        "target_mz": metric.target_mz,
        "rt_min": metric.rt_min,
        "rt_max": metric.rt_max,
        "ppm": metric.ppm,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "trace_smoothing_method": TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": TRACE_SMOOTH_WINDOW_POINTS,
        "point_count": metric.point_count,
        "median_rt_step_sec": metric.median_rt_step_sec,
        "effective_smoothing_window_sec": metric.effective_smoothing_window_sec,
        "raw_max_intensity": metric.raw_max_intensity,
        "raw_apex_rt_min": metric.raw_apex_rt_min,
        "raw_apex_delta_sec_to_base": metric.raw_apex_delta_sec_to_base,
        "raw_smoothed_apex_delta_sec": metric.raw_smoothed_apex_delta_sec,
        "raw_height_ratio_to_base": metric.raw_height_ratio_to_base,
        "raw_pearson_r_to_base": metric.raw_pearson_r_to_base,
        "raw_active_overlap_fraction": metric.raw_active_overlap_fraction,
        "max_intensity": metric.max_intensity,
        "apex_rt_min": metric.apex_rt_min,
        "apex_delta_sec_to_base": metric.apex_delta_sec_to_base,
        "height_ratio_to_base": metric.height_ratio_to_base,
        "pearson_r_to_base": metric.pearson_r_to_base,
        "active_overlap_fraction": metric.active_overlap_fraction,
        "shifted_pearson_r_to_base": metric.shifted_pearson_r_to_base,
        "shape_shift_sec_to_base": metric.shape_shift_sec_to_base,
        "trace_quality_flags": metric.trace_quality_flags,
        "trace_support_status": metric.trace_support_status,
        "reason": metric.reason,
    }


def _trace_point_rows(
    *,
    sample_stem: str,
    candidate_id: str,
    trace_by_position: Mapping[str, XICTrace],
) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for position_label in POSITION_LABELS:
        trace = trace_by_position.get(position_label, XICTrace.empty())
        rt, intensity = _finite_sorted_arrays(trace)
        rows.extend(
            {
                "schema_version": SCHEMA_VERSION,
                "sample_stem": sample_stem,
                "candidate_id": candidate_id,
                "position_label": position_label,
                "intensity_basis": RAW_TRACE_BASIS,
                "rt_min": float(rt_value),
                "intensity": float(intensity_value),
            }
            for rt_value, intensity_value in zip(rt, intensity, strict=True)
        )
    return tuple(rows)


def _position_target_mz(
    row: Mapping[str, str],
    *,
    position_label: str,
    mz_source: Literal["expected", "observed"],
    label: str,
) -> float:
    if position_label == "base":
        return _required_float(row, "base_mz", label=label)
    observed_field = f"{position_label}_mz"
    expected_field = f"{position_label}_expected_mz"
    primary = expected_field if mz_source == "expected" else observed_field
    fallback = observed_field if mz_source == "expected" else expected_field
    primary_value = optional_float(row.get(primary))
    if primary_value is not None:
        return primary_value
    fallback_value = optional_float(row.get(fallback))
    if fallback_value is not None:
        return fallback_value
    raise ValueError(
        f"{label}: missing numeric m/z for {position_label} "
        f"({primary!r} / {fallback!r})"
    )


def _required_text(row: Mapping[str, str], field: str) -> str:
    text = str(row.get(field, "")).strip()
    if not text:
        raise ValueError(f"missing required field {field!r}")
    return text


def _required_float(row: Mapping[str, str], field: str, *, label: str) -> float:
    value = optional_float(row.get(field))
    if value is None:
        raise ValueError(f"{label}: missing or invalid numeric field {field!r}")
    return value
