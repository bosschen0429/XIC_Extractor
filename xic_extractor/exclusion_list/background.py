"""Recurring MS1 ions of a solvent blank and when they elute.

Ions are histogram peaks of centroid m/z on a 0.5 ppm log scale; each centroid
joins the nearest peak within 2.5 ppm. Peak picking on a histogram keeps two
close ions apart where chaining neighbouring centroids would merge them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from xic_extractor.mass_accuracy import log_ppm_scale

INTENSITY_FLOOR = 1e4
BIN_PPM = 0.5
ASSIGN_PPM = 2.5
SMOOTH_BINS = 5
PEAK_BINS = 11
MIN_POINTS = 12
GAP_SCANS = 12
MIN_SCANS = 12
PAD_MIN = 0.5
PERSISTENT_FRACTION = 0.5
TOP_N = 20


@dataclass(frozen=True, slots=True)
class Ms1Centroids:
    """Every MS1 centroid of one blank, ordered by ``scan`` (index into ``scan_rt``)."""

    scan_rt: NDArray[np.float64]
    scan: NDArray[np.int64]
    mz: NDArray[np.float64]
    intensity: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class BackgroundWindow:
    """One RT window of one ion, with the ion's strongest centroid per scan."""

    ion: int
    mz: float
    start: float
    stop: float
    scans: NDArray[np.int64]
    intensities: NDArray[np.float64]

    @property
    def apex(self) -> float:
        return float(self.intensities.max())

    @property
    def area(self) -> float:
        return float(self.intensities.sum())


def find_background_windows(
    centroids: Ms1Centroids, run_end: float, intensity_threshold: float
) -> tuple[BackgroundWindow, ...]:
    """Return eluting windows of every recurring ion, ordered by ion m/z.

    An ion seen in at least half of the scans gets one whole-run window.
    Otherwise runs of at least 12 scans, split at gaps over 12 scans, become
    windows padded by 0.5 min; overlapping windows merge.
    """
    keep = centroids.intensity >= min(INTENSITY_FLOOR, intensity_threshold)
    mz, intensity, scan = (
        centroids.mz[keep],
        centroids.intensity[keep],
        centroids.scan[keep],
    )
    if not mz.size:
        return ()
    label, mz, intensity, scan = _assign_ions(mz, intensity, scan)
    order = np.lexsort((scan, label))
    label, mz, intensity, scan = label[order], mz[order], intensity[order], scan[order]
    bounds = np.r_[0, np.flatnonzero(np.diff(label)) + 1, len(label)]
    rt = centroids.scan_rt
    windows: list[BackgroundWindow] = []
    for begin, end in zip(bounds[:-1], bounds[1:], strict=True):
        if end - begin < MIN_SCANS:  # too few points for any window
            continue
        scans, first = np.unique(scan[begin:end], return_index=True)
        peak = np.maximum.reduceat(intensity[begin:end], first)
        ion = int(label[begin])
        centre = float(np.average(mz[begin:end], weights=intensity[begin:end]))
        if len(scans) / len(rt) >= PERSISTENT_FRACTION:
            windows.append(BackgroundWindow(ion, centre, 0.0, run_end, scans, peak))
            continue
        for low, high in _eluting_runs(scans, rt, run_end):
            windows.append(
                BackgroundWindow(
                    ion,
                    centre,
                    max(0.0, float(rt[scans[low]]) - PAD_MIN),
                    min(run_end, float(rt[scans[high - 1]]) + PAD_MIN),
                    scans[low:high],
                    peak[low:high],
                )
            )
    return tuple(windows)


def whole_run_windows(
    windows: Sequence[BackgroundWindow], run_end: float
) -> tuple[BackgroundWindow, ...]:
    """Merge each ion's windows into one window spanning the run."""
    by_ion: dict[int, list[BackgroundWindow]] = {}
    for window in windows:
        by_ion.setdefault(window.ion, []).append(window)
    return tuple(
        BackgroundWindow(
            ion,
            parts[0].mz,
            0.0,
            run_end,
            np.concatenate([part.scans for part in parts]),
            np.concatenate([part.intensities for part in parts]),
        )
        for ion, parts in by_ion.items()
    )


def top_n_counts(
    centroids: Ms1Centroids,
    windows: Sequence[BackgroundWindow],
    scan_range: tuple[float, float],
) -> NDArray[np.int64]:
    """Count, per window, the scans where its ion is among the 20 most intense.

    Ranking uses only centroids inside ``scan_range``: a method acquiring that
    range never sees ions outside it.
    """
    low, high = scan_range
    inside = (centroids.mz >= low) & (centroids.mz <= high)
    scan_count = len(centroids.scan_rt)
    bounds = np.searchsorted(centroids.scan, np.arange(scan_count + 1))
    threshold = np.zeros(scan_count)  # N-th highest intensity of each scan
    for index in range(scan_count):
        begin, end = bounds[index], bounds[index + 1]
        acquired = centroids.intensity[begin:end][inside[begin:end]]
        cut = len(acquired) - TOP_N
        if cut >= 0:
            threshold[index] = np.partition(acquired, cut)[cut]
    return np.array(
        [int((w.intensities >= threshold[w.scans]).sum()) for w in windows],
        dtype=np.int64,
    )


def _assign_ions(
    mz: NDArray[np.float64],
    intensity: NDArray[np.float64],
    scan: NDArray[np.int64],
) -> tuple[
    NDArray[np.int64], NDArray[np.float64], NDArray[np.float64], NDArray[np.int64]
]:
    log_ppm = log_ppm_scale(mz)
    origin = np.floor(log_ppm.min() / BIN_PPM) - 20
    bins = (np.floor(log_ppm / BIN_PPM) - origin).astype(np.int64)
    smooth = uniform_filter1d(np.bincount(bins).astype(np.float64), SMOOTH_BINS)
    is_peak = (smooth == maximum_filter1d(smooth, PEAK_BINS)) & (
        smooth * SMOOTH_BINS >= MIN_POINTS
    )
    peaks = np.flatnonzero(is_peak)
    if not peaks.size:
        return peaks, mz[:0], intensity[:0], scan[:0]
    peaks = peaks[np.r_[True, np.diff(peaks) > 2]]  # one peak per plateau
    position = np.searchsorted(peaks, bins)
    left = np.clip(position - 1, 0, len(peaks) - 1)
    right = np.clip(position, 0, len(peaks) - 1)
    left_gap, right_gap = np.abs(bins - peaks[left]), np.abs(bins - peaks[right])
    label = np.where(left_gap <= right_gap, left, right)
    assigned = np.minimum(left_gap, right_gap) * BIN_PPM <= ASSIGN_PPM
    return label[assigned], mz[assigned], intensity[assigned], scan[assigned]


def _eluting_runs(
    scans: NDArray[np.int64], rt: NDArray[np.float64], run_end: float
) -> list[tuple[int, int]]:
    cut = np.flatnonzero(np.diff(scans) > GAP_SCANS) + 1
    runs = [
        (int(low), int(high))
        for low, high in zip(np.r_[0, cut], np.r_[cut, len(scans)], strict=True)
        if high - low >= MIN_SCANS
    ]
    merged: list[tuple[int, int]] = []
    for low, high in runs:
        if merged and (
            float(rt[scans[low]]) - PAD_MIN
            <= min(run_end, float(rt[scans[merged[-1][1] - 1]]) + PAD_MIN)
        ):
            merged[-1] = (merged[-1][0], high)
        else:
            merged.append((low, high))
    return merged
