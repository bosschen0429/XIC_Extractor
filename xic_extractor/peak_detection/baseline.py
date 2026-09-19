from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import numpy as np
from numpy.linalg import LinAlgError
from scipy import sparse
from scipy.linalg import solveh_banded
from scipy.sparse.linalg import spsolve

from xic_extractor.peak_detection.integration import integrate_area_counts_seconds

BaselineMethod = Literal["asls"]
AREA_UNCERTAINTY_FORMULA_VERSION = "baseline_residual_mad"
LINEAR_EDGE_RETIRED_MESSAGE = "linear_edge baseline integration is retired; use asls"


@lru_cache(maxsize=512)
def _asls_penalty(n: int, lam: float) -> tuple[sparse.csc_matrix, np.ndarray]:
    """Cache the AsLS smoothness penalty ``lam * D^T D`` for a trace length.

    The second-difference operator depends only on ``n`` and ``lam``, so it is
    built once per ``(n, lam)`` instead of on every ``asls_baseline`` call.
    Returns the sparse penalty (for the rare non-SPD fallback) plus its
    symmetric banded diagonals (offsets 0/+1/+2) in scipy ``solveh_banded``
    upper form for the fast banded solve.
    """
    differences = sparse.diags(
        [1, -2, 1], [0, 1, 2], shape=(n - 2, n), dtype=float, format="csc"
    )
    penalty = (lam * (differences.T @ differences)).tocsc()
    bands = np.zeros((3, n), dtype=float)
    bands[2, :] = penalty.diagonal(0)
    bands[1, 1:] = penalty.diagonal(1)
    bands[0, 2:] = penalty.diagonal(2)
    return penalty, bands


def asls_baseline(
    y: np.ndarray, lam: float = 1e5, p: float = 0.01, n_iter: int = 10
) -> np.ndarray:
    """Asymmetric Least Squares baseline (Eilers & Boelens 2005).

    The penalised system ``(W + lam D^T D) x = W y`` is symmetric positive
    definite and pentadiagonal, so it is solved with a banded Cholesky solver
    (``solveh_banded``) over the cached penalty bands instead of rebuilding a
    general sparse matrix and running superLU on every call. Mathematically
    identical to the previous ``spsolve`` formulation (validated to rtol 1e-9);
    falls back to ``spsolve`` only if a degenerate weight vector makes the
    system non-positive-definite.
    """
    values = np.asarray(y, dtype=float)
    if values.ndim != 1:
        raise ValueError("asls_baseline expects a 1-D trace")
    if not np.all(np.isfinite(values)):
        raise ValueError("asls_baseline input must contain only finite values")
    if lam <= 0:
        raise ValueError("lam must be > 0")
    if not 0 < p < 1:
        raise ValueError("p must be > 0 and < 1")
    if n_iter < 1:
        raise ValueError("n_iter must be >= 1")
    n = len(values)
    if n < 3:
        return values.copy()

    penalty, bands = _asls_penalty(n, float(lam))
    penalty_main = bands[2]
    ab = np.empty((3, n), dtype=float)
    ab[0] = bands[0]
    ab[1] = bands[1]
    weights = np.ones(n)
    baseline = values.copy()
    for _ in range(n_iter):
        ab[2] = penalty_main + weights
        try:
            baseline = solveh_banded(
                ab, weights * values, lower=False, check_finite=False
            )
        except LinAlgError:
            weight_matrix = sparse.diags(weights, 0, format="csc")
            baseline = spsolve((weight_matrix + penalty).tocsc(), weights * values)
        weights = p * (values > baseline) + (1 - p) * (values < baseline)
    return baseline


@dataclass(frozen=True)
class BaselineIntegration:
    area_baseline_corrected: float
    area_uncertainty: float | None
    baseline_type: str
    baseline_score: float | None
    area_uncertainty_formula_version: str = ""
    baseline_residual_mad: float | None = None
    area_uncertainty_noise_source: str = ""


def integrate_asls_baseline(
    intensity_values: np.ndarray,
    rt_values: np.ndarray,
    left: int,
    right: int,
    *,
    lam: float = 1e5,
    p: float = 0.01,
    n_iter: int = 10,
    baseline_values: np.ndarray | None = None,
) -> BaselineIntegration:
    rt = np.asarray(rt_values, dtype=float)
    intensity = np.asarray(intensity_values, dtype=float)
    _validate_trace_arrays(rt, intensity)
    left_index, right_index = bounded_trace_interval(left, right, len(rt))
    full_baseline = (
        np.asarray(baseline_values, dtype=float)
        if baseline_values is not None
        else asls_baseline(intensity, lam=lam, p=p, n_iter=n_iter)
    )
    if full_baseline.shape != intensity.shape:
        raise ValueError("baseline_values must match intensity_values shape")
    segment = intensity[left_index:right_index]
    segment_rt = rt[left_index:right_index]
    baseline_segment = full_baseline[left_index:right_index]
    corrected = np.maximum(segment - baseline_segment, 0.0)
    corrected_area = _area_counts_seconds(corrected, segment_rt)
    raw_area = integrate_area_counts_seconds(intensity, rt, left_index, right_index)
    _baseline_values, residual_mad = compute_asls_residual_mad(
        intensity,
        baseline_values=full_baseline,
    )
    uncertainty = _area_uncertainty_counts_seconds(
        rt,
        left_index,
        right_index,
        baseline_residual_mad=residual_mad,
    )
    return BaselineIntegration(
        area_baseline_corrected=corrected_area,
        area_uncertainty=uncertainty,
        baseline_type="asls",
        baseline_score=_safe_ratio(corrected_area, raw_area),
        area_uncertainty_formula_version=AREA_UNCERTAINTY_FORMULA_VERSION,
        baseline_residual_mad=residual_mad,
        area_uncertainty_noise_source="asls_residual"
        if residual_mad is not None
        else "",
    )


