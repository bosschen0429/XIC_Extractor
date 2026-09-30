from __future__ import annotations

import io
import pickle
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter
from types import ModuleType, SimpleNamespace
from typing import Any

from xic_extractor.execution.workers import execute_process_jobs
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
    success_result_delivery: Callable[[int, Any], Any] | None = None,
) -> list[Any]:
    pending_jobs = list(jobs)
    _validate_process_job_payloads(pending_jobs)
    report = execute_process_jobs(
        pending_jobs,
        worker=worker,
        error_factory=error_factory,
        is_terminal_failure=is_terminal_failure or (lambda _result: False),
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=progress_callback,
        success_result_delivery=success_result_delivery,
    )
    if report.first_failure is None and (
        report.cancelled_job_ordinals
        or report.in_flight_job_ordinals
        or report.unsubmitted_job_ordinals
    ):
        raise AlignmentProcessExecutionError(
            "process execution ended with an incomplete mechanical census: "
            f"cancelled={report.cancelled_job_ordinals}, "
            f"in_flight={report.in_flight_job_ordinals}, "
            f"unsubmitted={report.unsubmitted_job_ordinals}"
        )
    return list(report.observed_results)


def validate_process_job_payload(job: Any) -> None:
    process_job_payload_size_bytes(job)


def _validate_process_job_payloads(jobs: Iterable[Any]) -> None:
    """Validate one batch without materializing transport bytes."""

    seen_object_ids: set[int] = set()
    for index, job in enumerate(jobs):
        _validate_payload_value(
            job,
            path=f"jobs[{index}]",
            seen_object_ids=seen_object_ids,
        )


def process_job_payload_size_bytes(job: Any) -> int:
    _validate_payload_value(job, path="job", seen_object_ids=set())
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

    @property
    def acquisition_source(self) -> object | None:
        return getattr(self._source, "acquisition_source", None)

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


def _validate_payload_value(
    value: Any,
    *,
    path: str,
    seen_object_ids: set[int],
) -> None:
    if callable(value):
        raise TypeError(f"{path} contains callable value")
    if value is None or isinstance(
        value,
        (str, int, float, bool, bytes, Path),
    ):
        return
    if isinstance(value, io.IOBase):
        raise TypeError(f"{path} contains file handle")
    if isinstance(value, ModuleType):
        raise TypeError(f"{path} contains module object")
    if isinstance(value, Enum):
        enum_value = value.value
        if enum_value is None or isinstance(
            enum_value,
            (str, int, float, bool, bytes),
        ):
            return
        raise TypeError(f"{path} contains unsupported enum value")
    value_id = id(value)
    if value_id in seen_object_ids:
        return
    seen_object_ids.add(value_id)
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _validate_payload_value(
                getattr(value, field.name),
                path=f"{path}.{field.name}",
                seen_object_ids=seen_object_ids,
            )
        return
    if isinstance(value, SimpleNamespace):
        _validate_payload_value(
            vars(value),
            path=f"{path}.__dict__",
            seen_object_ids=seen_object_ids,
        )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_payload_value(
                key,
                path=f"{path}.<key>",
                seen_object_ids=seen_object_ids,
            )
            _validate_payload_value(
                item,
                path=f"{path}[{key!r}]",
                seen_object_ids=seen_object_ids,
            )
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, item in enumerate(value):
            _validate_payload_value(
                item,
                path=f"{path}[{index}]",
                seen_object_ids=seen_object_ids,
            )
        return
    raise TypeError(
        f"{path} contains unsupported payload value "
        f"{type(value).__module__}.{type(value).__qualname__}"
    )
