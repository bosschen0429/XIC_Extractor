from __future__ import annotations

import re
from collections.abc import Iterable
from statistics import median

from xic_extractor.instrument_qc.models import HCDAuditRow, SDOLEKTrendRow

_HCD_PRODUCT_REVIEW_STATUSES = frozenset(
    {
        "no_ms2_trigger",
        "no_product_match",
        "hcd_partial",
    }
)
_TARGET_RT_WINDOW_REVIEW_FLAG = "target_rt_window_review"


def manual_review_rows(
    sdolek_rows: list[SDOLEKTrendRow],
    mixstds_rows: list[SDOLEKTrendRow],
    hcd_rows: list[HCDAuditRow],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    isotope_support_index = _same_sample_isotope_support_index(hcd_rows)
    outside_window_support_keys = _outside_window_ms2_support_keys(hcd_rows)
    for hcd_row in hcd_rows:
        if _TARGET_RT_WINDOW_REVIEW_FLAG in hcd_row.review_flags:
            if _skip_target_rt_window_queue_row(hcd_row, isotope_support_index):
                continue
            rows.append(_manual_row("target_rt_window_mismatch", hcd=hcd_row))
            continue
        if _skip_hcd_product_queue_row(hcd_row, isotope_support_index):
            continue
        if hcd_row.hcd_status in _HCD_PRODUCT_REVIEW_STATUSES:
            rows.append(_manual_row(f"hcd_{hcd_row.hcd_status}", hcd=hcd_row))
        if hcd_row.hcd_status == "hcd_group_unmapped":
            rows.append(_manual_row("hcd_group_unmapped", hcd=hcd_row))
        if "activation_unknown_review" in hcd_row.review_flags:
            rows.append(_manual_row("activation_unknown", hcd=hcd_row))
    for trend_row in sdolek_rows:
        if (
            trend_row.compound == "LEK"
            and trend_row.rt_delta_to_reference_min is not None
            and abs(trend_row.rt_delta_to_reference_min) > 0.30
            and not _compound_rt_is_batch_stable(sdolek_rows, "LEK")
        ):
            rows.append(_manual_row("lek_rt_shift", trend=trend_row))
    for trend_row in mixstds_rows:
        if _has_outside_window_ms2_support(trend_row, outside_window_support_keys):
            continue
        if trend_row.status != "detected":
            rows.append(_manual_row("mixstds_not_detected", trend=trend_row))
    return _compact_manual_review_rows(rows)


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return ""
    return "; ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _skip_hcd_product_queue_row(
    hcd_row: HCDAuditRow,
    isotope_support_index: dict[tuple[str, str], frozenset[str]],
) -> bool:
    if hcd_row.hcd_status not in _HCD_PRODUCT_REVIEW_STATUSES:
        return False
    if hcd_row.hcd_mapping_source == "unmapped":
        return True
    return _has_same_sample_isotope_support(hcd_row, isotope_support_index)


def _skip_target_rt_window_queue_row(
    hcd_row: HCDAuditRow,
    isotope_support_index: dict[tuple[str, str], frozenset[str]],
) -> bool:
    if hcd_row.hcd_mapping_source == "unmapped":
        return True
    return _has_same_sample_isotope_support(hcd_row, isotope_support_index)


