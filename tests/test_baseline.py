import numpy as np
import pytest

from xic_extractor.peak_detection.baseline import (
    asls_baseline,
    asls_baseline_on_rt,
)


def test_baseline_on_flat_trace_returns_constant() -> None:
    y = np.full(200, 5.0)
    bl = asls_baseline(y, lam=1e5, p=0.01)
    assert np.allclose(bl, 5.0, atol=0.1)


def test_baseline_tracks_slow_hump_but_not_sharp_peak() -> None:
    n = 400
    x = np.arange(n)
    hump = 3.0 * np.exp(-((x - n / 2) ** 2) / (2 * 80**2))
    peak = 20.0 * np.exp(-((x - n / 2) ** 2) / (2 * 5**2))
    y = hump + peak + 0.1 * np.sin(x / 5)
    bl = asls_baseline(y, lam=1e5, p=0.01)
    apex = n // 2
    assert bl[apex] < peak[apex] * 0.3
    side = n // 4
    assert abs(bl[side] - hump[side]) < 0.5


def test_baseline_shape_matches_input() -> None:
    y = np.random.RandomState(0).normal(0, 1, 128) + 10
    bl = asls_baseline(y, lam=1e4, p=0.01)
    assert bl.shape == y.shape


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_baseline_rejects_non_finite_values(bad_value: float) -> None:
    y = np.array([1.0, bad_value, 2.0])
    with pytest.raises(ValueError, match="finite"):
        asls_baseline(y)


def test_baseline_rejects_non_1d_input() -> None:
    y = np.ones((5, 2))
    with pytest.raises(ValueError, match="1-D"):
        asls_baseline(y)


def test_short_baseline_returns_float_copy() -> None:
    y = np.array([1, 2])
    bl = asls_baseline(y)
    assert bl.dtype.kind == "f"
    assert np.array_equal(bl, np.array([1.0, 2.0]))
    assert not np.shares_memory(y, bl)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lam": 0}, "lam"),
        ({"p": 0}, "p"),
        ({"p": 1}, "p"),
        ({"n_iter": 0}, "n_iter"),
    ],
)
def test_baseline_rejects_invalid_parameters(
    kwargs: dict[str, float | int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        asls_baseline(np.ones(5), **kwargs)


@pytest.mark.parametrize("lam", [0.1, 1.0, 10.0])
@pytest.mark.parametrize("n_points", [301, 901, 2701])
@pytest.mark.parametrize("slope", [0.0, 1.25])
def test_rt_baseline_preserves_linear_background(
    lam: float, n_points: int, slope: float
) -> None:
    unit_rt = np.linspace(0.0, 1.0, n_points)
    rt = 3.0 * (unit_rt + 0.55 * np.sin(2.0 * np.pi * unit_rt) / (2.0 * np.pi))
    expected = 7.5 + slope * rt

    baseline = asls_baseline_on_rt(rt, expected, lam=lam, p=0.01)

    assert np.all(np.isfinite(baseline))
    assert np.allclose(baseline, expected, rtol=2e-7, atol=2e-7)


@pytest.mark.parametrize("lam", [0.1, 1.0, 10.0])
def test_rt_baseline_gaussian_area_is_stable_across_sampling_grids(
    lam: float,
) -> None:
    duration = 3.0
    centre = 1.55
    sigma = 0.08
    amplitude = 25.0
    expected_background_area = 10.0 * duration + 0.4 * duration**2
    expected_peak_area = amplitude * sigma * np.sqrt(2.0 * np.pi)
    common_rt = np.linspace(0.0, duration, 601)
    common_baselines: list[np.ndarray] = []
    baseline_areas: list[float] = []
    corrected_areas: list[float] = []

    for n_points, nonuniform in [(301, False), (901, False), (2701, True)]:
        unit_rt = np.linspace(0.0, 1.0, n_points)
        rt = duration * unit_rt
        if nonuniform:
            rt = duration * (
                unit_rt + 0.55 * np.sin(2.0 * np.pi * unit_rt) / (2.0 * np.pi)
            )
        background = 10.0 + 0.8 * rt
        peak = amplitude * np.exp(-0.5 * ((rt - centre) / sigma) ** 2)
        intensity = background + peak

        assert np.trapezoid(background, rt) == pytest.approx(
            expected_background_area, rel=1e-12
        )
        assert np.trapezoid(peak, rt) == pytest.approx(expected_peak_area, rel=2e-6)

        baseline = asls_baseline_on_rt(rt, intensity, lam=lam, p=0.01)
        common_baselines.append(np.interp(common_rt, rt, baseline))
        baseline_areas.append(float(np.trapezoid(baseline, rt)))
        corrected_areas.append(float(np.trapezoid(intensity - baseline, rt)))

    for common_baseline in common_baselines:
        assert np.all(np.isfinite(common_baseline))
        assert common_baseline == pytest.approx(common_baselines[0], rel=2e-3, abs=0.02)
    for baseline_area in baseline_areas:
        assert baseline_area == pytest.approx(baseline_areas[0], rel=2e-3, abs=0.02)
    for corrected_area in corrected_areas:
        assert corrected_area == pytest.approx(corrected_areas[0], rel=2e-3, abs=0.02)
    assert baseline_areas[0] == pytest.approx(expected_background_area, rel=0.02)
    assert corrected_areas[0] == pytest.approx(expected_peak_area, rel=0.05)


def test_rt_baseline_uniform_grid_matches_index_space_scaling() -> None:
    rt = np.linspace(0.0, 3.0, 301)
    spacing = rt[1] - rt[0]
    intensity = 8.0 + 0.5 * rt + 20.0 * np.exp(-0.5 * ((rt - 1.5) / 0.08) ** 2)
    physical_lam = 1.0

    rt_baseline = asls_baseline_on_rt(rt, intensity, lam=physical_lam)
    index_baseline = asls_baseline(intensity, lam=physical_lam / spacing**4)

    # Trapezoidal end cells differ from the legacy equal-weight objective.
    assert rt_baseline[10:-10] == pytest.approx(
        index_baseline[10:-10], rel=2e-3, abs=0.02
    )


@pytest.mark.parametrize(
    ("rt", "intensity", "message"),
    [
        (np.array([0.0, 1.0]), np.array([1.0]), "same length"),
        (np.array([0.0, 1.0, 0.5]), np.ones(3), "strictly increasing"),
        (np.array([0.0, np.nan, 1.0]), np.ones(3), "finite"),
    ],
)
def test_rt_baseline_rejects_invalid_trace(
    rt: np.ndarray, intensity: np.ndarray, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        asls_baseline_on_rt(rt, intensity, lam=1.0)
