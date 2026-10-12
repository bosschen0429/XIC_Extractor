from __future__ import annotations

from xic_extractor.tabular_io import split_semicolon_labels


def has_semicolon_token(value: object, token: str) -> bool:
    return token in set(split_semicolon_labels(value))
