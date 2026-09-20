"""Generic worker admission pool and execution infrastructure."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
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
from typing import TypeVar, cast

from xic_extractor.execution_engine.owner_pickle import (
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

        if self._shared_bootstrap_bytes is None:
            return None
        return WorkerTransportMetrics(
            shared_bootstrap_bytes=self._shared_bootstrap_bytes,
            task_request_bytes=self._task_request_bytes,
            task_response_bytes=self._task_response_bytes,
            worker_pids=tuple(sorted(self._worker_pids)),
        )

    def set_initializer(self, initializer: Callable[[], None]) -> None:
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

    def observe_cancellation(
        self, should_cancel: Callable[[], bool] | None
    ) -> bool:
        if should_cancel is not None and should_cancel():
            self.stop(InterruptedError("execution cancelled"))
        return self._stopped.is_set()

    def _ensure_executor(
        self, capacity: int
    ) -> ThreadPoolExecutor | ProcessPoolExecutor | None:
        if self._executor is not None or self._closed:
            return self._executor
        effective_workers = min(self._max_workers, max(1, capacity))
        if self._backend == "process":
            if self._shared_objects:
                bootstrap = owner_pickle_dumps(self._shared_objects, ())
                self._shared_bootstrap_bytes = len(bootstrap) * effective_workers
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
            while (
                len(pending) < effective_workers
                and not self.observe_cancellation(should_cancel)
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
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def __enter__(self) -> WorkerAdmissionPool:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.shutdown()
