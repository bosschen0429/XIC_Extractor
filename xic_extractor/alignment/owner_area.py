from __future__ import annotations

import math
from collections.abc import Iterable
from statistics import median
from typing import cast


def positive_finite(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return float(value)
    return None


def owner_quantification_eligible(owner: object) -> bool:
    if not bool(getattr(owner, "quantification_eligible", True)):
        return False
    missing = object()
    apex = getattr(owner, "owner_apex_rt", missing)
    left = getattr(owner, "owner_peak_start_rt", missing)
    right = getattr(owner, "owner_peak_end_rt", missing)
    integration = getattr(owner, "selected_integration", missing)
    if integration is not missing and integration is not None:
        apex = getattr(integration, "rt_apex_min", missing)
        left = getattr(integration, "rt_left_min", missing)
        right = getattr(integration, "rt_right_min", missing)
    if any(value is missing for value in (apex, left, right)):
        return True
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in (apex, left, right)
    ):
        return False
    return cast(float, left) < cast(float, apex) < cast(float, right)


def median_owner_area(feature: object) -> float | None:
    owners = getattr(feature, "owners", ())
    return median_positive_owner_area(owners)


def median_positive_owner_area(owners: Iterable[object]) -> float | None:
    areas = [
        area
        for owner in owners
        if owner_quantification_eligible(owner)
        for area in (positive_finite(getattr(owner, "owner_area", None)),)
        if area is not None
    ]
    return float(median(areas)) if areas else None
