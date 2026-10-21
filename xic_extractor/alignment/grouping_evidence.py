"""Shared physical peak shape and signal-to-noise evidence for grouping."""

from __future__ import annotations

import math
from collections.abc import Sequence

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_area import owner_quantification_eligible
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error
from xic_extractor.peak_detection.ms1_shape_identity import compare_local_peak_shapes

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


def peak_shape_union_conflict(
    left: Sequence[SampleLocalMS1Owner],
    right: Sequence[SampleLocalMS1Owner],
    config: AlignmentConfig,
) -> tuple[str, str] | None:
    """A measured shape contradiction cannot be repaired by a mass/RT bridge.

    Check the members, not just the joining edge: otherwise an intermediate
    shape can transitively reunite two incompatible peaks. Missing/flat/short
    traces supply no shape evidence, rather than a fabricated match or veto.
    """
    for first in left:
        if first.peak_shape is None:
            continue
        for second in right:
            if second.peak_shape is None or first.owner_id == second.owner_id:
                continue
            evidence = compare_local_peak_shapes(first.peak_shape, second.peak_shape)
            if (
                evidence.similarity is not None
                and evidence.similarity < config.min_peak_shape_similarity
            ):
                return first.owner_id, second.owner_id
    return None


def _min_owner_peak_snr(
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
