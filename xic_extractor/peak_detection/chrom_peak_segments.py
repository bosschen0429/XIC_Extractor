from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_prominences

from xic_extractor.peak_detection.baseline import integrate_with_baseline
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
ChromPeakSegmentClass = Literal[
    "isolated_peak",
    "separate_peak",
    "low_scan_segment",
]

# A separable valley must be this many short-scale noise sigmas below the
# smaller adjacent lobe. Calibrated on the #1049 reviewed doublets (B) against
# noise ripples, zero dropouts (A) and broad single components (W).
SEPARABLE_VALLEY_NOISE_SIGMA = 3.0


@dataclass(frozen=True)
class ChromPeakSegmentPolicy:
    min_scan_count: int = 3
    min_apex_residual: float = 5.0
    min_apex_fraction_of_context: float = 0.05
    baseline_return_fraction: float = 0.02
    baseline_return_min_residual: float = 1.0
    include_baseline_return_samples: bool = False
    clear_valley_max_fraction: float = 0.30
    morphology_trace_method: str = "gaussian_15"
    morphology_trace_window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS
    morphology_min_sigma_rt: float = 0.0
    min_prominence_noise_sigma: float = 0.0
    min_prominence_fraction_of_envelope: float = 0.04
    min_prominence_fraction_of_apex: float = 0.0
    # Minimum RT span of a separable acquired valley (in addition to two scans).
    valley_min_duration_min: float | None = None

    def baseline_return_threshold(self, apex_residual: float) -> float:
        return max(
            self.baseline_return_min_residual,
            apex_residual * self.baseline_return_fraction,
        )


@dataclass(frozen=True)
class ChromPeakSegment:
    interval: TraceInterval
    apex_index: int
    apex_rt_min: float
    raw_apex_residual: float
    morphology_apex_residual: float
    area_baseline_corrected: float
    morphology_area_shadow: float
    segment_class: ChromPeakSegmentClass
    boundary_stop_reason: str
    evidence_sources: tuple[str, ...]


@dataclass(frozen=True)
class ChromPeakSegmentEnumeration:
    status: ChromPeakSegmentStatus
    segments: tuple[ChromPeakSegment, ...]
    n_points: int
    morphology_trace_method: str
    morphology_trace_window_points: int
    morphology_trace_effective_points: int
    morphology_trace_values: tuple[float, ...] = ()


@dataclass(frozen=True)
class _ApexSeed:
    index: int
    height: float
    threshold: float


@dataclass(frozen=True)
class _AdjacentPeakRelation:
    valley_index: int
    relation_class: Literal["clear_split", "unresolved"]


def measure_chrom_peak_component(
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
    *,
    start_index: int,
    end_index: int,
    policy: ChromPeakSegmentPolicy,
) -> ChromPeakSegment:
    """Measure one already resolved valley basin with the existing boundary walk.

    The component owner has chosen the peak. Do not enumerate a new coarse
    population on this sub-context: a neighboring convolution lobe could move
    its seed to the cut boundary and erase the acquired component.
    """
    residual = np.maximum(intensity - baseline, 0.0)
    apex = start_index + int(np.argmax(residual[start_index:end_index]))
    seed = _ApexSeed(
        index=apex,
        height=float(residual[apex]),
        threshold=policy.baseline_return_threshold(float(residual[apex])),
    )
    return _segment_from_seed(
        rt,
        intensity,
        baseline,
        _morphology_trace(rt, residual, policy),
        _trace_interval(rt, start_index, end_index),
        seed=seed,
        left_relation=None,
        right_relation=None,
        policy=policy,
        envelope_seeds=(seed,),
    )


