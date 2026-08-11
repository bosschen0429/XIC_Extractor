from __future__ import annotations

import pickle
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.acquisition_contract import (
    BatchXICAdapter,
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    mint_raw_acquisition_session_capability,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.diagnostics.timing import TimingRecord, TimingRecorder
from xic_extractor.discovery.batch_contract import (
    DISCOVERY_BATCH_INDEX_SCHEMA_VERSION,
    DISCOVERY_BATCH_RECEIPT_COLUMNS,
    DiscoveryCandidateUniverseReceipt,
    discovery_receipt_index_fields,
    mint_discovery_candidate_universe_receipt,
)
from xic_extractor.discovery.csv_writer import (
    format_discovery_csv_value,
    write_discovery_candidates_csv,
    write_discovery_review_csv,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryCandidate,
    DiscoveryRunOutputs,
    DiscoverySettings,
    ReviewPriority,
)
from xic_extractor.discovery.ms1_backfill import (
    MS1XicSource,
    _backfill_ms1_candidates_with_acquisition,
    _DiscoveryMs1Measurements,
    backfill_ms1_candidates,
)
from xic_extractor.discovery.ms2_seeds import MS2ScanSource, collect_strict_nl_seeds
from xic_extractor.discovery.peak_anchor import assign_peak_anchors
from xic_extractor.execution_runtime_owner import (
    derive_runtime_acquisition_metrics,
    derive_runtime_raw_open_metrics,
)
from xic_extractor.process_job_execution import execute_process_jobs
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    build_acquisition_source_identity,
)
from xic_extractor.raw_reader import (
    THERMO_RAW_ACQUISITION_FORMAT,
    THERMO_RAW_OBSERVATION_NAMESPACE,
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


def run_discovery(
    raw_path: Path,
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    peak_config: ExtractionConfig,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
) -> DiscoveryRunOutputs:
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    sample_stem = raw_path.stem
    if raw_opener is None:
        acquisition_source = _acquisition_source(raw_path)
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
    return _write_dual_csvs(
        output_dir,
        candidates,
        timing_recorder=recorder,
        sample_stem=sample_stem,
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
) -> DiscoveryBatchOutputs:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
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
        )

    index_rows: list[dict[str, str]] = []
    per_sample: list[DiscoveryRunOutputs] = []
    for sample_index, raw_path in enumerate(raw_paths):
        sample_stem = raw_path.stem
        acquisition_source = _acquisition_source(raw_path)
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
        sample_output_dir = output_dir / sample_stem
        outputs = _write_dual_csvs(
            sample_output_dir,
            candidates,
            timing_recorder=recorder,
            sample_stem=sample_stem,
        )
        per_sample.append(outputs)
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
) -> DiscoveryBatchOutputs:
    pending_jobs: list[DiscoverySampleProcessJob] = []
    for sample_index, raw_path in enumerate(raw_paths):
        acquisition_source = _acquisition_source(raw_path)
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
        if (
            expected_job is None
            or observed.sample_stem != expected_job.raw_path.stem
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
        outputs = _write_dual_csvs(
            job.output_dir / sample_stem,
            candidates,
            timing_recorder=recorder,
            sample_stem=sample_stem,
        )
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
    )


def _write_dual_csvs(
    output_dir: Path,
    candidates: tuple[DiscoveryCandidate, ...],
    *,
    timing_recorder: TimingRecorder,
    sample_stem: str,
) -> DiscoveryRunOutputs:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = output_dir / "discovery_candidates.csv"
    review_path = output_dir / "discovery_review.csv"
    candidates_tmp = candidates_path.with_name(f"{candidates_path.name}.tmp")
    review_tmp = review_path.with_name(f"{review_path.name}.tmp")

    try:
        with timing_recorder.stage(
            "discover.write_candidates_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_candidates_csv(candidates_tmp, candidates)
        with timing_recorder.stage(
            "discover.write_review_csv",
            sample_stem=sample_stem,
            metrics={"row_count": len(candidates)},
        ):
            write_discovery_review_csv(review_tmp, candidates)
        candidates_tmp.replace(candidates_path)
        review_tmp.replace(review_path)
    except Exception:
        candidates_tmp.unlink(missing_ok=True)
        review_tmp.unlink(missing_ok=True)
        raise

    return DiscoveryRunOutputs(
        candidates_csv=candidates_path,
        review_csv=review_path,
    )


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
) -> _DiscoveryRawFileResult:
    sample_stem = raw_path.stem
    with raw_opener(raw_path, peak_config.dll_dir) as raw:
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
                acquisition_source = _owner_bound_acquisition_source(raw)
                if acquisition_source is None:
                    candidates = backfill_ms1_candidates(
                        raw,
                        groups,
                        settings=settings,
                        peak_config=peak_config,
                    )
                    ms1_measurements = None
                else:
                    acquisition_result = _backfill_ms1_candidates_with_acquisition(
                        cast(BatchXICAdapter, raw),
                        groups,
                        acquisition_source=acquisition_source,
                        settings=settings,
                        peak_config=peak_config,
                        measurement_observer=_capture_acquired_measurements,
                    )
                    candidates = acquisition_result.candidates
                    ms1_measurements = acquisition_result.measurements
                stage.metrics["candidate_count"] = len(candidates)
            completed = True
            return _DiscoveryRawFileResult(
                candidates=candidates,
                ms1_measurements=ms1_measurements,
                raw_open_receipt=raw_open_receipt,
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
            raise ValueError(
                f"owner-bound Discovery RAW handle lacks {method_name}"
            )
    return source


def _owner_bound_raw_open_receipt(
    raw: object,
) -> RawAcquisitionSessionReceipt | None:
    receipt = getattr(raw, "raw_open_receipt", None)
    if receipt is None:
        return None
    if not isinstance(receipt, RawAcquisitionSessionReceipt):
        raise ValueError(
            "Discovery RAW handle exposed an invalid RAW-open receipt"
        )
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
    if runtime_metrics.logical_xic_request_count != nonempty_group_count:
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
            "xic_extractor.execution_runtime_owner."
            "derive_runtime_raw_open_metrics"
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
            "raw handle call counter read failed: "
            f"{type(exc).__name__}: {exc}"
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
