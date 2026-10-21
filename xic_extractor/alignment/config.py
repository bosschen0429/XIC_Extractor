from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

ReviewPriority = Literal["HIGH", "MEDIUM", "LOW"]
OwnerBuildXicBackend = Literal["raw", "raw_superwindow", "ms1_index"]
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
    mz_bucket_neighbor_radius: int = 2
    anchor_priorities: tuple[ReviewPriority, ...] = ("HIGH",)
    anchor_min_evidence_score: int = 60
    anchor_min_seed_events: int = 2
    anchor_min_scan_support_score: float = 0.5
    duplicate_fold_ppm: float = 5.0
    duplicate_fold_rt_sec: float = 2.0
    duplicate_fold_product_ppm: float = 10.0
    duplicate_fold_observed_loss_ppm: float = 10.0
    duplicate_fold_min_detected_overlap: float = 0.80
    duplicate_fold_min_shared_detected_count: int = 3
    duplicate_fold_min_detected_jaccard: float = 0.60
    duplicate_fold_min_present_overlap: float = 0.80
    owner_window_overlap_fraction: float = 0.50
    owner_apex_close_sec: float = 2.0
    owner_tail_seed_guard_sec: float = 30.0
    owner_tail_max_secondary_ratio: float = 0.30
    owner_backfill_min_detected_samples: int = 1
    identity_rt_candidate_window_sec: float = 180.0
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
        _require_positive_int(
            "mz_bucket_neighbor_radius",
            self.mz_bucket_neighbor_radius,
        )
        _require_anchor_priorities(self.anchor_priorities)
        _require_int_range(
            "anchor_min_evidence_score",
            self.anchor_min_evidence_score,
            0,
            100,
        )
        _require_positive_int("anchor_min_seed_events", self.anchor_min_seed_events)
        _require_numeric_range(
            "anchor_min_scan_support_score",
            self.anchor_min_scan_support_score,
            0,
            1,
        )
        _require_positive("duplicate_fold_ppm", self.duplicate_fold_ppm)
        _require_positive("duplicate_fold_rt_sec", self.duplicate_fold_rt_sec)
        _require_positive("duplicate_fold_product_ppm", self.duplicate_fold_product_ppm)
        _require_positive(
            "duplicate_fold_observed_loss_ppm",
            self.duplicate_fold_observed_loss_ppm,
        )
        _require_numeric_range(
            "duplicate_fold_min_detected_overlap",
            self.duplicate_fold_min_detected_overlap,
            0,
            1,
        )
        _require_positive_int(
            "duplicate_fold_min_shared_detected_count",
            self.duplicate_fold_min_shared_detected_count,
        )
        _require_numeric_range(
            "duplicate_fold_min_detected_jaccard",
            self.duplicate_fold_min_detected_jaccard,
            0,
            1,
        )
        _require_numeric_range(
            "duplicate_fold_min_present_overlap",
            self.duplicate_fold_min_present_overlap,
            0,
            1,
        )
        _require_numeric_range(
            "owner_window_overlap_fraction",
            self.owner_window_overlap_fraction,
            0,
            1,
        )
        _require_positive("owner_apex_close_sec", self.owner_apex_close_sec)
        _require_positive(
            "owner_tail_seed_guard_sec",
            self.owner_tail_seed_guard_sec,
        )
        _require_numeric_range(
            "owner_tail_max_secondary_ratio",
            self.owner_tail_max_secondary_ratio,
            0,
            1,
        )
        _require_positive_int(
            "owner_backfill_min_detected_samples",
            self.owner_backfill_min_detected_samples,
        )
        _require_positive(
            "identity_rt_candidate_window_sec",
            self.identity_rt_candidate_window_sec,
        )
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


def _require_anchor_priorities(value: tuple[ReviewPriority, ...]) -> None:
    valid_priorities = {"HIGH", "MEDIUM", "LOW"}
    if (
        type(value) is not tuple
        or not value
        or any(
            not isinstance(priority, str) or priority not in valid_priorities
            for priority in value
        )
    ):
        raise ValueError("anchor_priorities must be a non-empty tuple of valid values")


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
