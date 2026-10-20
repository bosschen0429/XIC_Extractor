from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from xic_extractor.peak_detection.baseline import integrate_with_baseline
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    gaussian15_morphology_trace,
    morphology_trace_effective_points,
    positive_residual_area,
)

SelectedBoundaryMode = Literal[
    "resolver_interval",
    "selected_full_envelope",
    "review_only",
    "invalid_trace",
]
RowBoundaryDecision = Literal["accept_candidate", "reject", "externalize", "defer"]


@dataclass(frozen=True)
class TraceInterval:
    start_index: int
    end_index: int
    rt_start_min: float
    rt_end_min: float
    scan_count: int


@dataclass(frozen=True)
class BoundaryExpansion:
    interval: TraceInterval
    bridged_internal_dip: bool


@dataclass(frozen=True)
class SelectedEnvelopePolicy:
    min_scan_count: int = 3
    min_apex_residual: float = 5.0
    baseline_return_fraction: float = 0.02
    baseline_return_min_residual: float = 1.0
    morphology_trace_method: str = "gaussian_15"
    morphology_trace_window_points: int = DEFAULT_GAUSSIAN15_WINDOW_POINTS
    sustained_baseline_return_scan_count: int = 2
    internal_dip_bridge_max_scan_count: int = 1
    max_envelope_width_min: float = 10.0
    neighbor_apex_min_fraction: float = 0.75
    baseline_separated_neighbor_apex_min_fraction: float = 0.20
    neighbor_apex_min_delta_min: float = 0.5
    split_apex_max_delta_min: float = 2.0

    def baseline_return_threshold(self, apex_residual: float) -> float:
        return max(
            self.baseline_return_min_residual,
            apex_residual * self.baseline_return_fraction,
        )


@dataclass(frozen=True)
class SelectedEnvelopeBoundaryEvaluation:
    selected_candidate_id: str
    resolver_interval: TraceInterval
    selected_envelope_interval: TraceInterval
    quantitation_context_interval: TraceInterval
    policy_snapshot: SelectedEnvelopePolicy
    resolved_baseline_return_threshold: float | None
    morphology_trace_method: str
    morphology_trace_window_points: int
    morphology_trace_effective_points: int
    selected_boundary_mode: SelectedBoundaryMode
    legacy_resolver_provenance: str
    boundary_change_class: str
    boundary_evidence_sources: tuple[str, ...]
    boundary_stop_reason: str
    asls_area_old_interval: float | None
    asls_area_selected_envelope: float | None
    area_delta_ratio: float | None
    row_boundary_decision: RowBoundaryDecision
    gaussian15_area_old_interval_shadow: float | None = None
    gaussian15_area_selected_envelope_shadow: float | None = None
    gaussian15_area_delta_ratio_shadow: float | None = None


