"""Range and compact text summaries for the reconciliation gallery."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from xic_extractor.diagnostics.diagnostic_io import optional_float, text_value

from .backfill_reconciliation_gallery_models import (
    ReconciliationGroup,
    _ordered_unique,
)


def _family_seed_summary(groups: Sequence[ReconciliationGroup]) -> str:
    seed_count = len(groups)
    seed_label = "1 seed" if seed_count == 1 else f"{seed_count} seeds"
    mz = _compact_value_range(group.seed_mz for group in groups)
    rt = _compact_value_range(group.seed_rt for group in groups)
    return f"{seed_label} · m/z {mz} · RT {rt}"


def _family_window_summary(groups: Sequence[ReconciliationGroup]) -> str:
    windows = _compact_text_values(group.seed_rt_window for group in groups)
    if not windows:
        return "window unknown"
    window = windows[0] if len(windows) == 1 else f"{len(windows)} windows"
    return f"window {window}"


def _compact_value_range(values: Iterable[str]) -> str:
    unique = _compact_text_values(values)
    if not unique:
        return "unknown"
    if len(unique) == 1:
        return unique[0]
    numeric = [optional_float(value) for value in unique]
    if all(value is not None for value in numeric):
        finite = [value for value in numeric if value is not None]
        return f"{min(finite):.6g}-{max(finite):.6g}"
    return f"{unique[0]}-{unique[-1]}"


def _compact_text_values(values: Iterable[str]) -> tuple[str, ...]:
    return _ordered_unique(text_value(value) for value in values if text_value(value))


def _seed_mz_range(groups: Sequence[ReconciliationGroup]) -> str:
    return _numeric_range_text(group.seed_mz for group in groups)


def _seed_rt_range(groups: Sequence[ReconciliationGroup]) -> str:
    return _numeric_range_text(group.seed_rt for group in groups)


def _seed_window_range(groups: Sequence[ReconciliationGroup]) -> str:
    starts: list[str] = []
    ends: list[str] = []
    for group in groups:
        if "-" not in group.seed_rt_window:
            continue
        start, end = group.seed_rt_window.split("-", 1)
        starts.append(start)
        ends.append(end)
    if not starts or not ends:
        return "unknown"
    return f"{_numeric_range_start(starts)}-{_numeric_range_end(ends)}"


def _numeric_range_text(values: Iterable[str]) -> str:
    parsed = _parsed_numeric_values(values)
    if not parsed:
        return "unknown"
    low = min(parsed, key=lambda item: item[0])
    high = max(parsed, key=lambda item: item[0])
    if low[0] == high[0]:
        return low[1]
    return f"{low[1]}-{high[1]}"


def _numeric_range_start(values: Iterable[str]) -> str:
    parsed = _parsed_numeric_values(values)
    if not parsed:
        return "unknown"
    return min(parsed, key=lambda item: item[0])[1]


def _numeric_range_end(values: Iterable[str]) -> str:
    parsed = _parsed_numeric_values(values)
    if not parsed:
        return "unknown"
    return max(parsed, key=lambda item: item[0])[1]


def _parsed_numeric_values(values: Iterable[str]) -> tuple[tuple[float, str], ...]:
    parsed: list[tuple[float, str]] = []
    for value in values:
        text = text_value(value)
        number = optional_float(text)
        if number is None:
            continue
        parsed.append((number, text))
    return tuple(parsed)
