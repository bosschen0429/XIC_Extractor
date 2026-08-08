from __future__ import annotations

import io
import pickle
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from time import perf_counter
from types import ModuleType
from typing import Any

from xic_extractor.process_job_execution import execute_process_jobs
from xic_extractor.xic_models import XICTrace

ProcessProgressCallback = Callable[[Any], None]


class AlignmentProcessExecutionError(RuntimeError):
    """Raised when an alignment process worker reports a failed sample job."""


def run_process_jobs(
    jobs: Iterable[Any],
    *,
    worker: Callable[[Any], Any],
    error_factory: Callable[[Any, Exception], Any],
    max_workers: int,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: ProcessProgressCallback | None = None,
    is_terminal_failure: Callable[[Any], bool] | None = None,
) -> list[Any]:
    pending_jobs = list(jobs)
    for job in pending_jobs:
        validate_process_job_payload(job)
    report = execute_process_jobs(
        pending_jobs,
        worker=worker,
        error_factory=error_factory,
        is_terminal_failure=is_terminal_failure or (lambda _result: False),
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=progress_callback,
    )
    return list(report.observed_results)


def validate_process_job_payload(job: Any) -> None:
    process_job_payload_size_bytes(job)


def process_job_payload_size_bytes(job: Any) -> int:
    _validate_payload_value(job, path="job")
    try:
        return len(pickle.dumps(job))
    except Exception as exc:
        raise TypeError(f"job payload is not pickleable: {exc}") from exc


@dataclass
class TimedProcessStats:
    sample_stem: str
    elapsed_sec: float = 0.0
    extract_xic_count: int = 0
    extract_xic_batch_count: int = 0
    raw_chromatogram_call_count: int = 0
    point_count: int = 0


class TimedProcessRawSource:
    def __init__(
        self,
        source: Any,
        *,
        stats: TimedProcessStats,
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._source = source
        self._stats = stats
        self._timer = timer

    def extract_xic(
        self,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tol: float,
    ) -> tuple[Any, Any]:
        raw_call_count_before = _raw_chromatogram_call_count(self._source)
        start = self._timer()
        try:
            rt, intensity = self._source.extract_xic(mz, rt_min, rt_max, ppm_tol)
        finally:
            self._stats.extract_xic_count += 1
            self._stats.extract_xic_batch_count += 1
            self._stats.elapsed_sec += self._timer() - start
            self._stats.raw_chromatogram_call_count += _raw_call_delta(
                raw_call_count_before,
                _raw_chromatogram_call_count(self._source),
            )
        self._stats.point_count += _trace_point_count(rt)
        return rt, intensity

    def extract_xic_many(self, requests: Iterable[Any]) -> tuple[Any, ...]:
        requests = tuple(requests)
        if hasattr(self._source, "extract_xic_many"):
            raw_call_count_before = _raw_chromatogram_call_count(self._source)
            start = self._timer()
            try:
                batch_traces = tuple(self._source.extract_xic_many(requests))
            finally:
                self._stats.elapsed_sec += self._timer() - start
            self._stats.extract_xic_count += len(requests)
            self._stats.extract_xic_batch_count += 1 if requests else 0
            self._stats.raw_chromatogram_call_count += _raw_call_delta(
                raw_call_count_before,
                _raw_chromatogram_call_count(self._source),
            )
            self._stats.point_count += sum(
                len(trace.intensity) for trace in batch_traces
            )
            return batch_traces

        fallback_traces: list[XICTrace] = []
        for request in requests:
            rt, intensity = self.extract_xic(
                request.mz,
                request.rt_min,
                request.rt_max,
                request.ppm_tol,
            )
            fallback_traces.append(XICTrace.from_arrays(rt, intensity))
        return tuple(fallback_traces)

    def scan_window_for_request(self, request: Any) -> Any:
        return self._source.scan_window_for_request(request)

    def retention_time_for_scan(self, scan_number: Any) -> Any:
        return self._source.retention_time_for_scan(scan_number)


def _trace_point_count(trace: object) -> int:
    try:
        return len(trace)  # type: ignore[arg-type]
    except TypeError:
        return 0


def _raw_chromatogram_call_count(source: object) -> int | None:
    value = getattr(source, "raw_chromatogram_call_count", None)
    if isinstance(value, int):
        return value
    return None


def _raw_call_delta(before: int | None, after: int | None) -> int:
    if before is None or after is None:
        return 0
    return max(0, after - before)


def _validate_payload_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool, bytes, Path)):
        return
    if callable(value):
        raise TypeError(f"{path} contains callable value")
    if isinstance(value, io.IOBase):
        raise TypeError(f"{path} contains file handle")
    if isinstance(value, ModuleType):
        raise TypeError(f"{path} contains module object")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _validate_payload_value(
                getattr(value, field.name),
                path=f"{path}.{field.name}",
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_payload_value(key, path=f"{path}.<key>")
            _validate_payload_value(item, path=f"{path}[{key!r}]")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _validate_payload_value(item, path=f"{path}[{index}]")
