"""Resolve analysis membership and chronology from native RAW and SLD facts."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path, PureWindowsPath

from xic_extractor.analysis_set import (
    ANALYZED_ROLES,
    AnalysisKind,
    AnalysisSet,
    discover_raw_files,
    select_analysis_set,
)
from xic_extractor.injection_timeline import (
    InjectionHeader,
    PerturbationPolicy,
    ScheduledInjection,
    build_injection_timeline,
)
from xic_extractor.raw_reader import (
    RawSampleHeader,
    read_raw_headers,
    read_sequence_samples,
)
from xic_extractor.raw_reader_errors import RawReaderError
from xic_extractor.raw_source_binding import stat_identity
from xic_extractor.sample_metadata import (
    SampleMetadata,
    SampleRole,
    SequenceMatchReport,
    infer_sample_role,
    load_and_match_sample_metadata,
)

_ANALYSIS_HEADER_CACHE: dict[
    tuple[str, tuple[int, int, int, int, int], str],
    tuple[InjectionHeader, SampleRole, str],
] = {}


def read_analysis_set(
    raw_root: Path,
    dll_dir: Path,
    sample_metadata: Sequence[SampleMetadata] | None = None,
    *,
    analysis: AnalysisKind | None,
    sequence_source: Path | None = None,
    raw_paths: Sequence[Path] | None = None,
    include_context: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AnalysisSet:
    """Resolve who each RAW below root is and when it was injected.

    ``analysis`` selects whose membership rules apply (see ``select_analysis_set``);
    ``None`` lists every RAW without deciding membership.

    ``raw_paths`` names the RAW files when a caller already holds them (for
    example a Discovery batch); otherwise files below ``raw_root`` are discovered.
    With ``include_context=False``, explicit non-analysis annotations are resolved
    before I/O; those files remain unordered context without opening their RAW.

    The RAW headers own order (acquisition clock, older batches first) and the
    sample name. 上機資訊 (``sequence_source``) and workbench rows
    (``sample_metadata``, RAW-keyed) only annotate role, group, batch, and
    exclusion; a document may cover just some of the RAW. An unreadable RAW
    that would be analyzed stops resolution, because its order would be a
    guess; one that would not be analyzed is listed in ``unreadable``.
    """
    root = Path(raw_root)
    paths = tuple(raw_paths) if raw_paths is not None else discover_raw_files(root)
    # Skipping explicit context files needs annotations before any RAW is read.
    # Filenames cannot tell copies of one injection apart, so this match only
    # selects files to skip; final annotations match the injection timeline.
    overrides = _sequence_overrides(
        load_and_match_sample_metadata(sequence_source, [path.stem for path in paths])
        if sequence_source is not None and not include_context
        else None,
        sample_metadata,
    )
    headers: list[InjectionHeader] = []
    header_rows: dict[str, SampleMetadata] = {}
    unread: dict[str, SampleMetadata] = {}
    unread_errors: dict[str, str] = {}
    unopened: dict[str, SampleMetadata] = {}
    header_paths: list[tuple[int, Path]] = []
    for index, path in enumerate(paths):
        if should_cancel is not None and should_cancel():
            raise InterruptedError("RAW 樣本準備已停止。")
        annotation = overrides.get(path.stem.casefold())
        if (
            not include_context
            and annotation is not None
            and (
                annotation.excluded
                or (
                    annotation.sample_role is not None
                    and analysis is not None
                    and annotation.sample_role not in ANALYZED_ROLES[analysis]
                )
            )
        ):
            unopened[str(path.resolve())] = replace(
                annotation,
                sample_role=annotation.sample_role
                or infer_sample_role(None, sample_name=path.stem),
                batch_id=annotation.batch_id or root.name,
            )
            continue
        header_paths.append((index, path))
    # Each task owns and closes its vendor handle. Results retain path association;
    # acquisition clocks, not thread completion order, determine injection order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        pending = [
            (index, path, pool.submit(_analysis_header, path, dll_dir))
            for index, path in header_paths
        ]
        try:
            for index, path, future in pending:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("RAW 樣本準備已停止。")
                if progress is not None:
                    progress(index, len(paths), f"讀取 RAW 檔頭：{path.name}")
                try:
                    header, role, header_name = future.result()
                except (RawReaderError, ValueError) as exc:
                    unread_errors[str(path.resolve())] = str(exc)
                    unread[str(path.resolve())] = SampleMetadata(
                        sample_name=path.stem,
                        raw_stem=path.stem,
                        sample_role=infer_sample_role(None, sample_name=path.stem),
                        batch_id=root.name,
                    )
                    continue
                headers.append(header)
                header_rows[header.raw_path] = SampleMetadata(
                    sample_name=header_name,
                    raw_stem=path.stem,
                    sample_role=role,
                    batch_id=root.name,
                )
        finally:
            # Cancellation waits only for open handles, never all queued files.
            for _index, _path, future in pending:
                future.cancel()
    # Each report above names the file being read, so it counts the ones before.
    if progress is not None and paths:
        progress(len(paths), len(paths), "RAW 檔頭讀取完成")
    timeline = build_injection_timeline(headers, PerturbationPolicy())
    sequence = (
        load_and_match_sample_metadata(
            sequence_source,
            (
                *(injection.raw_stem for injection in timeline),
                *(Path(path).stem for path in (*unopened, *unread)),
            ),
        )
        if sequence_source is not None
        else None
    )
    return select_analysis_set(
        timeline,
        header_rows,
        tuple(_sequence_overrides(sequence, sample_metadata).values()),
        analysis=analysis,
        unreadable=unread,
        sequence=sequence,
        unopened_context=unopened,
        unreadable_errors=unread_errors,
    )


def _sequence_overrides(
    sequence: SequenceMatchReport | None,
    sample_metadata: Sequence[SampleMetadata] | None,
) -> dict[str, SampleMetadata]:
    """Matched sequence rows, then workbench rows, keyed by RAW stem."""
    matched = set(sequence.matched_raw_stems) if sequence is not None else set()
    overrides = {
        row.raw_stem.casefold(): row
        for row in (sequence.rows if sequence is not None else ())
        if row.raw_stem in matched
    }
    overrides.update({row.raw_stem.casefold(): row for row in sample_metadata or ()})
    return overrides


def _analysis_header(
    path: Path, dll_dir: Path
) -> tuple[InjectionHeader, SampleRole, str]:
    """Header facts are immutable for an unchanged file; reuse across resolutions."""
    stat = path.stat()
    key = (str(path.resolve()), stat_identity(stat), str(dll_dir))
    cached = _ANALYSIS_HEADER_CACHE.get(key)
    if cached is not None:
        return cached
    header, sample = read_raw_headers(path, dll_dir)
    facts = (header, _raw_sample_role(path, sample), str(sample.sample_name or ""))
    _ANALYSIS_HEADER_CACHE[key] = facts
    return facts


def _raw_sample_role(path: Path, sample: RawSampleHeader) -> SampleRole:
    # Header SampleName is free text; use the filename and method vocabulary
    # consistently for analysis membership and auto-derived metadata.
    return infer_sample_role(
        sample.sample_type,
        sample_name=path.stem,
        comment=" ".join(
            (
                sample.sample_name,
                PureWindowsPath(sample.instrument_method).stem,
                sample.comment,
            )
        ),
    )


def read_sld_injections(path: Path, dll_dir: Path) -> tuple[ScheduledInjection, ...]:
    """Read an optional Xcalibur sequence; it supplies no acquisition clock."""
    entries = []
    for index, (filename, directory, method) in enumerate(
        read_sequence_samples(path, dll_dir), start=1
    ):
        if filename and not filename.lower().endswith(".raw"):
            filename += ".raw"
        source = PureWindowsPath(directory) / filename
        entries.append(
            ScheduledInjection(
                sequence_path=str(path.resolve()),
                row_number=index,
                acquisition_source=str(source) if filename else "",
                instrument_method=method,
            )
        )
    return tuple(entries)
