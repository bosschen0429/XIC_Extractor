"""Canonical validation, JSON serialization, and parsing primitives.

These primitives provide fast, reusable invariant guards across data models,
contracts, and persistence envelopes, reducing copy-paste boilerplate.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from numbers import Integral, Real
from uuid import UUID

_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON-compatible object to canonical UTF-8 bytes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def make_unique_mapping_hook(
    label: str = "JSON payload",
    error_factory: Callable[[str], Exception] = ValueError,
) -> Callable[[Sequence[tuple[str, object]]], dict[str, object]]:
    """Return an object_pairs_hook for json.loads that rejects duplicate keys."""

    def hook(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise error_factory(f"{label} has duplicate field: {key}")
            result[key] = value
        return result

    return hook


json_unique_mapping_hook = make_unique_mapping_hook()


def parse_json_mapping_payload(
    payload: bytes | str,
    *,
    label: str,
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] = TypeError,
    enforce_canonical: bool = False,
) -> tuple[bytes, dict[str, object]]:
    """Validate, decode, and parse payload into canonical UTF-8 bytes and mapping."""
    raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    if not isinstance(raw, bytes):
        raise type_error_factory(f"{label} payload must be bytes or str")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise error_factory(f"{label} payload must be UTF-8") from exc
    try:
        hook = (
            json_unique_mapping_hook
            if error_factory is ValueError
            else make_unique_mapping_hook(label=label, error_factory=error_factory)
        )
        value = json.loads(decoded, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        raise error_factory(f"{label} payload must be valid JSON") from exc
    if not isinstance(value, dict):
        raise error_factory(f"{label} must be one JSON object")
    if enforce_canonical and canonical_json_bytes(value) != raw:
        raise error_factory(f"{label} is not canonical")
    return raw, value


def canonical_float_hex(
    value: object,
    *,
    label: str = "canonical numeric fields",
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] = TypeError,
) -> str:
    """Return canonical hexadecimal representation of a finite real number."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise type_error_factory(f"{label} must be numbers")
    number = float(value)
    if not math.isfinite(number):
        raise error_factory(f"{label} must be finite")
    return (0.0 if number == 0.0 else number).hex()


def parse_canonical_float_hex(
    value: object,
    *,
    field: str = "field",
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] = TypeError,
) -> float:
    """Parse and roundtrip-verify a canonical finite hexadecimal float string."""
    if not isinstance(value, str):
        raise type_error_factory(f"{field} must be canonical hexadecimal text")
    try:
        number = float.fromhex(value)
    except ValueError as exc:
        raise error_factory(f"{field} must be canonical hexadecimal text") from exc
    if not math.isfinite(number) or canonical_float_hex(number) != value:
        raise error_factory(f"{field} must be canonical finite hexadecimal text")
    return number


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


def require_mapping(
    value: object,
    *,
    label: str = "payload",
    error_factory: Callable[[str], Exception] = ValueError,
) -> dict[str, object]:
    """Validate that value is a dictionary."""
    if not isinstance(value, dict):
        raise error_factory(f"{label} must be one JSON object")
    return value


def require_list(
    value: object,
    *,
    label: str = "payload",
    error_factory: Callable[[str], Exception] = ValueError,
) -> list[object]:
    """Validate that value is a list."""
    if not isinstance(value, list):
        raise error_factory(f"{label} must be one JSON array")
    return value


def require_tuple_of(
    value: object,
    item_type: type | tuple[type, ...],
    *,
    field: str = "field",
    error_factory: Callable[[str], Exception] = ValueError,
) -> tuple[object, ...]:
    """Validate that value is a tuple containing only instances of item_type."""
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        type_name = (
            item_type.__name__
            if hasattr(item_type, "__name__")
            else "/".join(t.__name__ for t in item_type)
        )
        raise error_factory(f"{field} must be a tuple of {type_name} values")
    return value


def require_keys(
    value: Mapping[str, object],
    keys: tuple[str, ...],
    *,
    label: str,
    error_factory: Callable[[str], Exception] = ValueError,
) -> None:
    """Validate that value's keys exactly match the expected keys sequence."""
    actual = tuple(value)
    if actual != keys:
        raise error_factory(f"{label} fields must be exactly {keys}; got {actual}")


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


def require_optional_lower_sha256(
    value: object,
    *,
    field: str = "digest",
    error_factory: Callable[[str], Exception] = ValueError,
    type_error_factory: Callable[[str], Exception] | None = None,
) -> str | None:
    """Return lowercase SHA-256 digest if value is not None, else None."""
    if value is None:
        return None
    return require_lower_sha256(
        value,
        field=field,
        error_factory=error_factory,
        type_error_factory=type_error_factory,
    )


__all__ = [
    "canonical_float_hex",
    "canonical_json_bytes",
    "json_unique_mapping_hook",
    "make_unique_mapping_hook",
    "parse_canonical_float_hex",
    "parse_json_mapping_payload",
    "require_boolean",
    "require_finite_real",
    "require_integer",
    "require_keys",
    "require_list",
    "require_lower_sha256",
    "require_mapping",
    "require_optional_integer",
    "require_optional_lower_sha256",
    "require_optional_text",
    "require_owner_normalized_text",
    "require_text",
    "require_tuple_of",
]


def require_attempt_uuid(
    value: object,
    *,
    error_factory: Callable[[str], Exception] = ValueError,
) -> str:
    """Read the canonical UUID used by persisted execution attempt markers."""
    if not isinstance(value, str):
        raise error_factory("processing_attempt_id must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise error_factory("processing_attempt_id must be a UUID") from exc
    if str(parsed) != value:
        raise error_factory("processing_attempt_id must be a canonical UUID")
    return value
