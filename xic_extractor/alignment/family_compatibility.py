"""Cross-sample group compatibility checks.

Despite the legacy module name 'family_compatibility', these functions check
whether two cross-sample groups represent the same chemical entity using m/z,
RT, product m/z, and observed neutral loss tolerances.  They do NOT check
discovery-layer peak-anchor membership.

A future rename to ``group_compatibility.py`` is planned but deferred to
minimize merge conflicts with active branches.
"""

from __future__ import annotations

from typing import Any

from xic_extractor.alignment.config import AlignmentConfig

_PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA = 0.02


def loose_compatible_primary_family(
    left: Any,
    right: Any,
    config: AlignmentConfig,
) -> bool:
    if bool(getattr(left, "review_only", False)) or bool(
        getattr(right, "review_only", False),
    ):
        return False
    if str(left.neutral_loss_tag) != str(right.neutral_loss_tag):
        return False
    if ppm(family_center_mz(left), family_center_mz(right)) > config.max_ppm:
        return False
    if (
        abs(family_center_rt(left) - family_center_rt(right)) * 60.0
        > config.identity_rt_candidate_window_sec
    ):
        return False
    if (
        ppm(family_observed_loss(left), family_observed_loss(right))
        > config.observed_loss_tolerance_ppm
    ):
        return False
    if (
        ppm(family_product_mz(left), family_product_mz(right))
        <= config.product_mz_tolerance_ppm
    ):
        return True
    precursor_delta = family_center_mz(right) - family_center_mz(left)
    product_delta = family_product_mz(right) - family_product_mz(left)
    return abs(product_delta - precursor_delta) <= _PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA


def family_center_mz(row: Any) -> float:
    if hasattr(row, "family_center_mz"):
        return float(row.family_center_mz)
    return float(row.cluster_center_mz)


def family_center_rt(row: Any) -> float:
    if hasattr(row, "family_center_rt"):
        return float(row.family_center_rt)
    return float(row.cluster_center_rt)


def family_product_mz(row: Any) -> float:
    if hasattr(row, "family_product_mz"):
        return float(row.family_product_mz)
    return float(row.cluster_product_mz)


def family_observed_loss(row: Any) -> float:
    if hasattr(row, "family_observed_neutral_loss_da"):
        return float(row.family_observed_neutral_loss_da)
    return float(row.cluster_observed_neutral_loss_da)


def ppm(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), 1e-12) * 1_000_000.0
