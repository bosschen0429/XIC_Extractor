"""Trace-level Pt pattern evidence helpers.

This module owns the reusable chromatography evidence semantics for
``delta_mass(pattern_id=pt)`` candidates. It does not open RAW files, write
diagnostic outputs, or grant final quant authority.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from xic_extractor.discovery.pt_pattern_artifact_contract import (
    PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS,
    PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS,
    PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS,
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_RAW_TRACE_BASIS,
    PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
    PT_PATTERN_TRACE_METRIC_COLUMNS,
    PT_PATTERN_TRACE_POINT_COLUMNS,
    PT_PATTERN_TRACE_SUMMARY_COLUMNS,
    PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION,
    pt_pattern_candidate_assignment_fingerprint,
)
from xic_extractor.peak_detection.ms1_shape_identity import (
    DEFAULT_OWN_MAX_SMOOTH_POINTS,
    gaussian_smooth_values,
)
from xic_extractor.tabular_io import (
    format_diagnostic_value,
    optional_float,
    read_delimited_rows,
)
from xic_extractor.xic_models import XICRequest, XICTrace

ANCHOR_SEGMENTATION_SUMMARY_COLUMNS = PT_PATTERN_ANCHOR_SEGMENTATION_SUMMARY_COLUMNS
ANCHOR_SEGMENTATION_METRIC_COLUMNS = PT_PATTERN_ANCHOR_SEGMENTATION_METRIC_COLUMNS
SUMMARY_COLUMNS = PT_PATTERN_TRACE_SUMMARY_COLUMNS
METRIC_COLUMNS = PT_PATTERN_TRACE_METRIC_COLUMNS
TRACE_POINT_COLUMNS = PT_PATTERN_TRACE_POINT_COLUMNS
RAW_TRACE_BASIS = PT_PATTERN_RAW_TRACE_BASIS

SCHEMA_VERSION = PT_PATTERN_TRACE_SUMMARY_SCHEMA_VERSION
EVIDENCE_OWNER = "xic_extractor.discovery.pt_pattern_trace_evidence"
READINESS_TIER = "diagnostic_only"
TRACE_DECISION_BASIS = "gaussian15_smoothed_trace"
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
REQUIRED_CANDIDATE_COLUMNS = PT_PATTERN_CANDIDATE_ASSIGNMENT_COLUMNS


TRACE_SUMMARY_AGGREGATE_COLUMNS = (
    "min_median_rt_step_sec",
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


TRACE_POINT_PROJECTED_METRIC_COLUMNS = (
    "rt_min",
    "rt_max",
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

TRACE_LINEAGE_COLUMNS = (
    PT_PATTERN_CANDIDATE_ASSIGNMENT_SHA256_FIELD,
    PT_PATTERN_TRACE_EXTRACTION_SHA256_FIELD,
    PT_PATTERN_TRACE_ARTIFACT_ROLE_FIELD,
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
    seen_candidate_ids: set[str] = set()
    seen_canonical_bases: set[tuple[str, str]] = set()
    for row_index, row in enumerate(rows, start=2):
        candidate_id = _required_text(row, "candidate_id")
        if candidate_id in seen_candidate_ids:
            raise ValueError(
                f"{path}:{row_index}: duplicate candidate_id: {candidate_id}"
            )
        seen_candidate_ids.add(candidate_id)
        pt_pattern_candidate_assignment_fingerprint(row)
        canonical_base = (
            _required_text(row, "sample_stem"),
            _required_text(row, "base_feature_id"),
        )
        if canonical_base in seen_canonical_bases:
            raise ValueError(
                f"{path}:{row_index}: duplicate canonical Pt base assignment: "
                f"sample_stem={canonical_base[0]!r}, "
                f"base_feature_id={canonical_base[1]!r}"
            )
        seen_canonical_bases.add(canonical_base)
    return tuple(rows[offset : offset + limit])


def validate_candidate_sample_binding(
    rows: Sequence[Mapping[str, str]],
    *,
    expected_sample_stem: str,
) -> None:
    """Fail closed when candidate rows do not belong to the evidence source."""

    expected = expected_sample_stem.strip()
    if not expected:
        raise ValueError("source sample stem must be non-empty")
    observed = sorted({_required_text(row, "sample_stem") for row in rows})
    if observed and observed != [expected]:
        raise ValueError(
            f"candidate sample stems {observed!r} do not match source sample "
            f"{expected!r}"
        )


def build_trace_requests_for_candidate(
    row: Mapping[str, str],
    *,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
) -> tuple[TraceRequestItem, ...]:
    pt_pattern_candidate_assignment_fingerprint(row)
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
    trace_extraction_sha256: str,
    trace_artifact_role: Literal["full_context", "anchor_local"],
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
    candidate_assignment_sha256 = pt_pattern_candidate_assignment_fingerprint(row)
    if re.fullmatch(r"[0-9a-f]{64}", trace_extraction_sha256) is None:
        raise ValueError("trace_extraction_sha256 must be a lowercase SHA-256 digest")
    if trace_artifact_role not in {
        PT_PATTERN_TRACE_FULL_CONTEXT_ROLE,
        PT_PATTERN_TRACE_ANCHOR_LOCAL_ROLE,
    }:
        raise ValueError("trace_artifact_role must be 'full_context' or 'anchor_local'")
    base_mz = _required_float(row, "base_mz", label=candidate_id)
    base_rt = _required_float(row, "base_rt_apex_min", label=candidate_id)

    trace_by_position = {
        position_label: _csv_precision_trace(
            trace_by_position.get(position_label, XICTrace.empty())
        )
        for position_label in POSITION_LABELS
    }
    base_trace = trace_by_position["base"]
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
    metric_rows = tuple(
        _metric_row(
            metric,
            sample_stem=sample_stem,
            candidate_id=candidate_id,
            candidate_assignment_sha256=candidate_assignment_sha256,
            trace_extraction_sha256=trace_extraction_sha256,
            trace_artifact_role=trace_artifact_role,
        )
        for metric in metrics
    )
    metric_rows = _with_serialized_metric_call_projection(
        metric_rows,
        min_pearson_r=min_pearson_r,
        max_apex_delta_sec=max_apex_delta_sec,
    )
    summary_aggregate = trace_summary_aggregate_fields(
        tuple(
            {
                field: format_diagnostic_value(value)
                for field, value in metric_row.items()
            }
            for metric_row in metric_rows
        )
    )
    trace_point_rows = (
        _trace_point_rows(
            sample_stem=sample_stem,
            candidate_id=candidate_id,
            candidate_assignment_sha256=candidate_assignment_sha256,
            trace_extraction_sha256=trace_extraction_sha256,
            trace_artifact_role=trace_artifact_role,
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
            "candidate_assignment_sha256": candidate_assignment_sha256,
            "trace_extraction_sha256": trace_extraction_sha256,
            "trace_artifact_role": trace_artifact_role,
            "scan_pattern_seed_status": "full_pattern_seeded",
            "base_mz": base_mz,
            "base_rt_apex_min": base_rt,
            "rt_half_window_sec": rt_half_window_sec,
            "ppm": ppm,
            "mz_source": mz_source,
            "trace_decision_basis": TRACE_DECISION_BASIS,
            "trace_smoothing_method": TRACE_SMOOTH_METHOD,
            "trace_smoothing_window_points": TRACE_SMOOTH_WINDOW_POINTS,
            **summary_aggregate,
        },
        metric_rows=metric_rows,
        trace_point_rows=trace_point_rows,
    )


def trace_metric_evidence_fields_from_points(
    metric_rows: Sequence[Mapping[str, object]],
    trace_point_rows: Sequence[Mapping[str, object]],
    *,
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
) -> tuple[dict[str, str], ...]:
    """Rebuild every point-derived metric field from persisted raw traces."""

    metrics_by_candidate: dict[str, dict[str, Mapping[str, object]]] = {}
    candidate_order: list[str] = []
    for row in metric_rows:
        candidate_id = _required_metric_text(row, "candidate_id")
        position_label = _required_metric_text(row, "position_label")
        if candidate_id not in metrics_by_candidate:
            metrics_by_candidate[candidate_id] = {}
            candidate_order.append(candidate_id)
        if position_label in metrics_by_candidate[candidate_id]:
            raise ValueError(
                f"duplicate trace metric candidate/position: "
                f"{candidate_id}:{position_label}"
            )
        metrics_by_candidate[candidate_id][position_label] = row

    points_by_pair: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in trace_point_rows:
        candidate_id = _required_metric_text(row, "candidate_id")
        position_label = _required_metric_text(row, "position_label")
        pair = (candidate_id, position_label)
        if (
            candidate_id not in metrics_by_candidate
            or position_label not in metrics_by_candidate[candidate_id]
        ):
            raise ValueError(
                "trace points contain a candidate/position outside metrics"
            )
        rt_min = _optional_finite_metric_float(row, "rt_min")
        intensity = _optional_finite_metric_float(row, "intensity")
        if rt_min is None or intensity is None or rt_min < 0.0 or intensity < 0.0:
            raise ValueError(
                "trace point projection requires finite non-negative rt/intensity"
            )
        points_by_pair.setdefault(pair, []).append((rt_min, intensity))

    output_by_pair: dict[tuple[str, str], dict[str, str]] = {}
    for candidate_id in candidate_order:
        rows_by_position = metrics_by_candidate[candidate_id]
        if set(rows_by_position) != set(POSITION_LABELS):
            raise ValueError(
                "trace point projection requires exactly one metric row for "
                "every Pt position"
            )
        trace_by_position = {
            position_label: XICTrace.from_arrays(
                [
                    rt_min
                    for rt_min, _intensity in points_by_pair.get(
                        (candidate_id, position_label),
                        (),
                    )
                ],
                [
                    intensity
                    for _rt_min, intensity in points_by_pair.get(
                        (candidate_id, position_label),
                        (),
                    )
                ],
            )
            for position_label in POSITION_LABELS
        }
        base_trace = trace_by_position["base"]
        base_raw_stats = _trace_stats(base_trace, intensity_basis="raw")
        base_stats = _trace_stats(base_trace, intensity_basis="decision")
        projected_metrics = [
            _trace_metric(
                position_label=position_label,
                target_mz=_required_positive_metric_float(
                    rows_by_position[position_label],
                    "target_mz",
                ),
                trace=trace_by_position[position_label],
                base_trace=base_trace,
                base_raw_stats=base_raw_stats,
                base_stats=base_stats,
                ppm=_required_positive_metric_float(
                    rows_by_position[position_label],
                    "ppm",
                ),
                active_fraction=active_fraction,
                min_pearson_r=min_pearson_r,
                max_apex_delta_sec=max_apex_delta_sec,
            )
            for position_label in POSITION_LABELS
        ]
        projected_metrics = _with_family_quality_flags(
            projected_metrics,
            trace_by_position,
        )
        projected_rows = _with_serialized_metric_call_projection(
            tuple(
                _metric_row(
                    metric,
                    sample_stem=_required_metric_text(
                        rows_by_position[metric.position_label],
                        "sample_stem",
                    ),
                    candidate_id=candidate_id,
                    candidate_assignment_sha256=_required_metric_text(
                        rows_by_position[metric.position_label],
                        "candidate_assignment_sha256",
                    ),
                    trace_extraction_sha256=_required_metric_text(
                        rows_by_position[metric.position_label],
                        "trace_extraction_sha256",
                    ),
                    trace_artifact_role=_required_metric_text(
                        rows_by_position[metric.position_label],
                        "trace_artifact_role",
                    ),
                )
                for metric in projected_metrics
            ),
            min_pearson_r=min_pearson_r,
            max_apex_delta_sec=max_apex_delta_sec,
        )
        for row in projected_rows:
            pair = (candidate_id, str(row["position_label"]))
            output_by_pair[pair] = {
                field: format_diagnostic_value(row.get(field))
                for field in TRACE_POINT_PROJECTED_METRIC_COLUMNS
            }

    return tuple(
        output_by_pair[
            (
                _required_metric_text(row, "candidate_id"),
                _required_metric_text(row, "position_label"),
            )
        ]
        for row in metric_rows
    )


def combined_pt_pattern_status(trace_support_status: str) -> str:
    return _combined_pt_pattern_status(trace_support_status)


def trace_summary_aggregate_fields(
    metric_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Rebuild producer-owned summary aggregates from one candidate's metrics."""

    by_position: dict[str, Mapping[str, object]] = {}
    for row in metric_rows:
        position_label = str(row.get("position_label", "")).strip()
        if position_label not in POSITION_LABELS:
            raise ValueError(
                f"unsupported trace metric position_label {position_label!r}"
            )
        if position_label in by_position:
            raise ValueError(
                f"duplicate trace metric position_label {position_label!r}"
            )
        by_position[position_label] = row
    if set(by_position) != set(POSITION_LABELS):
        raise ValueError(
            "trace summary projection requires exactly one metric row for "
            "every Pt position"
        )

    ordered_rows = [by_position[label] for label in POSITION_LABELS]
    non_base_rows = [by_position[label] for label in NON_BASE_POSITION_LABELS]
    point_counts = [
        _required_non_negative_metric_int(row, "point_count") for row in ordered_rows
    ]
    support_statuses = [
        _required_metric_text(row, "trace_support_status") for row in ordered_rows
    ]
    allowed_support_statuses = {
        "supported",
        "missing_points",
        "missing_signal",
        "base_missing_signal",
        "review",
    }
    if not set(support_statuses).issubset(allowed_support_statuses):
        raise ValueError("trace metric contains unsupported trace_support_status")

    max_intensities = [
        _optional_finite_metric_float(row, "max_intensity") for row in ordered_rows
    ]
    supported_count = sum(
        _required_metric_text(row, "trace_support_status") == "supported"
        for row in non_base_rows
    )
    positions_with_signal = sum(
        value is not None and value > 0.0 for value in max_intensities
    )
    base_max_intensity = max_intensities[0]
    if base_max_intensity is None or base_max_intensity <= 0.0:
        summary_status = "base_missing_signal"
        summary_reason = "base_has_no_positive_signal"
    elif positions_with_signal < len(POSITION_LABELS):
        summary_status = "incomplete_trace_signal"
        summary_reason = "one_or_more_positions_missing_signal"
    elif supported_count == len(NON_BASE_POSITION_LABELS):
        summary_status = "supported"
        summary_reason = "all_non_base_positions_trace_supported"
    elif supported_count > 0:
        summary_status = "partial_support"
        summary_reason = "some_non_base_positions_trace_supported"
    else:
        summary_status = "review"
        summary_reason = "no_non_base_positions_trace_supported"

    trace_ambiguity_status, trace_ambiguity_flags = _summary_ambiguity_from_metric_rows(
        non_base_rows
    )
    shifted_only_position_labels = _shifted_only_labels_from_metric_rows(non_base_rows)
    return {
        "min_median_rt_step_sec": _min_optional_metric_value(
            ordered_rows,
            "median_rt_step_sec",
            positive_only=True,
        ),
        "min_effective_smoothing_window_sec": _min_optional_metric_value(
            ordered_rows,
            "effective_smoothing_window_sec",
        ),
        "max_trace_point_count": max(point_counts),
        "positions_requested": len(POSITION_LABELS),
        "positions_with_points": sum(value > 0 for value in point_counts),
        "positions_with_signal": positions_with_signal,
        "non_base_positions_supported": supported_count,
        "max_abs_apex_delta_sec": _max_abs_optional_metric_value(
            non_base_rows,
            "apex_delta_sec_to_base",
        ),
        "min_pearson_r_to_base": _min_optional_metric_value(
            non_base_rows,
            "pearson_r_to_base",
        ),
        "min_active_overlap_fraction": _min_optional_metric_value(
            non_base_rows,
            "active_overlap_fraction",
        ),
        "min_shifted_pearson_r_to_base": _min_optional_metric_value(
            non_base_rows,
            "shifted_pearson_r_to_base",
        ),
        "max_abs_shape_shift_sec_to_base": _max_abs_optional_metric_value(
            non_base_rows,
            "shape_shift_sec_to_base",
        ),
        "raw_max_abs_apex_delta_sec": _max_abs_optional_metric_value(
            non_base_rows,
            "raw_apex_delta_sec_to_base",
        ),
        "raw_min_pearson_r_to_base": _min_optional_metric_value(
            non_base_rows,
            "raw_pearson_r_to_base",
        ),
        "raw_min_active_overlap_fraction": _min_optional_metric_value(
            non_base_rows,
            "raw_active_overlap_fraction",
        ),
        "trace_ambiguity_status": trace_ambiguity_status,
        "trace_ambiguity_flags": trace_ambiguity_flags,
        "shifted_only_position_labels": shifted_only_position_labels,
        "trace_support_status": summary_status,
        "combined_pt_pattern_status": _combined_pt_pattern_status(summary_status),
        "reason": summary_reason,
    }


