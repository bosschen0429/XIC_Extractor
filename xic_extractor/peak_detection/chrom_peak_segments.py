from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_prominences

from xic_extractor.peak_detection.baseline import bounded_trace_interval
from xic_extractor.peak_detection.integration import integrate_area_counts_seconds
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
)
from xic_extractor.peak_detection.signal_quality import short_scale_noise_sigma


@dataclass(frozen=True)
class TraceInterval:
    start_index: int
    end_index: int
    rt_start_min: float
    rt_end_min: float
    scan_count: int


ChromPeakSegmentStatus = Literal[
    "OK",
    "NO_SIGNAL",
    "WINDOW_TOO_SHORT",
    "PEAK_NOT_FOUND",
    "MALFORMED_TRACE",
]

SEPARABLE_VALLEY_NOISE_SIGMA = 3.0


@dataclass(frozen=True)
class ChromPeakSegmentPolicy:
    min_scan_count: int = 3
    min_apex_residual: float = 5.0
    baseline_return_fraction: float = 0.02
    baseline_return_min_residual: float = 1.0
    clear_valley_max_fraction: float = 0.30
    morphology_trace_window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS
    morphology_min_sigma_rt: float = 0.0
    min_prominence_noise_sigma: float = 0.0
    min_prominence_fraction_of_apex: float = 0.0
    valley_min_duration_min: float | None = None

    def baseline_return_threshold(self, apex_residual: float) -> float:
        return max(
            self.baseline_return_min_residual,
            apex_residual * self.baseline_return_fraction,
        )


@dataclass(frozen=True)
class LocatedChromPeak:
    """Where one peak lies on the trace: its samples and its apex."""

    interval: TraceInterval
    apex_index: int
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class ChromPeakSegment:
    interval: TraceInterval
    apex_index: int
    apex_rt_min: float
    apex_residual: float
    area_baseline_corrected: float
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class ChromPeakSegmentEnumeration:
    status: ChromPeakSegmentStatus
    segments: tuple[ChromPeakSegment, ...]
    component_cut_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class _ApexSeed:
    index: int
    height: float
    threshold: float


@dataclass(frozen=True)
class _AdjacentPeakRelation:
    valley_index: int
    relation_class: Literal["clear_split", "shallow_valley"]


_SeedGroup = tuple[
    tuple[_ApexSeed, ...],
    _AdjacentPeakRelation | None,
    _AdjacentPeakRelation | None,
]


def measure_chrom_peak_components(
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
    *,
    components: tuple[tuple[int, int], ...],
    policy: ChromPeakSegmentPolicy,
) -> tuple[ChromPeakSegment, ...]:
    """Measure already resolved valley basins with the existing boundary walk.

    Each ``(start_index, end_index)`` basin is a peak its owner has chosen. Do
    not enumerate a new coarse population inside a basin: a neighboring
    convolution lobe could move its seed to the cut boundary and erase the
    acquired component.
    """
    residual = np.maximum(intensity - baseline, 0.0)
    segments = []
    for start_index, end_index in components:
        apex = start_index + int(np.argmax(residual[start_index:end_index]))
        seed = _ApexSeed(
            index=apex,
            height=float(residual[apex]),
            threshold=policy.baseline_return_threshold(float(residual[apex])),
        )
        located = _locate_peak(
            rt,
            residual,
            _trace_interval(rt, start_index, end_index),
            ((seed,), None, None),
            policy,
        )
        segments.append(_measured_segment(rt, residual, located))
    return tuple(segments)


def enumerate_chrom_peak_segments(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    policy: ChromPeakSegmentPolicy,
    quantitation_context_rt_start: float | None = None,
    quantitation_context_rt_end: float | None = None,
    acquired_intensity_values: np.ndarray | None = None,
) -> ChromPeakSegmentEnumeration:
    """Enumerate chromatographic peak segments in one XIC context.

    This does not select a product peak. It creates the explicit peak-segment
    layer from which a caller chooses the segment to integrate.
    """
    status, rt, residual, located, cut_indices = _locate_peaks(
        rt_values,
        intensity_values,
        baseline_values,
        policy=policy,
        quantitation_context_rt_start=quantitation_context_rt_start,
        quantitation_context_rt_end=quantitation_context_rt_end,
        acquired_intensity_values=acquired_intensity_values,
    )
    return ChromPeakSegmentEnumeration(
        status=status,
        segments=tuple(_measured_segment(rt, residual, peak) for peak in located),
        component_cut_indices=cut_indices,
    )


