from __future__ import annotations

import csv
import pickle
from collections.abc import Callable
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.acquisition_contract import (
    BatchXICAdapter,
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    mint_raw_acquisition_session_capability,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
    resolve_signal_processing_profile,
)
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundLibrary,
    apply_blank_qualification_to_candidates,
    deserialize_background_library,
    resolve_background_library,
)
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DISCOVERY_BRIEF_COLUMNS,
    DISCOVERY_CANDIDATE_COLUMNS,
    MS1_FEATURE_BACKED_STATES,
    DiscoveryBatchOutputs,
    DiscoveryCandidate,
    DiscoveryPhysicalWorkItem,
    DiscoveryRunOutputs,
    DiscoverySettings,
    ReviewPriority,
)
from xic_extractor.discovery.ms1_backfill import (
    MS1CentroidMz,
    MS1XicSource,
    _backfill_ms1_candidates_with_acquisition,
    _DiscoveryMs1Measurements,
    backfill_ms1_candidates,
)
from xic_extractor.discovery.ms2_seeds import MS2ScanSource, collect_strict_nl_seeds
from xic_extractor.discovery.peak_anchor import assign_peak_anchors
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
    DiscoveryPhysicalFactRecord,
    bind_discovery_physical_record_evidence,
    bind_physical_facts_to_final_candidates,
    order_physical_bindings_for_candidate_csv,
)
from xic_extractor.discovery.physical_fact_sidecar import (
    stage_discovery_physical_fact_sidecar as write_discovery_physical_fact_sidecar,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    processing_run_input_for_path,
    validate_processing_run_spec_call,
)
from xic_extractor.execution.attempt import (
    derive_runtime_acquisition_metrics,
    derive_runtime_raw_open_metrics,
)
from xic_extractor.execution.timing import TimingRecord, TimingRecorder
from xic_extractor.execution.workers import execute_process_jobs
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.processing_run_spec import (
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
    RawFileHandle,
)
from xic_extractor.tabular_io import write_delimited_rows

_BATCH_INDEX_COLUMNS = (
    "schema_version",
    "sample_stem",
    "raw_file",
    "candidate_csv",
    "review_csv",
    "candidate_count",
    "high_count",
    "medium_count",
    "low_count",
    *DISCOVERY_BATCH_RECEIPT_COLUMNS,
)
_DISCOVERY_MS1_MEASUREMENT_NAMES = frozenset(
    {
        "discovery_ms1_input_group_count",
        "discovery_ms1_empty_group_count",
        "discovery_ms1_exact_scan_window_count",
        "discovery_ms1_predicted_chunked_call_count",
        "discovery_ms1_batch_max_requests",
        "discovery_ms1_reacquisition_request_count",
        "discovery_ms1_predicted_call_reduction_pct",
    }
)


class DiscoveryRawHandle(MS1XicSource, MS2ScanSource, Protocol):
    """RAW reader surface needed by single-file discovery orchestration."""


RawOpener = Callable[[Path, Path], AbstractContextManager[DiscoveryRawHandle]]


@dataclass(frozen=True)
class _DiscoveryRawFileResult:
    candidates: tuple[DiscoveryCandidate, ...]
    ms1_measurements: _DiscoveryMs1Measurements | None
    raw_open_receipt: RawAcquisitionSessionReceipt | None
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = ()
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = ()
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...] = ()


@dataclass(frozen=True)
class DiscoverySampleProcessJob:
    sample_index: int
    raw_path: Path
    output_dir: Path
    settings: DiscoverySettings
    peak_config: ExtractionConfig
    acquisition_source: AcquisitionSourceIdentity
    raw_session_capability: RawAcquisitionSessionCapability
    instrument_diagnostics: bool = False
    run_spec: ProcessingRunSpec | None = None
    acquisition_input: ProcessingAcquisitionInput | None = None


@dataclass(frozen=True)
class DiscoverySampleProcessResult:
    sample_index: int
    sample_stem: str
    outputs: DiscoveryRunOutputs
    receipt: DiscoveryCandidateUniverseReceipt
    index_row: dict[str, str]
    timing_records: tuple[TimingRecord, ...]
    measurement_observations: tuple[dict[str, object], ...]
    ms1_measurements: _DiscoveryMs1Measurements | None = None
    raw_open_receipt: RawAcquisitionSessionReceipt | None = None
    run_spec_id: str = ""
    profile_id: str = ""
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
    run_spec_id: str = ""
    profile_id: str = ""
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
        self.run_spec_id = first_failure.run_spec_id
        self.profile_id = first_failure.profile_id
        self.acquisition_source_id = first_failure.acquisition_source_id
        super().__init__(f"{first_failure.raw_name}: {first_failure.message}")


def _finalize_physical_bindings_for_output(
    *,
    run_spec: ProcessingRunSpec | None,
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidatePhysicalBinding, ...]:
    if run_spec is None:
        return ()
    return order_physical_bindings_for_candidate_csv(
        bind_physical_facts_to_final_candidates(bindings, candidates)
    )


@lru_cache(maxsize=2)
def _bound_background_library(content: str) -> BlankBackgroundLibrary:
    return deserialize_background_library(content)


