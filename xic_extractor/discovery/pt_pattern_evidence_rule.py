"""Reusable Pt pattern evidence rule.

This module owns the small, row-level decision rule for Pt full-pattern
evidence. It does not read RAW files, write diagnostics, or grant matrix/export
authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from xic_extractor.tabular_io import optional_float

REQUIRED_COMBINED_STATUS = "pt_pattern_detected_diagnostic"
MIN_SUPPORTED_POSITIONS = 3
DEFAULT_APEX_DELTA_MAX_SEC = 10.0
DEFAULT_PEARSON_MIN = 0.90
DEFAULT_OVERLAP_MIN = 0.50
DEFAULT_SHIFTED_PEARSON_MIN = 0.85
DEFAULT_SHAPE_SHIFT_MAX_SEC = 45.0


@dataclass(frozen=True)
class PtPatternEvidenceThresholds:
    required_combined_status: str = REQUIRED_COMBINED_STATUS
    min_non_base_positions_supported: int = MIN_SUPPORTED_POSITIONS
    max_abs_apex_delta_sec: float = DEFAULT_APEX_DELTA_MAX_SEC
    min_pearson_r_to_base: float = DEFAULT_PEARSON_MIN
    min_active_overlap_fraction: float = DEFAULT_OVERLAP_MIN
    min_shifted_pearson_r_to_base: float = DEFAULT_SHIFTED_PEARSON_MIN
    max_abs_shape_shift_sec_to_base: float = DEFAULT_SHAPE_SHIFT_MAX_SEC

    def __post_init__(self) -> None:
        if not self.required_combined_status.strip():
            raise ValueError("required_combined_status must be non-empty")
        if self.min_non_base_positions_supported <= 0:
            raise ValueError("min_non_base_positions_supported must be > 0")
        if self.max_abs_apex_delta_sec <= 0:
            raise ValueError("max_abs_apex_delta_sec must be > 0")
        if not 0 <= self.min_pearson_r_to_base <= 1:
            raise ValueError("min_pearson_r_to_base must be between 0 and 1")
        if not 0 <= self.min_active_overlap_fraction <= 1:
            raise ValueError("min_active_overlap_fraction must be between 0 and 1")
        if not 0 <= self.min_shifted_pearson_r_to_base <= 1:
            raise ValueError("min_shifted_pearson_r_to_base must be between 0 and 1")
        if self.max_abs_shape_shift_sec_to_base <= 0:
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
    if (
        apex_delta <= thresholds.max_abs_apex_delta_sec
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
        and shape_shift is not None
        and shape_shift <= thresholds.max_abs_shape_shift_sec_to_base
    ):
        return PtPatternEvidenceDecision(
            call="accept",
            detection_status="detected",
            reason="full_pattern_shifted_shape_supported",
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
    if apex_delta > thresholds.max_abs_apex_delta_sec:
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
        f"_pearson_ge_{_token(thresholds.min_pearson_r_to_base)}"
        f"_overlap_ge_{_token(thresholds.min_active_overlap_fraction)}"
        f"_shiftedpearson_ge_{_token(thresholds.min_shifted_pearson_r_to_base)}"
        f"_shapeshift_le_{_token(thresholds.max_abs_shape_shift_sec_to_base)}s"
    )


def _reject(reason: str) -> PtPatternEvidenceDecision:
    return PtPatternEvidenceDecision(
        call="reject",
        detection_status="non_detected",
        reason=reason,
    )


def _token(value: float) -> str:
    return f"{value:g}".replace(".", "p")
