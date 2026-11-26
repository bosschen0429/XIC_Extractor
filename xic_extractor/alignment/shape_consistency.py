"""Whether a group's members share one elution shape across injections.

A compound elutes with the same shape in every injection, give or take
intensity. Background plateaus, noise texture and tail extensions do not: each
injection shows a different slice of differently shaped signal. Each member's
canonical signal is aligned at its apex (for one band, the apex of the whole
envelope, since members may be either half) and scaled to its height; the group's
consistency is the median pairwise correlation. A group below the floor does
not enter the Matrix. The floor and window are product policy chosen on the
8RAW background rows and true peaks reviewed in #1283.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations

import numpy as np

from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.physical_peak_evaluation import peak_envelope

SHAPE_HALF_WINDOW_MIN = 0.6
SHAPE_GRID_POINTS = 61
MIN_SHAPE_CONSISTENCY = 0.6
_GRID = np.linspace(-SHAPE_HALF_WINDOW_MIN, SHAPE_HALF_WINDOW_MIN, SHAPE_GRID_POINTS)


def _profile(peak: PeakObservation, *, one_band: bool) -> np.ndarray | None:
    evaluation = peak.physical_evaluation
    prepared = None if evaluation is None else evaluation.retained_prepared_signal
    if evaluation is None or prepared is None:
        return None
    rt = np.asarray(prepared.rt, dtype=float)
    signal = np.asarray(prepared.smoothed, dtype=float)
    apex = peak.rt_apex_min
    selected = evaluation.answer.selected_segment
    if one_band and selected is not None:
        envelope = peak_envelope(evaluation.segment_universe, selected)
        span = slice(envelope[0].start_index, envelope[-1].end_index)
        apex = float(rt[span][int(np.argmax(signal[span]))])
    profile = np.interp(apex + _GRID, rt, signal, left=0.0, right=0.0)
    top = float(profile.max())
    return profile / top if top > 0.0 else None


def shape_consistency(group: CrossSamplePeakGroupHypothesis) -> float | None:
    """Median pairwise correlation of member shapes; ``None`` below two shapes."""
    profiles = [
        profile
        for owner in group.owners
        if (profile := _profile(owner.peak, one_band=group.one_band)) is not None
    ]
    correlations = [
        float(np.corrcoef(first, second)[0, 1])
        for first, second in combinations(profiles, 2)
        if np.std(first) > 0.0 and np.std(second) > 0.0
    ]
    return float(np.median(correlations)) if correlations else None


def reject_inconsistent_groups(
    groups: Sequence[CrossSamplePeakGroupHypothesis],
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Reject established groups whose members do not share one elution shape."""
    result = []
    for group in groups:
        consistency = (
            shape_consistency(group) if group.disposition == "established" else None
        )
        if consistency is not None and consistency < MIN_SHAPE_CONSISTENCY:
            group = replace(
                group,
                disposition="rejected",
                evidence=f"{group.evidence};inconsistent_shape:{consistency:.2f}",
            )
        result.append(group)
    return tuple(result)
