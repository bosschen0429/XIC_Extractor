from __future__ import annotations

import math

import numpy as np

from xic_extractor.peak_detection.baseline import asls_baseline

LABEL_SYMMETRY = "symmetry"
LABEL_LOCAL_SN = "local_sn"
LABEL_NL = "nl_support"
LABEL_RT_PRIOR = "rt_prior"
LABEL_RT_CENTRALITY = "rt_centrality"
LABEL_NOISE_SHAPE = "noise_shape"
LABEL_PEAK_WIDTH = "peak_width"

# These thresholds are scoring/selection heuristics, not product-presence
# authority. Promotion to a hard detection policy needs a contract and
# biological-matrix validation artifact.
_SYMMETRY_SOFT_LOW, _SYMMETRY_SOFT_HIGH = 0.5, 2.0
_SYMMETRY_HARD_LOW, _SYMMETRY_HARD_HIGH = 0.3, 3.0
_SN_SOFT_THRESHOLD = 3.0
_SN_HARD_THRESHOLD = 2.0
_SN_DIRTY_SOFT_THRESHOLD = 2.0
_SN_DIRTY_HARD_THRESHOLD = 1.3
_RT_PRIOR_SIGMA_SOFT = 2.0
_RT_PRIOR_SIGMA_HARD = 5.0
_RT_PRIOR_NO_SIGMA_SOFT_MIN = 0.2
_RT_PRIOR_NO_SIGMA_HARD_MIN = 1.0


def short_scale_noise_mad(rt: np.ndarray, intensity: np.ndarray) -> float:
    """Unscaled MAD of normalized, RT-weighted three-point residuals."""
    if len(intensity) < 3:
        return 0.0
    weight = (rt[2:] - rt[1:-1]) / (rt[2:] - rt[:-2])
    residual = (
        weight * intensity[:-2]
        + (1.0 - weight) * intensity[2:]
        - intensity[1:-1]
    ) / np.sqrt(1.0 + weight**2 + (1.0 - weight)**2)
    return float(np.median(np.abs(residual - np.median(residual))))


def symmetry_severity(half_width_ratio: float) -> tuple[int, str]:
    if not _is_finite(half_width_ratio):
        return 2, LABEL_SYMMETRY
    if (
        half_width_ratio < _SYMMETRY_HARD_LOW
        or half_width_ratio > _SYMMETRY_HARD_HIGH
    ):
        return 2, LABEL_SYMMETRY
    if (
        half_width_ratio < _SYMMETRY_SOFT_LOW
        or half_width_ratio > _SYMMETRY_SOFT_HIGH
    ):
        return 1, LABEL_SYMMETRY
    return 0, LABEL_SYMMETRY


def local_sn_severity(
    intensity: np.ndarray,
    apex_index: int,
    dirty_matrix: bool,
    *,
    baseline: np.ndarray | None = None,
    residual_mad: float | None = None,
) -> tuple[int, str]:
    """S/N ratio of peak apex vs. MAD of trace residual after AsLS baseline."""
    values = np.asarray(intensity, dtype=float)
    if len(values) < 5 or apex_index < 0 or apex_index >= len(values):
        return 2, LABEL_LOCAL_SN
    cached_baseline = baseline
    mad = residual_mad
    if cached_baseline is None or mad is None:
        cached_baseline, mad = compute_local_sn_cache(values)
    if cached_baseline is None or mad is None:
        return 2, LABEL_LOCAL_SN
    if mad <= 0:
        return 0, LABEL_LOCAL_SN
    peak_above_baseline = float(values[apex_index] - cached_baseline[apex_index])
    ratio = peak_above_baseline / mad
    hard = _SN_DIRTY_HARD_THRESHOLD if dirty_matrix else _SN_HARD_THRESHOLD
    soft = _SN_DIRTY_SOFT_THRESHOLD if dirty_matrix else _SN_SOFT_THRESHOLD
    if ratio < hard:
        return 2, LABEL_LOCAL_SN
    if ratio < soft:
        return 1, LABEL_LOCAL_SN
    return 0, LABEL_LOCAL_SN


