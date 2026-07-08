"""Shared m/z accuracy primitives.

This module centralizes ppm arithmetic so feature matchers, XIC extraction,
alignment, and diagnostics can converge on the same reference-m/z semantics as
they are migrated. The current contract uses the expected/reference m/z as the
ppm denominator.
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


def _require_finite_mz(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive m/z")


def _require_positive_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0")


def _require_non_negative_finite(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0")
