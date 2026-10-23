"""Shared peak correspondence and signal-to-noise evidence for grouping."""

from __future__ import annotations

import math
from collections.abc import Sequence
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
    """Check complete groups in one supported RT frame, not just their edge.

    Each selected centre must be supported by the other's measured peak region.
    A low apex-relative correlation cannot veto that common-coordinate support.
    Without a complete calibrated region, retain the measured-shape comparison;
    an unavailable coordinate is not permission to mix raw and common RT.
    Physical order remains a separate membership constraint.
    """
    for first in left:
        for second in right:
            if first.owner_id == second.owner_id:
                continue
            a = rt_frame.calibrated_peak_region(first) if rt_frame is not None else None
            b = (
                rt_frame.calibrated_peak_region(second)
                if rt_frame is not None
                else None
            )
            if (a is None) != (b is None):
                # A calibrated and an unsupported raw member do not supply
                # correspondence evidence in a shared coordinate frame.
                return first.owner_id, second.owner_id
            if a is not None and b is not None:
                if not (a[0] <= b[1] <= a[2] and b[0] <= a[1] <= b[2]):
                    return first.owner_id, second.owner_id
                continue
            if first.peak_shape is None or second.peak_shape is None:
                continue
            if trajectories is not None and trajectories.supports_ordered_pair(
                first.owner_id, second.owner_id
            ):
                continue
            evidence = compare_local_peak_shapes(first.peak_shape, second.peak_shape)
            if (
                evidence.similarity is not None
                and evidence.similarity < config.min_peak_shape_similarity
            ):
                return first.owner_id, second.owner_id
    return None


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
