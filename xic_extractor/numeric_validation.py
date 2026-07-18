"""Small runtime guards for invariant-bearing numeric inputs."""

from __future__ import annotations

import math
from numbers import Integral, Real


def require_finite_real(value: object, *, name: str) -> float:
    """Return ``value`` as float, rejecting bools, strings, and non-finite data."""

    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a real number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def require_integer(value: object, *, name: str) -> int:
    """Return an exact integer, rejecting bools and truncation-prone values."""

    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    return int(value)
