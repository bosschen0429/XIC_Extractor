"""Shared m/z accuracy primitives.

This module centralizes ppm arithmetic and the derived neutral-loss window.
Ordinary ppm errors use the expected/reference m/z as the denominator.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

PPM_BOUNDARY_EPSILON = 1e-9


def signed_ppm_error(observed_mz: float, reference_mz: float) -> float:
    """Return signed ppm error using ``reference_mz`` as denominator.

    ``observed_mz`` may be any finite mass, such as an observed neutral loss.
    """

    if not math.isfinite(observed_mz):
        raise ValueError("observed_mz must be finite")
    _require_finite_mz("reference_mz", reference_mz)
    return (observed_mz - reference_mz) / reference_mz * 1e6


def signed_ppm_errors(
    observed_mz: npt.NDArray[np.floating], reference_mz: float
) -> npt.NDArray[np.floating]:
    """Return element-wise signed ppm errors of an array against one reference."""

    _require_finite_mz("reference_mz", reference_mz)
    return (observed_mz - reference_mz) / reference_mz * 1e6


def absolute_ppm_error(observed_mz: float, reference_mz: float) -> float:
    """Return absolute ppm error using ``reference_mz`` as denominator."""

    return abs(signed_ppm_error(observed_mz, reference_mz))


def mz_tolerance_da(
    reference_mz: float,
    tolerance_ppm: float,
    *,
    minimum_da: float = 0.0,
) -> float:
    """Return the absolute m/z tolerance in Daltons for a ppm window."""

    _require_finite_mz("reference_mz", reference_mz)
    _require_positive_finite("tolerance_ppm", tolerance_ppm)
    _require_non_negative_finite("minimum_da", minimum_da)
    return max(reference_mz * tolerance_ppm * 1e-6, minimum_da)


def neutral_loss_match(
    precursor_mz: float,
    observed_product_mz: float,
    expected_loss_da: float,
    tolerance_ppm: float,
    *,
    observed_loss_da: float | None = None,
) -> tuple[float, float, bool]:
    """Return loss error, quadrature ion window (Da), and inclusive match.

    ``tolerance_ppm`` applies separately to precursor and observed product m/z.
    """

    _require_finite_mz("precursor_mz", precursor_mz)
    _require_finite_mz("observed_product_mz", observed_product_mz)
    _require_positive_finite("expected_loss_da", expected_loss_da)
    _require_positive_finite("tolerance_ppm", tolerance_ppm)
    if observed_loss_da is None:
        observed_loss_da = precursor_mz - observed_product_mz
    if not math.isfinite(observed_loss_da):
        raise ValueError("observed_loss_da must be finite")
    error_da = abs(observed_loss_da - expected_loss_da)
    window_da = math.hypot(precursor_mz, observed_product_mz) * tolerance_ppm * 1e-6
    return error_da, window_da, error_da <= window_da


def ppm_scale_factor(shift_ppm: float) -> float:
    """Return the multiplicative m/z scale of a ``shift_ppm`` mass shift."""

    return 1.0 + shift_ppm * 1e-6


def mz_window(
    reference_mz: float,
    tolerance_ppm: float,
    *,
    minimum_da: float = 0.0,
) -> tuple[float, float]:
    """Return inclusive lower/upper m/z bounds around ``reference_mz``."""

    tolerance_da = mz_tolerance_da(
        reference_mz,
        tolerance_ppm,
        minimum_da=minimum_da,
    )
    return reference_mz - tolerance_da, reference_mz + tolerance_da


def reference_mz_window(
    observed_mz: float, tolerance_ppm: float
) -> tuple[float, float]:
    """Return bounds of the reference m/z values whose ppm window holds ``observed_mz``.

    Ppm is relative to the unknown reference, so the interval is asymmetric.
    """

    _require_finite_mz("observed_mz", observed_mz)
    _require_positive_finite("tolerance_ppm", tolerance_ppm)
    fraction = tolerance_ppm / 1e6
    return observed_mz / (1.0 + fraction), observed_mz / (1.0 - fraction)


def within_both_mz_tolerances(
    left_mz: float,
    left_tolerance_ppm: float,
    right_mz: float,
    right_tolerance_ppm: float,
) -> bool:
    """Return whether two m/z values lie within the narrower of their own windows."""

    return abs(left_mz - right_mz) <= min(
        mz_tolerance_da(left_mz, left_tolerance_ppm),
        mz_tolerance_da(right_mz, right_tolerance_ppm),
    )


def is_within_mz_tolerance(
    observed_mz: float,
    reference_mz: float,
    *,
    tolerance_ppm: float,
    minimum_da: float = 0.0,
    boundary_epsilon_ppm: float = PPM_BOUNDARY_EPSILON,
) -> bool:
    """Return whether ``observed_mz`` is within a ppm/mDa m/z tolerance."""

    _require_finite_mz("observed_mz", observed_mz)
    tolerance_da = mz_tolerance_da(
        reference_mz,
        tolerance_ppm,
        minimum_da=minimum_da,
    )
    _require_non_negative_finite("boundary_epsilon_ppm", boundary_epsilon_ppm)
    epsilon_da = reference_mz * boundary_epsilon_ppm * 1e-6
    return abs(observed_mz - reference_mz) <= tolerance_da + epsilon_da


def within_ppm(
    observed_mz: float,
    reference_mz: float,
    tolerance_ppm: float,
    *,
    minimum_da: float = 0.0,
    boundary_epsilon_ppm: float = PPM_BOUNDARY_EPSILON,
) -> bool:
    """Return whether ``observed_mz`` is within ``tolerance_ppm`` of ``reference_mz``.

    Uses ``reference_mz`` as denominator. Returns False if inputs are
    non-finite, negative, or non-positive.
    """
    if (
        not math.isfinite(observed_mz)
        or not math.isfinite(reference_mz)
        or not math.isfinite(tolerance_ppm)
        or not math.isfinite(minimum_da)
        or not math.isfinite(boundary_epsilon_ppm)
        or observed_mz <= 0
        or reference_mz <= 0
        or tolerance_ppm < 0
        or minimum_da < 0
        or boundary_epsilon_ppm < 0
    ):
        return False
    tolerance_da = max(reference_mz * tolerance_ppm * 1e-6, minimum_da)
    epsilon_da = reference_mz * boundary_epsilon_ppm * 1e-6
    return abs(observed_mz - reference_mz) <= tolerance_da + epsilon_da


def weighted_ion_mz(
    readings: Sequence[tuple[float, float]],
    *,
    max_spread_ppm: float,
) -> float | None:
    """Return one ion's m/z from ``(mz, intensity)`` readings at several scans.

    The result is the intensity-weighted mean. Readings that spread beyond
    ``max_spread_ppm`` come from more than one ion, so no single m/z is
    returned; neither is one without positive intensity.
    """
    _require_non_negative_finite("max_spread_ppm", max_spread_ppm)
    usable = [
        (mz, weight)
        for mz, weight in readings
        if math.isfinite(mz) and mz > 0 and math.isfinite(weight) and weight > 0
    ]
    if not usable:
        return None
    low = min(mz for mz, _ in usable)
    high = max(mz for mz, _ in usable)
    if (high - low) / low * 1e6 > max_spread_ppm:
        return None
    total = sum(weight for _, weight in usable)
    return sum(mz * weight for mz, weight in usable) / total


def _require_finite_mz(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive m/z")


def _require_positive_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_non_negative_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")