def integrate_with_baseline(
    intensity_values: np.ndarray,
    rt_values: np.ndarray,
    left: int,
    right: int,
    *,
    baseline_method: str = "asls",
    baseline_values: np.ndarray | None = None,
    uncertainty_baseline_values: np.ndarray | None = None,
    baseline_residual_mad: float | None = None,
    baseline_residual_mad_source: str = "asls_residual",
) -> BaselineIntegration:
    _ = uncertainty_baseline_values, baseline_residual_mad, baseline_residual_mad_source
    if baseline_method == "asls":
        return integrate_asls_baseline(
            intensity_values,
            rt_values,
            left,
            right,
            baseline_values=baseline_values,
        )
    if baseline_method == "linear_edge":
        raise ValueError(LINEAR_EDGE_RETIRED_MESSAGE)
    raise ValueError("baseline_method must be 'asls'")


def _validate_trace_arrays(rt: np.ndarray, intensity: np.ndarray) -> None:
    if rt.ndim != 1 or intensity.ndim != 1:
        raise ValueError("rt_values and intensity_values must be one-dimensional")
    if len(rt) != len(intensity):
        raise ValueError("rt_values and intensity_values must have the same length")
    if len(rt) < 2:
        raise ValueError("rt_values and intensity_values must contain at least 2 scans")


def bounded_trace_interval(
    left_index: int,
    right_index: int,
    n_points: int,
) -> tuple[int, int]:
    if n_points < 2:
        raise ValueError("n_points must be at least 2")
    bounded_left = max(0, min(left_index, n_points - 2))
    bounded_right = max(bounded_left + 2, min(right_index, n_points))
    return bounded_left, bounded_right


def _area_counts_seconds(values: np.ndarray, rt_values: np.ndarray) -> float:
    return float(np.trapezoid(values, rt_values)) * 60.0


def compute_asls_residual_mad(
    intensity_values: np.ndarray,
    *,
    baseline_values: np.ndarray | None = None,
) -> tuple[np.ndarray | None, float | None]:
    values = np.asarray(intensity_values, dtype=float)
    if len(values) < 5 or not np.all(np.isfinite(values)):
        return None, None
    if baseline_values is not None:
        baseline = np.asarray(baseline_values, dtype=float)
    else:
        try:
            baseline = asls_baseline(values)
        except (ValueError, FloatingPointError):
            return None, None
    if baseline.shape != values.shape:
        if baseline_values is not None:
            raise ValueError("baseline_values must match intensity_values shape")
        return None, None
    residual = values - baseline
    residual_mad = float(np.median(np.abs(residual - np.median(residual))))
    if not np.isfinite(residual_mad):
        return baseline, None
    return baseline, residual_mad


def _area_uncertainty_counts_seconds(
    rt_values: np.ndarray,
    left_index: int,
    right_index: int,
    *,
    baseline_residual_mad: float | None = None,
) -> float | None:
    noise = baseline_residual_mad
    if noise is None or not np.isfinite(noise):
        return None
    scan_period_s = _median_scan_period_seconds(rt_values)
    if scan_period_s is None:
        return None
    n_points = right_index - left_index
    if n_points < 2:
        return None
    return float(noise * scan_period_s * np.sqrt(n_points))


def _median_scan_period_seconds(rt_values: np.ndarray) -> float | None:
    if len(rt_values) < 2:
        return None
    diffs = np.diff(rt_values)
    if len(diffs) == 0 or not np.all(np.isfinite(diffs)):
        return None
    scan_period = float(np.median(diffs)) * 60.0
    if not np.isfinite(scan_period) or scan_period <= 0:
        return None
    return scan_period


def _pre_peak_mad(
    intensity_values: np.ndarray,
    left_index: int,
    *,
    window_size: int = 10,
) -> float | None:
    window_left = max(0, left_index - window_size)
    window = intensity_values[window_left:left_index]
    window = window[np.isfinite(window)]
    if len(window) < 3:
        return None
    median = float(np.median(window))
    mad = float(np.median(np.abs(window - median)))
    if not np.isfinite(mad):
        return None
    return mad


def _safe_ratio(value: float, reference: float) -> float | None:
    if reference <= 0:
        return None
    return max(0.0, min(1.0, value / reference))
