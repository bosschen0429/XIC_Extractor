from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
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
    min_prominence_noise_mad: float = 0.0

    def baseline_return_threshold(self, apex_residual: float) -> float:
        return max(
            self.baseline_return_min_residual,
            apex_residual * self.baseline_return_fraction,
        )


@dataclass(frozen=True)
class ChromPeakSegment:
    segment_id: str
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

    morphology_trace = _morphology_trace(residual, active_policy)
    morphology_effective_points = _morphology_trace_effective_points(
        len(rt),
        active_policy,
    )
    seeds = _apex_seeds(morphology_trace, context_interval, active_policy)
    if active_policy.min_prominence_noise_mad > 0.0:
        seeds = _supported_apex_seeds(
            seeds,
            morphology_trace,
            rt,
            (
                intensity
                if acquired_intensity_values is None
                else acquired_intensity_values
            ),
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

    relations = _adjacent_relations(morphology_trace, seeds, active_policy)
    segments = tuple(
        _segment_from_seed(
            rt,
            intensity,
            baseline,
            morphology_trace,
            context_interval,
            seed=seed,
            seed_index=index,
            left_relation=relations[index - 1] if index > 0 else None,
            right_relation=relations[index] if index < len(relations) else None,
            policy=active_policy,
        )
        for index, seed in enumerate(seeds)
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


def select_segment_by_apex_rt(
    segments: Sequence[ChromPeakSegment],
    apex_rt_min: float,
) -> ChromPeakSegment | None:
    if not segments:
        return None
    target = float(apex_rt_min)
    return min(segments, key=lambda segment: abs(segment.apex_rt_min - target))


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
    residual: np.ndarray,
    policy: ChromPeakSegmentPolicy,
) -> np.ndarray:
    window = _morphology_trace_effective_points(len(residual), policy)
    if window <= 1:
        return residual.copy()
    if policy.morphology_trace_method == "gaussian_15":
        return gaussian15_morphology_trace(residual, window_points=window)
    if policy.morphology_trace_method == "smooth_15":
        kernel = np.ones(window, dtype=float) / float(window)
        return np.convolve(residual, kernel, mode="same")
    return residual.copy()


def _morphology_trace_effective_points(
    trace_length: int,
    policy: ChromPeakSegmentPolicy,
) -> int:
    if trace_length <= 0:
        return 0
    if policy.morphology_trace_method not in {"gaussian_15", "smooth_15"}:
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
    for seed, prominence, left, right in zip(
        interior, prominences, left_bases, right_bases
    ):
        left, right = start + int(left), start + int(right) + 1
        noise = short_scale_noise_mad(rt[left:right], acquired[left:right])
        if prominence >= policy.min_prominence_noise_mad * noise:
            supported.add(seed.index)
    # Suppress subordinate ripple only inside an independently supported
    # envelope. A clear valley starts another group. An unsupported group is
    # retained for the ordinary support/contrast gates, not removed to make a
    # window question artificially unique.
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
            min(left_seed.height, right_seed.height),
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
    seed_index: int,
    left_relation: _AdjacentPeakRelation | None,
    right_relation: _AdjacentPeakRelation | None,
    policy: ChromPeakSegmentPolicy,
) -> ChromPeakSegment:
    left, right = _seed_interval_indices(
        morphology_trace, context_interval, seed, left_relation, right_relation, policy
    )
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
    return ChromPeakSegment(
        segment_id=f"chrom_peak_segment_{seed_index + 1:03d}",
        interval=interval,
        apex_index=seed.index,
        apex_rt_min=float(rt[seed.index]),
        raw_apex_residual=max(
            0.0,
            float(intensity[seed.index] - baseline[seed.index]),
        ),
        morphology_apex_residual=seed.height,
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
        left > context_interval.start_index
        and morphology_trace[left - 1] > threshold
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
    while (
        right < context_interval.end_index
        and morphology_trace[right] > threshold
    ):
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
