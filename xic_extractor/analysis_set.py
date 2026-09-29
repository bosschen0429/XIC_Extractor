"""Role-owned analysis set over the full injection timeline, independent of folders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Literal

from xic_extractor.injection_timeline import Injection
from xic_extractor.sample_metadata import SampleMetadata, SampleRole

ANALYSIS_SAMPLE_ROLES: frozenset[str] = frozenset({"study_sample", "qc", "pooled_qc"})

RoleSource = Literal["sample_metadata", "raw_header", "raw_file_name"]
ContextReason = Literal["role", "excluded"]


@dataclass(frozen=True)
class AnalysisSetEntry:
    raw_path: Path
    raw_stem: str
    injection_order: int | None
    sample_role: SampleRole
    role_source: RoleSource
    context_reason: ContextReason | None

    @property
    def analyzed(self) -> bool:
        return self.context_reason is None


@dataclass(frozen=True)
class AnalysisSet:
    """Every injection stays in chronology; only analyzed entries are processed."""

    entries: tuple[AnalysisSetEntry, ...]

    @property
    def analyzed(self) -> tuple[AnalysisSetEntry, ...]:
        return tuple(entry for entry in self.entries if entry.analyzed)

    @property
    def raw_paths(self) -> tuple[Path, ...]:
        return tuple(entry.raw_path for entry in self.analyzed)


def discover_raw_files(root: Path) -> tuple[Path, ...]:
    """All RAW below root; folder layout carries no role or chronology."""
    if not root.is_dir():
        raise ValueError(f"RAW root is not a directory: {root}")
    return tuple(
        sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".raw")
    )


def select_analysis_set(
    timeline: Sequence[Injection],
    inferred_roles: Mapping[str, SampleRole],
    sample_metadata: Sequence[SampleMetadata] | None = None,
    *,
    unread_paths: Sequence[str] = (),
) -> AnalysisSet:
    """Decide analysis membership by role; metadata rows override inferred roles.

    ``inferred_roles`` is keyed by RAW path as the reader recorded it. A metadata
    row with a known role replaces the inferred role; its ``excluded`` flag
    removes a sample that was injected but is not part of the analysis.
    ``unread_paths`` are RAW whose header could not be read: they keep a
    filename role and no chronology, so their file error surfaces downstream.
    """
    rows = _metadata_by_stem(sample_metadata or ())
    located: list[tuple[str, str, int | None, RoleSource]] = [
        (_local_copy_path(i), i.raw_stem, i.injection_order, "raw_header")
        for i in timeline
    ]
    located.extend(
        (path, PureWindowsPath(path).stem, None, "raw_file_name")
        for path in unread_paths
    )
    entries: list[AnalysisSetEntry] = []
    analyzed_stems: set[str] = set()
    for copy_path, raw_stem, injection_order, inferred_source in located:
        row = rows.get(raw_stem.casefold())
        if row is not None and row.sample_role != "unknown":
            role: SampleRole = row.sample_role
            source: RoleSource = "sample_metadata"
        else:
            role = inferred_roles[copy_path]
            source = inferred_source
        reason: ContextReason | None = (
            "excluded"
            if row is not None and row.excluded
            else None
            if role in ANALYSIS_SAMPLE_ROLES
            else "role"
        )
        if reason is None:
            if raw_stem.casefold() in analyzed_stems:
                raise ValueError(f"distinct injections share RAW stem: {raw_stem}")
            analyzed_stems.add(raw_stem.casefold())
        entries.append(
            AnalysisSetEntry(
                raw_path=Path(copy_path),
                raw_stem=raw_stem,
                injection_order=injection_order,
                sample_role=role,
                role_source=source,
                context_reason=reason,
            )
        )
    return AnalysisSet(tuple(entries))


def _metadata_by_stem(rows: Sequence[SampleMetadata]) -> dict[str, SampleMetadata]:
    by_stem: dict[str, SampleMetadata] = {}
    for row in rows:
        key = row.raw_stem.casefold()
        if key in by_stem:
            raise ValueError(f"duplicate sample metadata RAW stem: {row.raw_stem}")
        by_stem[key] = row
    return by_stem


def _local_copy_path(injection: Injection) -> str:
    """The shallowest copy carrying the sample's local filename."""
    named = [
        path
        for path in injection.copy_paths
        if PureWindowsPath(path).stem.casefold() == injection.raw_stem.casefold()
    ]
    return min(
        named or injection.copy_paths,
        key=lambda path: (len(PureWindowsPath(path).parts), path.casefold(), path),
    )
