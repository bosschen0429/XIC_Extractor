from __future__ import annotations

import csv
import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Iterable

from xic_extractor.instrument_qc.models import (
    InstrumentQCDiagnostic,
    InstrumentQCRunOutput,
    SDOLEKTrendRow,
)
from xic_extractor.tabular_io import write_tsv

RUN_COLUMNS = [
    "run_id",
    "run_fingerprint",
    "timestamp_utc",
    "instrument_id",
    "method_doc",
    "raw_dir",
    "output_dir",
    "code_version",
    "sdolek_row_count",
    "mixstds_row_count",
    "blank_row_count",
    "diagnostic_counts",
]

ROW_COLUMNS = [
    "run_id",
    "sample_name",
    "compound",
    "status",
    "apex_rt_min",
    "area",
    "base_width_min",
    "trend_confidence",
    "trend_flags",
]

BLANK_COLUMNS = ["run_id", "sample_name", "status", "tic_area", "bpc_height"]


@dataclass(frozen=True)
class LifecycleAppendResult:
    run_id: str
    run_fingerprint: str
    runs_tsv: Path
    sdolek_tsv: Path
    mixstds_tsv: Path
    blank_tsv: Path
    summary_json: Path


class DuplicateLifecycleRunError(Exception):
    """Raised when a lifecycle append would duplicate an existing run."""


def append_lifecycle_dataset(
    *,
    output: InstrumentQCRunOutput,
    raw_dir: Path,
    output_dir: Path,
    lifecycle_root: Path,
    instrument_id: str,
    method_doc: Path | None = None,
    allow_duplicate: bool = False,
    code_version: str | None = None,
    timestamp_utc: str | None = None,
) -> LifecycleAppendResult:
    timestamp = timestamp_utc or datetime.now(UTC).replace(microsecond=0).isoformat()
    version = code_version or _git_version()
    fingerprint = _run_fingerprint(
        instrument_id=instrument_id,
        method_doc=method_doc,
        raw_dir=raw_dir,
        output=output,
        code_version=version,
    )
    run_id = f"{timestamp.replace(':', '').replace('-', '')}_{fingerprint[:12]}"
    paths = _lifecycle_paths(lifecycle_root)
    existing = _existing_fingerprints(paths.runs_tsv)
    if fingerprint in existing and not allow_duplicate:
        raise DuplicateLifecycleRunError(f"DUPLICATE_LIFECYCLE_RUN: {fingerprint}")

    run_row = {
        "run_id": run_id,
        "run_fingerprint": fingerprint,
        "timestamp_utc": timestamp,
        "instrument_id": instrument_id,
        "method_doc": "" if method_doc is None else str(method_doc),
        "raw_dir": str(raw_dir),
        "output_dir": str(output_dir),
        "code_version": version,
        "sdolek_row_count": len(output.trend_rows),
        "mixstds_row_count": len(output.mixstds_rows),
        "blank_row_count": 0,
        "diagnostic_counts": _format_counts(_diagnostic_counts(output.diagnostics)),
    }
    _append_lifecycle_files(paths, run_row, output)
    return LifecycleAppendResult(
        run_id=run_id,
        run_fingerprint=fingerprint,
        runs_tsv=paths.runs_tsv,
        sdolek_tsv=paths.sdolek_tsv,
        mixstds_tsv=paths.mixstds_tsv,
        blank_tsv=paths.blank_tsv,
        summary_json=paths.summary_json,
    )


@dataclass(frozen=True)
class _LifecyclePaths:
    runs_tsv: Path
    sdolek_tsv: Path
    mixstds_tsv: Path
    blank_tsv: Path
    summary_json: Path


def _lifecycle_paths(root: Path) -> _LifecyclePaths:
    return _LifecyclePaths(
        runs_tsv=root / "instrument_qc_lifecycle_runs.tsv",
        sdolek_tsv=root / "instrument_qc_lifecycle_sdolek.tsv",
        mixstds_tsv=root / "instrument_qc_lifecycle_mixstds.tsv",
        blank_tsv=root / "instrument_qc_lifecycle_blank.tsv",
        summary_json=root / "instrument_qc_lifecycle_summary.json",
    )