def evaluate_selected_envelope_boundary(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    baseline_values: np.ndarray,
    *,
    selected_apex_rt: float,
    resolver_rt_start: float,
    resolver_rt_end: float,
    quantitation_context_rt_start: float,
    quantitation_context_rt_end: float,
    selected_candidate_id: str,
    policy: SelectedEnvelopePolicy | None = None,
    legacy_resolver_provenance: str = "",
    blank_like_context: bool = False,
) -> SelectedEnvelopeBoundaryEvaluation:
    """Evaluate a bounded full-envelope candidate for one selected peak."""
    active_policy = policy or SelectedEnvelopePolicy()
    rt, intensity, baseline = _coerce_trace(
        rt_values,
        intensity_values,
        baseline_values,
    )
    fallback_interval = _fallback_interval(rt)

    if _is_malformed_trace(rt, intensity, baseline):
        return _blocked_evaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=fallback_interval,
            selected_envelope_interval=fallback_interval,
            quantitation_context_interval=fallback_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=None,
            mode="invalid_trace",
            change_class="malformed",
            stop_reason="malformed_trace",
            row_boundary_decision="externalize",
            legacy_resolver_provenance=legacy_resolver_provenance,
            evidence_sources=("trace_validation",),
        )

    resolver_interval = _interval_from_rt_bounds(rt, resolver_rt_start, resolver_rt_end)
    context_interval = _interval_from_rt_bounds(
        rt,
        quantitation_context_rt_start,
        quantitation_context_rt_end,
    )

    if len(rt) < active_policy.min_scan_count:
        return _blocked_evaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=resolver_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=None,
            mode="invalid_trace",
            change_class="low_scan",
            stop_reason="too_few_scans",
            row_boundary_decision="externalize",
            legacy_resolver_provenance=legacy_resolver_provenance,
            evidence_sources=("trace_validation",),
        )

    residual = intensity - baseline
    apex_index = _nearest_index(rt, selected_apex_rt)
    apex_residual = float(residual[apex_index])
    threshold = active_policy.baseline_return_threshold(max(apex_residual, 0.0))
    if apex_residual < active_policy.min_apex_residual:
        return _blocked_evaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=resolver_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            mode="review_only",
            change_class="low_sn",
            stop_reason="apex_below_min_residual",
            row_boundary_decision="externalize",
            legacy_resolver_provenance=legacy_resolver_provenance,
            evidence_sources=("asls_baseline", "local_sn"),
            rt=rt,
            intensity=intensity,
            baseline=baseline,
        )

    morphology_trace = _morphology_trace(residual, active_policy)
    morphology_effective_points = _morphology_trace_effective_points(
        len(residual),
        active_policy,
    )
    boundary_expansion = _expand_to_baseline_return(
        rt,
        morphology_trace,
        apex_index=apex_index,
        threshold=threshold,
        context_interval=context_interval,
        policy=active_policy,
    )
    envelope_interval = boundary_expansion.interval
    old_area, envelope_area, delta_ratio = _area_delta(
        rt,
        intensity,
        baseline,
        resolver_interval,
        envelope_interval,
    )
    shadow_old_area, shadow_envelope_area, shadow_delta_ratio = (
        _gaussian15_shadow_area_delta(
            rt,
            residual,
            resolver_interval,
            envelope_interval,
        )
    )
    evidence_sources: tuple[str, ...] = (
        "asls_baseline",
        "morphology_trace",
        "raw_residual",
        "baseline_return",
        "quantitation_context",
    )
    if boundary_expansion.bridged_internal_dip:
        evidence_sources += ("internal_dip_bridge",)

    if envelope_interval.scan_count < active_policy.min_scan_count:
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="low_scan",
            boundary_evidence_sources=evidence_sources + ("scan_support",),
            boundary_stop_reason="selected_envelope_too_few_scans",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="externalize",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    context_apex_conflict = _context_apex_conflict_index(
        rt,
        residual,
        context_interval,
        envelope_interval,
        selected_apex_index=apex_index,
        apex_residual=apex_residual,
        policy=active_policy,
    )
    if context_apex_conflict is not None:
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="context_apex_conflict",
            boundary_evidence_sources=evidence_sources + ("context_apex_conflict",),
            boundary_stop_reason="stronger_context_apex_outside_envelope",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="externalize",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    if blank_like_context:
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="carryover_blank_like",
            boundary_evidence_sources=evidence_sources + ("blank_context",),
            boundary_stop_reason="blank_like_context",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="externalize",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    if _touches_context_edge_above_threshold(
        residual,
        envelope_interval,
        context_interval,
        threshold=threshold,
    ):
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="tail_uncertain",
            boundary_evidence_sources=evidence_sources + ("tail_stop",),
            boundary_stop_reason="context_edge_above_baseline_return",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="defer",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    if _interval_width_min(envelope_interval) > active_policy.max_envelope_width_min:
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="overmerge_rejected",
            boundary_evidence_sources=evidence_sources + ("max_width",),
            boundary_stop_reason="max_envelope_width_exceeded",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="reject",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    neighbor_apexes = _neighbor_apex_indices(
        rt,
        residual,
        envelope_interval,
        selected_apex_index=apex_index,
        apex_residual=apex_residual,
        baseline_return_threshold=threshold,
        policy=active_policy,
    )
    if neighbor_apexes:
        change_class, stop_reason, evidence_label = _apex_conflict_labels(
            rt,
            selected_apex_index=apex_index,
            neighbor_apexes=neighbor_apexes,
            policy=active_policy,
        )
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="review_only",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class=change_class,
            boundary_evidence_sources=evidence_sources + (evidence_label,),
            boundary_stop_reason=stop_reason,
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="externalize",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    if _interval_narrows_resolver(envelope_interval, resolver_interval):
        return SelectedEnvelopeBoundaryEvaluation(
            selected_candidate_id=selected_candidate_id,
            resolver_interval=resolver_interval,
            selected_envelope_interval=envelope_interval,
            quantitation_context_interval=context_interval,
            policy_snapshot=active_policy,
            resolved_baseline_return_threshold=threshold,
            morphology_trace_method=active_policy.morphology_trace_method,
            morphology_trace_window_points=(
                active_policy.morphology_trace_window_points
            ),
            morphology_trace_effective_points=morphology_effective_points,
            selected_boundary_mode="selected_full_envelope",
            legacy_resolver_provenance=legacy_resolver_provenance,
            boundary_change_class="resolver_overwide_narrowed",
            boundary_evidence_sources=evidence_sources
            + ("resolver_interval_narrowing",),
            boundary_stop_reason="baseline_return_reached",
            asls_area_old_interval=old_area,
            asls_area_selected_envelope=envelope_area,
            area_delta_ratio=delta_ratio,
            row_boundary_decision="accept_candidate",
            gaussian15_area_old_interval_shadow=shadow_old_area,
            gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
            gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
        )

    if envelope_interval == resolver_interval:
        mode: SelectedBoundaryMode = "resolver_interval"
        change_class = "no_change"
    elif boundary_expansion.bridged_internal_dip:
        mode = "selected_full_envelope"
        change_class = "internal_dip_bridged"
    else:
        mode = "selected_full_envelope"
        change_class = "flank_recovered"

    return SelectedEnvelopeBoundaryEvaluation(
        selected_candidate_id=selected_candidate_id,
        resolver_interval=resolver_interval,
        selected_envelope_interval=envelope_interval,
        quantitation_context_interval=context_interval,
        policy_snapshot=active_policy,
        resolved_baseline_return_threshold=threshold,
        morphology_trace_method=active_policy.morphology_trace_method,
        morphology_trace_window_points=active_policy.morphology_trace_window_points,
        morphology_trace_effective_points=morphology_effective_points,
        selected_boundary_mode=mode,
        legacy_resolver_provenance=legacy_resolver_provenance,
        boundary_change_class=change_class,
        boundary_evidence_sources=evidence_sources,
        boundary_stop_reason="baseline_return_reached",
        asls_area_old_interval=old_area,
        asls_area_selected_envelope=envelope_area,
        area_delta_ratio=delta_ratio,
        row_boundary_decision="accept_candidate",
        gaussian15_area_old_interval_shadow=shadow_old_area,
        gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
        gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
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
    return not (
        np.all(np.isfinite(rt))
        and np.all(np.isfinite(intensity))
        and np.all(np.isfinite(baseline))
    )


def _fallback_interval(rt: np.ndarray) -> TraceInterval:
    if rt.ndim == 1 and len(rt) >= 1 and np.all(np.isfinite(rt)):
        return TraceInterval(0, len(rt), float(rt[0]), float(rt[-1]), len(rt))
    return TraceInterval(0, 0, 0.0, 0.0, 0)


def _interval_from_rt_bounds(
    rt: np.ndarray,
    rt_start: float,
    rt_end: float,
) -> TraceInterval:
    start = min(float(rt_start), float(rt_end))
    end = max(float(rt_start), float(rt_end))
    left = int(np.searchsorted(rt, start, side="left"))
    right = int(np.searchsorted(rt, end, side="right"))
    left = max(0, min(left, len(rt) - 2))
    right = max(left + 2, min(right, len(rt)))
    return TraceInterval(
        start_index=left,
        end_index=right,
        rt_start_min=float(rt[left]),
        rt_end_min=float(rt[right - 1]),
        scan_count=right - left,
    )


def _nearest_index(rt: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(rt - float(value))))


