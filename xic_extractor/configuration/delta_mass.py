"""Shared, in-memory Delta Mass pattern settings."""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.validation_primitives import require_finite_real


@dataclass(frozen=True)
class DeltaMassPosition:
    label: str
    delta_da: float
    ratio_min: float
    ratio_max: float


def validate_delta_mass_position(model: DeltaMassPosition) -> None:
    if not model.label.strip():
        raise ValueError("position requires a non-empty text label")
    for name in ("delta_da", "ratio_min", "ratio_max"):
        require_finite_real(getattr(model, name), name=name)
    if model.delta_da < 0 or not 0 < model.ratio_min <= model.ratio_max:
        raise ValueError(
            "position requires nonnegative delta and positive ordered ratios"
        )


@dataclass(frozen=True)
class DeltaMassPatternSettings:
    pattern_id: str
    method_label: str
    positions: tuple[DeltaMassPosition, ...]
    anchor_positions: tuple[str, ...]
    discriminating_positions: tuple[str, ...]
    mz_tolerance_ppm: float = 20.0
    apex_floor_sec: float = 1.0
    scan_interval_multiplier: float = 1.25
    shape_window_sec: float = 10.0
    min_shape_r: float = 0.9
    lowest_position: str = ""
    background_inner_sec: float = 20.0
    background_outer_sec: float = 30.0
    min_height_above_background: float = 25_000.0
    min_background_sigma: float = 5.0
    base_support_positions: tuple[str, ...] = ()
    min_base_support: float = 0.25
    shape_reference_position: str = ""
    ratio_reference_position: str = ""


def validate_delta_mass_pattern(model: DeltaMassPatternSettings) -> None:
    if not model.method_label.strip():
        raise ValueError("method_label must be non-empty text")
    labels = [p.label for p in model.positions]
    if any(
        value and value not in labels
        for value in (model.shape_reference_position, model.ratio_reference_position)
    ):
        raise ValueError("shape and ratio reference must name pattern positions")
    deltas = [p.delta_da for p in model.positions]
    if (
        len(labels) < 2
        or len(set(labels)) != len(labels)
        or deltas != sorted(set(deltas))
        or deltas[0] != 0
    ):
        raise ValueError(
            "pattern requires two or more unique ordered positions starting at zero"
        )
    if (
        not model.anchor_positions
        or not set(model.anchor_positions) <= set(labels)
        or not set(model.discriminating_positions) <= set(labels)
    ):
        raise ValueError("anchor and discriminating roles must name pattern positions")
    if model.lowest_position and model.lowest_position not in labels:
        raise ValueError("lowest_position must name a pattern position or be empty")
    if len(set(model.base_support_positions)) != len(
        model.base_support_positions
    ) or not set(model.base_support_positions) <= set(labels[1:]):
        raise ValueError("base_support_positions must name unique non-base positions")
    require_finite_real(model.min_base_support, name="min_base_support")
    if not 0 < model.min_base_support <= 1:
        raise ValueError("min_base_support must be greater than zero and at most one")
    for name in (
        "background_inner_sec",
        "background_outer_sec",
        "min_height_above_background",
        "min_background_sigma",
    ):
        value = require_finite_real(getattr(model, name), name=name)
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if (model.lowest_position or model.base_support_positions) and not (
        model.shape_window_sec < model.background_inner_sec < model.background_outer_sec
    ):
        raise ValueError("background flanks must lie outside the shape window")
    for value in (
        model.mz_tolerance_ppm,
        model.apex_floor_sec,
        model.scan_interval_multiplier,
        model.shape_window_sec,
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("method tolerances must be finite and positive")
    require_finite_real(model.min_shape_r, name="min_shape_r")
    if not -1 <= model.min_shape_r <= 1:
        raise ValueError("min_shape_r must be between -1 and 1")
