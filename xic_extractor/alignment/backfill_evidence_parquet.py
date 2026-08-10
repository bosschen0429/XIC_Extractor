"""Optional Parquet shadow adapter for owner-readable Backfill evidence."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar, cast

from xic_extractor.alignment.backfill_evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    BackfillEvidenceOwnerBindings,
    BackfillEvidenceRecord,
    EvidenceQueryResult,
    SkippedEvidenceOwnerBindings,
    SkippedEvidenceRecord,
)
from xic_extractor.alignment.backfill_evidence_owner import (
    backfill_evidence_query_claims,
    skipped_evidence_query_claims,
    validate_backfill_evidence_bindings,
    validate_skipped_evidence_bindings,
)
from xic_extractor.alignment.backfill_evidence_serialization import (
    backfill_evidence_record_from_projection,
    backfill_evidence_record_to_projection,
    skipped_evidence_record_from_projection,
    skipped_evidence_record_to_projection,
)

PARQUET_ROW_ORDINAL_COLUMN = "_row_ordinal"
DEFAULT_PARQUET_ROW_GROUP_SIZE = 16_384
DEFAULT_PARQUET_COMPRESSION = "zstd"

_RecordT = TypeVar("_RecordT")


def write_backfill_evidence_parquet(
    path: Path,
    records: Iterable[BackfillEvidenceRecord],
    *,
    row_group_size: int = DEFAULT_PARQUET_ROW_GROUP_SIZE,
    compression: str = DEFAULT_PARQUET_COMPRESSION,
) -> int:
    return _write_records(
        path,
        records,
        columns=ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        project=backfill_evidence_record_to_projection,
        row_group_size=row_group_size,
        compression=compression,
    )


def write_skipped_evidence_parquet(
    path: Path,
    records: Iterable[SkippedEvidenceRecord],
    *,
    row_group_size: int = DEFAULT_PARQUET_ROW_GROUP_SIZE,
    compression: str = DEFAULT_PARQUET_COMPRESSION,
) -> int:
    return _write_records(
        path,
        records,
        columns=SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        project=skipped_evidence_record_to_projection,
        row_group_size=row_group_size,
        compression=compression,
    )


def iter_backfill_evidence_parquet(
    path: Path,
    *,
    owner_bindings: BackfillEvidenceOwnerBindings | None = None,
    batch_size: int = DEFAULT_PARQUET_ROW_GROUP_SIZE,
) -> Iterator[BackfillEvidenceRecord]:
    for row_number, projection in _iter_projection_rows(
        path,
        columns=ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        batch_size=batch_size,
    ):
        try:
            record = backfill_evidence_record_from_projection(projection)
            if owner_bindings is not None:
                validate_backfill_evidence_bindings(record, owner_bindings)
        except ValueError as exc:
            raise ValueError(f"{path}: row ordinal {row_number}: {exc}") from exc
        yield record


def iter_skipped_evidence_parquet(
    path: Path,
    *,
    owner_bindings: SkippedEvidenceOwnerBindings | None = None,
    batch_size: int = DEFAULT_PARQUET_ROW_GROUP_SIZE,
) -> Iterator[SkippedEvidenceRecord]:
    for row_number, projection in _iter_projection_rows(
        path,
        columns=SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        batch_size=batch_size,
    ):
        try:
            record = skipped_evidence_record_from_projection(projection)
            if owner_bindings is not None:
                validate_skipped_evidence_bindings(record, owner_bindings)
        except ValueError as exc:
            raise ValueError(f"{path}: row ordinal {row_number}: {exc}") from exc
        yield record


def lookup_backfill_evidence_parquet(
    path: Path,
    *,
    feature_family_id: str,
    sample_stem: str,
    group_hypothesis_id: str | None = None,
    owner_bindings: BackfillEvidenceOwnerBindings | None = None,
) -> EvidenceQueryResult[BackfillEvidenceRecord]:
    _require_lookup_key("feature_family_id", feature_family_id)
    _require_lookup_key("sample_stem", sample_stem)
    filters: list[tuple[str, str, str]] = [
        ("feature_family_id", "=", feature_family_id),
        ("sample_stem", "=", sample_stem),
    ]
    if group_hypothesis_id is not None:
        _require_lookup_key("group_hypothesis_id", group_hypothesis_id)
        filters.append(("group_hypothesis_id", "=", group_hypothesis_id))
    records: list[BackfillEvidenceRecord] = []
    seen: set[tuple[str, str, str]] = set()
    owner_bound = owner_bindings is not None
    for row_number, projection in _read_filtered_projection_rows(
        path,
        columns=ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
        filters=filters,
    ):
        try:
            record = backfill_evidence_record_from_projection(projection)
            if owner_bindings is not None:
                validate_backfill_evidence_bindings(record, owner_bindings)
                owner_bound = record.owner_binding_ids_available and owner_bound
        except ValueError as exc:
            raise ValueError(f"{path}: row ordinal {row_number}: {exc}") from exc
        if record.identity in seen:
            raise ValueError(
                f"{path}: duplicate composite identity {record.identity!r}"
            )
        seen.add(record.identity)
        records.append(record)
    result = tuple(records)
    return EvidenceQueryResult(
        records=result,
        claims=backfill_evidence_query_claims(
            found=bool(result),
            owner_bound=owner_bound,
        ),
    )


def lookup_skipped_evidence_parquet(
    path: Path,
    *,
    feature_family_id: str,
    sample_stem: str,
    owner_bindings: SkippedEvidenceOwnerBindings | None = None,
) -> EvidenceQueryResult[SkippedEvidenceRecord]:
    _require_lookup_key("feature_family_id", feature_family_id)
    _require_lookup_key("sample_stem", sample_stem)
    filters = [
        ("feature_family_id", "=", feature_family_id),
        ("sample_stem", "=", sample_stem),
    ]
    records: list[SkippedEvidenceRecord] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for row_number, projection in _read_filtered_projection_rows(
        path,
        columns=SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        filters=filters,
    ):
        try:
            record = skipped_evidence_record_from_projection(projection)
            if owner_bindings is not None:
                validate_skipped_evidence_bindings(record, owner_bindings)
        except ValueError as exc:
            raise ValueError(f"{path}: row ordinal {row_number}: {exc}") from exc
        if record.identity in seen:
            raise ValueError(
                f"{path}: duplicate composite identity {record.identity!r}"
            )
        seen.add(record.identity)
        records.append(record)
    result = tuple(records)
    return EvidenceQueryResult(
        records=result,
        claims=skipped_evidence_query_claims(
            found=bool(result),
            owner_bound=owner_bindings is not None,
        ),
    )


def parquet_evidence_row_count(path: Path, *, columns: Sequence[str]) -> int:
    _pa, pq = _load_pyarrow()
    parquet_file = pq.ParquetFile(str(path))
    try:
        _validate_parquet_schema(path, parquet_file.schema_arrow, columns)
        return int(parquet_file.metadata.num_rows)
    finally:
        parquet_file.close()


def _write_records(
    path: Path,
    records: Iterable[_RecordT],
    *,
    columns: tuple[str, ...],
    project: Callable[[_RecordT], dict[str, str]],
    row_group_size: int,
    compression: str,
) -> int:
    if row_group_size <= 0:
        raise ValueError("row_group_size must be positive")
    pa, pq = _load_pyarrow()
    schema = _parquet_schema(pa, columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = pq.ParquetWriter(
        str(path),
        schema,
        compression=compression,
        use_dictionary=True,
        write_statistics=True,
    )
    row_count = 0
    batch: list[dict[str, str | int]] = []
    try:
        for record in records:
            row: dict[str, str | int] = dict(project(record))
            row[PARQUET_ROW_ORDINAL_COLUMN] = row_count
            batch.append(row)
            row_count += 1
            if len(batch) >= row_group_size:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()
    return row_count


def _iter_projection_rows(
    path: Path,
    *,
    columns: Sequence[str],
    batch_size: int,
) -> Iterator[tuple[int, Mapping[str, str]]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    _pa, pq = _load_pyarrow()
    parquet_file = pq.ParquetFile(str(path))
    expected_ordinal = 0
    try:
        _validate_parquet_schema(path, parquet_file.schema_arrow, columns)
        for batch in parquet_file.iter_batches(batch_size=batch_size):
            for raw_row in batch.to_pylist():
                ordinal, projection = _projection_row(path, raw_row, columns)
                if ordinal != expected_ordinal:
                    raise ValueError(
                        f"{path}: row ordinal {ordinal!r} does not match expected "
                        f"source order {expected_ordinal}"
                    )
                expected_ordinal += 1
                yield ordinal, projection
    finally:
        parquet_file.close()


def _read_filtered_projection_rows(
    path: Path,
    *,
    columns: Sequence[str],
    filters: list[tuple[str, str, str]],
) -> tuple[tuple[int, Mapping[str, str]], ...]:
    _pa, pq = _load_pyarrow()
    parquet_file = pq.ParquetFile(str(path))
    try:
        _validate_parquet_schema(path, parquet_file.schema_arrow, columns)
    finally:
        parquet_file.close()
    table = pq.read_table(str(path), filters=filters)
    rows: list[tuple[int, Mapping[str, str]]] = []
    previous_ordinal: int | None = None
    for raw_row in table.to_pylist():
        ordinal, projection = _projection_row(path, raw_row, columns)
        if previous_ordinal is not None and ordinal <= previous_ordinal:
            raise ValueError(
                f"{path}: filtered row ordinals must be strictly increasing; "
                f"got {ordinal} after {previous_ordinal}"
            )
        rows.append((ordinal, projection))
        previous_ordinal = ordinal
    return tuple(rows)


def _projection_row(
    path: Path,
    raw_row: Mapping[str, object],
    columns: Sequence[str],
) -> tuple[int, Mapping[str, str]]:
    ordinal = raw_row.get(PARQUET_ROW_ORDINAL_COLUMN)
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
        raise ValueError(f"{path}: row ordinal must be a non-negative integer")
    projection: dict[str, str] = {}
    for field_name in columns:
        value = raw_row.get(field_name)
        if not isinstance(value, str):
            raise ValueError(f"{path}: field {field_name} must be a non-null string")
        projection[field_name] = value
    return ordinal, projection


def _parquet_schema(pa: Any, columns: Sequence[str]) -> Any:
    return pa.schema(
        [pa.field(field_name, pa.string(), nullable=False) for field_name in columns]
        + [pa.field(PARQUET_ROW_ORDINAL_COLUMN, pa.int64(), nullable=False)]
    )


def _validate_parquet_schema(
    path: Path,
    actual_schema: Any,
    columns: Sequence[str],
) -> None:
    pa, _pq = _load_pyarrow()
    expected_schema = _parquet_schema(pa, columns)
    actual_fields = tuple(
        (field.name, str(field.type), field.nullable) for field in actual_schema
    )
    expected_fields = tuple(
        (field.name, str(field.type), field.nullable) for field in expected_schema
    )
    if actual_fields != expected_fields:
        raise ValueError(
            f"{path}: expected exact Parquet schema {expected_fields!r}, "
            f"got {actual_fields!r}"
        )


def _require_lookup_key(field_name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} lookup key must be non-empty")


def _load_pyarrow() -> tuple[Any, Any]:
    try:
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Parquet shadow support requires the optional 'parquet' dependency; "
            "install xic-extractor[parquet]"
        ) from exc
    return cast(Any, pa), cast(Any, pq)


__all__ = (
    "DEFAULT_PARQUET_COMPRESSION",
    "DEFAULT_PARQUET_ROW_GROUP_SIZE",
    "PARQUET_ROW_ORDINAL_COLUMN",
    "iter_backfill_evidence_parquet",
    "iter_skipped_evidence_parquet",
    "lookup_backfill_evidence_parquet",
    "lookup_skipped_evidence_parquet",
    "parquet_evidence_row_count",
    "write_backfill_evidence_parquet",
    "write_skipped_evidence_parquet",
)
