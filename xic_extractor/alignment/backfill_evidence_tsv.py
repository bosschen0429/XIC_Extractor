"""Strict TSV adapters for Alignment Backfill evidence records."""

from __future__ import annotations

import csv
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import cast

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceOwnerBindings,
    BackfillEvidenceRecord,
    EvidenceClaim,
    EvidenceClaimState,
    EvidenceQueryResult,
    SkippedEvidenceOwnerBindings,
    SkippedEvidenceRecord,
)
from xic_extractor.alignment.matrix import CellStatus
from xic_extractor.alignment.production_decisions import (
    ProductionStatus,
    RescueTier,
)

_NONNEGATIVE_INTEGER = re.compile(r"0|[1-9][0-9]*")
_RawRow = Mapping[str | None, str | list[str] | None]


def iter_backfill_evidence_tsv(
    path: Path,
    *,
    owner_bindings: BackfillEvidenceOwnerBindings | None = None,
) -> Iterator[BackfillEvidenceRecord]:
    for row_number, row in _iter_exact_tsv_rows(
        path,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ):
        try:
            record = _backfill_record_from_row(row)
            if owner_bindings is not None:
                _validate_backfill_bindings(record, owner_bindings)
        except ValueError as exc:
            raise ValueError(f"{path}: row {row_number}: {exc}") from exc
        yield record


def iter_skipped_evidence_tsv(
    path: Path,
    *,
    owner_bindings: SkippedEvidenceOwnerBindings | None = None,
) -> Iterator[SkippedEvidenceRecord]:
    for row_number, row in _iter_exact_tsv_rows(
        path,
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    ):
        try:
            record = _skipped_record_from_row(row)
            if owner_bindings is not None:
                _validate_skipped_bindings(record, owner_bindings)
        except ValueError as exc:
            raise ValueError(f"{path}: row {row_number}: {exc}") from exc
        yield record


