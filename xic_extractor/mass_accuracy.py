"""Shared m/z accuracy primitives.

This module centralizes ppm arithmetic and the derived neutral-loss window.
Ordinary ppm errors use the expected/reference m/z as the denominator.
"""

from __future__ import annotations

import math

PPM_BOUNDARY_EPSILON = 1e-9


def signed_ppm_error(observed_mz: float, reference_mz: float) -> float:
    """Return signed ppm error using ``reference_mz`` as denominator."""

    _require_finite_mz("observed_mz", observed_mz)
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


def _require_finite_mz(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive m/z")


def _require_positive_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_non_negative_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")
