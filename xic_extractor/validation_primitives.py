"""Canonical validation and parsing primitives.

These primitives provide fast, reusable invariant guards across data models,
contracts, and persistence envelopes, reducing copy-paste boilerplate.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from numbers import Integral, Real

_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")


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


def require_optional_integer(
    value: object,
    *,
    name: str = "value",
    field: str | None = None,
    non_negative: bool = False,
    positive: bool = False,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] | None = None,
) -> int | None:
    """Return integer if value is not None, else None."""
    if value is None:
        return None
    return require_integer(
        value,
        name=name,
        field=field,
        non_negative=non_negative,
        positive=positive,
        error_factory=error_factory,
        type_error_factory=type_error_factory,
    )


def require_boolean(
    value: object,
    *,
    field: str = "field",
    error_factory: Callable[[str], Exception] = TypeError,
) -> bool:
    """Validate that value is a bool."""
    if not isinstance(value, bool):
        raise error_factory(f"{field} must be a boolean")
    return value


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


def require_optional_text(
    value: object,
    *,
    field: str = "field",
    allow_blank: bool = False,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] = TypeError,
) -> str | None:
    """Return text if value is not None, else None."""
    if value is None:
        return None
    return require_text(
        value,
        field=field,
        allow_blank=allow_blank,
        error_factory=error_factory,
        type_error_factory=type_error_factory,
    )


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


def is_lower_sha256(value: object) -> bool:
    """Return whether value is a 64-character lowercase hex SHA-256 digest."""
    return isinstance(value, str) and _LOWER_SHA256.fullmatch(value) is not None


def require_lower_sha256(
    value: object,
    *,
    field: str = "digest",
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] | None = None,
) -> str:
    """Validate that value is a 64-character lowercase hex SHA-256 digest."""
    if not isinstance(value, str):
        if type_error_factory is not None:
            raise type_error_factory(f"{field} must be text")
        raise error_factory(f"{field} must be a lowercase SHA-256")
    if not is_lower_sha256(value):
        raise error_factory(f"{field} must be a lowercase SHA-256")
    return value


__all__ = [
    "require_boolean",
    "require_finite_real",
    "require_integer",
    "require_lower_sha256",
    "require_optional_integer",
    "require_optional_text",
    "require_owner_normalized_text",
    "require_text",
]