def lookup_backfill_evidence_tsv(
    path: Path,
    *,
    feature_family_id: str,
    sample_stem: str,
    group_hypothesis_id: str | None = None,
    owner_bindings: BackfillEvidenceOwnerBindings | None = None,
) -> EvidenceQueryResult[BackfillEvidenceRecord]:
    _require_lookup_key("feature_family_id", feature_family_id)
    _require_lookup_key("sample_stem", sample_stem)
    if group_hypothesis_id is not None:
        _require_lookup_key("group_hypothesis_id", group_hypothesis_id)
    matches: list[BackfillEvidenceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    for record in iter_backfill_evidence_tsv(path, owner_bindings=owner_bindings):
        if record.feature_family_id != feature_family_id:
            continue
        if record.sample_stem != sample_stem:
            continue
        if (
            group_hypothesis_id is not None
            and record.group_hypothesis_id != group_hypothesis_id
        ):
            continue
        if record.identity in seen:
            raise ValueError(
                f"{path}: duplicate composite identity {record.identity!r}"
            )
        seen.add(record.identity)
        matches.append(record)
    records = tuple(matches)
    return EvidenceQueryResult(
        records=records,
        claims=_backfill_query_claims(
            found=bool(records),
            owner_bound=owner_bindings is not None,
        ),
    )


def lookup_skipped_evidence_tsv(
    path: Path,
    *,
    feature_family_id: str,
    sample_stem: str,
    owner_bindings: SkippedEvidenceOwnerBindings | None = None,
) -> EvidenceQueryResult[SkippedEvidenceRecord]:
    _require_lookup_key("feature_family_id", feature_family_id)
    _require_lookup_key("sample_stem", sample_stem)
    matches: list[SkippedEvidenceRecord] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for record in iter_skipped_evidence_tsv(path, owner_bindings=owner_bindings):
        if record.feature_family_id != feature_family_id:
            continue
        if record.sample_stem != sample_stem:
            continue
        if record.identity in seen:
            raise ValueError(
                f"{path}: duplicate composite identity {record.identity!r}"
            )
        seen.add(record.identity)
        matches.append(record)
    records = tuple(matches)
    return EvidenceQueryResult(
        records=records,
        claims=_skipped_query_claims(
            found=bool(records),
            owner_bound=owner_bindings is not None,
        ),
    )


def _backfill_record_from_row(row: Mapping[str, str]) -> BackfillEvidenceRecord:
    return BackfillEvidenceRecord(
        schema_version=_required_text(row, "schema_version"),
        feature_family_id=_required_text(row, "feature_family_id"),
        group_hypothesis_id=_required_text(row, "group_hypothesis_id"),
        public_family_id=_required_text(row, "public_family_id"),
        sample_stem=_required_text(row, "sample_stem"),
        status=cast(CellStatus, _required_text(row, "status")),
        production_cell_status=cast(
            ProductionStatus,
            _required_text(row, "production_cell_status"),
        ),
        rescue_tier=cast(RescueTier, _text(row, "rescue_tier")),
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
        primary_matrix_area_source=_text(row, "primary_matrix_area_source"),
        primary_matrix_area_reason=_text(row, "primary_matrix_area_reason"),
        apex_rt=_parse_optional_float(row, "apex_rt"),
        height=_parse_optional_float(row, "height"),
        peak_start_rt=_parse_optional_float(row, "peak_start_rt"),
        peak_end_rt=_parse_optional_float(row, "peak_end_rt"),
        rt_delta_sec=_parse_optional_float(row, "rt_delta_sec"),
        trace_quality=_text(row, "trace_quality"),
        scan_support_score=_parse_optional_float(row, "scan_support_score"),
        gap_fill_state=_text(row, "gap_fill_state"),
        gap_fill_reason=_text(row, "gap_fill_reason"),
        backfill_evidence_reason=_text(row, "backfill_evidence_reason"),
        source_candidate_id=_text(row, "source_candidate_id"),
        source_raw_file=_text(row, "source_raw_file"),
        neutral_loss_tag=_required_text(row, "neutral_loss_tag"),
        family_center_mz=_parse_required_float(row, "family_center_mz"),
        family_center_rt=_parse_required_float(row, "family_center_rt"),
        region_shadow_verdict=_text(row, "region_shadow_verdict"),
        reason=_text(row, "reason"),
    )


def _skipped_record_from_row(row: Mapping[str, str]) -> SkippedEvidenceRecord:
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


def _validate_backfill_bindings(
    record: BackfillEvidenceRecord,
    bindings: BackfillEvidenceOwnerBindings,
) -> None:
    if record.group_hypothesis_id not in bindings.known_group_ids:
        raise ValueError(
            f"unknown group_hypothesis_id {record.group_hypothesis_id!r}"
        )
    if record.sample_stem not in bindings.known_sample_stems:
        raise ValueError(f"unknown sample_stem {record.sample_stem!r}")
    if not record.source_candidate_id:
        return
    candidate_sample = bindings.candidate_samples.get(record.source_candidate_id)
    if candidate_sample is None:
        raise ValueError(
            f"unknown source_candidate_id {record.source_candidate_id!r}"
        )
    if candidate_sample != record.sample_stem:
        raise ValueError(
            f"source_candidate_id {record.source_candidate_id!r} belongs to sample "
            f"{candidate_sample!r}, not {record.sample_stem!r}"
        )


def _validate_skipped_bindings(
    record: SkippedEvidenceRecord,
    bindings: SkippedEvidenceOwnerBindings,
) -> None:
    if record.feature_family_id not in bindings.known_feature_family_ids:
        raise ValueError(
            f"unknown feature_family_id {record.feature_family_id!r}"
        )
    if record.sample_stem not in bindings.known_sample_stems:
        raise ValueError(f"unknown sample_stem {record.sample_stem!r}")
    if record.backfill_scope != bindings.expected_backfill_scope:
        raise ValueError(
            f"backfill_scope {record.backfill_scope!r} does not match expected "
            f"{bindings.expected_backfill_scope!r}"
        )
    if record.predicate_version != bindings.expected_predicate_version:
        raise ValueError(
            f"predicate_version {record.predicate_version!r} does not match expected "
            f"{bindings.expected_predicate_version!r}"
        )
    if record.source_artifact != bindings.expected_source_artifact:
        raise ValueError(
            f"source_artifact {record.source_artifact!r} does not match expected "
            f"{bindings.expected_source_artifact!r}"
        )


def _backfill_query_claims(
    *,
    found: bool,
    owner_bound: bool,
) -> tuple[EvidenceClaim, ...]:
    return (
        EvidenceClaim(
            subject="record_content",
            state=(
                EvidenceClaimState.STRUCTURE_BOUND
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "the reduced BackfillEvidenceRecord passed the exact schema and "
                "intrinsic projection invariants"
                if found
                else "no matching reduced record was retained"
            ),
        ),
        EvidenceClaim(
            subject="cross_artifact_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and owner_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "group, sample, and candidate bindings passed owner validation"
                if found and owner_bound
                else "complete owner bindings were not supplied or no record matched"
            ),
        ),
        EvidenceClaim(
            subject="production_decision",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "the compact schema omits complete ProductionDecisionSet owner inputs"
            ),
        ),
        EvidenceClaim(
            subject="full_aligned_cell",
            state=EvidenceClaimState.UNAVAILABLE,
            reason="the compact schema omits complete AlignedCell owner content",
        ),
        EvidenceClaim(
            subject="missing_key_absence",
            state=EvidenceClaimState.UNAVAILABLE,
            reason="zero retained rows does not prove scientific absence",
        ),
    )


