import numpy as np
import pytest

from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    gaussian15_morphology_trace,
    gaussian15_positive_asls_residual_metrics,
    gaussian_raw_minus_asls_raw_positive_trace,
    positive_residual_area,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    SignalProcessingProfile,
)


def test_gaussian15_morphology_trace_preserves_centered_spike_area() -> None:
    residual = np.zeros(31)
    residual[15] = 100.0

    smoothed = gaussian15_morphology_trace(residual)

    assert smoothed[15] < 100.0
    assert smoothed.sum() == pytest.approx(100.0)
    assert np.all(smoothed >= 0.0)


def test_positive_residual_area_integrates_only_positive_signal_seconds() -> None:
    rt = np.array([0.0, 1.0, 2.0])
    residual = np.array([-5.0, 10.0, -5.0])

    area = positive_residual_area(rt, residual, 0, 3)

    assert area == pytest.approx(600.0)


def test_gaussian15_positive_asls_residual_metrics_carry_provenance() -> None:
    rt = np.linspace(0.0, 2.0, 31)
    intensity = np.zeros(31)
    intensity[15] = 100.0
    baseline = np.zeros(31)

    metrics = gaussian15_positive_asls_residual_metrics(
        rt,
        intensity,
        baseline,
        10,
        21,
    )

    assert metrics.area_positive_asls_residual > 0.0
    assert metrics.area_source == MS1_MORPHOLOGY_AREA_SOURCE
    assert metrics.trace_method == MS1_MORPHOLOGY_TRACE_METHOD
    assert metrics.trace_window_points == DEFAULT_GAUSSIAN15_WINDOW_POINTS
    assert metrics.trace_effective_points == DEFAULT_GAUSSIAN15_WINDOW_POINTS


def _analytic_gaussian_area_counts_seconds(height: float, sigma_min: float) -> float:
    """Closed-form area of a Gaussian peak in counts*seconds.

    integral over RT(min) of height*exp(-0.5*((t-mu)/sigma)**2) is
    height*sigma*sqrt(2*pi); the morphology area scales minutes to seconds (*60).
    """
    return height * sigma_min * float(np.sqrt(2.0 * np.pi)) * 60.0


def test_gaussian15_morphology_area_matches_analytic_gaussian_integral() -> None:
    # End-to-end oracle for the production primary area (area_ms1_morphology):
    # a clean, well-contained Gaussian over a zero baseline must integrate to
    # its closed-form area. Gaussian15 smoothing preserves integral and the
    # positive clip removes nothing from a strictly positive peak, so the
    # composed metric must not distort the quantitation of a clean peak.
    height = 1000.0
    sigma_min = 0.08
    center_min = 1.0
    rt = np.linspace(0.0, 2.0, 401)
    intensity = height * np.exp(-0.5 * ((rt - center_min) / sigma_min) ** 2)
    baseline = np.zeros_like(rt)

    metrics = gaussian15_positive_asls_residual_metrics(
        rt, intensity, baseline, 0, len(rt)
    )

    analytic_area = _analytic_gaussian_area_counts_seconds(height, sigma_min)
    # This path is linear (residual subtract, area-preserving convolution,
    # trapezoid), so the observed deviation is machine precision (~1e-15);
    # rel=1e-3 still flags any real (>=0.1%) area drift.
    assert metrics.area_positive_asls_residual == pytest.approx(analytic_area, rel=1e-3)


def test_gaussian15_morphology_area_removes_sloped_baseline_before_integration() -> (
    None
):
    # Pins the residual subtraction (intensity - baseline) on a NON-constant
    # baseline, so it is not a bit-for-bit copy of the zero-baseline oracle: the
    # same Gaussian riding on a sloped drift must yield the same analytic area.
    # If the varying baseline is not fully removed, the drift would distort the
    # integral and this assertion would fail loudly.
    height = 1000.0
    sigma_min = 0.08
    center_min = 1.0
    rt = np.linspace(0.0, 2.0, 401)
    gaussian = height * np.exp(-0.5 * ((rt - center_min) / sigma_min) ** 2)
    sloped_baseline = np.linspace(200.0, 600.0, rt.size)
    intensity = gaussian + sloped_baseline
    baseline = sloped_baseline.copy()

    metrics = gaussian15_positive_asls_residual_metrics(
        rt, intensity, baseline, 0, len(rt)
    )

    analytic_area = _analytic_gaussian_area_counts_seconds(height, sigma_min)
    assert metrics.area_positive_asls_residual == pytest.approx(analytic_area, rel=1e-3)


def test_canonical_formula_smooths_raw_then_subtracts_raw_asls_baseline() -> None:
    raw = np.asarray([10.0, 20.0, 80.0, 20.0, 10.0] * 5)
    baseline = np.linspace(8.0, 18.0, raw.size)
    profile = SignalProcessingProfile(window_points=5)

    actual = gaussian_raw_minus_asls_raw_positive_trace(
        raw,
        baseline,
        profile=profile,
    )
    expected = np.maximum(
        gaussian15_morphology_trace(raw, window_points=5) - baseline,
        0.0,
    )
    residual_first = np.maximum(
        gaussian15_morphology_trace(raw - baseline, window_points=5),
        0.0,
    )

    assert actual == pytest.approx(expected)
    assert not np.allclose(actual, residual_first)


def test_canonical_formula_rejects_short_trace_instead_of_shrinking_window() -> None:
    profile = SignalProcessingProfile(window_points=15)
    with pytest.raises(ValueError, match="insufficient_points_for_profile"):
        gaussian_raw_minus_asls_raw_positive_trace(
            np.ones(7),
            np.zeros(7),
            profile=profile,
        )
