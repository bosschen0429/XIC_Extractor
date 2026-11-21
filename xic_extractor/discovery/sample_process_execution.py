"""Run Discovery samples in a process pool and reduce their results.

The pipeline owner supplies the per-sample Discovery flow; this module owns the
picklable job and result envelopes, worker scheduling, and the projection of
worker diagnostics into the parent timing recorder.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery import output_publication
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryPeak,
    EventAttachment,
)
from xic_extractor.discovery.ms1_capture import _DiscoveryMs1Measurements
from xic_extractor.discovery.runtime_measurements import (
    portable_worker_observations,
    record_discovery_ms1_measurements,
    record_unavailable_raw_open,
    reject_caller_authored_discovery_ms1_measurement,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecord, TimingRecorder
from xic_extractor.execution.workers import execute_process_jobs
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)


@dataclass(frozen=True)
class DiscoverySampleProcessJob:
    sample_index: int
    output_dir: Path
    settings: DiscoverySettings
    signal_processing_profile: SignalProcessingProfile
    dll_dir: Path
    acquisition_input: ProcessingAcquisitionInput
    instrument_diagnostics: bool = False


@dataclass(frozen=True)
class DiscoverySampleProcessResult:
    sample_index: int
    sample_stem: str
    publication: output_publication.PublishedDiscoverySample
    timing_records: tuple[TimingRecord, ...]
    measurement_observations: tuple[dict[str, object], ...]
    ms1_measurements: _DiscoveryMs1Measurements | None = None


@dataclass(frozen=True)
class DiscoverySampleProcessError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str
    timing_records: tuple[TimingRecord, ...] = ()
    measurement_observations: tuple[dict[str, object], ...] = ()
    ms1_measurements: _DiscoveryMs1Measurements | None = None


class DiscoveryProcessExecutionError(RuntimeError):
    """Terminal execution failure with completed-work census."""

    def __init__(
        self,
        first_failure: DiscoverySampleProcessError,
        *,
        completed_sample_stems: tuple[str, ...],
        failed_sample_stems: tuple[str, ...],
        cancelled_job_count: int,
        in_flight_job_count: int,
        unsubmitted_job_count: int,
    ) -> None:
        self.first_failure = first_failure
        self.completed_sample_stems = completed_sample_stems
        self.failed_sample_stems = failed_sample_stems
        self.failed_job_count = len(failed_sample_stems)
        self.cancelled_job_count = cancelled_job_count
        self.in_flight_job_count = in_flight_job_count
        self.unsubmitted_job_count = unsubmitted_job_count
        super().__init__(f"{first_failure.raw_name}: {first_failure.message}")


class DiscoveredSample(Protocol):
    """Completed per-sample Discovery facts the process worker publishes."""

    @property
    def peaks(self) -> tuple[DiscoveryPeak, ...]: ...

    @property
    def attachments(self) -> tuple[EventAttachment, ...]: ...

    @property
    def ms1_measurements(self) -> _DiscoveryMs1Measurements | None: ...

    @property
    def mass_recalibration_result(self) -> MassRecalibrationResult: ...


class SampleDiscoverer(Protocol):
    """Discovers one job's sample; the pipeline owner supplies the flow."""

    def __call__(
        self,
        job: DiscoverySampleProcessJob,
        *,
        timing_recorder: TimingRecorder,
        measurement_observer: Callable[[_DiscoveryMs1Measurements], None],
    ) -> DiscoveredSample: ...


def raise_if_discovery_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Discovery cancelled before output publication")


