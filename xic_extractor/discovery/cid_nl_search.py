"""CID-NL sample Discovery: strict NL events -> ions -> measured peaks -> part file."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path
from typing import Protocol, cast

from xic_extractor.configuration.mass_recalibration import MassRecalibrationResult
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.blank_qualification import (
    BlankBackgroundLibrary,
    deserialize_background_library,
)
from xic_extractor.discovery.event_evidence import ranked_neutral_losses
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    BlankBackgroundTest,
    DiscoveryMs1Source,
    Ms1Capture,
    capture_ms1_peaks,
)
from xic_extractor.discovery.ms2_events import (
    AcquisitionSpanMS2Source,
    collect_strict_nl_events,
    read_parent_ms1_observations,
)
from xic_extractor.discovery.output_publication import (
    PublishedDiscoverySample,
    publish_discovery_batch_sample,
)
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import ProcessingAcquisitionInput


class CidNlSearchSource(DiscoveryMs1Source, AcquisitionSpanMS2Source, Protocol):
    def ms1_centroid(
        self, rt: float, mz_min: float, mz_max: float
    ) -> tuple[float, float] | None: ...


class DiscoveryRawHandle(CidNlSearchSource, Protocol):
    """RAW reader surface needed to discover one sample."""

    @property
    def mass_recalibration_result(self) -> MassRecalibrationResult: ...


RawOpener = Callable[[Path, Path], AbstractContextManager[DiscoveryRawHandle]]


@dataclass(frozen=True)
class DiscoverySampleJob:
    """One sample of a Discovery batch; picklable for process workers."""

    sample_index: int
    output_dir: Path
    settings: DiscoverySettings
    signal_processing_profile: SignalProcessingProfile
    dll_dir: Path
    acquisition_input: ProcessingAcquisitionInput


def discover_sample(
    job: DiscoverySampleJob,
    *,
    timing_recorder: TimingRecorder,
    raw_opener: RawOpener | None = None,
) -> PublishedDiscoverySample:
    """Open one RAW, search it, close it, then write the sample's part file."""
    settings = job.settings
    acquisition_input = job.acquisition_input
    raw_path = acquisition_input.raw_path
    opener = raw_opener or partial(
        _open_source_bound_raw, job=job, timing_recorder=timing_recorder
    )
    try:
        with opener(raw_path, job.dll_dir) as raw:
            try:
                capture = search_cid_nl_sample(
                    raw,
                    raw_file=raw_path,
                    settings=settings,
                    acquisition_source=acquisition_input.acquisition_source,
                    signal_processing_profile=job.signal_processing_profile,
                    timing_recorder=timing_recorder,
                    is_blank=_blank_background_test(settings, acquisition_input),
                )
            except Exception:
                _observe_unfinished_xic_reads(timing_recorder, raw, raw_path.stem)
                raise
            mass_recalibration_result = raw.mass_recalibration_result
    finally:
        _observe_unrecorded_raw_open(timing_recorder, raw_path.stem)
    return publish_discovery_batch_sample(
        raw_path,
        capture.peaks,
        capture.attachments,
        output_dir=job.output_dir,
        sample_index=job.sample_index,
        neutral_loss_by_tag=ranked_neutral_losses(settings),
        acquisition_source=acquisition_input.acquisition_source,
        timing_recorder=timing_recorder,
        mass_recalibration_result=mass_recalibration_result,
    )


