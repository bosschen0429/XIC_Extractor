from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionReceipt,
)
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
)
from xic_extractor.discovery import output_publication
from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundLibrary,
    apply_blank_qualification_to_peaks,
    deserialize_background_library,
)
from xic_extractor.discovery.cid_nl_search import (
    CidNlSearchSource,
    search_cid_nl_sample,
)
from xic_extractor.discovery.evidence_score import score_peak_evidence
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryPeak,
    DiscoveryPhysicalWorkItem,
    DiscoveryRunOutputs,
    EventAttachment,
)
from xic_extractor.discovery.ms1_capture import (
    _DiscoveryMs1Measurements,
)
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryPeakPhysicalBinding,
    DiscoveryPhysicalFactRecord,
    bind_physical_facts_to_final_peaks,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    validate_processing_run_spec_call,
)
from xic_extractor.discovery.runtime_measurements import (
    record_discovery_ms1_measurements,
    record_raw_measurements,
    record_raw_open_metrics,
)
from xic_extractor.discovery.sample_process_execution import (
    DiscoverySampleProcessJob,
    raise_if_discovery_cancelled,
    run_discovery_batch_process,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import (
    CidNlDiscoveryMethodSpec,
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)


class DiscoveryRawHandle(CidNlSearchSource, Protocol):
    """RAW reader surface needed by single-file discovery orchestration."""

    @property
    def acquisition_source(self) -> AcquisitionSourceIdentity: ...

    @property
    def raw_open_receipt(self) -> RawAcquisitionSessionReceipt | None: ...

    @property
    def mass_recalibration_result(self) -> MassRecalibrationResult: ...


RawOpener = Callable[[Path, Path], AbstractContextManager[DiscoveryRawHandle]]


@dataclass(frozen=True)
class _DiscoverySampleResult:
    peaks: tuple[DiscoveryPeak, ...]
    attachments: tuple[EventAttachment, ...]
    ms1_measurements: _DiscoveryMs1Measurements | None
    raw_open_receipt: RawAcquisitionSessionReceipt | None
    physical_work_manifest: tuple[DiscoveryPhysicalWorkItem, ...]
    physical_evaluations: tuple[DiscoveryPhysicalFactRecord, ...]
    physical_bindings: tuple[DiscoveryPeakPhysicalBinding, ...]
    mass_recalibration_result: MassRecalibrationResult = MassRecalibrationResult("none")


@lru_cache(maxsize=2)
def _bound_background_library(content: str) -> BlankBackgroundLibrary:
    return deserialize_background_library(content)


def _apply_blank_qualification_if_enabled(
    peaks: tuple[DiscoveryPeak, ...],
    *,
    settings: DiscoverySettings,
    timing_recorder: TimingRecorder,
    sample_stem: str,
    acquisition_input: ProcessingAcquisitionInput,
) -> tuple[DiscoveryPeak, ...]:
    if not settings.enable_blank_qualification:
        return peaks
    with timing_recorder.stage(
        "discover.blank_qualification",
        sample_stem=sample_stem,
        metrics={"peak_count": len(peaks)},
    ) as stage:
        if acquisition_input.blank_library_sha256 is None:
            library = None
        elif acquisition_input.blank_library_content is None:
            raise ValueError("bound blank library snapshot is unavailable")
        else:
            library = _bound_background_library(acquisition_input.blank_library_content)
        qualified = apply_blank_qualification_to_peaks(
            peaks,
            library,
            ppm_tol=settings.blank_ppm_tol,
            fold_change_threshold=settings.blank_fold_change_threshold,
        )
        stage.metrics["excluded_background_count"] = sum(
            peak.status == "blank" for peak in qualified
        )
        return qualified


def run_discovery(
    raw_path: Path,
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    dll_dir: Path,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    run_spec: ProcessingRunSpec,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryRunOutputs:
    raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    (acquisition_input,) = validate_processing_run_spec_call(
        run_spec,
        (raw_path,),
        discovery_settings=settings,
    )
    discovery_result = _discover_sample(
        raw_path,
        settings=settings,
        dll_dir=dll_dir,
        raw_opener=raw_opener,
        timing_recorder=recorder,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
    )
    raise_if_discovery_cancelled(should_cancel)
    publication = output_publication.publish_discovery_batch_sample(
        raw_path,
        discovery_result.peaks,
        discovery_result.attachments,
        output_dir=output_dir,
        sample_output_dir=output_dir,
        acquisition_source=acquisition_input.acquisition_source,
        timing_recorder=recorder,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
        physical_evaluations=discovery_result.physical_evaluations,
        physical_bindings=discovery_result.physical_bindings,
        mass_recalibration_result=discovery_result.mass_recalibration_result,
    )
    receipt_index = output_publication.write_batch_index(
        output_dir / "discovery_batch_index.csv",
        [publication.index_row],
        timing_recorder=recorder,
    )
    return replace(publication.outputs, batch_index_csv=receipt_index)


def run_discovery_batch(
    raw_paths: tuple[Path, ...],
    *,
    output_dir: Path,
    settings: DiscoverySettings,
    dll_dir: Path,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    max_workers: int = 1,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    run_spec: ProcessingRunSpec,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryBatchOutputs:
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")
    raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    run_inputs = validate_processing_run_spec_call(
        run_spec,
        raw_paths,
        discovery_settings=settings,
    )
    if max_workers > 1:
        if raw_opener is not None:
            raise ValueError(
                "parallel Discovery owns RAW handles inside spawned workers; "
                "raw_opener injection requires max_workers=1"
            )
        return run_discovery_batch_process(
            raw_paths,
            output_dir=output_dir,
            settings=settings,
            dll_dir=dll_dir,
            timing_recorder=recorder,
            max_workers=max_workers,
            executor_factory=executor_factory,
            progress_callback=progress_callback,
            run_spec=run_spec,
            run_inputs=run_inputs,
            should_cancel=should_cancel,
            discover_sample=discover_sample_for_process_job,
        )

    index_rows: list[dict[str, str]] = []
    per_sample: list[DiscoveryRunOutputs] = []
    for sample_index, raw_path in enumerate(raw_paths):
        raise_if_discovery_cancelled(should_cancel)
        sample_stem = raw_path.stem
        acquisition_input = run_inputs[sample_index]
        acquisition_source = acquisition_input.acquisition_source
        discovery_result = _discover_sample(
            raw_path,
            settings=settings,
            dll_dir=dll_dir,
            raw_opener=raw_opener,
            timing_recorder=recorder,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )
        publication = output_publication.publish_discovery_batch_sample(
            raw_path,
            discovery_result.peaks,
            discovery_result.attachments,
            output_dir=output_dir,
            acquisition_source=acquisition_source,
            timing_recorder=recorder,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
            physical_evaluations=discovery_result.physical_evaluations,
            physical_bindings=discovery_result.physical_bindings,
            mass_recalibration_result=discovery_result.mass_recalibration_result,
        )
        per_sample.append(publication.outputs)
        index_rows.append(publication.index_row)
        if progress_callback is not None:
            progress_callback(sample_index + 1, len(raw_paths), sample_stem)

    raise_if_discovery_cancelled(should_cancel)
    return DiscoveryBatchOutputs(
        batch_index_csv=output_publication.write_batch_index(
            output_dir / "discovery_batch_index.csv",
            index_rows,
            timing_recorder=recorder,
        ),
        per_sample=tuple(per_sample),
    )


def _discover_sample(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    dll_dir: Path,
    raw_opener: RawOpener | None,
    timing_recorder: TimingRecorder,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    acquisition_source = acquisition_input.acquisition_source
    if raw_opener is None:
        opener = _owner_bound_raw_opener(
            acquisition_source,
            settings.mass_recalibration,
        )
    else:
        opener = raw_opener
    return _discover_completed_sample_with_runtime_projection(
        raw_path,
        settings=settings,
        dll_dir=dll_dir,
        raw_opener=opener,
        timing_recorder=timing_recorder,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
    )


def _discover_completed_sample_with_runtime_projection(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    """Return peaks ready for publication after projecting owner metrics."""
    observed_receipt: RawAcquisitionSessionReceipt | None = None
    receipt_observed = False
    acquisition_projected = False

    def _capture_receipt(receipt: RawAcquisitionSessionReceipt | None) -> None:
        nonlocal observed_receipt, receipt_observed
        if receipt_observed:
            raise ValueError("Discovery RAW-open receipt was emitted twice")
        receipt_observed = True
        observed_receipt = receipt

    def _project_acquisition(result: _DiscoverySampleResult) -> None:
        nonlocal acquisition_projected
        acquisition_projected = True
        record_raw_open_metrics(
            timing_recorder,
            acquisition_input.acquisition_source,
            result.raw_open_receipt,
            raw_path.stem,
        )
        record_discovery_ms1_measurements(
            timing_recorder,
            result.ms1_measurements,
            raw_path.stem,
        )

    try:
        result = _discover_completed_sample(
            raw_path,
            settings=settings,
            dll_dir=dll_dir,
            raw_opener=raw_opener,
            timing_recorder=timing_recorder,
            raw_open_receipt_observer=_capture_receipt,
            acquisition_observer=_project_acquisition,
            run_spec=run_spec,
            acquisition_input=acquisition_input,
        )
    except Exception:
        if not acquisition_projected:
            record_raw_open_metrics(
                timing_recorder,
                acquisition_input.acquisition_source,
                observed_receipt,
                raw_path.stem,
            )
        raise
    return result


def _discover_completed_sample(
    raw_path: Path,
    *,
    settings: DiscoverySettings,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    raw_open_receipt_observer: (
        Callable[[RawAcquisitionSessionReceipt | None], None] | None
    ) = None,
    acquisition_observer: Callable[[_DiscoverySampleResult], None] | None = None,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    """Acquire one RAW, close it, then complete its peak facts."""

    acquired = _acquire_discovery_sample(
        raw_path,
        dll_dir=dll_dir,
        raw_opener=raw_opener,
        timing_recorder=timing_recorder,
        measurement_observer=measurement_observer,
        raw_open_receipt_observer=raw_open_receipt_observer,
        run_spec=run_spec,
        acquisition_input=acquisition_input,
    )
    if acquisition_observer is not None:
        acquisition_observer(acquired)
    sample_stem = raw_path.stem
    with timing_recorder.stage(
        "discover.evidence_score",
        sample_stem=sample_stem,
        metrics={"peak_count": len(acquired.peaks)},
    ):
        peaks = score_peak_evidence(acquired.peaks, settings=settings)
    peaks = _apply_blank_qualification_if_enabled(
        peaks,
        settings=settings,
        timing_recorder=timing_recorder,
        sample_stem=sample_stem,
        acquisition_input=acquisition_input,
    )
    physical_bindings = bind_physical_facts_to_final_peaks(
        acquired.physical_bindings,
        peaks,
    )
    return _DiscoverySampleResult(
        peaks=peaks,
        attachments=acquired.attachments,
        ms1_measurements=acquired.ms1_measurements,
        raw_open_receipt=acquired.raw_open_receipt,
        physical_work_manifest=acquired.physical_work_manifest,
        physical_evaluations=acquired.physical_evaluations,
        physical_bindings=physical_bindings,
        mass_recalibration_result=acquired.mass_recalibration_result,
    )


def _acquire_discovery_sample(
    raw_path: Path,
    *,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    raw_open_receipt_observer: (
        Callable[[RawAcquisitionSessionReceipt | None], None] | None
    ) = None,
    run_spec: ProcessingRunSpec,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    if (
        acquisition_input.input_ordinal >= len(run_spec.acquisition_inputs)
        or run_spec.acquisition_inputs[acquisition_input.input_ordinal]
        != acquisition_input
    ):
        raise ValueError("Discovery input crosses ProcessingRunSpec binding")
    method = run_spec.discovery_method
    if not isinstance(method, CidNlDiscoveryMethodSpec):
        raise ValueError("sample-local Discovery search supports only CID-NL")
    sample_stem = raw_path.stem
    with raw_opener(raw_path, dll_dir) as raw:
        owner_bound_source = _owner_bound_acquisition_source(raw)
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
            acquisition_result = search_cid_nl_sample(
                raw,
                raw_file=raw_path,
                method=method,
                acquisition_source=acquisition_input.acquisition_source,
                signal_processing_profile=run_spec.signal_processing_profile,
                timing_recorder=timing_recorder,
                measurement_observer=_capture_acquired_measurements,
            )
            completed = True
            return _DiscoverySampleResult(
                peaks=acquisition_result.peaks,
                attachments=acquisition_result.attachments,
                ms1_measurements=acquisition_result.measurements,
                raw_open_receipt=raw_open_receipt,
                physical_work_manifest=acquisition_result.physical_work_manifest,
                physical_evaluations=acquisition_result.physical_evaluations,
                physical_bindings=acquisition_result.physical_bindings,
                mass_recalibration_result=raw.mass_recalibration_result,
            )
        finally:
            if not completed:
                if acquired_measurements is None:
                    record_raw_measurements(timing_recorder, raw, sample_stem)
                else:
                    try:
                        record_discovery_ms1_measurements(
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
    raw: DiscoveryRawHandle,
) -> AcquisitionSourceIdentity:
    source = raw.acquisition_source
    if not isinstance(source, AcquisitionSourceIdentity):
        raise ValueError("Discovery RAW handle has an invalid acquisition source")
    return source


def _owner_bound_raw_open_receipt(
    raw: DiscoveryRawHandle,
) -> RawAcquisitionSessionReceipt | None:
    receipt = raw.raw_open_receipt
    if receipt is None:
        return None
    if not isinstance(receipt, RawAcquisitionSessionReceipt):
        raise ValueError("Discovery RAW handle exposed an invalid RAW-open receipt")
    return receipt


def _owner_bound_raw_opener(
    acquisition_source: AcquisitionSourceIdentity,
    mass_recalibration: MassRecalibration,
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
                mass_recalibration=mass_recalibration,
                expected_acquisition_source=acquisition_source,
            ),
        )

    return _open


def discover_sample_for_process_job(
    job: DiscoverySampleProcessJob,
    *,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None],
    raw_open_receipt_observer: Callable[[RawAcquisitionSessionReceipt | None], None],
) -> _DiscoverySampleResult:
    """Discover one process job's sample through the source-bound RAW opener."""
    return _discover_completed_sample(
        job.raw_path,
        settings=job.settings,
        dll_dir=job.dll_dir,
        raw_opener=_owner_bound_raw_opener(
            job.acquisition_source,
            job.settings.mass_recalibration,
        ),
        timing_recorder=timing_recorder,
        measurement_observer=measurement_observer,
        raw_open_receipt_observer=raw_open_receipt_observer,
        run_spec=job.run_spec,
        acquisition_input=job.acquisition_input,
    )
