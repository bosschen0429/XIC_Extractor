"""FULL COHORT slice gate for reviewed PeakHypothesis backfill promotions.

This diagnostic reads existing alignment artifacts only. It does not read RAW
files, mutate matrices, or remap primary-loser rows.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics import backfill_peakhypothesis_promotion
from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    optional_float,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_acceptance_gate_v1"

FULL_COHORT_ACCEPTANCE_GATE_COLUMNS = (
    "schema_version",
    "source_run_id",
    "peak_hypothesis_id",
    "feature_family_id",
    "seed_group_id",
    "sample_stem",
    "promotion_decision",
    "projected_matrix_value",
    "full_cohort_match_strategy",
    "full_cohort_anchor_mz",
    "full_cohort_anchor_rt",
    "full_cohort_matched_feature_family_id",
    "full_cohort_matched_peak_hypothesis_id",
    "full_cohort_match_mz_delta_ppm",
    "full_cohort_match_rt_delta_min",
    "full_cohort_candidate_count",
    "full_cohort_exact_cell_found",
    "full_cohort_cell_status",
    "full_cohort_primary_matrix_area",
    "full_cohort_primary_matrix_area_source",
    "full_cohort_primary_matrix_area_reason",
    "full_cohort_peak_start_rt",
    "full_cohort_peak_end_rt",
    "full_cohort_trace_quality",
    "full_cohort_review_identity_decision",
    "full_cohort_include_in_primary_matrix",
    "full_cohort_consolidation_state",
    "full_cohort_consolidation_winner_group_hypothesis_id",
    "full_cohort_row_flags",
    "full_cohort_acceptance_gate_status",
    "full_cohort_slice_blockers",
    "recommended_action",
)

_ACCEPTABLE_DIRECT_CELL_STATUSES = {"detected", "rescued"}
_GAUSSIAN15_AREA_SOURCE = "gaussian15_positive_asls_residual"
_DEFAULT_ANCHOR_MZ_TOLERANCE_PPM = 20.0
_DEFAULT_ANCHOR_RT_TOLERANCE_MIN = 0.75
_SEED_MZ_RE = re.compile(r"(?:^|::)mz=([0-9.+\-eE]+)")
_SEED_RT_RE = re.compile(r"(?:^|::)rt=([0-9.+\-eE]+)")


@dataclass(frozen=True)
class FullCohortAcceptanceGateIndex:
    rows: tuple[dict[str, str], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class FullCohortAcceptanceGateOutputs:
    cells_tsv: Path
    summary_json: Path


@dataclass(frozen=True)
class _CandidateMatch:
    review_row: dict[str, str] | None
    cell_row: dict[str, str] | None
    strategy: str
    anchor_mz: float | None
    anchor_rt: float | None
    mz_delta_ppm: float | None
    rt_delta_min: float | None
    candidate_count: int


def build_full_cohort_acceptance_gate(
    *,
    promotion_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    full_cohort_review_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    full_cohort_cell_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    source_run_id: str = "",
    anchor_mz_tolerance_ppm: float = _DEFAULT_ANCHOR_MZ_TOLERANCE_PPM,
    anchor_rt_tolerance_min: float = _DEFAULT_ANCHOR_RT_TOLERANCE_MIN,
) -> FullCohortAcceptanceGateIndex:
    _validate_promotion_schema(promotion_rows)
    review_by_family = _index_review_rows(full_cohort_review_rows)
    cells_by_key = _index_cell_rows(full_cohort_cell_rows)
    promoted_rows = tuple(
        row
        for row in promotion_rows
        if _value(row, "promotion_decision") == "promote_matrix_write"
    )
    rows = tuple(
        _build_gate_row(
            promotion_row=row,
            candidate_match=_select_candidate_match(
                promotion_row=row,
                full_cohort_review_rows=full_cohort_review_rows,
                review_by_family=review_by_family,
                cells_by_key=cells_by_key,
                anchor_mz_tolerance_ppm=anchor_mz_tolerance_ppm,
                anchor_rt_tolerance_min=anchor_rt_tolerance_min,
            ),
            source_run_id=source_run_id,
        )
        for row in promoted_rows
    )
    summary = _summary(rows, source_run_id=source_run_id)
    return FullCohortAcceptanceGateIndex(rows=rows, summary=summary)


def write_full_cohort_acceptance_gate_outputs(
    output_dir: Path,
    index: FullCohortAcceptanceGateIndex,
) -> FullCohortAcceptanceGateOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    cells_tsv = output_dir / "backfill_peakhypothesis_acceptance_gate.tsv"
    summary_json = output_dir / "backfill_peakhypothesis_acceptance_gate_summary.json"
    write_tsv(
        cells_tsv,
        index.rows,
        FULL_COHORT_ACCEPTANCE_GATE_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return FullCohortAcceptanceGateOutputs(
        cells_tsv=cells_tsv, summary_json=summary_json
    )


def _build_gate_row(
    *,
    promotion_row: dict[str, str],
    candidate_match: _CandidateMatch,
    source_run_id: str,
) -> dict[str, str]:
    blockers = _blockers(
        candidate_match=candidate_match,
    )
    gate_status = _gate_status(blockers, candidate_match)
    review = candidate_match.review_row or {}
    cell = candidate_match.cell_row or {}
    consolidation_state = _value(review, "consolidation_state") or _value(
        cell, "consolidation_state"
    )
    consolidation_winner = _value(
        review, "consolidation_winner_group_hypothesis_id"
    ) or _value(cell, "consolidation_winner_group_hypothesis_id")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "peak_hypothesis_id": _value(promotion_row, "peak_hypothesis_id"),
        "feature_family_id": _value(promotion_row, "feature_family_id"),
        "seed_group_id": _value(promotion_row, "seed_group_id"),
        "sample_stem": _value(promotion_row, "sample_stem"),
        "promotion_decision": _value(promotion_row, "promotion_decision"),
        "projected_matrix_value": _value(promotion_row, "projected_matrix_value"),
        "full_cohort_match_strategy": candidate_match.strategy,
        "full_cohort_anchor_mz": _float_text(candidate_match.anchor_mz),
        "full_cohort_anchor_rt": _float_text(candidate_match.anchor_rt),
        "full_cohort_matched_feature_family_id": _value(review, "feature_family_id"),
        "full_cohort_matched_peak_hypothesis_id": (
            _value(review, "group_hypothesis_id") or _value(review, "feature_family_id")
        ),
        "full_cohort_match_mz_delta_ppm": _float_text(candidate_match.mz_delta_ppm),
        "full_cohort_match_rt_delta_min": _float_text(candidate_match.rt_delta_min),
        "full_cohort_candidate_count": str(candidate_match.candidate_count),
        "full_cohort_exact_cell_found": (
            "TRUE" if candidate_match.cell_row is not None else "FALSE"
        ),
        "full_cohort_cell_status": _value(cell, "status"),
        "full_cohort_primary_matrix_area": _value(cell, "primary_matrix_area"),
        "full_cohort_primary_matrix_area_source": _value(
            cell,
            "primary_matrix_area_source",
        ),
        "full_cohort_primary_matrix_area_reason": _value(
            cell,
            "primary_matrix_area_reason",
        ),
        "full_cohort_peak_start_rt": _value(cell, "peak_start_rt"),
        "full_cohort_peak_end_rt": _value(cell, "peak_end_rt"),
        "full_cohort_trace_quality": _value(cell, "trace_quality"),
        "full_cohort_review_identity_decision": _value(review, "identity_decision"),
        "full_cohort_include_in_primary_matrix": _value(
            review,
            "include_in_primary_matrix",
        ),
        "full_cohort_consolidation_state": consolidation_state,
        "full_cohort_consolidation_winner_group_hypothesis_id": consolidation_winner,
        "full_cohort_row_flags": _value(review, "row_flags"),
        "full_cohort_acceptance_gate_status": gate_status,
        "full_cohort_slice_blockers": ";".join(blockers),
        "recommended_action": _recommended_action(blockers),
    }


def _blockers(
    *,
    candidate_match: _CandidateMatch,
) -> tuple[str, ...]:
    if candidate_match.review_row is None:
        return ("full_cohort_hypothesis_candidate_missing",)
    if candidate_match.cell_row is None:
        return ("full_cohort_hypothesis_candidate_cell_missing",)

    review = candidate_match.review_row
    cell = candidate_match.cell_row
    blockers: list[str] = []
    if _value(review, "include_in_primary_matrix") != "TRUE":
        blockers.append("full_cohort_candidate_not_primary_matrix_row")

    consolidation_state = _value(review, "consolidation_state") or _value(
        cell, "consolidation_state"
    )
    consolidation_winner = _value(
        review, "consolidation_winner_group_hypothesis_id"
    ) or _value(cell, "consolidation_winner_group_hypothesis_id")
    if consolidation_state == "primary_loser" or consolidation_winner:
        blockers.append("full_cohort_candidate_family_consolidation_review_required")

    status = _value(cell, "status")
    if status not in _ACCEPTABLE_DIRECT_CELL_STATUSES:
        blockers.append(f"full_cohort_cell_status_{status or 'missing'}")

    primary_area = optional_float(_value(cell, "primary_matrix_area"))
    if primary_area is None or primary_area <= 0:
        blockers.append("full_cohort_primary_matrix_area_missing")
    if _value(cell, "primary_matrix_area_source") != _GAUSSIAN15_AREA_SOURCE:
        blockers.append("full_cohort_primary_matrix_area_source_not_gaussian15")
    return tuple(blockers)


def _gate_status(
    blockers: tuple[str, ...],
    candidate_match: _CandidateMatch,
) -> str:
    if not blockers:
        return "candidate_no_regression"
    if _has_reviewable_hypothesis_candidate(blockers, candidate_match):
        return "hypothesis_candidate_review"
    return "blocked"


def _has_reviewable_hypothesis_candidate(
    blockers: tuple[str, ...],
    candidate_match: _CandidateMatch,
) -> bool:
    if candidate_match.review_row is None or candidate_match.cell_row is None:
        return False
    hard_blockers = {
        "full_cohort_hypothesis_candidate_missing",
        "full_cohort_hypothesis_candidate_cell_missing",
        "full_cohort_cell_status_absent",
        "full_cohort_cell_status_missing",
        "full_cohort_primary_matrix_area_missing",
        "full_cohort_primary_matrix_area_source_not_gaussian15",
    }
    if any(blocker in hard_blockers for blocker in blockers):
        return False
    status = _value(candidate_match.cell_row, "status")
    return status in _ACCEPTABLE_DIRECT_CELL_STATUSES


def _recommended_action(blockers: tuple[str, ...]) -> str:
    if not blockers:
        return "eligible_for_direct_full_cohort_activation_trial"
    if "full_cohort_candidate_family_consolidation_review_required" in blockers:
        return "review_hypothesis_anchor_candidate_before_activation"
    return "manual_full_cohort_review_required"


def _summary(
    rows: tuple[dict[str, str], ...],
    *,
    source_run_id: str,
) -> dict[str, Any]:
    status_counts = Counter(
        _value(row, "full_cohort_acceptance_gate_status") for row in rows
    )
    blocker_counts = Counter(
        blocker
        for row in rows
        for blocker in _semicolon_values(row.get("full_cohort_slice_blockers"))
    )
    if status_counts.get("blocked", 0):
        gate_status = "fail"
    elif status_counts.get("hypothesis_candidate_review", 0):
        gate_status = "partial"
    else:
        gate_status = "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "gate_status": gate_status,
        "promotion_row_count": len(rows),
        "candidate_no_regression_count": status_counts.get(
            "candidate_no_regression",
            0,
        ),
        "blocked_count": status_counts.get("blocked", 0),
        "hypothesis_candidate_review_count": status_counts.get(
            "hypothesis_candidate_review",
            0,
        ),
        "exact_cell_found_count": sum(
            1 for row in rows if _value(row, "full_cohort_exact_cell_found") == "TRUE"
        ),
        "exact_cell_missing_count": blocker_counts.get(
            "full_cohort_hypothesis_candidate_cell_missing",
            0,
        ),
        "not_primary_matrix_row_count": blocker_counts.get(
            "full_cohort_candidate_not_primary_matrix_row",
            0,
        ),
        "primary_loser_count": sum(
            1
            for row in rows
            if _value(row, "full_cohort_consolidation_state") == "primary_loser"
        ),
        "family_consolidation_review_count": blocker_counts.get(
            "full_cohort_candidate_family_consolidation_review_required",
            0,
        ),
        "duplicate_assigned_count": blocker_counts.get(
            "full_cohort_cell_status_duplicate_assigned",
            0,
        ),
        "absent_count": blocker_counts.get("full_cohort_cell_status_absent", 0),
        "missing_gaussian_area_count": sum(
            count
            for blocker, count in blocker_counts.items()
            if blocker
            in {
                "full_cohort_primary_matrix_area_missing",
                "full_cohort_primary_matrix_area_source_not_gaussian15",
            }
        ),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": (
            "ready_for_direct_full_cohort_activation_trial"
            if gate_status == "pass"
            else "review_full_cohort_hypothesis_candidates_before_product_transfer"
        ),
        "blocker_counts": dict(sorted(blocker_counts.items())),
    }


def _select_candidate_match(
    *,
    promotion_row: dict[str, str],
    full_cohort_review_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    review_by_family: dict[str, dict[str, str]],
    cells_by_key: dict[tuple[str, str], dict[str, str]],
    anchor_mz_tolerance_ppm: float,
    anchor_rt_tolerance_min: float,
) -> _CandidateMatch:
    sample = _value(promotion_row, "sample_stem")
    anchor = _seed_anchor(promotion_row)
    if anchor is None:
        family_id = _value(promotion_row, "feature_family_id")
        review = review_by_family.get(family_id)
        return _CandidateMatch(
            review_row=review,
            cell_row=cells_by_key.get((family_id, sample)),
            strategy="feature_family_exact_fallback",
            anchor_mz=None,
            anchor_rt=None,
            mz_delta_ppm=None,
            rt_delta_min=None,
            candidate_count=1 if review is not None else 0,
        )

    anchor_mz, anchor_rt = anchor
    candidates: list[tuple[tuple[float, float, float], dict[str, str]]] = []
    for row in full_cohort_review_rows:
        family_mz = optional_float(_value(row, "family_center_mz"))
        family_rt = optional_float(_value(row, "family_center_rt"))
        if family_mz is None or family_rt is None:
            continue
        mz_delta_ppm = abs(family_mz - anchor_mz) / anchor_mz * 1_000_000
        rt_delta_min = abs(family_rt - anchor_rt)
        if (
            mz_delta_ppm <= anchor_mz_tolerance_ppm
            and rt_delta_min <= anchor_rt_tolerance_min
        ):
            cell = cells_by_key.get((_value(row, "feature_family_id"), sample))
            cell_score = _cell_match_score(cell, anchor_rt)
            candidates.append(((cell_score, rt_delta_min, mz_delta_ppm), row))

    if not candidates:
        return _CandidateMatch(
            review_row=None,
            cell_row=None,
            strategy="hypothesis_anchor_mz_rt_sample",
            anchor_mz=anchor_mz,
            anchor_rt=anchor_rt,
            mz_delta_ppm=None,
            rt_delta_min=None,
            candidate_count=0,
        )

    sort_key, review = min(candidates, key=lambda item: item[0])
    family_id = _value(review, "feature_family_id")
    return _CandidateMatch(
        review_row=review,
        cell_row=cells_by_key.get((family_id, sample)),
        strategy="hypothesis_anchor_mz_rt_sample",
        anchor_mz=anchor_mz,
        anchor_rt=anchor_rt,
        mz_delta_ppm=sort_key[2],
        rt_delta_min=sort_key[1],
        candidate_count=len(candidates),
    )


def _cell_match_score(cell: dict[str, str] | None, anchor_rt: float) -> float:
    if cell is None:
        return 1_000_000.0
    status = _value(cell, "status")
    status_penalty = 0.0 if status in _ACCEPTABLE_DIRECT_CELL_STATUSES else 1000.0
    apex_rt = optional_float(_value(cell, "apex_rt"))
    if apex_rt is None:
        apex_rt = optional_float(_value(cell, "peak_start_rt"))
    apex_delta = abs(apex_rt - anchor_rt) if apex_rt is not None else 100.0
    area = optional_float(_value(cell, "primary_matrix_area"))
    area_penalty = 0.0 if area is not None and area > 0 else 100.0
    return status_penalty + area_penalty + apex_delta


def _seed_anchor(row: dict[str, str]) -> tuple[float, float] | None:
    seed_group_id = _value(row, "seed_group_id")
    mz_match = _SEED_MZ_RE.search(seed_group_id)
    rt_match = _SEED_RT_RE.search(seed_group_id)
    if mz_match is None or rt_match is None:
        return None
    try:
        mz = float(mz_match.group(1))
        rt = float(rt_match.group(1))
    except ValueError:
        return None
    if not (math.isfinite(mz) and math.isfinite(rt) and mz > 0):
        return None
    return (mz, rt)


def _float_text(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.6g}"


def _validate_promotion_schema(
    rows: list[dict[str, str]] | tuple[dict[str, str], ...],
) -> None:
    for index, row in enumerate(rows, start=1):
        actual = _value(row, "schema_version")
        if actual != backfill_peakhypothesis_promotion.SCHEMA_VERSION:
            raise ValueError(
                "promotion row schema_version mismatch at row "
                f"{index}: expected "
                f"{backfill_peakhypothesis_promotion.SCHEMA_VERSION!r}, "
                f"got {actual!r}",
            )


def _index_review_rows(
    rows: list[dict[str, str]] | tuple[dict[str, str], ...],
) -> dict[str, dict[str, str]]:
    by_family: dict[str, dict[str, str]] = {}
    for row in rows:
        family_id = _value(row, "feature_family_id")
        if not family_id:
            continue
        if family_id in by_family:
            raise ValueError(f"duplicate full_cohort review row: {family_id}")
        by_family[family_id] = row
    return by_family


def _index_cell_rows(
    rows: list[dict[str, str]] | tuple[dict[str, str], ...],
) -> dict[tuple[str, str], dict[str, str]]:
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        family_id = _value(row, "feature_family_id")
        sample = _value(row, "sample_stem")
        if not family_id or not sample:
            continue
        key = (family_id, sample)
        if key in by_key:
            raise ValueError(f"duplicate full_cohort cell row: {family_id}|{sample}")
        by_key[key] = row
    return by_key


def _semicolon_values(value: object) -> tuple[str, ...]:
    return tuple(part for part in text_value(value).split(";") if part)


def _value(row: dict[str, str], column: str) -> str:
    return text_value(row.get(column, ""))
