"""Qt-free sample metadata adapter for the local workbench."""

from __future__ import annotations

import csv
import hashlib
import os
import re
from collections.abc import Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.analysis_set import ANALYSIS_SAMPLE_ROLES
from xic_extractor.raw_reader import read_analysis_set
from xic_extractor.sample_groups import (
    resolve_sample_group,
    sample_group_key,
    suggest_sample_groups,
)
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SAMPLE_ROLES,
    SampleMetadata,
    SampleRole,
    is_sample_metadata_source,
    load_sample_metadata,
    match_sample_metadata,
    parse_sample_metadata,
)
from xic_inspector.rendering.group_colors import group_colors

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
# Kept in the shared sample-metadata format so Targeted, Untargeted and the CLI
# read confirmed annotations through the same sequence-source path.
ANNOTATIONS_DIR = "sample_metadata"


def sample_annotations_path(config_dir: Path, raw_dir: Path) -> Path:
    """One confirmed sample-metadata file per RAW folder, shared by both flows."""
    resolved = Path(raw_dir).resolve()
    digest = hashlib.sha256(os.path.normcase(str(resolved)).encode("utf-8"))
    name = re.sub(r"[^\w.-]+", "_", resolved.name).strip("._") or "raw"
    return Path(config_dir) / ANNOTATIONS_DIR / f"{name}-{digest.hexdigest()[:12]}.tsv"


