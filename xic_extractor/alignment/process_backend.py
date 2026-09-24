from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.identity_coherence.models import (
    IdentityCoherenceTraceRequest,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceProcessOutput as IdentityTraceProcessOutput,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceSampleJob as IdentityTraceSampleJob,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceSampleResult as IdentityTraceSampleResult,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceTimingStats as IdentityTraceTimingStats,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceWorkerError as IdentityTraceWorkerError,
)
from xic_extractor.alignment.identity_trace_process import (
    IdentityTraceWorkerResult as IdentityTraceWorkerResult,
)
from xic_extractor.alignment.identity_trace_process import (
    collect_identity_trace_results as collect_identity_trace_results,
)
from xic_extractor.alignment.identity_trace_process import (
    extract_identity_trace_sample_from_raw as _extract_identity_trace_sample_from_raw,
)
from xic_extractor.alignment.identity_trace_process import (
    identity_trace_blocked_sample_result as _identity_trace_blocked_sample_result,
)
from xic_extractor.alignment.identity_trace_process import (
    run_identity_trace_jobs as _run_identity_trace_jobs,
)
from xic_extractor.alignment.identity_trace_process import (
    run_identity_trace_process as _run_identity_trace_process,
)
from xic_extractor.alignment.ms1_index_source import (
    OwnerBuildXicBackend,
    source_for_owner_build_backend,
)
from xic_extractor.alignment.ownership import (
    AlignmentCrossSampleIdentityContext,
    OwnershipBuildResult,
    build_sample_local_owners,
)
from xic_extractor.alignment.ownership_models import (
    AmbiguousOwnerRecord,
    OwnerAssignment,
    SampleLocalMS1Owner,
)
from xic_extractor.alignment.process_execution import (
    AlignmentProcessExecutionError,
    ProcessProgressCallback,
)
from xic_extractor.alignment.process_execution import (
    TimedProcessRawSource as _TimedProcessRawSource,
)
from xic_extractor.alignment.process_execution import (
    TimedProcessStats as _TimedProcessStats,
)
from xic_extractor.alignment.process_execution import (
    run_process_jobs as _run_process_jobs,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.processing_identity import AcquisitionSourceIdentity


@dataclass(frozen=True)
class OwnerBuildSampleJob:
    sample_index: int
    sample_stem: str
    raw_path: Path
    dll_dir: Path
    candidates: tuple[Any, ...]
    alignment_config: AlignmentConfig
    peak_config: ExtractionConfig
    expected_acquisition_source: AcquisitionSourceIdentity | None = None
    raw_xic_batch_size: int = 1
    owner_build_xic_backend: OwnerBuildXicBackend = "raw"
    emit_region_audit: bool = False
    region_audit_family_ids: frozenset[str] | None = None
    audit_evidence_mode: str = "full"
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None


@dataclass(frozen=True)
class OwnerBuildTimingStats:
    sample_stem: str
    elapsed_sec: float
    extract_xic_count: int
    point_count: int
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0


@dataclass(frozen=True)
class OwnerBuildSampleResult:
    sample_index: int
    sample_stem: str
    owners: tuple[SampleLocalMS1Owner, ...]
    assignments: tuple[OwnerAssignment, ...]
    ambiguous_records: tuple[AmbiguousOwnerRecord, ...]
    timing_stats: tuple[OwnerBuildTimingStats, ...] = ()


@dataclass(frozen=True)
class OwnerBuildProcessOutput:
    ownership: OwnershipBuildResult
    timing_stats: tuple[OwnerBuildTimingStats, ...]


@dataclass(frozen=True)
class OwnerBuildWorkerError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str


OwnerBuildWorkerResult = OwnerBuildSampleResult | OwnerBuildWorkerError


def _validate_expected_acquisition_sources(
    *,
    sample_order: tuple[str, ...],
    raw_paths: Mapping[str, Path],
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
) -> None:
    if expected_acquisition_sources is None:
        return
    for sample_stem in sample_order:
        if sample_stem not in raw_paths:
            raise ValueError(
                "owner-bound process execution requires RAW for every sample: "
                f"{sample_stem}"
            )
        _expected_acquisition_source(expected_acquisition_sources, sample_stem)


def _expected_acquisition_source(
    expected_acquisition_sources: Mapping[str, AcquisitionSourceIdentity] | None,
    sample_stem: str,
) -> AcquisitionSourceIdentity | None:
    if expected_acquisition_sources is None:
        return None
    source = expected_acquisition_sources.get(sample_stem)
    if not isinstance(source, AcquisitionSourceIdentity):
        raise ValueError(
            "owner-bound process execution requires an acquisition source for "
            f"{sample_stem}"
        )
    return source


def run_identity_trace_process(
    requests: Sequence[IdentityCoherenceTraceRequest],
    *,
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    runner: Callable[..., list[IdentityTraceWorkerResult]] | None = None,
) -> IdentityTraceProcessOutput:
    active_runner = runner or run_identity_trace_jobs
    return _run_identity_trace_process(
        requests,
        raw_paths=raw_paths,
        dll_dir=dll_dir,
        max_workers=max_workers,
        raw_xic_batch_size=raw_xic_batch_size,
        runner=active_runner,
    )


def run_identity_trace_jobs(
    jobs: Iterable[IdentityTraceSampleJob],
    *,
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
) -> list[IdentityTraceWorkerResult]:
    return _run_identity_trace_jobs(
        jobs,
        worker=extract_identity_trace_sample_job,
        max_workers=max_workers,
        executor_factory=executor_factory,
    )


def extract_identity_trace_sample_job(
    job: IdentityTraceSampleJob,
) -> IdentityTraceWorkerResult:
    from xic_extractor.raw_reader import open_raw

    try:
        raw_context = open_raw(job.raw_path, job.dll_dir)
        raw = raw_context.__enter__()
    except Exception:
        return _identity_trace_blocked_sample_result(job)

    exc_info: tuple[
        type[BaseException] | None,
        BaseException | None,
        TracebackType | None,
    ] = (None, None, None)
    try:
        return _extract_identity_trace_sample_from_raw(job, raw)
    except BaseException as error:
        exc_info = (type(error), error, error.__traceback__)
        raise
    finally:
        raw_context.__exit__(*exc_info)


def run_owner_build_process(
    candidates: Sequence[Any],
    *,
    sample_order: tuple[str, ...],
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    alignment_config: AlignmentConfig,
    peak_config: ExtractionConfig,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    owner_build_xic_backend: OwnerBuildXicBackend = "raw",
    expected_acquisition_sources: Mapping[
        str,
        AcquisitionSourceIdentity,
    ]
    | None = None,
    emit_region_audit: bool = False,
    region_audit_family_ids: frozenset[str] | None = None,
    audit_evidence_mode: str = "full",
    runner: Callable[..., list[OwnerBuildWorkerResult]] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
    cross_sample_identity_context: AlignmentCrossSampleIdentityContext | None = None,
) -> OwnerBuildProcessOutput:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")
    _validate_expected_acquisition_sources(
        sample_order=sample_order,
        raw_paths=raw_paths,
        expected_acquisition_sources=expected_acquisition_sources,
    )
    candidates_by_sample: dict[str, list[Any]] = {}
    for candidate in candidates:
        candidates_by_sample.setdefault(
            str(candidate.sample_stem),
            [],
        ).append(candidate)

    jobs: list[OwnerBuildSampleJob] = []
    parent_results: list[OwnerBuildSampleResult] = []
    for index, sample_stem in enumerate(sample_order, start=1):
        sample_candidates = tuple(candidates_by_sample.get(sample_stem, ()))
        if not sample_candidates:
            continue
        raw_path = raw_paths.get(sample_stem)
        if raw_path is None:
            if expected_acquisition_sources is not None:
                raise ValueError(
                    "owner-bound owner build requires RAW for every sample: "
                    f"{sample_stem}"
                )
            ownership = build_sample_local_owners(
                sample_candidates,
                raw_sources={},
                alignment_config=alignment_config,
                peak_config=peak_config,
                raw_xic_batch_size=raw_xic_batch_size,
                emit_region_audit=emit_region_audit,
                region_audit_family_ids=region_audit_family_ids,
                audit_evidence_mode=audit_evidence_mode,
                cross_sample_identity_context=cross_sample_identity_context,
            )
            result = _owner_build_sample_result(
                sample_index=index,
                sample_stem=sample_stem,
                ownership=ownership,
                timing_stats=(),
            )
            if progress_callback is not None:
                progress_callback(result)
            parent_results.append(result)
            continue
        jobs.append(
            OwnerBuildSampleJob(
                sample_index=index,
                sample_stem=sample_stem,
                raw_path=raw_path,
                dll_dir=dll_dir,
                candidates=sample_candidates,
                alignment_config=alignment_config,
                peak_config=peak_config,
                expected_acquisition_source=_expected_acquisition_source(
                    expected_acquisition_sources,
                    sample_stem,
                ),
                raw_xic_batch_size=raw_xic_batch_size,
                owner_build_xic_backend=owner_build_xic_backend,
                emit_region_audit=emit_region_audit,
                region_audit_family_ids=region_audit_family_ids,
                audit_evidence_mode=audit_evidence_mode,
                cross_sample_identity_context=cross_sample_identity_context,
            )
        )
    active_runner = runner or run_owner_build_jobs
    if not jobs:
        worker_results = []
    elif progress_callback is None:
        worker_results = active_runner(jobs, max_workers=max_workers)
    else:
        worker_results = active_runner(
            jobs,
            max_workers=max_workers,
            progress_callback=progress_callback,
        )
    return collect_owner_build_results((*parent_results, *worker_results))


def collect_owner_build_results(
    results: Iterable[OwnerBuildWorkerResult],
) -> OwnerBuildProcessOutput:
    successes: list[OwnerBuildSampleResult] = []
    errors: list[OwnerBuildWorkerError] = []
    for result in results:
        if isinstance(result, OwnerBuildWorkerError):
            errors.append(result)
        else:
            successes.append(result)
    if errors:
        messages = "; ".join(
            f"{error.raw_name}: {error.message}"
            for error in sorted(errors, key=lambda item: item.sample_index)
        )
        raise AlignmentProcessExecutionError(messages)

    unresolved_assignments = tuple(
        assignment
        for result in sorted(successes, key=lambda item: item.sample_index)
        for assignment in result.assignments
        if assignment.assignment_status == "unresolved"
    )
    sample_sorted = sorted(successes, key=lambda item: item.sample_stem)
    resolved_assignments = tuple(
        assignment
        for result in sample_sorted
        for assignment in result.assignments
        if assignment.assignment_status != "unresolved"
    )
    ownership = OwnershipBuildResult(
        owners=tuple(owner for result in sample_sorted for owner in result.owners),
        assignments=(*unresolved_assignments, *resolved_assignments),
        ambiguous_records=tuple(
            record for result in sample_sorted for record in result.ambiguous_records
        ),
    )
    timing_stats = tuple(
        stat
        for result in sorted(successes, key=lambda item: item.sample_index)
        for stat in result.timing_stats
    )
    return OwnerBuildProcessOutput(ownership=ownership, timing_stats=timing_stats)


def _owner_build_sample_result(
    *,
    sample_index: int,
    sample_stem: str,
    ownership: OwnershipBuildResult,
    timing_stats: tuple[OwnerBuildTimingStats, ...],
) -> OwnerBuildSampleResult:
    return OwnerBuildSampleResult(
        sample_index=sample_index,
        sample_stem=sample_stem,
        owners=ownership.owners,
        assignments=ownership.assignments,
        ambiguous_records=ownership.ambiguous_records,
        timing_stats=timing_stats,
    )


def run_owner_build_jobs(
    jobs: Iterable[OwnerBuildSampleJob],
    *,
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
) -> list[OwnerBuildWorkerResult]:
    return _run_process_jobs(
        jobs,
        worker=extract_owner_build_sample_job,
        error_factory=_owner_build_worker_error,
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=progress_callback,
        is_terminal_failure=lambda result: isinstance(result, OwnerBuildWorkerError),
    )


def extract_owner_build_sample_job(
    job: OwnerBuildSampleJob,
) -> OwnerBuildWorkerResult:
    from xic_extractor.raw_reader import open_raw

    try:
        with open_raw(
            job.raw_path,
            job.dll_dir,
            expected_acquisition_source=job.expected_acquisition_source,
        ) as raw:
            stats = _TimedProcessStats(sample_stem=job.sample_stem)
            owner_build_source = source_for_owner_build_backend(
                raw,
                job.owner_build_xic_backend,
            )
            timed_raw = _TimedProcessRawSource(owner_build_source, stats=stats)
            ownership = build_sample_local_owners(
                job.candidates,
                raw_sources={job.sample_stem: timed_raw},
                alignment_config=job.alignment_config,
                peak_config=job.peak_config,
                raw_xic_batch_size=job.raw_xic_batch_size,
                emit_region_audit=job.emit_region_audit,
                region_audit_family_ids=job.region_audit_family_ids,
                audit_evidence_mode=job.audit_evidence_mode,
                cross_sample_identity_context=job.cross_sample_identity_context,
            )
        return _owner_build_sample_result(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            ownership=ownership,
            timing_stats=(
                OwnerBuildTimingStats(
                    sample_stem=job.sample_stem,
                    elapsed_sec=stats.elapsed_sec,
                    extract_xic_count=stats.extract_xic_count,
                    point_count=stats.point_count,
                    extract_xic_batch_count=stats.extract_xic_batch_count,
                    raw_chromatogram_call_count=(stats.raw_chromatogram_call_count),
                ),
            ),
        )
    except Exception as exc:
        return OwnerBuildWorkerError(
            sample_index=job.sample_index,
            sample_stem=job.sample_stem,
            raw_name=job.raw_path.name,
            message=f"{type(exc).__name__}: {exc}",
        )


def _owner_build_worker_error(
    job: OwnerBuildSampleJob,
    exc: Exception,
) -> OwnerBuildWorkerError:
    return OwnerBuildWorkerError(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        raw_name=job.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
    )
