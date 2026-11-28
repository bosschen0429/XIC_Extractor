"""Discover a batch's samples in a process pool.

Each worker discovers one sample and writes its part file; the parent replays
the workers' timing into its own recorder and returns the parts to merge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, cast

from xic_extractor.discovery.cid_nl_search import DiscoverySampleJob, discover_sample
from xic_extractor.discovery.output_publication import PublishedDiscoverySample
from xic_extractor.execution.timing import TimingRecord, TimingRecorder
from xic_extractor.execution.workers import execute_process_jobs


@dataclass(frozen=True)
class DiscoverySampleProcessResult:
    publication: PublishedDiscoverySample
    timing_records: tuple[TimingRecord, ...]
    measurement_observations: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class DiscoverySampleProcessError:
    raw_name: str
    message: str
    timing_records: tuple[TimingRecord, ...] = ()
    measurement_observations: tuple[dict[str, object], ...] = ()


class DiscoveryProcessExecutionError(RuntimeError):
    """The first sample failure of a parallel Discovery run."""


def discover_samples_in_processes(
    jobs: tuple[DiscoverySampleJob, ...],
    *,
    timing_recorder: TimingRecorder,
    max_workers: int,
    executor_factory: Callable[..., Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    should_cancel: Callable[[], bool] | None,
) -> tuple[PublishedDiscoverySample, ...]:
    """Every sample's part file, or the first failure; a failure stops dispatch."""
    completed = 0

    def _progress(result: object) -> None:
        nonlocal completed
        if not isinstance(result, DiscoverySampleProcessResult):
            return
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, len(jobs), result.publication.sample_stem)

    report = execute_process_jobs(
        jobs,
        worker=partial(
            run_discovery_sample_process_job, record_timing=timing_recorder.enabled
        ),
        error_factory=_process_error,
        is_terminal_failure=lambda result: isinstance(
            result, DiscoverySampleProcessError
        ),
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=_progress,
        should_cancel=should_cancel,
    )
    for observed in report.observed_results:
        for record in observed.timing_records:
            timing_recorder.record(
                record.stage,
                elapsed_sec=record.elapsed_sec,
                sample_stem=record.sample_stem,
                metrics=record.metrics,
            )
        for observation in observed.measurement_observations:
            timing_recorder.observe(
                str(observation["metric"]),
                value=observation.get("value"),
                scope=str(observation.get("scope", "run")),
                unit=str(observation.get("unit", "")),
                aggregation=str(observation.get("aggregation", "sum")),
                source=str(observation.get("source", "")),
                availability=str(observation.get("availability", "measured")),
                reason=str(observation.get("reason", "")),
            )
    if report.first_failure is not None:
        failure = cast(DiscoverySampleProcessError, report.first_failure)
        raise DiscoveryProcessExecutionError(f"{failure.raw_name}: {failure.message}")
    if report.cancellation_requested:
        raise InterruptedError("Discovery cancelled before batch publication")
    return tuple(
        cast(DiscoverySampleProcessResult, result).publication
        for result in report.completed_results
    )


def run_discovery_sample_process_job(
    job: DiscoverySampleJob, *, record_timing: bool
) -> DiscoverySampleProcessResult | DiscoverySampleProcessError:
    recorder = (
        TimingRecorder("discovery-worker")
        if record_timing
        else TimingRecorder.disabled("discovery-worker")
    )
    try:
        publication = discover_sample(job, timing_recorder=recorder)
    except Exception as exc:
        return _process_error(job, exc, recorder)
    return DiscoverySampleProcessResult(
        publication=publication,
        timing_records=recorder.records,
        measurement_observations=tuple(recorder.measurement_observations),
    )


def _process_error(
    job: DiscoverySampleJob,
    exc: Exception,
    recorder: TimingRecorder | None = None,
) -> DiscoverySampleProcessError:
    return DiscoverySampleProcessError(
        raw_name=job.acquisition_input.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
        timing_records=() if recorder is None else recorder.records,
        measurement_observations=()
        if recorder is None
        else tuple(recorder.measurement_observations),
    )
