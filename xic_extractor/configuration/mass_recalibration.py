"""RAW adapter mass policy; fitted coefficients belong to each opened RAW."""

from __future__ import annotations

from enum import Enum


class MassRecalibration(str, Enum):
    NONE = "none"
    EASY_IC_PROBE_V1 = "easy_ic_probe_v1"


def resolve_mass_recalibration(value: object) -> MassRecalibration:
    if isinstance(value, MassRecalibration):
        return value
    try:
        return MassRecalibration(value)
    except ValueError:
        allowed = ", ".join(policy.value for policy in MassRecalibration)
        raise ValueError(
            f"mass_recalibration must be one of: {allowed}; got {value!r}"
        ) from None
