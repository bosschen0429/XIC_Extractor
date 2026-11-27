"""Place early peaks of an under-equilibrated injection where they would elute.

After a short method, the next injection starts on a column that has not
re-equilibrated, and its early-eluting compounds come early:
shift(RT, injection) = A × g(RT). The general map absorbs the shift where the
injection has anchors; its early segment usually has none, so those peaks keep
the whole shift. g was measured on the 60 min dna_dr gradient; another gradient
needs it measured again. See docs/product/rt-calibration.md.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
    from xic_extractor.injection_timeline import InjectionSequence

SEGMENT_END_MIN = 8.5
_DECAY_MIN = 5.26
_SHAPE_BEFORE_END = (
    (2.6, 0.0),
    (2.8, 0.36),
    (3.4, 0.95),
    (4.0, 2.1),
    (5.0, 2.6),
    (5.5, 1.8),
    (6.5, 1.4),
    (7.5, 1.0),
    (8.5, 1.0),
)
_SHAPE_MAX = max(value for _, value in _SHAPE_BEFORE_END)
_NEIGHBOURS = 3
_MIN_SHIFT_MIN = 10 / 60
_JITTER_MULTIPLIER = 5
_BISECTION_STEPS = 30


def early_segment_shape(rt_min: float) -> float:
    """g: the shift at an elution time, relative to the shift at the segment end."""
    if rt_min >= SEGMENT_END_MIN:
        return math.exp(-(rt_min - SEGMENT_END_MIN) / _DECAY_MIN)
    if rt_min <= _SHAPE_BEFORE_END[0][0]:
        return 0.0
    right = bisect_right(_SHAPE_BEFORE_END, rt_min, key=lambda knot: knot[0])
    (left_rt, left_value), (right_rt, right_value) = _SHAPE_BEFORE_END[
        right - 1 : right + 1
    ]
    return left_value + (rt_min - left_rt) / (right_rt - left_rt) * (
        right_value - left_value
    )


@dataclass(frozen=True, slots=True)
class EarlySegmentShift:
    """Affected injections' shift at the segment end and unaffected neighbours."""

    amplitude_min: Mapping[str, float] = field(default_factory=dict)
    neighbours: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def elution_rt_min(self, sample_stem: str, observed_rt: float) -> float | None:
        """The RT this peak would have without the shift; None where none applies.

        Solves observed = elution + A × g(elution): g changes steeply before the
        segment end, so evaluating it at the observed RT would undo too little.
        """
        amplitude = self.amplitude_min.get(sample_stem)
        if amplitude is None or observed_rt >= SEGMENT_END_MIN:
            return None
        low, high = observed_rt, observed_rt - amplitude * _SHAPE_MAX
        for _ in range(_BISECTION_STEPS):
            middle = (low + high) / 2
            if middle + amplitude * early_segment_shape(middle) < observed_rt:
                low = middle
            else:
                high = middle
        return (low + high) / 2


def measure_early_segment_shift(
    calibration: BatchRtCalibration, injections: InjectionSequence
) -> EarlySegmentShift:
    """Find injections whose earliest standard came early, fading with RT.

    A standard's deviation is its observed RT minus the median of the same
    standard in up to three injections on each side. An injection is affected
    when its earliest standard is earlier than both ten seconds and five times
    the batch's median absolute deviation, and its next standard moved the same
    way by a fraction closer to the shape's decay than to a uniform shift.
    With fewer than two standards, no injection is affected.
    """
    injections = injections.restrict(calibration.samples)
    neighbours = {
        stem: injections.around(stem, _NEIGHBOURS) for stem in injections.stems
    }
    observed: dict[str, dict[str, float]] = defaultdict(dict)
    references: dict[str, list[float]] = defaultdict(list)
    for stem in injections.stems:
        for label, rt, reference, _ in calibration.samples[stem].standard_residuals:
            observed[label][stem] = rt
            references[label].append(reference)
    labels = sorted(references, key=lambda label: (median(references[label]), label))
    if len(labels) < 2:
        return EarlySegmentShift()
    first_shape = early_segment_shape(median(references[labels[0]]))
    first = _deviations(observed[labels[0]], neighbours)
    if not first_shape or not first:
        return EarlySegmentShift()
    second = _deviations(observed[labels[1]], neighbours)
    limit = max(
        _MIN_SHIFT_MIN,
        _JITTER_MULTIPLIER * median(abs(value) for value in first.values()),
    )
    largest_kept_fraction = (
        1 + early_segment_shape(median(references[labels[1]])) / first_shape
    ) / 2
    amplitude = {
        stem: shift / first_shape
        for stem, shift in first.items()
        if shift < -limit
        and (later := second.get(stem)) is not None
        and later < 0
        and later / shift <= largest_kept_fraction
    }
    return EarlySegmentShift(
        amplitude,
        {
            stem: tuple(other for other in neighbours[stem] if other not in amplitude)
            for stem in amplitude
        },
    )


def _deviations(
    values: Mapping[str, float], neighbours: Mapping[str, tuple[str, ...]]
) -> dict[str, float]:
    result = {}
    for stem, peers in neighbours.items():
        near = [values[other] for other in peers if other in values]
        if stem in values and len(near) >= _NEIGHBOURS:
            result[stem] = values[stem] - median(near)
    return result