def trace_metric_call_fields(
    metric_row: Mapping[str, object],
    *,
    base_metric_row: Mapping[str, object],
    min_pearson_r: float,
    max_apex_delta_sec: float,
) -> dict[str, str]:
    """Rebuild one producer-owned metric call from serialized scalar evidence."""

    position_label = _required_metric_text(metric_row, "position_label")
    point_count = _required_non_negative_metric_int(metric_row, "point_count")
    max_intensity = _optional_finite_metric_float(metric_row, "max_intensity")
    if position_label == "base":
        status = (
            "supported"
            if max_intensity is not None and max_intensity > 0.0
            else "missing_signal"
        )
        reason = status
    else:
        status, reason = _position_support_status(
            point_count=point_count,
            max_intensity=max_intensity,
            base_max_intensity=_optional_finite_metric_float(
                base_metric_row,
                "max_intensity",
            ),
            apex_delta_sec=_optional_finite_metric_float(
                metric_row,
                "apex_delta_sec_to_base",
            ),
            pearson_r=_optional_finite_metric_float(
                metric_row,
                "pearson_r_to_base",
            ),
            active_overlap_fraction=_optional_finite_metric_float(
                metric_row,
                "active_overlap_fraction",
            ),
            shifted_pearson_r=_optional_finite_metric_float(
                metric_row,
                "shifted_pearson_r_to_base",
            ),
            min_pearson_r=min_pearson_r,
            max_apex_delta_sec=max_apex_delta_sec,
        )
    flags = _split_flags(
        _quality_flags(
            point_count=point_count,
            effective_smoothing_window_sec=_optional_finite_metric_float(
                metric_row,
                "effective_smoothing_window_sec",
            ),
            raw_smoothed_apex_delta_sec=_optional_finite_metric_float(
                metric_row,
                "raw_smoothed_apex_delta_sec",
            ),
            position_label=position_label,
            reason=reason,
            raw_pearson_r=_optional_finite_metric_float(
                metric_row,
                "raw_pearson_r_to_base",
            ),
            pearson_r=_optional_finite_metric_float(
                metric_row,
                "pearson_r_to_base",
            ),
            shape_shift_sec=_optional_finite_metric_float(
                metric_row,
                "shape_shift_sec_to_base",
            ),
            min_pearson_r=min_pearson_r,
        )
    )
    existing_flags = _split_flags(str(metric_row.get("trace_quality_flags", "")))
    if (
        position_label == "pt_plus_4"
        and "plus4_window_tail_dominance" in existing_flags
    ):
        # This family-level flag is derived from the complete trace window,
        # not from this scalar metric row. Preserve it while rebuilding every
        # scalar-reconstructable flag.
        flags.add("plus4_window_tail_dominance")
    return {
        "trace_quality_flags": _join_flags(flags),
        "trace_support_status": status,
        "reason": reason,
    }


