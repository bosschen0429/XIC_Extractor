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


def is_attention_predicate(
    *,
    edge_failures: tuple[str, ...] = (),
    edge_dispositions: tuple[str, ...] = (),
    method_verdicts: tuple[str, ...] = (),
    group_statuses: tuple[str, ...] = (),
    cell_decision_unresolved_null: bool = False,
) -> bool:
    """Evaluate whether any scoped owner predicate matches the attention condition."""
    for failure in edge_failures:
        if failure in ("ambiguous_owner", "identity_conflict"):
            return True
    for disp in edge_dispositions:
        if disp == "review_only":
            return True
    for verdict in method_verdicts:
        if verdict == "HOLD":
            return True
    for status in group_statuses:
        if status in ("provisional", "unresolved"):
            return True
    if cell_decision_unresolved_null:
        return True
    return False


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
