from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SAMPLE_METADATA_SCHEMA_VERSION = "sample_metadata_v1"
SAMPLE_METADATA_COLUMNS: tuple[str, ...] = (
    "schema_version",
    "sample_name",
    "raw_stem",
    "injection_order",
    "sample_role",
    "batch_id",
    "prep_batch_id",
    "matrix_type",
    "group",
    "excluded",
    "exclusion_reason",
)
SampleRole = Literal[
    "study_sample",
    "qc",
    "pooled_qc",
    "blank",
    "calibrator",
    "solvent",
    "system_suitability",
    "unknown",
]
SAMPLE_ROLES: frozenset[str] = frozenset(
    {
        "study_sample",
        "qc",
        "pooled_qc",
        "blank",
        "calibrator",
        "solvent",
        "system_suitability",
        "unknown",
    }
)


@dataclass(frozen=True)
class SampleMetadata:
    sample_name: str
    raw_stem: str
    injection_order: int | None = None
    sample_role: SampleRole = "unknown"
    batch_id: str = ""
    prep_batch_id: str = ""
    matrix_type: str = ""
    group: str = ""
    excluded: bool = False
    exclusion_reason: str = ""


class SampleMetadataError(ValueError):
    """Raised when sample metadata cannot be treated as a shared contract."""


def load_sample_metadata(path: Path) -> tuple[SampleMetadata, ...]:
    return parse_sample_metadata(
        _read_sample_metadata_rows(path),
        source=str(path),
    )


def is_sample_metadata_source(path: Path) -> bool:
    if path.suffix.lower() not in {".csv", ".tsv", ".txt"} or not path.is_file():
        return False
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        fieldnames = set(reader.fieldnames or ())
    return "schema_version" in fieldnames and "sample_name" in fieldnames


def parse_sample_metadata(
    rows: Iterable[Mapping[str, object]],
    *,
    source: str = "<memory>",
) -> tuple[SampleMetadata, ...]:
    parsed: list[SampleMetadata] = []
    seen_sample_names: set[str] = set()
    seen_raw_stems: set[str] = set()
    seen_aliases: dict[str, tuple[int, str]] = {}
    for index, row in enumerate(rows, start=2):
        normalized = _normalize_row(row)
        if not any(normalized.values()):
            continue
        item = _parse_sample_metadata_row(normalized, source=source, row_number=index)
        _check_unique(item.sample_name, seen_sample_names, "sample_name", source, index)
        if item.raw_stem:
            _check_unique(item.raw_stem, seen_raw_stems, "raw_stem", source, index)
        _register_alias(
            item.sample_name,
            "sample_name",
            seen_aliases,
            source,
            index,
        )
        if item.raw_stem:
            _register_alias(
                item.raw_stem,
                "raw_stem",
                seen_aliases,
                source,
                index,
            )
        parsed.append(item)
    return tuple(parsed)


def sample_metadata_to_injection_order(
    rows: Sequence[SampleMetadata],
) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        if row.injection_order is None:
            continue
        _add_injection_order(out, row.sample_name, row.injection_order)
        if row.raw_stem and row.raw_stem != row.sample_name:
            _add_injection_order(out, row.raw_stem, row.injection_order)
    return out


def summarize_sample_metadata(rows: Sequence[SampleMetadata]) -> dict[str, object]:
    role_counts = {role: 0 for role in sorted(SAMPLE_ROLES)}
    excluded_count = 0
    with_injection_order = 0
    for row in rows:
        role_counts[row.sample_role] += 1
        if row.excluded:
            excluded_count += 1
        if row.injection_order is not None:
            with_injection_order += 1
    return {
        "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
        "sample_count": len(rows),
        "with_injection_order_count": with_injection_order,
        "excluded_count": excluded_count,
        "role_counts": role_counts,
    }


