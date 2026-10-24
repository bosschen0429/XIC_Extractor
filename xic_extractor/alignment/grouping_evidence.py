"""Shared peak correspondence and signal-to-noise evidence for grouping."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from statistics import median
from typing import TYPE_CHECKING

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.owner_area import owner_quantification_eligible
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

if TYPE_CHECKING:
    from xic_extractor.alignment.peak_trajectories import PeakTrajectories
    from xic_extractor.alignment.rt_frame import RtFrame

DETECTION_LIMIT_SNR = 3.0
QUANTITATION_LIMIT_SNR = 10.0


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
) -> tuple[str, str] | None:
    """Whether two member sets measure separate peaks in one common RT frame.

    Compare each group's consensus peak region rather than every member pair:
    one narrow or locally miscalibrated member cannot veto an otherwise
    coincident group, and an intermediate member still cannot bridge two
    separated bands because every union is checked against complete groups.
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
        return _median_shape_conflict(left, right, config, trajectories)
    left_region = _consensus_region(left_regions.values())
    right_region = _consensus_region(right_regions.values())
    if not (
        _apex_outside(left_region, right_region[1])
        or _apex_outside(right_region, left_region[1])
    ):
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
) -> tuple[str, str] | None:
    pairs = []
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
            similarity = compare_local_peak_shapes(
                first.peak_shape, second.peak_shape
            ).similarity
            if similarity is not None:
                pairs.append((similarity, first.owner_id, second.owner_id))
    if not pairs or median(item[0] for item in pairs) >= (
        config.min_peak_shape_similarity
    ):
        return None
    _, first_id, second_id = min(pairs)
    return first_id, second_id


def _apex_outside(region: _Region, apex: float) -> bool:
    start, _, end = region
    return (start is not None and apex < start) or (end is not None and apex > end)


def _farthest_member(regions: Mapping[str, _Region], apex: float) -> str:
    return max(
        regions, key=lambda owner_id: (abs(regions[owner_id][1] - apex), owner_id)
    )


def min_owner_peak_snr(
    left: SampleLocalMS1Owner,
    right: SampleLocalMS1Owner,
) -> float | None:
    left_snr = owner_peak_snr(left)
    right_snr = owner_peak_snr(right)
    if left_snr is None or right_snr is None:
        return None
    return min(left_snr, right_snr)


def owner_peak_snr(owner: SampleLocalMS1Owner) -> float | None:
    """Signal-to-noise of the peak Alignment resolved for this owner."""
    integration = owner.selected_integration
    if integration is None:
        return None
    residual = integration.baseline_residual_mad
    if residual is None or residual <= 0.0:
        return None
    return integration.height_raw / residual


def owner_peak_meets_quantitation_limit(owner: SampleLocalMS1Owner) -> bool:
    """Whether the resolved peak clears the quantitation-limit convention."""
    snr = owner_peak_snr(owner)
    return (
        owner_quantification_eligible(owner)
        and snr is not None
        and snr >= QUANTITATION_LIMIT_SNR
    )


def owner_uncertainty_reason(
    owner: SampleLocalMS1Owner,
    config: AlignmentConfig,
    rules: GroupingRules,
) -> str:
    if owner.identity_conflict:
        return "owner_identity_conflict"
    if owner.is_provisional:
        return "provisional_owner"
    if owner.owner_area <= 0.0:
        return "nonpositive_owner_area"
    if "ambiguous" in owner.assignment_reason:
        return "ambiguous_owner_assignment"
    if rules.support_level(owner, owner, config) == "weak":
        return "weak_identity_evidence"
    return ""
