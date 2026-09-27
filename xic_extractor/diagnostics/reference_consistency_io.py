"""Target-list and RAW adapters for the warning-only reference diagnostic."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from xic_extractor.configuration.models import Target
from xic_extractor.configuration.targets import read_targets
from xic_extractor.diagnostics.reference_consistency import (
    ClusterObservation,
    ReferencePolicy,
    ReferenceReport,
    check_reference_consistency,
    trace_cluster_apices,
)
from xic_extractor.raw_reader import open_raw
from xic_extractor.tabular_io import file_sha256
from xic_extractor.xic_models import XICRequest


@dataclass(frozen=True)
class ReferenceGroup:
    name: str
    targets_csv: Path
    targets: tuple[Target, ...]
    raw_files: tuple[Path, ...]


def read_reference_manifest(path: Path) -> tuple[ReferenceGroup, ...]:
    """Resolve explicit group inputs relative to the manifest."""
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    groups = []
    seen = set()
    for entry in payload["groups"]:
        name = str(entry["name"]).strip()
        if not name or name in seen:
            raise ValueError("group names must be nonempty and unique")
        seen.add(name)
        target_path = (path.parent / entry["targets_csv"]).resolve()
        raw_files = tuple(
            (path.parent / p).resolve() for p in entry.get("raw_files", [])
        )
        if len(set(raw_files)) != len(raw_files):
            raise ValueError(f"{name}: duplicate RAW files")
        for raw in raw_files:
            if not raw.is_file():
                raise ValueError(f"RAW file does not exist: {raw}")
        groups.append(
            ReferenceGroup(name, target_path, read_targets(target_path), raw_files)
        )
    if not groups:
        raise ValueError("manifest must contain at least one group")
    return tuple(groups)


def reference_preflight(
    groups: Sequence[ReferenceGroup],
    dll_dir: Path | None,
    rt_min: float,
    rt_max: float,
) -> dict[str, object]:
    if (
        not math.isfinite(rt_min)
        or not math.isfinite(rt_max)
        or not 0 <= rt_min < rt_max
    ):
        raise ValueError("acquisition RT range must be finite and 0 <= start < end")
    raw_count = sum(len(group.raw_files) for group in groups)
    if raw_count and (dll_dir is None or not dll_dir.is_dir()):
        raise ValueError("--dll-dir must exist when RAW inputs are supplied")
    return {
        "evidence": "diagnostic_only",
        "raw_count": raw_count,
        "rt_min": rt_min,
        "rt_max": rt_max,
        "dll_dir": str(dll_dir) if dll_dir else None,
        "groups": [
            {
                "name": group.name,
                "targets_csv": str(group.targets_csv),
                "targets_sha256": file_sha256(group.targets_csv),
                "target_count": len(group.targets),
                "raw_files": [
                    {
                        "path": str(raw),
                        "size": raw.stat().st_size,
                        "mtime_ns": raw.stat().st_mtime_ns,
                    }
                    for raw in group.raw_files
                ],
            }
            for group in groups
        ],
        "trace_request_count": sum(
            len({(t.mz, t.ppm_tol) for t in group.targets}) * len(group.raw_files)
            for group in groups
        ),
        "acquisition": (
            "one open and one extract_xic_many per RAW; unique m/z+ppm requests"
        ),
    }


def run_reference_diagnostic(
    groups: Sequence[ReferenceGroup],
    *,
    dll_dir: Path | None,
    output_dir: Path,
    policy: ReferencePolicy = ReferencePolicy(),
    rt_min: float = 1.0,
    rt_max: float = 80.0,
    progress: Callable[[str], None] | None = None,
) -> ReferenceReport:
    provenance = reference_preflight(groups, dll_dir, rt_min, rt_max)
    observations = []
    for group in groups:
        requests = {
            (t.mz, t.ppm_tol): XICRequest(t.mz, rt_min, rt_max, t.ppm_tol)
            for t in group.targets
        }
        for raw_path in group.raw_files:
            if progress:
                progress(f"Acquiring {group.name}: {raw_path.name}")
            assert dll_dir is not None
            with open_raw(raw_path, dll_dir) as raw:
                traces = raw.extract_xic_many(tuple(requests.values()))
            clusters = {
                key: trace_cluster_apices(trace, policy)
                for key, trace in zip(requests, traces, strict=True)
            }
            for target in group.targets:
                observations.append(
                    ClusterObservation(
                        group.name,
                        str(raw_path),
                        target.label,
                        clusters[target.mz, target.ppm_tol],
                    )
                )
    report = check_reference_consistency(
        {group.name: group.targets for group in groups},
        observations,
        policy,
    )
    report["summary"]["provenance"] = provenance
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reference_consistency.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "reference_review.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(report["rows"][0]), delimiter="\t"
        )
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow(
                {
                    key: json.dumps(value) if isinstance(value, list) else value
                    for key, value in row.items()
                }
            )
    return report
