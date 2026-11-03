"""Shared peak correspondence evidence for grouping."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from statistics import median
from typing import TYPE_CHECKING

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.peak_detection.ms1_shape_identity import (
    LocalPeakShapeComparisons,
    compare_local_peak_shapes,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories
    from xic_extractor.alignment.rt_frame import RtFrame


def pair_ppm(left: float, right: float) -> float:
    """Pair gate convention: the left endpoint is the reference."""
    return abs(left - right) / max(abs(left), 1e-12) * 1_000_000.0


def envelope_fraction(
    current_min: float, current_max: float, value: float, tolerance_ppm: float
) -> float:
    """Full group extent, relative to its lower endpoint and tolerance."""
    lower, upper = min(current_min, value), max(current_max, value)
    return (
        math.inf if lower <= 0.0 else absolute_ppm_error(upper, lower) / tolerance_ppm
    )


def peak_correspondence_conflict(
    left: Sequence[SampleLocalMS1Owner],
    right: Sequence[SampleLocalMS1Owner],
    config: AlignmentConfig,
    *,
    trajectories: PeakTrajectories | None = None,
    rt_frame: RtFrame | None = None,
    shape_comparisons: LocalPeakShapeComparisons | None = None,
) -> tuple[str, str] | None:
    """Whether two member sets measure separate peaks in one common RT frame.

    Reject only disjoint group-consensus peak regions, not shifted apices
    within overlapping regions. Every union compares the complete groups'
    median regions rather than letting one narrow member veto a band.
    When only one side has calibration support, both sides are compared in the
    shared observed RT frame instead: missing support is not evidence of a
    different peak. Without calibrated regions on either
    side, the groups' median measured-shape similarity decides: chromatography
    varies individual pairs, so one noisy pair cannot split a band, while an
    unrelated background trace still differs from most members. Pairs
    distinguished by physical peak order are not shape evidence.
    Missing/flat/short traces supply no shape evidence.
    """
    if not left or not right:
        return None
    left_regions = _calibrated_regions(left, rt_frame)
    right_regions = _calibrated_regions(right, rt_frame)
    if bool(left_regions) != bool(right_regions):
        left_regions = _observed_regions(left)
        right_regions = _observed_regions(right)
    elif not left_regions:
        return _median_shape_conflict(
            left, right, config, trajectories, shape_comparisons
        )
    left_region = _consensus_region(left_regions.values())
    right_region = _consensus_region(right_regions.values())
    if not _regions_disjoint(left_region, right_region):
        return None
    return (
        _farthest_member(left_regions, right_region[1]),
        _farthest_member(right_regions, left_region[1]),
    )


_Region = tuple[float | None, float, float | None]


def _calibrated_regions(
    members: Sequence[SampleLocalMS1Owner], rt_frame: RtFrame | None
) -> dict[str, _Region]:
    if rt_frame is None:
        return {}
    return {
        member.owner_id: region
        for member in members
        if (region := rt_frame.calibrated_peak_region(member)) is not None
    }


def _observed_regions(members: Sequence[SampleLocalMS1Owner]) -> dict[str, _Region]:
    return {
        member.owner_id: (
            member.owner_peak_start_rt,
            member.owner_apex_rt,
            member.owner_peak_end_rt,
        )
        for member in members
    }


def _consensus_region(regions: Iterable[_Region]) -> _Region:
    """Median supported edges and apex; an unsupported edge stays unknown."""
    starts, apices, ends = [], [], []
    for start, apex, end in regions:
        apices.append(apex)
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    return (
        median(starts) if starts else None,
        median(apices),
        median(ends) if ends else None,
    )


def _median_shape_conflict(
    left: Sequence[SampleLocalMS1Owner],
    right: Sequence[SampleLocalMS1Owner],
    config: AlignmentConfig,
    trajectories: PeakTrajectories | None,
    shape_comparisons: LocalPeakShapeComparisons | None,
) -> tuple[str, str] | None:
    pairs = []
    compare = (
        compare_local_peak_shapes
        if shape_comparisons is None
        else shape_comparisons.compare
    )
    for first in left:
        for second in right:
            if (
                first.peak_shape is None
                or second.peak_shape is None
                or first.owner_id == second.owner_id
                or (
                    trajectories is not None
                    and trajectories.supports_ordered_pair(
                        first.owner_id, second.owner_id
                    )
                )
            ):
                continue
            similarity = compare(first.peak_shape, second.peak_shape).similarity
            if similarity is not None:
                pairs.append((similarity, first.owner_id, second.owner_id))
    if not pairs:
        return None
    similarity = median(item[0] for item in pairs)
    if (
        shape_comparisons is not None
        and similarity <= config.min_peak_shape_similarity + 1e-12
    ):
        # Batched reductions can differ by roundoff. Preserve the exact boundary
        # and the original conflicting-pair tie order for every potential veto.
        return _median_shape_conflict(left, right, config, trajectories, None)
    if similarity >= config.min_peak_shape_similarity:
        return None
    _, first_id, second_id = min(pairs)
    return first_id, second_id


def _regions_disjoint(left: _Region, right: _Region) -> bool:
    left_start, _, left_end = left
    right_start, _, right_end = right
    return (
        left_end is not None and right_start is not None and left_end < right_start
    ) or (right_end is not None and left_start is not None and right_end < left_start)


def _farthest_member(regions: Mapping[str, _Region], apex: float) -> str:
    return max(
        regions, key=lambda owner_id: (abs(regions[owner_id][1] - apex), owner_id)
    )


def owner_uncertainty_reason(
    owner: SampleLocalMS1Owner,
    config: AlignmentConfig,
    rules: GroupingRules,
) -> str:
    if owner.is_provisional:
        return "provisional_owner"
    if owner.owner_area <= 0.0:
        return "nonpositive_owner_area"
    if "ambiguous" in owner.assignment_reason:
        return "ambiguous_owner_assignment"
    if rules.support_level(owner, owner, config) == "weak":
        return "weak_identity_evidence"
    return ""
