"""Numeric projection facade retained for two current consumers only.

`backfill_scope.py` and `primary_consolidation.py` still consume legacy row
numeric fields through this adapter.  Remove this module when both consume the
CrossSample owner-native typed numeric projection directly.
"""

from __future__ import annotations

from typing import Any


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
