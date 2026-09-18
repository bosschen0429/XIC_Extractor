from __future__ import annotations

import pytest

from xic_extractor.mass_accuracy import (
    absolute_ppm_error,
    is_within_mz_tolerance,
    mz_tolerance_da,
    mz_window,
    signed_ppm_error,
    within_ppm,
)


def test_signed_ppm_error_uses_reference_mz_denominator() -> None:
    observed_mz = 100.001
    reference_mz = 100.0

    assert signed_ppm_error(observed_mz, reference_mz) == pytest.approx(10.0)
    assert absolute_ppm_error(observed_mz, reference_mz) == pytest.approx(10.0)


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
