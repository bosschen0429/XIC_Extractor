"""Classify legacy family-id peak-group rows for MS1 backfill review."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

SUGGESTED_OVERLAY_HALF_WINDOW_MIN = 1.1


def _candidate_row(
    review_row: Mapping[str, str],
    cells: Sequence[Mapping[str, str]],
    overlay: Mapping[str, Any] | None,
) -> dict[str, Any]:
    detected = [row for row in cells if row.get("status") == "detected"]
    rescued = [row for row in cells if row.get("status") == "backfilled"]
    detected_height_median = _median(_float(row.get("height")) for row in detected)
    rescued_height_median = _median(_float(row.get("height")) for row in rescued)
    detected_area_median = _median(_float(row.get("area")) for row in detected)
    rescued_area_median = _median(_float(row.get("area")) for row in rescued)
    detected_count = int(_float(review_row.get("detected_count")) or 0)
    rescue_count = int(_float(review_row.get("accepted_backfill_count")) or 0)
    accepted_count = int(_float(review_row.get("accepted_cell_count")) or 0)
    rescue_fraction = _safe_fraction(rescue_count, accepted_count) or 0.0
    rescue_per_detected_seed = _safe_ratio(rescue_count, detected_count) or 0.0
    height_ratio = _safe_ratio(detected_height_median, rescued_height_median)
    area_ratio = _safe_ratio(detected_area_median, rescued_area_median)
    overlay_summary = dict(overlay or {})
    classification = _review_classification(
        overlay_summary=overlay_summary,
        height_ratio=height_ratio,
        rescue_count=rescue_count,
    )
    priority = rescue_fraction * max(rescue_per_detected_seed, 1.0)
    if height_ratio is not None:
        priority *= min(height_ratio, 3.0)
    mz = review_row.get("family_center_mz", "")
    center_rt = review_row.get("family_center_rt", "")
    overlay_hint = _suggested_overlay_hint(
        family_id=review_row["feature_family_id"],
        mz=mz,
        center_rt=center_rt,
    )
    return {
        "feature_family_id": review_row["feature_family_id"],
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "family_center_mz": mz,
        "family_center_rt": center_rt,
        **overlay_hint,
        "detected_count": detected_count,
        "accepted_backfill_count": rescue_count,
        "accepted_cell_count": accepted_count,
        "rescue_fraction": rescue_fraction,
        "rescue_per_detected_seed": rescue_per_detected_seed,
        "detected_height_median": detected_height_median,
        "rescued_height_median": rescued_height_median,
        "detected_to_rescued_height_ratio": height_ratio,
        "detected_area_median": detected_area_median,
        "rescued_area_median": rescued_area_median,
        "detected_to_rescued_area_ratio": area_ratio,
        "overlay_status": "provided" if overlay is not None else "not_provided",
        "overlay_family_verdict": overlay_summary.get("family_verdict", ""),
        "dda_trigger_limited_ms2_support": overlay_summary.get(
            "dda_trigger_limited_ms2_support",
            "",
        ),
        "detected_backfilled_count": overlay_summary.get(
            "detected_backfilled_count", ""
        ),
        "global_apex_assessable_trace_count": overlay_summary.get(
            "global_apex_assessable_trace_count",
            "",
        ),
        "global_apex_assessable_fraction": overlay_summary.get(
            "global_apex_assessable_fraction",
            "",
        ),
        "selected_apex_in_trace_window_count": overlay_summary.get(
            "selected_apex_in_trace_window_count",
            "",
        ),
        "selected_apex_in_trace_window_fraction": overlay_summary.get(
            "selected_apex_in_trace_window_fraction",
            "",
        ),
        "local_apex_assessable_trace_count": overlay_summary.get(
            "local_apex_assessable_trace_count",
            "",
        ),
        "global_apex_interference_count": overlay_summary.get(
            "global_apex_interference_count",
            "",
        ),
        "shape_supported_fraction": overlay_summary.get("shape_supported_fraction", ""),
        "global_apex_interference_fraction": overlay_summary.get(
            "global_apex_interference_fraction",
            "",
        ),
        "local_apex_supported_count": overlay_summary.get(
            "local_apex_supported_count",
            "",
        ),
        "local_apex_supported_fraction": overlay_summary.get(
            "local_apex_supported_fraction",
            "",
        ),
        "review_classification": classification,
        "recommended_next_action": _recommended_next_action(classification),
        "review_priority_score": priority,
        "row_flags": review_row.get("row_flags", ""),
        "primary_evidence": review_row.get("primary_evidence", ""),
        "reason": review_row.get("reason", ""),
    }


def _review_classification(
    *,
    overlay_summary: Mapping[str, Any],
    height_ratio: float | None,
    rescue_count: int,
) -> str:
    verdict = str(overlay_summary.get("family_verdict", ""))
    if verdict == "ms1_shape_supports_family_backfill":
        if overlay_summary.get("dda_trigger_limited_ms2_support") is True:
            return "ms1_supported_dda_limited_backfill"
        return "ms1_supported_backfill"
    if verdict == "review_required_neighboring_ms1_interference":
        return "neighboring_interference_review"
    if verdict == "review_required_low_ms1_assessable_coverage":
        return "low_ms1_assessable_coverage_review"
    if verdict == "review_required_uncertain_ms1_shape":
        return "uncertain_shape_review"
    if verdict:
        return "overlay_review_required"
    if height_ratio is not None and height_ratio >= 1.25 and rescue_count >= 70:
        return "needs_ms1_overlay_high_priority"
    return "needs_ms1_overlay"


def _recommended_next_action(classification: str) -> str:
    if classification.startswith("ms1_supported"):
        return "keep_primary_candidate_with_ms1_support_note"
    if classification in {
        "neighboring_interference_review",
        "low_ms1_assessable_coverage_review",
        "uncertain_shape_review",
        "overlay_review_required",
    }:
        return "manual_review_before_gate_change"
    if classification == "needs_ms1_overlay_high_priority":
        return "generate_overlay_first"
    return "generate_overlay_if_review_budget_allows"


def _suggested_overlay_hint(
    *,
    family_id: str,
    mz: str,
    center_rt: str,
) -> dict[str, str]:
    rt = _float(center_rt)
    if rt is None or not mz:
        return {
            "suggested_rt_min": "",
            "suggested_rt_max": "",
            "suggested_output_prefix": "",
            "suggested_overlay_command_args": "",
        }
    rt_min = max(0.0, rt - SUGGESTED_OVERLAY_HALF_WINDOW_MIN)
    rt_max = rt + SUGGESTED_OVERLAY_HALF_WINDOW_MIN
    prefix = f"{family_id.lower()}_ms1_overlay_review"
    args = (
        f"--family-id {family_id} "
        f"--mz {mz} "
        f"--rt-min {rt_min:.4f} "
        f"--rt-max {rt_max:.4f} "
        f"--family-center-rt {rt:.4f} "
        f"--output-prefix {prefix}"
    )
    return {
        "suggested_rt_min": f"{rt_min:.4f}",
        "suggested_rt_max": f"{rt_max:.4f}",
        "suggested_output_prefix": prefix,
        "suggested_overlay_command_args": args,
    }


def _is_candidate_review_row(
    row: Mapping[str, str],
    *,
    neutral_loss_tag: str,
    max_detected_count: int,
    min_rescue_count: int,
    min_accepted_count: int,
) -> bool:
    if row.get("include_in_primary_matrix") != "TRUE":
        return False
    if row.get("neutral_loss_tag") != neutral_loss_tag:
        return False
    detected_count = int(_float(row.get("detected_count")) or 0)
    rescue_count = int(_float(row.get("accepted_backfill_count")) or 0)
    accepted_count = int(_float(row.get("accepted_cell_count")) or 0)
    return (
        2 <= detected_count <= max_detected_count
        and rescue_count >= min_rescue_count
        and accepted_count >= min_accepted_count
    )


def _summary_rows(
    candidates: Sequence[Mapping[str, Any]],
    queue: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    classifications = Counter(str(row["review_classification"]) for row in candidates)
    overlay_statuses = Counter(str(row["overlay_status"]) for row in candidates)
    summary = [
        {"metric": "candidate_count", "value": str(len(candidates))},
        {"metric": "image_queue_count", "value": str(len(queue))},
    ]
    summary.extend(
        {"metric": f"classification:{key}", "value": str(value)}
        for key, value in sorted(classifications.items())
    )
    summary.extend(
        {"metric": f"overlay_status:{key}", "value": str(value)}
        for key, value in sorted(overlay_statuses.items())
    )
    return summary


def _classification_sort_key(classification: str) -> int:
    order = {
        "neighboring_interference_review": 0,
        "low_ms1_assessable_coverage_review": 1,
        "uncertain_shape_review": 2,
        "overlay_review_required": 3,
        "needs_ms1_overlay_high_priority": 4,
        "needs_ms1_overlay": 5,
        "ms1_supported_dda_limited_backfill": 6,
        "ms1_supported_backfill": 7,
    }
    return order.get(classification, 99)


def _median(values: Iterable[float | None]) -> float | None:
    finite = [value for value in values if value is not None]
    if not finite:
        return None
    finite.sort()
    midpoint = len(finite) // 2
    if len(finite) % 2:
        return finite[midpoint]
    return (finite[midpoint - 1] + finite[midpoint]) / 2.0


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _safe_ratio(
    numerator: float | int | None,
    denominator: float | int | None,
) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _safe_fraction(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator
