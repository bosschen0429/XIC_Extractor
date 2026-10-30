from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

BackfillXicBackend = Literal["raw", "ms1_index", "ms1_index_hybrid"]


@dataclass(frozen=True)
class AlignmentConfig:
    preferred_ppm: float = 20.0
    max_ppm: float = 50.0
    preferred_rt_sec: float = 60.0
    max_rt_sec: float = 180.0
    product_mz_tolerance_ppm: float = 20.0
    observed_loss_tolerance_ppm: float = 20.0
    min_peak_shape_similarity: float = 0.80
    anchor_min_evidence_score: int = 60
    anchor_min_seed_events: int = 2
    owner_apex_close_sec: float = 2.0
    rt_unit: Literal["min"] = "min"
    fragmentation_model: Literal["cid_nl"] = "cid_nl"
    backfill_enabled: bool = True

    def __post_init__(self) -> None:
        if type(self.backfill_enabled) is not bool:
            raise ValueError("backfill_enabled must be a boolean")
        _require_positive("preferred_ppm", self.preferred_ppm)
        _require_positive("max_ppm", self.max_ppm)
        _require_at_most("preferred_ppm", self.preferred_ppm, "max_ppm", self.max_ppm)
        _require_positive("preferred_rt_sec", self.preferred_rt_sec)
        _require_positive("max_rt_sec", self.max_rt_sec)
        _require_at_most(
            "preferred_rt_sec",
            self.preferred_rt_sec,
            "max_rt_sec",
            self.max_rt_sec,
        )
        _require_positive("product_mz_tolerance_ppm", self.product_mz_tolerance_ppm)
        _require_numeric_range(
            "min_peak_shape_similarity",
            self.min_peak_shape_similarity,
            0,
            1,
        )
        _require_positive(
            "observed_loss_tolerance_ppm",
            self.observed_loss_tolerance_ppm,
        )
        _require_int_range(
            "anchor_min_evidence_score",
            self.anchor_min_evidence_score,
            0,
            100,
        )
        _require_positive_int("anchor_min_seed_events", self.anchor_min_seed_events)
        _require_positive("owner_apex_close_sec", self.owner_apex_close_sec)
        if self.rt_unit != "min":
            raise ValueError('rt_unit must be "min"')
        if self.fragmentation_model != "cid_nl":
            raise ValueError('fragmentation_model must be "cid_nl"')


def _require_positive(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be finite and positive")


def _require_positive_int(name: str, value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")


def _require_int_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")


def _require_numeric_range(
    name: str,
    value: float,
    minimum: float,
    maximum: float,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{name} must be finite numeric between {minimum} and {maximum}",
        )


def _require_at_most(
    preferred_name: str,
    preferred_value: float,
    max_name: str,
    max_value: float,
) -> None:
    if preferred_value > max_value:
        raise ValueError(f"{preferred_name} must be <= {max_name}")