def enumerate_chrom_peak_segments(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    quantitation_context_rt_start: float | None = None,
    quantitation_context_rt_end: float | None = None,
    policy: ChromPeakSegmentPolicy | None = None,
    acquired_intensity_values: np.ndarray | None = None,
) -> ChromPeakSegmentEnumeration:
    """Enumerate diagnostic chromatographic peak segments in one XIC context.

    This does not select a product peak or mutate area behavior. It creates the
    explicit peak-segment layer needed before model selection chooses which
    segment should be integrated.
    """
    active_policy = policy or ChromPeakSegmentPolicy()
    rt, intensity, baseline = _coerce_trace(
        rt_values,
        intensity_values,
        baseline_values,
    )
    if _is_malformed_trace(rt, intensity, baseline):
        return _empty_result("MALFORMED_TRACE", rt, active_policy)
    if len(rt) < active_policy.min_scan_count:
        return _empty_result("WINDOW_TOO_SHORT", rt, active_policy)

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
        return _empty_result("NO_SIGNAL", rt, active_policy)

    morphology_trace = _morphology_trace(rt, residual, active_policy)
    morphology_effective_points = _morphology_trace_effective_points(
        len(rt),
        active_policy,
    )
    acquired = (
        intensity if acquired_intensity_values is None else acquired_intensity_values
    )
    seeds = _apex_seeds(morphology_trace, context_interval, active_policy)
    if active_policy.min_prominence_noise_sigma > 0.0:
        seeds = _supported_apex_seeds(
            seeds,
            morphology_trace,
            rt,
            acquired,
            context_interval,
            active_policy,
        )
    if not seeds and active_policy.morphology_trace_method == "rt_envelope":
        # Coarse smoothing can lower a supported narrow peak below the seed
        # height floor; its own scans still reach ordinary qualification.
        seeds = _apex_seeds(residual, context_interval, active_policy)
        if active_policy.min_prominence_noise_sigma > 0.0:
            seeds = _supported_apex_seeds(
                seeds, residual, rt, acquired, context_interval, active_policy
            )
    if not seeds:
        return ChromPeakSegmentEnumeration(
            status="PEAK_NOT_FOUND",
            segments=(),
            n_points=len(rt),
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            morphology_trace_values=tuple(float(value) for value in morphology_trace),
        )

    # Coarse lobes own their separating valley as well as their seeds.
    relations = _adjacent_relations(morphology_trace, seeds, active_policy)
    groups: list[
        tuple[
            tuple[_ApexSeed, ...],
            _AdjacentPeakRelation | None,
            _AdjacentPeakRelation | None,
        ]
    ] = []
    group_start = 0
    for index in range(len(seeds)):
        if (
            active_policy.morphology_trace_method != "rt_envelope"
            or index == len(seeds) - 1
            or relations[index].relation_class == "clear_split"
        ):
            groups.append(
                (
                    seeds[group_start : index + 1],
                    relations[group_start - 1] if group_start > 0 else None,
                    relations[index] if index < len(relations) else None,
                )
            )
            group_start = index + 1
    if active_policy.morphology_trace_method == "rt_envelope":
        # Smoothing can bury a valley the acquired scans still resolve. Split
        # each coarse component at its separable acquired valleys; anything
        # not separable stays one component measured as a whole.
        groups = [
            part
            for group in groups
            for part in _split_at_separable_valleys(
                group, rt, residual, acquired, context_interval, active_policy
            )
        ]
    segments = tuple(
        _segment_from_seed(
            rt,
            intensity,
            baseline,
            morphology_trace,
            context_interval,
            seed=max(group_seeds, key=lambda item: (item.height, -item.index)),
            left_relation=left_relation,
            right_relation=right_relation,
            policy=active_policy,
            envelope_seeds=group_seeds,
        )
        for group_seeds, left_relation, right_relation in groups
    )
    return ChromPeakSegmentEnumeration(
        status="OK",
        segments=segments,
        n_points=len(rt),
        morphology_trace_method=active_policy.morphology_trace_method,
        morphology_trace_window_points=active_policy.morphology_trace_window_points,
        morphology_trace_effective_points=morphology_effective_points,
        morphology_trace_values=tuple(float(value) for value in morphology_trace),
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


def _empty_result(
    status: ChromPeakSegmentStatus,
    rt: np.ndarray,
    policy: ChromPeakSegmentPolicy,
) -> ChromPeakSegmentEnumeration:
    return ChromPeakSegmentEnumeration(
        status=status,
        segments=(),
        n_points=len(rt) if rt.ndim == 1 else 0,
        morphology_trace_method=policy.morphology_trace_method,
        morphology_trace_window_points=policy.morphology_trace_window_points,
        morphology_trace_effective_points=0,
    )


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


def _morphology_trace(
    rt: np.ndarray,
    residual: np.ndarray,
    policy: ChromPeakSegmentPolicy,
) -> np.ndarray:
    window = _morphology_trace_effective_points(len(residual), policy)
    if policy.morphology_trace_method == "gaussian_15":
        if window <= 1:
            return residual.copy()
        return gaussian15_morphology_trace(residual, window_points=window)
    if policy.morphology_trace_method == "smooth_15":
        if window <= 1:
            return residual.copy()
        kernel = np.ones(window, dtype=float) / float(window)
        return np.convolve(residual, kernel, mode="same")
    if policy.morphology_trace_method == "rt_envelope":
        return _rt_envelope_trace(rt, residual, policy)
    return residual.copy()


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


def _morphology_trace_effective_points(
    trace_length: int,
    policy: ChromPeakSegmentPolicy,
) -> int:
    if trace_length <= 0:
        return 0
    if policy.morphology_trace_method not in {
        "gaussian_15",
        "smooth_15",
        "rt_envelope",
    }:
        return 1
    if policy.morphology_trace_window_points <= 1:
        return 1
    if trace_length < policy.morphology_trace_window_points:
        return 1
    window = min(policy.morphology_trace_window_points, trace_length)
    if window % 2 == 0:
        window -= 1
    return max(window, 1)


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
    max_context = float(np.max(context))
    min_height = max(
        policy.min_apex_residual,
        max_context * policy.min_apex_fraction_of_context,
    )
    peaks = _local_peak_indices(context)
    if policy.min_prominence_fraction_of_apex > 0:
        # Remove small surface ripples before asking which adjacent lobes a
        # valley separates. Their height cannot hide a larger sustained trough.
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
        if float(context[local_index]) >= min_height
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
    # Measure the whole contour before neighboring ripple can truncate it.
    # Edge maxima remain candidates: missing context is not noise evidence.
    start, end = context.start_index, context.end_index
    interior = tuple(seed for seed in seeds if start < seed.index < end - 1)
    prominences, left_bases, right_bases = peak_prominences(
        signal[start:end], [seed.index - start for seed in interior]
    )
    relations = _adjacent_relations(signal, seeds, policy)
    retained = {seed.index for seed in seeds if seed.index in (start, end - 1)}
    supported: set[int] = set()
    prominence_by_index: dict[int, float] = {}
    seed_positions = {seed.index: index for index, seed in enumerate(seeds)}
    for seed, prominence, left, right in zip(
        interior, prominences, left_bases, right_bases, strict=True
    ):
        left, right = start + int(left), start + int(right) + 1
        prominence_by_index[seed.index] = float(prominence)
        noise = short_scale_noise_sigma(rt[left:right], acquired[left:right]) or 0.0
        # A subordinate bump cannot dilute its noise estimate with distant
        # zero background. Include the acquired contour toward an adjacent
        # higher lobe when both belong to the same connected envelope.
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
    # Noise significance does not make a bump a separate chromatographic peak.
    # Compare its prominence with the whole connected envelope before using a
    # neighboring valley as a quantitative boundary. Baseline-separated weak
    # peaks form their own groups, so a strong remote peak cannot erase them.
    # Unsupported groups still reach the ordinary support/contrast gates.
    group: list[_ApexSeed] = []
    for index, seed in enumerate(seeds):
        group.append(seed)
        if index < len(relations) and relations[index].relation_class != "clear_split":
            continue
        if any(item.index in supported for item in group):
            envelope_height = max(item.height for item in group)
            retained.update(
                item.index
                for item in group
                if item.index in supported
                and prominence_by_index[item.index]
                >= policy.min_prominence_fraction_of_envelope * envelope_height
            )
        else:
            retained.update(item.index for item in group)
        group = []
    return tuple(seed for seed in seeds if seed.index in retained)


def _split_at_separable_valleys(
    group: tuple[
        tuple[_ApexSeed, ...],
        _AdjacentPeakRelation | None,
        _AdjacentPeakRelation | None,
    ],
    rt: np.ndarray,
    residual: np.ndarray,
    acquired: np.ndarray,
    context: TraceInterval,
    policy: ChromPeakSegmentPolicy,
) -> list[
    tuple[
        tuple[_ApexSeed, ...],
        _AdjacentPeakRelation | None,
        _AdjacentPeakRelation | None,
    ]
]:
    """Cut one coarse component at its separable acquired valleys."""
    seeds, left_relation, right_relation = group
    start = left_relation.valley_index if left_relation else context.start_index
    end = right_relation.valley_index + 1 if right_relation else context.end_index
    cuts = separable_valleys(rt, acquired, start, end, policy, cut_signal=residual)
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
        # A coarse seed can sit on the cut itself; the lobe's own maximum
        # anchors its boundary walk.
        apex = part_start + int(np.argmax(residual[part_start:part_end]))
        height = float(residual[apex])
        part_seeds = {s.index: s for s in seeds if part_start <= s.index < part_end}
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
        # One scan cannot be the smaller lobe that sets the valley depth; a
        # single scan above the other side's maximum may still stand apart.
        left_peak, right_peak = left_max[first - 1], right_max[last + 1]
        if (first - lobe_start < 2 and left_peak <= right_peak) or (
            lobe_end - last < 2 and right_peak <= left_peak
        ):
            continue
        left_bounded = lobe_start == 0 or (
            lobe_start >= 2 and values[lobe_start - 2] <= threshold
        )
        right_bounded = lobe_end == count - 1 or (
            lobe_end <= count - 3 and values[lobe_end + 2] <= threshold
        )
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
        relation_class: Literal["clear_split", "unresolved"] = (
            "clear_split"
            if valley_fraction <= policy.clear_valley_max_fraction
            else "unresolved"
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


def _segment_from_seed(
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
    morphology_trace: np.ndarray,
    context_interval: TraceInterval,
    *,
    seed: _ApexSeed,
    left_relation: _AdjacentPeakRelation | None,
    right_relation: _AdjacentPeakRelation | None,
    policy: ChromPeakSegmentPolicy,
    envelope_seeds: tuple[_ApexSeed, ...],
) -> ChromPeakSegment:
    # The RT envelope establishes supported lobes and separating valleys.
    # Its convolution tails are not acquired quantitative support: close the
    # interval on the unchanged canonical signal used for area and apex.
    boundary_signal = (
        np.maximum(intensity - baseline, 0.0)
        if policy.morphology_trace_method == "rt_envelope"
        else morphology_trace
    )
    left, _ = _seed_interval_indices(
        boundary_signal,
        context_interval,
        envelope_seeds[0],
        left_relation,
        right_relation,
        policy,
    )
    _, right = _seed_interval_indices(
        boundary_signal,
        context_interval,
        envelope_seeds[-1],
        left_relation,
        right_relation,
        policy,
    )
    apex_index = seed.index
    if policy.morphology_trace_method == "rt_envelope":
        apex_index = left + int(np.argmax(boundary_signal[left:right]))
        # The envelope locates the basin, not its quantitative return height.
        # Trim its outer tails against the selected canonical apex, preserving
        # interior dropout notches and the separating-valley limits above.
        threshold = policy.baseline_return_threshold(float(boundary_signal[apex_index]))
        basin_left, basin_right = left, right
        while left < apex_index and boundary_signal[left] <= threshold:
            left += 1
        while right > apex_index + 1 and boundary_signal[right - 1] <= threshold:
            right -= 1
        if policy.include_baseline_return_samples:
            left = max(basin_left, left - 1)
            right = min(basin_right, right + 1)
    relation_labels = _relation_labels(left_relation, right_relation)
    interval = _trace_interval(rt, left, right)
    segment_class = _segment_class(interval, relation_labels, policy)
    stop_reason = _boundary_stop_reason(segment_class, relation_labels)
    area = integrate_with_baseline(
        intensity,
        rt,
        interval.start_index,
        interval.end_index,
        baseline_values=baseline,
    ).area_baseline_corrected
    morphology_area = _positive_area(
        morphology_trace,
        rt,
        interval.start_index,
        interval.end_index,
    )
    apex_residual = (
        float(boundary_signal[apex_index])
        if policy.morphology_trace_method == "rt_envelope"
        else float(morphology_trace[apex_index])
    )
    return ChromPeakSegment(
        interval=interval,
        apex_index=apex_index,
        apex_rt_min=float(rt[apex_index]),
        raw_apex_residual=max(
            0.0,
            float(intensity[apex_index] - baseline[apex_index]),
        ),
        morphology_apex_residual=apex_residual,
        area_baseline_corrected=area,
        morphology_area_shadow=morphology_area,
        segment_class=segment_class,
        boundary_stop_reason=stop_reason,
        evidence_sources=_evidence_sources(relation_labels, segment_class),
    )


def _seed_interval_indices(
    morphology_trace: np.ndarray,
    context_interval: TraceInterval,
    seed: _ApexSeed,
    left_relation: _AdjacentPeakRelation | None,
    right_relation: _AdjacentPeakRelation | None,
    policy: ChromPeakSegmentPolicy,
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
    if policy.include_baseline_return_samples:
        # Retain the scans bracketing the return to baseline. An MS2 event can
        # fall between the last positive MS1 scan and that return scan.
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


def _segment_class(
    interval: TraceInterval,
    relation_labels: tuple[str, ...],
    policy: ChromPeakSegmentPolicy,
) -> ChromPeakSegmentClass:
    if interval.scan_count < policy.min_scan_count:
        return "low_scan_segment"
    if "clear_split" in relation_labels:
        return "separate_peak"
    return "isolated_peak"


def _boundary_stop_reason(
    segment_class: ChromPeakSegmentClass,
    relation_labels: tuple[str, ...],
) -> str:
    if segment_class == "low_scan_segment":
        return "segment_too_few_scans"
    if "clear_split" in relation_labels:
        return "baseline_valley_split"
    return "baseline_return"


def _evidence_sources(
    relation_labels: tuple[str, ...],
    segment_class: ChromPeakSegmentClass,
) -> tuple[str, ...]:
    sources = [
        "morphology_trace",
        "morphology_local_maximum",
        "baseline_return",
        "raw_area_asls",
    ]
    if "clear_split" in relation_labels:
        sources.append("local_minimum_valley")
    if segment_class == "low_scan_segment":
        sources.append("scan_support")
    return tuple(sources)


def _positive_area(
    values: np.ndarray,
    rt: np.ndarray,
    left: int,
    right: int,
) -> float:
    segment = np.maximum(values[left:right], 0.0)
    segment_rt = rt[left:right]
    if len(segment) < 2:
        return 0.0
    return float(np.trapezoid(segment, segment_rt)) * 60.0


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator
