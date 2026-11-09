"""Run Discovery samples in a process pool and reduce their results.

The pipeline owner supplies the per-sample Discovery flow; this module owns the
picklable job and result envelopes, worker scheduling, identity checks, and the
projection of worker diagnostics into the parent timing recorder.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionReceipt,
)
from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery import output_publication
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryEvent,
    DiscoveryPeak,
    DiscoveryPhysicalWorkItem,
)
from xic_extractor.discovery.ms1_capture import _DiscoveryMs1Measurements
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPeakPhysicalBinding,
    DiscoveryPhysicalFactRecord,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    processing_run_input_for_path,
)
from xic_extractor.discovery.runtime_measurements import (
    portable_worker_observations,
    record_discovery_ms1_measurements,
    record_raw_open_metrics,
    reject_caller_authored_discovery_ms1_measurement,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecord, TimingRecorder
from xic_extractor.execution.workers import execute_process_jobs
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)


@dataclass(frozen=True)
class DiscoverySampleProcessJob:
    sample_index: int
    raw_path: Path
    output_dir: Path
    settings: DiscoverySettings
    dll_dir: Path
    acquisition_source: AcquisitionSourceIdentity
    run_spec: ProcessingRunSpec
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
    raw_open_receipt: RawAcquisitionSessionReceipt | None = None
    signal_processing_profile: SignalProcessingProfile | None = None
    acquisition_source_id: str = ""


@dataclass(frozen=True)
class DiscoverySampleProcessError:
    sample_index: int
    sample_stem: str
    raw_name: str
    message: str
    timing_records: tuple[TimingRecord, ...] = ()
    measurement_observations: tuple[dict[str, object], ...] = ()
    ms1_measurements: _DiscoveryMs1Measurements | None = None
    raw_open_receipt: RawAcquisitionSessionReceipt | None = None
    signal_processing_profile: SignalProcessingProfile | None = None
    acquisition_source_id: str = ""


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
        self.signal_processing_profile = first_failure.signal_processing_profile
        self.acquisition_source_id = first_failure.acquisition_source_id
        super().__init__(f"{first_failure.raw_name}: {first_failure.message}")


class DiscoveredSample(Protocol):
    """Completed per-sample Discovery facts the process worker publishes."""

    @property
    def peaks(self) -> tuple[DiscoveryPeak, ...]: ...

    @property
    def events(self) -> tuple[DiscoveryEvent, ...]: ...

    @property
    def ms1_measurements(self) -> _DiscoveryMs1Measurements | None: ...

    @property
    def raw_open_receipt(self) -> RawAcquisitionSessionReceipt | None: ...

    @property
    def physical_work_manifest(self) -> tuple[DiscoveryPhysicalWorkItem, ...]: ...

    @property
    def physical_evaluations(self) -> tuple[DiscoveryPhysicalFactRecord, ...]: ...

    @property
    def physical_bindings(self) -> tuple[DiscoveryPeakPhysicalBinding, ...]: ...

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
        raw_open_receipt_observer: Callable[
            [RawAcquisitionSessionReceipt | None], None
        ],
    ) -> DiscoveredSample: ...


def raise_if_discovery_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Discovery cancelled before output publication")


def run_discovery_batch_process(
    raw_paths: tuple[Path, ...],
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    dll_dir: Path,
    timing_recorder: TimingRecorder,
    max_workers: int,
    executor_factory: Callable[..., Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    run_spec: ProcessingRunSpec,
    run_inputs: tuple[ProcessingAcquisitionInput, ...],
    should_cancel: Callable[[], bool] | None,
    discover_sample: SampleDiscoverer,
) -> DiscoveryBatchOutputs:
    pending_jobs: list[DiscoverySampleProcessJob] = []
    for sample_index, raw_path in enumerate(raw_paths):
        raise_if_discovery_cancelled(should_cancel)
        acquisition_input = run_inputs[sample_index]
        acquisition_source = acquisition_input.acquisition_source
        pending_jobs.append(
            DiscoverySampleProcessJob(
                sample_index=sample_index,
                raw_path=raw_path,
                output_dir=output_dir,
                settings=settings,
                dll_dir=dll_dir,
                acquisition_source=acquisition_source,
                instrument_diagnostics=timing_recorder.enabled,
                run_spec=run_spec,
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
            scope=job.raw_path.stem,
            source="discovery.process_job.request",
        )
    for observed in report.observed_results:
        expected_job = jobs_by_sample_index.get(observed.sample_index)
        if expected_job is None or observed.sample_stem != expected_job.raw_path.stem:
            raise ValueError(
                "Discovery process result does not match a submitted sample job"
            )
        if (
            observed.signal_processing_profile
            != expected_job.run_spec.signal_processing_profile
            or observed.acquisition_source_id
            != expected_job.acquisition_source.acquisition_source_id
        ):
            raise ValueError(
                "Discovery process result crosses profile/acquisition binding"
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
        record_raw_open_metrics(
            timing_recorder,
            expected_job.acquisition_source,
            observed.raw_open_receipt,
            observed.sample_stem,
        )
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
    raw_open_receipt: RawAcquisitionSessionReceipt | None = None
    raw_open_receipt_observed = False

    def _capture_acquired_measurements(
        measurements: _DiscoveryMs1Measurements,
    ) -> None:
        nonlocal acquired_measurements
        if acquired_measurements is not None:
            raise ValueError("Discovery MS1 measurements were emitted twice")
        acquired_measurements = measurements

    def _capture_raw_open_receipt(
        receipt: RawAcquisitionSessionReceipt | None,
    ) -> None:
        nonlocal raw_open_receipt, raw_open_receipt_observed
        if raw_open_receipt_observed:
            raise ValueError("Discovery RAW-open receipt was emitted twice")
        raw_open_receipt_observed = True
        raw_open_receipt = receipt

    try:
        _validate_discovery_process_job_binding(job)
        discovery_result = discover_sample(
            job,
            timing_recorder=recorder,
            measurement_observer=_capture_acquired_measurements,
            raw_open_receipt_observer=_capture_raw_open_receipt,
        )
        sample_stem = job.raw_path.stem
        publication = output_publication.publish_discovery_batch_sample(
            job.raw_path,
            discovery_result.peaks,
            discovery_result.events,
            output_dir=job.output_dir,
            acquisition_source=job.acquisition_source,
            timing_recorder=recorder,
            run_spec=job.run_spec,
            acquisition_input=job.acquisition_input,
            physical_evaluations=discovery_result.physical_evaluations,
            physical_bindings=discovery_result.physical_bindings,
            mass_recalibration_result=discovery_result.mass_recalibration_result,
        )
        return DiscoverySampleProcessResult(
            sample_index=job.sample_index,
            sample_stem=sample_stem,
            publication=publication,
            timing_records=recorder.records,
            measurement_observations=portable_worker_observations(recorder),
            ms1_measurements=discovery_result.ms1_measurements,
            raw_open_receipt=discovery_result.raw_open_receipt,
            signal_processing_profile=job.run_spec.signal_processing_profile,
            acquisition_source_id=job.acquisition_source.acquisition_source_id,
        )
    except Exception as exc:
        return _discovery_process_error(
            job,
            exc,
            timing_records=recorder.records,
            measurement_observations=portable_worker_observations(recorder),
            ms1_measurements=acquired_measurements,
            raw_open_receipt=raw_open_receipt,
        )


def _validate_discovery_process_job_binding(job: DiscoverySampleProcessJob) -> None:
    expected = processing_run_input_for_path(
        job.run_spec,
        job.raw_path,
        input_ordinal=job.acquisition_input.input_ordinal,
    )
    if expected != job.acquisition_input:
        raise ValueError("Discovery process job crosses run-spec input binding")
    if job.acquisition_source != expected.acquisition_source:
        raise ValueError("Discovery process job crosses acquisition identity")
    if job.settings != job.run_spec.discovery_method.settings:
        raise ValueError("Discovery process job crosses method settings")


def _discovery_process_error(
    job: DiscoverySampleProcessJob,
    exc: Exception,
    *,
    timing_records: tuple[TimingRecord, ...] = (),
    measurement_observations: tuple[dict[str, object], ...] = (),
    ms1_measurements: _DiscoveryMs1Measurements | None = None,
    raw_open_receipt: RawAcquisitionSessionReceipt | None = None,
) -> DiscoverySampleProcessError:
    return DiscoverySampleProcessError(
        sample_index=job.sample_index,
        sample_stem=job.raw_path.stem,
        raw_name=job.raw_path.name,
        message=f"{type(exc).__name__}: {exc}",
        timing_records=timing_records,
        measurement_observations=measurement_observations,
        ms1_measurements=ms1_measurements,
        raw_open_receipt=raw_open_receipt,
        signal_processing_profile=job.run_spec.signal_processing_profile,
        acquisition_source_id=job.acquisition_source.acquisition_source_id,
    )
