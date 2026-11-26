from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

from xic_extractor.configuration.execution_profile import ExecutionProfile
from xic_extractor.configuration.mass_recalibration import (
    MassRecalibration,
    MassRecalibrationResult,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
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
from xic_extractor.discovery.models import (
    DiscoveryBatchOutputs,
    DiscoveryPeak,
    DiscoveryRunOutputs,
    EventAttachment,
)
from xic_extractor.discovery.ms1_capture import (
    _DiscoveryMs1Measurements,
)
from xic_extractor.discovery.peak_binding import DiscoveryPeakPhysicalBinding
from xic_extractor.discovery.priority import order_discovery_peak_bindings
from xic_extractor.discovery.runtime_measurements import (
    record_discovery_ms1_measurements,
    record_raw_measurements,
    record_unavailable_raw_open,
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
    ProcessingAcquisitionInput,
    ProcessingRunSpec,
)


class DiscoveryRawHandle(CidNlSearchSource, Protocol):
    """RAW reader surface needed by single-file discovery orchestration."""

    @property
    def mass_recalibration_result(self) -> MassRecalibrationResult: ...


RawOpener = Callable[[Path, Path], AbstractContextManager[DiscoveryRawHandle]]


@dataclass(frozen=True)
class _DiscoverySampleResult:
    peaks: tuple[DiscoveryPeak, ...]
    attachments: tuple[EventAttachment, ...]
    ms1_measurements: _DiscoveryMs1Measurements | None
    physical_bindings: tuple[DiscoveryPeakPhysicalBinding, ...]
    mass_recalibration_result: MassRecalibrationResult = MassRecalibrationResult("none")


@lru_cache(maxsize=2)
def _bound_background_library(content: str) -> BlankBackgroundLibrary:
    return deserialize_background_library(content)


def _apply_blank_qualification_if_enabled(
    bindings: tuple[DiscoveryPeakPhysicalBinding, ...],
    *,
    settings: DiscoverySettings,
    timing_recorder: TimingRecorder,
    raw_stem: str,
    acquisition_input: ProcessingAcquisitionInput,
) -> tuple[DiscoveryPeakPhysicalBinding, ...]:
    if not settings.enable_blank_qualification:
        return bindings
    with timing_recorder.stage(
        "discover.blank_qualification",
        raw_stem=raw_stem,
        metrics={"peak_count": len(bindings)},
    ) as stage:
        content = acquisition_input.blank_library_content
        library = None if content is None else _bound_background_library(content)
        qualified = apply_blank_qualification_to_peaks(
            bindings,
            library,
            ppm_tol=settings.blank_ppm_tol,
            fold_change_threshold=settings.blank_fold_change_threshold,
        )
        stage.metrics["excluded_background_count"] = sum(
            binding.peak.status == "blank" for binding in qualified
        )
        return qualified


def run_discovery(
    run_spec: ProcessingRunSpec,
    *,
    output_dir: Path,
    dll_dir: Path,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryRunOutputs:
    raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    (acquisition_input,) = run_spec.acquisition_inputs
    discovery_result = _discover_sample(
        settings=run_spec.settings,
        signal_processing_profile=run_spec.signal_processing_profile,
        dll_dir=dll_dir,
        raw_opener=raw_opener,
        timing_recorder=recorder,
        acquisition_input=acquisition_input,
    )
    raise_if_discovery_cancelled(should_cancel)
    publication = output_publication.publish_discovery_batch_sample(
        acquisition_input.raw_path,
        discovery_result.peaks,
        discovery_result.attachments,
        output_dir=output_dir,
        sample_output_dir=output_dir,
        acquisition_source=acquisition_input.acquisition_source,
        timing_recorder=recorder,
        mass_recalibration_result=discovery_result.mass_recalibration_result,
    )
    receipt_index = output_publication.write_batch_index(
        output_dir / "discovery_batch_index.csv",
        [publication.index_row],
        timing_recorder=recorder,
    )
    return replace(publication.outputs, batch_index_csv=receipt_index)


def run_discovery_batch(
    run_spec: ProcessingRunSpec,
    *,
    output_dir: Path,
    dll_dir: Path,
    raw_opener: RawOpener | None = None,
    timing_recorder: TimingRecorder | None = None,
    execution: ExecutionProfile,
    executor_factory: Callable[..., Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DiscoveryBatchOutputs:
    raise_if_discovery_cancelled(should_cancel)
    recorder = timing_recorder or TimingRecorder.disabled("discovery")
    run_inputs = run_spec.acquisition_inputs
    max_workers = min(len(run_inputs), execution.parallel_workers)
    if execution.backend == "process" and max_workers > 1:
        if raw_opener is not None:
            raise ValueError(
                "parallel Discovery owns RAW handles inside spawned workers; "
                "raw_opener injection requires in-process execution"
            )
        return run_discovery_batch_process(
            run_spec,
            output_dir=output_dir,
            dll_dir=dll_dir,
            timing_recorder=recorder,
            max_workers=max_workers,
            executor_factory=executor_factory,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            discover_sample=discover_sample_for_process_job,
        )

    index_rows: list[dict[str, str]] = []
    per_sample: list[DiscoveryRunOutputs] = []
    for sample_index, acquisition_input in enumerate(run_inputs):
        raise_if_discovery_cancelled(should_cancel)
        raw_stem = acquisition_input.raw_path.stem
        discovery_result = _discover_sample(
            settings=run_spec.settings,
            signal_processing_profile=run_spec.signal_processing_profile,
            dll_dir=dll_dir,
            raw_opener=raw_opener,
            timing_recorder=recorder,
            acquisition_input=acquisition_input,
        )
        publication = output_publication.publish_discovery_batch_sample(
            acquisition_input.raw_path,
            discovery_result.peaks,
            discovery_result.attachments,
            output_dir=output_dir,
            acquisition_source=acquisition_input.acquisition_source,
            timing_recorder=recorder,
            mass_recalibration_result=discovery_result.mass_recalibration_result,
        )
        per_sample.append(publication.outputs)
        index_rows.append(publication.index_row)
        if progress_callback is not None:
            progress_callback(sample_index + 1, len(run_inputs), raw_stem)

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
    *,
    settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
    dll_dir: Path,
    raw_opener: RawOpener | None,
    timing_recorder: TimingRecorder,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    if raw_opener is None:
        opener = _owner_bound_raw_opener(
            acquisition_input.acquisition_source,
            settings.mass_recalibration,
            timing_recorder,
        )
    else:
        opener = raw_opener
    return _discover_completed_sample_with_runtime_projection(
        settings=settings,
        signal_processing_profile=signal_processing_profile,
        dll_dir=dll_dir,
        raw_opener=opener,
        timing_recorder=timing_recorder,
        acquisition_input=acquisition_input,
    )


def _discover_completed_sample_with_runtime_projection(
    *,
    settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    """Return peaks ready for publication after projecting owner metrics."""

    raw_path = acquisition_input.raw_path

    def _project_acquisition(result: _DiscoverySampleResult) -> None:
        record_discovery_ms1_measurements(
            timing_recorder,
            result.ms1_measurements,
            raw_path.stem,
        )

    try:
        result = _discover_completed_sample(
            settings=settings,
            signal_processing_profile=signal_processing_profile,
            dll_dir=dll_dir,
            raw_opener=raw_opener,
            timing_recorder=timing_recorder,
            acquisition_observer=_project_acquisition,
            acquisition_input=acquisition_input,
        )
    finally:
        record_unavailable_raw_open(timing_recorder, raw_path.stem)
    return result


def _discover_completed_sample(
    *,
    settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    acquisition_observer: Callable[[_DiscoverySampleResult], None] | None = None,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    """Acquire one RAW, close it, then complete its peak facts."""

    acquired = _acquire_discovery_sample(
        settings=settings,
        signal_processing_profile=signal_processing_profile,
        dll_dir=dll_dir,
        raw_opener=raw_opener,
        timing_recorder=timing_recorder,
        measurement_observer=measurement_observer,
        acquisition_input=acquisition_input,
    )
    if acquisition_observer is not None:
        acquisition_observer(acquired)
    physical_bindings = order_discovery_peak_bindings(
        _apply_blank_qualification_if_enabled(
            acquired.physical_bindings,
            settings=settings,
            timing_recorder=timing_recorder,
            raw_stem=acquisition_input.raw_path.stem,
            acquisition_input=acquisition_input,
        ),
        settings=settings,
    )
    return _DiscoverySampleResult(
        peaks=tuple(binding.peak for binding in physical_bindings),
        attachments=acquired.attachments,
        ms1_measurements=acquired.ms1_measurements,
        physical_bindings=physical_bindings,
        mass_recalibration_result=acquired.mass_recalibration_result,
    )


def _acquire_discovery_sample(
    *,
    settings: DiscoverySettings,
    signal_processing_profile: SignalProcessingProfile,
    dll_dir: Path,
    raw_opener: RawOpener,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
    acquisition_input: ProcessingAcquisitionInput,
) -> _DiscoverySampleResult:
    raw_path = acquisition_input.raw_path
    raw_stem = raw_path.stem
    with raw_opener(raw_path, dll_dir) as raw:
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
                settings=settings,
                acquisition_source=acquisition_input.acquisition_source,
                signal_processing_profile=signal_processing_profile,
                timing_recorder=timing_recorder,
                measurement_observer=_capture_acquired_measurements,
            )
            completed = True
            return _DiscoverySampleResult(
                peaks=acquisition_result.peaks,
                attachments=acquisition_result.attachments,
                ms1_measurements=acquisition_result.measurements,
                physical_bindings=acquisition_result.physical_bindings,
                mass_recalibration_result=raw.mass_recalibration_result,
            )
        finally:
            if not completed:
                if acquired_measurements is None:
                    record_raw_measurements(timing_recorder, raw, raw_stem)
                else:
                    try:
                        record_discovery_ms1_measurements(
                            timing_recorder,
                            acquired_measurements,
                            raw_stem,
                        )
                    except Exception as diagnostic_exc:
                        timing_recorder.observe(
                            "diagnostic_projection_failure_count",
                            value=1,
                            scope=raw_stem,
                            unit="failures",
                            source="xic_extractor.discovery.pipeline",
                            availability="failed",
                            reason=(
                                f"{type(diagnostic_exc).__name__}: {diagnostic_exc}"
                            ),
                        )


def _owner_bound_raw_opener(
    acquisition_source: AcquisitionSourceIdentity,
    mass_recalibration: MassRecalibration,
    timing_recorder: TimingRecorder,
) -> RawOpener:
    """Bind the production reader to its RAW source and runtime observations."""

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
                timing_recorder=timing_recorder,
            ),
        )

    return _open


def discover_sample_for_process_job(
    job: DiscoverySampleProcessJob,
    *,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None],
) -> _DiscoverySampleResult:
    """Discover one process job's sample through the source-bound RAW opener."""
    return _discover_completed_sample(
        settings=job.settings,
        signal_processing_profile=job.signal_processing_profile,
        dll_dir=job.dll_dir,
        raw_opener=_owner_bound_raw_opener(
            job.acquisition_input.acquisition_source,
            job.settings.mass_recalibration,
            timing_recorder,
        ),
        timing_recorder=timing_recorder,
        measurement_observer=measurement_observer,
        acquisition_input=job.acquisition_input,
    )