def _morphology_trace(
    residual: np.ndarray,
    policy: SelectedEnvelopePolicy,
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
    policy: SelectedEnvelopePolicy,
) -> int:
    return morphology_trace_effective_points(
        trace_length,
        method=policy.morphology_trace_method,
        window_points=policy.morphology_trace_window_points,
    )


def _expand_to_baseline_return(
    rt: np.ndarray,
    morphology_trace: np.ndarray,
    *,
    apex_index: int,
    threshold: float,
    context_interval: TraceInterval,
    policy: SelectedEnvelopePolicy,
) -> BoundaryExpansion:
    left, bridged_left = _find_left_boundary(
        morphology_trace,
        apex_index=apex_index,
        threshold=threshold,
        context_interval=context_interval,
        policy=policy,
    )
    right, bridged_right = _find_right_boundary(
        morphology_trace,
        apex_index=apex_index,
        threshold=threshold,
        context_interval=context_interval,
        policy=policy,
    )

    interval = TraceInterval(
        start_index=left,
        end_index=right,
        rt_start_min=float(rt[left]),
        rt_end_min=float(rt[right - 1]),
        scan_count=right - left,
    )
    return BoundaryExpansion(
        interval=interval,
        bridged_internal_dip=bridged_left or bridged_right,
    )


