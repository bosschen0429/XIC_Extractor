"""Decide whether same-m/z groups are one chromatographic band.

A separable-valley cut measures where acquired signal can be measured
separately; it does not decide how many compounds an envelope holds. In some
injections one band is split into two halves, and each half groups with its
counterparts in other injections, so one compound becomes two rows. A true
pair of co-eluting compounds produces the same single-injection shape. Only the
cross-injection behaviour separates them:

- a true pair returns to background between its two peaks in some injection;
- a split band never does, and the area ratio of its two halves stays within a
  factor of two across the injections where both halves are members.

Groups judged to be one band are merged into one group whose value in each
sample is the whole envelope holding its member. The rule reads only member
observations and is independent of group ids and input order.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    _hypothesis_from_owner_group,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.physical_peak_evaluation import peak_envelope
from xic_extractor.peak_detection.physical_peak_models import PhysicalSegment

MAX_HALF_AREA_RATIO_RANGE = 2.0
"""Product policy, from the 8RAW split bands and true pairs of #1283."""
MIN_RATIO_SAMPLES = 2
_RT_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class _Member:
    left: float
    right: float
    envelope: tuple[PhysicalSegment, ...]
    area: float
    """The member's own segment area."""


def _member(peak: PeakObservation) -> _Member | None:
    evaluation = peak.physical_evaluation
    if evaluation is None:
        return None
    segment = evaluation.answer.selected_segment
    if segment is None:
        return None
    return _Member(
        left=peak.rt_left_min,
        right=peak.rt_right_min,
        envelope=peak_envelope(evaluation.segment_universe, segment),
        area=segment.area_counts_seconds,
    )


def _holds(envelope: tuple[PhysicalSegment, ...], member: _Member) -> bool:
    return any(
        segment.left_rt_min - _RT_TOLERANCE <= member.left
        and member.right <= segment.right_rt_min + _RT_TOLERANCE
        for segment in envelope
    )


def _relation(first: _Member, second: _Member) -> str:
    if _holds(first.envelope, second) or _holds(second.envelope, first):
        return "same_envelope"
    if (
        first.envelope[-1].right_rt_min < second.envelope[0].left_rt_min
        or second.envelope[-1].right_rt_min < first.envelope[0].left_rt_min
    ):
        return "resolved"
    return "contradiction"


def _one_band(
    early: CrossSamplePeakGroupHypothesis, late: CrossSamplePeakGroupHypothesis
) -> bool:
    early_by_sample = {owner.raw_stem: owner.peak for owner in early.owners}
    late_by_sample = {owner.raw_stem: owner.peak for owner in late.owners}
    log_ratios = []
    for sample in sorted(early_by_sample.keys() & late_by_sample.keys()):
        first = _member(early_by_sample[sample])
        second = _member(late_by_sample[sample])
        if first is None or second is None:
            return False
        relation = _relation(first, second)
        if relation != "same_envelope":
            return False
        if (
            first.area > 0.0
            and second.area > 0.0
            and (first.left, first.right) != (second.left, second.right)
        ):
            log_ratios.append(math.log10(first.area / second.area))
    return len(log_ratios) >= MIN_RATIO_SAMPLES and max(log_ratios) - min(
        log_ratios
    ) <= math.log10(MAX_HALF_AREA_RATIO_RANGE)


def merge_one_band_groups(
    groups: Sequence[CrossSamplePeakGroupHypothesis],
    *,
    config: AlignmentConfig,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Merge established groups that the band rule judges to be one band.

    A merged group keeps one member per sample: where several groups hold a
    member, the earliest apex. Its value is the whole envelope holding that
    member, so which piece is kept does not change the value.
    """
    established = sorted(
        (group for group in groups if group.disposition == "established"),
        key=lambda group: (group.group_center_mz, group.group_center_rt),
    )
    links: dict[int, set[int]] = defaultdict(set)
    for i, first in enumerate(established):
        for j in range(i + 1, len(established)):
            second = established[j]
            if not within_ppm(
                first.group_center_mz,
                second.group_center_mz,
                config.precursor_mz_tolerance_ppm,
            ):
                break
            early, late = sorted(
                (first, second), key=lambda group: group.group_center_rt
            )
            if _one_band(early, late):
                links[i].add(j)
                links[j].add(i)
    if not links:
        return tuple(groups)
    merged: list[CrossSamplePeakGroupHypothesis] = []
    absorbed: set[int] = set()
    for start in sorted(links):
        if start in absorbed:
            continue
        component, frontier = {start}, [start]
        while frontier:
            for neighbour in links[frontier.pop()]:
                if neighbour not in component:
                    component.add(neighbour)
                    frontier.append(neighbour)
        absorbed |= component
        merged.append(_merged_group([established[index] for index in component]))
    kept = [
        group
        for group in groups
        if not any(group is established[index] for index in absorbed)
    ]
    return tuple((*kept, *merged))


def _merged_group(
    parts: Sequence[CrossSamplePeakGroupHypothesis],
) -> CrossSamplePeakGroupHypothesis:
    by_sample: dict[str, SampleLocalMS1Owner] = {}
    for part in parts:
        for owner in part.owners:
            held = by_sample.get(owner.raw_stem)
            if held is None or (owner.peak.rt_apex_min, owner.owner_id) < (
                held.peak.rt_apex_min,
                held.owner_id,
            ):
                by_sample[owner.raw_stem] = owner
    largest = max(parts, key=lambda part: (len(part.owners), -part.group_center_rt))
    return _hypothesis_from_owner_group(
        tuple(sorted(by_sample.values(), key=lambda owner: owner.owner_id)),
        disposition="established",
        evidence=";".join(sorted({part.evidence for part in parts if part.evidence}))
        + ";one_band",
        inference_basis=largest.inference_basis,
        inference_decisions=largest.inference_decisions,
        one_band=True,
    )
