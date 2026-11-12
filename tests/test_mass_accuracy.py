from __future__ import annotations

import numpy as np
import pytest

from xic_extractor.mass_accuracy import (
    absolute_ppm_error,
    is_within_mz_tolerance,
    mz_tolerance_da,
    mz_window,
    neutral_loss_match,
    ppm_scale_factor,
    reference_mz_window,
    signed_ppm_error,
    signed_ppm_errors,
    weighted_ion_mz,
    within_both_mz_tolerances,
    within_ppm,
)


def test_neutral_loss_match_uses_quadrature_window_at_boundary() -> None:
    # At 500/400 m/z and 20 ppm, the window is about 0.012806 Da.
    inside_error, inside_window, inside = neutral_loss_match(
        500.0, 400.0128, 100.0, 20.0
    )
    outside_error, outside_window, outside = neutral_loss_match(
        500.0, 400.0129, 100.0, 20.0
    )

    assert inside_error == pytest.approx(0.0128)
    assert inside_window == pytest.approx(0.0128064, rel=1e-5)
    assert inside is True
    assert outside_error == pytest.approx(0.0129)
    assert outside_window == pytest.approx(inside_window, rel=1e-5)
    assert outside is False


def test_neutral_loss_match_allows_asymmetric_ion_error() -> None:
    # A 25 ppm product deviation can be within the propagated two-ion window.
    error_da, window_da, matches = neutral_loss_match(500.0, 400.01, 100.0, 20.0)

    assert error_da == pytest.approx(0.01)
    assert window_da == pytest.approx(0.0128064, rel=1e-5)
    assert matches is True


def test_signed_ppm_error_uses_reference_mz_denominator() -> None:
    observed_mz = 100.001
    reference_mz = 100.0

    assert signed_ppm_error(observed_mz, reference_mz) == pytest.approx(10.0)
    assert absolute_ppm_error(observed_mz, reference_mz) == pytest.approx(10.0)


def test_signed_ppm_error_accepts_any_finite_observed_mass() -> None:
    # An observed neutral loss can fall below zero; only the reference divides.
    assert signed_ppm_error(-1.0, 100.0) == pytest.approx(-1.01e6)
    with pytest.raises(ValueError, match="observed_mz"):
        signed_ppm_error(float("nan"), 100.0)


def test_signed_ppm_errors_match_the_scalar_error_per_element() -> None:
    observed = np.array([99.999, 100.0, 100.002])

    errors = signed_ppm_errors(observed, 100.0)

    assert errors.tolist() == [signed_ppm_error(mz, 100.0) for mz in observed]


def test_ppm_scale_factor_applies_a_ppm_shift_multiplicatively() -> None:
    assert 500.0 * ppm_scale_factor(4.0) == pytest.approx(500.002)
    assert ppm_scale_factor(0.0) == 1.0


def test_reference_mz_window_holds_references_whose_window_holds_observed() -> None:
    low, high = reference_mz_window(500.0, 20.0)

    # Each bound is a reference whose own 20 ppm window just reaches 500.
    assert absolute_ppm_error(500.0, low) == pytest.approx(20.0)
    assert absolute_ppm_error(500.0, high) == pytest.approx(20.0)
    assert 500.0 - low < high - 500.0


def test_within_both_mz_tolerances_uses_the_narrower_window() -> None:
    # 0.006 Da apart: inside the 20 ppm window of 500, outside the 5 ppm one.
    assert within_both_mz_tolerances(500.0, 20.0, 500.006, 20.0) is True
    assert within_both_mz_tolerances(500.0, 20.0, 500.006, 5.0) is False
    assert within_both_mz_tolerances(500.0, 5.0, 500.006, 20.0) is False


def test_mz_window_uses_ppm_scaled_da_width() -> None:
    lower_mz, upper_mz = mz_window(500.0, 20.0)

    assert lower_mz == pytest.approx(499.99)
    assert upper_mz == pytest.approx(500.01)
    assert mz_tolerance_da(500.0, 20.0) == pytest.approx(0.01)


def test_mz_window_can_apply_minimum_da_floor() -> None:
    lower_mz, upper_mz = mz_window(100.0, 1.0, minimum_da=0.001)

    assert lower_mz == pytest.approx(99.999)
    assert upper_mz == pytest.approx(100.001)


def test_is_within_mz_tolerance_accepts_exact_boundary_with_epsilon() -> None:
    reference_mz = 501.002111
    boundary_mz = reference_mz + reference_mz * 20.0e-6

    assert (
        is_within_mz_tolerance(
            boundary_mz,
            reference_mz,
            tolerance_ppm=20.0,
        )
        is True
    )
    assert (
        is_within_mz_tolerance(
            reference_mz + reference_mz * 20.1e-6,
            reference_mz,
            tolerance_ppm=20.0,
        )
        is False
    )


def test_mass_accuracy_inputs_must_be_positive_finite() -> None:
    with pytest.raises(ValueError, match="reference_mz"):
        signed_ppm_error(100.0, 0.0)
    with pytest.raises(ValueError, match="observed_mz"):
        is_within_mz_tolerance(0.0, 100.0, tolerance_ppm=20.0)
    with pytest.raises(ValueError, match="tolerance_ppm"):
        mz_window(100.0, 0.0)


def test_within_ppm_evaluates_accuracy_and_safely_handles_invalid_inputs() -> None:
    reference_mz = 500.0
    within_mz = 500.005  # 10 ppm
    outside_mz = 500.015  # 30 ppm

    assert within_ppm(within_mz, reference_mz, 20.0) is True
    assert within_ppm(outside_mz, reference_mz, 20.0) is False

    # Exact boundary absorption
    boundary_mz = reference_mz + reference_mz * 20.0e-6
    assert within_ppm(boundary_mz, reference_mz, 20.0) is True

    # Safe handling of invalid inputs without throwing
    assert within_ppm(-100.0, 500.0, 20.0) is False
    assert within_ppm(500.0, -100.0, 20.0) is False
    assert within_ppm(500.0, 500.0, -1.0) is False
    assert within_ppm(float("nan"), 500.0, 20.0) is False
    assert within_ppm(500.0, float("inf"), 20.0) is False


def test_weighted_ion_mz_averages_one_ion_by_intensity() -> None:
    readings = [(359.2410, 1.0e6), (359.2414, 3.0e6), (359.2412, 0.0)]

    assert weighted_ion_mz(readings, max_spread_ppm=5.0) == pytest.approx(359.2413)


def test_weighted_ion_mz_refuses_readings_from_two_ions() -> None:
    # 359.2413 and 359.2440 are 7.5 ppm apart: two ions share the window.
    readings = [(359.2413, 1.0e6), (359.2440, 1.0e6)]

    assert weighted_ion_mz(readings, max_spread_ppm=5.0) is None
    assert weighted_ion_mz([], max_spread_ppm=5.0) is None
    assert weighted_ion_mz([(float("nan"), 1.0)], max_spread_ppm=5.0) is None
