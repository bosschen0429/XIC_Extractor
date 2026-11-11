"""Stratum classification for XIC Inspector selection."""

from __future__ import annotations

from typing import Literal

StratumKey = Literal["attention", "excluded", "null", "zero", "rest"]

STRATA_ORDER: tuple[StratumKey, ...] = (
    "attention",
    "excluded",
    "null",
    "zero",
    "rest",
)


def classify_scoped_stratum(
    *,
    is_attention: bool,
    is_excluded: bool,
    has_null_cell: bool,
    has_zero_cell: bool,
) -> StratumKey:
    """Classify an item into one of 5 strata based on scoped priority."""
    if is_attention:
        return "attention"
    if is_excluded:
        return "excluded"
    if has_null_cell:
        return "null"
    if has_zero_cell:
        return "zero"
    return "rest"
