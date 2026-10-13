"""Qt-free sample metadata adapter for the local workbench."""

from __future__ import annotations

import csv
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.raw_reader import read_analysis_set
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SAMPLE_ROLES,
    SampleMetadata,
    SampleRole,
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
    """One row per injection, as the RAW analysis-set owner resolves it."""

    if dll_dir is None:
        raise ValueError("讀取 RAW 樣本需要 RawFileReader DLL 資料夾。")
    analysis = read_analysis_set(
        Path(raw_dir),
        Path(dll_dir),
        sequence_source=Path(sequence_source) if sequence_source else None,
    )
    rows = analysis.sample_metadata
    stems = [row.raw_stem for row in rows]
    report = analysis.sequence
    summary: dict[str, Any] = {
        "raw_count": len(stems),
        "matched_count": report.matched_count if report else 0,
        "unmatched_raw_stems": list(report.unmatched_raw_stems if report else stems),
        "unmatched_sequence_names": list(
            report.unmatched_sequence_names if report else ()
        ),
        "ambiguous_sequence_names": list(
            report.ambiguous_sequence_names if report else ()
        ),
        "conflicts": [asdict(conflict) for conflict in report.conflicts]
        if report
        else [],
    }
    summary["unreadable_raw"] = [path.name for path in analysis.unreadable]
    if sequence_source:
        summary["sequence_source"] = str(Path(sequence_source))

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


def apply_raw_chronology(
    rows: list[dict[str, Any]],
    *,
    raw_dir: Path,
    dll_dir: Path,
) -> list[dict[str, Any]]:
    """Keep the workbench annotations; order and names come from the RAW."""
    analysis = read_analysis_set(Path(raw_dir), Path(dll_dir), from_payload(rows))
    return [asdict(row) for row in analysis.sample_metadata]


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
