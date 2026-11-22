"""Shared RAW source protocol and immutable trace snapshots for CID-NL acquisition."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Protocol

import numpy as np

from xic_extractor.acquisition.ms2_windows import MS2ScanSource
from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
)
from xic_extractor.discovery.models import DiscoveryEvent
from xic_extractor.discovery.ms2_events import discovery_events_from_scan
from xic_extractor.discovery.settings import DiscoverySettings
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.precursor_evidence import MS1MassWindowSource

_ADAPTER_NAMESPACE = "xic_extractor.untargeted_cid_nl_real_data_adapter"


class CidNlAcquisitionError(ValueError):
    """Raised when real observations cannot close one Untargeted subject."""


class CidNlRawSource(
    BatchXICAdapter,
    MS2ScanSource,
    MS1MassWindowSource,
    Protocol,
):
    def acquisition_rt_bounds(self) -> tuple[float, float]: ...


def read_direct_cid_nl_seeds(
    raw: MS2ScanSource,
    *,
    raw_file: Path,
    settings: DiscoverySettings,
    rt_min: float,
    rt_max: float,
) -> Iterator[DiscoveryEvent]:
    """Read a local RAW window through the canonical scan-level method."""
    seen = set()
    for event in raw.iter_ms2_scans(rt_min, rt_max):
        if event.parse_error is not None or event.scan is None:
            raise CidNlAcquisitionError(
                f"{raw_file}: CID-NL MS2 acquisition did not complete at scan "
                f"{event.scan_number}: {event.parse_error or 'missing spectrum'}"
            )
        scan = event.scan
        if scan.scan_number in seen:
            raise CidNlAcquisitionError(
                f"{raw_file}: duplicate CID-NL acquisition scan {scan.scan_number}"
            )
        seen.add(scan.scan_number)
        yield from (
            seed
            for seed in discovery_events_from_scan(
                scan, raw_file=raw_file, settings=settings
            )
            if seed.supports_direct_nl
        )


def _freeze_acquisition(acquisition: TraceAcquisitionBatch) -> TraceAcquisitionBatch:
    frozen_traces: list[AcquiredTrace] = []
    for acquired in acquisition.traces:
        rt = np.array(acquired.trace.rt, dtype=float, copy=True)
        intensity = np.array(acquired.trace.intensity, dtype=float, copy=True)
        rt.setflags(write=False)
        intensity.setflags(write=False)
        frozen_traces.append(
            replace(
                acquired,
                trace=type(acquired.trace)(rt=rt, intensity=intensity),
            )
        )
    return replace(acquisition, traces=tuple(frozen_traces))


_within_ppm = within_ppm


__all__ = ["CidNlAcquisitionError", "CidNlRawSource"]
