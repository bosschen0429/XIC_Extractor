from __future__ import annotations

from typing import Any

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.anchors import (
    PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN,
)


def get_rt_window(
    raw: Any,
    target: Target,
    config: ExtractionConfig,
    *,
    reference_rt: float | None,
    target_reference_rt: float | None = None,
    sample_drift: float = 0.0,
) -> tuple[float, float, bool, float | None]:
    """Return (rt_min, rt_max, anchor_used, anchor_rt)."""
    from xic_extractor import extractor

    if target.neutral_loss_da is None or target.nl_ppm_max is None:
        return target.rt_min, target.rt_max, False, None

    rt_center = (target.rt_min + target.rt_max) / 2.0 + sample_drift
    paired_reference_rt = _paired_reference_inside_target_window(
        target,
        reference_rt=reference_rt,
    )
    fallback_reference_rt = (
        target_reference_rt
        if target_reference_rt is not None
        else paired_reference_rt
        if paired_reference_rt is not None
        else rt_center
    )
    anchor_reference_rt = (
        target_reference_rt if target_reference_rt is not None else paired_reference_rt
    )
    anchor_rt = extractor.find_nl_anchor_rt(
        raw,
        precursor_mz=target.mz,
        rt_center=rt_center,
        search_margin_min=config.nl_rt_anchor_search_margin_min,
        neutral_loss_da=target.neutral_loss_da,
        nl_ppm_max=target.nl_ppm_max,
        ms2_precursor_tol_da=config.ms2_precursor_tol_da,
        nl_min_intensity_ratio=config.nl_min_intensity_ratio,
        reference_rt=anchor_reference_rt,
    )
    if anchor_rt is not None:
        if _paired_target_anchor_is_too_far_from_reference(
            anchor_rt,
            reference_rt=anchor_reference_rt,
        ):
            half = config.nl_fallback_half_window_min
            return (
                max(0.0, fallback_reference_rt - half),
                fallback_reference_rt + half,
                False,
                fallback_reference_rt,
            )
        half = config.nl_rt_anchor_half_window_min
        return max(0.0, anchor_rt - half), anchor_rt + half, True, anchor_rt

    half = config.nl_fallback_half_window_min
    if reference_rt is not None:
        return (
            max(0.0, fallback_reference_rt - half),
            fallback_reference_rt + half,
            False,
            fallback_reference_rt,
        )
    return max(0.0, rt_center - half), rt_center + half, False, None


def _paired_reference_inside_target_window(
    target: Target,
    *,
    reference_rt: float | None,
) -> float | None:
    if target.is_istd or not target.istd_pair or reference_rt is None:
        return None
    if target.rt_min <= reference_rt <= target.rt_max:
        return reference_rt
    return None


def _paired_target_anchor_is_too_far_from_reference(
    anchor_rt: float,
    *,
    reference_rt: float | None,
) -> bool:
    if reference_rt is None:
        return False
    return (
        abs(anchor_rt - reference_rt) > PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN
    )
