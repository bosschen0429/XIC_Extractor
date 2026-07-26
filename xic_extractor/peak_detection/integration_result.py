"""Workflow-neutral integrated-signal result model."""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = ["IntegrationResult"]
