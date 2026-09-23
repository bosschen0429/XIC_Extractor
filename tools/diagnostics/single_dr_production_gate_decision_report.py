"""Decision report for single-dR primary matrix gate candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools.diagnostics.single_dr_gate_decision_loaders import (
    load_discovery_candidates,
    load_rt_context,
    load_targeted_istd_context,
    read_tsv,
)
from tools.diagnostics.single_dr_gate_decision_writers import write_outputs
from xic_extractor.alignment.identity_gates import (
    EXTREME_BACKFILL_REASON,
    WEAK_SEED_BACKFILL_REASON,
    WEAK_SEED_TOLERATED_REASON,
    DetectedSeedRef,
    SeedQualitySummary,
    is_dr_neutral_loss_tag,
    lookup_seed_candidate,
    summarize_detected_seed_quality,
)
from xic_extractor.alignment.promotion_policy import (
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_CONFLICT_REASON,
    BACKFILL_MS2_CONFLICT_REASON,
    BACKFILL_MS2_CONTEXT_BLOCKED_REASON,
    BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    RESCUE_ONLY_BLOCKED_REASON,
    classify_backfill_promotion,
    evidence_from_tsv_rows,
)

_REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "detected_count",
    "review_backfill_count",
    "include_in_primary_matrix",
)
_BACKFILL_EVIDENCE_COLUMNS = (
    "backfill_ms1_pattern_status",
    "backfill_ms1_pattern_evidence_level",
    "backfill_qc_reference_status",
    "backfill_qc_reference_evidence_level",
    "backfill_matrix_rt_drift_status",
    "backfill_drift_evidence_level",
    "backfill_drift_compatible_status",
    "backfill_drift_corrected_delta_sec",
    "backfill_candidate_ms2_pattern_status",
    "backfill_candidate_ms2_evidence_level",
    "backfill_ms2_trigger_scan_count",
    "backfill_strict_nl_scan_count",
    "backfill_ms2_trace_strength",
    "backfill_dda_missing_nl_policy_status",
    "backfill_family_ms2_required_tag_status",
    "backfill_evidence_reason",
)
_CELLS_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "primary_matrix_area",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    *_BACKFILL_EVIDENCE_COLUMNS,
)

_EXTREME_GATE_ID = "dr_extreme_backfill_dependency"
_WEAK_SEED_GATE_ID = "dr_weak_seed_backfill_dependency"
_WEAK_SEED_TOLERATED_GATE_ID = "dr_weak_seed_tolerated_watch"
_DUPLICATE_GATE_ID = "dr_duplicate_rescue_pressure"
_POLICY_BLOCK_REASONS = {
    BACKFILL_MS1_PATTERN_BLOCKED_REASON,
    BACKFILL_MS1_PATTERN_CONFLICT_REASON,
    BACKFILL_MS2_CONTEXT_BLOCKED_REASON,
    BACKFILL_MS2_CONFLICT_REASON,
    BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
    LOW_MS1_COVERAGE_BLOCKED_REASON,
    MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    NEIGHBOR_INTERFERENCE_BLOCKED_REASON,
    RESCUE_ONLY_BLOCKED_REASON,
    "duplicate_claim_pressure",
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_decision_report(
            alignment_dir=args.alignment_dir,
            discovery_batch_index=args.discovery_batch_index,
            rt_normalization_families_tsv=args.rt_normalization_families_tsv,
            targeted_istd_benchmark_json=args.targeted_istd_benchmark_json,
        )
        write_outputs(args.output_dir, result)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"single-dR gate decision report: {args.output_dir}")
    return 0


def build_decision_report(
    *,
    alignment_dir: Path,
    discovery_batch_index: Path | None = None,
    rt_normalization_families_tsv: Path | None = None,
    targeted_istd_benchmark_json: Path | None = None,
) -> dict[str, Any]:
    review_rows = read_tsv(
        alignment_dir / "alignment_review.tsv",
        required_columns=_REVIEW_REQUIRED_COLUMNS,
    )
    cell_rows = read_tsv(
        alignment_dir / "alignment_cells.tsv",
        required_columns=_CELLS_REQUIRED_COLUMNS,
    )
    cells_by_family = _cells_by_family(cell_rows)
    sample_order = _sample_order(cell_rows)
    discovery = load_discovery_candidates(discovery_batch_index)
    rt_context = load_rt_context(rt_normalization_families_tsv)
    benchmark = load_targeted_istd_context(targeted_istd_benchmark_json)

    families: list[dict[str, Any]] = []
    detected_cells: list[dict[str, Any]] = []
    for review_row in review_rows:
        if not _is_single_dr_gate_row(review_row):
            continue
        family_id = review_row["feature_family_id"]
        family_cells = cells_by_family.get(family_id, ())
        seed_quality = _seed_quality(
            family_cells,
            discovery=discovery,
        )
        family_rt_context = rt_context.get(family_id, "")
        family_istd = benchmark["families"].get(family_id, {})
        family = _classify_family(
            review_row,
            family_cells,
            sample_count=len(sample_order),
            seed_quality=seed_quality,
            rt_context=family_rt_context,
            targeted_istd=family_istd,
        )
        families.append(family)
        detected_cells.extend(
            _detected_cell_rows(family_cells, discovery=discovery),
        )

    families.sort(
        key=lambda row: (
            _risk_sort_key(str(row["risk_classification"])),
            -int(row["q_rescue"]),
            str(row["feature_family_id"]),
        ),
    )
    detected_cells.sort(
        key=lambda row: (
            str(row["feature_family_id"]),
            str(row["sample_stem"]),
        ),
    )
    gate_candidates = _gate_candidates(families)
    summary = _summary_rows(
        alignment_dir=alignment_dir,
        sample_count=len(sample_order),
        families=families,
        gate_candidates=gate_candidates,
    )
    return {
        "alignment_dir": str(alignment_dir),
        "sample_count": len(sample_order),
        "enrichment": {
            "discovery_batch_index": (
                str(discovery_batch_index)
                if discovery_batch_index is not None
                else "not_provided"
            ),
            "rt_normalization_families_tsv": (
                str(rt_normalization_families_tsv)
                if rt_normalization_families_tsv is not None
                else "not_provided"
            ),
            "targeted_istd_benchmark_json": (
                str(targeted_istd_benchmark_json)
                if targeted_istd_benchmark_json is not None
                else "not_provided"
            ),
            "discovery_status": discovery["status"],
            "rt_context_status": rt_context["status"],
            "targeted_istd_status": benchmark["status"],
        },
        "summary": summary,
        "families": families,
        "detected_cells": detected_cells,
        "gate_candidates": gate_candidates,
    }


def _classify_family(
    review_row: Mapping[str, str],
    cells: tuple[dict[str, str], ...],
    *,
    sample_count: int,
    seed_quality: SeedQualitySummary,
    rt_context: str,
    targeted_istd: Mapping[str, Any],
) -> dict[str, Any]:
    q_detected = _int_value(
        review_row.get("quantifiable_detected_count", "")
        or review_row.get("detected_count", ""),
    )
    q_rescue = _int_value(
        review_row.get("quantifiable_backfill_count", "")
        or review_row.get("accepted_backfill_count", ""),
    )
    denominator = sample_count or len(cells) or 1
    rescue_fraction = q_rescue / denominator
    duplicate_count = _int_value(review_row.get("duplicate_assigned_count", ""))
    row_flags = review_row.get("row_flags", "")
    policy_evidence = evidence_from_tsv_rows(
        review_row,
        cells,
        seed_quality=seed_quality,
        sample_count=denominator,
    )
    dependency = policy_evidence.backfill_dependency
    promotion = classify_backfill_promotion(policy_evidence)

    if promotion.unavailable:
        classification = "unavailable_backfill_owner_evidence"
    elif promotion.supported:
        classification = "supported_backfill_capped"
    elif promotion.reason == NEIGHBOR_INTERFERENCE_BLOCKED_REASON:
        classification = "blocked_neighboring_ms1_interference"
    elif promotion.reason == LOW_MS1_COVERAGE_BLOCKED_REASON:
        classification = "blocked_low_ms1_assessable_coverage"
    elif promotion.reason in {
        BACKFILL_MS1_PATTERN_BLOCKED_REASON,
        BACKFILL_MS1_PATTERN_CONFLICT_REASON,
        BACKFILL_MS2_CONTEXT_BLOCKED_REASON,
        BACKFILL_MS2_CONFLICT_REASON,
        BACKFILL_RT_EXPLANATION_BLOCKED_REASON,
        MISSING_BACKFILL_EVIDENCE_BLOCKED_REASON,
    }:
        classification = "blocked_missing_backfill_identity_evidence"
    elif promotion.reason == RESCUE_ONLY_BLOCKED_REASON:
        classification = "blocked_rescue_only"
    elif dependency == EXTREME_BACKFILL_REASON:
        classification = "risky_extreme_backfill"
    elif dependency == WEAK_SEED_BACKFILL_REASON:
        classification = "risky_weak_seed_backfill"
    elif dependency == WEAK_SEED_TOLERATED_REASON:
        classification = "watch_weak_seed_tolerated"
    elif _is_duplicate_rescue_watch(
        q_detected=q_detected,
        q_rescue=q_rescue,
        rescue_fraction=rescue_fraction,
        duplicate_count=duplicate_count,
        row_flags=row_flags,
    ):
        classification = "watch_duplicate_rescue"
    elif q_detected >= 5:
        classification = "strong"
    else:
        classification = "weak"

    labels = targeted_istd.get("target_labels", ())
    statuses = targeted_istd.get("statuses", ())
    return {
        "feature_family_id": review_row["feature_family_id"],
        "neutral_loss_tag": review_row.get("neutral_loss_tag", ""),
        "risk_classification": classification,
        "rt_context": rt_context or "",
        "include_in_primary_matrix": _is_true(
            review_row.get("include_in_primary_matrix", ""),
        ),
        "q_detected": q_detected,
        "q_rescue": q_rescue,
        "sample_count": denominator,
        "rescue_fraction": f"{rescue_fraction:.4f}",
        "duplicate_assigned_count": duplicate_count,
        "row_flags": row_flags,
        "promotion_state": promotion.state,
        "promotion_reason": promotion.reason,
        "promotion_flags": ";".join(promotion.flags),
        "supported_rescue_count": promotion.supported_rescue_count,
        "assessed_rescue_count": promotion.assessed_rescue_count,
        "seed_quality_status": seed_quality.status,
        "min_evidence_score": _optional_metric(seed_quality.min_evidence_score),
        "min_seed_event_count": _optional_metric(seed_quality.min_seed_event_count),
        "max_abs_nl_ppm": _optional_metric(seed_quality.max_abs_nl_ppm),
        "min_scan_support_score": _optional_metric(
            seed_quality.min_scan_support_score,
        ),
        "missing_detected_candidate_count": (
            seed_quality.missing_detected_candidate_count
            if seed_quality.available
            else ""
        ),
        "targeted_istd_labels": ";".join(labels),
        "targeted_istd_statuses": ";".join(statuses),
        "family_center_mz": review_row.get("family_center_mz", ""),
        "family_center_rt": review_row.get("family_center_rt", ""),
        "family_product_mz": review_row.get("family_product_mz", ""),
        "family_observed_neutral_loss_da": review_row.get(
            "family_observed_neutral_loss_da",
            "",
        ),
    }


def _is_duplicate_rescue_watch(
    *,
    q_detected: int,
    q_rescue: int,
    rescue_fraction: float,
    duplicate_count: int,
    row_flags: str,
) -> bool:
    flags = set(_split_list(row_flags))
    duplicate_pressure = duplicate_count > 0 or "duplicate_claim_pressure" in flags
    rescue_heavy = rescue_fraction >= 0.50 or "rescue_heavy" in flags
    low_detected_support = q_detected <= 5
    return duplicate_pressure and rescue_heavy and low_detected_support and q_rescue > 0


def _seed_quality(
    cells: tuple[dict[str, str], ...],
    *,
    discovery: Mapping[str, Any],
) -> SeedQualitySummary:
    detected_cells = [cell for cell in cells if cell.get("status") == "detected"]
    if discovery["status"] == "not_provided":
        return summarize_detected_seed_quality(
            (),
            None,
            enrichment_available=False,
        )

    return summarize_detected_seed_quality(
        tuple(
            DetectedSeedRef(
                sample_stem=cell.get("sample_stem", ""),
                source_candidate_id=cell.get("source_candidate_id", ""),
            )
            for cell in detected_cells
        ),
        discovery["candidates"],
        enrichment_available=True,
    )


def _detected_cell_rows(
    cells: tuple[dict[str, str], ...],
    *,
    discovery: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cell in cells:
        if cell.get("status") != "detected":
            continue
        quality = (
            _lookup_candidate_quality(cell, discovery["candidates"])
            if discovery["status"] != "not_provided"
            else None
        )
        rows.append(
            {
                "feature_family_id": cell.get("feature_family_id", ""),
                "sample_stem": cell.get("sample_stem", ""),
                "status": cell.get("status", ""),
                "source_candidate_id": cell.get("source_candidate_id", ""),
                "area": cell.get("area", ""),
                "apex_rt": cell.get("apex_rt", ""),
                "seed_candidate_joined": quality is not None,
                "evidence_score": _quality_value(quality, "evidence_score"),
                "seed_event_count": _quality_value(quality, "seed_event_count"),
                "neutral_loss_mass_error_ppm": _quality_value(
                    quality,
                    "neutral_loss_mass_error_ppm",
                ),
                "ms1_scan_support_score": _quality_value(
                    quality,
                    "ms1_scan_support_score",
                ),
            },
        )
    return rows


def _gate_candidates(families: list[dict[str, Any]]) -> list[dict[str, Any]]:
    family_buckets = _gate_family_buckets(families)
    return [
        _gate_candidate(
            gate_candidate_id=_EXTREME_GATE_ID,
            rule_description=(
                "single dR primary rows with q_detected <= 2 and "
                "rescue_fraction >= 0.70"
            ),
            families=family_buckets.by_classification["risky_extreme_backfill"],
            default_action="implement",
            false_positive_risk_reason=(
                "Most quantification comes from owner backfill while original "
                "neutral-loss detected support is at most two samples."
            ),
        ),
        _gate_candidate(
            gate_candidate_id=_WEAK_SEED_GATE_ID,
            rule_description=(
                "single dR primary rows with q_detected <= 3, "
                "rescue_fraction >= 0.60, and weak detected seed quality"
            ),
            families=family_buckets.by_classification["risky_weak_seed_backfill"],
            default_action="implement",
            false_positive_risk_reason=(
                "Backfill-heavy rows start from low-quality or unjoined "
                "detected seed evidence."
            ),
        ),
        _gate_candidate(
            gate_candidate_id="dr_supported_backfill_capped",
            rule_description=(
                "single dR primary rows whose rescue-heavy support is accepted "
                "only through cell-level evidence and capped confidence"
            ),
            families=family_buckets.by_classification["supported_backfill_capped"],
            default_action="keep_warning",
            false_positive_risk_reason=(
                "The row is production-supported by cell-level MS1/RT evidence, "
                "but high backfill dependency remains visible as a capped warning."
            ),
        ),
        _gate_candidate(
            gate_candidate_id="dr_backfill_policy_blocked",
            rule_description=(
                "single dR rows blocked by the shared cell-evidence promotion policy"
            ),
            families=family_buckets.blocked_families,
            default_action="implement",
            false_positive_risk_reason=(
                "The shared policy could not verify assessable cell-level "
                "MS1/RT evidence for production backfill."
            ),
        ),
        _gate_candidate(
            gate_candidate_id=_DUPLICATE_GATE_ID,
            rule_description=(
                "single dR primary rows with duplicate pressure, rescue-heavy "
                "support, and low detected support"
            ),
            families=family_buckets.by_classification["watch_duplicate_rescue"],
            default_action="keep_warning",
            false_positive_risk_reason=(
                "Duplicate pressure plus rescue-heavy support can indicate a "
                "family still competing for row identity."
            ),
        ),
        _gate_candidate(
            gate_candidate_id=_WEAK_SEED_TOLERATED_GATE_ID,
            rule_description=(
                "single dR primary rows with rescue-heavy support and a "
                "tolerated weak-seed signal"
            ),
            families=family_buckets.by_classification["watch_weak_seed_tolerated"],
            default_action="keep_warning",
            false_positive_risk_reason=(
                "The row passes through detected support or product-authorized "
                "same-peak rescue evidence, but a weak seed signal still needs "
                "to stay visible."
            ),
        ),
    ]


class _GateFamilyBuckets:
    def __init__(self) -> None:
        self.by_classification: defaultdict[str, list[dict[str, Any]]] = defaultdict(
            list,
        )
        self.blocked_families: list[dict[str, Any]] = []


def _gate_family_buckets(families: Sequence[dict[str, Any]]) -> _GateFamilyBuckets:
    buckets = _GateFamilyBuckets()
    for family in families:
        classification = str(family["risk_classification"])
        buckets.by_classification[classification].append(family)
        if classification.startswith("blocked_"):
            buckets.blocked_families.append(family)
    return buckets


def _gate_candidate(
    *,
    gate_candidate_id: str,
    rule_description: str,
    families: list[dict[str, Any]],
    default_action: str,
    false_positive_risk_reason: str,
) -> dict[str, Any]:
    affected_primary_rows = sum(
        1 for row in families if row["include_in_primary_matrix"]
    )
    affected_istd_rows = sum(1 for row in families if row["targeted_istd_labels"])
    affected_known_target_rows = affected_istd_rows
    if not families:
        recommended_action = "reject"
        reason = "No current primary rows match this rule."
    elif affected_istd_rows:
        recommended_action = "keep_warning"
        reason = (
            "The rule affects targeted ISTD-selected families, so it must not "
            "be auto-implemented without manual review."
        )
    else:
        recommended_action = default_action
        reason = _recommendation_reason(default_action)
    return {
        "gate_candidate_id": gate_candidate_id,
        "rule_description": rule_description,
        "affected_primary_rows": affected_primary_rows,
        "affected_istd_rows": affected_istd_rows,
        "affected_known_target_rows": affected_known_target_rows,
        "affected_rows_by_reason": _affected_rows_by_reason(families),
        "false_positive_risk_reason": false_positive_risk_reason,
        "recommended_action": recommended_action,
        "recommendation_reason": reason,
    }


def _recommendation_reason(default_action: str) -> str:
    if default_action == "implement":
        return (
            "No targeted ISTD-selected family is affected; the rule is narrow "
            "enough to move into the next production gate candidate set."
        )
    if default_action == "keep_warning":
        return (
            "The signal is a useful review warning but is not yet strict enough "
            "for automatic demotion."
        )
    return "The rule is not currently actionable."


def _affected_rows_by_reason(families: list[dict[str, Any]]) -> str:
    counts = Counter(str(row["risk_classification"]) for row in families)
    return ";".join(f"{key}={counts[key]}" for key in sorted(counts))


def _summary_rows(
    *,
    alignment_dir: Path,
    sample_count: int,
    families: list[dict[str, Any]],
    gate_candidates: list[dict[str, Any]],
) -> list[dict[str, str]]:
    class_counts = Counter(str(row["risk_classification"]) for row in families)
    action_counts = Counter(str(row["recommended_action"]) for row in gate_candidates)
    rows = [
        {"metric": "alignment_dir", "value": str(alignment_dir)},
        {"metric": "sample_count", "value": str(sample_count)},
        {"metric": "single_dr_gate_rows", "value": str(len(families))},
        {
            "metric": "single_dr_primary_rows",
            "value": str(
                sum(1 for row in families if row["include_in_primary_matrix"]),
            ),
        },
    ]
    for key in (
        "strong",
        "weak",
        "risky_extreme_backfill",
        "risky_weak_seed_backfill",
        "supported_backfill_capped",
        "blocked_neighboring_ms1_interference",
        "blocked_low_ms1_assessable_coverage",
        "blocked_missing_backfill_identity_evidence",
        "blocked_rescue_only",
        "unavailable_backfill_owner_evidence",
        "watch_duplicate_rescue",
        "watch_weak_seed_tolerated",
    ):
        rows.append(
            {
                "metric": f"{key}_rows",
                "value": str(class_counts.get(key, 0)),
            },
        )
    for action in ("implement", "keep_warning", "reject"):
        rows.append(
            {
                "metric": f"gate_candidates_{action}",
                "value": str(action_counts.get(action, 0)),
            },
        )
    return rows


def _lookup_candidate_quality(
    cell: Mapping[str, str],
    candidates: Mapping[tuple[str, str], Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    return lookup_seed_candidate(
        DetectedSeedRef(
            sample_stem=cell.get("sample_stem", ""),
            source_candidate_id=cell.get("source_candidate_id", ""),
        ),
        candidates,
    )


def _is_single_dr_gate_row(row: Mapping[str, str]) -> bool:
    if not is_dr_neutral_loss_tag(row.get("neutral_loss_tag", "")):
        return False
    if _is_true(row.get("include_in_primary_matrix", "")):
        return True
    if row.get("identity_reason", "") in _POLICY_BLOCK_REASONS:
        return True
    row_flags = set(_split_tokens(row.get("row_flags", "")))
    return (
        "high_backfill_dependency" in row_flags
        or "weak_seed_backfill_dependency" in row_flags
    )


def _cells_by_family(
    rows: tuple[dict[str, str], ...],
) -> dict[str, tuple[dict[str, str], ...]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["feature_family_id"]].append(row)
    return {feature_id: tuple(items) for feature_id, items in grouped.items()}


def _sample_order(rows: tuple[dict[str, str], ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        sample = row.get("sample_stem", "")
        if sample and sample not in seen:
            seen.add(sample)
            ordered.append(sample)
    return tuple(ordered)


def _split_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for chunk in value.replace(",", ";").replace(" ", ";").split(";")
        if (token := chunk.strip())
    )


def _risk_sort_key(classification: str) -> int:
    order = {
        "blocked_neighboring_ms1_interference": 0,
        "blocked_low_ms1_assessable_coverage": 1,
        "blocked_rescue_only": 2,
        "risky_extreme_backfill": 0,
        "risky_weak_seed_backfill": 1,
        "supported_backfill_capped": 2,
        "watch_duplicate_rescue": 3,
        "weak": 4,
        "strong": 5,
    }
    return order.get(classification, 99)


def _quality_value(
    quality: Mapping[str, Any] | None,
    key: str,
) -> Any:
    if quality is None:
        return ""
    value = quality.get(key)
    return "" if value is None else value


def _optional_metric(value: float | None) -> float | str:
    return value if value is not None else ""


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _int_value(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _is_true(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "t", "yes", "y"}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a single-dR production gate decision report.",
    )
    parser.add_argument("--alignment-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--discovery-batch-index", type=Path)
    parser.add_argument("--rt-normalization-families-tsv", type=Path)
    parser.add_argument("--targeted-istd-benchmark-json", type=Path)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