def _append_lifecycle_files(
    paths: _LifecyclePaths,
    run_row: dict[str, object],
    output: InstrumentQCRunOutput,
) -> None:
    """Render the whole append before publishing; restore history on I/O failure."""
    root = paths.runs_tsv.parent
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=".lifecycle-", dir=root))
    staged = _lifecycle_paths(staging)
    final_paths = (
        paths.sdolek_tsv,
        paths.mixstds_tsv,
        paths.blank_tsv,
        paths.summary_json,
        paths.runs_tsv,
    )
    retain_staging = False
    try:
        for final in final_paths:
            if final.exists():
                shutil.copyfile(final, staging / final.name)
        run_id = str(run_row["run_id"])
        _append_rows(staged.runs_tsv, RUN_COLUMNS, [run_row])
        _append_rows(
            staged.sdolek_tsv,
            ROW_COLUMNS,
            [_trend_row(run_id, row) for row in output.trend_rows],
        )
        _append_rows(
            staged.mixstds_tsv,
            ROW_COLUMNS,
            [_trend_row(run_id, row) for row in output.mixstds_rows],
        )
        _append_rows(staged.blank_tsv, BLANK_COLUMNS, [])
        _write_summary(staged.summary_json, staged.runs_tsv)

        changed: list[tuple[Path, Path | None]] = []
        try:
            for final in final_paths:
                backup = staging / f"{final.name}.previous" if final.exists() else None
                if backup is not None:
                    final.replace(backup)
                changed.append((final, backup))
                (staging / final.name).replace(final)
        except OSError as publication_error:
            rollback_errors = []
            for final, backup in reversed(changed):
                try:
                    if backup is not None:
                        backup.replace(final)
                    else:
                        final.unlink(missing_ok=True)
                except OSError as exc:
                    rollback_errors.append(exc)
            if rollback_errors:
                retain_staging = True
                raise OSError(
                    "Lifecycle append rollback failed; "
                    f"recoverable files retained at {staging}"
                ) from ExceptionGroup(
                    "lifecycle publication and rollback failures",
                    [publication_error, *rollback_errors],
                )
            raise
    finally:
        if not retain_staging:
            shutil.rmtree(staging, ignore_errors=True)


def _run_fingerprint(
    *,
    instrument_id: str,
    method_doc: Path | None,
    raw_dir: Path,
    output: InstrumentQCRunOutput,
    code_version: str,
) -> str:
    artifact_hashes = {
        str(path): _file_hash(path)
        for path in (
            output.trend_tsv,
            output.trend_json,
            output.diagnostics_tsv,
            output.workbook,
            output.mixstds_trend_tsv,
            output.mixstds_trend_json,
            output.mixstds_diagnostics_tsv,
            output.hcd_audit_tsv,
            output.hcd_audit_json,
        )
        if path is not None
    }
    payload = {
        "instrument_id": instrument_id,
        "method_doc": "" if method_doc is None else str(method_doc),
        "raw_dir": str(raw_dir),
        "artifact_hashes": artifact_hashes,
        "code_version": code_version,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _existing_fingerprints(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return {row["run_fingerprint"] for row in reader if row.get("run_fingerprint")}


def _append_rows(
    path: Path,
    columns: list[str],
    new_rows: Iterable[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_rows = _read_existing_rows(path)
    rows: list[dict[str, object]] = [
        {column: existing_row.get(column, "") for column in columns}
        for existing_row in existing_rows
    ]
    rows.extend(
        {column: new_row.get(column, "") for column in columns} for new_row in new_rows
    )
    temp_path = path.with_suffix(path.suffix + ".tmp")
    write_tsv(temp_path, rows, columns)
    temp_path.replace(path)


def _read_existing_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _trend_row(run_id: str, row: SDOLEKTrendRow) -> dict[str, object]:
    return {
        "run_id": run_id,
        "sample_name": row.sample_name,
        "compound": row.compound,
        "status": row.status,
        "apex_rt_min": _optional_number(row.apex_rt_min),
        "area": _optional_number(row.area),
        "base_width_min": _optional_number(row.base_width_min),
        "trend_confidence": row.trend_confidence,
        "trend_flags": ";".join(row.trend_flags),
    }


def _diagnostic_counts(
    diagnostics: Iterable[InstrumentQCDiagnostic],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for diagnostic in diagnostics:
        counts[diagnostic.issue] = counts.get(diagnostic.issue, 0) + 1
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    return "; ".join(f"{key}={counts[key]}" for key in sorted(counts))


def _write_summary(path: Path, runs_tsv: Path) -> None:
    run_rows = _read_existing_rows(runs_tsv)
    payload = {
        "run_count": len(run_rows),
        "instrument_counts": _count_values(
            row.get("instrument_id", "") for row in run_rows
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _optional_number(value: float | None) -> str:
    return "" if value is None else f"{value:.6g}"


def _git_version() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unknown"
    version = completed.stdout.strip()
    return version or "unknown"