def _apply_blank_qualification_if_enabled(
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    raw_path: Path,
    settings: DiscoverySettings,
    timing_recorder: TimingRecorder,
    sample_stem: str,
    acquisition_input: ProcessingAcquisitionInput | None = None,
) -> tuple[DiscoveryCandidate, ...]:
    if not settings.enable_blank_qualification:
        return candidates
    with timing_recorder.stage(
        "discover.blank_qualification",
        sample_stem=sample_stem,
        metrics={"candidate_count": len(candidates)},
    ) as stage:
        if acquisition_input is None:
            library = resolve_background_library(
                raw_dir=raw_path.parent,
                library_path=settings.blank_library_path,
            )
        elif acquisition_input.blank_library_sha256 is None:
            library = None
        elif acquisition_input.blank_library_content is None:
            raise ValueError("bound blank library snapshot is unavailable")
        else:
            library = _bound_background_library(acquisition_input.blank_library_content)
        qualified = apply_blank_qualification_to_candidates(
            candidates,
            library,
            ppm_tol=settings.blank_ppm_tol,
            fold_change_threshold=settings.blank_fold_change_threshold,
        )
        excluded_count = sum(
            1
            for c in qualified
            if c.discovery_candidate_state == "review_only_orphan_nl"
            and c.reason.startswith("system background:")
        )
        stage.metrics["excluded_background_count"] = excluded_count
        return qualified