def _find_left_boundary(
    morphology_trace: np.ndarray,
    *,
    apex_index: int,
    threshold: float,
    context_interval: TraceInterval,
    policy: SelectedEnvelopePolicy,
) -> tuple[int, bool]:
    index = apex_index - 1
    bridged_internal_dip = False
    while index >= context_interval.start_index:
        if morphology_trace[index] > threshold:
            index -= 1
            continue

        run_start = index
        while (
            run_start > context_interval.start_index
            and morphology_trace[run_start - 1] <= threshold
        ):
            run_start -= 1
        run_length = index - run_start + 1
        has_signal_beyond_run = (
            run_start > context_interval.start_index
            and morphology_trace[run_start - 1] > threshold
        )
        if _can_bridge_baseline_run(
            run_length,
            has_signal_beyond_run=has_signal_beyond_run,
            policy=policy,
        ):
            bridged_internal_dip = True
            index = run_start - 1
            continue
        return index + 1, bridged_internal_dip

    return context_interval.start_index, bridged_internal_dip


def _find_right_boundary(
    morphology_trace: np.ndarray,
    *,
    apex_index: int,
    threshold: float,
    context_interval: TraceInterval,
    policy: SelectedEnvelopePolicy,
) -> tuple[int, bool]:
    index = apex_index + 1
    bridged_internal_dip = False
    while index < context_interval.end_index:
        if morphology_trace[index] > threshold:
            index += 1
            continue

        run_end = index
        while (
            run_end + 1 < context_interval.end_index
            and morphology_trace[run_end + 1] <= threshold
        ):
            run_end += 1
        run_length = run_end - index + 1
        has_signal_beyond_run = (
            run_end + 1 < context_interval.end_index
            and morphology_trace[run_end + 1] > threshold
        )
        if _can_bridge_baseline_run(
            run_length,
            has_signal_beyond_run=has_signal_beyond_run,
            policy=policy,
        ):
            bridged_internal_dip = True
            index = run_end + 1
            continue
        return index, bridged_internal_dip

    return context_interval.end_index, bridged_internal_dip


def _can_bridge_baseline_run(
    run_length: int,
    *,
    has_signal_beyond_run: bool,
    policy: SelectedEnvelopePolicy,
) -> bool:
    if not has_signal_beyond_run:
        return False
    if run_length >= policy.sustained_baseline_return_scan_count:
        return False
    return run_length <= policy.internal_dip_bridge_max_scan_count


def _area_delta(
    rt: np.ndarray,
    intensity: np.ndarray,
    baseline: np.ndarray,
    old_interval: TraceInterval,
    envelope_interval: TraceInterval,
) -> tuple[float | None, float | None, float | None]:
    old_area = integrate_with_baseline(
        intensity,
        rt,
        old_interval.start_index,
        old_interval.end_index,
        baseline_values=baseline,
    ).area_baseline_corrected
    envelope_area = integrate_with_baseline(
        intensity,
        rt,
        envelope_interval.start_index,
        envelope_interval.end_index,
        baseline_values=baseline,
    ).area_baseline_corrected
    if old_area > 0:
        return old_area, envelope_area, (envelope_area - old_area) / old_area
    return old_area, envelope_area, None


