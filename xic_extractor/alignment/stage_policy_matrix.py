"""Private canonical semantic codec for Alignment stage-policy identities."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping


def _canonical_semantic_bytes(
    value: object,
    *,
    field_names: tuple[str, ...] | None = None,
) -> bytes:
    """Encode one strict semantic value or one literal-field projection."""

    try:
        if field_names is None:
            canonical_value = _canonical_semantic_value(value)
        else:
            canonical_value = _project_literal_fields(value, field_names)
        return json.dumps(
            canonical_value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(
            "unsupported canonical semantic"
        ):
            raise
        raise ValueError("unsupported canonical semantic value") from exc


def _project_literal_fields(
    value: object,
    field_names: tuple[str, ...],
) -> dict[str, object]:
    if type(field_names) is not tuple or not field_names:
        raise ValueError("unsupported canonical semantic field tuple")
    if any(type(field_name) is not str or not field_name for field_name in field_names):
        raise ValueError("unsupported canonical semantic field tuple")
    if len(set(field_names)) != len(field_names):
        raise ValueError("unsupported canonical semantic duplicate field")
    if type(value) is not dict:
        raise ValueError("unsupported canonical semantic mapping")

    content = value
    if any(type(field_name) is not str for field_name in content):
        raise ValueError("unsupported canonical semantic mapping field")
    if len(content) != len(field_names) or any(
        field_name not in content for field_name in field_names
    ):
        raise ValueError("unsupported canonical semantic field projection")

    return {
        field_name: _canonical_semantic_value(content[field_name])
        for field_name in field_names
    }


def _canonical_semantic_value(value: object) -> object:
    if value is None:
        return None
    if type(value) is str:
        return value
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("unsupported canonical semantic non-finite float")
        encoded = "0x0.0p+0" if value == 0.0 else value.hex()
        return {"$float": encoded}
    if type(value) is tuple:
        return [_canonical_semantic_value(item) for item in value]
    if isinstance(value, Mapping):
        raise ValueError("unsupported canonical semantic mapping")
    raise ValueError("unsupported canonical semantic value type")


__all__ = ()
