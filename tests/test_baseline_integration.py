import numpy as np
import pytest

from xic_extractor.peak_detection.baseline import (
    bounded_trace_interval,
    compute_asls_residual_mad,
    integrate_asls_baseline,
    integrate_with_baseline,
)


def test_area_uncertainty_uses_baseline_residual_noise_not_peak_height(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = np.linspace(0.0, 0.9, 10)
    low_peak = np.asarray([10, 10, 10, 12, 18, 12, 10, 10, 10, 10], dtype=float)
    high_peak = np.asarray([10, 10, 10, 20, 90, 20, 10, 10, 10, 10], dtype=float)
    shared_baseline = np.full_like(rt, 10.0)

    monkeypatch.setattr(
        "xic_extractor.peak_detection.baseline.asls_baseline",
        lambda values, **_kwargs: shared_baseline,
    )

    low = integrate_asls_baseline(low_peak, rt, 3, 6)
    high = integrate_asls_baseline(high_peak, rt, 3, 6)

    assert low.area_uncertainty_formula_version == "baseline_residual_mad"
    assert high.area_uncertainty_formula_version == "baseline_residual_mad"
    assert low.area_uncertainty == pytest.approx(high.area_uncertainty)
    assert high.area_baseline_corrected > low.area_baseline_corrected


def test_area_uncertainty_returns_none_for_non_positive_scan_period() -> None:
    rt = np.asarray([0.1, 0.1, 0.1, 0.1, 0.1])
    intensity = np.asarray([10.0, 20.0, 25.0, 12.0, 11.0])
    baseline = np.full_like(intensity, 8.0)

    result = integrate_asls_baseline(intensity, rt, 0, 5, baseline_values=baseline)

    assert result.area_uncertainty is None
    assert result.baseline_residual_mad is not None
    assert result.area_uncertainty_noise_source == "asls_residual"


def test_asls_baseline_integrates_shadow_area_without_exceeding_raw() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    intensity = np.asarray([8.0, 12.0, 70.0, 65.0, 20.0, 12.0])

    result = integrate_asls_baseline(intensity, rt, 1, 5)

    assert result.baseline_type == "asls"
    assert result.area_baseline_corrected > 0.0
    raw_area = 60.0 * float(np.trapezoid(intensity[1:5], rt[1:5]))
    assert result.area_baseline_corrected <= raw_area
    assert result.baseline_score is not None
    assert 0.0 <= result.baseline_score <= 1.0
    assert result.area_uncertainty is not None
    assert result.area_uncertainty_formula_version == "baseline_residual_mad"
    assert result.baseline_residual_mad is not None
    assert result.area_uncertainty_noise_source == "asls_residual"


def test_asls_baseline_keeps_provenance_when_uncertainty_unavailable() -> None:
    rt = np.asarray([0.1, 0.1, 0.1, 0.1, 0.1])
    intensity = np.asarray([8.0, 12.0, 70.0, 20.0, 12.0])
    baseline = np.full_like(intensity, 8.0)

    result = integrate_asls_baseline(intensity, rt, 0, 5, baseline_values=baseline)

    assert result.area_uncertainty is None
    assert result.area_uncertainty_formula_version == "baseline_residual_mad"
    assert result.baseline_residual_mad is not None
    assert result.area_uncertainty_noise_source == "asls_residual"


def test_compute_asls_residual_mad_rejects_explicit_shape_mismatch() -> None:
    with pytest.raises(ValueError, match="baseline_values"):
        compute_asls_residual_mad(
            np.asarray([1.0, 2.0, 3.0, 4.0, 5.0]),
            baseline_values=np.asarray([1.0, 2.0]),
        )


def test_integrate_with_baseline_dispatches_asls() -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3])
    intensity = np.asarray([10.0, 30.0, 25.0, 12.0])

    asls = integrate_with_baseline(
        intensity,
        rt,
        0,
        4,
        baseline_method="asls",
    )

    assert asls.baseline_type == "asls"


