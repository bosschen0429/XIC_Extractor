"""Qt-free sample metadata adapter for the local workbench."""

from __future__ import annotations

import csv
import hashlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from xic_extractor.analysis_set import ANALYSIS_SAMPLE_ROLES, discover_raw_files
from xic_extractor.raw_sequence import read_analysis_set
from xic_extractor.sample_groups import (
    resolve_sample_group,
    sample_group_key,
)
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SAMPLE_ROLES,
    SampleMetadata,
    load_and_match_sample_metadata,
    load_sample_metadata,
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
# Mirrors the role labels of the Workbench sample editor.
ROLE_LABELS = {
    "study_sample": "研究樣本",
    "qc": "QC",
    "pooled_qc": "Pooled QC",
    "blank": "Blank",
    "calibrator": "校正品",
    "solvent": "溶劑",
    "system_suitability": "系統適用性",
    "unknown": "未指定",
}
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


def review_sample_groups(
    config_dir: Path, raw_dir: Path, source_samples: list[dict[str, Any]]
) -> dict[str, str]:
    """Label review curves by each sample's confirmed role, via RAW stems.

    This is display context, not a rewrite of historical analysis metadata.
    Reading annotations needs neither RAW enumeration nor instrument headers.
    """
    path = sample_annotations_path(config_dir, raw_dir)
    if not path.is_file():
        return {}
    roles = {
        row.raw_stem.casefold(): ROLE_LABELS[row.sample_role]
        for row in load_sample_metadata(path)
    }
    labels = {}
    for sample in source_samples:
        stem = sample.get("sample_stem", sample["sample_key"])
        if label := roles.get(stem.casefold()):
            labels[sample["sample_key"]] = label
    return labels


def read_batch_samples(
    config_dir: Path,
    raw_dir: Path,
    *,
    dll_dir: Path | None,
    sequence_source: Path | None = None,
    import_sequence: bool = False,
    read_headers: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
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
        read_headers=read_headers,
        progress=progress,
        should_cancel=should_cancel,
    )
    if importing and confirmed:
        kept = {row.raw_stem.casefold(): row for row in load_sample_metadata(saved)}
        result["samples"] = [
            _keep_confirmed(row, kept.get(row["raw_stem"].casefold()))
            for row in result["samples"]
        ]
    if importing and import_sequence:
        if should_cancel is not None and should_cancel():
            raise InterruptedError("樣本資訊匯入已停止；原先確認的分組與排除未變更。")
        _write_confirmed(saved, from_payload(result["samples"]) or ())
        confirmed = True
    result["summary"]["annotation_source"] = (
        "sequence" if importing else "confirmed" if confirmed else "raw"
    )
    result["summary"]["confirmed"] = confirmed
    return _with_overview(result)


def save_batch_samples(
    config_dir: Path, raw_dir: Path, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    path = sample_annotations_path(config_dir, raw_dir)
    metadata = from_payload(rows) or ()
    _write_confirmed(path, metadata)
    return {"path": str(path), "overview": sample_overview(metadata)}


def _write_confirmed(path: Path, metadata: Sequence[SampleMetadata]) -> None:
    """Write a folder's confirmed metadata only as the owner can read it back."""
    parse_sample_metadata(_export_rows(metadata), source=str(path))
    _write_rows(path, metadata)


def exclude_sample(
    config_dir: Path,
    raw_dir: Path,
    raw_stem: str,
    reason: str = "",
    *,
    dll_dir: Path | None,
    sequence_source: Path | None = None,
) -> dict[str, Any]:
    """Mark one injection of a reviewed run excluded for the folder's next run.

    A folder without confirmed metadata is read first with the 上機資訊 the
    reviewed run used, so the exclusion keeps that run's roles and groups.
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
    target["excluded"] = True
    if reason.strip():
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
    return result


def load_samples(
    raw_dir: Path,
    *,
    dll_dir: Path | None = None,
    sequence_source: Path | None = None,
    read_headers: bool = True,
    include_context: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Preview annotations without RAW I/O, or resolve acquired sample facts."""

    if should_cancel is not None and should_cancel():
        raise InterruptedError("樣本資訊讀取已停止。")
    if read_headers:
        if dll_dir is None:
            raise ValueError("讀取 RAW 樣本需要 RawFileReader DLL 資料夾。")
        analysis = read_analysis_set(
            Path(raw_dir),
            Path(dll_dir),
            sequence_source=Path(sequence_source) if sequence_source else None,
            include_context=include_context,
            progress=progress,
            should_cancel=should_cancel,
        )
        rows = analysis.sample_metadata
        report = analysis.sequence
        unreadable = [path.name for path in analysis.unreadable]
        unopened = [path.name for path in analysis.unopened_context]
    else:
        if progress is not None:
            progress(0, 0, "列出 RAW 檔名（不開啟 RAW）")
        paths = discover_raw_files(Path(raw_dir))
        if should_cancel is not None and should_cancel():
            raise InterruptedError("樣本資訊讀取已停止。")
        if progress is not None:
            progress(0, 0, "解析上機資訊並配對檔名")
        report = (
            load_and_match_sample_metadata(
                Path(sequence_source), [p.stem for p in paths]
            )
            if sequence_source
            else None
        )
        # Preview names are RAW aliases, not acquired sample identities. Neither
        # document order nor directory enumeration becomes injection chronology.
        matched_stems = set(report.matched_raw_stems) if report is not None else set()
        preview_rows = (
            report.rows
            if report is not None and paths
            else tuple(
                SampleMetadata(sample_name=path.stem, raw_stem=path.stem)
                for path in paths
            )
        )
        rows = tuple(
            replace(
                row,
                sample_name=row.raw_stem,
                injection_order=None,
                batch_id=row.batch_id or Path(raw_dir).name,
                sample_role=row.sample_role
                if row.raw_stem in matched_stems
                else "unknown",
            )
            for row in preview_rows
        )
        unreadable = []
        unopened = []
    if should_cancel is not None and should_cancel():
        raise InterruptedError("樣本資訊讀取已停止。")
    stems = [row.raw_stem for row in rows]
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
    summary["unreadable_raw"] = unreadable
    summary["unopened_context"] = unopened
    summary["chronology_pending"] = not read_headers
    if sequence_source:
        summary["sequence_source"] = str(Path(sequence_source))

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
