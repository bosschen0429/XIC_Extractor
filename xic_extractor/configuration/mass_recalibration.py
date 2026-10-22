"""RAW adapter mass policy; fitted coefficients belong to each opened RAW."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from enum import Enum


class MassRecalibration(str, Enum):
    NONE = "none"
    EASY_IC_PROBE = "easy_ic_probe"


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


@dataclass(frozen=True, slots=True)
class MassRecalibrationResult:
    """Per-file probe provenance; only applied results carry coefficients."""

    outcome: str
    reason: str | None = None
    a: float | None = None
    b: float | None = None
    n_fit: int = 0
    n_val: int = 0
    val_raw_p95: float | None = None
    val_corrected_p95: float | None = None
    policy: MassRecalibration = MassRecalibration.NONE


VENDOR_MASS_RECALIBRATION_RESULT = MassRecalibrationResult("none")


def serialize_mass_recalibration_result(result: MassRecalibrationResult) -> str:
    return json.dumps(
        asdict(result), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def deserialize_mass_recalibration_result(content: str) -> MassRecalibrationResult:
    """Read optional persisted telemetry, including vendor-only legacy receipts."""
    if not content:
        return MassRecalibrationResult("none")
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {
        field.name for field in fields(MassRecalibrationResult)
    }:
        raise ValueError("invalid mass recalibration result fields")
    payload["policy"] = resolve_mass_recalibration(payload["policy"])
    if payload["outcome"] not in {"none", "applied", "not_needed", "unavailable"}:
        raise ValueError("invalid mass recalibration outcome")
    return MassRecalibrationResult(**payload)
