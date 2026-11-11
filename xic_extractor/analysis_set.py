"""Analysis set over the full injection timeline, independent of folders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PureWindowsPath
from typing import Literal

from xic_extractor.injection_timeline import Injection
from xic_extractor.sample_metadata import (
    SAMPLE_ROLES,
    SampleMetadata,
    SampleRole,
    SequenceMatchReport,
)

AnalysisKind = Literal["targeted", "untargeted"]
# Exclusion is the only user control over analysis; roles only label, except
# that Untargeted builds its feature matrix from samples and QC.
ANALYZED_ROLES: Mapping[AnalysisKind, frozenset[SampleRole]] = {
    "targeted": frozenset(SAMPLE_ROLES),
    "untargeted": frozenset({"sample", "qc"}),
}

RoleSource = Literal["sample_metadata", "raw_header"]
ContextReason = Literal["role", "excluded", "unreadable"]


@dataclass(frozen=True)
class AnalysisSetEntry:
    """One injection: its RAW copy and the sample facts every flow reads."""

    raw_path: Path
    copy_paths: tuple[Path, ...]
    metadata: SampleMetadata
    role_source: RoleSource
    context_reason: ContextReason | None

    @property
    def raw_stem(self) -> str:
        return self.metadata.raw_stem

    @property
    def injection_order(self) -> int:
        assert self.metadata.injection_order is not None
        return self.metadata.injection_order

    @property
    def sample_role(self) -> SampleRole:
        assert self.metadata.sample_role is not None
        return self.metadata.sample_role

    @property
    def analyzed(self) -> bool:
        return self.context_reason is None


@dataclass(frozen=True)
class AnalysisSet:
    """Acquired chronology and annotations; only analyzed entries are processed.

    Entries without injection order are RAW whose header was not read: excluded
    files left unopened, and unreadable files that would not be analyzed.
    """

    entries: tuple[AnalysisSetEntry, ...]
    sequence: SequenceMatchReport | None = None
    # Explicit non-analysis inputs whose acquisition facts were not requested.
    unopened_context: tuple[Path, ...] = ()

    @property
    def unreadable(self) -> tuple[Path, ...]:
        return tuple(
            entry.raw_path
            for entry in self.entries
            if entry.context_reason == "unreadable"
        )

    @property
    def sample_metadata(self) -> tuple[SampleMetadata, ...]:
        return tuple(entry.metadata for entry in self.entries)

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
    header_rows: Mapping[str, SampleMetadata],
    overrides: Sequence[SampleMetadata] = (),
    *,
    analysis: AnalysisKind | None,
    unreadable: Mapping[str, SampleMetadata] | None = None,
    sequence: SequenceMatchReport | None = None,
    unopened_context: Mapping[str, SampleMetadata] | None = None,
    unreadable_errors: Mapping[str, str] | None = None,
) -> AnalysisSet:
    """Give each injection its sample facts and decide analysis membership.

    ``header_rows`` holds the RAW header facts keyed by RAW path as the reader
    recorded it: header sample name, role, and batch. The timeline owns
    injection order. ``overrides`` are RAW-keyed annotations from 上機資訊 or
    the workbench: a known role replaces the header role, and group, batch,
    preparation, matrix, and exclusion fields fill in. They never rename a
    sample or change its order.

    ``analysis`` names whose membership rules apply; ``None`` lists the RAW
    without deciding membership, so nothing is analyzed and nothing blocks.

    ``unreadable`` holds RAW without a readable header, keyed by path, with a
    filename role. One that would be analyzed stops resolution because its
    order is unknown; any other stays an unordered ``unreadable`` entry.
    """
    analyzed_roles = ANALYZED_ROLES[analysis] if analysis is not None else frozenset()
    rows = _metadata_by_stem(overrides)
    blocking: list[str] = []
    skipped: list[AnalysisSetEntry] = []
    for path, fallback in (unreadable or {}).items():
        row = rows.get(fallback.raw_stem.casefold())
        fallback_role = (
            row.sample_role if row is not None else None
        ) or fallback.sample_role
        excluded = row is not None and row.excluded
        if fallback_role in analyzed_roles and not excluded:
            read_error = (unreadable_errors or {}).get(path, "")
            blocking.append(
                PureWindowsPath(path).name + (f" ({read_error})" if read_error else "")
            )
            continue
        annotation = row or fallback
        skipped.append(
            AnalysisSetEntry(
                raw_path=Path(path),
                copy_paths=(Path(path),),
                metadata=replace(
                    fallback,
                    sample_role=fallback_role,
                    batch_id=annotation.batch_id or fallback.batch_id,
                    prep_batch_id=annotation.prep_batch_id,
                    matrix_type=annotation.matrix_type,
                    group=annotation.group,
                    excluded=excluded,
                    exclusion_reason=row.exclusion_reason if row is not None else "",
                ),
                role_source="sample_metadata"
                if row is not None and row.sample_role is not None
                else "raw_header",
                context_reason="unreadable",
            )
        )
    if blocking:
        shown = "、".join(blocking[:5]) + (" 等" if len(blocking) > 5 else "")
        raise ValueError(
            f"無法讀取 {len(blocking)} 個要分析的 RAW 檔頭，無法確定注射順序：{shown}。"
            "請確認 RAW 檔案完整，且 RawFileReader DLL 資料夾正確。"
        )
    entries: list[AnalysisSetEntry] = []
    analyzed_stems: set[str] = set()
    names: set[str] = set()
    raw_aliases = {injection.raw_stem for injection in timeline} | {
        row.raw_stem for row in (unopened_context or {}).values()
    }
    for injection in timeline:
        copy_path = _local_copy_path(injection)
        raw_stem = injection.raw_stem
        header = header_rows[copy_path]
        name = _sample_name(header.sample_name, raw_stem, names, raw_aliases)
        row = rows.get(raw_stem.casefold())
        if row is not None and row.sample_role is not None:
            role: SampleRole = row.sample_role
            source: RoleSource = "sample_metadata"
        else:
            assert header.sample_role is not None
            role = header.sample_role
            source = "raw_header"
        reason: ContextReason | None = (
            "excluded"
            if row is not None and row.excluded
            else None
            if role in analyzed_roles
            else "role"
        )
        if reason is None:
            if raw_stem.casefold() in analyzed_stems:
                raise ValueError(f"distinct injections share RAW stem: {raw_stem}")
            analyzed_stems.add(raw_stem.casefold())
        annotation = row or header
        entries.append(
            AnalysisSetEntry(
                raw_path=Path(copy_path),
                copy_paths=tuple(Path(path) for path in injection.copy_paths),
                metadata=SampleMetadata(
                    sample_name=name,
                    raw_stem=raw_stem,
                    injection_order=injection.injection_order,
                    sample_role=role,
                    batch_id=annotation.batch_id or header.batch_id,
                    prep_batch_id=annotation.prep_batch_id,
                    matrix_type=annotation.matrix_type,
                    group=annotation.group or header.group,
                    excluded=row is not None and row.excluded,
                    exclusion_reason=row.exclusion_reason if row is not None else "",
                ),
                role_source=source,
                context_reason=reason,
            )
        )
    for path, row in (unopened_context or {}).items():
        entries.append(
            AnalysisSetEntry(
                raw_path=Path(path),
                copy_paths=(Path(path),),
                metadata=replace(row, sample_name=row.raw_stem, injection_order=None),
                role_source="sample_metadata",
                context_reason="excluded" if row.excluded else "role",
            )
        )
    return AnalysisSet(
        (*entries, *skipped),
        sequence,
        tuple(Path(path) for path in unopened_context or ()),
    )


def _sample_name(
    header_name: str, raw_stem: str, taken: set[str], raw_aliases: set[str]
) -> str:
    """Reserve every RAW lookup alias before choosing header display names."""
    name = header_name.strip()
    if (
        not name
        or name.lower() in {"unknown", "sample"}
        or name in taken
        or (name in raw_aliases and name != raw_stem)
    ):
        name = raw_stem
    suffix = 2
    while name in taken or (name in raw_aliases and name != raw_stem):
        name = f"{raw_stem}__{suffix}"
        suffix += 1
    taken.add(name)
    return name


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
