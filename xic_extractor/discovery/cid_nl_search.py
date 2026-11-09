"""CID-NL sample search: strict NL events -> ion channels -> measured peaks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.ion_channels import build_discovery_channels
from xic_extractor.discovery.ms1_capture import (
    DiscoveryMs1Source,
    _DiscoveryMs1AcquisitionResult,
    _DiscoveryMs1Measurements,
    capture_ms1_peaks,
)
from xic_extractor.discovery.ms2_seeds import (
    AcquisitionSpanMS2Source,
    collect_strict_nl_seeds,
)
from xic_extractor.discovery.strict_seed_receipts import read_parent_ms1_observations
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
        "discover.ms2_seeds", sample_stem=raw_file.stem
    ) as stage:
        seeds = collect_strict_nl_seeds(raw, raw_file=raw_file, settings=settings)
        stage.metrics["seed_count"] = len(seeds)
    with timing_recorder.stage(
        "discover.ion_channels", sample_stem=raw_file.stem
    ) as stage:
        # An inferred precursor's parent-MS1 ion decides its channel.
        measured = read_parent_ms1_observations(
            raw, seeds, tolerance_ppm=settings.precursor_mz_tolerance_ppm
        )
        channels = build_discovery_channels(measured, settings=settings)
        stage.metrics["channel_count"] = len(channels)
    with timing_recorder.stage(
        "discover.ms1_capture", sample_stem=raw_file.stem
    ) as stage:
        result = capture_ms1_peaks(
            raw,
            channels,
            acquisition_source=acquisition_source,
            settings=settings,
            measurement_observer=measurement_observer,
            signal_processing_profile=signal_processing_profile,
            ms1_centroid_mz=raw.ms1_centroid_mz,
        )
        stage.metrics["peak_count"] = len(result.peaks)
        stage.metrics["event_count"] = len(result.events)
        return result
