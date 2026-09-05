"""Review queue for anchored FULL COHORT PeakHypothesis backfill candidates.

This diagnostic packages existing slice-gate rows for human review. It does not
read RAW files, choose S/N, apply activation, or mutate matrix artifacts.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.diagnostics import backfill_peakhypothesis_acceptance_gate
from xic_extractor.diagnostics.diagnostic_io import (
    format_diagnostic_value,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_hypothesis_review_v1"

REVIEW_QUEUE_COLUMNS = (
    "schema_version",
    "source_run_id",
    "review_item_id",
    "source_peak_hypothesis_id",
    "source_feature_family_id",
    "source_seed_group_id",
    "sample_stem",
    "full_cohort_match_strategy",
    "full_cohort_anchor_mz",
    "full_cohort_anchor_rt",
    "full_cohort_matched_peak_hypothesis_id",
    "full_cohort_matched_feature_family_id",
    "full_cohort_match_mz_delta_ppm",
    "full_cohort_match_rt_delta_min",
    "full_cohort_candidate_count",
    "full_cohort_cell_status",
    "full_cohort_primary_matrix_area",
    "full_cohort_primary_matrix_area_source",
    "full_cohort_peak_start_rt",
    "full_cohort_peak_end_rt",
    "full_cohort_trace_quality",
    "full_cohort_include_in_primary_matrix",
    "full_cohort_consolidation_state",
    "full_cohort_consolidation_winner_group_hypothesis_id",
    "full_cohort_slice_blockers",
    "review_focus",
    "review_question",
    "proposed_product_transfer_status",
    "recommended_action",
    "reviewer_verdict",
    "reviewer_note",
)

_HYPOTHESIS_CANDIDATE_REVIEW = "hypothesis_candidate_review"
_CANDIDATE_NO_REGRESSION = "candidate_no_regression"
_FAMILY_CONSOLIDATION_BLOCKER = (
    "full_cohort_candidate_family_consolidation_review_required"
)
_NON_PRIMARY_BLOCKER = "full_cohort_candidate_not_primary_matrix_row"


@dataclass(frozen=True)
class FullCohortHypothesisReviewIndex:
    rows: tuple[dict[str, str], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class FullCohortHypothesisReviewOutputs:
    review_queue_tsv: Path
    summary_json: Path


def build_full_cohort_hypothesis_review_queue(
    *,
    full_cohort_acceptance_gate_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    source_run_id: str = "",
) -> FullCohortHypothesisReviewIndex:
    _validate_slice_gate_schema(full_cohort_acceptance_gate_rows)
    queue_source_rows = tuple(
        row
        for row in full_cohort_acceptance_gate_rows
        if _value(row, "full_cohort_acceptance_gate_status")
        == _HYPOTHESIS_CANDIDATE_REVIEW
    )
    rows = tuple(
        _build_review_row(row, index=index, source_run_id=source_run_id)
        for index, row in enumerate(queue_source_rows, start=1)
    )
    return FullCohortHypothesisReviewIndex(
        rows=rows,
        summary=_summary(
            rows,
            full_cohort_acceptance_gate_rows=full_cohort_acceptance_gate_rows,
            source_run_id=source_run_id,
        ),
    )


def write_full_cohort_hypothesis_review_outputs(
    output_dir: Path,
    index: FullCohortHypothesisReviewIndex,
) -> FullCohortHypothesisReviewOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    review_queue_tsv = (
        output_dir / "backfill_peakhypothesis_hypothesis_review_queue.tsv"
    )
    summary_json = output_dir / "backfill_peakhypothesis_hypothesis_review_summary.json"
    write_tsv(
        review_queue_tsv,
        index.rows,
        REVIEW_QUEUE_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return FullCohortHypothesisReviewOutputs(
        review_queue_tsv=review_queue_tsv,
        summary_json=summary_json,
    )


def _build_review_row(
    row: dict[str, str],
    *,
    index: int,
    source_run_id: str,
) -> dict[str, str]:
    review_focus = _review_focus(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "review_item_id": f"HYPREV{index:04d}",
        "source_peak_hypothesis_id": _value(row, "peak_hypothesis_id"),
        "source_feature_family_id": _value(row, "feature_family_id"),
        "source_seed_group_id": _value(row, "seed_group_id"),
        "sample_stem": _value(row, "sample_stem"),
        "full_cohort_match_strategy": _value(row, "full_cohort_match_strategy"),
        "full_cohort_anchor_mz": _value(row, "full_cohort_anchor_mz"),
        "full_cohort_anchor_rt": _value(row, "full_cohort_anchor_rt"),
        "full_cohort_matched_peak_hypothesis_id": _value(
            row,
            "full_cohort_matched_peak_hypothesis_id",
        ),
        "full_cohort_matched_feature_family_id": _value(
            row,
            "full_cohort_matched_feature_family_id",
        ),
        "full_cohort_match_mz_delta_ppm": _value(row, "full_cohort_match_mz_delta_ppm"),
        "full_cohort_match_rt_delta_min": _value(row, "full_cohort_match_rt_delta_min"),
        "full_cohort_candidate_count": _value(row, "full_cohort_candidate_count"),
        "full_cohort_cell_status": _value(row, "full_cohort_cell_status"),
        "full_cohort_primary_matrix_area": _value(
            row, "full_cohort_primary_matrix_area"
        ),
        "full_cohort_primary_matrix_area_source": _value(
            row,
            "full_cohort_primary_matrix_area_source",
        ),
        "full_cohort_peak_start_rt": _value(row, "full_cohort_peak_start_rt"),
        "full_cohort_peak_end_rt": _value(row, "full_cohort_peak_end_rt"),
        "full_cohort_trace_quality": _value(row, "full_cohort_trace_quality"),
        "full_cohort_include_in_primary_matrix": _value(
            row,
            "full_cohort_include_in_primary_matrix",
        ),
        "full_cohort_consolidation_state": _value(
            row, "full_cohort_consolidation_state"
        ),
        "full_cohort_consolidation_winner_group_hypothesis_id": _value(
            row,
            "full_cohort_consolidation_winner_group_hypothesis_id",
        ),
        "full_cohort_slice_blockers": _value(row, "full_cohort_slice_blockers"),
        "review_focus": review_focus,
        "review_question": _review_question(review_focus),
        "proposed_product_transfer_status": (
            "review_only_pending_same_peak_and_consolidation_policy"
        ),
        "recommended_action": "manual_same_peak_review_before_product_transfer",
        "reviewer_verdict": "",
        "reviewer_note": "",
    }


def _summary(
    rows: tuple[dict[str, str], ...],
    *,
    full_cohort_acceptance_gate_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    source_run_id: str,
) -> dict[str, Any]:
    input_status_counts = Counter(
        _value(row, "full_cohort_acceptance_gate_status")
        for row in full_cohort_acceptance_gate_rows
    )
    focus_counts = Counter(_value(row, "review_focus") for row in rows)
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "review_queue_status": ("manual_review_required" if rows else "empty"),
        "input_row_count": len(full_cohort_acceptance_gate_rows),
        "candidate_queue_count": len(rows),
        "direct_candidate_count": input_status_counts.get(_CANDIDATE_NO_REGRESSION, 0),
        "blocked_input_count": input_status_counts.get("blocked", 0),
        "non_primary_candidate_count": focus_counts.get(
            "non_primary_candidate_needs_consolidation_policy",
            0,
        ),
        "primary_row_consolidation_context_count": focus_counts.get(
            "primary_candidate_with_family_consolidation_context",
            0,
        ),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": (
            "manual_review_full_cohort_hypothesis_candidates"
            if rows
            else "no_full_cohort_hypothesis_candidate_review_rows"
        ),
        "review_focus_counts": dict(sorted(focus_counts.items())),
        "input_status_counts": dict(sorted(input_status_counts.items())),
    }


def _review_focus(row: dict[str, str]) -> str:
    blockers = set(_semicolon_values(_value(row, "full_cohort_slice_blockers")))
    if _NON_PRIMARY_BLOCKER in blockers:
        return "non_primary_candidate_needs_consolidation_policy"
    if _FAMILY_CONSOLIDATION_BLOCKER in blockers:
        return "primary_candidate_with_family_consolidation_context"
    return "hypothesis_candidate_manual_review"


def _review_question(review_focus: str) -> str:
    if review_focus == "non_primary_candidate_needs_consolidation_policy":
        return "same_peak_evidence_strong_enough_despite_non_primary_family_row"
    if review_focus == "primary_candidate_with_family_consolidation_context":
        return "same_peak_evidence_strong_enough_with_primary_family_context"
    return "same_peak_candidate_requires_manual_review"


def _validate_slice_gate_schema(
    rows: list[dict[str, str]] | tuple[dict[str, str], ...],
) -> None:
    for index, row in enumerate(rows, start=1):
        actual = _value(row, "schema_version")
        if actual != backfill_peakhypothesis_acceptance_gate.SCHEMA_VERSION:
            raise ValueError(
                "full_cohort slice gate schema_version mismatch at row "
                f"{index}: expected "
                f"{backfill_peakhypothesis_acceptance_gate.SCHEMA_VERSION!r}, "
                f"got {actual!r}",
            )


def _semicolon_values(value: object) -> tuple[str, ...]:
    return tuple(part for part in text_value(value).split(";") if part)


def _value(row: dict[str, str], column: str) -> str:
    return text_value(row.get(column, ""))
