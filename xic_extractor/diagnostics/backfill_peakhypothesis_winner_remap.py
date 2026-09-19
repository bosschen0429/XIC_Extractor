"""FULL COHORT family-consolidation context for backfill PeakHypothesis cells.

This diagnostic maps source PeakHypothesis/sample cells that became FULL COHORT
primary losers to their current primary winner rows. It writes proposal evidence
only and does not mutate matrices, apply activation, or establish hypothesis
identity authority.
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
    optional_float,
    text_value,
    write_tsv,
)

SCHEMA_VERSION = "backfill_peakhypothesis_winner_remap"

WINNER_REMAP_COLUMNS = (
    "schema_version",
    "source_run_id",
    "source_peak_hypothesis_id",
    "source_feature_family_id",
    "source_seed_group_id",
    "sample_stem",
    "source_projected_matrix_value",
    "source_full_cohort_cell_status",
    "source_full_cohort_slice_blockers",
    "winner_peak_hypothesis_id",
    "winner_feature_family_id",
    "winner_review_found",
    "winner_exact_cell_found",
    "winner_review_identity_decision",
    "winner_include_in_primary_matrix",
    "winner_consolidation_state",
    "winner_row_flags",
    "winner_cell_status",
    "winner_primary_matrix_area",
    "winner_primary_matrix_area_source",
    "winner_primary_matrix_area_reason",
    "winner_peak_start_rt",
    "winner_peak_end_rt",
    "winner_trace_quality",
    "matrix_value_source",
    "remap_status",
    "remap_blockers",
    "recommended_action",
)

_ACCEPTABLE_WINNER_CELL_STATUSES = {"detected", "rescued"}
_GAUSSIAN15_AREA_SOURCE = "gaussian15_positive_asls_residual"


@dataclass(frozen=True)
class FullCohortWinnerRemapIndex:
    rows: tuple[dict[str, str], ...]
    summary: dict[str, Any]


@dataclass(frozen=True)
class FullCohortWinnerRemapOutputs:
    rows_tsv: Path
    summary_json: Path


def build_full_cohort_winner_remap(
    *,
    full_cohort_acceptance_gate_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    full_cohort_review_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    full_cohort_cell_rows: list[dict[str, str]] | tuple[dict[str, str], ...],
    source_run_id: str = "",
) -> FullCohortWinnerRemapIndex:
    _validate_slice_gate_schema(full_cohort_acceptance_gate_rows)
    review_by_family = _index_review_rows(full_cohort_review_rows)
    cells_by_key = _index_cell_rows(full_cohort_cell_rows)
    rows = tuple(
        _build_remap_row(
            source_row=row,
            winner_review_row=review_by_family.get(_winner_id(row)),
            winner_cell_row=cells_by_key.get(
                (_winner_id(row), _value(row, "sample_stem")),
            ),
            source_run_id=source_run_id,
        )
        for row in full_cohort_acceptance_gate_rows
    )
    return FullCohortWinnerRemapIndex(
        rows=rows,
        summary=_summary(rows, source_run_id=source_run_id),
    )


def write_full_cohort_winner_remap_outputs(
    output_dir: Path,
    index: FullCohortWinnerRemapIndex,
) -> FullCohortWinnerRemapOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_tsv = output_dir / "backfill_peakhypothesis_winner_remap.tsv"
    summary_json = (
        output_dir / "backfill_peakhypothesis_winner_remap_summary.json"
    )
    write_tsv(
        rows_tsv,
        index.rows,
        WINNER_REMAP_COLUMNS,
        formatter=format_diagnostic_value,
        lineterminator="\n",
    )
    summary_json.write_text(
        json.dumps(index.summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return FullCohortWinnerRemapOutputs(rows_tsv=rows_tsv, summary_json=summary_json)


def _build_remap_row(
    *,
    source_row: dict[str, str],
    winner_review_row: dict[str, str] | None,
    winner_cell_row: dict[str, str] | None,
    source_run_id: str,
) -> dict[str, str]:
    winner_id = _winner_id(source_row)
    blockers = _blockers(
        winner_id=winner_id,
        winner_review_row=winner_review_row,
        winner_cell_row=winner_cell_row,
    )
    status = "blocked" if blockers else "remap_candidate_review"
    review = winner_review_row or {}
    cell = winner_cell_row or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "source_peak_hypothesis_id": _value(source_row, "peak_hypothesis_id"),
        "source_feature_family_id": _value(source_row, "feature_family_id"),
        "source_seed_group_id": _value(source_row, "seed_group_id"),
        "sample_stem": _value(source_row, "sample_stem"),
        "source_projected_matrix_value": _value(
            source_row,
            "projected_matrix_value",
        ),
        "source_full_cohort_cell_status": _value(source_row, "full_cohort_cell_status"),
        "source_full_cohort_slice_blockers": _value(
            source_row,
            "full_cohort_slice_blockers",
        ),
        "winner_peak_hypothesis_id": winner_id,
        "winner_feature_family_id": winner_id,
        "winner_review_found": "TRUE" if winner_review_row is not None else "FALSE",
        "winner_exact_cell_found": "TRUE" if winner_cell_row is not None else "FALSE",
        "winner_review_identity_decision": _value(review, "identity_decision"),
        "winner_include_in_primary_matrix": _value(
            review,
            "include_in_primary_matrix",
        ),
        "winner_consolidation_state": _value(review, "consolidation_state"),
        "winner_row_flags": _value(review, "row_flags"),
        "winner_cell_status": _value(cell, "status"),
        "winner_primary_matrix_area": _value(cell, "primary_matrix_area"),
        "winner_primary_matrix_area_source": _value(
            cell,
            "primary_matrix_area_source",
        ),
        "winner_primary_matrix_area_reason": _value(
            cell,
            "primary_matrix_area_reason",
        ),
        "winner_peak_start_rt": _value(cell, "peak_start_rt"),
        "winner_peak_end_rt": _value(cell, "peak_end_rt"),
        "winner_trace_quality": _value(cell, "trace_quality"),
        "matrix_value_source": (
            "full_cohort_winner_primary_matrix_area" if not blockers else ""
        ),
        "remap_status": status,
        "remap_blockers": ";".join(blockers),
        "recommended_action": _recommended_action(blockers),
    }


def _blockers(
    *,
    winner_id: str,
    winner_review_row: dict[str, str] | None,
    winner_cell_row: dict[str, str] | None,
) -> tuple[str, ...]:
    if not winner_id:
        return ("missing_winner_peak_hypothesis_id",)

    blockers: list[str] = []
    review = winner_review_row or {}
    cell = winner_cell_row or {}
    if winner_review_row is None:
        blockers.append("winner_review_row_missing")
    if _value(review, "include_in_primary_matrix") != "TRUE":
        blockers.append("winner_not_primary_matrix_row")
    if _value(review, "consolidation_state") == "primary_loser":
        blockers.append("winner_is_primary_loser")

    if winner_cell_row is None:
        blockers.append("winner_exact_cell_missing")
        return tuple(blockers)

    status = _value(cell, "status")
    if status not in _ACCEPTABLE_WINNER_CELL_STATUSES:
        blockers.append(f"winner_cell_status_{status or 'missing'}")
    primary_area = optional_float(_value(cell, "primary_matrix_area"))
    if primary_area is None or primary_area <= 0:
        blockers.append("winner_primary_matrix_area_missing")
    if _value(cell, "primary_matrix_area_source") != _GAUSSIAN15_AREA_SOURCE:
        blockers.append("winner_primary_matrix_area_source_not_gaussian15")
    return tuple(blockers)


def _recommended_action(blockers: tuple[str, ...]) -> str:
    if not blockers:
        return "review_remapped_winner_peak_shape_before_activation"
    if "missing_winner_peak_hypothesis_id" in blockers:
        return "manual_full_cohort_review_required"
    return "review_winner_remap_blockers"


def _summary(
    rows: tuple[dict[str, str], ...],
    *,
    source_run_id: str,
) -> dict[str, Any]:
    status_counts = Counter(_value(row, "remap_status") for row in rows)
    blocker_counts = Counter(
        blocker
        for row in rows
        for blocker in _semicolon_values(row.get("remap_blockers"))
    )
    candidate_count = status_counts.get("remap_candidate_review", 0)
    blocked_count = status_counts.get("blocked", 0)
    if candidate_count and blocked_count:
        remap_gate_status = "partial"
    elif candidate_count:
        remap_gate_status = "pass"
    else:
        remap_gate_status = "fail"
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_id": source_run_id,
        "remap_gate_status": remap_gate_status,
        "row_count": len(rows),
        "remap_candidate_review_count": candidate_count,
        "blocked_count": blocked_count,
        "winner_detected_count": sum(
            1 for row in rows if _value(row, "winner_cell_status") == "detected"
        ),
        "winner_rescued_count": sum(
            1 for row in rows if _value(row, "winner_cell_status") == "rescued"
        ),
        "missing_winner_count": blocker_counts.get(
            "missing_winner_peak_hypothesis_id",
            0,
        ),
        "matrix_contract_changed": False,
        "product_behavior_changed": False,
        "next_action": (
            "review_remapped_winner_peak_shapes"
            if candidate_count
            else "manual_full_cohort_review_required"
        ),
        "blocker_counts": dict(sorted(blocker_counts.items())),
    }


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


def _winner_id(row: dict[str, str]) -> str:
    return _value(row, "full_cohort_consolidation_winner_group_hypothesis_id")


def _semicolon_values(value: object) -> tuple[str, ...]:
    return tuple(part for part in text_value(value).split(";") if part)


def _value(row: dict[str, str], column: str) -> str:
    return text_value(row.get(column, ""))