def test_integrate_with_baseline_reuses_precomputed_asls_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = np.asarray([0.0, 0.1, 0.2, 0.3, 0.4])
    intensity = np.asarray([10.0, 25.0, 50.0, 35.0, 20.0])
    baseline = np.full_like(intensity, 10.0)

    def _unexpected_asls(*_args: object, **_kwargs: object) -> np.ndarray:
        raise AssertionError("precomputed baseline should be reused")

    monkeypatch.setattr(
        "xic_extractor.peak_detection.baseline.asls_baseline",
        _unexpected_asls,
    )

    result = integrate_with_baseline(
        intensity,
        rt,
        0,
        5,
        baseline_method="asls",
        baseline_values=baseline,
    )

    assert result.baseline_type == "asls"
    assert result.area_baseline_corrected == pytest.approx(510.0)


def test_integrate_with_baseline_rejects_retired_linear_edge_method() -> None:
    rt = np.asarray([0.0, 0.1, 0.2])
    intensity = np.asarray([10.0, 20.0, 12.0])

    with pytest.raises(ValueError, match="retired; use asls"):
        integrate_with_baseline(
            intensity,
            rt,
            0,
            3,
            baseline_method="linear_edge",
        )


def test_integrate_with_baseline_rejects_unknown_method() -> None:
    rt = np.asarray([0.0, 0.1, 0.2])
    intensity = np.asarray([10.0, 20.0, 12.0])

    with pytest.raises(ValueError, match="must be 'asls'"):
        integrate_with_baseline(
            intensity,
            rt,
            0,
            3,
            baseline_method="airpls",
        )


def test_bounded_trace_interval_matches_integration_interval_contract() -> None:
    assert bounded_trace_interval(2, 3, 3) == (1, 3)
    assert bounded_trace_interval(-5, 1, 4) == (0, 2)

    with pytest.raises(ValueError, match="at least 2"):
        bounded_trace_interval(0, 1, 1)


def test_asls_baseline_corrected_area_is_invariant_to_drift_shape() -> None:
    # Baseline-drift oracle: ASLS must strip flat, linear, and curved drift so
    # the recovered analyte area does not depend on the baseline shape. This
    # pins drift removal as a relative invariant and sidesteps the (separate)
    # question of ASLS's absolute bias on a clean peak.
    rt = np.linspace(8.0, 10.0, 401)
    peak = 1000.0 * np.exp(-0.5 * ((rt - 9.0) / 0.08) ** 2)
    left = int(np.searchsorted(rt, 8.55))
    right = int(np.searchsorted(rt, 9.45))

    drifts = {
        "zero": np.zeros_like(rt),
        "offset": np.full_like(rt, 300.0),
        "linear": np.linspace(50.0, 250.0, rt.size),
        "curved": 300.0 * np.exp(-0.5 * ((rt - 9.0) / 1.5) ** 2),
    }
    areas: dict[str, float | None] = {}
    for name, drift in drifts.items():
        result = integrate_asls_baseline(peak + drift, rt, left, right)
        areas[name] = result.area_baseline_corrected

    reference = areas["zero"]
    assert reference is not None and reference > 0.0
    for name, area in areas.items():
        # Drift removal agrees to <0.05% across shapes; rel=0.005 still flags a
        # >=0.5% drift-removal failure with comfortable margin.
        assert area == pytest.approx(reference, rel=0.005), name


def test_asls_clean_peak_area_bias_is_documented_tripwire() -> None:
    # Characterization, NOT a correctness target: at this DENSELY sampled fixture
    # (401 pts, ~16 pts/sigma) ASLS bends into the peak base and recovers ~89% of
    # the geometric Gaussian area. NOTE: this ~11% is a dense-sampling artifact;
    # at realistic chromatographic densities (~4-8 pts/sigma) the same params bias
    # only ~1-4% (sweep on 2026-06-14 -> decided not to retune). This pins the
    # CURRENT recovered fraction so an ASLS algorithm change is surfaced; it does
    # not endorse the value. Update the bounds deliberately if ASLS is retuned.
    rt = np.linspace(8.0, 10.0, 401)
    peak = 1000.0 * np.exp(-0.5 * ((rt - 9.0) / 0.08) ** 2)
    left = int(np.searchsorted(rt, 8.55))
    right = int(np.searchsorted(rt, 9.45))
    analytic_area = 1000.0 * 0.08 * float(np.sqrt(2 * np.pi)) * 60.0

    recovered = integrate_asls_baseline(peak, rt, left, right).area_baseline_corrected

    assert recovered is not None
    ratio = recovered / analytic_area
    assert 0.85 <= ratio <= 0.93