def read_batch_samples(
    config_dir: Path,
    raw_dir: Path,
    *,
    dll_dir: Path | None,
    sequence_source: Path | None = None,
    import_sequence: bool = False,
    legacy_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read RAW samples with the folder's confirmed annotations.

    Once a folder has confirmed annotations they are its sample-metadata
    source. Importing a sequence list re-reads that list, keeps confirmed
    groups and exclusions it does not supply, and confirms the merged rows.
    """
    saved = sample_annotations_path(config_dir, raw_dir)
    confirmed = saved.is_file()
    importing = bool(sequence_source) and (import_sequence or not confirmed)
    result = load_samples(
        raw_dir,
        dll_dir=dll_dir,
        sequence_source=sequence_source if importing else saved if confirmed else None,
        custom_roles=None if confirmed else legacy_roles,
    )
    if importing and confirmed:
        kept = {row.raw_stem.casefold(): row for row in load_sample_metadata(saved)}
        result["samples"] = [
            _keep_confirmed(row, kept.get(row["raw_stem"].casefold()))
            for row in result["samples"]
        ]
    if importing and import_sequence:
        _write_confirmed(saved, from_payload(result["samples"]) or ())
        confirmed = True
    result["summary"]["annotation_source"] = (
        "sequence" if importing else "confirmed" if confirmed else "raw"
    )
    if not confirmed:
        result["summary"]["earlier_annotations"] = _earlier_annotations(
            saved.parent, [row["raw_stem"] for row in result["samples"]]
        )
    return _with_overview(result)


def _earlier_annotations(store: Path, raw_stems: list[str]) -> list[dict[str, Any]]:
    """Confirmed files that cover this folder's RAW, e.g. after it was moved."""
    wanted = {stem.casefold() for stem in raw_stems}
    found = []
    for path in sorted(store.glob("*.tsv")) if wanted and store.is_dir() else ():
        try:
            stems = {row.raw_stem.casefold() for row in load_sample_metadata(path)}
        except ValueError:
            continue
        matched = len(wanted & stems)
        if matched * 5 >= len(wanted) * 4:
            found.append({"path": str(path), "matched": matched, "total": len(wanted)})
    return sorted(found, key=lambda item: -item["matched"])


def save_batch_samples(
    config_dir: Path, raw_dir: Path, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    path = sample_annotations_path(config_dir, raw_dir)
    metadata = from_payload(rows) or ()
    _write_confirmed(path, metadata)
    return {"path": str(path), "overview": sample_overview(metadata)}


def _write_confirmed(path: Path, metadata: Sequence[SampleMetadata]) -> None:
    """Write a folder's confirmed metadata only as the owner can read it back."""
    unexplained = [
        row.raw_stem
        for row in metadata
        if row.excluded and not row.exclusion_reason.strip()
    ]
    if unexplained:
        raise ValueError(f"排除的樣本需要填排除原因：{'、'.join(unexplained)}")
    parse_sample_metadata(_export_rows(metadata), source=str(path))
    _write_rows(path, metadata)


_TABLE_HINTS = {
    "name": ("sample", "name", "樣品", "樣本", "檔名", "file", "raw"),
    "group": ("group", "組", "type", "類別", "condition"),
    "excluded": ("exclu", "排除"),
    "reason": ("reason", "why", "note", "remark", "原因", "備註"),
}
_EXCLUDED_WORDS = {"true", "yes", "y", "1", "x", "v", "✓", "排除", "是"}


def list_confirmed_annotations(config_dir: Path) -> list[dict[str, Any]]:
    """Confirmed folders in this workspace, newest first, for reuse."""
    store = Path(config_dir) / ANNOTATIONS_DIR
    files = sorted(
        store.glob("*.tsv") if store.is_dir() else (),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return [{"path": str(path), "name": path.stem.rsplit("-", 1)[0]} for path in files]


def preview_annotations(
    raw_stems: Sequence[str],
    *,
    path: Path | None = None,
    text: str | None = None,
    columns: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Match a collaborator table or earlier sample metadata to this batch.

    Earlier sample metadata carries its own columns. Other tables first return
    their columns; with the chosen name/group/exclusion columns they return
    which RAW each row names, using the shared sequence-name matching.
    """
    if (
        path is not None
        and path.suffix.lower() in {".csv", ".tsv", ".txt"}
        and is_sample_metadata_source(path)
    ):
        rows = load_sample_metadata(path)
        kind = "metadata"
    else:
        header, body = _read_table(path, text)
        if columns is None:
            return {
                "kind": "table",
                "columns": header,
                "preview": body[:5],
                "row_count": len(body),
                "guess": _guess_columns(header),
            }
        rows = _table_rows(body, columns)
        kind = "table"
    stems = list(raw_stems)
    passes = []
    if kind == "metadata":
        # Earlier metadata binds its own RAW exactly; the rest, such as a new
        # batch named the same way, falls back to sequence-name matching.
        exact = match_sample_metadata(rows, stems, exact_raw_stems=True)
        passes.append((exact, stems))
        bound = {stem.casefold() for stem in exact.matched_raw_stems}
        rows = tuple(row for row in rows if row.raw_stem.casefold() not in bound)
        stems = list(exact.unmatched_raw_stems)
    report = match_sample_metadata(rows, stems)
    passes.append((report, stems))
    by_stem = {
        stem: row
        for result, inputs in passes
        for stem, row in zip(inputs, result.rows, strict=True)
        if stem in set(result.matched_raw_stems)
    }
    return {
        "kind": kind,
        "matched": [
            {
                "raw_stem": stem,
                "name": by_stem[stem].sample_name,
                "group": by_stem[stem].group,
                "excluded": by_stem[stem].excluded,
                "exclusion_reason": by_stem[stem].exclusion_reason,
            }
            for stem in raw_stems
            if stem in by_stem
        ],
        "unmatched": list(report.unmatched_sequence_names),
        "ambiguous": list(report.ambiguous_sequence_names),
        "unmatched_raw": list(report.unmatched_raw_stems),
    }


def _read_table(
    path: Path | None, text: str | None
) -> tuple[list[str], list[list[str]]]:
    if text is not None and text.strip():
        delimiter = "\t" if "\t" in text else ","
        raw = list(csv.reader(text.splitlines(), delimiter=delimiter))
    elif path is not None and path.suffix.lower() in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=True)
        try:
            raw = [
                [_cell_text(value) for value in row]
                for row in book.worksheets[0].iter_rows(values_only=True)
            ]
        finally:
            book.close()
    elif path is not None and path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        delimiter = "," if path.suffix.lower() == ".csv" else "\t"
        with path.open(newline="", encoding="utf-8-sig") as handle:
            raw = list(csv.reader(handle, delimiter=delimiter))
    else:
        raise ValueError("請選擇 Excel、CSV、TSV 檔，或貼上兩欄以上的表格。")
    rows = [
        [cell.strip() for cell in row] for row in raw if any(c.strip() for c in row)
    ]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError("表格需要標題列，以及至少兩欄、一列資料。")
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = [name or f"第 {index + 1} 欄" for index, name in enumerate(rows[0])]
    return header, rows[1:]


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _guess_columns(header: Sequence[str]) -> dict[str, int | None]:
    guess: dict[str, int | None] = {}
    for field, hints in _TABLE_HINTS.items():
        guess[field] = next(
            (
                index
                for index, name in enumerate(header)
                if any(hint in name.casefold() for hint in hints)
                and index not in guess.values()
            ),
            None,
        )
    if guess["name"] is None:
        guess["name"] = 0
    return guess


def _table_rows(
    body: Sequence[Sequence[str]], columns: dict[str, int | None]
) -> tuple[SampleMetadata, ...]:
    name, group, excluded, reason = (
        columns.get(key) for key in ("name", "group", "excluded", "reason")
    )
    if name is None or (group is None and excluded is None):
        raise ValueError("請指定名稱欄，以及組別欄或排除欄。")
    return tuple(
        SampleMetadata(
            sample_name=row[name],
            raw_stem=row[name],
            group=row[group] if group is not None else "",
            excluded=excluded is not None
            and row[excluded].casefold() in _EXCLUDED_WORDS,
            exclusion_reason=row[reason] if reason is not None else "",
        )
        for row in body
        if row[name]
    )


def exclude_sample(
    config_dir: Path,
    raw_dir: Path,
    raw_stem: str,
    reason: str,
    *,
    dll_dir: Path | None,
    sequence_source: Path | None = None,
) -> dict[str, Any]:
    """Mark one injection of a reviewed run excluded for the folder's next run.

    A folder without confirmed metadata is read first, with the 上機資訊 its
    batch settings name, so the exclusion does not drop its roles or groups.
    """
    path = sample_annotations_path(config_dir, raw_dir)
    rows = (
        [asdict(row) for row in load_sample_metadata(path)]
        if path.is_file()
        else read_batch_samples(
            config_dir, raw_dir, dll_dir=dll_dir, sequence_source=sequence_source
        )["samples"]
    )
    target = next(
        (row for row in rows if row["raw_stem"].casefold() == raw_stem.casefold()),
        None,
    )
    if target is None:
        raise ValueError(f"{raw_stem} 不在這個 RAW 資料夾的樣品資訊中。")
    if not reason.strip():
        raise ValueError("請填寫排除原因。")
    target["excluded"] = True
    target["exclusion_reason"] = reason.strip()
    return {**save_batch_samples(config_dir, raw_dir, rows), "samples": rows}


def sample_overview(rows: Sequence[SampleMetadata]) -> dict[str, Any]:
    """Pre-run counts over the samples that will be analyzed."""
    groups: dict[str, dict[str, Any]] = {}
    resolved: dict[str, str] = {}
    unassigned = 0
    for row in rows:
        if row.excluded or row.sample_role not in ANALYSIS_SAMPLE_ROLES:
            continue
        group = resolve_sample_group(row)
        resolved[row.raw_stem] = group
        key = sample_group_key(group) or "other"
        entry = groups.setdefault(key, {"group": group, "count": 0})
        entry["count"] += 1
        if row.sample_role == "study_sample" and not sample_group_key(row.group):
            unassigned += 1
    # The analyzed population is the one a Targeted run writes, so the
    # Inspector colours the same groups the same way.
    colors = group_colors(resolved.values())
    for entry in groups.values():
        entry["color"] = colors[entry["group"]]
    return {
        "groups": list(groups.values()),
        "row_groups": {
            stem: {"group": group, "color": colors[group]}
            for stem, group in resolved.items()
        },
        "unassigned_study_samples": unassigned,
        "single_sample_groups": [
            entry["group"]
            for entry in groups.values()
            if entry["count"] == 1 and entry["group"] not in {"QC", "Other"}
        ],
        "excluded": [
            {"raw_stem": row.raw_stem, "reason": row.exclusion_reason}
            for row in rows
            if row.excluded
        ],
    }


def _keep_confirmed(
    row: dict[str, Any], confirmed: SampleMetadata | None
) -> dict[str, Any]:
    if confirmed is None:
        return row
    kept = dict(row)
    if not sample_group_key(row["group"]):
        kept["group"] = confirmed.group
    if not row["excluded"] and confirmed.excluded:
        kept["excluded"] = True
        kept["exclusion_reason"] = confirmed.exclusion_reason
    return kept


def _with_overview(result: dict[str, Any]) -> dict[str, Any]:
    rows = from_payload(result["samples"]) or ()
    result["overview"] = sample_overview(rows)
    names = [row.sample_name or row.raw_stem for row in rows]
    suggested = suggest_sample_groups(names)
    result["group_suggestions"] = {
        row.raw_stem: suggested[name] for row, name in zip(rows, names, strict=True)
    }
    return result


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
    _write_rows(Path(path), metadata)
    return {"path": str(path), "row_count": len(metadata)}


def _export_rows(metadata: Sequence[SampleMetadata]) -> list[dict[str, str]]:
    return [
        {
            "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
            **{
                key: "" if value is None else str(value)
                for key, value in asdict(row).items()
            },
            "excluded": "true" if row.excluded else "false",
        }
        for row in metadata
    ]


def _write_rows(destination: Path, metadata: Sequence[SampleMetadata]) -> None:
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
        writer.writerows(_export_rows(metadata))


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