def compute_local_sn_cache(
    intensity: np.ndarray,
    *,
    baseline_unclipped: tuple[float, ...] | None = None,
) -> tuple[np.ndarray | None, float | None]:
    """Return unclipped AsLS and whole-trace residual MAD, reusing live preparation."""
    values = np.asarray(intensity, dtype=float)
    if len(values) < 5 or not np.all(np.isfinite(values)):
        return None, None
    if baseline_unclipped is None:
        try:
            baseline = asls_baseline(values)
        except ValueError:
            return None, None
    else:
        baseline = np.asarray(baseline_unclipped, dtype=float)
    residual = values - baseline
    mad = float(np.median(np.abs(residual - np.median(residual))))
    return baseline, mad


def nl_support_severity(ms2_present: bool, nl_match: bool) -> tuple[int, str]:
    if ms2_present and nl_match:
        return 0, LABEL_NL
    if ms2_present and not nl_match:
        return 2, LABEL_NL
    return 1, LABEL_NL


def rt_prior_severity(
    observed: float,
    prior: float | None,
    sigma: float | None,
) -> tuple[int, str]:
    if prior is None:
        return 0, LABEL_RT_PRIOR
    if not _is_finite(observed) or not _is_finite(prior):
        return 2, LABEL_RT_PRIOR
    deviation = abs(observed - prior)
    if sigma is not None and _is_finite(sigma) and sigma > 0:
        n_sigma = deviation / sigma
        if _at_least(n_sigma, _RT_PRIOR_SIGMA_HARD):
            return 2, LABEL_RT_PRIOR
        if _at_least(n_sigma, _RT_PRIOR_SIGMA_SOFT):
            return 1, LABEL_RT_PRIOR
        return 0, LABEL_RT_PRIOR
    if _at_least(deviation, _RT_PRIOR_NO_SIGMA_HARD_MIN):
        return 2, LABEL_RT_PRIOR
    if _at_least(deviation, _RT_PRIOR_NO_SIGMA_SOFT_MIN):
        return 1, LABEL_RT_PRIOR
    return 0, LABEL_RT_PRIOR


def rt_centrality_severity(
    observed: float, rt_min: float, rt_max: float
) -> tuple[int, str]:
    if not (_is_finite(observed) and _is_finite(rt_min) and _is_finite(rt_max)):
        return 2, LABEL_RT_CENTRALITY
    span = rt_max - rt_min
    if span <= 0:
        return 2, LABEL_RT_CENTRALITY
    distance_low = observed - rt_min
    distance_high = rt_max - observed
    min_edge = min(distance_low, distance_high) / span
    if min_edge < 0.01:
        return 2, LABEL_RT_CENTRALITY
    if min_edge < 0.10:
        return 1, LABEL_RT_CENTRALITY
    return 0, LABEL_RT_CENTRALITY


def noise_shape_severity(intensity: np.ndarray) -> tuple[int, str]:
    """Jaggedness: sum of abs second differences normalised by peak span."""
    values = np.asarray(intensity, dtype=float)
    if values.ndim != 1 or not np.all(np.isfinite(values)):
        return 2, LABEL_NOISE_SHAPE
    if len(values) < 3:
        return 0, LABEL_NOISE_SHAPE
    span = float(values.max() - values.min())
    if span <= 0:
        return 0, LABEL_NOISE_SHAPE
    second_diff = np.abs(np.diff(values, n=2))
    jagged = float(second_diff.sum() / (span * len(values)))
    if jagged > 0.5:
        return 2, LABEL_NOISE_SHAPE
    if jagged > 0.3:
        return 1, LABEL_NOISE_SHAPE
    return 0, LABEL_NOISE_SHAPE


def peak_width_severity(fwhm_ratio: float | None) -> tuple[int, str]:
    if fwhm_ratio is None:
        return 0, LABEL_PEAK_WIDTH
    if not _is_finite(fwhm_ratio):
        return 2, LABEL_PEAK_WIDTH
    if fwhm_ratio < 0.3 or fwhm_ratio > 3.0:
        return 2, LABEL_PEAK_WIDTH
    if fwhm_ratio < 0.5 or fwhm_ratio > 2.0:
        return 1, LABEL_PEAK_WIDTH
    return 0, LABEL_PEAK_WIDTH


def outside_rt_window(observed: float, rt_min: float, rt_max: float) -> bool:
    if not (_is_finite(observed) and _is_finite(rt_min) and _is_finite(rt_max)):
        return True
    return observed < rt_min or observed > rt_max


def _is_finite(value: float) -> bool:
    return math.isfinite(float(value))


def _at_least(value: float, threshold: float) -> bool:
    return value >= threshold or math.isclose(
        value,
        threshold,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
