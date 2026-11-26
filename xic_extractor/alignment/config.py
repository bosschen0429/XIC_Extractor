from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from xic_extractor.configuration.cid_nl_profile import DEFAULT_CID_NL_MASS_POLICY


@dataclass(frozen=True)
class AlignmentConfig:
    """Correspondence settings; mass tolerances are the CID-NL mass policy's.

    A bound CID-NL recipe copies its Discovery mass tolerances here.
    Band membership additionally caps full-band precursor span at 10 ppm.
    """

    precursor_mz_tolerance_ppm: float = (
        DEFAULT_CID_NL_MASS_POLICY.precursor_mz_tolerance_ppm
    )
    preferred_rt_sec: float = 60.0
    product_mz_tolerance_ppm: float = (
        DEFAULT_CID_NL_MASS_POLICY.product_mz_tolerance_ppm
    )
    min_peak_shape_similarity: float = 0.80
    owner_apex_close_sec: float = 2.0
    rt_unit: Literal["min"] = "min"
    fragmentation_model: Literal["cid_nl"] = "cid_nl"
    backfill_enabled: bool = True


def validate_alignment_config(model: AlignmentConfig) -> None:
    _require_positive("precursor_mz_tolerance_ppm", model.precursor_mz_tolerance_ppm)
    _require_positive("preferred_rt_sec", model.preferred_rt_sec)
    _require_positive("product_mz_tolerance_ppm", model.product_mz_tolerance_ppm)
    _require_numeric_range(
        "min_peak_shape_similarity",
        model.min_peak_shape_similarity,
        0,
        1,
    )
    _require_positive("owner_apex_close_sec", model.owner_apex_close_sec)
    if model.rt_unit != "min":
        raise ValueError('rt_unit must be "min"')
    if model.fragmentation_model != "cid_nl":
        raise ValueError('fragmentation_model must be "cid_nl"')


def _require_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _require_numeric_range(
    name: str,
    value: float,
    minimum: float,
    maximum: float,
) -> None:
    if (
        isinstance(value, bool)
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(
            f"{name} must be finite numeric between {minimum} and {maximum}",
        )
