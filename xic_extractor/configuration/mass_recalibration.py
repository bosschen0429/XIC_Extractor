"""RAW mass policy and per-file restoration/replay results."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields
from enum import Enum


class MassRecalibration(str, Enum):
    NONE = "none"
    EASY_IC_RESTORE = "easy_ic_restore"
    # Read/replay saved results only; new executions do not fit background ions.
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
    """Restoration baseline, or legacy fitted coefficients for saved replay."""

    outcome: str
    reason: str | None = None
    a: float | None = None
    b: float | None = None
    n_fit: int = 0
    n_val: int = 0
    val_raw_p95: float | None = None
    val_corrected_p95: float | None = None
    policy: MassRecalibration = MassRecalibration.NONE
    baseline_lm: float | None = None
    n_anchor: int = 0


VENDOR_MASS_RECALIBRATION_RESULT = MassRecalibrationResult("none")


def serialize_mass_recalibration_result(result: MassRecalibrationResult) -> str:
    payload = asdict(result)
    if result.policy is MassRecalibration.EASY_IC_RESTORE:
        payload = {
            key: payload[key]
            for key in ("outcome", "reason", "policy", "baseline_lm", "n_anchor")
        }
    else:
        del payload["baseline_lm"], payload["n_anchor"]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def deserialize_mass_recalibration_result(content: str) -> MassRecalibrationResult:
    """Read optional persisted telemetry, including vendor-only legacy receipts."""
    if not content:
        return MassRecalibrationResult("none")
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError("invalid mass recalibration result fields")
    policy = resolve_mass_recalibration(payload.get("policy"))
    expected = (
        {"outcome", "reason", "policy", "baseline_lm", "n_anchor"}
        if policy is MassRecalibration.EASY_IC_RESTORE
        else {field.name for field in fields(MassRecalibrationResult)}
        - {"baseline_lm", "n_anchor"}
    )
    if set(payload) != expected:
        raise ValueError("invalid mass recalibration result fields")
    payload["policy"] = policy
    if payload["outcome"] not in {"none", "applied", "not_needed", "unavailable"}:
        raise ValueError("invalid mass recalibration outcome")
    if policy is MassRecalibration.EASY_IC_RESTORE:
        baseline, count = payload["baseline_lm"], payload["n_anchor"]
        if (
            type(count) is not int
            or count < 0
            or payload["outcome"] not in {"applied", "unavailable"}
            or (
                payload["outcome"] == "applied"
                and (
                    not isinstance(baseline, (int, float))
                    or not math.isfinite(baseline)
                    or count == 0
                )
            )
            or (payload["outcome"] == "unavailable" and baseline is not None)
        ):
            raise ValueError("invalid EASY-IC restoration baseline")
    return MassRecalibrationResult(**payload)
