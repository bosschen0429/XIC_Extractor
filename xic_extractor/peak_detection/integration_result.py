"""Workflow-neutral integrated-signal result model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xic_extractor.peak_detection.signal_quality import SignalQuality

_FLOAT_FIELDS = (
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "height_raw",
    "height_smoothed",
    "area_raw_counts_seconds",
)
_OPTIONAL_FLOAT_FIELDS = (
    "area_baseline_corrected",
    "area_uncertainty",
    "baseline_residual_mad",
    "baseline_score",
    "area_ms1_morphology",
)
_OPTIONAL_INT_FIELDS = (
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
)


@dataclass(frozen=True)
class IntegrationResult:
    rt_left_min: float
    rt_apex_min: float
    rt_right_min: float
    raw_apex_rt_min: float
    rt_width_min: float
    height_raw: float
    height_smoothed: float
    area_raw_counts_seconds: float
    integration_method: str = "raw_trapezoid"
    boundary_sources: tuple[str, ...] = ("candidate_interval",)
    area_baseline_corrected: float | None = None
    area_uncertainty: float | None = None
    area_uncertainty_formula_version: str = ""
    baseline_residual_mad: float | None = None
    area_uncertainty_noise_source: str = ""
    baseline_type: str = ""
    baseline_score: float | None = None
    raw_scan_indices: tuple[int, ...] = ()
    area_ms1_morphology: float | None = None
    ms1_morphology_area_source: str = ""
    ms1_morphology_trace_method: str = ""
    ms1_morphology_trace_window_points: int | None = None
    ms1_morphology_trace_effective_points: int | None = None
    signal_quality: SignalQuality | None = None

    def __post_init__(self) -> None:
        for field_name in _FLOAT_FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _canonical_float(value, field=field_name),
                )
        for field_name in _OPTIONAL_FLOAT_FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _canonical_float(value, field=field_name),
                )
        for field_name in _OPTIONAL_INT_FIELDS:
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(
                    self,
                    field_name,
                    _canonical_int(value, field=field_name),
                )


def _canonical_float(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    return 0.0 if normalized == 0.0 else normalized


def _canonical_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if normalized != value:
        raise ValueError(f"{field} must be an integer")
    return normalized


__all__ = ["IntegrationResult"]