def locate_chrom_peaks(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    *,
    policy: ChromPeakSegmentPolicy,
) -> tuple[tuple[LocatedChromPeak, ...], tuple[int, ...]]:
    """Peaks and the cuts between them on an acquired trace; nothing is measured."""
    intensity = np.asarray(intensity_values, dtype=float)
    _, _, _, located, cut_indices = _locate_peaks(
        rt_values,
        intensity,
        np.zeros_like(intensity),
        policy=policy,
        acquired_intensity_values=intensity,
    )
    return located, cut_indices


def _locate_peaks(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    policy: ChromPeakSegmentPolicy,
    quantitation_context_rt_start: float | None = None,
    quantitation_context_rt_end: float | None = None,
    acquired_intensity_values: np.ndarray | None = None,
) -> tuple[
    ChromPeakSegmentStatus,
    np.ndarray,
    np.ndarray,
    tuple[LocatedChromPeak, ...],
    tuple[int, ...],
]:
    """Status, RT, residual, located peaks and the cuts between them."""
    rt, intensity, baseline = _coerce_trace(
        rt_values,
        intensity_values,
        baseline_values,
    )
    if _is_malformed_trace(rt, intensity, baseline):
        return "MALFORMED_TRACE", rt, intensity, (), ()
    if len(rt) < policy.min_scan_count:
        return "WINDOW_TOO_SHORT", rt, intensity, (), ()

    context_interval = _context_interval(
        rt,
        quantitation_context_rt_start,
        quantitation_context_rt_end,
    )
    residual = np.maximum(intensity - baseline, 0.0)
    context_residual = residual[
        context_interval.start_index : context_interval.end_index
    ]
    if context_residual.size == 0 or float(np.max(context_residual)) <= 0.0:
        return "NO_SIGNAL", rt, residual, (), ()

    envelope = _rt_envelope_trace(rt, residual, policy)
    acquired = (
        intensity if acquired_intensity_values is None else acquired_intensity_values
    )
    seeds = _apex_seeds(envelope, context_interval, policy)
    if policy.min_prominence_noise_sigma > 0.0:
        seeds = _supported_apex_seeds(
            seeds,
            envelope,
            rt,
            acquired,
            context_interval,
            policy,
        )
    if not seeds:
        seeds = _apex_seeds(residual, context_interval, policy)
        if policy.min_prominence_noise_sigma > 0.0:
            seeds = _supported_apex_seeds(
                seeds, residual, rt, acquired, context_interval, policy
            )
    if not seeds:
        return "PEAK_NOT_FOUND", rt, residual, (), ()

    relations = _adjacent_relations(envelope, seeds, policy)
    groups: list[_SeedGroup] = []
    group_start = 0
    for index in range(len(seeds)):
        if index == len(seeds) - 1 or relations[index].relation_class == "clear_split":
            groups.append(
                (
                    seeds[group_start : index + 1],
                    relations[group_start - 1] if group_start > 0 else None,
                    relations[index] if index < len(relations) else None,
                )
            )
            group_start = index + 1
    native_envelope = gaussian15_morphology_trace(
        acquired, window_points=policy.morphology_trace_window_points
    )
    groups = [
        part
        for group in groups
        for part in _split_at_separable_valleys(
            group,
            rt,
            residual,
            acquired,
            native_envelope,
            context_interval,
            policy,
        )
    ]
    return (
        "OK",
        rt,
        residual,
        tuple(
            _locate_peak(rt, residual, context_interval, group, policy)
            for group in groups
        ),
        tuple(
            relation.valley_index
            for _, _, relation in groups[:-1]
            if relation is not None
        ),
    )