def _gaussian15_shadow_area_delta(
    rt: np.ndarray,
    residual: np.ndarray,
    old_interval: TraceInterval,
    envelope_interval: TraceInterval,
) -> tuple[float | None, float | None, float | None]:
    smoothed_residual = gaussian15_morphology_trace(residual)
    old_area = _positive_residual_area(rt, smoothed_residual, old_interval)
    envelope_area = _positive_residual_area(rt, smoothed_residual, envelope_interval)
    if old_area > 0:
        return old_area, envelope_area, (envelope_area - old_area) / old_area
    return old_area, envelope_area, None


def _positive_residual_area(
    rt: np.ndarray,
    residual: np.ndarray,
    interval: TraceInterval,
) -> float:
    return positive_residual_area(
        rt,
        residual,
        interval.start_index,
        interval.end_index,
    )


def _blocked_evaluation(
    *,
    selected_candidate_id: str,
    resolver_interval: TraceInterval,
    selected_envelope_interval: TraceInterval,
    quantitation_context_interval: TraceInterval,
    policy_snapshot: SelectedEnvelopePolicy,
    resolved_baseline_return_threshold: float | None,
    mode: SelectedBoundaryMode,
    change_class: str,
    stop_reason: str,
    row_boundary_decision: RowBoundaryDecision,
    legacy_resolver_provenance: str,
    evidence_sources: tuple[str, ...],
    rt: np.ndarray | None = None,
    intensity: np.ndarray | None = None,
    baseline: np.ndarray | None = None,
) -> SelectedEnvelopeBoundaryEvaluation:
    old_area = None
    envelope_area = None
    delta_ratio = None
    shadow_old_area = None
    shadow_envelope_area = None
    shadow_delta_ratio = None
    if rt is not None and intensity is not None and baseline is not None:
        old_area, envelope_area, delta_ratio = _area_delta(
            rt,
            intensity,
            baseline,
            resolver_interval,
            selected_envelope_interval,
        )
        shadow_old_area, shadow_envelope_area, shadow_delta_ratio = (
            _gaussian15_shadow_area_delta(
                rt,
                intensity - baseline,
                resolver_interval,
                selected_envelope_interval,
            )
        )
    return SelectedEnvelopeBoundaryEvaluation(
        selected_candidate_id=selected_candidate_id,
        resolver_interval=resolver_interval,
        selected_envelope_interval=selected_envelope_interval,
        quantitation_context_interval=quantitation_context_interval,
        policy_snapshot=policy_snapshot,
        resolved_baseline_return_threshold=resolved_baseline_return_threshold,
        morphology_trace_method=policy_snapshot.morphology_trace_method,
        morphology_trace_window_points=policy_snapshot.morphology_trace_window_points,
        morphology_trace_effective_points=0,
        selected_boundary_mode=mode,
        legacy_resolver_provenance=legacy_resolver_provenance,
        boundary_change_class=change_class,
        boundary_evidence_sources=evidence_sources,
        boundary_stop_reason=stop_reason,
        asls_area_old_interval=old_area,
        asls_area_selected_envelope=envelope_area,
        area_delta_ratio=delta_ratio,
        row_boundary_decision=row_boundary_decision,
        gaussian15_area_old_interval_shadow=shadow_old_area,
        gaussian15_area_selected_envelope_shadow=shadow_envelope_area,
        gaussian15_area_delta_ratio_shadow=shadow_delta_ratio,
    )


def _touches_context_edge_above_threshold(
    residual: np.ndarray,
    envelope_interval: TraceInterval,
    context_interval: TraceInterval,
    *,
    threshold: float,
) -> bool:
    left_open = envelope_interval.start_index == context_interval.start_index
    right_open = envelope_interval.end_index == context_interval.end_index
    if left_open and residual[envelope_interval.start_index] > threshold:
        return True
    return right_open and residual[envelope_interval.end_index - 1] > threshold


def _interval_width_min(interval: TraceInterval) -> float:
    return interval.rt_end_min - interval.rt_start_min