def _with_serialized_metric_call_projection(
    metric_rows: Sequence[Mapping[str, object]],
    *,
    min_pearson_r: float,
    max_apex_delta_sec: float,
) -> tuple[dict[str, object], ...]:
    serialized_rows = tuple(
        {field: format_diagnostic_value(value) for field, value in row.items()}
        for row in metric_rows
    )
    base_row = next(
        row for row in serialized_rows if str(row.get("position_label", "")) == "base"
    )
    output: list[dict[str, object]] = []
    for original_row, serialized_row in zip(
        metric_rows,
        serialized_rows,
        strict=True,
    ):
        output.append(
            dict(original_row)
            | trace_metric_call_fields(
                serialized_row,
                base_metric_row=base_row,
                min_pearson_r=min_pearson_r,
                max_apex_delta_sec=max_apex_delta_sec,
            )
        )
    return tuple(output)


def _required_metric_text(row: Mapping[str, object], field: str) -> str:
    value = str(row.get(field, "")).strip()
    if not value:
        raise ValueError(f"trace metric {field} must be non-empty")
    return value


def _required_non_negative_metric_int(
    row: Mapping[str, object],
    field: str,
) -> int:
    value = _optional_finite_metric_float(row, field)
    if value is None or value < 0.0 or not value.is_integer():
        raise ValueError(f"trace metric {field} must be a non-negative integer")
    return int(value)


