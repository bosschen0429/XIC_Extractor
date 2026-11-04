"""Lossless JSON transport through ordered, text-only Excel cells."""

import json
from collections.abc import Iterable, Mapping

JSON_CELL_CHAR_LIMIT = 32767


def encode_json_cells(value: Mapping) -> tuple[str, ...]:
    # ASCII escapes also keep non-BMP characters within Excel's text limit.
    text = json.dumps(value, ensure_ascii=True, allow_nan=False)
    return tuple(
        text[start : start + JSON_CELL_CHAR_LIMIT]
        for start in range(0, len(text), JSON_CELL_CHAR_LIMIT)
    )


def decode_json_cells(cells: Iterable[tuple[object, object]]) -> dict:
    """Admit external fragment numbers before assembling the JSON object."""
    parts: dict[int, str] = {}
    for part, text in cells:
        if isinstance(part, int) and not isinstance(part, bool):
            index = part
        elif isinstance(part, str) and part.isdecimal():
            index = int(part)
        else:
            raise ValueError("JSON part must be a positive integer")
        if index < 1 or index in parts:
            raise ValueError("JSON parts must be positive and unique")
        if not isinstance(text, str):
            raise ValueError("JSON fragments must be text")
        parts[index] = text
    if not parts or sorted(parts) != list(range(1, len(parts) + 1)):
        raise ValueError("JSON parts must be contiguous, starting at 1")
    value = json.loads("".join(parts[index] for index in range(1, len(parts) + 1)))
    if not isinstance(value, dict):
        raise ValueError("JSON definition must be an object")
    return value
