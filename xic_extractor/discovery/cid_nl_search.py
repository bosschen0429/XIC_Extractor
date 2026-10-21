"""CID-NL sample search: selected losses -> bounded MS1 work -> method evidence."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from xic_extractor.acquisition_contract import BatchXICAdapter
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.discovery.grouping import group_discovery_seeds
from xic_extractor.discovery.ms1_capture import (
    _DiscoveryMs1AcquisitionResult,
    _DiscoveryMs1Measurements,
    capture_ms1_candidates,
)
from xic_extractor.discovery.ms2_seeds import MS2ScanSource, collect_strict_nl_seeds
from xic_extractor.execution.timing import TimingRecorder
from xic_extractor.processing_identity import AcquisitionSourceIdentity
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec


class CidNlSearchSource(BatchXICAdapter, MS2ScanSource, Protocol):
    def ms1_centroid_mz(
        self, rt: float, mz_min: float, mz_max: float
    ) -> float | None: ...


def search_cid_nl_sample(
    raw: CidNlSearchSource,
    *,
    raw_file: Path,
    method: CidNlDiscoveryMethodSpec,
    acquisition_source: AcquisitionSourceIdentity,
    run_spec_id: str,
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
        "discover.group_seeds", sample_stem=raw_file.stem
    ) as stage:
        groups = group_discovery_seeds(seeds, settings=settings)
        stage.metrics["group_count"] = len(groups)
    with timing_recorder.stage(
        "discover.ms1_capture", sample_stem=raw_file.stem
    ) as stage:
        result = capture_ms1_candidates(
            raw,
            groups,
            acquisition_source=acquisition_source,
            run_spec_id=run_spec_id,
            settings=settings,
            measurement_observer=measurement_observer,
            signal_processing_profile=signal_processing_profile,
            ms1_centroid_mz=raw.ms1_centroid_mz,
        )
        stage.metrics["candidate_count"] = len(result.candidates)
        return result
