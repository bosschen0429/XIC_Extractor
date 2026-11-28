"""Canonical validation and parsing primitives.

These primitives provide fast, reusable invariant guards across data models,
contracts, and persistence envelopes, reducing copy-paste boilerplate.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from numbers import Integral, Real


def require_finite_real(
    value: object,
    *,
    name: str = "value",
    field: str | None = None,
    non_negative: bool = False,
    positive: bool = False,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] | None = None,
) -> float:
    """Return value as float, rejecting bools, strings, and non-finite data."""
    label = field if field is not None else name
    te_factory = type_error_factory if type_error_factory is not None else error_factory
    if isinstance(value, bool) or not isinstance(value, Real):
        raise te_factory(f"{label} must be a real number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise error_factory(f"{label} must be finite")
    if non_negative and parsed < 0.0:
        raise error_factory(f"{label} must be non-negative")
    if positive and parsed <= 0.0:
        raise error_factory(f"{label} must be positive")
    return parsed


def require_integer(
    value: object,
    *,
    name: str = "value",
    field: str | None = None,
    non_negative: bool = False,
    positive: bool = False,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] | None = None,
) -> int:
    """Return an exact integer, rejecting bools and non-integral data."""
    label = field if field is not None else name
    te_factory = type_error_factory if type_error_factory is not None else error_factory
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise te_factory(f"{label} must be an integer")
    parsed = int(value)
    if non_negative and parsed < 0:
        raise error_factory(f"{label} must be a non-negative integer")
    if positive and parsed <= 0:
        raise error_factory(f"{label} must be a positive integer")
    return parsed


def require_text(
    value: object,
    *,
    field: str = "field",
    allow_blank: bool = False,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] = TypeError,
) -> str:
    """Validate that value is a string and non-empty unless allow_blank is True."""
    if not isinstance(value, str):
        raise type_error_factory(f"{field} must be text")
    if not allow_blank and not value:
        raise error_factory(f"{field} must be non-empty text")
    return value


def require_owner_normalized_text(
    value: object,
    *,
    field: str = "field",
    error_factory: Callable[[str], Exception] = ValueError,
) -> str:
    """Validate that value is non-empty owner-normalized (stripped) text."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise error_factory(f"{field} must be non-empty owner-normalized text")
    return value


__all__ = [
    "require_finite_real",
    "require_integer",
    "require_owner_normalized_text",
    "require_text",
]
