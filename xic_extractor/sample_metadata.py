from __future__ import annotations

import csv
import hashlib
import json
import re
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
_CANONICAL_SAMPLE_ID_PREFIX = "xic.sample.v1:"
_SAMPLE_METADATA_SNAPSHOT_PREFIX = "xic.sample-metadata-snapshot.v1:"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True, slots=True)
class SampleMetadataSnapshot:
    """Owner-minted immutable snapshot for one sample identity namespace."""

    snapshot_id: str
    content_sha256: str
    identity_namespace: str
    rows: tuple[SampleMetadata, ...]

    def __post_init__(self) -> None:
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        content_sha256 = _validate_sha256(
            self.content_sha256,
            field="content_sha256",
        )
        rows = _validated_snapshot_rows(self.rows)
        if rows != self.rows:
            raise SampleMetadataError(
                "sample metadata snapshot rows must be canonical-sorted"
            )
        expected_content_sha256 = _sample_metadata_snapshot_content_sha256(
            rows,
            identity_namespace=namespace,
        )
        if content_sha256 != expected_content_sha256:
            raise SampleMetadataError(
                "sample metadata snapshot content digest mismatches owner rows"
            )
        expected_id = _sample_metadata_snapshot_id(content_sha256)
        if self.snapshot_id != expected_id:
            raise SampleMetadataError(
                "sample metadata snapshot ID mismatches owner content"
            )


@dataclass(frozen=True, slots=True)
class CanonicalSampleIdentity:
    """Owner-minted sample identity; aliases and projections are not its basis."""

    sample_identity_id: str
    sample_metadata_schema_version: str
    identity_namespace: str
    sample_key: str

    def __post_init__(self) -> None:
        if self.sample_metadata_schema_version != SAMPLE_METADATA_SCHEMA_VERSION:
            raise SampleMetadataError(
                "sample identity schema does not match sample_metadata_v1"
            )
        namespace = _owner_normalized_text(
            self.identity_namespace,
            field="identity_namespace",
        )
        sample_key = _canonical_sample_key(self.sample_key)
        expected_id = _canonical_sample_identity_id(
            sample_key,
            identity_namespace=namespace,
        )
        if self.sample_identity_id != expected_id:
            raise SampleMetadataError(
                "sample_identity_id mismatches the owner-minted sample key"
            )


@dataclass(frozen=True, slots=True)
class ResolvedCanonicalSampleIdentity:
    """Self-validating owner resolution of one current metadata lookup token."""

    identity: CanonicalSampleIdentity
    snapshot: SampleMetadataSnapshot
    metadata: SampleMetadata
    lookup_token: str

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CanonicalSampleIdentity):
            raise SampleMetadataError(
                "identity must be an owner-minted canonical sample identity"
            )
        if not isinstance(self.snapshot, SampleMetadataSnapshot):
            raise SampleMetadataError(
                "snapshot must be an owner-minted SampleMetadataSnapshot"
            )
        if not isinstance(self.metadata, SampleMetadata):
            raise SampleMetadataError(
                "metadata must be the owner row used for sample resolution"
            )
        if self.metadata not in self.snapshot.rows:
            raise SampleMetadataError(
                "resolved metadata row is not present in the owner snapshot"
            )
        token = _owner_normalized_text(self.lookup_token, field="lookup_token")
        if token not in {self.metadata.sample_name, self.metadata.raw_stem}:
            raise SampleMetadataError(
                "lookup_token does not bind the resolved sample metadata row"
            )
        if self.identity != mint_canonical_sample_identity(
            self.metadata,
            identity_namespace=self.snapshot.identity_namespace,
        ):
            raise SampleMetadataError(
                "resolved sample identity mismatches its owner metadata row"
            )


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


def mint_canonical_sample_identity(
    row: SampleMetadata,
    *,
    identity_namespace: str,
) -> CanonicalSampleIdentity:
    """Mint the v1 canonical identity from the owner-declared primary key."""

    if not isinstance(row, SampleMetadata):
        raise SampleMetadataError("sample identity must be minted from SampleMetadata")
    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    sample_key = _canonical_sample_key(row.sample_name)
    return CanonicalSampleIdentity(
        sample_identity_id=_canonical_sample_identity_id(
            sample_key,
            identity_namespace=namespace,
        ),
        sample_metadata_schema_version=SAMPLE_METADATA_SCHEMA_VERSION,
        identity_namespace=namespace,
        sample_key=sample_key,
    )


def mint_sample_metadata_snapshot(
    rows: Sequence[SampleMetadata],
    *,
    identity_namespace: str,
) -> SampleMetadataSnapshot:
    """Mint one immutable, order-neutral owner snapshot without persistence."""

    namespace = _owner_normalized_text(
        identity_namespace,
        field="identity_namespace",
    )
    canonical_rows = _validated_snapshot_rows(tuple(rows))
    content_sha256 = _sample_metadata_snapshot_content_sha256(
        canonical_rows,
        identity_namespace=namespace,
    )
    return SampleMetadataSnapshot(
        snapshot_id=_sample_metadata_snapshot_id(content_sha256),
        content_sha256=content_sha256,
        identity_namespace=namespace,
        rows=canonical_rows,
    )