def _required_positive_metric_float(
    row: Mapping[str, object],
    field: str,
) -> float:
    value = _optional_finite_metric_float(row, field)
    if value is None or value <= 0.0:
        raise ValueError(f"trace metric {field} must be a positive finite number")
    return value


def _optional_finite_metric_float(
    row: Mapping[str, object],
    field: str,
) -> float | None:
    value = optional_float(row.get(field))
    if value is not None and not math.isfinite(value):
        raise ValueError(f"trace metric {field} must be finite when supplied")
    return value


def _min_optional_metric_value(
    rows: Sequence[Mapping[str, object]],
    field: str,
    *,
    positive_only: bool = False,
) -> float | None:
    values = [
        value
        for row in rows
        if (value := _optional_finite_metric_float(row, field)) is not None
        and (not positive_only or value > 0.0)
    ]
    return min(values) if values else None


def _max_abs_optional_metric_value(
    rows: Sequence[Mapping[str, object]],
    field: str,
) -> float | None:
    values = [
        abs(value)
        for row in rows
        if (value := _optional_finite_metric_float(row, field)) is not None
    ]
    return max(values) if values else None


def _summary_ambiguity_from_metric_rows(
    rows: Sequence[Mapping[str, object]],
) -> tuple[str, str]:
    flags: set[str] = set()
    shifted_only_positions: list[str] = []
    shifted_values: list[float] = []
    for row in rows:
        metric_flags = _split_flags(str(row.get("trace_quality_flags", "")))
        for flag in (
            "low_scan_density",
            "short_effective_smoothing_window",
            "raw_smoothed_apex_mismatch",
            "raw_shape_weak_for_shifted_support",
            "smoothed_shape_weak_for_shifted_support",
            "plus4_shifted_only_support",
            "plus4_window_tail_dominance",
            "large_shape_shift",
        ):
            if flag in metric_flags:
                flags.add(flag)
        if "shifted_only_support" in metric_flags:
            flags.add("shifted_only_support_present")
            shifted_only_positions.append(_required_metric_text(row, "position_label"))
        shape_shift = _optional_finite_metric_float(
            row,
            "shape_shift_sec_to_base",
        )
        if shape_shift is not None:
            shifted_values.append(shape_shift)
    if _has_mixed_shift_direction(shifted_values):
        flags.add("position_shift_direction_mixed")
    if _has_isolated_position_shift_rows(rows):
        flags.add("isolated_position_shift")
    if shifted_only_positions:
        flags.add(
            "shifted_only_positions=" + ",".join(sorted(set(shifted_only_positions)))
        )
    joined = _join_flags(flags)
    return ("review_required" if joined != TRACE_FLAG_NONE else "clear", joined)