def _interval_narrows_resolver(
    envelope_interval: TraceInterval,
    resolver_interval: TraceInterval,
) -> bool:
    return (
        envelope_interval.start_index > resolver_interval.start_index
        or envelope_interval.end_index < resolver_interval.end_index
    )


def _context_apex_conflict_index(
    rt: np.ndarray,
    residual: np.ndarray,
    context_interval: TraceInterval,
    envelope_interval: TraceInterval,
    *,
    selected_apex_index: int,
    apex_residual: float,
    policy: SelectedEnvelopePolicy,
) -> int | None:
    min_conflict_residual = apex_residual
    best_index: int | None = None
    best_residual = min_conflict_residual
    for index in range(context_interval.start_index, context_interval.end_index):
        if envelope_interval.start_index <= index < envelope_interval.end_index:
            continue
        rt_delta = abs(rt[index] - rt[selected_apex_index])
        if rt_delta < policy.neighbor_apex_min_delta_min:
            continue
        if residual[index] < best_residual:
            continue
        if not _is_local_apex(residual, index, context_interval):
            continue
        best_index = index
        best_residual = float(residual[index])
    return best_index


def _is_local_apex(
    residual: np.ndarray,
    index: int,
    interval: TraceInterval,
) -> bool:
    left_value = residual[index - 1] if index > interval.start_index else -np.inf
    right_value = residual[index + 1] if index + 1 < interval.end_index else -np.inf
    return residual[index] >= left_value and residual[index] >= right_value


def _neighbor_apex_indices(
    rt: np.ndarray,
    residual: np.ndarray,
    envelope_interval: TraceInterval,
    *,
    selected_apex_index: int,
    apex_residual: float,
    baseline_return_threshold: float,
    policy: SelectedEnvelopePolicy,
) -> tuple[int, ...]:
    neighbors: list[int] = []
    min_neighbor_residual = apex_residual * policy.neighbor_apex_min_fraction
    min_baseline_separated_neighbor_residual = apex_residual * (
        policy.baseline_separated_neighbor_apex_min_fraction
    )
    for index in range(
        envelope_interval.start_index + 1,
        envelope_interval.end_index - 1,
    ):
        if index == selected_apex_index:
            continue
        rt_delta = abs(rt[index] - rt[selected_apex_index])
        baseline_separated = _has_raw_baseline_gap_between(
            residual,
            selected_apex_index,
            index,
            threshold=baseline_return_threshold,
        )
        sustained_baseline_separated = _has_raw_baseline_gap_between(
            residual,
            selected_apex_index,
            index,
            threshold=baseline_return_threshold,
            min_run_length=policy.sustained_baseline_return_scan_count,
        )
        if rt_delta < policy.neighbor_apex_min_delta_min and not baseline_separated:
            continue
        active_min_residual = (
            min_baseline_separated_neighbor_residual
            if sustained_baseline_separated
            else min_neighbor_residual
        )
        if residual[index] < active_min_residual:
            continue
        if (
            residual[index] >= residual[index - 1]
            and residual[index] > residual[index + 1]
        ):
            neighbors.append(index)
    return tuple(neighbors)


def _has_raw_baseline_gap_between(
    residual: np.ndarray,
    selected_apex_index: int,
    candidate_apex_index: int,
    *,
    threshold: float,
    min_run_length: int = 1,
) -> bool:
    start = min(selected_apex_index, candidate_apex_index) + 1
    end = max(selected_apex_index, candidate_apex_index)
    if start >= end:
        return False
    run_length = 0
    for value in residual[start:end]:
        if value <= threshold:
            run_length += 1
            if run_length >= min_run_length:
                return True
            continue
        run_length = 0
    return False


def _apex_conflict_labels(
    rt: np.ndarray,
    *,
    selected_apex_index: int,
    neighbor_apexes: tuple[int, ...],
    policy: SelectedEnvelopePolicy,
) -> tuple[str, str, str]:
    nearest_delta = min(
        abs(float(rt[index] - rt[selected_apex_index])) for index in neighbor_apexes
    )
    if nearest_delta <= policy.split_apex_max_delta_min:
        return (
            "split_supported",
            "split_supported_review_required",
            "split_supported",
        )
    return "neighbor_apex", "neighbor_apex_conflict", "neighbor_apex"
