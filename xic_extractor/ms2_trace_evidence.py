from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

MS2TraceStrength = Literal["none", "weak", "moderate", "strong"]

_STRONG_APEX_DELTA_MIN = 0.15
_MODERATE_APEX_DELTA_MIN = 0.20
_STRONG_CONTINUITY_MIN = 0.5


@dataclass(frozen=True)
class MS2TracePoint:
    rt: float
    intensity: float
    base_ratio: float
    observed_loss_error_ppm: float


@dataclass(frozen=True)
class MS2TraceEvidence:
    product_point_count: int
    product_apex_rt: float | None
    product_apex_delta_min: float | None
    product_height: float | None
    product_area: float | None
    trace_continuity: float | None
    strength: MS2TraceStrength


def empty_ms2_trace_evidence() -> MS2TraceEvidence:
    return MS2TraceEvidence(
        product_point_count=0,
        product_apex_rt=None,
        product_apex_delta_min=None,
        product_height=None,
        product_area=None,
        trace_continuity=None,
        strength="none",
    )


def summarize_ms2_trace(
    points: Sequence[MS2TracePoint],
    *,
    candidate_apex_rt: float,
    trigger_scan_count: int,
) -> MS2TraceEvidence:
    valid_points = tuple(
        point
        for point in points
        if _is_finite(point.rt) and _is_finite(point.intensity)
    )
    product_point_count = len(valid_points)
    trace_continuity = _trace_continuity(product_point_count, trigger_scan_count)
    if not valid_points:
        return MS2TraceEvidence(
            product_point_count=0,
            product_apex_rt=None,
            product_apex_delta_min=None,
            product_height=None,
            product_area=None,
            trace_continuity=trace_continuity,
            strength="none",
        )

    apex = max(valid_points, key=lambda point: point.intensity)
    product_apex_delta_min = abs(float(apex.rt) - float(candidate_apex_rt))
    product_area = _product_area(valid_points)
    strength = _trace_strength(
        product_point_count=product_point_count,
        product_apex_delta_min=product_apex_delta_min,
        trace_continuity=trace_continuity,
    )
    return MS2TraceEvidence(
        product_point_count=product_point_count,
        product_apex_rt=float(apex.rt),
        product_apex_delta_min=product_apex_delta_min,
        product_height=float(apex.intensity),
        product_area=product_area,
        trace_continuity=trace_continuity,
        strength=strength,
    )


def _trace_continuity(
    product_point_count: int, trigger_scan_count: int
) -> float | None:
    if trigger_scan_count <= 0:
        return None
    return product_point_count / trigger_scan_count


def _product_area(points: Sequence[MS2TracePoint]) -> float | None:
    if len(points) < 2:
        return None
    sorted_points = sorted(points, key=lambda point: point.rt)
    area = 0.0
    for left, right in zip(sorted_points, sorted_points[1:], strict=False):
        width = max(0.0, float(right.rt) - float(left.rt))
        area += width * (float(left.intensity) + float(right.intensity)) / 2.0
    return area


def _trace_strength(
    *,
    product_point_count: int,
    product_apex_delta_min: float,
    trace_continuity: float | None,
) -> MS2TraceStrength:
    if product_point_count <= 0:
        return "none"
    if (
        product_point_count >= 2
        and trace_continuity is not None
        and trace_continuity >= _STRONG_CONTINUITY_MIN
        and product_apex_delta_min <= _STRONG_APEX_DELTA_MIN
    ):
        return "strong"
    if product_apex_delta_min <= _MODERATE_APEX_DELTA_MIN:
        return "moderate"
    return "weak"


def _is_finite(value: float) -> bool:
    try:
        return math.isfinite(float(value))
    except TypeError, ValueError:
        return False
