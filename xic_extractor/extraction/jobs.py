from __future__ import annotations

import io
import pickle
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, fields, is_dataclass
from multiprocessing import get_context
from pathlib import Path
from types import ModuleType
from typing import Any

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import RawFileExtractionResult
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.processing_identity import AcquisitionSourceExpectation


@dataclass(frozen=True)
class RawFileJob:
    raw_index: int
    raw_path: Path
    config: ExtractionConfig
    targets: tuple[Target, ...]
    expected_acquisition_source: AcquisitionSourceExpectation | None = None


@dataclass(frozen=True)
class WorkerError:
    raw_index: int
    raw_name: str
    message: str


@dataclass(frozen=True)
class SpawnSmokeJob:
    raw_index: int
    raw_name: str


class ParallelExecutionError(RuntimeError):
    """Raised when a process worker reports a failed RAW job."""


WorkerResult = RawFileExtractionResult | WorkerError


def validate_job_payload(job: RawFileJob) -> None:
    _validate_payload_value(job, path="job")
    try:
        pickle.dumps(job)
    except Exception as exc:
        raise TypeError(f"job payload is not pickleable: {exc}") from exc


def collect_ordered_results(
    results: Iterable[WorkerResult],
) -> list[RawFileExtractionResult]:
    successes: list[RawFileExtractionResult] = []
    errors: list[WorkerError] = []
    for result in results:
        if isinstance(result, WorkerError):
            errors.append(result)
        else:
            successes.append(result)

    _raise_worker_errors(errors)
    return sorted(successes, key=lambda item: item.raw_index)


def _raise_worker_errors(errors: list[WorkerError]) -> None:
    if not errors:
        return
    messages = "; ".join(
        f"{error.raw_name}: {error.message}"
        for error in sorted(
            errors,
            key=lambda item: item.raw_index,
        )
    )
    raise ParallelExecutionError(messages)


def run_raw_file_jobs(
    jobs: Iterable[RawFileJob],
    *,
    max_workers: int,
    should_stop: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    total: int | None = None,
    executor_factory: Callable[..., Any] | None = None,
) -> list[WorkerResult]:
    return _run_jobs(
        jobs,
        worker=extract_raw_file_job,
        max_workers=max_workers,
        should_stop=should_stop,
        progress_callback=progress_callback,
        total=total,
        executor_factory=executor_factory,
    )


def _run_jobs(
    jobs: Iterable[RawFileJob],
    *,
    worker: Callable[[RawFileJob], Any],
    max_workers: int,
    should_stop: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    total: int | None = None,
    executor_factory: Callable[..., Any] | None = None,
) -> list[Any]:
    pending_jobs = list(jobs)
    for job in pending_jobs:
        validate_job_payload(job)
    context = get_context("spawn")
    factory = executor_factory or ProcessPoolExecutor
    results: list[Any] = []
    next_job_index = 0

    with factory(max_workers=max_workers, mp_context=context) as executor:
        future_to_job: dict[Any, RawFileJob] = {}

        def _submit_until_capacity() -> None:
            nonlocal next_job_index
            while len(future_to_job) < max_workers and next_job_index < len(
                pending_jobs
            ):
                if should_stop is not None and should_stop():
                    return
                job = pending_jobs[next_job_index]
                future_to_job[executor.submit(worker, job)] = job
                next_job_index += 1

        _submit_until_capacity()
        while future_to_job:
            if should_stop is not None and should_stop():
                for future in future_to_job:
                    future.cancel()
                break

            done, _not_done = wait(
                future_to_job,
                timeout=0.1,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue

            for future in done:
                job = future_to_job.pop(future)
                if future.cancelled():
                    continue
                result_count = len(results) + 1
                if progress_callback is not None:
                    progress_callback(
                        result_count,
                        total if total is not None else len(pending_jobs),
                        job.raw_path.name,
                    )
                _submit_until_capacity()
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(
                        WorkerError(
                            raw_index=job.raw_index,
                            raw_name=job.raw_path.name,
                            message=f"{type(exc).__name__}: {exc}",
                        )
                    )
    return results


def extract_raw_file_job(job: RawFileJob) -> WorkerResult:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    try:
        scoring_context_factory = build_scoring_context_factory(config=job.config)
        extraction_kwargs = {}
        if job.expected_acquisition_source is not None:
            extraction_kwargs["expected_acquisition_source"] = (
                job.expected_acquisition_source
            )
        return extract_raw_file_result(
            job.raw_index,
            job.config,
            list(job.targets),
            job.raw_path,
            scoring_context_factory=scoring_context_factory,
            **extraction_kwargs,
        )
    except Exception as exc:
        return WorkerError(
            raw_index=job.raw_index,
            raw_name=job.raw_path.name,
            message=f"{type(exc).__name__}: {exc}",
        )


def _validate_payload_value(value: Any, *, path: str) -> None:
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
