from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment.identity_coherence.models import (
    CandidateTrace,
    IdentityCoherenceTraceRequest,
    IdentityCoherenceTraceResult,
)
from xic_extractor.alignment.process_execution import (
    AlignmentProcessExecutionError,
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
from xic_extractor.xic_models import XICRequest


@dataclass(frozen=True)
class IdentityTraceSampleJob:
    sample_index: int
    sample_stem: str
    raw_path: Path
    dll_dir: Path
    requests: tuple[tuple[int, IdentityCoherenceTraceRequest], ...]
    raw_xic_batch_size: int = 1


@dataclass(frozen=True)
class IdentityTraceTimingStats:
    sample_stem: str
    elapsed_sec: float
    extract_xic_count: int
    point_count: int
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0


@dataclass(frozen=True)
class IdentityTraceSampleResult:
    sample_index: int
    sample_stem: str
    indexed_results: tuple[tuple[int, IdentityCoherenceTraceResult], ...]
    timing_stats: tuple[IdentityTraceTimingStats, ...] = ()


@dataclass(frozen=True)
class IdentityTraceProcessOutput:
    results: tuple[IdentityCoherenceTraceResult, ...]
    timing_stats: tuple[IdentityTraceTimingStats, ...]


@dataclass(frozen=True)
class IdentityTraceWorkerError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str


IdentityTraceWorkerResult = IdentityTraceSampleResult | IdentityTraceWorkerError


def run_identity_trace_process(
    requests: Sequence[IdentityCoherenceTraceRequest],
    *,
    raw_paths: Mapping[str, Path],
    dll_dir: Path,
    max_workers: int,
    raw_xic_batch_size: int = 1,
    runner: Callable[..., list[IdentityTraceWorkerResult]],
) -> IdentityTraceProcessOutput:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if raw_xic_batch_size < 1:
        raise ValueError("raw_xic_batch_size must be >= 1")

    indexed_requests = tuple(enumerate(requests))
    grouped: dict[str, list[tuple[int, IdentityCoherenceTraceRequest]]] = {}
    for index, request in indexed_requests:
        grouped.setdefault(request.sample_id, []).append((index, request))

    jobs: list[IdentityTraceSampleJob] = []
    parent_results: list[IdentityTraceSampleResult] = []
    for sample_index, sample_stem in enumerate(sorted(grouped), start=1):
        sample_requests = tuple(grouped[sample_stem])
        raw_path = raw_paths.get(sample_stem)
        if raw_path is None:
            parent_results.append(
                IdentityTraceSampleResult(
                    sample_index=sample_index,
                    sample_stem=sample_stem,
                    indexed_results=tuple(
                        (
                            index,
                            _identity_trace_blocked_result(
                                request,
                                "missing_raw_source",
                            ),
                        )
                        for index, request in sample_requests
                    ),
                )
            )
            continue
        jobs.append(
            IdentityTraceSampleJob(
                sample_index=sample_index,
                sample_stem=sample_stem,
                raw_path=raw_path,
                dll_dir=dll_dir,
                requests=sample_requests,
                raw_xic_batch_size=raw_xic_batch_size,
            )
        )

    worker_results = runner(jobs, max_workers=max_workers) if jobs else []
    return collect_identity_trace_results(
        (*parent_results, *worker_results),
        request_count=len(indexed_requests),
    )


def run_identity_trace_jobs(
    jobs: Iterable[IdentityTraceSampleJob],
    *,
    worker: Callable[[IdentityTraceSampleJob], IdentityTraceWorkerResult],
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
) -> list[IdentityTraceWorkerResult]:
    return _run_process_jobs(
        jobs,
        worker=worker,
        error_factory=_identity_trace_worker_error,
        max_workers=max_workers,
        executor_factory=executor_factory,
        is_terminal_failure=lambda result: isinstance(
            result, IdentityTraceWorkerError
        ),
    )


def collect_identity_trace_results(
    results: Iterable[IdentityTraceWorkerResult],
    *,
    request_count: int,
) -> IdentityTraceProcessOutput:
    successes: list[IdentityTraceSampleResult] = []
    errors: list[IdentityTraceWorkerError] = []
    for result in results:
        if isinstance(result, IdentityTraceWorkerError):
            errors.append(result)
        else:
            successes.append(result)
    if errors:
        messages = "; ".join(
            f"{error.raw_name}: {error.message}"
            for error in sorted(errors, key=lambda item: item.sample_index)
        )
        raise AlignmentProcessExecutionError(messages)

    indexed: list[tuple[int, IdentityCoherenceTraceResult]] = [
        item for result in successes for item in result.indexed_results
    ]
    indexed.sort(key=lambda item: item[0])
    if [index for index, _result in indexed] != list(range(request_count)):
        raise AlignmentProcessExecutionError("identity trace results are incomplete")
    timing_stats = tuple(
        stat
        for result in sorted(successes, key=lambda item: item.sample_index)
        for stat in result.timing_stats
    )
    return IdentityTraceProcessOutput(
        results=tuple(result for _index, result in indexed),
        timing_stats=timing_stats,
    )


def identity_trace_blocked_sample_result(
    job: IdentityTraceSampleJob,
) -> IdentityTraceSampleResult:
    stats = _TimedProcessStats(sample_stem=job.sample_stem)
    indexed_results = tuple(
        (
            index,
            _identity_trace_blocked_result(
                request,
                "raw_xic_extraction_error",
                raw_xic_request_count=1,
            ),
        )
        for index, request in job.requests
    )
    return _identity_trace_sample_result(job, indexed_results, stats)


def extract_identity_trace_sample_from_raw(
    job: IdentityTraceSampleJob,
    raw: Any,
) -> IdentityTraceSampleResult:
    stats = _TimedProcessStats(sample_stem=job.sample_stem)
    timed_raw = _TimedProcessRawSource(raw, stats=stats)
    indexed_results = _extract_identity_trace_results_for_sample(
        job.requests,
        timed_raw,
        raw_xic_batch_size=job.raw_xic_batch_size,
    )
    return _identity_trace_sample_result(job, indexed_results, stats)


def _identity_trace_sample_result(
    job: IdentityTraceSampleJob,
    indexed_results: tuple[tuple[int, IdentityCoherenceTraceResult], ...],
    stats: _TimedProcessStats,
) -> IdentityTraceSampleResult:
    return IdentityTraceSampleResult(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        indexed_results=indexed_results,
        timing_stats=(
            IdentityTraceTimingStats(
                sample_stem=job.sample_stem,
                elapsed_sec=stats.elapsed_sec,
                extract_xic_count=stats.extract_xic_count,
                point_count=stats.point_count,
                extract_xic_batch_count=stats.extract_xic_batch_count,
                raw_chromatogram_call_count=stats.raw_chromatogram_call_count,
            ),
        ),
    )


def _extract_identity_trace_results_for_sample(
    indexed_requests: tuple[tuple[int, IdentityCoherenceTraceRequest], ...],
    timed_raw: _TimedProcessRawSource,
    *,
    raw_xic_batch_size: int,
) -> tuple[tuple[int, IdentityCoherenceTraceResult], ...]:
    indexed_results: list[tuple[int, IdentityCoherenceTraceResult]] = []
    for chunk in _chunked(indexed_requests, raw_xic_batch_size):
        xic_requests = tuple(
            _identity_trace_to_xic_request(request) for _, request in chunk
        )
        try:
            traces = tuple(timed_raw.extract_xic_many(xic_requests))
        except Exception:
            indexed_results.extend(
                (
                    index,
                    _identity_trace_blocked_result(
                        request,
                        "raw_xic_extraction_error",
                        raw_xic_request_count=1,
                    ),
                )
                for index, request in chunk
            )
            continue
        for (index, request), xic_trace in zip(chunk, traces, strict=True):
            try:
                trace = CandidateTrace(
                    rt_min=tuple(float(value) for value in xic_trace.rt),
                    intensity=tuple(float(value) for value in xic_trace.intensity),
                )
            except (TypeError, ValueError):
                result = _identity_trace_data_quality_result(
                    request,
                    "invalid_trace_payload",
                )
            else:
                result = IdentityCoherenceTraceResult(
                    request=request,
                    trace=trace,
                    status="pass",
                    raw_xic_request_count=1,
                    xic_point_count=len(trace.rt_min),
                )
            indexed_results.append((index, result))
    return tuple(indexed_results)


def _identity_trace_to_xic_request(
    request: IdentityCoherenceTraceRequest,
) -> XICRequest:
    return XICRequest(
        mz=request.precursor_mz,
        rt_min=request.rt_min,
        rt_max=request.rt_max,
        ppm_tol=request.ppm_tolerance,
    )


def _identity_trace_blocked_result(
    request: IdentityCoherenceTraceRequest,
    blocked_reason: str,
    *,
    raw_xic_request_count: int = 0,
) -> IdentityCoherenceTraceResult:
    return IdentityCoherenceTraceResult(
        request=request,
        trace=None,
        status="blocked_infrastructure",
        blocked_reason=blocked_reason,
        raw_xic_request_count=raw_xic_request_count,
    )


def _identity_trace_data_quality_result(
    request: IdentityCoherenceTraceRequest,
    reason: str,
) -> IdentityCoherenceTraceResult:
    return IdentityCoherenceTraceResult(
        request=request,
        trace=None,
        status="data_quality_reject",
        blocked_reason=reason,
        raw_xic_request_count=1,
    )


def _identity_trace_worker_error(
    job: IdentityTraceSampleJob,
    exc: Exception,
) -> IdentityTraceWorkerError:
    return IdentityTraceWorkerError(
        sample_index=job.sample_index,
        sample_stem=job.sample_stem,
        raw_name=job.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
    )


def _chunked(
    values: Sequence[Any],
    size: int,
) -> Iterable[tuple[Any, ...]]:
    if size < 1:
        raise ValueError("size must be >= 1")
    for index in range(0, len(values), size):
        yield tuple(values[index : index + size])