def _coerce_trace(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (
        np.asarray(rt_values, dtype=float),
        np.asarray(intensity_values, dtype=float),
        np.asarray(baseline_values, dtype=float),
    )


def _is_malformed_trace(
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
) -> bool:
    if rt.ndim != 1 or intensity.ndim != 1 or baseline.ndim != 1:
        return True
    if len(rt) != len(intensity) or len(rt) != len(baseline):
        return True
    if len(rt) < 2:
        return True
    if not (
        np.all(np.isfinite(rt))
        and np.all(np.isfinite(intensity))
        and np.all(np.isfinite(baseline))
    ):
        return True
    return bool(np.any(np.diff(rt) <= 0.0))


def _context_interval(
    rt: np.ndarray,
    rt_start: float | None,
    rt_end: float | None,
) -> TraceInterval:
    start = float(rt[0]) if rt_start is None else float(rt_start)
    end = float(rt[-1]) if rt_end is None else float(rt_end)
    left = int(np.searchsorted(rt, min(start, end), side="left"))
    right = int(np.searchsorted(rt, max(start, end), side="right"))
    left = max(0, min(left, len(rt) - 2))
    right = max(left + 2, min(right, len(rt)))
    return TraceInterval(
        start_index=left,
        end_index=right,
        rt_start_min=float(rt[left]),
        rt_end_min=float(rt[right - 1]),
        scan_count=right - left,
    )


def _rt_envelope_trace(
    rt: np.ndarray,
    residual: np.ndarray,
    policy: ChromPeakSegmentPolicy,
) -> np.ndarray:
    min_sigma_rt = max(float(policy.morphology_min_sigma_rt), 0.0)
    if min_sigma_rt == 0.0:
        return residual.copy()

    median_spacing = float(np.median(np.diff(rt)))
    existing_sigma_rt = (
        median_spacing * float(policy.morphology_trace_window_points) / 6.0
    )
    if existing_sigma_rt >= min_sigma_rt:
        return residual.copy()

    additional_sigma_rt = float(np.sqrt(min_sigma_rt**2 - existing_sigma_rt**2))
    uniform_rt = np.arange(
        float(rt[0]),
        float(rt[-1]) + median_spacing,
        median_spacing,
        dtype=float,
    )
    uniform_residual = np.interp(uniform_rt, rt, residual)
    uniform_envelope = gaussian_filter1d(
        uniform_residual,
        sigma=additional_sigma_rt / median_spacing,
        mode="nearest",
    )
    return np.interp(rt, uniform_rt, uniform_envelope)


def _apex_seeds(
    morphology_trace: np.ndarray,
    context_interval: TraceInterval,
    policy: ChromPeakSegmentPolicy,
) -> tuple[_ApexSeed, ...]:
    context = morphology_trace[
        context_interval.start_index : context_interval.end_index
    ]
    if context.size == 0:
        return ()
    peaks = _local_peak_indices(context)
    if policy.min_prominence_fraction_of_apex > 0:
        interior = [index for index in peaks if 0 < index < len(context) - 1]
        prominences = peak_prominences(context, interior)[0]
        supported = {
            index
            for index, prominence in zip(interior, prominences, strict=True)
            if prominence >= policy.min_prominence_fraction_of_apex * context[index]
        }
        peaks = tuple(
            index
            for index in peaks
            if index in supported or index in {0, len(context) - 1}
        )
    seeds = tuple(
        _ApexSeed(
            index=context_interval.start_index + local_index,
            height=float(context[local_index]),
            threshold=policy.baseline_return_threshold(float(context[local_index])),
        )
        for local_index in peaks
        if float(context[local_index]) >= policy.min_apex_residual
    )
    return tuple(sorted(seeds, key=lambda seed: seed.index))


def _local_peak_indices(values: np.ndarray) -> tuple[int, ...]:
    peak_indices = [int(index) for index in find_peaks(values)[0]]
    if len(values) == 1:
        peak_indices.append(0)
    elif len(values) >= 2:
        if values[0] > values[1]:
            peak_indices.insert(0, 0)
        if values[-1] > values[-2]:
            peak_indices.append(len(values) - 1)
    if not peak_indices and values.size:
        peak_indices.append(int(np.argmax(values)))
    return tuple(sorted(set(peak_indices)))


def _supported_apex_seeds(
    seeds: tuple[_ApexSeed, ...],
    signal: np.ndarray,
    rt: np.ndarray,
    acquired: np.ndarray,
    context: TraceInterval,
    policy: ChromPeakSegmentPolicy,
) -> tuple[_ApexSeed, ...]:
    start, end = context.start_index, context.end_index
    interior = tuple(seed for seed in seeds if start < seed.index < end - 1)
    prominences, left_bases, right_bases = peak_prominences(
        signal[start:end], [seed.index - start for seed in interior]
    )
    relations = _adjacent_relations(signal, seeds, policy)
    retained = {seed.index for seed in seeds if seed.index in (start, end - 1)}
    supported: set[int] = set()
    seed_positions = {seed.index: index for index, seed in enumerate(seeds)}
    for seed, prominence, left, right in zip(
        interior, prominences, left_bases, right_bases, strict=True
    ):
        left, right = start + int(left), start + int(right) + 1
        noise = short_scale_noise_sigma(rt[left:right], acquired[left:right]) or 0.0
        position = seed_positions[seed.index]
        for neighbor_position in (position - 1, position + 1):
            if not 0 <= neighbor_position < len(seeds):
                continue
            neighbor = seeds[neighbor_position]
            relation = relations[min(position, neighbor_position)]
            if (
                neighbor.height <= seed.height
                or relation.relation_class == "clear_split"
            ):
                continue
            contour_left, contour_right = sorted((seed.index, neighbor.index))
            noise = max(
                noise,
                short_scale_noise_sigma(
                    rt[contour_left : contour_right + 1],
                    acquired[contour_left : contour_right + 1],
                )
                or 0.0,
            )
        if prominence >= policy.min_prominence_noise_sigma * noise:
            supported.add(seed.index)
    group: list[_ApexSeed] = []
    for index, seed in enumerate(seeds):
        group.append(seed)
        if index < len(relations) and relations[index].relation_class != "clear_split":
            continue
        if any(item.index in supported for item in group):
            retained.update(item.index for item in group if item.index in supported)
        else:
            retained.update(item.index for item in group)
        group = []
    return tuple(seed for seed in seeds if seed.index in retained)


def _split_at_separable_valleys(
    group: _SeedGroup,
    rt: np.ndarray,
    residual: np.ndarray,
    acquired: np.ndarray,
    native_envelope: np.ndarray,
    context: TraceInterval,
    policy: ChromPeakSegmentPolicy,
) -> list[_SeedGroup]:
    """Separate acquired lobes across sustained non-detection or measured valleys."""
    seeds, left_relation, right_relation = group
    start = left_relation.valley_index if left_relation else context.start_index
    end = right_relation.valley_index + 1 if right_relation else context.end_index
    gaps = _sampled_nondetection_cuts(rt, acquired, native_envelope, start, end, policy)
    edges = (start, *gaps, end)
    cuts: list[int] = []
    for left, right in zip(edges, edges[1:], strict=False):
        cuts.extend(
            separable_valleys(rt, acquired, left, right, policy, cut_signal=residual)
        )
        if right != end:
            cuts.append(right)
    if not cuts:
        return [group]
    bounds = (start, *cuts, end)
    relations = (
        left_relation,
        *(_AdjacentPeakRelation(cut, "clear_split") for cut in cuts),
        right_relation,
    )
    parts = []
    for index, (part_start, part_end) in enumerate(
        zip(bounds, bounds[1:], strict=False)
    ):
        apex = part_start + int(np.argmax(residual[part_start:part_end]))
        height = float(residual[apex])
        part_seeds = {s.index: s for s in seeds if part_start <= s.index < part_end}
        if len(part_seeds) < 2:
            part_seeds = {}
        part_seeds.setdefault(
            apex, _ApexSeed(apex, height, policy.baseline_return_threshold(height))
        )
        parts.append(
            (
                tuple(part_seeds[i] for i in sorted(part_seeds)),
                relations[index],
                relations[index + 1],
            )
        )
    return parts


def _sampled_nondetection_cuts(
    rt: np.ndarray,
    acquired: np.ndarray,
    native_envelope: np.ndarray,
    start: int,
    end: int,
    policy: ChromPeakSegmentPolicy,
) -> tuple[int, ...]:
    """Separate bounded detections across clear valleys on the native scan grid."""
    detected = acquired[start:end] > 0.0
    changes = np.flatnonzero(np.diff(detected, prepend=False, append=False)) + start
    starts, ends = changes[::2], changes[1::2]
    rt_resolution = max(
        policy.morphology_min_sigma_rt, policy.valley_min_duration_min or 0.0
    )
    cuts = []
    for left, gap, resume, right in zip(
        starts[:-1], ends[:-1], starts[1:], ends[1:], strict=True
    ):
        if min(gap - left, resume - gap, right - resume) < 2:
            continue
        if rt[resume] - rt[gap - 1] < rt_resolution:
            continue
        if acquired[gap - 1] >= np.max(acquired[left:gap]) or acquired[
            resume
        ] >= np.max(acquired[resume:right]):
            continue
        cut = int(gap + np.argmin(native_envelope[gap:resume]))
        minor = min(
            float(np.max(native_envelope[left:gap])),
            float(np.max(native_envelope[resume:right])),
        )
        if native_envelope[cut] <= policy.clear_valley_max_fraction * minor:
            cuts.append(cut)
    return tuple(cuts)


def separable_valleys(
    rt: np.ndarray,
    acquired: np.ndarray,
    start: int,
    end: int,
    policy: ChromPeakSegmentPolicy,
    *,
    cut_signal: np.ndarray | None = None,
) -> tuple[int, ...]:
    """Acquired valleys in ``[start, end)`` that separate two measurable lobes.

    Zero scans are censored: they neither form nor fill a valley. A valley is
    at least two consecutive scans at or below
    ``min(minor - k * sigma, clear_valley_max_fraction * minor)``, where minor
    is the smaller side maximum and sigma the short-scale noise of the scans.
    Each adjacent lobe holds two scans above that level (one scan suffices
    only on the higher side, which cannot set the depth) and is itself a peak:
    beyond it the signal returns to the level for two scans, or the range
    ends. The deepest valley cuts first; each side is examined again. The
    result depends only on the acquired values, not on peak or input order.
    The cut is the lowest scan of the valley in ``cut_signal`` (the signal the
    caller measures), or in the acquired values when none is given.
    """
    indices = np.arange(start, end)
    indices = indices[acquired[indices] > 0.0]
    if len(indices) < 6:
        return ()
    valley = _deepest_separable_valley(rt[indices], acquired[indices], policy)
    if valley is None:
        return ()
    first, last = int(indices[valley[0]]), int(indices[valley[1]])
    if cut_signal is None:
        run = indices[valley[0] : valley[1] + 1]
        cut = int(run[np.argmin(acquired[run])])
    else:
        cut = first + int(np.argmin(cut_signal[first : last + 1]))
    return (
        *separable_valleys(rt, acquired, start, cut, policy, cut_signal=cut_signal),
        cut,
        *separable_valleys(rt, acquired, cut + 1, end, policy, cut_signal=cut_signal),
    )


def _deepest_separable_valley(
    rt: np.ndarray, values: np.ndarray, policy: ChromPeakSegmentPolicy
) -> tuple[int, int] | None:
    """The deepest separable valley run as (first, last) scan positions."""
    count = len(values)
    sigma = short_scale_noise_sigma(rt, values) or 0.0
    left_max = np.maximum.accumulate(values)
    right_max = np.maximum.accumulate(values[::-1])[::-1]
    best: tuple[float, int] | None = None
    best_valley: tuple[int, int] | None = None
    position = 1
    while position < count - 1:
        minor = min(float(left_max[position - 1]), float(right_max[position + 1]))
        threshold = min(
            minor - SEPARABLE_VALLEY_NOISE_SIGMA * sigma,
            policy.clear_valley_max_fraction * minor,
        )
        if values[position] > threshold:
            position += 1
            continue
        first = last = position
        while first > 0 and values[first - 1] <= threshold:
            first -= 1
        while last < count - 1 and values[last + 1] <= threshold:
            last += 1
        position = last + 1
        if last - first < 1 or first < 1 or last > count - 2:
            continue
        if (
            policy.valley_min_duration_min is not None
            and rt[last] - rt[first] < policy.valley_min_duration_min
        ):
            continue
        lobe_start = first - 1
        while lobe_start > 0 and values[lobe_start - 1] > threshold:
            lobe_start -= 1
        lobe_end = last + 1
        while lobe_end < count - 1 and values[lobe_end + 1] > threshold:
            lobe_end += 1
        left_peak, right_peak = left_max[first - 1], right_max[last + 1]
        if (first - lobe_start < 2 and left_peak <= right_peak) or (
            lobe_end - last < 2 and right_peak <= left_peak
        ):
            continue
        left_bounded = lobe_start <= 1 or values[lobe_start - 2] <= threshold
        right_bounded = lobe_end >= count - 2 or values[lobe_end + 2] <= threshold
        if not (left_bounded and right_bounded):
            continue
        key = (minor - float(np.min(values[first : last + 1])), -first)
        if best is None or key > best:
            best, best_valley = key, (first, last)
    return best_valley


def _adjacent_relations(
    morphology_trace: np.ndarray,
    seeds: tuple[_ApexSeed, ...],
    policy: ChromPeakSegmentPolicy,
) -> tuple[_AdjacentPeakRelation, ...]:
    relations: list[_AdjacentPeakRelation] = []
    for left_seed, right_seed in zip(seeds, seeds[1:], strict=False):
        valley_index = _valley_index(
            morphology_trace,
            left_seed.index,
            right_seed.index,
        )
        valley_fraction = _safe_ratio(
            float(morphology_trace[valley_index]),
            min(
                float(morphology_trace[left_seed.index]),
                float(morphology_trace[right_seed.index]),
            ),
        )
        relation_class: Literal["clear_split", "shallow_valley"] = (
            "clear_split"
            if valley_fraction <= policy.clear_valley_max_fraction
            else "shallow_valley"
        )
        relations.append(
            _AdjacentPeakRelation(
                valley_index=valley_index,
                relation_class=relation_class,
            )
        )
    return tuple(relations)


def _valley_index(values: np.ndarray, left_peak: int, right_peak: int) -> int:
    start = min(left_peak, right_peak)
    end = max(left_peak, right_peak) + 1
    return int(start + np.argmin(values[start:end]))


def local_apex_prominence(signal: np.ndarray, apex_index: int) -> float:
    """Relief on one owner-segmented canonical component.

    Segmentation has already resolved independent lobes and their valleys.
    Unsplit surface maxima cannot create another contrast boundary. A selected
    endpoint is either that accepted separating valley or the earlier canonical
    return; use its actual value, never a lower interior or distant minimum.
    """
    return float(signal[apex_index]) - max(float(signal[0]), float(signal[-1]))


def _locate_peak(
    rt: np.ndarray,
    residual: np.ndarray,
    context_interval: TraceInterval,
    group: _SeedGroup,
    policy: ChromPeakSegmentPolicy,
) -> LocatedChromPeak:
    """Walk one seed group out to its baseline return, keeping the return scans."""
    seeds, left_relation, right_relation = group
    left, _ = _seed_interval_indices(
        residual, context_interval, seeds[0], left_relation, right_relation
    )
    _, right = _seed_interval_indices(
        residual, context_interval, seeds[-1], left_relation, right_relation
    )
    apex_index = left + int(np.argmax(residual[left:right]))
    threshold = policy.baseline_return_threshold(float(residual[apex_index]))
    basin_left, basin_right = left, right
    while left < apex_index and residual[left] <= threshold:
        left += 1
    while right > apex_index + 1 and residual[right - 1] <= threshold:
        right -= 1
    interval = _trace_interval(
        rt, max(basin_left, left - 1), min(basin_right, right + 1)
    )
    return LocatedChromPeak(
        interval=interval,
        apex_index=apex_index,
        evidence_sources=_evidence_sources(
            _relation_labels(left_relation, right_relation),
            low_scan=interval.scan_count < policy.min_scan_count,
        ),
    )


def _measured_segment(
    rt: np.ndarray, residual: np.ndarray, peak: LocatedChromPeak
) -> ChromPeakSegment:
    return ChromPeakSegment(
        interval=peak.interval,
        apex_index=peak.apex_index,
        apex_rt_min=float(rt[peak.apex_index]),
        apex_residual=float(residual[peak.apex_index]),
        area_baseline_corrected=integrate_area_counts_seconds(
            residual,
            rt,
            *bounded_trace_interval(
                peak.interval.start_index, peak.interval.end_index, len(rt)
            ),
        ),
        evidence_sources=peak.evidence_sources,
    )


def _seed_interval_indices(
    morphology_trace: np.ndarray,
    context_interval: TraceInterval,
    seed: _ApexSeed,
    left_relation: _AdjacentPeakRelation | None,
    right_relation: _AdjacentPeakRelation | None,
) -> tuple[int, int]:
    left = _left_baseline_boundary(
        morphology_trace,
        seed.index,
        seed.threshold,
        context_interval,
    )
    right = _right_baseline_boundary(
        morphology_trace,
        seed.index,
        seed.threshold,
        context_interval,
    )
    left = max(context_interval.start_index, left - 1)
    right = min(context_interval.end_index, right + 1)
    if left_relation is not None:
        left = max(left, left_relation.valley_index)
    if right_relation is not None:
        right = min(right, right_relation.valley_index + 1)
    return left, max(left + 1, right)


def _left_baseline_boundary(
    morphology_trace: np.ndarray,
    apex_index: int,
    threshold: float,
    context_interval: TraceInterval,
) -> int:
    left = apex_index
    while (
        left > context_interval.start_index and morphology_trace[left - 1] > threshold
    ):
        left -= 1
    return left


def _right_baseline_boundary(
    morphology_trace: np.ndarray,
    apex_index: int,
    threshold: float,
    context_interval: TraceInterval,
) -> int:
    right = apex_index + 1
    while right < context_interval.end_index and morphology_trace[right] > threshold:
        right += 1
    return right


def _trace_interval(rt: np.ndarray, left: int, right: int) -> TraceInterval:
    bounded_left = max(0, min(left, len(rt) - 1))
    bounded_right = max(bounded_left + 1, min(right, len(rt)))
    return TraceInterval(
        start_index=bounded_left,
        end_index=bounded_right,
        rt_start_min=float(rt[bounded_left]),
        rt_end_min=float(rt[bounded_right - 1]),
        scan_count=bounded_right - bounded_left,
    )


def _relation_labels(
    left_relation: _AdjacentPeakRelation | None,
    right_relation: _AdjacentPeakRelation | None,
) -> tuple[str, ...]:
    labels: list[str] = []
    for relation in (left_relation, right_relation):
        if relation is None:
            continue
        labels.append(relation.relation_class)
    return tuple(labels)


def _evidence_sources(
    relation_labels: tuple[str, ...], *, low_scan: bool
) -> tuple[str, ...]:
    sources = [
        "morphology_trace",
        "morphology_local_maximum",
        "baseline_return",
        "raw_area_asls",
    ]
    if "clear_split" in relation_labels:
        sources.append("local_minimum_valley")
    if low_scan:
        sources.append("scan_support")
    return tuple(sources)


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
