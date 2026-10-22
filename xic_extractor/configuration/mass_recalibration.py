"""Opt-in per-scan m/z recalibration policy applied by the RAW reader.

On the instruments this policy targets, the residual m/z error of every scan is
about twice the internal-calibration correction the vendor records for that
scan (``LM Correction (ppm)``). The reader removes it before any XIC, centroid
or MS2 consumer sees a mass; downstream algorithms never apply it themselves.
"""

from __future__ import annotations

from enum import Enum


class MassRecalibration(str, Enum):
    NONE = "none"
    EASY_IC_DOUBLED_LM = "easy_ic_doubled_lm"


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
