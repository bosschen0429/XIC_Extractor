"""Feature availability and explicit selection after matrix finalization."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeatureQuality:
    finite_count: int
    eligible_sample_count: int
    finite_fraction: float
    keep: bool
    reason: str


def evaluate_feature_quality(
    values: np.ndarray,
    *,
    eligible_samples: Sequence[bool],
    min_finite_fraction: float,
) -> tuple[FeatureQuality, ...]:
    """Select by finite-value coverage, including zero, without changing values.

    Availability describes published numbers, not counted detection. The caller
    supplies sample eligibility from explicit metadata; no role is inferred.
    """
    if not math.isfinite(min_finite_fraction) or not 0 <= min_finite_fraction <= 1:
        raise ValueError("min_finite_fraction must be finite and between 0 and 1")
    mask = np.asarray(eligible_samples, dtype=bool)
    if values.ndim != 2 or mask.ndim != 1 or values.shape[1] != mask.size:
        raise ValueError("values and eligible_samples must have the same sample census")
    count = int(mask.sum())
    if not count:
        raise ValueError(
            "no eligible study samples: require study_sample and excluded=FALSE"
        )
    finite_counts = np.isfinite(values[:, mask]).sum(axis=1)
    results = []
    for finite_count in finite_counts:
        fraction = int(finite_count) / count
        keep = fraction >= min_finite_fraction
        results.append(
            FeatureQuality(
                finite_count=int(finite_count),
                eligible_sample_count=count,
                finite_fraction=fraction,
                keep=keep,
                reason=(
                    "finite_fraction_at_or_above_minimum"
                    if keep
                    else "finite_fraction_below_minimum"
                ),
            )
        )
    return tuple(results)
