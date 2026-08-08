from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Any


@dataclass(frozen=True)
class ProcessJobExecutionReport:
    """Mechanical process results without inventing domain failure state."""

    observed_results: tuple[Any, ...]
    completed_results: tuple[Any, ...]
    first_failure: Any | None
    completed_job_ordinals: tuple[int, ...]
    cancelled_job_ordinals: tuple[int, ...]
    in_flight_job_ordinals: tuple[int, ...]
    unsubmitted_job_ordinals: tuple[int, ...]


def execute_process_jobs(
    jobs: Iterable[Any],
    *,
    worker: Callable[[Any], Any],
    error_factory: Callable[[Any, Exception], Any],
    is_terminal_failure: Callable[[Any], bool],
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[Any], None] | None = None,
) -> ProcessJobExecutionReport:
    """Run a bounded process batch and stop submitting after owner failure."""

    pending_jobs = tuple(jobs)
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if not pending_jobs:
        return ProcessJobExecutionReport((), (), None, (), (), (), ())

    worker_count = min(max_workers, len(pending_jobs))
    factory = executor_factory or ProcessPoolExecutor
    executor = factory(
        max_workers=worker_count,
        mp_context=get_context("spawn"),
    )
    future_to_ordinal: dict[Any, int] = {}
    results_by_ordinal: dict[int, Any] = {}
    completed_ordinals: set[int] = set()
    first_failure: Any | None = None
    next_job_ordinal = 0

    def _record_result(ordinal: int, result: Any) -> None:
        nonlocal first_failure
        if is_terminal_failure(result):
            if first_failure is None:
                first_failure = result
                results_by_ordinal[ordinal] = result
                if progress_callback is not None:
                    progress_callback(result)
            return
        results_by_ordinal[ordinal] = result
        completed_ordinals.add(ordinal)
        if progress_callback is not None:
            progress_callback(result)

    def _submit_until_capacity() -> None:
        nonlocal next_job_ordinal
        nonlocal first_failure
        while (
            first_failure is None
            and len(future_to_ordinal) < worker_count
            and next_job_ordinal < len(pending_jobs)
        ):
            ordinal = next_job_ordinal
            next_job_ordinal += 1
            job = pending_jobs[ordinal]
            try:
                future = executor.submit(worker, job)
            except Exception as exc:
                _record_result(ordinal, error_factory(job, exc))
                return
            future_to_ordinal[future] = ordinal

    cancelled_ordinals: set[int] = set()
    in_flight_ordinals: set[int] = set()
    try:
        _submit_until_capacity()
        while future_to_ordinal and first_failure is None:
            done, _not_done = wait(
                future_to_ordinal,
                timeout=0.1,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in sorted(done, key=future_to_ordinal.__getitem__):
                ordinal = future_to_ordinal.pop(future)
                job = pending_jobs[ordinal]
                if future.cancelled():
                    cancelled_ordinals.add(ordinal)
                    continue
                try:
                    result = future.result()
                except Exception as exc:
                    result = error_factory(job, exc)
                _record_result(ordinal, result)
            if first_failure is None:
                _submit_until_capacity()

        if first_failure is not None:
            for future, ordinal in future_to_ordinal.items():
                if future.cancel():
                    cancelled_ordinals.add(ordinal)
                else:
                    in_flight_ordinals.add(ordinal)
    finally:
        _shutdown_executor(
            executor,
            wait_for_workers=first_failure is None,
            cancel_futures=first_failure is not None,
        )

    ordered_observed = tuple(
        results_by_ordinal[ordinal] for ordinal in sorted(results_by_ordinal)
    )
    ordered_completed = tuple(
        results_by_ordinal[ordinal] for ordinal in sorted(completed_ordinals)
    )
    return ProcessJobExecutionReport(
        observed_results=ordered_observed,
        completed_results=ordered_completed,
        first_failure=first_failure,
        completed_job_ordinals=tuple(sorted(completed_ordinals)),
        cancelled_job_ordinals=tuple(sorted(cancelled_ordinals)),
        in_flight_job_ordinals=tuple(sorted(in_flight_ordinals)),
        unsubmitted_job_ordinals=tuple(range(next_job_ordinal, len(pending_jobs))),
    )


def _shutdown_executor(
    executor: Any,
    *,
    wait_for_workers: bool,
    cancel_futures: bool,
) -> None:
    shutdown = getattr(executor, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown(wait=wait_for_workers, cancel_futures=cancel_futures)
        except TypeError:
            shutdown(wait=wait_for_workers)
        return
    exit_context = getattr(executor, "__exit__", None)
    if callable(exit_context):
        exit_context(None, None, None)
