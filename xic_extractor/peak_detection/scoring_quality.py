from __future__ import annotations

from typing import Any, Sequence

_ADAP_LIKE_FLAG_LABELS = {
    "low_scan_support": "low scan support",
    "low_trace_continuity": "low trace continuity",
    "poor_edge_recovery": "poor edge recovery",
}
_TRACE_CAP_BLOCKING_FLAGS = {"low_scan_support"}
_ADAP_LIKE_SELECTION_WEIGHT = 0.25
_ADAP_LIKE_SELECTION_MAX = 0.5
_ADAP_EQUIVALENT_LEGACY_FLAGS = {
    "low_scan_support": "low_scan_count",
    "poor_edge_recovery": "low_top_edge_ratio",
}


def candidate_quality_penalty(candidate: Any) -> tuple[int, list[str]]:
    raw_flags = getattr(candidate, "quality_flags", ())
    flags = tuple(dict.fromkeys(str(flag) for flag in raw_flags))
    if not flags:
        return 0, []
    notes: list[str] = []

    legacy_flags = list(hard_quality_flags(flags))
    penalty = min(2, len(legacy_flags))
    if legacy_flags:
        notes.append(f"weak candidate: {', '.join(legacy_flags)}")
    return penalty, notes


def trace_quality_severities(candidate: Any) -> tuple[tuple[int, str], ...]:
    flags = {str(flag) for flag in getattr(candidate, "quality_flags", ())}
    return tuple(
        (1 if flag in flags else 0, label)
        for flag, label in _ADAP_LIKE_FLAG_LABELS.items()
    )


def candidate_selection_quality_penalty(candidate: Any) -> float:
    raw_flags = getattr(candidate, "quality_flags", ())
    flags = tuple(dict.fromkeys(str(flag) for flag in raw_flags))
    weighted_flags = [flag for flag in flags if flag in _ADAP_LIKE_FLAG_LABELS]
    return min(
        _ADAP_LIKE_SELECTION_MAX,
        len(weighted_flags) * _ADAP_LIKE_SELECTION_WEIGHT,
    )


def hard_quality_flags(raw_flags: tuple[object, ...]) -> tuple[str, ...]:
    flags = tuple(dict.fromkeys(str(flag) for flag in raw_flags))
    suppressed_legacy = _suppressed_legacy_flags(flags)
    return tuple(
        flag
        for flag in flags
        if flag not in _ADAP_LIKE_FLAG_LABELS and flag not in suppressed_legacy
    )


def is_adap_like_quality_flag(flag: object) -> bool:
    return str(flag) in _ADAP_LIKE_FLAG_LABELS


def has_adap_like_quality_flags(raw_flags: Sequence[object]) -> bool:
    return any(is_adap_like_quality_flag(flag) for flag in raw_flags)


def trace_quality_cap_required(
    active_trace_flags: Sequence[str],
    *,
    has_cwt_same_apex_support: bool,
) -> bool:
    flags = set(active_trace_flags)
    if flags & _TRACE_CAP_BLOCKING_FLAGS:
        return True
    return len(flags) >= 2 and not has_cwt_same_apex_support


def _suppressed_legacy_flags(flags: tuple[str, ...]) -> set[str]:
    flag_set = set(flags)
    return {
        legacy_flag
        for adap_flag, legacy_flag in _ADAP_EQUIVALENT_LEGACY_FLAGS.items()
        if adap_flag in flag_set and legacy_flag in flag_set
    }