def resolve_canonical_sample_identity(
    snapshot: SampleMetadataSnapshot,
    token: str,
    *,
    expected_identity: CanonicalSampleIdentity | None = None,
) -> ResolvedCanonicalSampleIdentity:
    """Resolve one exact owner alias and optionally reject stale rebinding."""

    if not isinstance(snapshot, SampleMetadataSnapshot):
        raise SampleMetadataError(
            "sample resolution requires an owner-minted SampleMetadataSnapshot"
        )
    clean_token = _owner_normalized_text(token, field="sample identity token")
    matches = [
        row
        for row in snapshot.rows
        if clean_token == row.sample_name or clean_token == row.raw_stem
    ]
    if not matches:
        raise SampleMetadataError(
            f"sample identity token {clean_token!r} is not owned by sample metadata"
        )
    if len(matches) != 1:
        raise SampleMetadataError(
            f"sample identity token {clean_token!r} is ambiguous"
        )
    resolved = mint_canonical_sample_identity(
        matches[0],
        identity_namespace=snapshot.identity_namespace,
    )
    if expected_identity is not None:
        if not isinstance(expected_identity, CanonicalSampleIdentity):
            raise SampleMetadataError(
                "expected_identity must be an owner-minted sample identity"
            )
        if resolved != expected_identity:
            raise SampleMetadataError(
                f"sample identity token {clean_token!r} resolved to a stale binding"
            )
    return ResolvedCanonicalSampleIdentity(
        identity=resolved,
        snapshot=snapshot,
        metadata=matches[0],
        lookup_token=clean_token,
    )


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


def _canonical_sample_key(value: object) -> str:
    return _owner_normalized_text(value, field="sample_key")


def _owner_normalized_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SampleMetadataError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _canonical_sample_identity_id(
    sample_key: str,
    *,
    identity_namespace: str,
) -> str:
    digest = _canonical_json_sha256(
        {
            "identity_namespace": identity_namespace,
            "sample_key": sample_key,
            "sample_metadata_schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "schema": "canonical_sample_identity_v1",
        }
    )
    return f"{_CANONICAL_SAMPLE_ID_PREFIX}{digest}"


def _validated_snapshot_rows(
    rows: tuple[SampleMetadata, ...],
) -> tuple[SampleMetadata, ...]:
    if not rows:
        raise SampleMetadataError("sample metadata snapshot must contain rows")
    if any(not isinstance(row, SampleMetadata) for row in rows):
        raise SampleMetadataError(
            "sample metadata snapshot rows must be SampleMetadata"
        )
    serialized = [
        {
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "sample_name": row.sample_name,
            "raw_stem": row.raw_stem,
            "injection_order": row.injection_order,
            "sample_role": row.sample_role,
            "batch_id": row.batch_id,
            "prep_batch_id": row.prep_batch_id,
            "matrix_type": row.matrix_type,
            "group": row.group,
            "excluded": "TRUE" if row.excluded else "FALSE",
            "exclusion_reason": row.exclusion_reason,
        }
        for row in rows
    ]
    parsed = parse_sample_metadata(
        serialized,
        source="<sample-metadata-snapshot>",
    )
    canonical_input = tuple(
        sorted(rows, key=lambda row: (row.sample_name, row.raw_stem))
    )
    canonical_parsed = tuple(
        sorted(parsed, key=lambda row: (row.sample_name, row.raw_stem))
    )
    if canonical_input != canonical_parsed:
        raise SampleMetadataError(
            "sample metadata snapshot rows must be owner-normalized"
        )
    return canonical_parsed


def _sample_metadata_snapshot_content_sha256(
    rows: tuple[SampleMetadata, ...],
    *,
    identity_namespace: str,
) -> str:
    return _canonical_json_sha256(
        {
            "schema": "sample_metadata_snapshot_v1",
            "sample_metadata_schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            "identity_namespace": identity_namespace,
            "rows": [
                {
                    "sample_name": row.sample_name,
                    "raw_stem": row.raw_stem,
                    "injection_order": row.injection_order,
                    "sample_role": row.sample_role,
                    "batch_id": row.batch_id,
                    "prep_batch_id": row.prep_batch_id,
                    "matrix_type": row.matrix_type,
                    "group": row.group,
                    "excluded": row.excluded,
                    "exclusion_reason": row.exclusion_reason,
                }
                for row in rows
            ],
        }
    )


def _sample_metadata_snapshot_id(content_sha256: str) -> str:
    return f"{_SAMPLE_METADATA_SNAPSHOT_PREFIX}{content_sha256}"


def _canonical_json_sha256(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SampleMetadataError(
            "sample identity content must be canonical JSON"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _validate_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise SampleMetadataError(
            f"{field} must be a lower-case SHA-256 digest"
        )
    return value