def _raise_if_discovery_cancelled(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise InterruptedError("Discovery cancelled before output publication")


def run_discovery(
    raw_path: Path,
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    run_spec: ProcessingRunSpec | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryRunOutputs:
    _raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    sample_stem = raw_path.stem
    acquisition_input: ProcessingAcquisitionInput | None = None
    acquisition_source: AcquisitionSourceIdentity | None = None
    if run_spec is not None:
        (acquisition_input,) = validate_processing_run_spec_call(
            run_spec,
            (raw_path,),
            discovery_settings=settings,
            signal_processing_profile=_processing_profile_from_config(peak_config),
        )
        acquisition_source = acquisition_input.acquisition_source
    elif raw_opener is None:
        acquisition_source = _acquisition_source(raw_path)
    if raw_opener is None:
        if acquisition_source is None:
            raise ValueError("Discovery RAW owner requires acquisition identity")
        raw_session_capability = mint_raw_acquisition_session_capability(
            acquisition_source
        )
        opener = _owner_bound_raw_opener(
            acquisition_source,
            raw_session_capability,
        )
    else:
        raw_session_capability = None
        opener = raw_opener
    discovery_result = _discover_raw_file_with_runtime_projection(
        raw_path,
        settings=settings,
        peak_config=peak_config,
        raw_opener=opener,
        timing_recorder=recorder,
        raw_session_capability=raw_session_capability,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
    )
    _record_discovery_ms1_measurements(
        recorder,
        discovery_result.ms1_measurements,
        sample_stem,
    )
    discovered = discovery_result.candidates
    with recorder.stage(
        "discover.peak_anchor",
        sample_stem=sample_stem,
        metrics={"input_count": len(discovered)},
    ) as stage:
        candidates = assign_peak_anchors(discovered, settings=settings)
        stage.metrics["candidate_count"] = len(candidates)
    candidates = _apply_blank_qualification_if_enabled(
        candidates,
        raw_path=raw_path,
        settings=settings,
        timing_recorder=recorder,
        sample_stem=sample_stem,
        acquisition_input=acquisition_input,
    )
    _raise_if_discovery_cancelled(should_cancel)
    return _write_dual_csvs(
        output_dir,
        candidates,
        timing_recorder=recorder,
        sample_stem=sample_stem,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        physical_work_manifest=discovery_result.physical_work_manifest,
        physical_evaluations=discovery_result.physical_evaluations,
        physical_bindings=_finalize_physical_bindings_for_output(
            run_spec=run_spec,
            bindings=discovery_result.physical_bindings,
            candidates=candidates,
        ),
    )


def run_discovery_batch(
    raw_paths: tuple[Path, ...],
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    max_workers: int = 1,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    run_spec: ProcessingRunSpec | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryBatchOutputs:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    _raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    run_inputs = (
        None
        if run_spec is None
        else validate_processing_run_spec_call(
            run_spec,
            raw_paths,
            discovery_settings=settings,
            signal_processing_profile=_processing_profile_from_config(peak_config),
        )
    )
    if max_workers > 1:
        if raw_opener is not None:
            raise ValueError(
                "parallel Discovery owns RAW handles inside spawned workers; "
                "raw_opener injection requires max_workers=1"
            )
        return _run_discovery_batch_process(
            raw_paths,
            output_dir=output_dir,
            settings=settings,
            peak_config=peak_config,
            timing_recorder=recorder,
            max_workers=max_workers,
            executor_factory=executor_factory,
            progress_callback=progress_callback,
            run_spec=run_spec,
            run_inputs=run_inputs,
            should_cancel=should_cancel,
        )

    index_rows: list[dict[str, str]] = []
    per_sample: list[DiscoveryRunOutputs] = []
    for sample_index, raw_path in enumerate(raw_paths):
        _raise_if_discovery_cancelled(should_cancel)
        sample_stem = raw_path.stem
        acquisition_input = None if run_inputs is None else run_inputs[sample_index]
        acquisition_source = (
            _acquisition_source(raw_path)
            if acquisition_input is None
            else acquisition_input.acquisition_source
        )
        if raw_opener is None:
            raw_session_capability = mint_raw_acquisition_session_capability(
                acquisition_source
            )
            opener = _owner_bound_raw_opener(
                acquisition_source,
                raw_session_capability,
            )
        else:
            raw_session_capability = None
            opener = raw_opener
        discovery_result = _discover_raw_file_with_runtime_projection(
            raw_path,
            settings=settings,
            peak_config=peak_config,
            raw_opener=opener,
            timing_recorder=recorder,
            raw_session_capability=raw_session_capability,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )
        _record_discovery_ms1_measurements(
            recorder,
            discovery_result.ms1_measurements,
            sample_stem,
        )
        discovered = discovery_result.candidates
        with recorder.stage(
            "discover.peak_anchor",
            sample_stem=sample_stem,
            metrics={"input_count": len(discovered)},
        ) as stage:
            candidates = assign_peak_anchors(discovered, settings=settings)
            stage.metrics["candidate_count"] = len(candidates)
        candidates = _apply_blank_qualification_if_enabled(
            candidates,
            raw_path=raw_path,
            settings=settings,
            timing_recorder=recorder,
            sample_stem=sample_stem,
            acquisition_input=acquisition_input,
        )
        sample_output_dir = output_dir / sample_stem
        outputs = _write_dual_csvs(
            sample_output_dir,
            candidates,
            timing_recorder=recorder,
            sample_stem=sample_stem,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
            physical_work_manifest=discovery_result.physical_work_manifest,
            physical_evaluations=discovery_result.physical_evaluations,
            physical_bindings=_finalize_physical_bindings_for_output(
                run_spec=run_spec,
                bindings=discovery_result.physical_bindings,
                candidates=candidates,
            ),
        )
        per_sample.append(outputs)
        candidates = _persisted_discovery_candidates(candidates)
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=acquisition_source,
            candidate_csv=outputs.candidates_csv,
            candidates=candidates,
        )
        index_rows.append(
            _batch_index_row(
                raw_path=raw_path,
                outputs=outputs,
                candidates=candidates,
                receipt=receipt,
                index_parent=output_dir,
            )
        )
        if progress_callback is not None:
            progress_callback(sample_index + 1, len(raw_paths), sample_stem)

    _raise_if_discovery_cancelled(should_cancel)
    return DiscoveryBatchOutputs(
        batch_index_csv=_write_batch_index(
            output_dir / "discovery_batch_index.csv",
            index_rows,
            timing_recorder=recorder,
        ),
        per_sample=tuple(per_sample),
    )


def _run_discovery_batch_process(
    raw_paths: tuple[Path, ...],
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    timing_recorder: TimingRecorder,
    max_workers: int,
    executor_factory: Callable[..., Any] | None,
    progress_callback: Callable[[int, int, str], None] | None,
    run_spec: ProcessingRunSpec | None,
    run_inputs: tuple[ProcessingAcquisitionInput, ...] | None,
    should_cancel: Callable[[], bool] | None,
) -> DiscoveryBatchOutputs:
    pending_jobs: list[DiscoverySampleProcessJob] = []
    for sample_index, raw_path in enumerate(raw_paths):
        _raise_if_discovery_cancelled(should_cancel)
        acquisition_input = None if run_inputs is None else run_inputs[sample_index]
        acquisition_source = (
            _acquisition_source(raw_path)
            if acquisition_input is None
            else acquisition_input.acquisition_source
        )
        pending_jobs.append(
            DiscoverySampleProcessJob(
                sample_index=sample_index,
                raw_path=raw_path,
                output_dir=output_dir,
                settings=settings,
                peak_config=peak_config,
                acquisition_source=acquisition_source,
                raw_session_capability=mint_raw_acquisition_session_capability(
                    acquisition_source
                ),
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
        worker=_run_discovery_sample_process_job,
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
        expected_run_spec_id = (
            expected_job.run_spec.run_spec_id
            if expected_job.run_spec is not None
            else ""
        )
        expected_profile_id = (
            expected_job.run_spec.profile_id
            if expected_job.run_spec is not None
            else ""
        )
        expected_acquisition_source_id = (
            expected_job.acquisition_source.acquisition_source_id
            if expected_job.run_spec is not None
            else ""
        )
        if (
            observed.run_spec_id != expected_run_spec_id
            or observed.profile_id != expected_profile_id
            or observed.acquisition_source_id != expected_acquisition_source_id
        ):
            raise ValueError(
                "Discovery process result crosses run/profile/acquisition identity"
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
            _reject_caller_authored_discovery_ms1_measurement(observation)
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
        _record_raw_open_metrics(
            timing_recorder,
            expected_job.raw_session_capability,
            observed.raw_open_receipt,
            observed.sample_stem,
        )
        _record_discovery_ms1_measurements(
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
    _raise_if_discovery_cancelled(should_cancel)

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
        batch_index_csv=_write_batch_index(
            output_dir / "discovery_batch_index.csv",
            [result.index_row for result in results],
            timing_recorder=timing_recorder,
        ),
        per_sample=tuple(result.outputs for result in results),
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


def _run_discovery_sample_process_job(
    job: DiscoverySampleProcessJob,
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
        discovery_result = _discover_raw_file(
            job.raw_path,
            settings=job.settings,
            peak_config=job.peak_config,
            raw_opener=_owner_bound_raw_opener(
                job.acquisition_source,
                job.raw_session_capability,
            ),
            timing_recorder=recorder,
            measurement_observer=_capture_acquired_measurements,
            raw_open_receipt_observer=_capture_raw_open_receipt,
            run_spec=job.run_spec,
            acquisition_input=job.acquisition_input,
        )
        sample_stem = job.raw_path.stem
        discovered = discovery_result.candidates
        with recorder.stage(
            "discover.peak_anchor",
            sample_stem=sample_stem,
            metrics={"input_count": len(discovered)},
        ) as stage:
            candidates = assign_peak_anchors(discovered, settings=job.settings)
            stage.metrics["candidate_count"] = len(candidates)
        candidates = _apply_blank_qualification_if_enabled(
            candidates,
            raw_path=job.raw_path,
            settings=job.settings,
            timing_recorder=recorder,
            sample_stem=sample_stem,
            acquisition_input=job.acquisition_input,
        )
        outputs = _write_dual_csvs(
            job.output_dir / sample_stem,
            candidates,
            timing_recorder=recorder,
            sample_stem=sample_stem,
            run_spec=job.run_spec,
            acquisition_input=job.acquisition_input,
            physical_work_manifest=discovery_result.physical_work_manifest,
            physical_evaluations=discovery_result.physical_evaluations,
            physical_bindings=_finalize_physical_bindings_for_output(
                run_spec=job.run_spec,
                bindings=discovery_result.physical_bindings,
                candidates=candidates,
            ),
        )
        candidates = _persisted_discovery_candidates(candidates)
        receipt = mint_discovery_candidate_universe_receipt(
            sample_stem=sample_stem,
            acquisition_source=job.acquisition_source,
            candidate_csv=outputs.candidates_csv,
            candidates=candidates,
        )
        return DiscoverySampleProcessResult(
            sample_index=job.sample_index,
            sample_stem=sample_stem,
            outputs=outputs,
            receipt=receipt,
            index_row=_batch_index_row(
                raw_path=job.raw_path,
                outputs=outputs,
                candidates=candidates,
                receipt=receipt,
                index_parent=job.output_dir,
            ),
            timing_records=recorder.records,
            measurement_observations=_portable_worker_observations(recorder),
            ms1_measurements=discovery_result.ms1_measurements,
            raw_open_receipt=discovery_result.raw_open_receipt,
            run_spec_id=(job.run_spec.run_spec_id if job.run_spec is not None else ""),
            profile_id=(job.run_spec.profile_id if job.run_spec is not None else ""),
            acquisition_source_id=(
                job.acquisition_source.acquisition_source_id
                if job.run_spec is not None
                else ""
            ),
        )
    except Exception as exc:
        return _discovery_process_error(
            job,
            exc,
            timing_records=recorder.records,
            measurement_observations=_portable_worker_observations(recorder),
            ms1_measurements=acquired_measurements,
            raw_open_receipt=raw_open_receipt,
        )


def _validate_discovery_process_job_binding(job: DiscoverySampleProcessJob) -> None:
    if (job.run_spec is None) != (job.acquisition_input is None):
        raise ValueError("Discovery process job run-spec binding is partial")
    if job.run_spec is None or job.acquisition_input is None:
        return
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
    if (
        _processing_profile_from_config(job.peak_config)
        != job.run_spec.signal_processing_profile
    ):
        raise ValueError("Discovery process job crosses signal profile")


def _processing_profile_from_config(
    peak_config: ExtractionConfig,
) -> SignalProcessingProfile:
    return resolve_signal_processing_profile(
        signal_processing_method=peak_config.signal_processing_method,
        signal_processing_window_points=peak_config.signal_processing_window_points,
    )


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
        run_spec_id=(job.run_spec.run_spec_id if job.run_spec is not None else ""),
        profile_id=(job.run_spec.profile_id if job.run_spec is not None else ""),
        acquisition_source_id=(
            job.acquisition_source.acquisition_source_id
            if job.run_spec is not None
            else ""
        ),
    )


def _is_feature_backed_candidate(candidate: DiscoveryCandidate) -> bool:
    return (
        candidate.ms1_peak_found
        and candidate.discovery_candidate_state in MS1_FEATURE_BACKED_STATES
    )


def _persisted_discovery_candidates(
    candidates: tuple[DiscoveryCandidate, ...],
) -> tuple[DiscoveryCandidate, ...]:
    return tuple(
        candidate
        for candidate in candidates
        if _is_feature_backed_candidate(candidate)
        or candidate.precursor_mz_basis in {"scan_precursor", "mixed"}
    )


def _persisted_physical_records(
    records: tuple[DiscoveryPhysicalFactRecord, ...],
    bindings: tuple[DiscoveryCandidatePhysicalBinding, ...],
) -> tuple[DiscoveryPhysicalFactRecord, ...]:
    """Project feature evidence and compact its persistence-local work indices."""
    feature_bindings = tuple(
        binding
        for binding in bindings
        if _is_feature_backed_candidate(binding.candidate)
    )
    used_work = {
        ordinal
        for binding in feature_bindings
        for ordinal in binding.member_work_ordinals
    }
    retained = tuple(
        record for record in records if record.work_item.work_ordinal in used_work
    )
    work_indices = {
        record.work_item.work_ordinal: index for index, record in enumerate(retained)
    }
    linked = bind_discovery_physical_record_evidence(retained, feature_bindings)
    return tuple(
        replace(
            record,
            work_item=replace(record.work_item, work_ordinal=index),
            candidate_links=tuple(
                replace(
                    link,
                    representative_work_ordinal=work_indices[
                        link.representative_work_ordinal
                    ],
                    member_work_ordinals=tuple(
                        work_indices[item] for item in link.member_work_ordinals
                    ),
                )
                for link in record.candidate_links
            ),
        )
        for index, record in enumerate(linked)
    )


def _write_dual_csvs(
    output_dir: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
    run_spec: ProcessingRunSpec | None = None,
    acquisition_input: ProcessingAcquisitionInput | None = None,
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...] = (),
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...] = (),
    physical_bindings: tuple[DiscoveryCandidatePhysicalBinding, ...] = (),
) -> DiscoveryRunOutputs:
    candidates = _persisted_discovery_candidates(candidates)
    persisted_ids = {candidate.candidate_id for candidate in candidates}
    physical_bindings = order_physical_bindings_for_candidate_csv(
        tuple(
            binding
            for binding in physical_bindings
            if binding.candidate.candidate_id in persisted_ids
        )
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "discovery_candidates.csv"
    review_path = output_dir / "discovery_review.csv"
    physical_facts_member_path = output_dir / "discovery_physical_facts.json"
    physical_facts_path = None if run_spec is None else physical_facts_member_path
    if (run_spec is None) != (acquisition_input is None):
        raise ValueError("Discovery physical persistence binding is partial")
    if run_spec is None and (physical_work_manifest or physical_evaluations):
        raise ValueError("legacy Discovery cannot persist owner physical facts")
    physical_evaluations = _persisted_physical_records(
        physical_evaluations,
        physical_bindings,
    )
    physical_work_manifest = tuple(record.work_item for record in physical_evaluations)

    final_paths = [candidates_path, review_path, physical_facts_member_path]
    publish_count = 3 if physical_facts_path is not None else 2
    temp_paths = [_discovery_output_temp_path(path) for path in final_paths]
    backup_paths = [_discovery_output_backup_path(path) for path in final_paths]
    existed_before = {path: path.exists() for path in final_paths}
    backups: list[tuple[Path, Path]] = []
    try:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
        with timing_recorder.stage(
            "discover.write_candidates_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_candidates_csv(temp_paths[0], candidates)
            _verify_staged_discovery_csv(
                temp_paths[0],
                columns=DISCOVERY_CANDIDATE_COLUMNS,
                row_count=len(candidates),
            )
        with timing_recorder.stage(
            "discover.write_review_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_review_csv(temp_paths[1], candidates)
            _verify_staged_discovery_csv(
                temp_paths[1],
                columns=DISCOVERY_BRIEF_COLUMNS,
                row_count=len(candidates),
            )
        if run_spec is not None and acquisition_input is not None:
            assert physical_facts_path is not None
            physical_tmp = temp_paths[2]
            with timing_recorder.stage(
                "discover.write_physical_facts_sidecar",
                sample_stem=sample_stem,
                metrics={
                    "work_count": len(physical_work_manifest),
                    "record_count": len(physical_evaluations),
                },
            ) as stage:
                write_discovery_physical_fact_sidecar(
                    physical_tmp,
                    run_spec=run_spec,
                    acquisition_input=acquisition_input,
                    work_manifest=physical_work_manifest,
                    records=physical_evaluations,
                )
                stage.metrics["byte_size"] = physical_tmp.stat().st_size
    except Exception:
        for path in (*temp_paths, *backup_paths):
            path.unlink(missing_ok=True)
            path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
        raise

    with timing_recorder.stage(
        "discover.publish_outputs",
        sample_stem=sample_stem,
        metrics={
            "artifact_count": publish_count,
            "replaced_existing_count": sum(
                int(existed_before[path]) for path in final_paths[:publish_count]
            ),
        },
    ):
        try:
            for final_path, backup_path in zip(
                final_paths,
                backup_paths,
                strict=True,
            ):
                if final_path.exists():
                    final_path.replace(backup_path)
                    backups.append((final_path, backup_path))
            for temp_path, final_path in zip(
                temp_paths[:publish_count],
                final_paths[:publish_count],
                strict=True,
            ):
                temp_path.replace(final_path)
            for _final_path, backup_path in backups:
                with suppress(OSError):
                    backup_path.unlink(missing_ok=True)
        except Exception:
            for final_path, backup_path in reversed(
                tuple(zip(final_paths, backup_paths, strict=True))
            ):
                if backup_path.exists():
                    final_path.unlink(missing_ok=True)
                    backup_path.replace(final_path)
                elif not existed_before[final_path]:
                    final_path.unlink(missing_ok=True)
            for path in (*temp_paths, *backup_paths):
                path.unlink(missing_ok=True)
                path.with_name(f"{path.name}.tmp").unlink(missing_ok=True)
            raise

    return DiscoveryRunOutputs(
        candidates_csv=candidates_path,
        review_csv=review_path,
        physical_facts_json=physical_facts_path,
        physical_bindings=physical_bindings,
    )


def _verify_staged_discovery_csv(
    path: Path,
    *,
    columns: tuple[str, ...],
    row_count: int,
) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or tuple(rows[0]) != columns:
        raise ValueError(f"{path}: staged Discovery CSV header verification failed")
    if len(rows) - 1 != row_count:
        raise ValueError(f"{path}: staged Discovery CSV row-count verification failed")


def _discovery_output_temp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def _discovery_output_backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def _batch_index_row(
    *,
    raw_path: Path,
    outputs: DiscoveryRunOutputs,
    candidates: tuple[DiscoveryCandidate, ...],
    receipt: DiscoveryCandidateUniverseReceipt,
    index_parent: Path,
) -> dict[str, str]:
    counts = _priority_counts(candidates)
    return {
        "schema_version": DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
        "sample_stem": format_discovery_csv_value("sample_stem", raw_path.stem),
        "raw_file": format_discovery_csv_value("raw_file", raw_path),
        "candidate_csv": format_discovery_csv_value(
            "candidate_csv",
            _relative_batch_artifact(outputs.candidates_csv, parent=index_parent),
        ),
        "review_csv": format_discovery_csv_value(
            "review_csv",
            _relative_batch_artifact(outputs.review_csv, parent=index_parent),
        ),
        "candidate_count": str(len(candidates)),
        "high_count": str(counts["HIGH"]),
        "medium_count": str(counts["MEDIUM"]),
        "low_count": str(counts["LOW"]),
        **discovery_receipt_index_fields(receipt),
    }


def _relative_batch_artifact(path: Path, *, parent: Path) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Discovery batch artifact must stay under its index") from exc
    return relative.as_posix()


def _priority_counts(
    candidates: tuple[DiscoveryCandidate, ...],
) -> dict[ReviewPriority, int]:
    counts: dict[ReviewPriority, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for candidate in candidates:
        counts[candidate.review_priority] += 1
    return counts


def _write_batch_index(
    output_path: Path,
    rows: list[dict[str, str]],
    *,
    timing_recorder: TimingRecorder,
) -> Path:
    with timing_recorder.stage(
        "discover.write_batch_index",
        metrics={"raw_count": len(rows), "row_count": len(rows)},
    ):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_delimited_rows(
            output_path,
            rows,
            _BATCH_INDEX_COLUMNS,
            delimiter=",",
            encoding="utf-8",
            extrasaction="raise",
        )
    return output_path


def _discover_raw_file_with_runtime_projection(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    raw_session_capability: RawAcquisitionSessionCapability | None,
    run_spec: ProcessingRunSpec | None = None,
    acquisition_input: ProcessingAcquisitionInput | None = None,
) -> _DiscoveryRawFileResult:
    observed_receipt: RawAcquisitionSessionReceipt | None = None
    receipt_observed = False

    def _capture_receipt(receipt: RawAcquisitionSessionReceipt | None) -> None:
        nonlocal observed_receipt, receipt_observed
        if receipt_observed:
            raise ValueError("Discovery RAW-open receipt was emitted twice")
        receipt_observed = True
        observed_receipt = receipt

    try:
        result = _discover_raw_file(
            raw_path,
            settings=settings,
            peak_config=peak_config,
            raw_opener=raw_opener,
            timing_recorder=timing_recorder,
            raw_open_receipt_observer=_capture_receipt,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )
    except Exception:
        _record_raw_open_metrics(
            timing_recorder,
            raw_session_capability,
            observed_receipt,
            raw_path.stem,
        )
        raise
    _record_raw_open_metrics(
        timing_recorder,
        raw_session_capability,
        result.raw_open_receipt,
        raw_path.stem,
    )
    return result


def _discover_raw_file(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    raw_open_receipt_observer: (
        Callable[[RawAcquisitionSessionReceipt | None], None] | None
    ) = None,
    run_spec: ProcessingRunSpec | None = None,
    acquisition_input: ProcessingAcquisitionInput | None = None,
) -> _DiscoveryRawFileResult:
    if (run_spec is None) != (acquisition_input is None):
        raise ValueError("Discovery ProcessingRunSpec input binding is partial")
    if run_spec is not None and acquisition_input is not None:
        if (
            acquisition_input.input_ordinal >= len(run_spec.acquisition_inputs)
            or run_spec.acquisition_inputs[acquisition_input.input_ordinal]
            != acquisition_input
        ):
            raise ValueError("Discovery input crosses ProcessingRunSpec binding")
    sample_stem = raw_path.stem
    with raw_opener(raw_path, peak_config.dll_dir) as raw:
        owner_bound_source: AcquisitionSourceIdentity | None = None
        if run_spec is not None and acquisition_input is not None:
            owner_bound_source = _owner_bound_acquisition_source(raw)
            if owner_bound_source is None:
                raise ValueError(
                    "spec-bound Discovery RAW handle lacks an acquisition source"
                )
            if owner_bound_source != acquisition_input.acquisition_source:
                raise ValueError(
                    "spec-bound Discovery RAW handle crosses acquisition source"
                )
        raw_open_receipt = _owner_bound_raw_open_receipt(raw)
        if raw_open_receipt_observer is not None:
            raw_open_receipt_observer(raw_open_receipt)
        completed = False
        acquired_measurements: _DiscoveryMs1Measurements | None = None

        def _capture_acquired_measurements(
            measurements: _DiscoveryMs1Measurements,
        ) -> None:
            nonlocal acquired_measurements
            if acquired_measurements is not None:
                raise ValueError("Discovery MS1 measurements were emitted twice")
            acquired_measurements = measurements
            if measurement_observer is not None:
                measurement_observer(measurements)

        try:
            with timing_recorder.stage(
                "discover.ms2_seeds",
                sample_stem=sample_stem,
            ) as stage:
                seeds = collect_strict_nl_seeds(
                    raw, raw_file=raw_path, settings=settings
                )
                stage.metrics["seed_count"] = len(seeds)
            with timing_recorder.stage(
                "discover.group_seeds",
                sample_stem=sample_stem,
            ) as stage:
                groups = group_discovery_seeds(seeds, settings=settings)
                stage.metrics["group_count"] = len(groups)
            with timing_recorder.stage(
                "discover.ms1_backfill",
                sample_stem=sample_stem,
            ) as stage:
                if run_spec is None:
                    owner_bound_source = _owner_bound_acquisition_source(raw)
                if run_spec is not None and acquisition_input is not None:
                    acquisition_source = acquisition_input.acquisition_source
                    acquisition_result = _backfill_ms1_candidates_with_acquisition(
                        cast(BatchXICAdapter, raw),
                        groups,
                        acquisition_source=acquisition_source,
                        run_spec_id=run_spec.run_spec_id,
                        settings=settings,
                        peak_config=peak_config,
                        measurement_observer=_capture_acquired_measurements,
                        signal_processing_profile=(run_spec.signal_processing_profile),
                        ms1_centroid_mz=_ms1_centroid_source(raw),
                    )
                    candidates = acquisition_result.candidates
                    ms1_measurements = acquisition_result.measurements
                    physical_work_manifest = acquisition_result.physical_work_manifest
                    physical_evaluations = acquisition_result.physical_evaluations
                    physical_bindings = acquisition_result.physical_bindings
                elif owner_bound_source is None:
                    candidates = backfill_ms1_candidates(
                        raw,
                        groups,
                        settings=settings,
                        peak_config=peak_config,
                    )
                    ms1_measurements = None
                    physical_work_manifest = ()
                    physical_evaluations = ()
                    physical_bindings = ()
                else:
                    acquisition_result = _backfill_ms1_candidates_with_acquisition(
                        cast(BatchXICAdapter, raw),
                        groups,
                        acquisition_source=owner_bound_source,
                        settings=settings,
                        peak_config=peak_config,
                        measurement_observer=_capture_acquired_measurements,
                        ms1_centroid_mz=_ms1_centroid_source(raw),
                    )
                    candidates = acquisition_result.candidates
                    ms1_measurements = acquisition_result.measurements
                    physical_work_manifest = ()
                    physical_evaluations = ()
                    physical_bindings = ()
                stage.metrics["candidate_count"] = len(candidates)
            completed = True
            return _DiscoveryRawFileResult(
                candidates=candidates,
                ms1_measurements=ms1_measurements,
                raw_open_receipt=raw_open_receipt,
                physical_work_manifest=physical_work_manifest,
                physical_evaluations=physical_evaluations,
                physical_bindings=physical_bindings,
            )
        finally:
            if not completed:
                if acquired_measurements is None:
                    _record_raw_measurements(timing_recorder, raw, sample_stem)
                else:
                    try:
                        _record_discovery_ms1_measurements(
                            timing_recorder,
                            acquired_measurements,
                            sample_stem,
                        )
                    except Exception as diagnostic_exc:
                        timing_recorder.observe(
                            "diagnostic_projection_failure_count",
                            value=1,
                            scope=sample_stem,
                            unit="failures",
                            source="xic_extractor.discovery.pipeline",
                            availability="failed",
                            reason=(
                                f"{type(diagnostic_exc).__name__}: {diagnostic_exc}"
                            ),
                        )


def _ms1_centroid_source(raw: object) -> MS1CentroidMz | None:
    return raw.ms1_centroid_mz if isinstance(raw, RawFileHandle) else None


def _owner_bound_acquisition_source(
    raw: object,
) -> AcquisitionSourceIdentity | None:
    source = getattr(raw, "acquisition_source", None)
    if source is None:
        return None
    if not isinstance(source, AcquisitionSourceIdentity):
        raise ValueError("Discovery RAW handle has an invalid acquisition source")
    for method_name in ("extract_xic_many", "scan_window_for_request"):
        if not callable(getattr(raw, method_name, None)):
            raise ValueError(f"owner-bound Discovery RAW handle lacks {method_name}")
    return source


def _owner_bound_raw_open_receipt(
    raw: object,
) -> RawAcquisitionSessionReceipt | None:
    receipt = getattr(raw, "raw_open_receipt", None)
    if receipt is None:
        return None
    if not isinstance(receipt, RawAcquisitionSessionReceipt):
        raise ValueError("Discovery RAW handle exposed an invalid RAW-open receipt")
    return receipt


def _record_discovery_ms1_measurements(
    timing_recorder: TimingRecorder,
    measurements: _DiscoveryMs1Measurements | None,
    sample_stem: str,
    *,
    unavailable_reason: str = (
        "RAW handle is not bound to the acquisition owner batch contract"
    ),
) -> None:
    if measurements is None:
        _record_unavailable_discovery_ms1_measurements(
            timing_recorder,
            sample_stem,
            reason=unavailable_reason,
        )
        return

    runtime_metrics = derive_runtime_acquisition_metrics(
        measurements.acquisition_batches
    )
    nonempty_group_count = (
        measurements.input_group_count - measurements.empty_group_count
    )
    if measurements.initial_request_count > nonempty_group_count or (
        runtime_metrics.logical_xic_request_count
        != measurements.initial_request_count + measurements.reacquisition_request_count
    ):
        raise ValueError(
            "Discovery MS1 acquisition receipts do not cover the input population"
        )
    acquisition_source = "xic_extractor.acquisition_contract.TraceAcquisitionBatch"
    discovery_source = "xic_extractor.discovery.ms1_backfill"
    observed = (
        (
            "logical_xic_request_count",
            runtime_metrics.logical_xic_request_count,
            "requests",
            acquisition_source,
        ),
        (
            "xic_batch_count",
            runtime_metrics.xic_batch_count,
            "batches",
            acquisition_source,
        ),
        (
            "adapter_chromatogram_call_count",
            runtime_metrics.adapter_chromatogram_call_count,
            "calls",
            acquisition_source,
        ),
        (
            "trace_point_count",
            runtime_metrics.trace_point_count,
            "points",
            acquisition_source,
        ),
        (
            "discovery_ms1_input_group_count",
            measurements.input_group_count,
            "groups",
            discovery_source,
        ),
        (
            "discovery_ms1_empty_group_count",
            measurements.empty_group_count,
            "groups",
            discovery_source,
        ),
        (
            "discovery_ms1_exact_scan_window_count",
            measurements.exact_scan_window_count,
            "windows",
            discovery_source,
        ),
        (
            "discovery_ms1_predicted_chunked_call_count",
            measurements.predicted_chunked_call_count,
            "calls",
            discovery_source,
        ),
        (
            "discovery_ms1_batch_max_requests",
            measurements.batch_max_requests,
            "requests/batch",
            discovery_source,
        ),
        (
            "discovery_ms1_reacquisition_request_count",
            measurements.reacquisition_request_count,
            "requests",
            discovery_source,
        ),
        (
            "discovery_ms1_predicted_call_reduction_pct",
            (
                0.0
                if runtime_metrics.logical_xic_request_count == 0
                else 100.0
                * (
                    runtime_metrics.logical_xic_request_count
                    - measurements.predicted_chunked_call_count
                )
                / runtime_metrics.logical_xic_request_count
            ),
            "percent",
            discovery_source,
        ),
    )
    for metric, value, unit, source in observed:
        timing_recorder.observe(
            metric,
            value=value,
            scope=sample_stem,
            unit=unit,
            source=source,
        )
    _record_rss_unavailable(timing_recorder, sample_stem)
    _validate_discovery_ms1_observations(timing_recorder, sample_stem)


def _record_unavailable_discovery_ms1_measurements(
    timing_recorder: TimingRecorder,
    sample_stem: str,
    *,
    reason: str,
) -> None:
    unavailable = (
        ("logical_xic_request_count", "requests"),
        ("xic_batch_count", "batches"),
        ("adapter_chromatogram_call_count", "calls"),
        ("trace_point_count", "points"),
        ("discovery_ms1_input_group_count", "groups"),
        ("discovery_ms1_empty_group_count", "groups"),
        ("discovery_ms1_exact_scan_window_count", "windows"),
        ("discovery_ms1_predicted_chunked_call_count", "calls"),
        ("discovery_ms1_batch_max_requests", "requests/batch"),
        ("discovery_ms1_reacquisition_request_count", "requests"),
        ("discovery_ms1_predicted_call_reduction_pct", "percent"),
    )
    for metric, unit in unavailable:
        timing_recorder.observe(
            metric,
            value=None,
            scope=sample_stem,
            unit=unit,
            source="xic_extractor.discovery.ms1_backfill",
            availability="unavailable",
            reason=reason,
        )
    _record_rss_unavailable(timing_recorder, sample_stem)
    _validate_discovery_ms1_observations(timing_recorder, sample_stem)


def _record_rss_unavailable(
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> None:
    reason = "unsupported: Discovery runner has no owner-bound RSS sampler"
    for metric in ("worker_peak_rss_bytes", "process_tree_peak_rss_bytes"):
        timing_recorder.observe(
            metric,
            value=None,
            scope=sample_stem,
            unit="bytes",
            aggregation="max",
            source="xic_extractor.discovery.pipeline",
            availability="unavailable",
            reason=reason,
        )


def _record_raw_open_metrics(
    timing_recorder: TimingRecorder,
    raw_session_capability: RawAcquisitionSessionCapability | None,
    raw_open_receipt: RawAcquisitionSessionReceipt | None,
    sample_stem: str,
) -> None:
    metrics = derive_runtime_raw_open_metrics(
        raw_session_capability,
        () if raw_open_receipt is None else (raw_open_receipt,),
    )
    timing_recorder.observe(
        "raw_open_count",
        value=metrics.raw_open_count,
        scope=sample_stem,
        unit="opens",
        source=(
            "xic_extractor.execution.attempt.derive_runtime_raw_open_metrics"
        ),
        availability=(
            "measured" if metrics.raw_open_count is not None else "unavailable"
        ),
        reason=metrics.unavailable_reason,
    )


def _portable_worker_observations(
    timing_recorder: TimingRecorder,
) -> tuple[dict[str, object], ...]:
    return tuple(
        observation
        for observation in timing_recorder.measurement_observations
        if not _is_reserved_process_measurement(observation)
    )


def _is_reserved_process_measurement(observation: dict[str, object]) -> bool:
    metric = observation.get("metric")
    reserved = {
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "worker_peak_rss_bytes",
        "process_tree_peak_rss_bytes",
    }
    return isinstance(metric, str) and (
        metric.startswith("discovery_ms1_") or metric in reserved
    )


def _reject_caller_authored_discovery_ms1_measurement(
    observation: dict[str, object],
) -> None:
    if _is_reserved_process_measurement(observation):
        raise ValueError(
            "Discovery worker diagnostics cannot author acquisition/runtime counts"
        )


def _validate_discovery_ms1_observations(
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> None:
    if not timing_recorder.enabled:
        return
    names = tuple(
        str(row["metric"])
        for row in timing_recorder.measurement_observations
        if row.get("scope") == sample_stem
        and isinstance(row.get("metric"), str)
        and str(row["metric"]).startswith("discovery_ms1_")
    )
    if len(names) != len(set(names)):
        raise ValueError("duplicate Discovery MS1 measurement field")
    if set(names) != _DISCOVERY_MS1_MEASUREMENT_NAMES:
        unexpected = sorted(set(names) - _DISCOVERY_MS1_MEASUREMENT_NAMES)
        missing = sorted(_DISCOVERY_MS1_MEASUREMENT_NAMES - set(names))
        raise ValueError(
            "unknown or missing Discovery MS1 measurement field: "
            f"unexpected={unexpected!r}, missing={missing!r}"
        )


def _record_raw_measurements(
    timing_recorder: TimingRecorder,
    raw: object,
    sample_stem: str,
) -> None:
    """Project Discovery owner facts without inventing unavailable counters."""

    try:
        raw_calls = getattr(raw, "raw_chromatogram_call_count", None)
        raw_calls_reason = (
            "" if isinstance(raw_calls, int) else "raw handle has no call counter"
        )
    except Exception as exc:
        raw_calls = None
        raw_calls_reason = (
            f"raw handle call counter read failed: {type(exc).__name__}: {exc}"
        )
    timing_recorder.observe(
        "logical_xic_request_count",
        value=None,
        scope=sample_stem,
        unit="requests",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter exposes no logical request counter",
    )
    timing_recorder.observe(
        "xic_batch_count",
        value=None,
        scope=sample_stem,
        unit="batches",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter exposes no batch counter",
    )
    timing_recorder.observe(
        "adapter_chromatogram_call_count",
        value=raw_calls,
        scope=sample_stem,
        unit="calls",
        source="discovery.raw_handle.raw_chromatogram_call_count",
        availability="measured" if isinstance(raw_calls, int) else "unavailable",
        reason=raw_calls_reason,
    )
    timing_recorder.observe(
        "trace_point_count",
        value=None,
        scope=sample_stem,
        unit="points",
        source="discovery.raw_handle",
        availability="unavailable",
        reason="Discovery raw adapter does not expose returned point count",
    )


def _owner_bound_raw_opener(
    acquisition_source: AcquisitionSourceIdentity,
    raw_session_capability: RawAcquisitionSessionCapability,
) -> RawOpener:
    """Bind the production batch reader to the content identity it will receipt."""

    def _open(
        raw_path: Path,
        dll_dir: Path,
    ) -> AbstractContextManager[DiscoveryRawHandle]:
        from xic_extractor.raw_reader import open_raw

        return cast(
            AbstractContextManager[DiscoveryRawHandle],
            open_raw(
                raw_path,
                dll_dir,
                expected_acquisition_source=acquisition_source,
                _raw_session_capability=raw_session_capability,
            ),
        )

    return _open


def _acquisition_source(raw_path: Path) -> AcquisitionSourceIdentity:
    return build_acquisition_source_identity(
        raw_path,
        acquisition_format=THERMO_RAW_ACQUISITION_FORMAT,
        observation_namespace=THERMO_RAW_OBSERVATION_NAMESPACE,
    )
