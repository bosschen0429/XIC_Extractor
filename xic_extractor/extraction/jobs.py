from __future__ import annotations

import io
import pickle
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.execution.workers import execute_process_jobs
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
    defer_analyte_selection: bool = False


@dataclass(frozen=True)
class WorkerError:
    raw_index: int
    raw_name: str
    message: str


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
    """Run every job; a failed RAW becomes a WorkerError beside the others."""
    pending_jobs = list(jobs)
    for job in pending_jobs:
        validate_job_payload(job)
    raw_names = {job.raw_index: job.raw_path.name for job in pending_jobs}
    reported = 0

    def report(result: WorkerResult) -> None:
        nonlocal reported
        reported += 1
        if progress_callback is not None:
            progress_callback(
                reported,
                total if total is not None else len(pending_jobs),
                raw_names[result.raw_index],
            )

    return list(
        execute_process_jobs(
            pending_jobs,
            worker=extract_raw_file_job,
            error_factory=lambda job, exc: WorkerError(
                raw_index=job.raw_index,
                raw_name=job.raw_path.name,
                message=f"{type(exc).__name__}: {exc}",
            ),
            is_terminal_failure=lambda _result: False,
            max_workers=max_workers,
            executor_factory=executor_factory,
            progress_callback=report,
            should_cancel=should_stop,
        ).observed_results
    )


def extract_raw_file_job(job: RawFileJob) -> WorkerResult:
    from xic_extractor.extraction.target_extraction import extract_raw_file_result

    try:
        scoring_context_factory = build_scoring_context_factory(config=job.config)
        extraction_kwargs: dict[str, Any] = {}
        if job.defer_analyte_selection:
            extraction_kwargs["defer_analyte_selection"] = True
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
