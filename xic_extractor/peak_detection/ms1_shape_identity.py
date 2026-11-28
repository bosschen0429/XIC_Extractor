from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_OWN_MAX_SMOOTH_POINTS = 15
DEFAULT_LOCAL_HALF_WINDOW_MIN = 0.35
DEFAULT_LOCAL_GRID_SIZE = 175
DEFAULT_COMPETING_EXCLUSION_WINDOW_MIN = 0.20
DEFAULT_COMPETING_MIN_RATIO = 0.05


@dataclass(frozen=True)
class ShapeSimilarityResult:
    status: str
    reason: str
    similarity: float | None = None
    compared_point_count: int = 0


@dataclass(frozen=True)
class LocalPeakShape:
    """Apex-relative, own-max signal samples; missing context stays missing.

    This is correspondence evidence, not a new baseline, peak selection or
    integration. Prepare it once per observation, before comparing owners.
    """

    half_window_min: float
    intensity: tuple[float | None, ...]
    source_point_count: int
    half_height_point_count: int | None = None
    edge_intensity_ratio: tuple[float, float] | None = None


def prepare_local_peak_shape(
    rt: Sequence[float | int | None],
    intensity: Sequence[float | int | None],
    *,
    apex_rt_min: float,
    half_window_min: float = DEFAULT_LOCAL_HALF_WINDOW_MIN,
    grid_size: int = DEFAULT_LOCAL_GRID_SIZE,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> LocalPeakShape:
    """Prepare selected-primary evidence without changing the observation.

    Only an observed nonpositive gap separates a neighboring signal island.
    Continuous background is retained; no baseline subtraction, width scaling,
    apex reselection or shift search is performed. Resolution counts measured
    half-height samples, never the zeros or interpolated/smoothed grid points.
    """
    if isinstance(half_window_min, bool):
        raise ValueError("local peak shape requires a finite normalized RT grid")
    if grid_size < 5 or not math.isfinite(half_window_min) or half_window_min <= 0:
        raise ValueError("local peak shape requires a positive window and >=5 points")
    grid = np.linspace(-half_window_min, half_window_min, grid_size)
    rt_array = _float_array(rt)
    intensity_array = _float_array(intensity)
    if rt_array.size != intensity_array.size:
        return LocalPeakShape(half_window_min, (None,) * grid_size, 0)
    relative_rt = rt_array - apex_rt_min
    mask = (
        np.isfinite(relative_rt)
        & np.isfinite(intensity_array)
        & (relative_rt >= -half_window_min)
        & (relative_rt <= half_window_min)
    )
    source_count = int(np.sum(mask))
    if source_count < 3:
        return LocalPeakShape(half_window_min, (None,) * grid_size, source_count)
    order = np.argsort(relative_rt[mask])
    relative_rt = relative_rt[mask][order]
    primary = intensity_array[mask][order].copy()
    apex_index = int(np.argmin(abs(relative_rt)))
    half_height_count = None
    edges = None
    if primary[apex_index] > 0:
        start = stop = apex_index
        while start > 0 and primary[start - 1] > 0:
            start -= 1
        while stop + 1 < source_count and primary[stop + 1] > 0:
            stop += 1
        primary[:start] = 0
        primary[stop + 1 :] = 0
        peak_height = float(np.max(primary))
        half_height_count = int(np.sum(primary >= 0.5 * peak_height))
        edges = (float(primary[0] / peak_height), float(primary[-1] / peak_height))
    smoothed = np.asarray(
        gaussian_smooth_values(primary, points=smooth_points), dtype=float
    )
    max_intensity = float(np.max(smoothed))
    if max_intensity <= 0:
        return LocalPeakShape(half_window_min, (None,) * grid_size, source_count)
    values = np.interp(
        grid,
        relative_rt,
        smoothed / max_intensity,
        left=np.nan,
        right=np.nan,
    )
    return LocalPeakShape(
        half_window_min,
        tuple(float(value) if math.isfinite(value) else None for value in values),
        source_count,
        half_height_count,
        edges,
    )


def compare_local_peak_shapes(
    left: LocalPeakShape,
    right: LocalPeakShape,
) -> ShapeSimilarityResult:
    """Compare only jointly observed points on the same relative RT grid."""
    if left.half_window_min != right.half_window_min or len(left.intensity) != len(
        right.intensity
    ):
        return ShapeSimilarityResult("inconclusive", "shape_grid_mismatch")
    if min(left.source_point_count, right.source_point_count) < 5:
        return ShapeSimilarityResult("inconclusive", "too_few_acquired_shape_points")
    similarity, count = _pearson_similarity(
        np.asarray(left.intensity, dtype=float),
        np.asarray(right.intensity, dtype=float),
    )
    return _local_shape_comparison_result(left, right, similarity, count)


def _local_shape_comparison_result(
    left: LocalPeakShape,
    right: LocalPeakShape,
    similarity: float | None,
    count: int,
) -> ShapeSimilarityResult:
    if 0 < count < (len(left.intensity) + 1) // 2:
        return ShapeSimilarityResult(
            "inconclusive",
            "insufficient_shared_shape_context",
            None,
            count,
        )
    if similarity is not None:
        if (
            left.half_height_point_count is None
            or right.half_height_point_count is None
        ):
            return ShapeSimilarityResult(
                "inconclusive",
                "unassessed_shape_resolution",
                None,
                count,
            )
        if min(left.half_height_point_count, right.half_height_point_count) < 5:
            edges = (left.edge_intensity_ratio, right.edge_intensity_ratio)
            localized = any(edge is not None and max(edge) < 0.5 for edge in edges)
            sustained = any(edge is not None and min(edge) >= 0.5 for edge in edges)
            if not (localized and sustained):
                return ShapeSimilarityResult(
                    "inconclusive",
                    "insufficient_primary_shape_resolution",
                    None,
                    count,
                )
            return ShapeSimilarityResult(
                "ok",
                "localized_vs_sustained_shape_contrast",
                similarity,
                count,
            )
    return ShapeSimilarityResult(
        "ok" if similarity is not None else "inconclusive",
        "local_own_max_shape_similarity"
        if similarity is not None
        else "similarity_unavailable",
        similarity,
        count,
    )


def gaussian_smooth_values(
    values: Iterable[float | int | None],
    *,
    points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> tuple[float, ...]:
    array = _float_array(values)
    if points < 3 or array.size < 3:
        return tuple(float(value) for value in array)
    window = min(points, array.size)
    if window % 2 == 0:
        window -= 1
    if window < 3:
        return tuple(float(value) for value in array)
    sigma = window / 6.0
    offsets = np.arange(window, dtype=float) - (window - 1) / 2.0
    kernel = np.exp(-0.5 * (offsets / sigma) ** 2)
    kernel = kernel / float(np.sum(kernel))
    pad = window // 2
    padded = np.pad(array, pad_width=pad, mode="edge")
    smoothed = np.convolve(padded, kernel, mode="valid")
    return tuple(float(value) for value in smoothed)


def _pearson_similarity(
    values: np.ndarray,
    reference: np.ndarray,
) -> tuple[float | None, int]:
    mask = np.isfinite(values) & np.isfinite(reference)
    count = int(np.sum(mask))
    if count < 5:
        return None, count
    x = values[mask]
    y = reference[mask]
    if float(np.std(x)) <= 1e-12 or float(np.std(y)) <= 1e-12:
        return None, count
    return float(np.corrcoef(x, y)[0, 1]), count


def _float_array(values: Iterable[float | int | None]) -> np.ndarray:
    try:
        return np.asarray(tuple(values), dtype=float)
    except TypeError, ValueError:
        return np.array([], dtype=float)
