"""Shared RAW source protocol and immutable trace snapshots for CID-NL acquisition."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

import numpy as np

from xic_extractor.acquisition.ms2_windows import MS2ScanSource
from xic_extractor.acquisition_contract import (
    AcquiredTrace,
    BatchXICAdapter,
    TraceAcquisitionBatch,
)
from xic_extractor.discovery.strict_event_receipts import StrictEventReceiptRaw
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.precursor_evidence import MS1MassWindowSource

_ADAPTER_NAMESPACE = "xic_extractor.untargeted_cid_nl_real_data_adapter"


class CidNlAcquisitionError(ValueError):
    """Raised when real observations cannot close one Untargeted subject."""


class CidNlRawSource(
    BatchXICAdapter,
    MS2ScanSource,
    StrictEventReceiptRaw,
    MS1MassWindowSource,
    Protocol,
):
    def acquisition_rt_bounds(self) -> tuple[float, float]: ...


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
