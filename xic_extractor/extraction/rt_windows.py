from __future__ import annotations

from typing import Any

from xic_extractor.acquisition.ms2_windows import acquire_ms2_window
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import (
    anchor_window_bounds,
)
from xic_extractor.evidence.neutral_loss import (
    find_nl_anchor_rt as evaluate_nl_anchor_rt,
)
from xic_extractor.extraction.anchors import (
    PAIRED_TARGET_NL_ANCHOR_REFERENCE_DELTA_MAX_MIN,
)


def find_nl_anchor_rt(
    raw: Any,
    *,
    sample_key: str,
    rt_center: float,
    search_margin_min: float,
    **question: Any,
) -> float | None:
    rt_min, rt_max = anchor_window_bounds(rt_center, search_margin_min)
    window = acquire_ms2_window(
        raw, sample_key=sample_key, rt_min=rt_min, rt_max=rt_max
    )
    return evaluate_nl_anchor_rt(window, **question)


def get_rt_window(
    raw: Any,
    target: Target,
    config: ExtractionConfig,
    *,
    sample_key: str,
    reference_rt: float | None,
    target_reference_rt: float | None = None,
    sample_drift: float = 0.0,
) -> tuple[float, float, bool, float | None]:
    """Return (rt_min, rt_max, anchor_used, anchor_rt)."""
    if target.neutral_loss_da is None:
        # The configured center guides identity; acquisition retains its full
        # scope and the reference does not claim a measured NL anchor.
        reference = (
            target_reference_rt
            if target_reference_rt is not None
            else (target.rt_min + target.rt_max) / 2.0 + sample_drift
        )
        return target.rt_min, target.rt_max, False, reference
    if target.nl_ppm_max is None:
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
    anchor_rt = find_nl_anchor_rt(
        raw,
        sample_key=sample_key,
        precursor_mz=target.mz,
        rt_center=rt_center,
        search_margin_min=config.nl_rt_anchor_search_margin_min,
        neutral_loss_da=target.neutral_loss_da,
        nl_ppm_max=target.nl_ppm_max,
        ms2_precursor_tol_da=config.ms2_precursor_tol_da,
        nl_min_intensity_ratio=config.nl_min_intensity_ratio,
        reference_rt=anchor_reference_rt,
    )
    if target.is_istd:
        # A DDA NL anchor is a reference, not proof that the exact-mass MS1
        # component is confined to its narrow neighborhood.
        return target.rt_min, target.rt_max, anchor_rt is not None, anchor_rt
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