def _skipped_query_claims(
    *,
    found: bool,
    owner_bound: bool,
) -> tuple[EvidenceClaim, ...]:
    return (
        EvidenceClaim(
            subject="record_content",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "complete SkippedEvidenceRecord reconstructed by the Alignment "
                "Backfill scope owner parser"
                if found
                else "no matching skipped record was retained"
            ),
        ),
        EvidenceClaim(
            subject="cross_artifact_binding",
            state=(
                EvidenceClaimState.OWNER_REHYDRATED
                if found and owner_bound
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "family, sample, scope, predicate, and source bindings passed"
                if found and owner_bound
                else "complete owner bindings were not supplied or no record matched"
            ),
        ),
        EvidenceClaim(
            subject="full_audit_execution",
            state=(
                EvidenceClaimState.STRUCTURE_BOUND
                if found
                else EvidenceClaimState.UNAVAILABLE
            ),
            reason=(
                "the persisted availability literal passed schema invariants but is "
                "not a runtime execution receipt"
                if found
                else "no matching skipped record was retained"
            ),
        ),
        EvidenceClaim(
            subject="missing_key_absence",
            state=EvidenceClaimState.UNAVAILABLE,
            reason=(
                "zero retained rows does not prove a Backfill request was unnecessary"
            ),
        ),
    )


def _iter_exact_tsv_rows(
    path: Path,
    expected_header: Sequence[str],
) -> Iterator[tuple[int, Mapping[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        _validate_exact_header(path, reader.fieldnames, expected_header)
        for row_number, raw_row in enumerate(reader, start=2):
            row = cast(_RawRow, raw_row)
            if None in row:
                raise ValueError(f"{path}: row {row_number}: unexpected extra fields")
            normalized: dict[str, str] = {}
            for field_name in expected_header:
                value = row.get(field_name)
                if not isinstance(value, str):
                    raise ValueError(
                        f"{path}: row {row_number}: field {field_name} is missing"
                    )
                normalized[field_name] = value
            yield row_number, normalized


def _validate_exact_header(
    path: Path,
    fieldnames: Sequence[str] | None,
    expected_header: Sequence[str],
) -> None:
    if fieldnames is None:
        raise ValueError(f"{path}: missing header row; expected exact header")
    seen: set[str] = set()
    duplicates: list[str] = []
    for field_name in fieldnames:
        if field_name in seen and field_name not in duplicates:
            duplicates.append(field_name)
        seen.add(field_name)
    if duplicates:
        raise ValueError(
            f"{path}: duplicate header fields: {', '.join(duplicates)}"
        )
    if tuple(fieldnames) != tuple(expected_header):
        raise ValueError(
            f"{path}: expected exact header {tuple(expected_header)!r}, "
            f"got {tuple(fieldnames)!r}"
        )


def _required_text(row: Mapping[str, str], field_name: str) -> str:
    value = _text(row, field_name).strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def _text(row: Mapping[str, str], field_name: str) -> str:
    return row[field_name]


def _parse_required_float(row: Mapping[str, str], field_name: str) -> float:
    value = _parse_optional_float(row, field_name)
    if value is None:
        raise ValueError(f"{field_name} is required")
    return value


def _parse_optional_float(
    row: Mapping[str, str],
    field_name: str,
) -> float | None:
    raw_value = _text(row, field_name).strip()
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
    raw_value = _text(row, field_name).strip()
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
    style: str,
) -> bool:
    allowed = {"TRUE": True, "FALSE": False} if style == "upper" else {
        "True": True,
        "False": False,
    }
    raw_value = _text(row, field_name)
    if raw_value not in allowed:
        raise ValueError(
            f"{field_name} must be one of {tuple(allowed)!r}, got {raw_value!r}"
        )
    return allowed[raw_value]


def _parse_row_flags(row: Mapping[str, str]) -> tuple[str, ...]:
    raw_value = _text(row, "row_flags")
    if not raw_value:
        return ()
    flags = tuple(raw_value.split(";"))
    if any(not flag for flag in flags):
        raise ValueError("row_flags must not contain empty members")
    if len(flags) != len(set(flags)):
        raise ValueError("row_flags must not contain duplicates")
    return flags


def _require_lookup_key(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} lookup key must be non-empty")


__all__ = (
    "iter_backfill_evidence_tsv",
    "iter_skipped_evidence_tsv",
    "lookup_backfill_evidence_tsv",
    "lookup_skipped_evidence_tsv",
)
