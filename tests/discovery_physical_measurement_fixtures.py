"""Helpers shared by test_discovery_physical_measurement and other test modules."""

from __future__ import annotations

import numpy as np

from tests.discovery_ms1_capture_fixtures import (
    _OwnerBoundBatchRaw as _BatchRaw,
)
from xic_extractor.peak_detection.physical_peak_population import (
    measure_physical_peak_population,
)


class _OwnerBoundBatchRaw(_BatchRaw):
    def __init__(self, source, **kwargs) -> None:
        super().__init__(source, **kwargs)
        self.rt = np.linspace(7.4, 8.2, 81)
        self.intensity = 100.0 * np.exp(-0.5 * ((self.rt - 7.8) / 0.06) ** 2)


def measured_peak_evaluations(capture, profile):
    """Each Discovery peak's measurement, recomputed by the physical owner.

    The trace is the last one Discovery acquired in the window that measured
    the peak: centred on ``peak_mz`` and holding a segment at its apex.
    """
    acquired = [
        trace for batch in capture.acquisition_batches for trace in batch.traces
    ]
    evaluations = []
    for peak in capture.peaks:
        for trace in reversed(acquired):
            request = trace.coordinate.request
            if request.mz != peak.peak_mz:
                continue
            population = measure_physical_peak_population(
                trace.trace.rt,
                trace.trace.intensity,
                context_rt_start_min=request.rt_min,
                context_rt_end_min=request.rt_max,
                profile=profile,
            )
            segment = population.segment_at(peak.ms1_apex_rt)
            if segment is not None and segment.apex_rt_min == peak.ms1_apex_rt:
                evaluations.append(population.measure_segment(segment))
                break
        else:
            raise AssertionError(f"{peak.peak_id}: no acquired trace measures it")
    return tuple(evaluations)
