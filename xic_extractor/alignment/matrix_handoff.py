from __future__ import annotations

import math

from xic_extractor.peak_detection.evidence_facts import (
    integration_with_trace_evidence,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.models import PeakResult


def integration_from_peak(
    peak: PeakResult,
    *,
    boundary_sources: tuple[str, ...],
    integration_method: str = "raw_trapezoid",
) -> IntegrationResult | None:
    return integration_from_values(
        area_raw_counts_seconds=peak.area,
        rt_apex_min=peak.rt,
        raw_apex_rt_min=peak.rt,
        height_raw=peak.intensity,
        height_smoothed=getattr(peak, "intensity_smoothed", peak.intensity),
        rt_left_min=peak.peak_start,
        rt_right_min=peak.peak_end,
        boundary_sources=boundary_sources,
        integration_method=integration_method,
    )


def integration_from_peak_trace(
    peak: PeakResult,
    rt_values: object,
    intensity_values: object,
    *,
    boundary_sources: tuple[str, ...],
    integration_method: str = "raw_trapezoid",
    baseline_integration_method: str = "asls",
) -> IntegrationResult | None:
    integration = integration_from_peak(
        peak,
        boundary_sources=boundary_sources,
        integration_method=integration_method,
    )
    if integration is None:
        return None
    try:
        return integration_with_trace_evidence(
            integration,
            rt_values,
            intensity_values,
            baseline_integration_method=baseline_integration_method,
        )
    except (IndexError, TypeError, ValueError, FloatingPointError):
        return integration


def integration_from_values(
    *,
    area_raw_counts_seconds: object,
    rt_apex_min: object,
    raw_apex_rt_min: object | None = None,
    height_raw: object,
    height_smoothed: object | None = None,
    rt_left_min: object,
    rt_right_min: object,
    boundary_sources: tuple[str, ...],
    integration_method: str = "raw_trapezoid",
    area_baseline_corrected: object | None = None,
    area_uncertainty: object | None = None,
    area_uncertainty_formula_version: str = "",
    baseline_residual_mad: object | None = None,
    area_uncertainty_noise_source: str = "",
    baseline_type: str = "",
    baseline_score: object | None = None,
    area_ms1_morphology: object | None = None,
    ms1_morphology_area_source: str = "",
    ms1_morphology_trace_method: str = "",
    ms1_morphology_trace_window_points: object | None = None,
    ms1_morphology_trace_effective_points: object | None = None,
) -> IntegrationResult | None:
    values = (
        area_raw_counts_seconds,
        rt_apex_min,
        height_raw,
        rt_left_min,
        rt_right_min,
    )
    if any(_finite(value) is None for value in values):
        return None
    apex = _finite(rt_apex_min)
    left = _finite(rt_left_min)
    right = _finite(rt_right_min)
    area = _finite(area_raw_counts_seconds)
    height = _finite(height_raw)
    raw_apex = _finite(raw_apex_rt_min)
    smoothed_height = _finite(height_smoothed)
    if apex is None or left is None or right is None or area is None or height is None:
        return None
    return IntegrationResult(
        rt_left_min=left,
        rt_apex_min=apex,
        rt_right_min=right,
        raw_apex_rt_min=apex if raw_apex is None else raw_apex,
        rt_width_min=right - left,
        height_raw=height,
        height_smoothed=height if smoothed_height is None else smoothed_height,
        area_raw_counts_seconds=area,
        integration_method=integration_method,
        boundary_sources=boundary_sources,
        area_baseline_corrected=_finite(area_baseline_corrected),
        area_uncertainty=_finite(area_uncertainty),
        area_uncertainty_formula_version=area_uncertainty_formula_version,
        baseline_residual_mad=_finite(baseline_residual_mad),
        area_uncertainty_noise_source=area_uncertainty_noise_source,
        baseline_type=baseline_type,
        baseline_score=_finite(baseline_score),
        area_ms1_morphology=_finite(area_ms1_morphology),
        ms1_morphology_area_source=ms1_morphology_area_source,
        ms1_morphology_trace_method=ms1_morphology_trace_method,
        ms1_morphology_trace_window_points=_finite_int(
            ms1_morphology_trace_window_points
        ),
        ms1_morphology_trace_effective_points=_finite_int(
            ms1_morphology_trace_effective_points
        ),
    )


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _finite_int(value: object) -> int | None:
    number = _finite(value)
    if number is None:
        return None
    return int(number)
