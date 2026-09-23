"""Classification logic for low MS1 coverage review diagnostics."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.low_ms1_coverage_review_loaders import (
    _backfill_seed_rows_by_family,
    _load_discovery_candidate,
    _read_tsv,
    _trace_summary_path,
)
from tools.diagnostics.low_ms1_coverage_review_models import (
    ALIGNMENT_REVIEW_REQUIRED_COLUMNS,
    APEX_AWARE_QUEUE_BUCKETS,
    ASSESSABLE_FRACTION_MIN,
    LOW_COVERAGE_CLASSIFICATION,
    REVIEW_REQUIRED_COLUMNS,
    SEED_APEX_DELTA_CONCERN_SEC,
    SEED_OVERLAY_QUEUE_BUCKETS,
    SEED_RT_SPAN_CONCERN_MIN,
    SELECTED_APEX_IN_WINDOW_MIN,
    SELECTED_APEX_OVERLAY_PADDING_MIN,
    TRACE_DISCOVERY_JOIN_COLUMNS,
    TRACE_REQUIRED_COLUMNS,
    ZERO_TRACE_INSIDE_WINDOW_FRACTION_MIN,
)

SeedGroupKey = tuple[str, str, str, str, str]
SeedGroupItem = tuple[SeedGroupKey, int]


def build_audit(
    *,
    review_candidates_tsv: Path,
    alignment_dir: Path,
    overlay_dir: Path,
    discovery_dir: Path | None = None,
    backfill_seed_audit_tsv: Path | None = None,
) -> dict[str, Any]:
    candidates = _read_tsv(
        review_candidates_tsv,
        required_columns=REVIEW_REQUIRED_COLUMNS,
    )
    alignment_review = _read_tsv(
        alignment_dir / "alignment_review.tsv",
        required_columns=ALIGNMENT_REVIEW_REQUIRED_COLUMNS,
    )
    alignment_by_family = {row["feature_family_id"]: row for row in alignment_review}
    seed_rows_by_family = _backfill_seed_rows_by_family(backfill_seed_audit_tsv)

    summary_rows: list[dict[str, Any]] = []
    detail_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("review_classification") != LOW_COVERAGE_CLASSIFICATION:
            continue
        family_id = candidate["feature_family_id"]
        trace_path = _trace_summary_path(candidate, overlay_dir)
        trace_rows = _read_tsv(
            trace_path,
            required_columns=(
                TRACE_REQUIRED_COLUMNS + TRACE_DISCOVERY_JOIN_COLUMNS
                if discovery_dir is not None
                else TRACE_REQUIRED_COLUMNS
            ),
        )
        family_summary, family_details = _audit_family(
            candidate=candidate,
            alignment_row=alignment_by_family.get(family_id, {}),
            trace_rows=trace_rows,
            trace_summary_path=trace_path,
            discovery_dir=discovery_dir,
            seed_rows=seed_rows_by_family.get(family_id, ()),
        )
        summary_rows.append(family_summary)
        detail_rows.extend(family_details)

    summary_rows.sort(
        key=lambda row: (
            str(row["root_cause_bucket"]),
            -float(row["review_risk_score"]),
            str(row["feature_family_id"]),
        )
    )
    apex_aware_queue = [
        _apex_aware_queue_row(row)
        for row in summary_rows
        if row["root_cause_bucket"] in APEX_AWARE_QUEUE_BUCKETS
    ]
    seed_aware_queue = [
        _seed_aware_queue_row(
            {
                **seed_row,
                "feature_family_id": row["feature_family_id"],
                "family_center_mz": row.get("family_center_mz", ""),
                "family_center_rt": row.get("family_center_rt", ""),
                "seed_context_bucket": row.get("seed_context_bucket", ""),
            }
        )
        for row in summary_rows
        if row["seed_context_bucket"] in SEED_OVERLAY_QUEUE_BUCKETS
        for seed_row in row["seed_overlay_rows"]
    ]
    return {
        "review_candidates_tsv": str(review_candidates_tsv),
        "alignment_dir": str(alignment_dir),
        "overlay_dir": str(overlay_dir),
        "discovery_dir": str(discovery_dir) if discovery_dir is not None else "",
        "backfill_seed_audit_tsv": (
            str(backfill_seed_audit_tsv) if backfill_seed_audit_tsv is not None else ""
        ),
        "thresholds": {
            "assessable_fraction_min": ASSESSABLE_FRACTION_MIN,
            "selected_apex_in_window_min": SELECTED_APEX_IN_WINDOW_MIN,
            "zero_trace_inside_window_fraction_min": (
                ZERO_TRACE_INSIDE_WINDOW_FRACTION_MIN
            ),
        },
        "summary": summary_rows,
        "rows": detail_rows,
        "selected_apex_overlay_queue": apex_aware_queue,
        "seed_overlay_queue": seed_aware_queue,
    }


def _audit_family(
    *,
    candidate: Mapping[str, str],
    alignment_row: Mapping[str, str],
    trace_rows: Sequence[Mapping[str, str]],
    trace_summary_path: Path,
    discovery_dir: Path | None,
    seed_rows: Sequence[Mapping[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rt_min = _required_float(candidate.get("suggested_rt_min"), "suggested_rt_min")
    rt_max = _required_float(candidate.get("suggested_rt_max"), "suggested_rt_max")
    family_id = candidate["feature_family_id"]
    cells = [
        row for row in trace_rows if row.get("status") in {"detected", "backfilled"}
    ]
    seed_by_sample = {row["sample_stem"]: row for row in seed_rows}
    details = [
        _detail_row(
            family_id=family_id,
            row=row,
            rt_min=rt_min,
            rt_max=rt_max,
            seed_row=seed_by_sample.get(row.get("sample_stem", "")),
        )
        for row in cells
    ]
    apex_values = _detail_apex_values(details)
    total = len(details)
    outside_window_count = sum(1 for row in details if not row["in_requested_window"])
    zero_trace_total_count = sum(1 for row in details if not row["trace_has_signal"])
    zero_trace_inside_window_count = sum(
        1
        for row in details
        if row["in_requested_window"] and not row["trace_has_signal"]
    )
    assessable_count = total - zero_trace_total_count
    split_supported_count = sum(
        1 for row in details if row["region_shadow_verdict"] == "split_supported"
    )
    status_counts = Counter(str(row["status"]) for row in details)
    region_counts = Counter(str(row["region_shadow_verdict"]) for row in details)
    selected_in_window_fraction = _safe_fraction(total - outside_window_count, total)
    computed_assessable_fraction = _safe_fraction(assessable_count, total)
    candidate_assessable_fraction = _float(
        candidate.get("global_apex_assessable_fraction"),
    )
    assessable_fraction = _first_float(
        candidate_assessable_fraction,
        computed_assessable_fraction,
    )
    candidate_selected_in_window_fraction = _float(
        candidate.get("selected_apex_in_trace_window_fraction"),
    )
    selected_in_window_fraction_for_decision = _first_float(
        candidate_selected_in_window_fraction,
        selected_in_window_fraction,
    )
    zero_inside_fraction = _safe_fraction(zero_trace_inside_window_count, total)
    split_supported_fraction = _safe_fraction(split_supported_count, total)
    event_cluster_count = int(_float(alignment_row.get("event_cluster_count")) or 0)
    seed_evidence = _detected_seed_evidence_summary(
        trace_rows=trace_rows,
        discovery_dir=discovery_dir,
    )
    seed_context = _backfill_seed_context_summary(seed_rows)
    bucket = _root_cause_bucket(
        selected_in_window_fraction=selected_in_window_fraction_for_decision,
        assessable_fraction=assessable_fraction,
        zero_inside_fraction=zero_inside_fraction,
        event_cluster_count=event_cluster_count,
        seed_context_bucket=str(seed_context["seed_context_bucket"]),
    )
    interpretation = _production_interpretation(bucket)
    recommendation = _recommended_next_action(bucket)
    risk_score = (
        (1.0 - assessable_fraction)
        + (1.0 - selected_in_window_fraction_for_decision)
        + min(event_cluster_count / 5.0, 1.0) * 0.5
    )
    return (
        {
            "feature_family_id": family_id,
            "family_center_mz": candidate.get("family_center_mz", ""),
            "family_center_rt": candidate.get("family_center_rt", ""),
            "rt_window_min": rt_min,
            "rt_window_max": rt_max,
            "event_cluster_count": event_cluster_count,
            "event_member_count": alignment_row.get("event_member_count", ""),
            "identity_decision": alignment_row.get("identity_decision", ""),
            "primary_evidence": alignment_row.get("primary_evidence", ""),
            "row_flags": alignment_row.get("row_flags", ""),
            "detected_count": candidate.get("detected_count", ""),
            "accepted_backfill_count": candidate.get("accepted_backfill_count", ""),
            "detected_backfilled_count": candidate.get("detected_backfilled_count", ""),
            "trace_cell_count": total,
            "selected_apex_rt_min": min(apex_values) if apex_values else None,
            "selected_apex_rt_max": max(apex_values) if apex_values else None,
            "assessable_count": assessable_count,
            "assessable_fraction": assessable_fraction,
            "trace_recomputed_assessable_fraction": computed_assessable_fraction,
            "assessable_fraction_delta": _delta(
                candidate_assessable_fraction,
                computed_assessable_fraction,
            ),
            "selected_apex_in_window_count": total - outside_window_count,
            "selected_apex_in_window_fraction": (
                selected_in_window_fraction_for_decision
            ),
            "trace_recomputed_selected_apex_in_window_fraction": (
                selected_in_window_fraction
            ),
            "selected_apex_in_window_fraction_delta": _delta(
                candidate_selected_in_window_fraction,
                selected_in_window_fraction,
            ),
            "selected_apex_outside_window_count": outside_window_count,
            "selected_apex_outside_window_fraction": _safe_fraction(
                outside_window_count,
                total,
            ),
            "zero_trace_total_count": zero_trace_total_count,
            "zero_trace_total_fraction": _safe_fraction(zero_trace_total_count, total),
            "zero_trace_inside_window_count": zero_trace_inside_window_count,
            "zero_trace_inside_window_fraction": zero_inside_fraction,
            "split_supported_count": split_supported_count,
            "split_supported_fraction": split_supported_fraction,
            "status_counts": _format_counts(status_counts),
            "region_verdict_counts": _format_counts(region_counts),
            **seed_evidence,
            **seed_context,
            "root_cause_bucket": bucket,
            "production_interpretation": interpretation,
            "recommended_next_action": recommendation,
            "review_risk_score": risk_score,
            "trace_summary_path": str(trace_summary_path),
            "alignment_reason": alignment_row.get("reason", ""),
        },
        details,
    )


def _detail_row(
    *,
    family_id: str,
    row: Mapping[str, str],
    rt_min: float,
    rt_max: float,
    seed_row: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    apex_rt = _float(row.get("cell_apex_rt"))
    trace_max = _float(row.get("trace_max_intensity")) or 0.0
    in_window = apex_rt is not None and rt_min <= apex_rt <= rt_max
    trace_has_signal = trace_max > 0.0
    flags: list[str] = []
    if not in_window:
        flags.append("selected_apex_outside_window")
    if not trace_has_signal:
        flags.append("zero_family_center_xic")
    return {
        "feature_family_id": family_id,
        "sample_stem": row.get("sample_stem", ""),
        "status": row.get("status", ""),
        "cell_area": row.get("cell_area", ""),
        "cell_height": row.get("cell_height", ""),
        "cell_apex_rt": row.get("cell_apex_rt", ""),
        "in_requested_window": in_window,
        "trace_has_signal": trace_has_signal,
        "trace_max_intensity": row.get("trace_max_intensity", ""),
        "trace_apex_rt": row.get("trace_apex_rt", ""),
        "global_trace_apex_delta_min": row.get("global_trace_apex_delta_min", ""),
        "local_window_to_global_max_ratio": row.get(
            "local_window_to_global_max_ratio",
            "",
        ),
        "region_shadow_verdict": row.get("region_shadow_verdict", ""),
        "source_candidate_id": row.get("source_candidate_id", ""),
        "backfill_seed_mz": (seed_row or {}).get("backfill_seed_mz", ""),
        "backfill_seed_rt": (seed_row or {}).get("backfill_seed_rt", ""),
        "backfill_request_rt_min": (seed_row or {}).get("backfill_request_rt_min", ""),
        "backfill_request_rt_max": (seed_row or {}).get("backfill_request_rt_max", ""),
        "backfill_request_ppm": (seed_row or {}).get("backfill_request_ppm", ""),
        "backfill_apex_delta_sec": (seed_row or {}).get(
            "backfill_apex_delta_sec",
            "",
        ),
        "issue_flags": ";".join(flags),
    }


def _detail_apex_values(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        parsed = _float(str(row.get("cell_apex_rt", "")))
        if parsed is not None:
            values.append(parsed)
    return values


def _root_cause_bucket(
    *,
    selected_in_window_fraction: float,
    assessable_fraction: float,
    zero_inside_fraction: float,
    event_cluster_count: int,
    seed_context_bucket: str,
) -> str:
    if selected_in_window_fraction < SELECTED_APEX_IN_WINDOW_MIN:
        if event_cluster_count > 1:
            return "rt_window_or_multiseed_shift"
        return "rt_window_mismatch"
    if assessable_fraction < ASSESSABLE_FRACTION_MIN:
        if seed_context_bucket in SEED_OVERLAY_QUEUE_BUCKETS:
            return "seed_aware_overlay_required"
        if zero_inside_fraction >= ZERO_TRACE_INSIDE_WINDOW_FRACTION_MIN:
            return "single_center_xic_not_supported"
        if event_cluster_count > 1:
            return "multi_seed_overlay_limitation"
        return "trace_assessment_incomplete"
    return "coverage_warning_not_reproduced"


def _production_interpretation(bucket: str) -> str:
    if bucket == "single_center_xic_not_supported":
        return (
            "selected RT is mostly inside the requested window, but family-center "
            "MS1 XIC is absent for many rescued cells; primary backfill support is "
            "not established by the current evidence"
        )
    if bucket == "seed_aware_overlay_required":
        return (
            "single-center overlay is insufficient because rescued cells came from "
            "multiple owner-backfill seeds or show large seed-to-apex deltas; run "
            "seed-specific overlays before changing primary backfill gates"
        )
    if bucket == "rt_window_or_multiseed_shift":
        return (
            "selected RT often falls outside the single family-centered overlay "
            "window; distinguish real RT drift from mixed seed centers before "
            "changing the primary gate"
        )
    if bucket == "multi_seed_overlay_limitation":
        return (
            "single-center overlay may be under-measuring a multi-seed family; "
            "seed-aware overlay is required before demotion"
        )
    if bucket == "rt_window_mismatch":
        return "single-center RT window does not cover selected cells"
    if bucket == "trace_assessment_incomplete":
        return "coverage warning persists but current trace summary is incomplete"
    return "low-coverage warning did not reproduce in the supplied trace summary"


def _recommended_next_action(bucket: str) -> str:
    if bucket == "single_center_xic_not_supported":
        return (
            "do_not_promote_ms1_shape_gate; consider stricter primary backfill review"
        )
    if bucket == "seed_aware_overlay_required":
        return "run_seed_aware_overlay_before_gate_change"
    if bucket == "rt_window_or_multiseed_shift":
        return "run_seed_aware_or_rt_warped_overlay_before_gate_change"
    if bucket == "multi_seed_overlay_limitation":
        return "run_seed_aware_overlay"
    if bucket == "rt_window_mismatch":
        return "inspect_rt_window_and_irt_context"
    return "manual_review_before_gate_change"


def _detected_seed_evidence_summary(
    *,
    trace_rows: Sequence[Mapping[str, str]],
    discovery_dir: Path | None,
) -> dict[str, Any]:
    detected_source_ids = [
        row.get("source_candidate_id", "")
        for row in trace_rows
        if row.get("status") == "detected" and row.get("source_candidate_id")
    ]
    if discovery_dir is None:
        return {
            "detected_seed_candidate_count": len(detected_source_ids),
            "detected_seed_joined_count": "",
            "missing_detected_seed_candidate_count": "",
            "min_seed_evidence_score": "",
            "median_seed_evidence_score": "",
            "min_seed_event_count": "",
            "max_abs_nl_ppm": "",
            "detected_seed_precursor_mz_span": "",
            "detected_seed_product_mz_span": "",
            "seed_evidence_bucket": "not_provided",
        }
    rows: list[Mapping[str, str]] = []
    missing = 0
    for source_id in detected_source_ids:
        candidate = _load_discovery_candidate(discovery_dir, source_id)
        if candidate is None:
            missing += 1
        else:
            rows.append(candidate)
    evidence_scores = _numeric_values(row.get("evidence_score") for row in rows)
    seed_events = _numeric_values(row.get("seed_event_count") for row in rows)
    nl_ppm = [
        abs(value)
        for value in _numeric_values(
            row.get("neutral_loss_mass_error_ppm") for row in rows
        )
    ]
    precursor_mz = _numeric_values(row.get("precursor_mz") for row in rows)
    product_mz = _numeric_values(row.get("product_mz") for row in rows)
    bucket = _seed_evidence_bucket(
        missing_count=missing,
        seed_events=seed_events,
        nl_ppm=nl_ppm,
    )
    return {
        "detected_seed_candidate_count": len(detected_source_ids),
        "detected_seed_joined_count": len(rows),
        "missing_detected_seed_candidate_count": missing,
        "min_seed_evidence_score": min(evidence_scores) if evidence_scores else "",
        "median_seed_evidence_score": _median(evidence_scores),
        "min_seed_event_count": min(seed_events) if seed_events else "",
        "max_abs_nl_ppm": max(nl_ppm) if nl_ppm else "",
        "detected_seed_precursor_mz_span": _span(precursor_mz),
        "detected_seed_product_mz_span": _span(product_mz),
        "seed_evidence_bucket": bucket,
    }


def _seed_evidence_bucket(
    *,
    missing_count: int,
    seed_events: Sequence[float],
    nl_ppm: Sequence[float],
) -> str:
    if missing_count:
        return "seed_join_incomplete"
    if not seed_events:
        return "no_detected_seed_candidates"
    if min(seed_events) < 2 or (nl_ppm and max(nl_ppm) > 10.0):
        return "weak_detected_seed_evidence"
    return "detected_seed_evidence_consistent"


def _backfill_seed_context_summary(
    seed_rows: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    if not seed_rows:
        return {
            "backfill_seed_row_count": 0,
            "backfill_seed_group_count": 0,
            "backfill_seed_rt_span": "",
            "backfill_seed_rt_distribution": "",
            "seed_apex_far_count": "",
            "seed_apex_far_fraction": "",
            "seed_context_bucket": "not_provided",
            "seed_overlay_rows": (),
        }
    grouped: Counter[SeedGroupKey] = Counter(
        (
            row.get("backfill_seed_mz", ""),
            row.get("backfill_seed_rt", ""),
            row.get("backfill_request_rt_min", ""),
            row.get("backfill_request_rt_max", ""),
            row.get("backfill_request_ppm", ""),
        )
        for row in seed_rows
    )
    seed_rt_values = _numeric_values(row.get("backfill_seed_rt") for row in seed_rows)
    seed_rt_span = _span(seed_rt_values)
    apex_deltas = [
        abs(value)
        for value in _numeric_values(
            row.get("backfill_apex_delta_sec") for row in seed_rows
        )
    ]
    far_count = sum(1 for value in apex_deltas if value > SEED_APEX_DELTA_CONCERN_SEC)
    far_fraction = _safe_fraction(far_count, len(apex_deltas))
    seed_group_count = len(grouped)
    sorted_seed_groups = _sorted_seed_groups(grouped)
    bucket = _seed_context_bucket(
        seed_group_count=seed_group_count,
        seed_rt_span=seed_rt_span,
        seed_apex_far_fraction=far_fraction,
    )
    return {
        "backfill_seed_row_count": len(seed_rows),
        "backfill_seed_group_count": seed_group_count,
        "backfill_seed_rt_span": seed_rt_span,
        "backfill_seed_rt_distribution": _format_seed_distribution(
            sorted_seed_groups,
        ),
        "seed_apex_far_count": far_count,
        "seed_apex_far_fraction": far_fraction,
        "seed_context_bucket": bucket,
        "seed_overlay_rows": _seed_overlay_rows(sorted_seed_groups),
    }


def _seed_context_bucket(
    *,
    seed_group_count: int,
    seed_rt_span: float | str,
    seed_apex_far_fraction: float,
) -> str:
    if (
        seed_group_count > 1
        and isinstance(seed_rt_span, float)
        and seed_rt_span >= SEED_RT_SPAN_CONCERN_MIN
    ):
        return "multi_seed_family_center_overlay_incomplete"
    if seed_apex_far_fraction >= 0.25:
        return "seed_apex_delta_concern"
    return "seed_context_consistent"


def _sorted_seed_groups(grouped: Counter[SeedGroupKey]) -> tuple[SeedGroupItem, ...]:
    return tuple(sorted(grouped.items(), key=lambda item: (-item[1], item[0])))


def _format_seed_distribution(seed_groups: Sequence[SeedGroupItem]) -> str:
    parts = []
    for key, count in seed_groups:
        seed_mz, seed_rt, rt_min, rt_max, ppm = key
        parts.append(
            f"mz={seed_mz},rt={seed_rt},window={rt_min}-{rt_max},ppm={ppm}:{count}"
        )
    return ";".join(parts)


def _seed_overlay_rows(
    seed_groups: Sequence[SeedGroupItem],
) -> tuple[dict[str, Any], ...]:
    rows = []
    for index, (key, count) in enumerate(seed_groups, start=1):
        seed_mz, seed_rt, rt_min, rt_max, ppm = key
        rows.append(
            {
                "seed_index": index,
                "backfill_seed_mz": seed_mz,
                "backfill_seed_rt": seed_rt,
                "suggested_rt_min": rt_min,
                "suggested_rt_max": rt_max,
                "ppm": ppm,
                "rescued_cell_count": count,
            }
        )
    return tuple(rows)


def _apex_aware_queue_row(row: Mapping[str, Any]) -> dict[str, Any]:
    family_id = str(row["feature_family_id"])
    apex_min = _float(str(row.get("selected_apex_rt_min", "")))
    apex_max = _float(str(row.get("selected_apex_rt_max", "")))
    if apex_min is None or apex_max is None:
        rt_min = row["rt_window_min"]
        rt_max = row["rt_window_max"]
    else:
        rt_min = max(0.0, apex_min - SELECTED_APEX_OVERLAY_PADDING_MIN)
        rt_max = apex_max + SELECTED_APEX_OVERLAY_PADDING_MIN
    prefix = f"{family_id.lower()}_selected_apex_window_overlay"
    return {
        "feature_family_id": family_id,
        "family_center_mz": row.get("family_center_mz", ""),
        "family_center_rt": row.get("family_center_rt", ""),
        "suggested_rt_min": rt_min,
        "suggested_rt_max": rt_max,
        "suggested_output_prefix": prefix,
        "root_cause_bucket": row.get("root_cause_bucket", ""),
        "selected_apex_rt_min": row.get("selected_apex_rt_min", ""),
        "selected_apex_rt_max": row.get("selected_apex_rt_max", ""),
        "original_rt_min": row.get("rt_window_min", ""),
        "original_rt_max": row.get("rt_window_max", ""),
    }


def _seed_aware_queue_row(seed_row: Mapping[str, Any]) -> dict[str, Any]:
    family_id = str(seed_row["feature_family_id"])
    prefix = f"{family_id.lower()}_seed{seed_row['seed_index']}_overlay"
    return {
        "feature_family_id": family_id,
        "family_center_mz": seed_row.get("family_center_mz", ""),
        "family_center_rt": seed_row.get("family_center_rt", ""),
        "backfill_seed_mz": seed_row.get("backfill_seed_mz", ""),
        "backfill_seed_rt": seed_row.get("backfill_seed_rt", ""),
        "suggested_rt_min": seed_row.get("suggested_rt_min", ""),
        "suggested_rt_max": seed_row.get("suggested_rt_max", ""),
        "ppm": seed_row.get("ppm", ""),
        "rescued_cell_count": seed_row.get("rescued_cell_count", ""),
        "suggested_output_prefix": prefix,
        "seed_context_bucket": seed_row.get("seed_context_bucket", ""),
    }


def _format_counts(counter: Counter[str]) -> str:
    return ";".join(f"{key}:{counter[key]}" for key in sorted(counter))


def _required_float(value: str | None, column: str) -> float:
    parsed = _float(value)
    if parsed is None:
        raise ValueError(f"invalid numeric value for {column}: {value!r}")
    return parsed


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _safe_fraction(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _numeric_values(values: Iterable[str | None]) -> list[float]:
    parsed: list[float] = []
    for value in values:
        number = _float(value)
        if number is not None:
            parsed.append(number)
    return parsed


def _median(values: Sequence[float]) -> float | str:
    if not values:
        return ""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _span(values: Sequence[float]) -> float | str:
    if not values:
        return ""
    return max(values) - min(values)


def _first_float(*values: float | None) -> float:
    for value in values:
        if value is not None:
            return value
    return 0.0


def _delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right
