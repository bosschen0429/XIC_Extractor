"""Trace-level Pt pattern evidence helpers.

This module owns the reusable chromatography evidence semantics for
``delta_mass(pattern_id=pt)`` candidates. It does not open RAW files, write
diagnostic outputs, or grant final quant authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
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
    "positions_requested",
    "positions_with_points",
    "positions_with_signal",
    "non_base_positions_supported",
    "max_abs_apex_delta_sec",
    "min_pearson_r_to_base",
    "min_active_overlap_fraction",
    "raw_max_abs_apex_delta_sec",
    "raw_min_pearson_r_to_base",
    "raw_min_active_overlap_fraction",
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
    "raw_max_intensity",
    "raw_apex_rt_min",
    "raw_apex_delta_sec_to_base",
    "raw_height_ratio_to_base",
    "raw_pearson_r_to_base",
    "raw_active_overlap_fraction",
    "max_intensity",
    "apex_rt_min",
    "apex_delta_sec_to_base",
    "height_ratio_to_base",
    "pearson_r_to_base",
    "active_overlap_fraction",
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
    raw_max_intensity: float | None
    raw_apex_rt_min: float | None
    raw_apex_delta_sec_to_base: float | None
    raw_height_ratio_to_base: float | None
    raw_pearson_r_to_base: float | None
    raw_active_overlap_fraction: float | None
    max_intensity: float | None
    apex_rt_min: float | None
    apex_delta_sec_to_base: float | None
    height_ratio_to_base: float | None
    pearson_r_to_base: float | None
    active_overlap_fraction: float | None
    trace_support_status: str
    reason: str


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

    non_base_metrics = [
        metric
        for metric in metrics
        if metric.position_label in NON_BASE_POSITION_LABELS
    ]
    supported_count = sum(
        metric.trace_support_status == "supported" for metric in non_base_metrics
    )
    positions_with_points = sum(metric.point_count > 0 for metric in metrics)
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
            "positions_requested": len(POSITION_LABELS),
            "positions_with_points": positions_with_points,
            "positions_with_signal": positions_with_signal,
            "non_base_positions_supported": supported_count,
            "max_abs_apex_delta_sec": max(apex_deltas) if apex_deltas else None,
            "min_pearson_r_to_base": min(correlations) if correlations else None,
            "min_active_overlap_fraction": min(overlaps) if overlaps else None,
            "raw_max_abs_apex_delta_sec": (
                max(raw_apex_deltas) if raw_apex_deltas else None
            ),
            "raw_min_pearson_r_to_base": (
                min(raw_correlations) if raw_correlations else None
            ),
            "raw_min_active_overlap_fraction": (
                min(raw_overlaps) if raw_overlaps else None
            ),
            "trace_support_status": summary_status,
            "combined_pt_pattern_status": _combined_pt_pattern_status(summary_status),
            "reason": summary_reason,
        },
        metric_rows=metric_rows,
        trace_point_rows=trace_point_rows,
    )


def combined_pt_pattern_status(trace_support_status: str) -> str:
    return _combined_pt_pattern_status(trace_support_status)


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
            raw_max_intensity=raw_max_intensity,
            raw_apex_rt_min=raw_apex_rt_min,
            raw_apex_delta_sec_to_base=0.0 if raw_apex_rt_min is not None else None,
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
        raw_max_intensity=raw_max_intensity,
        raw_apex_rt_min=raw_apex_rt_min,
        raw_apex_delta_sec_to_base=raw_apex_delta,
        raw_height_ratio_to_base=raw_height_ratio,
        raw_pearson_r_to_base=raw_pearson,
        raw_active_overlap_fraction=raw_active_overlap,
        max_intensity=max_intensity,
        apex_rt_min=apex_rt_min,
        apex_delta_sec_to_base=apex_delta,
        height_ratio_to_base=height_ratio,
        pearson_r_to_base=pearson,
        active_overlap_fraction=active_overlap,
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
    if abs(apex_delta_sec) > max_apex_delta_sec:
        return "review", "apex_rt_outside_threshold"
    if pearson_r is not None and pearson_r >= min_pearson_r:
        return "supported", "rt_and_shape_supported"
    if active_overlap_fraction is not None and active_overlap_fraction >= 0.80:
        return "supported", "rt_and_active_overlap_supported"
    return "review", "rt_supported_shape_review"


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
        "raw_max_intensity": metric.raw_max_intensity,
        "raw_apex_rt_min": metric.raw_apex_rt_min,
        "raw_apex_delta_sec_to_base": metric.raw_apex_delta_sec_to_base,
        "raw_height_ratio_to_base": metric.raw_height_ratio_to_base,
        "raw_pearson_r_to_base": metric.raw_pearson_r_to_base,
        "raw_active_overlap_fraction": metric.raw_active_overlap_fraction,
        "max_intensity": metric.max_intensity,
        "apex_rt_min": metric.apex_rt_min,
        "apex_delta_sec_to_base": metric.apex_delta_sec_to_base,
        "height_ratio_to_base": metric.height_ratio_to_base,
        "pearson_r_to_base": metric.pearson_r_to_base,
        "active_overlap_fraction": metric.active_overlap_fraction,
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