def _shifted_only_labels_from_metric_rows(
    rows: Sequence[Mapping[str, object]],
) -> str:
    labels = [
        _required_metric_text(row, "position_label")
        for row in rows
        if "shifted_only_support"
        in _split_flags(str(row.get("trace_quality_flags", "")))
    ]
    return ",".join(labels) if labels else TRACE_FLAG_NONE


def _has_isolated_position_shift_rows(
    rows: Sequence[Mapping[str, object]],
) -> bool:
    shifts = [
        value
        for row in rows
        if (
            value := _optional_finite_metric_float(
                row,
                "shape_shift_sec_to_base",
            )
        )
        is not None
    ]
    if len(shifts) < 3:
        return False
    for index, value in enumerate(shifts):
        others = shifts[:index] + shifts[index + 1 :]
        if abs(value) > LARGE_SHAPE_SHIFT_SEC and all(
            abs(other) <= RAW_SMOOTHED_APEX_MISMATCH_SEC for other in others
        ):
            return True
    return False


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

    reference_area = float(np.median([shape.area for shape in reference_shapes]))
    reference_span25 = float(
        np.median([shape.span25_sec for shape in reference_shapes])
    )
    reference_centroid = float(
        np.median([shape.centroid_rt_min for shape in reference_shapes])
    )
    if reference_area <= 0 or reference_span25 <= 0:
        return False

    return (
        plus4_shape.area / reference_area >= PLUS4_WINDOW_DOMINANCE_MIN_AREA_RATIO
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
    _base_raw_point_count, base_raw_max_intensity, base_raw_apex_rt_min = base_raw_stats
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
            "supported" if max_intensity and max_intensity > 0 else "missing_signal"
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
                0.0 if raw_apex_rt_min is not None and apex_rt_min is not None else None
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
        if (
            best is None
            or value > best[0]
            or (math.isclose(value, best[0]) and abs(lag) < abs(best[1]))
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
    if point_count < 3 or median_rt_step_sec is None or median_rt_step_sec <= 0.0:
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


def _csv_precision_trace(trace: XICTrace) -> XICTrace:
    rt, intensity = _finite_sorted_arrays(trace)
    return XICTrace.from_arrays(
        [float(format_diagnostic_value(value)) for value in rt],
        [float(format_diagnostic_value(value)) for value in intensity],
    )


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
    if abs(apex_delta_sec) > max_apex_delta_sec:
        return "review", "apex_rt_outside_threshold"
    if pearson_r is not None and pearson_r >= min_pearson_r:
        return "supported", "rt_and_shape_supported"
    if active_overlap_fraction is not None and active_overlap_fraction >= 0.80:
        return "supported", "rt_and_active_overlap_supported"
    if shifted_pearson_r is not None and shifted_pearson_r >= min_pearson_r:
        return "supported", "shifted_shape_supported"
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
        if shape_shift_sec is not None and abs(shape_shift_sec) > LARGE_SHAPE_SHIFT_SEC:
            flags.append("large_shape_shift")
        if position_label == "pt_plus_4":
            flags.append("plus4_shifted_only_support")
    return _join_flags(flags)


def _has_mixed_shift_direction(values: list[float]) -> bool:
    positive = any(value > 0 for value in values)
    negative = any(value < 0 for value in values)
    return positive and negative


def _split_flags(flags: str) -> set[str]:
    if not flags or flags == TRACE_FLAG_NONE:
        return set()
    return {flag for flag in flags.split(";") if flag}


def _join_flags(flags: list[str] | set[str]) -> str:
    unique = sorted(set(flags))
    return ";".join(unique) if unique else TRACE_FLAG_NONE


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
    candidate_assignment_sha256: str,
    trace_extraction_sha256: str,
    trace_artifact_role: str,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_stem": sample_stem,
        "candidate_id": candidate_id,
        "candidate_assignment_sha256": candidate_assignment_sha256,
        "trace_extraction_sha256": trace_extraction_sha256,
        "trace_artifact_role": trace_artifact_role,
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
    candidate_assignment_sha256: str,
    trace_extraction_sha256: str,
    trace_artifact_role: str,
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
                "candidate_assignment_sha256": candidate_assignment_sha256,
                "trace_extraction_sha256": trace_extraction_sha256,
                "trace_artifact_role": trace_artifact_role,
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
