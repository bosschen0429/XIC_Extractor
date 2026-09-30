from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from xic_extractor.alignment.drift_evidence import (
    KnownIstdReference,
    mint_known_istd_reference_from_measurements,
)
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.models import Target


def extract_batch_known_istd_reference(
    targets: Sequence[Target],
    raw_paths_by_sample: Mapping[str, Path],
    dll_dir: Path,
    peak_config: ExtractionConfig,
    *,
    source_identifier: str = "<in-memory>",
    source_sha256: str = "",
) -> KnownIstdReference:
    """Extract ISTD peaks across raw files and construct in-memory reference."""
    from xic_extractor.raw_reader import open_raw
    from xic_extractor.signal_processing import find_peak_and_area
    from xic_extractor.xic_models import XICRequest

    istd_targets = tuple(t for t in targets if t.is_istd)
    if not istd_targets:
        raise ValueError("No ISTD targets (is_istd=True) found in targets")

    requests = tuple(
        XICRequest(
            mz=target.mz,
            rt_min=target.rt_min,
            rt_max=target.rt_max,
            ppm_tol=target.ppm_tol,
        )
        for target in istd_targets
    )

    coordinates: dict[tuple[str, str], float] = {}
    for sample_stem, raw_path in sorted(raw_paths_by_sample.items()):
        with open_raw(raw_path, dll_dir=dll_dir) as raw:
            traces = raw.extract_xic_many(requests)
            for target, trace in zip(istd_targets, traces, strict=True):
                peak_result = find_peak_and_area(
                    trace.rt,
                    trace.intensity,
                    peak_config,
                    evidence_role="ISTD",
                )
                if peak_result.peak is not None and peak_result.peak.rt > 0:
                    peak_rt = float(peak_result.peak.rt)
                    coordinates[(target.label, sample_stem)] = peak_rt

    return mint_known_istd_reference_from_measurements(
        istd_targets,
        coordinates,
        source_identifier=source_identifier,
        source_sha256=source_sha256,
    )
