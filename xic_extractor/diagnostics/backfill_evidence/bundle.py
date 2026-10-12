"""Atomic opt-in publication for Backfill evidence Parquet shadow bundles."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.alignment.backfill.evidence_contract import (
    ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
)
from xic_extractor.diagnostics.backfill_evidence.parquet import (
    DEFAULT_PARQUET_COMPRESSION,
    DEFAULT_PARQUET_ROW_GROUP_SIZE,
    parquet_evidence_row_count,
    write_backfill_evidence_parquet,
    write_skipped_evidence_parquet,
)
from xic_extractor.diagnostics.backfill_evidence.tsv import (
    iter_backfill_evidence_tsv,
    iter_skipped_evidence_tsv,
)

PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION = (
    "alignment_backfill_evidence_parquet_shadow_bundle"
)
PARQUET_SHADOW_AUTHORITY = "diagnostic_only_shadow"
PARQUET_SHADOW_MANIFEST_FILENAME = "bundle_manifest.json"
BACKFILL_PARQUET_FILENAME = "backfill.parquet"
SKIPPED_PARQUET_FILENAME = "skipped.parquet"

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_MANIFEST_FIELDS = (
    "schema_version",
    "authority",
    "representation",
    "source_artifacts",
    "parquet_artifacts",
)
_REPRESENTATION_FIELDS = (
    "engine",
    "compression",
    "row_group_size",
    "logical_types",
)
_SOURCE_FIELDS = ("backfill", "skipped", "run_metadata")
_PARQUET_FIELDS = ("backfill", "skipped")
_SOURCE_BINDING_FIELDS = ("filename", "sha256", "byte_size")
_PARQUET_BINDING_FIELDS = (*_SOURCE_BINDING_FIELDS, "row_count")


@dataclass(frozen=True, slots=True)
class BackfillEvidenceParquetShadowBundle:
    root: Path
    schema_version: str
    authority: str
    backfill_parquet: Path
    skipped_parquet: Path
    manifest_path: Path
    backfill_row_count: int
    skipped_row_count: int


def write_backfill_evidence_parquet_shadow_bundle(
    output_dir: Path,
    *,
    backfill_tsv: Path,
    skipped_tsv: Path,
    run_metadata_json: Path | None = None,
    row_group_size: int = DEFAULT_PARQUET_ROW_GROUP_SIZE,
    compression: str = DEFAULT_PARQUET_COMPRESSION,
) -> Path:
    if output_dir.exists():
        raise FileExistsError(f"Parquet shadow output already exists: {output_dir}")
    source_root = _common_source_root(
        backfill_tsv,
        skipped_tsv,
        run_metadata_json,
    )
    backfill_source_binding = _file_binding(backfill_tsv)
    skipped_source_binding = _file_binding(skipped_tsv)
    run_metadata_source_binding = (
        _file_binding(run_metadata_json)
        if run_metadata_json is not None
        else None
    )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(
        f"{output_dir.name}.tmp-{uuid.uuid4().hex}"
    )
    staging_dir.mkdir()
    try:
        backfill_parquet = staging_dir / BACKFILL_PARQUET_FILENAME
        skipped_parquet = staging_dir / SKIPPED_PARQUET_FILENAME
        backfill_row_count = write_backfill_evidence_parquet(
            backfill_parquet,
            iter_backfill_evidence_tsv(backfill_tsv),
            row_group_size=row_group_size,
            compression=compression,
        )
        skipped_row_count = write_skipped_evidence_parquet(
            skipped_parquet,
            iter_skipped_evidence_tsv(skipped_tsv),
            row_group_size=row_group_size,
            compression=compression,
        )
        _validate_file_binding(
            backfill_tsv,
            backfill_source_binding,
            label="source backfill",
        )
        _validate_file_binding(
            skipped_tsv,
            skipped_source_binding,
            label="source skipped",
        )
        if run_metadata_json is not None and run_metadata_source_binding is not None:
            _validate_file_binding(
                run_metadata_json,
                run_metadata_source_binding,
                label="source run_metadata",
            )
        manifest = {
            "schema_version": PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION,
            "authority": PARQUET_SHADOW_AUTHORITY,
            "representation": {
                "engine": "parquet",
                "compression": compression,
                "row_group_size": row_group_size,
                "logical_types": "canonical_non_null_utf8_projection_plus_row_ordinal",
            },
            "source_artifacts": {
                "backfill": backfill_source_binding,
                "skipped": skipped_source_binding,
                "run_metadata": run_metadata_source_binding,
            },
            "parquet_artifacts": {
                "backfill": {
                    **_file_binding(backfill_parquet),
                    "row_count": backfill_row_count,
                },
                "skipped": {
                    **_file_binding(skipped_parquet),
                    "row_count": skipped_row_count,
                },
            },
        }
        (staging_dir / PARQUET_SHADOW_MANIFEST_FILENAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _validate_staged_bundle(staging_dir, source_root=source_root)
        staging_dir.replace(output_dir)
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise
    return output_dir


def open_backfill_evidence_parquet_shadow_bundle(
    root: Path,
    *,
    source_root: Path | None = None,
) -> BackfillEvidenceParquetShadowBundle:
    manifest_path = root / PARQUET_SHADOW_MANIFEST_FILENAME
    manifest = _load_manifest(manifest_path)
    _require_exact_fields(manifest, _MANIFEST_FIELDS, label="manifest")
    schema_version = _required_string(manifest, "schema_version")
    if schema_version != PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION:
        raise ValueError(
            f"{manifest_path}: unsupported schema_version {schema_version!r}"
        )
    authority = _required_string(manifest, "authority")
    if authority != PARQUET_SHADOW_AUTHORITY:
        raise ValueError(f"{manifest_path}: unsupported authority {authority!r}")

    representation = _required_mapping(manifest, "representation")
    _require_exact_fields(
        representation,
        _REPRESENTATION_FIELDS,
        label="representation",
    )
    if _required_string(representation, "engine") != "parquet":
        raise ValueError(f"{manifest_path}: representation engine must be 'parquet'")
    if (
        _required_string(representation, "logical_types")
        != "canonical_non_null_utf8_projection_plus_row_ordinal"
    ):
        raise ValueError(f"{manifest_path}: unsupported logical_types")
    _required_positive_int(representation, "row_group_size")
    _required_string(representation, "compression")

    sources = _required_mapping(manifest, "source_artifacts")
    _require_exact_fields(sources, _SOURCE_FIELDS, label="source_artifacts")
    source_bindings = {
        "backfill": _source_binding(sources, "backfill"),
        "skipped": _source_binding(sources, "skipped"),
    }
    run_metadata_value = sources.get("run_metadata")
    if run_metadata_value is not None:
        source_bindings["run_metadata"] = _validated_binding(
            run_metadata_value,
            fields=_SOURCE_BINDING_FIELDS,
            label="source run_metadata",
        )
    if source_root is not None:
        for label, binding in source_bindings.items():
            _validate_file_binding(
                source_root / str(binding["filename"]),
                binding,
                label=f"source {label}",
            )

    parquet_artifacts = _required_mapping(manifest, "parquet_artifacts")
    _require_exact_fields(
        parquet_artifacts,
        _PARQUET_FIELDS,
        label="parquet_artifacts",
    )
    backfill_binding = _parquet_binding(parquet_artifacts, "backfill")
    skipped_binding = _parquet_binding(parquet_artifacts, "skipped")
    if backfill_binding["filename"] != BACKFILL_PARQUET_FILENAME:
        raise ValueError(f"{manifest_path}: unexpected backfill Parquet filename")
    if skipped_binding["filename"] != SKIPPED_PARQUET_FILENAME:
        raise ValueError(f"{manifest_path}: unexpected skipped Parquet filename")
    backfill_parquet = root / BACKFILL_PARQUET_FILENAME
    skipped_parquet = root / SKIPPED_PARQUET_FILENAME
    _validate_file_binding(
        backfill_parquet,
        backfill_binding,
        label="backfill Parquet",
    )
    _validate_file_binding(
        skipped_parquet,
        skipped_binding,
        label="skipped Parquet",
    )
    backfill_row_count = parquet_evidence_row_count(
        backfill_parquet,
        columns=ALIGNMENT_BACKFILL_CELL_EVIDENCE_COLUMNS,
    )
    skipped_row_count = parquet_evidence_row_count(
        skipped_parquet,
        columns=SKIPPED_EVIDENCE_LEDGER_COLUMNS,
    )
    if backfill_row_count != backfill_binding["row_count"]:
        raise ValueError(f"{manifest_path}: backfill Parquet row_count mismatch")
    if skipped_row_count != skipped_binding["row_count"]:
        raise ValueError(f"{manifest_path}: skipped Parquet row_count mismatch")
    return BackfillEvidenceParquetShadowBundle(
        root=root,
        schema_version=schema_version,
        authority=authority,
        backfill_parquet=backfill_parquet,
        skipped_parquet=skipped_parquet,
        manifest_path=manifest_path,
        backfill_row_count=backfill_row_count,
        skipped_row_count=skipped_row_count,
    )


def _validate_staged_bundle(staging_dir: Path, *, source_root: Path) -> None:
    open_backfill_evidence_parquet_shadow_bundle(
        staging_dir,
        source_root=source_root,
    )


def _common_source_root(
    backfill_tsv: Path,
    skipped_tsv: Path,
    run_metadata_json: Path | None,
) -> Path:
    paths = [backfill_tsv, skipped_tsv]
    if run_metadata_json is not None:
        paths.append(run_metadata_json)
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    parents = {path.resolve().parent for path in paths}
    if len(parents) != 1:
        raise ValueError("shadow source artifacts must share one source directory")
    return parents.pop()


def _file_binding(path: Path) -> dict[str, str | int]:
    return {
        "filename": path.name,
        "sha256": _sha256(path),
        "byte_size": path.stat().st_size,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> Mapping[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{path}: duplicate JSON field {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: manifest must be an object")
    return value


def _source_binding(
    sources: Mapping[str, object],
    label: str,
) -> Mapping[str, str | int]:
    return _validated_binding(
        sources.get(label),
        fields=_SOURCE_BINDING_FIELDS,
        label=f"source {label}",
    )


def _parquet_binding(
    artifacts: Mapping[str, object],
    label: str,
) -> Mapping[str, str | int]:
    return _validated_binding(
        artifacts.get(label),
        fields=_PARQUET_BINDING_FIELDS,
        label=f"{label} Parquet",
    )


def _validated_binding(
    value: object,
    *,
    fields: tuple[str, ...],
    label: str,
) -> Mapping[str, str | int]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} binding must be an object")
    _require_exact_fields(value, fields, label=f"{label} binding")
    filename = _required_string(value, "filename")
    if Path(filename).name != filename or filename in {".", ".."}:
        raise ValueError(f"{label} filename must be a relative leaf name")
    sha256 = _required_string(value, "sha256")
    if _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ValueError(f"{label} sha256 must be lowercase hexadecimal")
    _required_nonnegative_int(value, "byte_size")
    if "row_count" in fields:
        _required_nonnegative_int(value, "row_count")
    return value


def _validate_file_binding(
    path: Path,
    binding: Mapping[str, str | int],
    *,
    label: str,
) -> None:
    if not path.is_file():
        raise ValueError(f"{label} file is missing: {path.name}")
    if path.stat().st_size != binding["byte_size"]:
        raise ValueError(f"{label} byte_size mismatch")
    if _sha256(path) != binding["sha256"]:
        raise ValueError(f"{label} SHA-256 mismatch")


def _require_exact_fields(
    value: Mapping[str, object],
    expected: tuple[str, ...],
    *,
    label: str,
) -> None:
    if set(value) != set(expected) or len(value) != len(expected):
        raise ValueError(
            f"expected exact {label} fields {expected!r}, got {tuple(value)!r}"
        )


def _required_mapping(
    value: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise ValueError(f"{key} must be an object")
    return result


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _required_positive_int(value: Mapping[str, object], key: str) -> int:
    result = _required_nonnegative_int(value, key)
    if result <= 0:
        raise ValueError(f"{key} must be positive")
    return result


def _required_nonnegative_int(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return result


__all__ = (
    "BACKFILL_PARQUET_FILENAME",
    "PARQUET_SHADOW_AUTHORITY",
    "PARQUET_SHADOW_BUNDLE_SCHEMA_VERSION",
    "PARQUET_SHADOW_MANIFEST_FILENAME",
    "SKIPPED_PARQUET_FILENAME",
    "BackfillEvidenceParquetShadowBundle",
    "open_backfill_evidence_parquet_shadow_bundle",
    "write_backfill_evidence_parquet_shadow_bundle",
)
