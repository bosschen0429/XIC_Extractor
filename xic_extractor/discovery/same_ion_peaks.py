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

from collections.abc import Collection, Sequence

import numpy as np

from xic_extractor.acquisition.physical_capture import CapturedPhysicalPeak
from xic_extractor.mass_accuracy import mz_window

SAME_ION_MIN_EQUAL_POINTS = 3


def group_same_ion_peaks(
    measurements: Sequence[CapturedPhysicalPeak],
    *,
    recorded_ordinals: Collection[int] = (),
) -> tuple[tuple[CapturedPhysicalPeak, ...], ...]:
    """Groups of one sample's measured peaks, each led by its kept measurement.

    ``recorded_ordinals`` names the measurements a recorded precursor located.
    """
    items = tuple(measurements)
    if any(item.evaluation.facts is None for item in items):
        raise ValueError("same-ion grouping requires measured peaks")
    parent = list(range(len(items)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    order = sorted(range(len(items)), key=lambda index: _window(items[index])[0])
    for position, left in enumerate(order):
        left_high = _window(items[left])[1]
        for right in order[position + 1 :]:
            if _window(items[right])[0] > left_high:
                break
            if _same_ion_peak(items[left], items[right]):
                parent[root(left)] = root(right)

    groups: dict[int, list[CapturedPhysicalPeak]] = {}
    for index, item in enumerate(items):
        groups.setdefault(root(index), []).append(item)
    recorded = frozenset(recorded_ordinals)
    ranked = tuple(
        _ranked(members, recorded_ordinals=recorded) for members in groups.values()
    )
    return tuple(sorted(ranked, key=lambda group: _identity_key(group[0])))


def _same_ion_peak(left: CapturedPhysicalPeak, right: CapturedPhysicalPeak) -> bool:
    left_low, left_high = _window(left)
    right_low, right_high = _window(right)
    if max(left_low, right_low) > min(left_high, right_high):
        return False
    left_facts = left.evaluation.facts
    right_facts = right.evaluation.facts
    assert left_facts is not None and right_facts is not None
    if not (
        right_facts.left_rt_min <= left_facts.apex_rt_min <= right_facts.right_rt_min
        and left_facts.left_rt_min <= right_facts.apex_rt_min <= left_facts.right_rt_min
    ):
        return False
    region = (
        max(left_facts.left_rt_min, right_facts.left_rt_min),
        min(left_facts.right_rt_min, right_facts.right_rt_min),
    )
    left_trace = left.acquired_trace.trace
    right_trace = right.acquired_trace.trace
    common, left_index, right_index = np.intersect1d(
        np.asarray(left_trace.rt, dtype=float),
        np.asarray(right_trace.rt, dtype=float),
        return_indices=True,
    )
    inside = (common >= region[0]) & (common <= region[1])
    left_values = np.asarray(left_trace.intensity, dtype=float)[left_index][inside]
    right_values = np.asarray(right_trace.intensity, dtype=float)[right_index][inside]
    both = (left_values > 0.0) & (right_values > 0.0)
    equal = int(np.count_nonzero(both & (left_values == right_values)))
    return equal >= SAME_ION_MIN_EQUAL_POINTS and 2 * equal > int(
        np.count_nonzero(both)
    )


def _ranked(
    members: Sequence[CapturedPhysicalPeak],
    *,
    recorded_ordinals: frozenset[int],
) -> tuple[CapturedPhysicalPeak, ...]:
    facts = [member.evaluation.facts for member in members]
    assert all(item is not None for item in facts)
    traces = [member.acquired_trace.trace for member in members]
    low = max(
        min(item.left_rt_min for item in facts if item is not None),
        max(float(np.min(trace.rt)) for trace in traces),
    )
    high = min(
        max(item.right_rt_min for item in facts if item is not None),
        min(float(np.max(trace.rt)) for trace in traces),
    )

    def observed(member: CapturedPhysicalPeak) -> int:
        trace = member.acquired_trace.trace
        rt = np.asarray(trace.rt, dtype=float)
        intensity = np.asarray(trace.intensity, dtype=float)
        return int(np.count_nonzero((rt >= low) & (rt <= high) & (intensity > 0.0)))

    return tuple(
        sorted(
            members,
            key=lambda member: (
                member.work_ordinal not in recorded_ordinals,
                -observed(member),
                -(member.search.request.rt_max - member.search.request.rt_min),
                *_identity_key(member),
            ),
        )
    )


def _identity_key(member: CapturedPhysicalPeak) -> tuple[float, float, float, float]:
    request = member.search.request
    facts = member.evaluation.facts
    assert facts is not None
    return request.mz, request.rt_min, request.rt_max, facts.apex_rt_min


def _window(member: CapturedPhysicalPeak) -> tuple[float, float]:
    request = member.search.request
    return mz_window(request.mz, request.ppm_tol)
