"""Anchor-local chromatographic segmentation evidence for Pt pattern traces.

This module does not define or select the canonical product-spine
``PeakHypothesis``. It only selects one anchor-containing segment per Pt
position and exposes the common comparison interval plus boundary facts.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import numpy as np

from xic_extractor.discovery.delta_mass_patterns import PT_PATTERN
from xic_extractor.peak_detection.baseline import asls_baseline
from xic_extractor.peak_detection.chrom_peak_segments import (
    ChromPeakSegment,
    ChromPeakSegmentPolicy,
    ChromPeakSegmentStatus,
    enumerate_chrom_peak_segments,
    segments_containing_rt,
)
from xic_extractor.xic_models import XICTrace, crop_xic_trace_by_rt

AnchorSegmentationStatus = Literal[
    "clear",
    "missing_anchor_segment",
    "ambiguous_anchor_segment",
    "empty_common_interval",
]
AnchorBoundaryReviewStatus = Literal[
    "clear",
    "review_required",
    "unavailable",
]
BoundaryTrend = Literal["ascending", "descending", "flat", "unavailable"]
POSITION_LABELS = tuple(position.label for position in PT_PATTERN.positions)


@dataclass(frozen=True, slots=True)
class PositionPeakSegmentSelection:
    position_label: str
    enumeration_status: ChromPeakSegmentStatus
    matching_segment_count: int
    selected_segment: ChromPeakSegment | None


@dataclass(frozen=True, slots=True)
class PositionAnchorBoundaryEvidence:
    position_label: str
    segment_rt_start_min: float
    segment_rt_end_min: float
    segment_duration_sec: float
    segment_class: str
    boundary_stop_reason: str
    owns_common_start: bool
    owns_common_end: bool
    left_clip_sec: float
    right_clip_sec: float
    common_retained_fraction: float
    common_left_residual_fraction: float | None
    common_right_residual_fraction: float | None
    common_left_trend: BoundaryTrend
    common_right_trend: BoundaryTrend


@dataclass(frozen=True, slots=True)
class _PositionPeakSegmentWork:
    selection: PositionPeakSegmentSelection
    trace: XICTrace
    morphology_trace_values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PtPatternAnchorSegmentation:
    status: AnchorSegmentationStatus
    reason: str
    anchor_rt_min: float
    common_rt_start_min: float | None
    common_rt_end_min: float | None
    position_selections: tuple[PositionPeakSegmentSelection, ...]
    trace_by_position: Mapping[str, XICTrace]
    boundary_review_status: AnchorBoundaryReviewStatus
    boundary_review_position_labels: tuple[str, ...]
    boundary_evidence: tuple[PositionAnchorBoundaryEvidence, ...]


def select_anchor_local_segments(
    trace_by_position: Mapping[str, XICTrace],
    *,
    anchor_rt_min: float,
    policy: ChromPeakSegmentPolicy | None = None,
) -> PtPatternAnchorSegmentation:
    """Select anchor-local segments without creating a ``PeakHypothesis``."""
    work_items = tuple(
        _select_position_segment(
            position_label,
            trace_by_position.get(position_label, XICTrace.empty()),
            anchor_rt_min=anchor_rt_min,
            policy=policy,
        )
        for position_label in POSITION_LABELS
    )
    selections = tuple(item.selection for item in work_items)
    missing = tuple(
        selection.position_label
        for selection in selections
        if selection.matching_segment_count == 0
    )
    if missing:
        return _failed_segmentation(
            status="missing_anchor_segment",
            reason="missing_anchor_segment:" + ",".join(missing),
            anchor_rt_min=anchor_rt_min,
            selections=selections,
        )
    ambiguous = tuple(
        selection.position_label
        for selection in selections
        if selection.matching_segment_count > 1
    )
    if ambiguous:
        return _failed_segmentation(
            status="ambiguous_anchor_segment",
            reason="ambiguous_anchor_segment:" + ",".join(ambiguous),
            anchor_rt_min=anchor_rt_min,
            selections=selections,
        )

    selected_segments = tuple(
        selection.selected_segment
        for selection in selections
        if selection.selected_segment is not None
    )
    rt_start_min = max(segment.interval.rt_start_min for segment in selected_segments)
    rt_end_min = min(segment.interval.rt_end_min for segment in selected_segments)
    if rt_start_min >= rt_end_min:
        return _failed_segmentation(
            status="empty_common_interval",
            reason="selected_segments_have_no_common_rt_interval",
            anchor_rt_min=anchor_rt_min,
            selections=selections,
        )

    localized = {
        position_label: crop_xic_trace_by_rt(
            trace_by_position.get(position_label, XICTrace.empty()),
            rt_start_min,
            rt_end_min,
            assume_sorted_rt=True,
        )
        for position_label in POSITION_LABELS
    }
    if any(trace.rt.size < 3 for trace in localized.values()):
        return _failed_segmentation(
            status="empty_common_interval",
            reason="selected_common_rt_interval_has_fewer_than_three_points",
            anchor_rt_min=anchor_rt_min,
            selections=selections,
        )
    boundary_evidence = tuple(
        _boundary_evidence(
            item,
            common_rt_start_min=rt_start_min,
            common_rt_end_min=rt_end_min,
        )
        for item in work_items
    )
    boundary_review_position_labels = tuple(
        item.position_label
        for item in boundary_evidence
        if _requires_boundary_review(item)
    )
    return PtPatternAnchorSegmentation(
        status="clear",
        reason="one_anchor_segment_per_position",
        anchor_rt_min=float(anchor_rt_min),
        common_rt_start_min=rt_start_min,
        common_rt_end_min=rt_end_min,
        position_selections=selections,
        trace_by_position=localized,
        boundary_review_status=(
            "review_required" if boundary_review_position_labels else "clear"
        ),
        boundary_review_position_labels=boundary_review_position_labels,
        boundary_evidence=boundary_evidence,
    )


def _select_position_segment(
    position_label: str,
    trace: XICTrace,
    *,
    anchor_rt_min: float,
    policy: ChromPeakSegmentPolicy | None,
) -> _PositionPeakSegmentWork:
    finite_trace = _finite_sorted_trace(trace)
    if finite_trace.rt.size < 3:
        return _PositionPeakSegmentWork(
            selection=PositionPeakSegmentSelection(
                position_label=position_label,
                enumeration_status="WINDOW_TOO_SHORT",
                matching_segment_count=0,
                selected_segment=None,
            ),
            trace=finite_trace,
            morphology_trace_values=(),
        )
    baseline = asls_baseline(finite_trace.intensity)
    enumeration = enumerate_chrom_peak_segments(
        finite_trace.rt,
        finite_trace.intensity,
        baseline,
        policy=policy,
    )
    matches = segments_containing_rt(enumeration.segments, anchor_rt_min)
    return _PositionPeakSegmentWork(
        selection=PositionPeakSegmentSelection(
            position_label=position_label,
            enumeration_status=enumeration.status,
            matching_segment_count=len(matches),
            selected_segment=matches[0] if len(matches) == 1 else None,
        ),
        trace=finite_trace,
        morphology_trace_values=enumeration.morphology_trace_values,
    )


def _boundary_evidence(
    item: _PositionPeakSegmentWork,
    *,
    common_rt_start_min: float,
    common_rt_end_min: float,
) -> PositionAnchorBoundaryEvidence:
    segment = item.selection.selected_segment
    if segment is None:
        raise ValueError("boundary evidence requires one selected segment")
    segment_duration_sec = max(
        0.0,
        (segment.interval.rt_end_min - segment.interval.rt_start_min) * 60.0,
    )
    common_duration_sec = max(
        0.0,
        (common_rt_end_min - common_rt_start_min) * 60.0,
    )
    left_index = int(np.searchsorted(item.trace.rt, common_rt_start_min, side="left"))
    right_index = (
        int(np.searchsorted(item.trace.rt, common_rt_end_min, side="right")) - 1
    )
    morphology = np.asarray(item.morphology_trace_values, dtype=float)
    apex_residual = float(segment.morphology_apex_residual)
    return PositionAnchorBoundaryEvidence(
        position_label=item.selection.position_label,
        segment_rt_start_min=float(segment.interval.rt_start_min),
        segment_rt_end_min=float(segment.interval.rt_end_min),
        segment_duration_sec=segment_duration_sec,
        segment_class=segment.segment_class,
        boundary_stop_reason=segment.boundary_stop_reason,
        owns_common_start=math.isclose(
            segment.interval.rt_start_min,
            common_rt_start_min,
            abs_tol=1e-9,
        ),
        owns_common_end=math.isclose(
            segment.interval.rt_end_min,
            common_rt_end_min,
            abs_tol=1e-9,
        ),
        left_clip_sec=max(
            0.0,
            (common_rt_start_min - segment.interval.rt_start_min) * 60.0,
        ),
        right_clip_sec=max(
            0.0,
            (segment.interval.rt_end_min - common_rt_end_min) * 60.0,
        ),
        common_retained_fraction=(
            common_duration_sec / segment_duration_sec
            if segment_duration_sec > 0.0
            else 0.0
        ),
        common_left_residual_fraction=_residual_fraction(
            morphology,
            left_index,
            apex_residual,
        ),
        common_right_residual_fraction=_residual_fraction(
            morphology,
            right_index,
            apex_residual,
        ),
        common_left_trend=_boundary_trend(morphology, left_index, side="left"),
        common_right_trend=_boundary_trend(morphology, right_index, side="right"),
    )


def _residual_fraction(
    morphology: np.ndarray,
    index: int,
    apex_residual: float,
) -> float | None:
    if (
        morphology.ndim != 1
        or not 0 <= index < morphology.size
        or not math.isfinite(apex_residual)
        or apex_residual <= 0.0
    ):
        return None
    value = float(morphology[index])
    if not math.isfinite(value):
        return None
    return value / apex_residual


def _boundary_trend(
    morphology: np.ndarray,
    index: int,
    *,
    side: Literal["left", "right"],
) -> BoundaryTrend:
    if morphology.ndim != 1 or not 0 <= index < morphology.size:
        return "unavailable"
    if side == "left":
        start = index
        stop = min(morphology.size, index + 4)
    else:
        start = max(0, index - 3)
        stop = index + 1
    values = morphology[start:stop]
    if values.size < 2 or not np.all(np.isfinite(values)):
        return "unavailable"
    delta = float(np.mean(np.diff(values)))
    scale = max(float(np.max(np.abs(values))), 1.0)
    if abs(delta) <= scale * 1e-6:
        return "flat"
    return "ascending" if delta > 0.0 else "descending"


def _requires_boundary_review(
    evidence: PositionAnchorBoundaryEvidence,
) -> bool:
    owns_common_boundary = evidence.owns_common_start or evidence.owns_common_end
    has_review_boundary = evidence.segment_class in {
        "shoulder_candidate",
        "low_scan_segment",
    } or ("review" in evidence.boundary_stop_reason)
    return owns_common_boundary and has_review_boundary


def _finite_sorted_trace(trace: XICTrace) -> XICTrace:
    finite = np.isfinite(trace.rt) & np.isfinite(trace.intensity)
    rt = trace.rt[finite]
    intensity = trace.intensity[finite]
    if rt.size <= 1:
        return XICTrace.from_arrays(rt, intensity)
    order = np.argsort(rt, kind="stable")
    return XICTrace.from_arrays(rt[order], intensity[order])


def _failed_segmentation(
    *,
    status: AnchorSegmentationStatus,
    reason: str,
    anchor_rt_min: float,
    selections: tuple[PositionPeakSegmentSelection, ...],
) -> PtPatternAnchorSegmentation:
    return PtPatternAnchorSegmentation(
        status=status,
        reason=reason,
        anchor_rt_min=float(anchor_rt_min),
        common_rt_start_min=None,
        common_rt_end_min=None,
        position_selections=selections,
        trace_by_position={},
        boundary_review_status="unavailable",
        boundary_review_position_labels=(),
        boundary_evidence=(),
    )