def run_discovery_batch_process(
    run_spec: ProcessingRunSpec,
    *,
    output_dir: Path,
    dll_dir: Path,
    timing_recorder: TimingRecorder,
    max_workers: int,
    executor_factory: Callable[..., Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    should_cancel: Callable[[], bool] | None,
    discover_sample: SampleDiscoverer,
) -> DiscoveryBatchOutputs:
    pending_jobs: list[DiscoverySampleProcessJob] = []
    for sample_index, acquisition_input in enumerate(run_spec.acquisition_inputs):
        raise_if_discovery_cancelled(should_cancel)
        pending_jobs.append(
            DiscoverySampleProcessJob(
                sample_index=sample_index,
                output_dir=output_dir,
                settings=run_spec.settings,
                signal_processing_profile=run_spec.signal_processing_profile,
                dll_dir=dll_dir,
                instrument_diagnostics=timing_recorder.enabled,
                acquisition_input=acquisition_input,
            )
        )
    jobs = tuple(pending_jobs)
    jobs_by_sample_index = {job.sample_index: job for job in jobs}

    completed = 0

    def _progress(result: object) -> None:
        nonlocal completed
        if not isinstance(result, DiscoverySampleProcessResult):
            return
        completed += 1
        if progress_callback is not None:
            progress_callback(completed, len(jobs), result.sample_stem)

    report = execute_process_jobs(
        jobs,
        worker=partial(
            run_discovery_sample_process_job, discover_sample=discover_sample
        ),
        error_factory=_discovery_process_error,
        is_terminal_failure=lambda result: isinstance(
            result, DiscoverySampleProcessError
        ),
        max_workers=max_workers,
        executor_factory=executor_factory,
        progress_callback=_progress,
        should_cancel=should_cancel,
    )
    for ordinal in report.submitted_job_ordinals:
        job = jobs[ordinal]
        _observe_pickle_payload_bytes(
            timing_recorder,
            job,
            scope=job.acquisition_input.raw_path.stem,
            source="discovery.process_job.request",
        )
    for observed in report.observed_results:
        expected_job = jobs_by_sample_index.get(observed.sample_index)
        if (
            expected_job is None
            or observed.sample_stem != expected_job.acquisition_input.raw_path.stem
        ):
            raise ValueError(
                "Discovery process result does not match a submitted sample job"
            )
        _observe_pickle_payload_bytes(
            timing_recorder,
            observed,
            scope=observed.sample_stem,
            source="discovery.process_job.result",
        )
        for record in observed.timing_records:
            timing_recorder.record(
                record.stage,
                elapsed_sec=record.elapsed_sec,
                sample_stem=record.sample_stem,
                metrics=record.metrics,
            )
        for observation in observed.measurement_observations:
            reject_caller_authored_discovery_ms1_measurement(observation)
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
        record_unavailable_raw_open(timing_recorder, observed.sample_stem)
        record_discovery_ms1_measurements(
            timing_recorder,
            observed.ms1_measurements,
            observed.sample_stem,
            unavailable_reason=(
                "process result carries no owner acquisition batch receipts"
            ),
        )
    if report.first_failure is not None:
        failure = cast(DiscoverySampleProcessError, report.first_failure)
        completed_samples = tuple(
            result.sample_stem
            for result in report.completed_results
            if isinstance(result, DiscoverySampleProcessResult)
        )
        failed_samples = tuple(
            result.sample_stem
            for result in report.observed_results
            if isinstance(result, DiscoverySampleProcessError)
        )
        raise DiscoveryProcessExecutionError(
            failure,
            completed_sample_stems=completed_samples,
            failed_sample_stems=failed_samples,
            cancelled_job_count=len(report.cancelled_job_ordinals),
            in_flight_job_count=len(report.in_flight_job_ordinals),
            unsubmitted_job_count=len(report.unsubmitted_job_ordinals),
        )

    if report.cancellation_requested:
        raise InterruptedError("Discovery cancelled before batch index publication")
    raise_if_discovery_cancelled(should_cancel)

    results = tuple(
        cast(DiscoverySampleProcessResult, result)
        for result in report.completed_results
    )
    if len(results) != len(jobs):
        raise DiscoveryProcessExecutionError(
            DiscoverySampleProcessError(
                sample_index=-1,
                sample_stem="",
                raw_name="",
                message="parallel Discovery result census is incomplete",
            ),
            completed_sample_stems=tuple(result.sample_stem for result in results),
            failed_sample_stems=(),
            cancelled_job_count=len(report.cancelled_job_ordinals),
            in_flight_job_count=len(report.in_flight_job_ordinals),
            unsubmitted_job_count=len(report.unsubmitted_job_ordinals),
        )

    return DiscoveryBatchOutputs(
        batch_index_csv=output_publication.write_batch_index(
            output_dir / "discovery_batch_index.csv",
            [result.publication.index_row for result in results],
            timing_recorder=timing_recorder,
        ),
        per_sample=tuple(result.publication.outputs for result in results),
    )


def _observe_pickle_payload_bytes(
    recorder: TimingRecorder,
    payload: object,
    *,
    scope: str,
    source: str,
) -> None:
    if not recorder.enabled:
        return
    try:
        byte_size = len(pickle.dumps(payload))
    except Exception as exc:
        recorder.observe(
            "ipc_bytes",
            value=None,
            scope=scope,
            unit="bytes",
            source=source,
            availability="failed",
            reason=f"pickle payload measurement failed: {type(exc).__name__}: {exc}",
        )
        return
    recorder.observe(
        "ipc_bytes",
        value=byte_size,
        scope=scope,
        unit="bytes",
        source=source,
        availability="partial",
        reason=(
            "pickle payload bytes exclude executor framing and transport confirmation"
        ),
    )


def run_discovery_sample_process_job(
    job: DiscoverySampleProcessJob,
    *,
    discover_sample: SampleDiscoverer,
) -> DiscoverySampleProcessResult | DiscoverySampleProcessError:
    recorder = (
        TimingRecorder("discovery-worker")
        if job.instrument_diagnostics
        else TimingRecorder.disabled("discovery-worker")
    )
    acquired_measurements: _DiscoveryMs1Measurements | None = None

    def _capture_acquired_measurements(
        measurements: _DiscoveryMs1Measurements,
    ) -> None:
        nonlocal acquired_measurements
        if acquired_measurements is not None:
            raise ValueError("Discovery MS1 measurements were emitted twice")
        acquired_measurements = measurements

    try:
        discovery_result = discover_sample(
            job,
            timing_recorder=recorder,
            measurement_observer=_capture_acquired_measurements,
        )
        raw_path = job.acquisition_input.raw_path
        publication = output_publication.publish_discovery_batch_sample(
            raw_path,
            discovery_result.peaks,
            discovery_result.attachments,
            output_dir=job.output_dir,
            acquisition_source=job.acquisition_input.acquisition_source,
            timing_recorder=recorder,
            mass_recalibration_result=discovery_result.mass_recalibration_result,
        )
        return DiscoverySampleProcessResult(
            sample_index=job.sample_index,
            sample_stem=raw_path.stem,
            publication=publication,
            timing_records=recorder.records,
            measurement_observations=portable_worker_observations(recorder),
            ms1_measurements=discovery_result.ms1_measurements,
        )
    except Exception as exc:
        return _discovery_process_error(
            job,
            exc,
            timing_records=recorder.records,
            measurement_observations=portable_worker_observations(recorder),
            ms1_measurements=acquired_measurements,
        )


def _discovery_process_error(
    job: DiscoverySampleProcessJob,
    exc: Exception,
    *,
    timing_records: tuple[TimingRecord, ...] = (),
    measurement_observations: tuple[dict[str, object], ...] = (),
    ms1_measurements: _DiscoveryMs1Measurements | None = None,
) -> DiscoverySampleProcessError:
    raw_path = job.acquisition_input.raw_path
    return DiscoverySampleProcessError(
        sample_index=job.sample_index,
        sample_stem=raw_path.stem,
        raw_name=raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
        timing_records=timing_records,
        measurement_observations=measurement_observations,
        ms1_measurements=ms1_measurements,
    )
