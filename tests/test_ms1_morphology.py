import warnings

import numpy as np
import pytest

from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    MS1_MORPHOLOGY_TRACE_METHOD,
    compute_shape_metrics,
    gaussian15_morphology_trace,
    gaussian15_positive_asls_residual_metrics,
    positive_residual_area,
)
from xic_extractor.peak_detection.scoring_metrics import (
    peak_width_severity,
    symmetry_severity,
)


def test_canonical_signal_cannot_be_created_by_negative_baseline() -> None:
    from xic_extractor.peak_detection.ms1_morphology import (
        canonical_gaussian_signal_traces,
    )

    raw = np.zeros(31)
    raw[20] = 100.0
    smoothed, signal = canonical_gaussian_signal_traces(
        raw, np.linspace(-500.0, 0.0, len(raw)), profile=SignalProcessingProfile()
    )

    assert np.all(signal <= smoothed)
    assert signal[0] == 0.0


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


def test_compute_shape_metrics_handles_non_peak_apex_without_warning() -> None:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        ratio, width_min = compute_shape_metrics(
            np.linspace(0.0, 2.0, 5), np.ones(5), 2
        )

    assert captured == []
    assert ratio == pytest.approx(1.0)
    assert width_min is None


def test_shape_symmetry_uses_nonuniform_rt_coordinates() -> None:
    rt = np.r_[np.linspace(0.0, 1.0, 151), np.linspace(1.02, 2.0, 50)]
    ratio, width_min = compute_shape_metrics(rt, _gaussian(rt), 150)

    assert ratio == pytest.approx(1.0, abs=0.001)
    assert width_min == pytest.approx(2.0 * np.sqrt(2.0 * np.log(2.0)) * 0.1, rel=0.001)
    assert symmetry_severity(ratio)[0] == 0


def test_shape_metrics_interpolate_fractional_half_prominence_crossings() -> None:
    rt = np.array([0.0, 1.0, 2.0, 4.0, 8.0])
    intensity = np.array([5.0, 6.0, 9.0, 6.0, 5.0])

    ratio, width_min = compute_shape_metrics(rt, intensity, 2)

    # Half prominence is 7: the RT crossings are 4/3 and 10/3 minutes.
    assert width_min == pytest.approx(2.0)
    assert ratio == pytest.approx(0.5)


def test_shape_metrics_distinguish_real_broad_and_asymmetric_peaks() -> None:
    rt = np.linspace(-2.0, 4.0, 901)
    _, paired_width_min = compute_shape_metrics(rt, _gaussian(rt), 450)
    broad_ratio, broad_width_min = compute_shape_metrics(
        rt, _gaussian(rt, sigma=0.25), 450
    )
    sigma = np.where(rt < 1.0, 0.3, 0.1)
    asymmetric = 1000.0 * np.exp(-0.5 * ((rt - 1.0) / sigma) ** 2)
    asymmetric_ratio, _ = compute_shape_metrics(rt, asymmetric, 450)

    assert broad_width_min / paired_width_min == pytest.approx(2.5, rel=0.001)
    assert peak_width_severity(broad_width_min / paired_width_min)[0] == 1
    assert broad_ratio == pytest.approx(1.0)
    assert asymmetric_ratio == pytest.approx(3.0, rel=0.001)
    assert symmetry_severity(asymmetric_ratio)[0] == 1


def test_shape_time_width_is_stable_for_direct_gaussian_scan_densities() -> None:
    widths = []
    for sample_count in (101, 301, 901):
        rt = np.linspace(0.0, 2.0, sample_count)
        ratio, width = compute_shape_metrics(rt, _gaussian(rt), sample_count // 2)
        assert ratio == pytest.approx(1.0)
        assert width == pytest.approx(2.0 * np.sqrt(2.0 * np.log(2.0)) * 0.1, rel=0.001)
        widths.append(width)
    assert max(widths) / min(widths) - 1.0 <= 0.001


def _gaussian(rt: np.ndarray, *, sigma: float = 0.1) -> np.ndarray:
    return 1000.0 * np.exp(-0.5 * ((rt - 1.0) / sigma) ** 2)
