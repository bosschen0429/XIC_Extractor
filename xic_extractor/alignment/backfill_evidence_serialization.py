"""Canonical string projections for owner-readable Backfill evidence records."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Literal, cast

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceRecord,
    SkippedEvidenceRecord,
)
from xic_extractor.alignment.matrix import CellStatus
from xic_extractor.alignment.output_rows import escape_excel_formula
from xic_extractor.alignment.production_decisions import (
    BackfillTier,
    ProductionStatus,
)

_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")


def backfill_evidence_identity_projection_spellings(value: str) -> tuple[str, ...]:
    """Return every current projection spelling for one canonical machine ID."""

    escaped = escape_excel_formula(value)
    return (value,) if escaped == value else (value, escaped)


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
        feature_family_id=_required_text(row, "feature_family_id"),
        group_hypothesis_id=_optional_text(row, "group_hypothesis_id"),
        public_family_id=_optional_text(row, "public_family_id"),
        sample_stem=_required_text(row, "sample_stem"),
        status=cast(CellStatus, _required_text(row, "status")),
        production_cell_status=cast(
            ProductionStatus,
            _required_text(row, "production_cell_status"),
        ),
        backfill_tier=cast(BackfillTier, row["backfill_tier"]),
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
        backfill_evidence_reason=row["backfill_evidence_reason"],
        source_candidate_id=row["source_candidate_id"],
        source_raw_file=row["source_raw_file"],
        neutral_loss_tag=_required_text(row, "neutral_loss_tag"),
        family_center_mz=_parse_required_float(row, "family_center_mz"),
        family_center_rt=_parse_required_float(row, "family_center_rt"),
        region_shadow_verdict=row["region_shadow_verdict"],
        reason=row["reason"],
    )


def skipped_evidence_record_from_projection(
    row: Mapping[str, str],
) -> SkippedEvidenceRecord:
    _require_exact_fields(row, SKIPPED_EVIDENCE_LEDGER_COLUMNS)
    return SkippedEvidenceRecord(
        feature_family_id=_required_text(row, "feature_family_id"),
        sample_stem=_required_text(row, "sample_stem"),
        family_center_mz=_parse_required_float(row, "family_center_mz"),
        family_center_rt=_parse_required_float(row, "family_center_rt"),
        rt_window_start=_parse_required_float(row, "rt_window_start"),
        rt_window_end=_parse_required_float(row, "rt_window_end"),
        pre_backfill_category=_required_text(row, "pre_backfill_category"),
        skipped_stage=_required_text(row, "skipped_stage"),
        skip_reason=_required_text(row, "skip_reason"),
        backfill_scope=_required_text(row, "backfill_scope"),
        predicate_version=_required_text(row, "predicate_version"),
        raw_xic_requests_skipped=_parse_positive_int(
            row,
            "raw_xic_requests_skipped",
        ),
        would_emit_in_full_audit=_parse_bool(
            row,
            "would_emit_in_full_audit",
            style="title",
        ),
        full_audit_available=_parse_bool(
            row,
            "full_audit_available",
            style="title",
        ),
        source_artifact=_required_text(row, "source_artifact"),
    )


def backfill_evidence_record_to_projection(
    record: BackfillEvidenceRecord,
) -> dict[str, str]:
    return {
        "schema_version": record.schema_version,
        "feature_family_id": record.feature_family_id,
        "group_hypothesis_id": record.group_hypothesis_id,
        "public_family_id": record.public_family_id,
        "sample_stem": record.sample_stem,
        "status": record.status,
        "production_cell_status": record.production_cell_status,
        "backfill_tier": record.backfill_tier,
        "write_matrix_value": _bool_text(record.write_matrix_value, style="upper"),
        "include_in_primary_matrix": _bool_text(
            record.include_in_primary_matrix,
            style="upper",
        ),
        "identity_decision": record.identity_decision,
        "row_flags": ";".join(record.row_flags),
        "area": _float_text(record.area),
        "primary_matrix_area": _float_text(record.primary_matrix_area),
        "primary_matrix_area_source": record.primary_matrix_area_source,
        "primary_matrix_area_reason": record.primary_matrix_area_reason,
        "apex_rt": _float_text(record.apex_rt),
        "height": _float_text(record.height),
        "peak_start_rt": _float_text(record.peak_start_rt),
        "peak_end_rt": _float_text(record.peak_end_rt),
        "rt_delta_sec": _float_text(record.rt_delta_sec),
        "trace_quality": record.trace_quality,
        "scan_support_score": _float_text(record.scan_support_score),
        "gap_fill_state": record.gap_fill_state,
        "gap_fill_reason": record.gap_fill_reason,
        "backfill_evidence_reason": record.backfill_evidence_reason,
        "source_candidate_id": record.source_candidate_id,
        "source_raw_file": record.source_raw_file,
        "neutral_loss_tag": record.neutral_loss_tag,
        "family_center_mz": _float_text(record.family_center_mz),
        "family_center_rt": _float_text(record.family_center_rt),
        "region_shadow_verdict": record.region_shadow_verdict,
        "reason": record.reason,
    }


def skipped_evidence_record_to_projection(
    record: SkippedEvidenceRecord,
) -> dict[str, str]:
    return {
        "feature_family_id": record.feature_family_id,
        "sample_stem": record.sample_stem,
        "family_center_mz": _float_text(record.family_center_mz),
        "family_center_rt": _float_text(record.family_center_rt),
        "rt_window_start": _float_text(record.rt_window_start),
        "rt_window_end": _float_text(record.rt_window_end),
        "pre_backfill_category": record.pre_backfill_category,
        "skipped_stage": record.skipped_stage,
        "skip_reason": record.skip_reason,
        "backfill_scope": record.backfill_scope,
        "predicate_version": record.predicate_version,
        "raw_xic_requests_skipped": str(record.raw_xic_requests_skipped),
        "would_emit_in_full_audit": _bool_text(
            record.would_emit_in_full_audit,
            style="title",
        ),
        "full_audit_available": _bool_text(
            record.full_audit_available,
            style="title",
        ),
        "source_artifact": record.source_artifact,
    }


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


def _parse_positive_int(row: Mapping[str, str], field_name: str) -> int:
    raw_value = row[field_name].strip()
    if _NONNEGATIVE_INTEGER.fullmatch(raw_value) is None:
        raise ValueError(f"{field_name} must be a positive integer")
    value = int(raw_value)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _parse_bool(
    row: Mapping[str, str],
    field_name: str,
    *,
    style: Literal["upper", "title"],
) -> bool:
    allowed = {"TRUE": True, "FALSE": False} if style == "upper" else {
        "True": True,
        "False": False,
    }
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


def _bool_text(value: bool, *, style: Literal["upper", "title"]) -> str:
    if style == "upper":
        return "TRUE" if value else "FALSE"
    return "True" if value else "False"


def _float_text(value: float | None) -> str:
    return "" if value is None else format(value, ".17g")


__all__ = (
    "backfill_evidence_identity_candidates",
    "backfill_evidence_identity_projection_spellings",
    "backfill_evidence_record_from_projection",
    "backfill_evidence_record_to_projection",
    "skipped_evidence_record_from_projection",
    "skipped_evidence_record_to_projection",
)
