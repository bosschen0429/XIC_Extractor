"""Reusable thread/process admission pools and one-shot process-job execution.

WorkerAdmissionPool retains its pool until close; execute_process_jobs creates
and drains a separate executor for each call.
"""

from __future__ import annotations

import os
import threading
from collections.abc import (
    Callable,
    Iterable,
    Sequence,
)
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.context import BaseContext
from typing import (
    Any,
    TypeVar,
    cast,
)

from xic_extractor.execution._serialization import (
    owner_pickle_dumps,
    owner_pickle_loads,
)

TItem = TypeVar("TItem")
TResult = TypeVar("TResult")
_PROCESS_SHARED_OBJECTS: tuple[object, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerTransportMetrics:
    shared_bootstrap_bytes: int
    task_request_bytes: int
    task_response_bytes: int
    worker_pids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _OwnerProcessResult:
    payload: bytes
    worker_pid: int


def _initialize_owner_worker(
    bootstrap_payload: bytes,
    initializer: Callable[[], None] | None,
) -> None:
    global _PROCESS_SHARED_OBJECTS
    shared = owner_pickle_loads(bootstrap_payload, ())
    if type(shared) is not tuple:
        raise TypeError("worker shared owner bootstrap must be a tuple")
    _PROCESS_SHARED_OBJECTS = shared
    if initializer is not None:
        initializer()


def _execute_owner_task(
    worker_fn: Callable[..., object],
    request_payload: bytes,
) -> _OwnerProcessResult:
    request = owner_pickle_loads(request_payload, _PROCESS_SHARED_OBJECTS)
    result = worker_fn(request)
    return _OwnerProcessResult(
        payload=owner_pickle_dumps(result, _PROCESS_SHARED_OBJECTS),
        worker_pid=os.getpid(),
    )


class WorkerAdmissionPool:
    """Manages worker concurrency budgets, admission windowing, and lifecycle."""

    def __init__(
        self,
        *,
        max_workers: int = 1,
        backend: str = "thread",
        thread_name_prefix: str = "execution-worker",
        initializer: Callable[[], None] | None = None,
        mp_context: BaseContext | None = None,
        shared_objects: tuple[object, ...] = (),
    ) -> None:
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        if backend not in {"thread", "process"}:
            raise ValueError("backend must be 'thread' or 'process'")
        if not isinstance(shared_objects, tuple):
            raise TypeError("shared_objects must be a tuple")
        self._max_workers = max_workers
        self._backend = backend
        self._thread_name_prefix = thread_name_prefix
        self._initializer = initializer
        self._mp_context = mp_context
        self._shared_objects = shared_objects
        self._executor: ThreadPoolExecutor | ProcessPoolExecutor | None = None
        self._executor_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._stopped = threading.Event()
        self._stop_lock = threading.Lock()
        self._first_error: Exception | None = None
        self._closed = False
        self._shared_bootstrap_bytes: int | None = None
        self._task_request_bytes = 0
        self._task_response_bytes = 0
        self._worker_pids: set[int] = set()

    @property
    def max_workers(self) -> int:
        return self._max_workers

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def initializer(self) -> Callable[[], None] | None:
        return self._initializer

    @property
    def transport_metrics(self) -> WorkerTransportMetrics | None:
        """Return measured owner transport bytes for shared process execution."""

        with self._metrics_lock:
            if self._shared_bootstrap_bytes is None:
                return None
            return WorkerTransportMetrics(
                shared_bootstrap_bytes=self._shared_bootstrap_bytes,
                task_request_bytes=self._task_request_bytes,
                task_response_bytes=self._task_response_bytes,
                worker_pids=tuple(sorted(self._worker_pids)),
            )

    def set_initializer(self, initializer: Callable[[], None]) -> None:
        with self._executor_lock:
            if self._executor is not None:
                raise RuntimeError("cannot set initializer after executor has started")
            self._initializer = initializer

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()

    @property
    def first_error(self) -> Exception | None:
        return self._first_error

    def stop(self, error: Exception) -> None:
        with self._stop_lock:
            if self._first_error is None:
                self._first_error = error
            self._stopped.set()

    def observe_cancellation(self, should_cancel: Callable[[], bool] | None) -> bool:
        if should_cancel is not None and should_cancel():
            self.stop(InterruptedError("execution cancelled"))
        return self._stopped.is_set()

    def _ensure_executor(
        self, capacity: int
    ) -> ThreadPoolExecutor | ProcessPoolExecutor | None:
        # Process batches may overlap on the same pool. Their request windows
        # are local; the executor owns the shared active-worker budget.
        with self._executor_lock:
            if self._closed:
                raise RuntimeError("worker admission pool is closed")
            if self._executor is not None:
                return self._executor
            effective_workers = min(self._max_workers, max(1, capacity))
            if self._backend == "process":
                # A small first batch must not permanently shrink a reused pool.
                effective_workers = self._max_workers
                if self._shared_objects:
                    bootstrap = owner_pickle_dumps(self._shared_objects, ())
                    with self._metrics_lock:
                        self._shared_bootstrap_bytes = (
                            len(bootstrap) * effective_workers
                        )
                    self._executor = ProcessPoolExecutor(
                        max_workers=effective_workers,
                        mp_context=self._mp_context or get_context("spawn"),
                        initializer=_initialize_owner_worker,
                        initargs=(bootstrap, self._initializer),
                    )
                else:
                    self._executor = ProcessPoolExecutor(
                        max_workers=effective_workers,
                        mp_context=self._mp_context or get_context("spawn"),
                    )
            elif effective_workers > 1:
                if self._initializer is not None:
                    self._initializer()
                self._executor = ThreadPoolExecutor(
                    max_workers=effective_workers,
                    thread_name_prefix=self._thread_name_prefix,
                )
            return self._executor

    def run_windowed(
        self,
        items: Sequence[TItem],
        worker_fn: Callable[..., TResult],
        *,
        task_arg_fn: Callable[[TItem], object] | None = None,
        skip_fn: Callable[[TItem], bool] | None = None,
        on_completed: Callable[[int, int, TItem, TResult], None] | None = None,
        on_error: Callable[[TItem, Exception], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        stop_on_error: bool = True,
    ) -> None:
        """Execute items using bounded windowed admission."""
        if not items:
            return
        if self.observe_cancellation(should_cancel):
            return

        executor = self._ensure_executor(len(items))
        total = len(items)
        completed = 0

        if executor is None:
            for item in items:
                if self.observe_cancellation(should_cancel):
                    break
                if skip_fn is not None and skip_fn(item):
                    completed += 1
                    if on_completed is not None:
                        on_completed(completed, total, item, None)  # type: ignore[arg-type]
                    continue
                try:
                    arg = task_arg_fn(item) if task_arg_fn is not None else item
                    res = worker_fn(arg)
                    completed += 1
                    if on_completed is not None:
                        on_completed(completed, total, item, res)
                except Exception as exc:
                    if on_error is not None:
                        on_error(item, exc)
                    if stop_on_error:
                        self.stop(exc)
                        break
            return

        effective_workers = min(self._max_workers, len(items))
        pending: dict[Future[object], TItem] = {}
        remaining = iter(items)

        def fill_window() -> None:
            nonlocal completed
            while len(pending) < effective_workers and not self.observe_cancellation(
                should_cancel
            ):
                item = next(remaining, None)
                if item is None:
                    break
                if skip_fn is not None and skip_fn(item):
                    completed += 1
                    if on_completed is not None:
                        on_completed(completed, total, item, None)  # type: ignore[arg-type]
                    continue
                arg = task_arg_fn(item) if task_arg_fn is not None else item
                if self._backend == "process" and self._shared_objects:
                    request_payload = owner_pickle_dumps(arg, self._shared_objects)
                    with self._metrics_lock:
                        self._task_request_bytes += len(request_payload)
                    submitted = cast(
                        Future[object],
                        executor.submit(
                            _execute_owner_task,
                            worker_fn,
                            request_payload,
                        ),
                    )
                else:
                    submitted = cast(Future[object], executor.submit(worker_fn, arg))
                pending[submitted] = item

        try:
            fill_window()
            while pending:
                self.observe_cancellation(should_cancel)
                done, _ = wait(
                    set(pending.keys()),
                    timeout=0.1,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    item = pending.pop(future)
                    try:
                        raw_result = future.result()
                        if self._backend == "process" and self._shared_objects:
                            if not isinstance(raw_result, _OwnerProcessResult):
                                raise TypeError(
                                    "shared process worker returned an invalid envelope"
                                )
                            with self._metrics_lock:
                                self._task_response_bytes += len(raw_result.payload)
                                self._worker_pids.add(raw_result.worker_pid)
                            raw_result = owner_pickle_loads(
                                raw_result.payload, self._shared_objects
                            )
                        res = cast(TResult, raw_result)
                        completed += 1
                        if on_completed is not None:
                            on_completed(completed, total, item, res)
                    except Exception as exc:
                        if on_error is not None:
                            on_error(item, exc)
                        if stop_on_error:
                            self.stop(exc)
                fill_window()
        except Exception as exc:
            if stop_on_error:
                self.stop(exc)
        finally:
            for future in list(pending.keys()):
                try:
                    future.result()
                except Exception:
                    pass

    def shutdown(self) -> None:
        with self._executor_lock:
            self._closed = True
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True)

    def __enter__(self) -> WorkerAdmissionPool:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.shutdown()


@dataclass(frozen=True)
class ProcessJobExecutionReport:
    """Mechanical process results without inventing domain failure state."""

    observed_results: tuple[Any, ...]
    completed_results: tuple[Any, ...]
    first_failure: Any | None
    submitted_job_ordinals: tuple[int, ...]
    completed_job_ordinals: tuple[int, ...]
    failed_job_ordinals: tuple[int, ...]
    cancelled_job_ordinals: tuple[int, ...]
    in_flight_job_ordinals: tuple[int, ...]
    unsubmitted_job_ordinals: tuple[int, ...]
    cancellation_requested: bool = False


def execute_process_jobs(
    jobs: Iterable[Any],
    *,
    worker: Callable[[Any], Any],
    error_factory: Callable[[Any, Exception], Any],
    is_terminal_failure: Callable[[Any], bool],
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[Any], None] | None = None,
    success_result_delivery: Callable[[int, Any], Any] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ProcessJobExecutionReport:
    """Stop dispatch on failure/cancellation; drain active work on cancellation."""

    pending_jobs = tuple(jobs)
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    if not pending_jobs:
        return ProcessJobExecutionReport((), (), None, (), (), (), (), (), ())
    if should_cancel is not None and should_cancel():
        return ProcessJobExecutionReport(
            (), (), None, (), (), (), (), (), tuple(range(len(pending_jobs))), True
        )

    worker_count = min(max_workers, len(pending_jobs))
    factory = executor_factory or ProcessPoolExecutor
    executor = factory(
        max_workers=worker_count,
        mp_context=get_context("spawn"),
    )
    future_to_ordinal: dict[Any, int] = {}
    results_by_ordinal: dict[int, Any] = {}
    submitted_ordinals: set[int] = set()
    completed_ordinals: set[int] = set()
    failed_ordinals: set[int] = set()
    first_failure: Any | None = None
    next_job_ordinal = 0
    cancellation_requested = False

    def _observe_cancellation() -> bool:
        nonlocal cancellation_requested
        if not cancellation_requested and should_cancel is not None:
            cancellation_requested = should_cancel()
        return cancellation_requested

    def _record_result(ordinal: int, result: Any) -> None:
        nonlocal first_failure
        results_by_ordinal[ordinal] = result
        if is_terminal_failure(result):
            failed_ordinals.add(ordinal)
            if first_failure is None:
                first_failure = result
        else:
            completed_ordinals.add(ordinal)
        if progress_callback is not None:
            progress_callback(result)

    def _record_finished_future(future: Any, ordinal: int) -> None:
        job = pending_jobs[ordinal]
        try:
            result = future.result()
        except Exception as exc:
            result = error_factory(job, exc)
        if success_result_delivery is not None and not is_terminal_failure(result):
            try:
                result = success_result_delivery(ordinal, result)
            except Exception as exc:
                result = error_factory(job, exc)
        _record_result(ordinal, result)

    def _submit_until_capacity() -> None:
        nonlocal next_job_ordinal
        nonlocal first_failure
        while (
            first_failure is None
            and not _observe_cancellation()
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
            submitted_ordinals.add(ordinal)

    cancelled_ordinals: set[int] = set()
    in_flight_ordinals: set[int] = set()
    try:
        _submit_until_capacity()
        while future_to_ordinal and first_failure is None:
            if _observe_cancellation():
                for future, ordinal in tuple(future_to_ordinal.items()):
                    if future.cancel():
                        cancelled_ordinals.add(ordinal)
                        del future_to_ordinal[future]
                if not future_to_ordinal:
                    break
            done, _not_done = wait(
                future_to_ordinal,
                timeout=0.1,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in sorted(done, key=future_to_ordinal.__getitem__):
                ordinal = future_to_ordinal.pop(future)
                if future.cancelled():
                    cancelled_ordinals.add(ordinal)
                    continue
                _record_finished_future(future, ordinal)
            if first_failure is None:
                _submit_until_capacity()

        if first_failure is not None:
            for future, ordinal in future_to_ordinal.items():
                if future.cancel():
                    cancelled_ordinals.add(ordinal)
                elif future.done():
                    _record_finished_future(future, ordinal)
                else:
                    in_flight_ordinals.add(ordinal)
    finally:
        if first_failure is not None:
            # Stop can arrive during wait(), after the loop's last observation.
            _observe_cancellation()
        _shutdown_executor(
            executor,
            wait_for_workers=first_failure is None or cancellation_requested,
            cancel_futures=first_failure is not None or cancellation_requested,
        )

    if cancellation_requested:
        # Failure still wins, but cancellation must not acknowledge shutdown
        # while a sibling can still write sample outputs or hold RAW handles.
        for future, ordinal in future_to_ordinal.items():
            if ordinal in in_flight_ordinals:
                _record_finished_future(future, ordinal)
        in_flight_ordinals.clear()

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
        submitted_job_ordinals=tuple(sorted(submitted_ordinals)),
        completed_job_ordinals=tuple(sorted(completed_ordinals)),
        failed_job_ordinals=tuple(sorted(failed_ordinals)),
        cancelled_job_ordinals=tuple(sorted(cancelled_ordinals)),
        in_flight_job_ordinals=tuple(sorted(in_flight_ordinals)),
        unsubmitted_job_ordinals=tuple(range(next_job_ordinal, len(pending_jobs))),
        cancellation_requested=cancellation_requested,
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