def _same_sample_isotope_support_index(
    hcd_rows: list[HCDAuditRow],
) -> dict[tuple[str, str], frozenset[str]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for row in hcd_rows:
        if row.hcd_status != "hcd_supported":
            continue
        base = _isotope_stripped_label(row.compound)
        grouped.setdefault((row.sample_name, base), set()).add(row.compound)
    return {key: frozenset(compounds) for key, compounds in grouped.items()}


def _has_same_sample_isotope_support(
    hcd_row: HCDAuditRow,
    isotope_support_index: dict[tuple[str, str], frozenset[str]],
) -> bool:
    target_base = _isotope_stripped_label(hcd_row.compound)
    supported_compounds = isotope_support_index.get((hcd_row.sample_name, target_base))
    if not supported_compounds:
        return False
    if target_base == hcd_row.compound:
        return any(compound != target_base for compound in supported_compounds)
    return any(compound != hcd_row.compound for compound in supported_compounds)


def _outside_window_ms2_support_keys(
    hcd_rows: list[HCDAuditRow],
) -> frozenset[tuple[str, str]]:
    return frozenset(
        (row.sample_name, row.compound)
        for row in hcd_rows
        if row.hcd_status == "hcd_supported"
        and _TARGET_RT_WINDOW_REVIEW_FLAG in row.review_flags
    )


def _has_outside_window_ms2_support(
    trend_row: SDOLEKTrendRow,
    outside_window_support_keys: frozenset[tuple[str, str]],
) -> bool:
    return (trend_row.sample_name, trend_row.compound) in outside_window_support_keys


def _isotope_stripped_label(label: str) -> str:
    value = re.sub(r"^\[[^\]]+\]-", "", label)
    value = re.sub(r"^(?:d\d+|[0-9]+N\d*)-", "", value)
    return value


def _compound_rt_is_batch_stable(
    rows: list[SDOLEKTrendRow],
    compound: str,
    *,
    min_rows: int = 3,
    max_rt_range_min: float = 0.70,
) -> bool:
    apex_rts = [
        row.apex_rt_min
        for row in rows
        if row.compound == compound
        and row.status == "detected"
        and row.apex_rt_min is not None
    ]
    if len(apex_rts) < min_rows:
        return False
    return max(apex_rts) - min(apex_rts) <= max_rt_range_min


def _compact_manual_review_rows(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        key = (
            row.get("queue_reason", ""),
            row.get("compound", ""),
            row.get("precursor_mz", ""),
            row.get("hcd_status", ""),
            row.get("activation_method", ""),
            row.get("hcd_mapping_source", ""),
        )
        grouped.setdefault(key, []).append(row)
    items = [_manual_review_item(group_rows) for group_rows in grouped.values()]
    return sorted(
        items,
        key=lambda row: (
            str(row["priority"]),
            str(row["queue_reason"]),
            str(row["compound"]),
        ),
    )


def _manual_review_item(rows: list[dict[str, object]]) -> dict[str, object]:
    first = rows[0]
    reason = str(first["queue_reason"])
    compound = str(first["compound"])
    precursor_mz = first.get("precursor_mz", "")
    samples = sorted({str(row.get("sample_name", "")) for row in rows})
    hcd_statuses = _counts(
        str(row.get("hcd_status", ""))
        for row in rows
        if row.get("hcd_status", "")
    )
    ms1_statuses = _counts(
        str(row.get("ms1_status", ""))
        for row in rows
        if row.get("ms1_status", "")
    )
    return {
        "priority": _review_priority(reason),
        "queue_reason": reason,
        "review_item": _review_item_label(reason, compound),
        "compound": compound,
        "precursor_mz": precursor_mz,
        "row_count": len(rows),
        "samples": _format_samples(samples),
        "ms1_summary": format_counts(ms1_statuses),
        "hcd_summary": format_counts(hcd_statuses),
        "rt_drift_hint": _rt_drift_hint(reason, rows),
        "product_hint": _product_hint(reason, rows),
        "suggested_action": _suggested_action(reason, rows),
        "manual_label": "",
        "manual_note": "",
    }


def _manual_row(
    reason: str,
    *,
    trend: SDOLEKTrendRow | None = None,
    hcd: HCDAuditRow | None = None,
) -> dict[str, object]:
    if hcd is not None:
        return {
            "queue_reason": reason,
            "sample_name": hcd.sample_name,
            "compound": hcd.compound,
            "precursor_mz": hcd.precursor_mz,
            "ms1_status": hcd.ms1_status,
            "ms1_apex_rt_min": hcd.ms1_apex_rt_min,
            "rt_delta_to_reference_min": "",
            "hcd_status": hcd.hcd_status,
            "activation_method": hcd.activation_method,
            "best_ms2_scan_rt_min": hcd.best_ms2_scan_rt_min,
            "apex_ms2_delta_min": hcd.apex_ms2_delta_min,
            "trigger_scan_count": hcd.trigger_scan_count,
            "expected_product_count": hcd.expected_product_count,
            "matched_product_count": hcd.matched_product_count,
            "best_product_ppm": hcd.best_product_ppm,
            "best_product_base_ratio": hcd.best_product_base_ratio,
            "matched_products": ";".join(hcd.matched_products),
            "hcd_mapping_source": hcd.hcd_mapping_source,
            "review_reason": hcd.review_reason,
            "manual_label": "",
            "manual_note": "",
        }
    if trend is None:
        raise ValueError("manual review row requires trend or hcd")
    return {
        "queue_reason": reason,
        "sample_name": trend.sample_name,
        "compound": trend.compound,
        "precursor_mz": trend.precursor_mz,
        "ms1_status": trend.status,
        "ms1_apex_rt_min": trend.apex_rt_min,
        "rt_delta_to_reference_min": trend.rt_delta_to_reference_min,
        "hcd_status": "",
        "activation_method": "",
        "best_ms2_scan_rt_min": "",
        "apex_ms2_delta_min": "",
        "trigger_scan_count": "",
        "expected_product_count": "",
        "matched_product_count": "",
        "best_product_ppm": "",
        "best_product_base_ratio": "",
        "matched_products": "",
        "hcd_mapping_source": "",
        "review_reason": trend.reason,
        "manual_label": "",
        "manual_note": "",
    }


def _review_priority(reason: str) -> str:
    priorities = {
        "hcd_no_ms2_trigger": "HIGH",
        "hcd_no_product_match": "HIGH",
        "hcd_hcd_partial": "MEDIUM",
        "target_rt_window_mismatch": "MEDIUM",
        "lek_rt_shift": "MEDIUM",
        "mixstds_not_detected": "MEDIUM",
        "hcd_group_unmapped": "LOW",
        "activation_unknown": "LOW",
    }
    return priorities.get(reason, "LOW")


def _review_item_label(reason: str, compound: str) -> str:
    labels = {
        "hcd_no_ms2_trigger": "MS1 detected but no precursor MS2 trigger",
        "hcd_no_product_match": "MS1 detected but expected product missing",
        "hcd_hcd_partial": "MS1 detected with weak/near product evidence",
        "target_rt_window_mismatch": "Product evidence outside target RT window",
        "hcd_group_unmapped": "HCD product group unmapped",
        "activation_unknown": "Activation method unknown",
        "lek_rt_shift": "LEK RT shifted from prior",
        "mixstds_not_detected": "Mix STDs target not detected by MS1",
    }
    return f"{compound}: {labels.get(reason, reason)}"


def _format_samples(samples: list[str], limit: int = 4) -> str:
    shown = [sample for sample in samples if sample][:limit]
    suffix = "" if len(samples) <= limit else f"; +{len(samples) - limit} more"
    return "; ".join(shown) + suffix


def _rt_drift_hint(reason: str, rows: list[dict[str, object]]) -> str:
    if reason == "lek_rt_shift":
        deltas = _float_values(row.get("rt_delta_to_reference_min", "") for row in rows)
        if not deltas:
            return "MS1 RT shift flagged; inspect trend sheet."
        max_abs = max(abs(value) for value in deltas)
        return f"MS1 prior delta max={max_abs:.3f} min; inspect LEK prior/window."
    if reason == "mixstds_not_detected":
        return "No MS1 peak selected; RT drift cannot be judged from HCD."
    deltas = _float_values(row.get("apex_ms2_delta_min", "") for row in rows)
    triggers = _float_values(row.get("trigger_scan_count", "") for row in rows)
    if not triggers or max(triggers) == 0:
        return "No precursor-matched MS2 trigger near MS1 apex."
    if not deltas:
        return "MS2 trigger exists, but delta is unavailable."
    med_delta = median(deltas)
    max_delta = max(deltas)
    if max_delta <= 0.08:
        interpretation = "RT drift is unlikely to be the main issue."
    elif max_delta <= 0.20:
        interpretation = "RT/off-apex timing could contribute; inspect rows."
    else:
        interpretation = "Large apex-MS2 gap; RT/off-apex issue likely."
    return (
        f"MS2-apex delta median={med_delta:.3f}, "
        f"max={max_delta:.3f} min. {interpretation}"
    )


def _product_hint(reason: str, rows: list[dict[str, object]]) -> str:
    if reason == "hcd_no_ms2_trigger":
        return "No product evidence because no precursor MS2 was triggered."
    if reason == "hcd_group_unmapped":
        sources = sorted({str(row.get("hcd_mapping_source", "")) for row in rows})
        return f"Add explicit hcd_base_group/product_group. source={';'.join(sources)}"
    if reason == "activation_unknown":
        return "Method doc sequence row lacks CID/wHCD detail."
    if reason == "target_rt_window_mismatch":
        return (
            "Product/NL evidence exists outside the configured target RT "
            "window; update RT prior/window before judging detection."
        )
    if reason == "lek_rt_shift":
        return (
            "SDO/LEK product table is independent; this queue item is MS1 "
            "RT-prior drift review."
        )
    expected = sum(_to_int(row.get("expected_product_count", "")) for row in rows)
    matched = sum(_to_int(row.get("matched_product_count", "")) for row in rows)
    best_ppm = _float_values(row.get("best_product_ppm", "") for row in rows)
    ratio = _float_values(row.get("best_product_base_ratio", "") for row in rows)
    detail = f"matched/expected={matched}/{expected}"
    sources = sorted(
        {
            str(row.get("hcd_mapping_source", ""))
            for row in rows
            if row.get("hcd_mapping_source", "")
        }
    )
    if sources:
        detail += f"; source={';'.join(sources)}"
    if best_ppm:
        detail += f"; best ppm={min(abs(value) for value in best_ppm):.1f}"
    if ratio:
        detail += f"; max product/base={max(ratio):.3f}"
    if reason == "mixstds_not_detected":
        return "MS1 not detected; HCD evidence not evaluated."
    return detail


def _suggested_action(reason: str, rows: list[dict[str, object]]) -> str:
    if reason == "hcd_no_ms2_trigger":
        return "Check DDA trigger/height threshold and MS1 peak height."
    if reason == "hcd_no_product_match":
        deltas = _float_values(row.get("apex_ms2_delta_min", "") for row in rows)
        sources = {
            str(row.get("hcd_mapping_source", ""))
            for row in rows
            if row.get("hcd_mapping_source", "")
        }
        if "unmapped" in sources:
            return (
                "Add explicit product group or leave this target as CID/NL-only "
                "manual review."
            )
        if deltas and max(deltas) > 0.08:
            return "Inspect RT/off-apex first, then base-specific product pattern."
        if sources == {"sdolek_builtin"}:
            return (
                "Review SDO/LEK-specific CID/wHCD product table and MS2 "
                "trigger quality."
            )
        return (
            "Review base-specific product pattern; add compound-specific "
            "registry if needed."
        )
    if reason == "hcd_hcd_partial":
        return "Inspect spectrum; product may be weak or ppm/intensity gate too strict."
    if reason == "target_rt_window_mismatch":
        return "Review XIC/MS2 at reported RT and update targets.csv RT window."
    if reason == "hcd_group_unmapped":
        return (
            "Add explicit HCD base/product group if this target needs "
            "identity review."
        )
    if reason == "activation_unknown":
        return "Parse method-detail table or manually label activation."
    if reason == "lek_rt_shift":
        return "Check whether LEK prior RT/window should be updated."
    if reason == "mixstds_not_detected":
        return "Confirm MS1 absence in XIC before interpreting HCD."
    return "Manual review."


def _float_values(values: Iterable[object]) -> list[float]:
    parsed: list[float] = []
    for value in values:
        try:
            if value == "":
                continue
            if not isinstance(value, int | float | str):
                continue
            parsed.append(float(value))
        except (TypeError, ValueError):
            continue
    return parsed


def _to_int(value: object) -> int:
    try:
        if value == "":
            return 0
        if not isinstance(value, int | float | str):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts
