"""Canonical string projections for owner-readable Backfill evidence records."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal, cast

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    BackfillEvidenceRecord,
)
from xic_extractor.alignment.matrix import CellStatus
from xic_extractor.alignment.production_decisions import ProductionStatus

_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")


def backfill_evidence_identity_candidates(value: str) -> tuple[str, ...]:
    """Return canonical candidates for a possibly formula-escaped projection ID."""

    if len(value) >= 2 and value[0] == "'" and value[1] in "=+-@":
        return (value, value[1:])
    return (value,)


def backfill_evidence_record_from_projection(
    row: Mapping[str, str],
) -> BackfillEvidenceRecord:
    _require_exact_fields(row, ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS)
    return BackfillEvidenceRecord(
        schema_version=_required_text(row, "schema_version"),
        feature_id=_required_text(row, "feature_id"),
        sample_stem=_required_text(row, "sample_stem"),
        status=cast(CellStatus, _required_text(row, "status")),
        production_cell_status=cast(
            ProductionStatus,
            _required_text(row, "production_cell_status"),
        ),
        write_matrix_value=_parse_bool(row, "write_matrix_value", style="upper"),
        include_in_primary_matrix=_parse_bool(
            row,
            "include_in_primary_matrix",
            style="upper",
        ),
        identity_decision=_required_text(row, "identity_decision"),
        row_flags=_parse_row_flags(row),
        area=_parse_optional_float(row, "area"),
        primary_matrix_area=_parse_optional_float(row, "primary_matrix_area"),
        primary_matrix_area_source=row["primary_matrix_area_source"],
        primary_matrix_area_reason=row["primary_matrix_area_reason"],
        apex_rt=_parse_optional_float(row, "apex_rt"),
        height=_parse_optional_float(row, "height"),
        peak_start_rt=_parse_optional_float(row, "peak_start_rt"),
        peak_end_rt=_parse_optional_float(row, "peak_end_rt"),
        rt_delta_sec=_parse_optional_float(row, "rt_delta_sec"),
        trace_quality=row["trace_quality"],
        scan_support_score=_parse_optional_float(row, "scan_support_score"),
        gap_fill_state=row["gap_fill_state"],
        gap_fill_reason=row["gap_fill_reason"],
        source_candidate_id=row["source_candidate_id"],
        neutral_loss_tag=_required_text(row, "neutral_loss_tag"),
        family_center_mz=_parse_required_float(row, "family_center_mz"),
        family_center_rt=_parse_required_float(row, "family_center_rt"),
        reason=row["reason"],
        backfill_query_rt_min=_parse_optional_float(row, "backfill_query_rt_min"),
        backfill_query_rt_max=_parse_optional_float(row, "backfill_query_rt_max"),
    )


def _require_exact_fields(row: Mapping[str, str], expected: tuple[str, ...]) -> None:
    if tuple(row) != expected:
        raise ValueError(
            f"expected exact projection fields {expected!r}, got {tuple(row)!r}"
        )


def _required_text(row: Mapping[str, str], field_name: str) -> str:
    value = _optional_text(row, field_name)
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def _optional_text(row: Mapping[str, str], field_name: str) -> str:
    return row[field_name].strip()


def _parse_required_float(row: Mapping[str, str], field_name: str) -> float:
    value = _parse_optional_float(row, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    return value


def _parse_optional_float(
    row: Mapping[str, str],
    field_name: str,
) -> float | None:
    raw_value = row[field_name].strip()
    if not raw_value:
        return None
    if raw_value.startswith("'-"):
        raw_value = raw_value[1:]
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")
    return value


def _parse_bool(
    row: Mapping[str, str],
    field_name: str,
    *,
    style: Literal["upper", "title"],
) -> bool:
    allowed = (
        {"TRUE": True, "FALSE": False}
        if style == "upper"
        else {
            "True": True,
            "False": False,
        }
    )
    raw_value = row[field_name]
    if raw_value not in allowed:
        raise ValueError(
            f"{field_name} must be one of {tuple(allowed)!r}, got {raw_value!r}"
        )
    return allowed[raw_value]


def _parse_row_flags(row: Mapping[str, str]) -> tuple[str, ...]:
    raw_value = row["row_flags"]
    if not raw_value:
        return ()
    flags = tuple(raw_value.split(";"))
    if any(not flag for flag in flags):
        raise ValueError("row_flags must not contain empty members")
    if len(flags) != len(set(flags)):
        raise ValueError("row_flags must not contain duplicates")
    return flags


def _float_text(value: float | None) -> str:
    return "" if value is None else format(value, ".17g")


__all__ = (
    "backfill_evidence_identity_candidates",
    "backfill_evidence_record_from_projection",
)
