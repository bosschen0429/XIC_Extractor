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

_PRODUCT_PRECURSOR_SHIFT_TOLERANCE_DA = 0.02


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


def ppm(left: float, right: float) -> float:
    return abs(left - right) / max(abs(left), 1e-12) * 1_000_000.0
