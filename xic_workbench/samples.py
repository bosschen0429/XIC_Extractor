"""Qt-free sample metadata adapter for the local workbench."""

from __future__ import annotations

import csv
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.analysis_set import discover_raw_files
from xic_extractor.raw_reader import derive_sample_metadata_from_raw_files
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SAMPLE_ROLES,
    SampleMetadata,
    SampleRole,
    infer_sample_role,
    load_and_match_sample_metadata,
)

ROLE_CHOICES = (
    "study_sample",
    "qc",
    "pooled_qc",
    "blank",
    "calibrator",
    "solvent",
    "system_suitability",
    "unknown",
)
_SAMPLE_FIELDS = tuple(SAMPLE_METADATA_COLUMNS[1:])


def load_samples(
    raw_dir: Path,
    *,
    dll_dir: Path | None = None,
    sequence_source: Path | None = None,
    custom_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Enumerate one row per RAW stem and optionally project sequence metadata."""

    root = Path(raw_dir)
    raw_files = _shallowest_raws(root)
    stems = tuple(path.stem for path in raw_files)
    derived_rows = (
        derive_sample_metadata_from_raw_files(
            raw_files,
            Path(dll_dir),
            batch_id=root.name,
        )
        if dll_dir is not None
        else None
    )
    if sequence_source is None:
        rows = (
            derived_rows
            if derived_rows is not None
            else tuple(
                SampleMetadata(
                    sample_name=stem,
                    raw_stem=stem,
                    sample_role=infer_sample_role("Unknown", sample_name=stem),
                    batch_id=root.name,
                )
                for stem in stems
            )
        )
        summary: dict[str, Any] = {
            "raw_count": len(stems),
            "matched_count": 0,
            "unmatched_raw_stems": list(stems),
            "unmatched_sequence_names": [],
            "ambiguous_sequence_names": [],
        }
    else:
        report = load_and_match_sample_metadata(Path(sequence_source), stems)
        rows = (
            _merge_sequence_rows(report.rows, report.matched_raw_stems, derived_rows)
            if derived_rows is not None
            else tuple(report.rows)
        )
        summary = {
            "raw_count": len(stems),
            "matched_count": report.matched_count,
            "unmatched_raw_stems": list(report.unmatched_raw_stems),
            "unmatched_sequence_names": list(report.unmatched_sequence_names),
            "ambiguous_sequence_names": list(report.ambiguous_sequence_names),
            "sequence_source": str(Path(sequence_source)),
        }

    overrides = custom_roles or {}
    invalid_roles = sorted(set(overrides.values()) - SAMPLE_ROLES)
    if invalid_roles:
        raise ValueError(f"unsupported sample role: {invalid_roles[0]}")
    rows = tuple(
        replace(
            row,
            sample_role=cast(
                SampleRole,
                overrides.get(row.raw_stem, row.sample_role),
            ),
        )
        for row in rows
    )
    summary["role_counts"] = {
        role: sum(row.sample_role == role for row in rows)
        for role in ROLE_CHOICES
        if any(row.sample_role == role for row in rows)
    }
    return {"samples": [asdict(row) for row in rows], "summary": summary}


def complete_injection_orders(
    rows: list[dict[str, Any]],
    *,
    raw_dir: Path,
    dll_dir: Path,
) -> list[dict[str, Any]]:
    """Complete missing chronology from the canonical RAW-header owner."""

    metadata = from_payload(rows) or ()
    if all(row.injection_order is not None for row in metadata):
        return [asdict(row) for row in metadata]
    raw_files = _shallowest_raws(Path(raw_dir))
    derived = derive_sample_metadata_from_raw_files(
        raw_files,
        Path(dll_dir),
        batch_id=Path(raw_dir).name,
    )
    order_by_stem = {row.raw_stem.casefold(): row.injection_order for row in derived}
    completed: list[SampleMetadata] = []
    for row in metadata:
        if row.injection_order is not None:
            completed.append(row)
            continue
        order = order_by_stem.get(row.raw_stem.casefold())
        if order is None:
            raise ValueError(
                f"RAW header did not provide injection_order for {row.raw_stem}"
            )
        completed.append(replace(row, injection_order=order))
    return [asdict(row) for row in completed]


def from_payload(value: object) -> tuple[SampleMetadata, ...] | None:
    """Validate JSON sample rows at the workbench request boundary."""

    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError("samples must be a list or null")
    rows: list[SampleMetadata] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise TypeError(f"samples[{index}] must be an object")
        unknown = sorted(set(item) - set(_SAMPLE_FIELDS))
        if unknown:
            raise ValueError(f"samples[{index}] has unsupported field: {unknown[0]}")
        missing = [key for key in ("sample_name", "raw_stem") if key not in item]
        if missing:
            raise ValueError(f"samples[{index}] is missing {missing[0]}")
        row = SampleMetadata(**item)
        _validate_row(row, index=index)
        rows.append(row)
    return tuple(rows)


def export_samples(rows: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    metadata = from_payload(rows) or ()
    destination = Path(path)
    suffix = destination.suffix.lower()
    if suffix not in {".tsv", ".csv"}:
        raise ValueError("sample metadata output must be .tsv or .csv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    delimiter = "\t" if suffix == ".tsv" else ","
    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=SAMPLE_METADATA_COLUMNS,
            delimiter=delimiter,
        )
        writer.writeheader()
        for row in metadata:
            writer.writerow(
                {
                    "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
                    **asdict(row),
                    "excluded": "true" if row.excluded else "false",
                }
            )
    return {"path": str(destination), "row_count": len(metadata)}


def _shallowest_raws(raw_dir: Path) -> tuple[Path, ...]:
    shallowest: dict[str, Path] = {}
    for path in sorted(
        discover_raw_files(raw_dir),
        key=lambda item: (len(item.parts), item.name.casefold()),
    ):
        shallowest.setdefault(path.stem.casefold(), path)
    return tuple(sorted(shallowest.values(), key=lambda item: item.name.casefold()))


def _merge_sequence_rows(
    sequence_rows: tuple[SampleMetadata, ...],
    matched_raw_stems: tuple[str, ...],
    derived_rows: tuple[SampleMetadata, ...],
) -> tuple[SampleMetadata, ...]:
    sequence_by_stem = {row.raw_stem.casefold(): row for row in sequence_rows}
    matched = {stem.casefold() for stem in matched_raw_stems}
    merged: list[SampleMetadata] = []
    for derived in derived_rows:
        key = derived.raw_stem.casefold()
        if key not in matched:
            merged.append(derived)
            continue
        source = sequence_by_stem[key]
        merged.append(
            replace(
                source,
                raw_stem=derived.raw_stem,
                injection_order=(
                    source.injection_order
                    if source.injection_order is not None
                    else derived.injection_order
                ),
            )
        )
    return tuple(merged)


def _validate_row(row: SampleMetadata, *, index: int) -> None:
    if not row.sample_name.strip() or not row.raw_stem.strip():
        raise ValueError(f"samples[{index}] requires sample_name and raw_stem")
    if row.sample_role not in SAMPLE_ROLES:
        raise ValueError(f"samples[{index}] has unsupported sample_role")
    if row.injection_order is not None and (
        isinstance(row.injection_order, bool)
        or not isinstance(row.injection_order, int)
        or row.injection_order < 1
    ):
        raise ValueError(f"samples[{index}] injection_order must be an integer >= 1")
    if not isinstance(row.excluded, bool):
        raise TypeError(f"samples[{index}] excluded must be boolean")
    for field in (
        "sample_name",
        "raw_stem",
        "batch_id",
        "prep_batch_id",
        "matrix_type",
        "group",
        "exclusion_reason",
    ):
        if not isinstance(getattr(row, field), str):
            raise TypeError(f"samples[{index}] {field} must be text")
