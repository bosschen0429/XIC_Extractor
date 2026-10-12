"""Strict TSV adapters for Alignment Backfill evidence records."""

from __future__ import annotations

import csv
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import cast

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceOwnerBindings,
    BackfillEvidenceOwnerResult,
    BackfillEvidenceRecord,
    EvidenceQueryResult,
    SkippedEvidenceOwnerBindings,
    SkippedEvidenceRecord,
)
from xic_extractor.alignment.evidence_claims import mint_evidence_query_result
from xic_extractor.diagnostics.backfill_evidence.bindings import (
    backfill_evidence_query_claims,
    rehydrate_backfill_evidence_bindings,
    skipped_evidence_query_claims,
    validate_skipped_evidence_bindings,
)
from xic_extractor.diagnostics.backfill_evidence.serialization import (
    backfill_evidence_record_from_projection,
    skipped_evidence_record_from_projection,
)

_RawRow = Mapping[str | None, str | list[str] | None]


def iter_backfill_evidence_tsv(
    path: Path,
    *,
    owner_bindings: BackfillEvidenceOwnerBindings | None = None,
) -> Iterator[BackfillEvidenceRecord]:
    for record, _owner_result in _iter_backfill_evidence_tsv_results(
        path,
        owner_bindings=owner_bindings,
    ):
        yield record


def iter_owner_bound_backfill_evidence_tsv(
    path: Path,
    *,
    owner_bindings: BackfillEvidenceOwnerBindings,
) -> Iterator[BackfillEvidenceOwnerResult]:
    for _record, owner_result in _iter_backfill_evidence_tsv_results(
        path,
        owner_bindings=owner_bindings,
    ):
        assert owner_result is not None
        yield owner_result


def _iter_backfill_evidence_tsv_results(
    path: Path,
    *,
    owner_bindings: BackfillEvidenceOwnerBindings | None,
) -> Iterator[tuple[BackfillEvidenceRecord, BackfillEvidenceOwnerResult | None]]:
    for row_number, row in _iter_exact_tsv_rows(
        path,
        ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    ):
        try:
            record = backfill_evidence_record_from_projection(row)
            owner_result = None
            if owner_bindings is not None:
                owner_result = rehydrate_backfill_evidence_bindings(
                    record,
                    owner_bindings,
                )
                record = owner_result.record
        except ValueError as exc:
            raise ValueError(f"{path}: row {row_number}: {exc}") from exc
        yield record, owner_result


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
            record = skipped_evidence_record_from_projection(row)
            if owner_bindings is not None:
                validate_skipped_evidence_bindings(record, owner_bindings)
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
    owner_results: list[BackfillEvidenceOwnerResult] = []
    seen: set[tuple[str, str, str]] = set()
    for record, owner_result in _iter_backfill_evidence_tsv_results(
        path,
        owner_bindings=owner_bindings,
    ):
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
        if owner_result is not None:
            owner_results.append(owner_result)
    records = tuple(matches)
    return mint_evidence_query_result(
        records=records,
        claims=backfill_evidence_query_claims(
            found=bool(records),
            owner_results=(
                tuple(owner_results) if owner_bindings is not None else None
            ),
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
    return mint_evidence_query_result(
        records=records,
        claims=skipped_evidence_query_claims(
            found=bool(records),
            owner_bound=owner_bindings is not None,
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


def _require_lookup_key(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} lookup key must be non-empty")


__all__ = (
    "iter_backfill_evidence_tsv",
    "iter_owner_bound_backfill_evidence_tsv",
    "iter_skipped_evidence_tsv",
    "lookup_backfill_evidence_tsv",
    "lookup_skipped_evidence_tsv",
)
