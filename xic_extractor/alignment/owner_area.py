from __future__ import annotations

import math
from collections.abc import Iterable
from statistics import median


def positive_finite(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return float(value)
    return None


def median_owner_area(feature: object) -> float | None:
    owners = getattr(feature, "owners", ())
    return median_positive_owner_area(owners)


def median_positive_owner_area(owners: Iterable[object]) -> float | None:
    areas = [
        area
        for owner in owners
        if bool(getattr(owner, "quantification_eligible", True))
        for area in (positive_finite(getattr(owner, "owner_area", None)),)
        if area is not None
    ]
    return float(median(areas)) if areas else None
