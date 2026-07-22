"""Reusable Pt pattern evidence rule.

This module owns the small, row-level decision rule for Pt full-pattern
evidence. It does not read RAW files, write diagnostics, or grant matrix/export
authority.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

from xic_extractor.numeric_validation import require_finite_real, require_integer
from xic_extractor.tabular_io import optional_float

REQUIRED_COMBINED_STATUS = "pt_pattern_detected_diagnostic"
VALID_COMBINED_PT_PATTERN_STATUSES = frozenset(
    {
        REQUIRED_COMBINED_STATUS,
        "pt_pattern_candidate_partial",
        "pt_pattern_candidate_incomplete",
        "pt_pattern_candidate_base_missing",
        "pt_pattern_candidate_review",
    }
)
MIN_SUPPORTED_POSITIONS = 3
DEFAULT_APEX_DELTA_MAX_SEC = 0.6
DEFAULT_APEX_DELTA_SCAN_STEP_MULTIPLIER = 1.25
DEFAULT_PEARSON_MIN = 0.90
DEFAULT_OVERLAP_MIN = 0.50
DEFAULT_SHIFTED_PEARSON_MIN = 0.85
DEFAULT_SHAPE_SHIFT_MAX_SEC = 45.0
MAX_NON_BASE_POSITIONS = 3
EVIDENCE_RULE_OWNER = "xic_extractor.discovery.pt_pattern_evidence_rule"


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
        if (
            not isinstance(self.required_combined_status, str)
            or self.required_combined_status
            not in VALID_COMBINED_PT_PATTERN_STATUSES
        ):
            raise ValueError(
                "required_combined_status must be one of "
                f"{sorted(VALID_COMBINED_PT_PATTERN_STATUSES)!r}"
            )
        min_supported = require_integer(
            self.min_non_base_positions_supported,
            name="min_non_base_positions_supported",
        )
        if not 1 <= min_supported <= MAX_NON_BASE_POSITIONS:
            raise ValueError(
                "min_non_base_positions_supported must be between 1 and "
                f"{MAX_NON_BASE_POSITIONS}"
            )
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

    supported = _validated_metric(
        row,
        "non_base_positions_supported",
        minimum=0,
        maximum=MAX_NON_BASE_POSITIONS,
        integer=True,
    )
    apex_delta = _validated_metric(
        row,
        "max_abs_apex_delta_sec",
        minimum=0,
    )
    pearson = _validated_metric(
        row,
        "min_pearson_r_to_base",
        minimum=-1,
        maximum=1,
    )
    overlap = _validated_metric(
        row,
        "min_active_overlap_fraction",
        minimum=0,
        maximum=1,
    )
    shifted_pearson = _validated_metric(
        row,
        "min_shifted_pearson_r_to_base",
        minimum=-1,
        maximum=1,
    )
    shape_shift = _validated_metric(
        row,
        "max_abs_shape_shift_sec_to_base",
        minimum=0,
    )
    if supported is None or apex_delta is None or pearson is None or overlap is None:
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
        f"_status_{quote(thresholds.required_combined_status, safe='')}"
        f"_positions_ge_{thresholds.min_non_base_positions_supported}"
        f"_apex_floor_le_{_token(thresholds.max_abs_apex_delta_sec)}s"
        "_or_scan_multiplier_"
        f"{_token(thresholds.max_abs_apex_delta_scan_step_multiplier)}"
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

    scan_interval_sec = pt_pattern_scan_interval_sec(row)
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


def _validated_metric(
    row: Mapping[str, object],
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    integer: bool = False,
) -> float | None:
    raw_value = row.get(field)
    value = optional_float(raw_value)
    if value is None:
        if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
            return None
        raise ValueError(f"{field} must be a finite number")
    if integer and not value.is_integer():
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be >= {minimum:g}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be <= {maximum:g}")
    return value


def _token(value: float) -> str:
    # ``repr`` is the shortest round-tripping representation of the exact
    # float. The previous ``:g`` default rounded to six significant digits,
    # allowing decision-distinct thresholds to share one audit rule ID.
    text = str(int(value)) if value.is_integer() else repr(value)
    return text.replace(".", "p").replace("-", "m").replace("+", "")


def pt_pattern_scan_interval_sec(row: Mapping[str, object]) -> float | None:
    """Return the canonical observed scan interval used by the Pt rule."""

    aggregate_step = _validated_positive_optional_metric(
        row,
        "min_median_rt_step_sec",
    )
    component_steps = {
        field: _validated_positive_optional_metric(row, field)
        for field in (
            "median_rt_step_sec",
            "pt_plus_1_median_rt_step_sec",
            "pt_plus_2_median_rt_step_sec",
            "pt_plus_4_median_rt_step_sec",
        )
    }
    supplied_components = [
        value for value in component_steps.values() if value is not None
    ]
    if aggregate_step is not None and supplied_components:
        component_min = min(supplied_components)
        if aggregate_step > component_min and not math.isclose(
            aggregate_step,
            component_min,
            rel_tol=1e-6,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "timing provenance is inconsistent: min_median_rt_step_sec "
                "must not exceed a supplied component median RT step"
            )
    supplied_steps = [
        value
        for value in (aggregate_step, *component_steps.values())
        if value is not None
    ]
    return min(supplied_steps) if supplied_steps else None


def _validated_positive_optional_metric(
    row: Mapping[str, object],
    field: str,
) -> float | None:
    value = _validated_metric(row, field, minimum=0)
    if value == 0:
        raise ValueError(f"{field} must be > 0")
    return value
