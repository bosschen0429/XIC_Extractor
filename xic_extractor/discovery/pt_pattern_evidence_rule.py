"""Reusable Pt pattern evidence rule.

This module owns the small, row-level decision rule for Pt full-pattern
evidence. It does not read RAW files, write diagnostics, or grant matrix/export
authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.numeric_validation import require_finite_real, require_integer
from xic_extractor.tabular_io import optional_float

REQUIRED_COMBINED_STATUS = "pt_pattern_detected_diagnostic"
MIN_SUPPORTED_POSITIONS = 3
DEFAULT_APEX_DELTA_MAX_SEC = 0.6
DEFAULT_APEX_DELTA_SCAN_STEP_MULTIPLIER = 1.25
DEFAULT_PEARSON_MIN = 0.90
DEFAULT_OVERLAP_MIN = 0.50
DEFAULT_SHIFTED_PEARSON_MIN = 0.85
DEFAULT_SHAPE_SHIFT_MAX_SEC = 45.0


@dataclass(frozen=True)
class PtPatternEvidenceThresholds:
    required_combined_status: str = REQUIRED_COMBINED_STATUS
    min_non_base_positions_supported: int = MIN_SUPPORTED_POSITIONS
    max_abs_apex_delta_sec: float = DEFAULT_APEX_DELTA_MAX_SEC
    max_abs_apex_delta_scan_step_multiplier: float = (
        DEFAULT_APEX_DELTA_SCAN_STEP_MULTIPLIER
    )
    min_pearson_r_to_base: float = DEFAULT_PEARSON_MIN
    min_active_overlap_fraction: float = DEFAULT_OVERLAP_MIN
    min_shifted_pearson_r_to_base: float = DEFAULT_SHIFTED_PEARSON_MIN
    max_abs_shape_shift_sec_to_base: float = DEFAULT_SHAPE_SHIFT_MAX_SEC

    def __post_init__(self) -> None:
        if not self.required_combined_status.strip():
            raise ValueError("required_combined_status must be non-empty")
        min_supported = require_integer(
            self.min_non_base_positions_supported,
            name="min_non_base_positions_supported",
        )
        if min_supported <= 0:
            raise ValueError("min_non_base_positions_supported must be > 0")
        max_apex_delta = require_finite_real(
            self.max_abs_apex_delta_sec,
            name="max_abs_apex_delta_sec",
        )
        if max_apex_delta <= 0:
            raise ValueError("max_abs_apex_delta_sec must be > 0")
        scan_step_multiplier = require_finite_real(
            self.max_abs_apex_delta_scan_step_multiplier,
            name="max_abs_apex_delta_scan_step_multiplier",
        )
        if scan_step_multiplier <= 0:
            raise ValueError("max_abs_apex_delta_scan_step_multiplier must be > 0")
        pearson_min = require_finite_real(
            self.min_pearson_r_to_base,
            name="min_pearson_r_to_base",
        )
        if not 0 <= pearson_min <= 1:
            raise ValueError("min_pearson_r_to_base must be between 0 and 1")
        overlap_min = require_finite_real(
            self.min_active_overlap_fraction,
            name="min_active_overlap_fraction",
        )
        if not 0 <= overlap_min <= 1:
            raise ValueError("min_active_overlap_fraction must be between 0 and 1")
        shifted_pearson_min = require_finite_real(
            self.min_shifted_pearson_r_to_base,
            name="min_shifted_pearson_r_to_base",
        )
        if not 0 <= shifted_pearson_min <= 1:
            raise ValueError("min_shifted_pearson_r_to_base must be between 0 and 1")
        shape_shift_max = require_finite_real(
            self.max_abs_shape_shift_sec_to_base,
            name="max_abs_shape_shift_sec_to_base",
        )
        if shape_shift_max <= 0:
            raise ValueError("max_abs_shape_shift_sec_to_base must be > 0")


@dataclass(frozen=True)
class PtPatternEvidenceDecision:
    call: Literal["accept", "reject"]
    detection_status: Literal["detected", "non_detected"]
    reason: str


DEFAULT_THRESHOLDS = PtPatternEvidenceThresholds()


def evaluate_pt_pattern_evidence(
    row: Mapping[str, object],
    *,
    thresholds: PtPatternEvidenceThresholds = DEFAULT_THRESHOLDS,
) -> PtPatternEvidenceDecision:
    status = str(row.get("combined_pt_pattern_status", "")).strip()
    if status != thresholds.required_combined_status:
        return _reject("combined_status_not_detected")

    supported = optional_float(row.get("non_base_positions_supported"))
    apex_delta = optional_float(row.get("max_abs_apex_delta_sec"))
    pearson = optional_float(row.get("min_pearson_r_to_base"))
    overlap = optional_float(row.get("min_active_overlap_fraction"))
    shifted_pearson = optional_float(row.get("min_shifted_pearson_r_to_base"))
    shape_shift = optional_float(row.get("max_abs_shape_shift_sec_to_base"))
    if (
        supported is None
        or apex_delta is None
        or pearson is None
        or overlap is None
    ):
        return _reject("missing_required_metric")
    if supported < thresholds.min_non_base_positions_supported:
        return _reject("insufficient_non_base_positions")
    effective_apex_delta_max_sec = effective_max_abs_apex_delta_sec(
        row,
        thresholds=thresholds,
    )
    if (
        apex_delta <= effective_apex_delta_max_sec
        and pearson >= thresholds.min_pearson_r_to_base
        and overlap >= thresholds.min_active_overlap_fraction
    ):
        return PtPatternEvidenceDecision(
            call="accept",
            detection_status="detected",
            reason="full_pattern_close_apex_shape_overlap",
        )
    if (
        shifted_pearson is not None
        and shifted_pearson >= thresholds.min_shifted_pearson_r_to_base
        and shape_shift is None
    ):
        return _reject("missing_shape_shift_metric")
    if (
        shifted_pearson is not None
        and shifted_pearson >= thresholds.min_shifted_pearson_r_to_base
        and shape_shift is not None
        and shape_shift > thresholds.max_abs_shape_shift_sec_to_base
    ):
        return _reject("shape_shift_above_threshold")
    if (
        shifted_pearson is not None
        and shifted_pearson >= thresholds.min_shifted_pearson_r_to_base
        and shape_shift is not None
        and shape_shift <= thresholds.max_abs_shape_shift_sec_to_base
    ):
        return _reject("shifted_shape_review_only")
    if apex_delta > effective_apex_delta_max_sec:
        return _reject("apex_delta_above_threshold")
    if pearson < thresholds.min_pearson_r_to_base:
        return _reject("pearson_below_threshold")
    if overlap < thresholds.min_active_overlap_fraction:
        return _reject("overlap_below_threshold")
    return _reject("shifted_pearson_below_threshold")


def pt_pattern_rule_id(
    thresholds: PtPatternEvidenceThresholds = DEFAULT_THRESHOLDS,
) -> str:
    return (
        "pt_v2_fullpattern"
        f"_apex_le_{_token(thresholds.max_abs_apex_delta_sec)}s"
        f"_or_{_token(thresholds.max_abs_apex_delta_scan_step_multiplier)}scan"
        f"_pearson_ge_{_token(thresholds.min_pearson_r_to_base)}"
        f"_overlap_ge_{_token(thresholds.min_active_overlap_fraction)}"
        "_shiftedreviewonly"
        f"_shiftedpearson_ge_{_token(thresholds.min_shifted_pearson_r_to_base)}"
        f"_shapeshift_le_{_token(thresholds.max_abs_shape_shift_sec_to_base)}s"
    )


def effective_max_abs_apex_delta_sec(
    row: Mapping[str, object],
    *,
    thresholds: PtPatternEvidenceThresholds = DEFAULT_THRESHOLDS,
) -> float:
    """Return the row-level close-apex tolerance.

    Pt isotope positions should co-elute, but a discrete XIC apex can move by
    one acquisition interval when the real apex sits between scans. Keep the
    hard floor strict, then allow one scan interval when the trace artifact
    exposes enough timing context.
    """

    scan_interval_sec = _scan_interval_sec(row)
    if scan_interval_sec is None:
        return thresholds.max_abs_apex_delta_sec
    return max(
        thresholds.max_abs_apex_delta_sec,
        scan_interval_sec * thresholds.max_abs_apex_delta_scan_step_multiplier,
    )


def _reject(reason: str) -> PtPatternEvidenceDecision:
    return PtPatternEvidenceDecision(
        call="reject",
        detection_status="non_detected",
        reason=reason,
    )


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")


def _scan_interval_sec(row: Mapping[str, object]) -> float | None:
    for field in ("min_median_rt_step_sec", "median_rt_step_sec"):
        value = optional_float(row.get(field))
        if value is not None and value > 0:
            return value

    effective_window_sec = optional_float(row.get("min_effective_smoothing_window_sec"))
    smoothing_window_points = optional_float(row.get("trace_smoothing_window_points"))
    if (
        effective_window_sec is not None
        and effective_window_sec > 0
        and smoothing_window_points is not None
        and smoothing_window_points > 0
    ):
        return effective_window_sec / smoothing_window_points

    per_position_steps = [
        optional_float(row.get(f"{label}_median_rt_step_sec"))
        for label in ("pt_plus_1", "pt_plus_2", "pt_plus_4")
    ]
    positive_steps = [
        value for value in per_position_steps if value is not None and value > 0
    ]
    if positive_steps:
        return min(positive_steps)
    return None
