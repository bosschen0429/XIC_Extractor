"""Decide which measured Discovery peaks observe one ion's peak.

Neighbouring ion XIC windows overlap, so one ion can be measured in several
windows. A trace point sums the centroids inside its window, so two windows
that contain the same centroids read exactly the same intensity. A centroid
near a window edge can fall inside one window only: that scan then reads 0 in
the other window, or a different value when the other window still holds the
ion. Two measurements are the same peak when:

- their mass windows overlap;
- each peak's apex lies inside the other peak's bounds;
- on the scans both traces cover inside the shared part of the two peaks and
  both read non-zero, at least ``SAME_ION_MIN_EQUAL_POINTS`` scans read exactly
  the same intensity, and those equal scans are the majority. The other scans
  are window-edge differences and are not evidence either way.

Different ions in overlapping windows (for example 13C and 15N isotopes
about 25 ppm apart) never read an equal intensity and stay apart.

The relation is closed transitively, so groups do not depend on input order,
channel order or peak IDs. Each group keeps one measurement: one located by a
recorded precursor when the group has one, since that precursor fixes the ion;
then the window that observes the most non-zero scans of the group's region
(fewest centroids lost past a window edge), then the wider RT context (least
clipped), then the lower XIC m/z and earlier context start.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, TypeVar

import numpy as np

from xic_extractor.acquisition_contract import AcquiredTrace
from xic_extractor.mass_accuracy import mz_window
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation

SAME_ION_MIN_EQUAL_POINTS = 3


class _MeasuredSegment(Protocol):
    @property
    def acquired_trace(self) -> AcquiredTrace: ...

    @property
    def evaluation(self) -> PhysicalPeakEvaluation: ...


_M = TypeVar("_M", bound=_MeasuredSegment)


def group_same_ion_peaks(
    measurements: Sequence[_M],
    *,
    recorded: Callable[[_M], bool] | None = None,
) -> tuple[tuple[_M, ...], ...]:
    """Groups of one sample's measured peaks, each led by its kept measurement."""
    items = tuple(measurements)
    if any(item.evaluation.answer.selected_segment is None for item in items):
        raise ValueError("same-ion grouping requires measured peaks")
    parent = list(range(len(items)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    windows = tuple(_window(item) for item in items)
    order = sorted(range(len(items)), key=lambda index: windows[index][0])
    for position, left in enumerate(order):
        left_high = windows[left][1]
        for offset in range(position + 1, len(order)):
            right = order[offset]
            if windows[right][0] > left_high:
                break
            if _same_ion_peak(items[left], items[right]):
                parent[root(left)] = root(right)

    groups: dict[int, list[_M]] = {}
    for index, item in enumerate(items):
        groups.setdefault(root(index), []).append(item)
    ranked = tuple(_ranked(members, recorded=recorded) for members in groups.values())
    return tuple(sorted(ranked, key=lambda group: _identity_key(group[0])))


def _same_ion_peak(left: _MeasuredSegment, right: _MeasuredSegment) -> bool:
    left_peak = left.evaluation.answer.selected_segment
    right_peak = right.evaluation.answer.selected_segment
    assert left_peak is not None and right_peak is not None
    if not (
        right_peak.left_rt_min <= left_peak.apex_rt_min <= right_peak.right_rt_min
        and left_peak.left_rt_min <= right_peak.apex_rt_min <= left_peak.right_rt_min
    ):
        return False
    region = (
        max(left_peak.left_rt_min, right_peak.left_rt_min),
        min(left_peak.right_rt_min, right_peak.right_rt_min),
    )
    left_trace = left.acquired_trace.trace
    right_trace = right.acquired_trace.trace
    left_inside = (left_trace.rt >= region[0]) & (left_trace.rt <= region[1])
    right_inside = (right_trace.rt >= region[0]) & (right_trace.rt <= region[1])
    _, left_index, right_index = np.intersect1d(
        left_trace.rt[left_inside], right_trace.rt[right_inside], return_indices=True
    )
    left_values = left_trace.intensity[left_inside][left_index]
    right_values = right_trace.intensity[right_inside][right_index]
    both = (left_values > 0.0) & (right_values > 0.0)
    equal = int(np.count_nonzero(both & (left_values == right_values)))
    return equal >= SAME_ION_MIN_EQUAL_POINTS and 2 * equal > int(
        np.count_nonzero(both)
    )


def _ranked(
    members: Sequence[_M],
    *,
    recorded: Callable[[_M], bool] | None,
) -> tuple[_M, ...]:
    if len(members) == 1:
        return tuple(members)
    peaks = [member.evaluation.answer.selected_segment for member in members]
    traces = [member.acquired_trace.trace for member in members]
    low = max(
        min(item.left_rt_min for item in peaks if item is not None),
        max(float(np.min(trace.rt)) for trace in traces),
    )
    high = min(
        max(item.right_rt_min for item in peaks if item is not None),
        min(float(np.max(trace.rt)) for trace in traces),
    )

    def observed(member: _MeasuredSegment) -> int:
        trace = member.acquired_trace.trace
        rt = np.asarray(trace.rt, dtype=float)
        intensity = np.asarray(trace.intensity, dtype=float)
        return int(np.count_nonzero((rt >= low) & (rt <= high) & (intensity > 0.0)))

    return tuple(
        sorted(
            members,
            key=lambda member: (
                not recorded(member) if recorded is not None else False,
                -observed(member),
                -(
                    member.acquired_trace.coordinate.request.rt_max
                    - member.acquired_trace.coordinate.request.rt_min
                ),
                *_identity_key(member),
            ),
        )
    )


def _identity_key(member: _MeasuredSegment) -> tuple[float, float, float, float]:
    request = member.acquired_trace.coordinate.request
    peak = member.evaluation.answer.selected_segment
    assert peak is not None
    return request.mz, request.rt_min, request.rt_max, peak.apex_rt_min


def _window(member: _MeasuredSegment) -> tuple[float, float]:
    request = member.acquired_trace.coordinate.request
    return mz_window(request.mz, request.ppm_tol)