def search_cid_nl_sample(
    raw: CidNlSearchSource,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    acquisition_source: AcquisitionSourceIdentity,
    signal_processing_profile: SignalProcessingProfile,
    timing_recorder: TimingRecorder,
    is_blank: BlankBackgroundTest | None = None,
) -> Ms1Capture:
    """Execute the bound tags/composition; physical processing knows neither."""
    with timing_recorder.stage(
        "discover.ms2_events", sample_stem=raw_file.stem
    ) as stage:
        events = collect_strict_nl_events(raw, raw_file=raw_file, settings=settings)
        stage.metrics["event_count"] = len(events)
    with timing_recorder.stage(
        "discover.ion_grouping", sample_stem=raw_file.stem
    ) as stage:
        measured = read_parent_ms1_observations(
            raw, events, tolerance_ppm=settings.precursor_mz_tolerance_ppm
        )
        ions = build_discovery_ions(measured, settings=settings)
        stage.metrics["ion_count"] = len(ions)
    with timing_recorder.stage(
        "discover.ms1_capture", sample_stem=raw_file.stem
    ) as stage:
        capture = capture_ms1_peaks(
            raw,
            ions,
            acquisition_source=acquisition_source,
            settings=settings,
            signal_processing_profile=signal_processing_profile,
            ms1_centroid=raw.ms1_centroid,
            is_blank=is_blank,
        )
        batches = capture.acquisition_batches
        stage.metrics.update(
            peak_count=len(capture.peaks),
            event_count=len(capture.attachments),
            logical_xic_request_count=sum(
                batch.logical_xic_request_count for batch in batches
            ),
            xic_batch_count=len(batches),
            adapter_chromatogram_call_count=sum(
                batch.adapter_chromatogram_call_count for batch in batches
            ),
            trace_point_count=sum(batch.trace_point_count for batch in batches),
        )
        return capture


def _open_source_bound_raw(
    raw_path: Path,
    dll_dir: Path,
    *,
    job: DiscoverySampleJob,
    timing_recorder: TimingRecorder,
) -> AbstractContextManager[DiscoveryRawHandle]:
    """The production reader, bound to the RAW revision the run spec fingerprinted."""
    from xic_extractor.raw_reader import open_raw

    return cast(
        AbstractContextManager[DiscoveryRawHandle],
        open_raw(
            raw_path,
            dll_dir,
            mass_recalibration=job.settings.mass_recalibration,
            expected_acquisition_source=job.acquisition_input.acquisition_source,
            timing_recorder=timing_recorder,
        ),
    )


def _observe_unfinished_xic_reads(
    timing_recorder: TimingRecorder, raw: CidNlSearchSource, sample_stem: str
) -> None:
    """A search that did not finish reports the vendor calls it made; the XIC
    counts it never completed stay unavailable with the reason."""
    source = "xic_extractor.discovery.cid_nl_search"
    timing_recorder.observe(
        "adapter_chromatogram_call_count",
        value=raw.raw_chromatogram_call_count,
        scope=sample_stem,
        unit="calls",
        source=source,
    )
    for metric, unit in (
        ("logical_xic_request_count", "requests"),
        ("xic_batch_count", "batches"),
        ("trace_point_count", "points"),
    ):
        timing_recorder.observe(
            metric,
            scope=sample_stem,
            unit=unit,
            source=source,
            availability="unavailable",
            reason="Discovery stopped before its XIC reads were counted",
        )


def _observe_unrecorded_raw_open(
    timing_recorder: TimingRecorder, sample_stem: str
) -> None:
    """The reader records each successful open; a sample without one says so."""
    if any(
        row["metric"] == "raw_open_count" and row["scope"] == sample_stem
        for row in timing_recorder.measurement_observations
    ):
        return
    timing_recorder.observe(
        "raw_open_count",
        scope=sample_stem,
        unit="opens",
        source="xic_extractor.discovery.cid_nl_search",
        availability="unavailable",
        reason="RAW opener recorded no successful open",
    )


@lru_cache(maxsize=2)
def _background_library(content: str) -> BlankBackgroundLibrary:
    return deserialize_background_library(content)


def _blank_background_test(
    settings: DiscoverySettings, acquisition_input: ProcessingAcquisitionInput
) -> BlankBackgroundTest | None:
    content = acquisition_input.blank_library_content
    if not settings.enable_blank_qualification or content is None:
        return None
    return partial(
        _background_library(content).is_background,
        ppm_tol=settings.blank_ppm_tol,
        fold_change_threshold=settings.blank_fold_change_threshold,
    )
