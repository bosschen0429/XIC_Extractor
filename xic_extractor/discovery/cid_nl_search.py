"""CID-NL sample search: strict NL events -> ions -> measured peaks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_grouping import build_discovery_ions
from xic_extractor.discovery.ms1_capture import (
    DiscoveryMs1Source,
    _DiscoveryMs1AcquisitionResult,
    _DiscoveryMs1Measurements,
    capture_ms1_peaks,
)
from xic_extractor.discovery.ms2_events import (
    AcquisitionSpanMS2Source,
    collect_strict_nl_events,
)
from xic_extractor.discovery.strict_event_receipts import read_parent_ms1_observations
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec


class CidNlSearchSource(DiscoveryMs1Source, AcquisitionSpanMS2Source, Protocol):
    def ms1_centroid_mz(
        self, rt: float, mz_min: float, mz_max: float
    ) -> float | None: ...


def search_cid_nl_sample(
    raw: CidNlSearchSource,
    *,
    raw_file: Path,
    method: CidNlDiscoveryMethodSpec,
    acquisition_source: AcquisitionSourceIdentity,
    signal_processing_profile: SignalProcessingProfile,
    timing_recorder: TimingRecorder,
    measurement_observer: Callable[[_DiscoveryMs1Measurements], None] | None = None,
) -> _DiscoveryMs1AcquisitionResult:
    """Execute the bound tags/composition; physical processing knows neither."""
    settings = method.settings
    with timing_recorder.stage(
        "discover.ms2_events", sample_stem=raw_file.stem
    ) as stage:
        events = collect_strict_nl_events(raw, raw_file=raw_file, settings=settings)
        stage.metrics["event_count"] = len(events)
    with timing_recorder.stage(
        "discover.ion_grouping", sample_stem=raw_file.stem
    ) as stage:
        # An inferred precursor's parent-MS1 ion decides which ion it joins.
        measured = read_parent_ms1_observations(
            raw, events, tolerance_ppm=settings.precursor_mz_tolerance_ppm
        )
        ions = build_discovery_ions(measured, settings=settings)
        stage.metrics["ion_count"] = len(ions)
    with timing_recorder.stage(
        "discover.ms1_capture", sample_stem=raw_file.stem
    ) as stage:
        result = capture_ms1_peaks(
            raw,
            ions,
            acquisition_source=acquisition_source,
            settings=settings,
            measurement_observer=measurement_observer,
            signal_processing_profile=signal_processing_profile,
            ms1_centroid_mz=raw.ms1_centroid_mz,
        )
        stage.metrics["peak_count"] = len(result.peaks)
        stage.metrics["event_count"] = len(result.attachments)
        return result
