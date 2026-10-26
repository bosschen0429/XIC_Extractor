from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_OWN_MAX_SMOOTH_POINTS = 15
DEFAULT_LOCAL_HALF_WINDOW_MIN = 0.35
DEFAULT_LOCAL_GRID_SIZE = 175
DEFAULT_COMPETING_EXCLUSION_WINDOW_MIN = 0.20
DEFAULT_COMPETING_MIN_RATIO = 0.05


@dataclass(frozen=True)
class OwnMaxTrace:
    status: str
    reason: str
    rt: tuple[float, ...] = ()
    intensity: tuple[float, ...] = ()
    max_intensity: float | None = None

    @property
    def is_usable(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ShapeSimilarityResult:
    status: str
    reason: str
    similarity: float | None = None
    compared_point_count: int = 0

    @property
    def is_usable(self) -> bool:
        return self.status == "ok" and self.similarity is not None


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

    def __post_init__(self) -> None:
        if (
            isinstance(self.half_window_min, bool)
            or not math.isfinite(self.half_window_min)
            or self.half_window_min <= 0
            or type(self.intensity) is not tuple
            or len(self.intensity) < 5
            or type(self.source_point_count) is not int
            or self.source_point_count < 0
            or (
                self.half_height_point_count is not None
                and (
                    type(self.half_height_point_count) is not int
                    or not 0 <= self.half_height_point_count <= self.source_point_count
                )
            )
            or (
                self.edge_intensity_ratio is not None
                and (
                    type(self.edge_intensity_ratio) is not tuple
                    or len(self.edge_intensity_ratio) != 2
                    or any(
                        isinstance(value, bool) or not math.isfinite(value) or value > 1
                        for value in self.edge_intensity_ratio
                    )
                )
            )
            or any(
                value is not None
                and (isinstance(value, bool) or not math.isfinite(value) or value > 1.0)
                for value in self.intensity
            )
        ):
            raise ValueError("local peak shape requires a finite normalized RT grid")


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
        # Keep the original RT support, but exclude disconnected signal only
        # from this private correspondence derivative, not from quantitation.
        primary[:start] = 0
        primary[stop + 1 :] = 0
        peak_height = float(np.max(primary))
        half_height_count = int(np.sum(primary >= 0.5 * peak_height))
        edges = (float(primary[0] / peak_height), float(primary[-1] / peak_height))
    smoothed = np.asarray(gaussian_smooth_values(primary, points=smooth_points))
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


@dataclass(frozen=True)
class _ShapeComparisonBlock:
    similarities: np.ndarray
    shared_counts: np.ndarray
    exact_pairs: np.ndarray


def _joint_shape_correlations(
    shapes: Sequence[LocalPeakShape],
) -> _ShapeComparisonBlock:
    values = np.asarray([shape.intensity for shape in shapes], dtype=float)
    observed = np.isfinite(values)
    size = len(shapes)
    correlations = np.full((size, size), np.nan)
    counts = np.zeros((size, size), dtype=int)
    exact_pairs = np.zeros((size, size), dtype=bool)
    # Small tiles bound temporary point arrays. Center each joint subset
    # explicitly, avoiding cancellation in E[x*x] - E[x]**2 for flat subsets.
    for start in range(0, size, 32):
        left_slice = slice(start, start + 32)
        for stop in range(start, size, 32):
            right_slice = slice(stop, stop + 32)
            joint = observed[left_slice, None, :] & observed[None, right_slice, :]
            count = joint.sum(axis=2)
            left = np.where(joint, values[left_slice, None, :], 0.0)
            right = np.where(joint, values[None, right_slice, :], 0.0)
            roundoff = (
                8
                * np.finfo(float).eps
                * values.shape[1]
                * np.maximum(
                    np.max(np.abs(left), axis=2), np.max(np.abs(right), axis=2)
                )
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                left -= np.where(joint, (left.sum(axis=2) / count)[:, :, None], 0.0)
                right -= np.where(joint, (right.sum(axis=2) / count)[:, :, None], 0.0)
                left_square = np.sum(left * left, axis=2)
                right_square = np.sum(right * right, axis=2)
                left_std = np.sqrt(left_square / count)
                right_std = np.sqrt(right_square / count)
                similarity = (
                    np.sum(left * right, axis=2)
                    / np.sqrt(left_square)
                    / np.sqrt(right_square)
                )
                usable = (count >= 5) & (left_std > 1e-12) & (right_std > 1e-12)
            similarity = np.where(usable, np.clip(similarity, -1, 1), np.nan)
            # Preserve the exact standalone decision at its flat-signal boundary.
            exact = (left_std <= 1e-12 + roundoff) | (right_std <= 1e-12 + roundoff)
            correlations[left_slice, right_slice] = similarity
            correlations[right_slice, left_slice] = similarity.T
            counts[left_slice, right_slice] = count
            counts[right_slice, left_slice] = count.T
            exact_pairs[left_slice, right_slice] = exact
            exact_pairs[right_slice, left_slice] = exact.T
    return _ShapeComparisonBlock(correlations, counts, exact_pairs)


class LocalPeakShapeComparisons:
    """Batch Pearson comparisons on each pair's jointly observed grid points.

    Covariance, support counts and variance are matrix operations prepared once.
    Blocks cap memory at 256 rows; cross-block and numerically flat pairs keep
    the standalone calculation. No completed comparison survives the batch.
    """

    def __init__(self, shapes: Iterable[LocalPeakShape]) -> None:
        # Retain the objects while indexing by identity; comparing a prepared
        # pair must not hash every grid value again.
        self._shapes = tuple(shapes)
        rows: dict[LocalPeakShape, tuple[_ShapeComparisonBlock, int]] = {}
        groups: dict[tuple[float, int], list[LocalPeakShape]] = defaultdict(list)
        for shape in dict.fromkeys(self._shapes):
            groups[(shape.half_window_min, len(shape.intensity))].append(shape)
        for group in groups.values():
            for start in range(0, len(group), 256):
                shapes_in_block = group[start : start + 256]
                block = _joint_shape_correlations(shapes_in_block)
                for row, shape in enumerate(shapes_in_block):
                    rows[shape] = (block, row)
        self._blocks = {id(shape): rows[shape] for shape in self._shapes}

    def compare(
        self, left: LocalPeakShape, right: LocalPeakShape
    ) -> ShapeSimilarityResult:
        first = self._blocks.get(id(left))
        second = self._blocks.get(id(right))
        if (
            first is None
            or second is None
            or first[0] is not second[0]
            or min(left.source_point_count, right.source_point_count) < 5
            or first[0].exact_pairs[first[1], second[1]]
        ):
            return compare_local_peak_shapes(left, right)
        block, left_index = first
        right_index = second[1]
        value = float(block.similarities[left_index, right_index])
        return _local_shape_comparison_result(
            left,
            right,
            value if math.isfinite(value) else None,
            int(block.shared_counts[left_index, right_index]),
        )


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
            # Sparse primary peaks cannot support fine-shape disagreement. A
            # localized peak versus signal sustained at BOTH observed edges
            # is a coarser contrast that does not require resolving peak width.
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


@dataclass(frozen=True)
class CompetingPeakSummary:
    status: str
    reason: str
    strongest_peak_rt_min: float | None = None
    strongest_peak_own_max_ratio: float | None = None
    strongest_competing_peak_rt_min: float | None = None
    strongest_competing_peak_own_max_ratio: float | None = None


def gaussian_smooth_values(
    values: Sequence[float | int | None],
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


def own_max_normalized_trace(
    rt: Sequence[float | int | None],
    intensity: Sequence[float | int | None],
    *,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> OwnMaxTrace:
    rt_array = _float_array(rt)
    intensity_array = _float_array(intensity)
    if rt_array.size != intensity_array.size:
        return OwnMaxTrace("inconclusive", "length_mismatch")
    mask = np.isfinite(rt_array) & np.isfinite(intensity_array)
    if int(np.sum(mask)) == 0:
        return OwnMaxTrace("inconclusive", "no_finite_points")
    rt_array = rt_array[mask]
    intensity_array = intensity_array[mask]
    if rt_array.size < 3:
        return OwnMaxTrace("inconclusive", "too_few_points")
    order = np.argsort(rt_array)
    rt_array = rt_array[order]
    smoothed = np.asarray(
        gaussian_smooth_values(intensity_array[order], points=smooth_points),
        dtype=float,
    )
    max_intensity = float(np.max(smoothed)) if smoothed.size else 0.0
    if max_intensity <= 0:
        return OwnMaxTrace("inconclusive", "non_positive_signal")
    normalized = smoothed / max_intensity
    return OwnMaxTrace(
        "ok",
        "own_max_normalized",
        tuple(float(value) for value in rt_array),
        tuple(float(value) for value in normalized),
        max_intensity,
    )


def local_own_max_shape_similarity(
    *,
    candidate_rt_min: float,
    candidate_rt: Sequence[float | int | None],
    candidate_intensity: Sequence[float | int | None],
    reference_rt_min: float,
    reference_rt: Sequence[float | int | None],
    reference_intensity: Sequence[float | int | None],
    half_window_min: float = DEFAULT_LOCAL_HALF_WINDOW_MIN,
    grid_size: int = DEFAULT_LOCAL_GRID_SIZE,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> ShapeSimilarityResult:
    if grid_size < 5:
        return ShapeSimilarityResult("inconclusive", "grid_too_small")
    candidate = prepare_local_peak_shape(
        candidate_rt,
        candidate_intensity,
        apex_rt_min=candidate_rt_min,
        half_window_min=half_window_min,
        grid_size=grid_size,
        smooth_points=smooth_points,
    )
    reference = prepare_local_peak_shape(
        reference_rt,
        reference_intensity,
        apex_rt_min=reference_rt_min,
        half_window_min=half_window_min,
        grid_size=grid_size,
        smooth_points=smooth_points,
    )
    return compare_local_peak_shapes(candidate, reference)


def competing_peak_summary(
    rt: Sequence[float | int | None],
    intensity: Sequence[float | int | None],
    *,
    candidate_rt_min: float,
    exclusion_window_min: float = DEFAULT_COMPETING_EXCLUSION_WINDOW_MIN,
    min_peak_ratio: float = DEFAULT_COMPETING_MIN_RATIO,
    smooth_points: int = DEFAULT_OWN_MAX_SMOOTH_POINTS,
) -> CompetingPeakSummary:
    normalized = own_max_normalized_trace(
        rt,
        intensity,
        smooth_points=smooth_points,
    )
    if not normalized.is_usable:
        return CompetingPeakSummary(normalized.status, normalized.reason)
    rt_array = np.asarray(normalized.rt, dtype=float)
    intensity_array = np.asarray(normalized.intensity, dtype=float)
    peaks = _local_maxima(rt_array, intensity_array, min_height=min_peak_ratio)
    if not peaks:
        return CompetingPeakSummary("inconclusive", "no_local_maxima")
    strongest = peaks[0]
    competing = next(
        (
            peak
            for peak in peaks
            if abs(peak[0] - candidate_rt_min) > exclusion_window_min
        ),
        None,
    )
    return CompetingPeakSummary(
        "ok",
        "competing_peak_summary",
        strongest_peak_rt_min=strongest[0],
        strongest_peak_own_max_ratio=strongest[1],
        strongest_competing_peak_rt_min=None if competing is None else competing[0],
        strongest_competing_peak_own_max_ratio=(
            None if competing is None else competing[1]
        ),
    )


def _local_maxima(
    rt: np.ndarray,
    intensity: np.ndarray,
    *,
    min_height: float,
) -> tuple[tuple[float, float], ...]:
    peaks: list[tuple[float, float]] = []
    for index in range(1, len(intensity) - 1):
        value = float(intensity[index])
        if not math.isfinite(value) or value < min_height:
            continue
        left = float(intensity[index - 1])
        right = float(intensity[index + 1])
        if value >= left and value >= right and (value > left or value > right):
            peaks.append((float(rt[index]), value))
    peaks.sort(key=lambda peak: peak[1], reverse=True)
    deduplicated: list[tuple[float, float]] = []
    for peak_rt, peak_height in peaks:
        if all(abs(peak_rt - existing_rt) > 0.08 for existing_rt, _ in deduplicated):
            deduplicated.append((peak_rt, peak_height))
    return tuple(deduplicated)


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


def _float_array(values: Sequence[float | int | None]) -> np.ndarray:
    try:
        return np.asarray(tuple(values), dtype=float)
    except (TypeError, ValueError):
        return np.array([], dtype=float)
