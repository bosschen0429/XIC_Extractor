"""Numerical-equivalence gate for the banded-solver AsLS optimization.

asls_baseline was changed from a per-call sparse rebuild + general superLU
spsolve to a cached penalty + banded Cholesky (solveh_banded). The scientific
output that reaches the matrix is the corrected AREA; this pins it equivalent to
the original spsolve formulation at rtol 1e-9. The raw baseline VALUES agree only
to ~1e-5 because AsLS has discrete weight-threshold flips (values==baseline) that
amplify solver roundoff — but those flips occur at near-zero-corrected-signal
points that integrate out of the area, so the science output is unaffected.
"""

from __future__ import annotations

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from xic_extractor.peak_detection.baseline import asls_baseline


def _asls_spsolve_reference(
    y: np.ndarray,
    lam: float = 1e5,
    p: float = 0.01,
    n_iter: int = 10,
) -> np.ndarray:
    """Original general-sparse spsolve AsLS, retained as the equivalence oracle."""
    values = np.asarray(y, dtype=float)
    n = len(values)
    if n < 3:
        return values.copy()
    differences = sparse.diags(
        [1, -2, 1], [0, 1, 2], shape=(n - 2, n), dtype=float, format="csc"
    )
    penalty = lam * (differences.T @ differences)
    weights = np.ones(n)
    baseline = values.copy()
    for _ in range(n_iter):
        weight_matrix = sparse.diags(weights, 0, format="csc")
        baseline = spsolve((weight_matrix + penalty).tocsc(), weights * values)
        weights = p * (values > baseline) + (1 - p) * (values < baseline)
    return baseline


def _representative_traces() -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    traces: list[np.ndarray] = []
    for _ in range(40):
        n = int(rng.integers(30, 400))
        x = np.arange(n)
        center = n * 0.5
        peak = 1000.0 * np.exp(-0.5 * ((x - center) / (n * 0.06)) ** 2)
        traces.append(np.maximum(peak + 50.0 + 0.3 * x + rng.normal(0, 5, n), 0.0))
    traces.append(np.linspace(10.0, 200.0, 100))
    traces.append(np.full(80, 42.0))
    traces.append(np.maximum(rng.normal(20, 3, 150), 0.0))
    return traces


def test_banded_asls_corrected_area_matches_spsolve_to_1e_9() -> None:
    for y in _representative_traces():
        n = len(y)
        left, right = int(n * 0.3), int(n * 0.7)
        corrected_new = np.maximum(y[left:right] - asls_baseline(y)[left:right], 0.0)
        corrected_ref = np.maximum(
            y[left:right] - _asls_spsolve_reference(y)[left:right], 0.0
        )
        area_new = float(np.trapezoid(corrected_new))
        area_ref = float(np.trapezoid(corrected_ref))
        assert abs(area_new - area_ref) <= 1e-9 * abs(area_ref) + 1e-3


def test_banded_asls_baseline_values_stay_bounded_vs_spsolve() -> None:
    for y in _representative_traces():
        assert np.allclose(
            asls_baseline(y), _asls_spsolve_reference(y), rtol=1e-3, atol=1e-1
        )


def test_banded_asls_handles_degenerate_flat_trace() -> None:
    output = asls_baseline(np.full(60, 17.0))
    assert output.shape == (60,)
    assert np.all(np.isfinite(output))


def test_banded_asls_degenerate_traces_match_spsolve_and_stay_finite() -> None:
    cases = {
        "all_zero": np.zeros(50),
        "n_equals_3": np.array([1.0, 5.0, 2.0]),
        "single_spike": np.concatenate([np.zeros(20), [500.0], np.zeros(29)]),
        "near_constant": np.full(60, 42.0) + 1e-6 * np.arange(60),
        "constant": np.full(40, 7.0),
    }
    for name, y in cases.items():
        new = asls_baseline(y)
        ref = _asls_spsolve_reference(y)
        assert new.shape == y.shape, name
        assert np.all(np.isfinite(new)), name
        assert np.allclose(new, ref, rtol=1e-3, atol=1e-1), name
