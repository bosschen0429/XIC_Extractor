"""Preset-owned parameters for the pure delta-mass method."""

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

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("position requires a non-empty text label")
        for name in ("delta_da", "ratio_min", "ratio_max"):
            require_finite_real(getattr(self, name), name=name)
        if self.delta_da < 0 or not 0 < self.ratio_min <= self.ratio_max:
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

    def __post_init__(self) -> None:
        if not isinstance(self.method_label, str) or not self.method_label.strip():
            raise ValueError("method_label must be non-empty text")
        labels = [p.label for p in self.positions]
        deltas = [p.delta_da for p in self.positions]
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
            not self.anchor_positions
            or not set(self.anchor_positions) <= set(labels)
            or not set(self.discriminating_positions) <= set(labels)
        ):
            raise ValueError(
                "anchor and discriminating roles must name pattern positions"
            )
        if not isinstance(self.lowest_position, str) or (
            self.lowest_position and self.lowest_position not in labels
        ):
            raise ValueError("lowest_position must name a pattern position or be empty")
        for name in (
            "background_inner_sec",
            "background_outer_sec",
            "min_height_above_background",
            "min_background_sigma",
        ):
            value = require_finite_real(getattr(self, name), name=name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.lowest_position and not (
            self.shape_window_sec
            < self.background_inner_sec
            < self.background_outer_sec
        ):
            raise ValueError("background flanks must lie outside the shape window")
        for value in (
            self.mz_tolerance_ppm,
            self.apex_floor_sec,
            self.scan_interval_multiplier,
            self.shape_window_sec,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("method tolerances must be finite and positive")
        require_finite_real(self.min_shape_r, name="min_shape_r")
        if not -1 <= self.min_shape_r <= 1:
            raise ValueError("min_shape_r must be between -1 and 1")


@dataclass(frozen=True)
class DeltaMassDiscoveryStrategy:
    label: str
    settings: DeltaMassPatternSettings
    cid_nl_composite: bool
    smoothing_window_sec: float = 6.6
