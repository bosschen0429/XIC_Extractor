"""Read an MS1 ion from the scans at its measured peak apex."""

from collections.abc import Callable

import numpy as np

from xic_extractor.acquisition_contract import AcquiredTrace
from xic_extractor.mass_accuracy import mz_window, weighted_ion_mz
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation

MS1Centroid = Callable[[float, float, float], tuple[float, float] | None]
"""(apex RT, m/z min, m/z max) -> (m/z, intensity) of the most intense MS1
centroid at that scan."""

APEX_ION_SPREAD_PPM = 5.0
_APEX_ION_SCAN_RADIUS = 1


def read_peak_apex_ion(
    acquired: AcquiredTrace,
    evaluation: PhysicalPeakEvaluation,
    *,
    ms1_centroid: MS1Centroid,
) -> tuple[float | None, list[tuple[float, float]]]:
    """The ion read inside the request at the apex scans, and its readings.

    Each reading is the scan RT and the ion centroid's intensity there.

    Readings spread wider than a few ppm come from two ions and report none.
    """
    request = acquired.coordinate.request
    selected = evaluation.answer.selected_segment
    if evaluation.state != "available" or selected is None:
        return None, []
    rt = acquired.trace.rt
    intensity = acquired.trace.intensity
    if len(rt) == 0:
        return None, []
    apex = int(np.argmin(np.abs(rt - selected.apex_rt_min)))
    low, high = mz_window(request.mz, request.ppm_tol)
    readings = []
    ion_reads = []
    for index in range(
        max(0, apex - _APEX_ION_SCAN_RADIUS),
        min(len(rt), apex + _APEX_ION_SCAN_RADIUS + 1),
    ):
        centroid = ms1_centroid(float(rt[index]), low, high)
        if centroid is not None:
            mz, centroid_intensity = centroid
            readings.append((mz, float(intensity[index])))
            ion_reads.append((float(rt[index]), centroid_intensity))
    return weighted_ion_mz(readings, max_spread_ppm=APEX_ION_SPREAD_PPM), ion_reads
