from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Any

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
from xic_extractor.alignment.process_execution import (
    AlignmentProcessExecutionError as AlignmentProcessExecutionError,
)


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