def _read_sample_metadata_rows(path: Path) -> list[Mapping[str, object]]:
    if not path.is_file():
        raise SampleMetadataError(f"{path}: sample metadata file not found")
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".txt"} else ","
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        if reader.fieldnames is None:
            raise SampleMetadataError(f"{path}: missing sample metadata header")
        missing = [
            header
            for header in SAMPLE_METADATA_COLUMNS
            if header not in reader.fieldnames
        ]
        if missing:
            raise SampleMetadataError(
                f"{path}: missing sample metadata columns: {', '.join(missing)}"
            )
        return list(reader)


def _parse_sample_metadata_row(
    row: Mapping[str, str],
    *,
    source: str,
    row_number: int,
) -> SampleMetadata:
    schema_version = row["schema_version"]
    if schema_version != SAMPLE_METADATA_SCHEMA_VERSION:
        raise SampleMetadataError(
            f"{source}:{row_number}: unsupported schema_version {schema_version!r}"
        )
    sample_name = _required_text(row, "sample_name", source, row_number)
    raw_stem = row["raw_stem"] or sample_name
    injection_order = _optional_int(
        row["injection_order"],
        "injection_order",
        source,
        row_number,
    )
    sample_role = row["sample_role"] or "unknown"
    if sample_role not in SAMPLE_ROLES:
        raise SampleMetadataError(
            f"{source}:{row_number}: unsupported sample_role {sample_role!r}"
        )
    excluded = _parse_bool(row["excluded"], "excluded", source, row_number)
    if excluded and not row["exclusion_reason"]:
        raise SampleMetadataError(
            f"{source}:{row_number}: excluded samples require exclusion_reason"
        )
    return SampleMetadata(
        sample_name=sample_name,
        raw_stem=raw_stem,
        injection_order=injection_order,
        sample_role=sample_role,  # type: ignore[arg-type]
        batch_id=row["batch_id"],
        prep_batch_id=row["prep_batch_id"],
        matrix_type=row["matrix_type"],
        group=row["group"],
        excluded=excluded,
        exclusion_reason=row["exclusion_reason"],
    )


def _normalize_row(row: Mapping[str, object]) -> dict[str, str]:
    return {
        header: _clean_text(row.get(header, ""))
        for header in SAMPLE_METADATA_COLUMNS
    }


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _required_text(
    row: Mapping[str, str],
    key: str,
    source: str,
    row_number: int,
) -> str:
    value = row[key]
    if not value:
        raise SampleMetadataError(f"{source}:{row_number}: {key} is required")
    return value


def _optional_int(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SampleMetadataError(
            f"{source}:{row_number}: {key} must be an integer"
        ) from exc
    if parsed < 1:
        raise SampleMetadataError(f"{source}:{row_number}: {key} must be >= 1")
    return parsed


def _parse_bool(
    value: str,
    key: str,
    source: str,
    row_number: int,
) -> bool:
    if not value:
        return False
    normalized = value.upper()
    if normalized == "TRUE":
        return True
    if normalized == "FALSE":
        return False
    raise SampleMetadataError(f"{source}:{row_number}: {key} must be TRUE or FALSE")


def _check_unique(
    value: str,
    seen: set[str],
    key: str,
    source: str,
    row_number: int,
) -> None:
    if value in seen:
        raise SampleMetadataError(f"{source}:{row_number}: duplicate {key} {value!r}")
    seen.add(value)


def _register_alias(
    value: str,
    alias_type: str,
    seen: dict[str, tuple[int, str]],
    source: str,
    row_number: int,
) -> None:
    existing = seen.get(value)
    if existing is not None and existing[0] != row_number:
        existing_row, existing_type = existing
        raise SampleMetadataError(
            f"{source}:{row_number}: sample metadata alias collision {value!r}; "
            f"already used as {existing_type} on row {existing_row}"
        )
    seen[value] = (row_number, alias_type)


def _add_injection_order(out: dict[str, int], sample: str, order: int) -> None:
    existing = out.get(sample)
    if existing is not None and existing != order:
        raise SampleMetadataError(
            f"conflicting injection_order for sample {sample!r}: {existing} != {order}"
        )
    out[sample] = order
