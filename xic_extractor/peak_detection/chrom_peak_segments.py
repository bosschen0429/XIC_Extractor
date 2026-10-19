from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import find_peaks, peak_prominences

from xic_extractor.peak_detection.baseline import integrate_with_baseline
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
)
from xic_extractor.peak_detection.scoring_metrics import short_scale_noise_mad
from xic_extractor.peak_detection.selected_envelope import TraceInterval

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
    "shoulder_candidate",
    "low_scan_segment",
]


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
    min_prominence_noise_mad: float = 0.0
    min_prominence_fraction_of_envelope: float = 0.04

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
    relation_class: Literal["clear_split", "shoulder_overlap"]


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
    # Match the RT envelope's scale decision once for the whole trace. When
    # canonical smoothing already covers that scale, its valley needs no repair.
    additional_envelope_smoothing = (
        active_policy.morphology_trace_method == "rt_envelope"
        and max(float(active_policy.morphology_min_sigma_rt), 0.0)
        > float(np.median(np.diff(rt)))
        * float(active_policy.morphology_trace_window_points) / 6.0
    )
    morphology_effective_points = _morphology_trace_effective_points(
        len(rt),
        active_policy,
    )
    acquired = (
        intensity if acquired_intensity_values is None else acquired_intensity_values
    )
    seeds = _apex_seeds(morphology_trace, context_interval, active_policy)
    if active_policy.min_prominence_noise_mad > 0.0:
        seeds = _supported_apex_seeds(
            seeds,
            morphology_trace,
            rt,
            acquired,
            context_interval,
            active_policy,
        )
    envelope_seed_indices = {seed.index for seed in seeds}
    if active_policy.morphology_trace_method == "rt_envelope":
        seeds = _restore_rt_envelope_clear_splits(
            seeds,
            morphology_trace,
            residual,
            rt,
            acquired,
            context_interval,
            active_policy,
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

    relation_trace = (
        residual
        if active_policy.morphology_trace_method == "rt_envelope"
        else morphology_trace
    )
    relations = _adjacent_relations(relation_trace, seeds, active_policy)
    if active_policy.morphology_trace_method == "rt_envelope":
        # Coarse lobes own their separating valley as well as their seeds.
        # A deeper canonical notch on a falling shoulder must not move that
        # boundary away from the sustained valley between the lobes. Supported
        # canonical clear splits, including restored narrow components, remain.
        envelope_relations = _adjacent_relations(
            morphology_trace, seeds, active_policy
        )
        relations = tuple(
            envelope_relation
            if (
                left.index in envelope_seed_indices
                and right.index in envelope_seed_indices
                and not (
                    relation.relation_class == "clear_split"
                    and _sustained_canonical_valley(
                        residual, rt, left, right, relation.valley_index, active_policy
                    )
                )
            )
            else relation
            for left, right, relation, envelope_relation in zip(
                seeds, seeds[1:], relations, envelope_relations
            )
        )
    # Supported lobes with a persistent valley remain separate components
    # even without baseline resolution. Their shoulder relation still
    # requires overlap review; short dropouts cannot supply that boundary.
    seed_groups: list[tuple[int, int]] = []
    group_start = 0
    for index in range(len(seeds)):
        if (
            active_policy.morphology_trace_method != "rt_envelope"
            or index == len(seeds) - 1
            or relations[index].relation_class == "clear_split"
            or (
                seeds[index].index in envelope_seed_indices
                and seeds[index + 1].index in envelope_seed_indices
                and _sustained_envelope_valley(
                    relation_trace, rt, seeds[index], seeds[index + 1],
                    relations[index].valley_index, active_policy,
                    additional_envelope_smoothing=additional_envelope_smoothing,
                )
            )
        ):
            seed_groups.append((group_start, index))
            group_start = index + 1
    segments = tuple(
        _segment_from_seed(
            rt,
            intensity,
            baseline,
            morphology_trace,
            context_interval,
            seed=max(
                seeds[start : end + 1], key=lambda item: (item.height, -item.index)
            ),
            left_relation=relations[start - 1] if start > 0 else None,
            right_relation=relations[end] if end < len(relations) else None,
            policy=active_policy,
            envelope_seeds=seeds[start : end + 1],
        )
        for start, end in seed_groups
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


def segments_containing_rt(
    segments: Sequence[ChromPeakSegment],
    rt_min: float,
) -> tuple[ChromPeakSegment, ...]:
    """Return only segments whose recorded interval contains the target RT."""
    target = float(rt_min)
    return tuple(
        segment
        for segment in segments
        if segment.interval.rt_start_min <= target <= segment.interval.rt_end_min
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
        interior, prominences, left_bases, right_bases
    ):
        left, right = start + int(left), start + int(right) + 1
        prominence_by_index[seed.index] = float(prominence)
        noise = short_scale_noise_mad(rt[left:right], acquired[left:right])
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
            noise = max(noise, short_scale_noise_mad(
                rt[contour_left:contour_right + 1],
                acquired[contour_left:contour_right + 1],
            ))
        if prominence >= policy.min_prominence_noise_mad * noise:
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


def _restore_rt_envelope_clear_splits(
    envelope_seeds: tuple[_ApexSeed, ...],
    envelope_trace: np.ndarray,
    canonical_residual: np.ndarray,
    rt: np.ndarray,
    acquired: np.ndarray,
    context: TraceInterval,
    policy: ChromPeakSegmentPolicy,
) -> tuple[_ApexSeed, ...]:
    canonical_seeds = _apex_seeds(canonical_residual, context, policy)
    if policy.min_prominence_noise_mad > 0.0:
        canonical_seeds = _supported_apex_seeds(
            canonical_seeds,
            canonical_residual,
            rt,
            acquired,
            context,
            policy,
        )
    canonical_relations = tuple(
        relation if (
            relation.relation_class != "clear_split"
            or _sustained_canonical_valley(
                canonical_residual, rt, left, right, relation.valley_index, policy
            )
        ) else _AdjacentPeakRelation(relation.valley_index, "shoulder_overlap")
        for left, right, relation in zip(
            canonical_seeds, canonical_seeds[1:],
            _adjacent_relations(canonical_residual, canonical_seeds, policy),
        )
    )
    connected_groups: list[list[_ApexSeed]] = []
    group: list[_ApexSeed] = []
    for index, seed in enumerate(canonical_seeds):
        group.append(seed)
        if (
            index < len(canonical_relations)
            and canonical_relations[index].relation_class != "clear_split"
        ):
            continue
        connected_groups.append(group)
        group = []
    if not connected_groups:
        return envelope_seeds
    if not envelope_seeds:
        # Coarse smoothing can lower a supported narrow peak below the seed
        # height floor. Its canonical group still reaches ordinary qualification,
        # including when no unrelated coarse maximum exists in the context.
        return tuple(
            max(connected, key=lambda seed: (seed.height, -seed.index))
            for connected in connected_groups
        )

    group_boundaries = (
        context.start_index,
        *(
            _valley_index(canonical_residual, left[-1].index, right[0].index)
            for left, right in zip(connected_groups, connected_groups[1:])
        ),
        context.end_index,
    )
    if len(connected_groups) < 2:
        return envelope_seeds
    restored = {seed.index: seed for seed in envelope_seeds}
    envelope_relations = _adjacent_relations(envelope_trace, envelope_seeds, policy)
    for index, envelope_seed in enumerate(envelope_seeds):
        left = (
            context.start_index
            if index == 0
            else envelope_relations[index - 1].valley_index
        )
        right = (
            context.end_index
            if index == len(envelope_seeds) - 1
            else envelope_relations[index].valley_index + 1
        )
        enclosed = tuple(
            (group_index, connected)
            for group_index, connected in enumerate(connected_groups)
            if left <= connected[0].index <= connected[-1].index < right
        )
        if len(enclosed) < 2:
            continue
        # Replacement groups must cover this maximum's canonical basin. Use
        # separating valleys: smoothing can move an asymmetric maximum outside
        # the canonical apex span. Distant flank groups cannot replace it.
        if not (
            group_boundaries[enclosed[0][0]]
            <= envelope_seed.index
            <= group_boundaries[enclosed[-1][0] + 1]
        ):
            continue
        restored.pop(envelope_seed.index)
        for _, connected in enclosed:
            representative = max(
                connected,
                key=lambda seed: (seed.height, -seed.index),
            )
            restored[representative.index] = representative
    return tuple(restored[index] for index in sorted(restored))


def _sustained_envelope_valley(
    signal: np.ndarray,
    rt: np.ndarray,
    left: _ApexSeed,
    right: _ApexSeed,
    valley_index: int,
    policy: ChromPeakSegmentPolicy,
    *,
    additional_envelope_smoothing: bool,
) -> bool:
    """Measure a supported lobe pair's valley width at half its dip depth.

    A broad decrease and recovery can separate overlapping components without
    returning to baseline. Requiring one canonical smoothing footprint rejects
    a brief acquired dropout that also creates two coarse-envelope maxima.
    """
    spacing = float(np.median(np.diff(rt[left.index:right.index + 1])))
    footprint = spacing * max(0, policy.morphology_trace_window_points - 1)
    weaker_height = min(float(signal[left.index]), float(signal[right.index]))
    valley_height = float(signal[valley_index])
    # An accepted canonical valley already separates these components. A
    # coarser-scale rescue may add separation evidence, never remove it.
    for use_supported_floor in (False, True):
        if use_supported_floor:
            if not additional_envelope_smoothing:
                return False
            # A finer canonical notch cannot set the depth of a broader RT valley.
            support_left = max(
                left.index,
                int(np.searchsorted(
                    rt, rt[valley_index] - 0.5 * footprint, side="left"
                )),
            )
            support_right = min(
                right.index + 1,
                int(np.searchsorted(
                    rt, rt[valley_index] + 0.5 * footprint, side="right"
                )),
            )
            valley_height = float(np.max(signal[support_left:support_right]))
        if weaker_height <= valley_height:
            continue
        threshold = valley_height + 0.5 * (weaker_height - valley_height)
        start = end = valley_index
        while start > left.index and signal[start - 1] <= threshold:
            start -= 1
        while end < right.index and signal[end + 1] <= threshold:
            end += 1
        if rt[end] - rt[start] >= footprint:
            return True
    return False


def _sustained_canonical_valley(
    signal: np.ndarray,
    rt: np.ndarray,
    left: _ApexSeed,
    right: _ApexSeed,
    valley_index: int,
    policy: ChromPeakSegmentPolicy,
) -> bool:
    """A fine-scale trough must outlast one canonical smoothing footprint.

    A brief dropout can survive Gaussian smoothing as a deep local notch.
    It cannot split a single RT envelope; a sustained low interval can recover
    two genuinely narrow peaks that the coarser envelope blurred together.
    """
    threshold = policy.clear_valley_max_fraction * min(
        float(signal[left.index]), float(signal[right.index])
    )
    start = end = valley_index
    while start > left.index and signal[start - 1] <= threshold:
        start -= 1
    while end < right.index and signal[end + 1] <= threshold:
        end += 1
    spacing = float(np.median(np.diff(rt[left.index:right.index + 1])))
    footprint = spacing * max(0, policy.morphology_trace_window_points - 1)
    return bool(rt[end] - rt[start] >= footprint)


def _adjacent_relations(
    morphology_trace: np.ndarray,
    seeds: tuple[_ApexSeed, ...],
    policy: ChromPeakSegmentPolicy,
) -> tuple[_AdjacentPeakRelation, ...]:
    relations: list[_AdjacentPeakRelation] = []
    for left_seed, right_seed in zip(seeds, seeds[1:]):
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
        relation_class: Literal["clear_split", "shoulder_overlap"] = (
            "clear_split"
            if valley_fraction <= policy.clear_valley_max_fraction
            else "shoulder_overlap"
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
        boundary_signal, context_interval, envelope_seeds[0],
        left_relation, right_relation, policy,
    )
    _, right = _seed_interval_indices(
        boundary_signal, context_interval, envelope_seeds[-1],
        left_relation, right_relation, policy,
    )
    relation_labels = _relation_labels(left_relation, right_relation)
    if policy.morphology_trace_method == "rt_envelope":
        # Qualification may discard a lobe as an independent candidate, but
        # that is not evidence that its parent envelope is a clean component.
        # Keep canonical contour complexity as review evidence, separately
        # from the coarser envelope's quantitative boundaries.
        contour = boundary_signal[left:right]
        peaks = find_peaks(contour)[0]
        prominences = peak_prominences(contour, peaks)[0]
        review_lobes = sum(
            prominences >= policy.min_prominence_fraction_of_envelope * np.max(contour)
        )
        if len(envelope_seeds) > 1 or review_lobes > 1:
            relation_labels = (*relation_labels, "shoulder_overlap")
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
    apex_index = seed.index
    if policy.morphology_trace_method == "rt_envelope":
        segment_residual = np.maximum(
            intensity[interval.start_index : interval.end_index]
            - baseline[interval.start_index : interval.end_index],
            0.0,
        )
        apex_index = interval.start_index + int(np.argmax(segment_residual))
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
    if "shoulder_overlap" in relation_labels:
        return "shoulder_candidate"
    if "clear_split" in relation_labels:
        return "separate_peak"
    return "isolated_peak"


def _boundary_stop_reason(
    segment_class: ChromPeakSegmentClass,
    relation_labels: tuple[str, ...],
) -> str:
    if segment_class == "low_scan_segment":
        return "segment_too_few_scans"
    if "shoulder_overlap" in relation_labels:
        return "shoulder_overlap_review"
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
    if "shoulder_overlap" in relation_labels:
        sources.append("shoulder_overlap")
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
