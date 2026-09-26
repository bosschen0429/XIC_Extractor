import numpy as np
import pytest

from xic_extractor.peak_detection.baseline import asls_baseline
from xic_extractor.peak_detection.scoring_metrics import (
    compute_local_sn_cache,
    local_sn_severity,
    nl_support_severity,
    noise_shape_severity,
    peak_width_severity,
    rt_centrality_severity,
    rt_prior_severity,
    symmetry_severity,
)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.3, 1),
        (0.5, 0),
        (1.0, 0),
        (0.6, 0),
        (1.8, 0),
        (2.0, 0),
        (3.0, 1),
        (0.4, 1),
        (2.5, 1),
        (0.2, 2),
        (4.0, 2),
    ],
)
def test_symmetry_severity(ratio: float, expected: int) -> None:
    severity, label = symmetry_severity(ratio)
    assert severity == expected
    assert label == "symmetry"


def test_symmetry_nan_is_major() -> None:
    severity, label = symmetry_severity(float("nan"))
    assert severity == 2
    assert label == "symmetry"


def _make_trace(
    peak_height: float, noise_std: float = 0.05, n: int = 400, seed: int = 0
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.arange(n)
    peak = peak_height * np.exp(-((x - n / 2) ** 2) / (2 * 5**2))
    noise = rng.normal(0.0, noise_std, n)
    return peak + noise + 1.0


def test_sn_cache_reuses_live_baseline_with_whole_trace_residual_mad(
    monkeypatch,
) -> None:
    values = _make_trace(10.0)
    baseline = asls_baseline(values)
    residual = values - baseline
    expected_mad = float(np.median(np.abs(residual - np.median(residual))))

    def unexpected_solve(*_args, **_kwargs):
        raise AssertionError("live baseline must not be solved again")

    monkeypatch.setattr(
        "xic_extractor.peak_detection.scoring_metrics.asls_baseline", unexpected_solve
    )
    actual_baseline, mad = compute_local_sn_cache(
        values, baseline_unclipped=tuple(baseline)
    )
    np.testing.assert_array_equal(actual_baseline, baseline)
    assert mad == expected_mad


@pytest.mark.parametrize("error", [ValueError, FloatingPointError, TypeError])
def test_standalone_sn_cache_preserves_solver_exception_policy(
    monkeypatch, error
) -> None:
    def failed_solve(*_args, **_kwargs):
        raise error("controlled solver failure")

    monkeypatch.setattr(
        "xic_extractor.peak_detection.scoring_metrics.asls_baseline", failed_solve
    )
    if error is ValueError:
        assert compute_local_sn_cache(_make_trace(10.0)) == (None, None)
    else:
        with pytest.raises(error, match="controlled solver failure"):
            compute_local_sn_cache(_make_trace(10.0))


def test_local_sn_pass_high_peak() -> None:
    y = _make_trace(peak_height=10.0, noise_std=0.05)
    sev, label = local_sn_severity(y, apex_index=200, dirty_matrix=False)
    assert sev == 0
    assert label == "local_sn"


def test_local_sn_minor_low_peak() -> None:
    y = _make_trace(peak_height=0.03, noise_std=0.05)
    sev, _ = local_sn_severity(y, apex_index=200, dirty_matrix=False)
    assert sev == 1


def test_local_sn_major_no_peak() -> None:
    y = _make_trace(peak_height=0.0, noise_std=0.05)
    sev, _ = local_sn_severity(y, apex_index=200, dirty_matrix=False)
    assert sev == 2


def test_dirty_matrix_relaxes_threshold() -> None:
    y = _make_trace(peak_height=0.3, noise_std=0.05)
    sev_default, _ = local_sn_severity(y, apex_index=200, dirty_matrix=False)
    sev_dirty, _ = local_sn_severity(y, apex_index=200, dirty_matrix=True)
    assert sev_dirty <= sev_default


def test_local_sn_invalid_trace_is_major() -> None:
    y = _make_trace(peak_height=10.0)
    y[200] = np.nan
    sev, label = local_sn_severity(y, apex_index=200, dirty_matrix=False)
    assert sev == 2
    assert label == "local_sn"


def test_nl_present_and_match_is_pass() -> None:
    sev, label = nl_support_severity(ms2_present=True, nl_match=True)
    assert sev == 0
    assert label == "nl_support"


def test_ms2_present_but_no_nl_match_is_major() -> None:
    sev, _ = nl_support_severity(ms2_present=True, nl_match=False)
    assert sev == 2


def test_no_ms2_is_minor() -> None:
    sev, _ = nl_support_severity(ms2_present=False, nl_match=False)
    assert sev == 1


def test_rt_prior_no_prior_skips() -> None:
    sev, label = rt_prior_severity(observed=10.0, prior=None, sigma=None)
    assert sev == 0
    assert label == "rt_prior"


def test_rt_prior_within_2sigma_pass() -> None:
    sev, _ = rt_prior_severity(observed=10.1, prior=10.0, sigma=0.1)
    assert sev == 0


def test_rt_prior_2_to_5_sigma_minor() -> None:
    sev, _ = rt_prior_severity(observed=10.3, prior=10.0, sigma=0.1)
    assert sev == 1


def test_rt_prior_exactly_2sigma_is_minor() -> None:
    sev, _ = rt_prior_severity(observed=10.2, prior=10.0, sigma=0.1)
    assert sev == 1


def test_rt_prior_beyond_5_sigma_major() -> None:
    sev, _ = rt_prior_severity(observed=11.0, prior=10.0, sigma=0.1)
    assert sev == 2


def test_rt_prior_exactly_5sigma_is_major() -> None:
    sev, _ = rt_prior_severity(observed=10.5, prior=10.0, sigma=0.1)
    assert sev == 2


def test_rt_prior_no_sigma_uses_1min_rule() -> None:
    sev, _ = rt_prior_severity(observed=10.3, prior=10.0, sigma=None)
    assert sev == 1
    sev, _ = rt_prior_severity(observed=11.5, prior=10.0, sigma=None)
    assert sev == 2


def test_rt_prior_no_sigma_exactly_soft_boundary_is_minor() -> None:
    sev, _ = rt_prior_severity(observed=10.2, prior=10.0, sigma=None)
    assert sev == 1


def test_rt_prior_no_sigma_exactly_hard_boundary_is_major() -> None:
    sev, _ = rt_prior_severity(observed=11.0, prior=10.0, sigma=None)
    assert sev == 2


@pytest.mark.parametrize(
    ("observed", "prior", "sigma"),
    [
        (float("nan"), 10.0, 0.1),
        (float("inf"), 10.0, 0.1),
        (10.0, float("nan"), 0.1),
        (10.0, float("inf"), 0.1),
    ],
)
def test_rt_prior_non_finite_observed_or_prior_is_major(
    observed: float, prior: float, sigma: float | None
) -> None:
    sev, label = rt_prior_severity(observed=observed, prior=prior, sigma=sigma)
    assert sev == 2
    assert label == "rt_prior"


def test_rt_prior_non_finite_sigma_falls_back_to_no_sigma_rule() -> None:
    sev, label = rt_prior_severity(observed=10.3, prior=10.0, sigma=float("nan"))
    assert sev == 1
    assert label == "rt_prior"


def test_rt_centrality_center_pass() -> None:
    sev, label = rt_centrality_severity(observed=5.0, rt_min=0.0, rt_max=10.0)
    assert sev == 0
    assert label == "rt_centrality"


def test_rt_centrality_within_10pct_soft() -> None:
    sev, _ = rt_centrality_severity(observed=0.8, rt_min=0.0, rt_max=10.0)
    assert sev == 1


def test_rt_centrality_exactly_10pct_passes() -> None:
    sev, _ = rt_centrality_severity(observed=1.0, rt_min=0.0, rt_max=10.0)
    assert sev == 0


def test_rt_centrality_within_1pct_major() -> None:
    sev, _ = rt_centrality_severity(observed=0.05, rt_min=0.0, rt_max=10.0)
    assert sev == 2


def test_rt_centrality_exactly_1pct_is_minor() -> None:
    sev, _ = rt_centrality_severity(observed=0.1, rt_min=0.0, rt_max=10.0)
    assert sev == 1


@pytest.mark.parametrize(
    ("observed", "rt_min", "rt_max"),
    [
        (float("nan"), 0.0, 10.0),
        (float("inf"), 0.0, 10.0),
        (5.0, float("nan"), 10.0),
        (5.0, 0.0, float("nan")),
        (5.0, 10.0, 0.0),
    ],
)
def test_rt_centrality_non_finite_or_invalid_window_is_major(
    observed: float, rt_min: float, rt_max: float
) -> None:
    sev, label = rt_centrality_severity(
        observed=observed, rt_min=rt_min, rt_max=rt_max
    )
    assert sev == 2
    assert label == "rt_centrality"


def test_noise_shape_smooth_pass() -> None:
    x = np.linspace(-3, 3, 201)
    y = np.exp(-x * x)
    sev, label = noise_shape_severity(y)
    assert sev == 0
    assert label == "noise_shape"


def test_noise_shape_ragged_minor() -> None:
    rng = np.random.default_rng(1)
    y = rng.normal(0, 1, 201)
    sev, _ = noise_shape_severity(y)
    assert sev == 1


def test_noise_shape_alternating_major() -> None:
    y = np.tile([0.0, 10.0], 101)[:201]
    sev, _ = noise_shape_severity(y)
    assert sev == 2


@pytest.mark.parametrize(
    "y",
    [
        np.array([0.0, 1.0, np.nan, 2.0]),
        np.array([0.0, 1.0, np.inf, 2.0]),
        np.array([[0.0, 1.0, 2.0], [0.0, 1.0, 2.0]]),
    ],
)
def test_noise_shape_malformed_trace_is_major(y: np.ndarray) -> None:
    sev, label = noise_shape_severity(y)
    assert sev == 2
    assert label == "noise_shape"


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (0.3, 1),
        (0.5, 0),
        (1.0, 0),
        (0.7, 0),
        (1.4, 0),
        (2.0, 0),
        (3.0, 1),
        (0.4, 1),
        (2.5, 1),
        (0.2, 2),
        (4.0, 2),
        (None, 0),
    ],
)
def test_peak_width_severity(ratio: float | None, expected: int) -> None:
    sev, label = peak_width_severity(ratio)
    assert sev == expected
    assert label == "peak_width"


def test_peak_width_nan_is_major() -> None:
    sev, label = peak_width_severity(float("nan"))
    assert sev == 2
    assert label == "peak_width"
