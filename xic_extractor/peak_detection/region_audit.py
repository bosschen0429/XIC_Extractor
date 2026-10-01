from __future__ import annotations

import warnings
from dataclasses import dataclass, replace

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.peak_detection.baseline import (
    BaselineMethod,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.boundaries import (
    BoundaryHypothesis,
    enumerate_boundary_hypotheses,
)
from xic_extractor.peak_detection.boundary_scoring import score_boundary_hypothesis
from xic_extractor.peak_detection.cwt import add_cwt_proposals_for_audit
from xic_extractor.peak_detection.integration_audit import (
    CellIntegrationAuditSummary,
    build_cell_integration_audit_summary,
)
from xic_extractor.peak_detection.interval_selection import (
    WeightedInterval,
    select_weighted_nonoverlap_intervals,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidatesResult,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.region_mixture_diagnostic import (
    classify_local_mixture,
)
from xic_extractor.peak_detection.region_model_selection import (
    RegionBoundaryEvidence,
    RegionSelectionDecision,
    decide_region_selection,
)
from xic_extractor.peak_detection.trace_quality import trace_continuity_score
from xic_extractor.peak_detection.traces import TraceGroup


@dataclass(frozen=True)
class PeakRegionAuditSummary:
    candidate_count: int | None = None
    selected_proposal_sources: tuple[str, ...] = ()
    selected_merge_note: str = ""
    shadow_status: str = ""
    shadow_verdict: str = ""
    merge_suggestion_source: str = ""
    area_ratio: float | None = None
    selected_interval_count: int | None = None
    selected_interval_gap_max_min: float | None = None
    local_mixture_diagnostic: str = ""
    local_mixture_reason: str = ""
    review_reason: str = ""
    decision_status: str = ""
    decision_class: str = ""
    product_action: str = ""
    promotion_reason: str = ""
    baseline_method: str = ""
    integration_audit: CellIntegrationAuditSummary | None = None


EMPTY_REGION_AUDIT = PeakRegionAuditSummary()


@dataclass(frozen=True)
class _ScoredBoundary:
    boundary: BoundaryHypothesis
    evidence: RegionBoundaryEvidence
    score: int


def _scored_region_boundaries_for_candidates(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    candidates: tuple[PeakCandidate, ...],
    selected_candidate: PeakCandidate,
    *,
    baseline_integration_method: BaselineMethod = "asls",
) -> tuple[_ScoredBoundary, ...]:
    scored: list[_ScoredBoundary] = []
    for index, candidate in enumerate(candidates):
        candidate_id = _audit_candidate_id(index, candidate)
        boundaries = enumerate_boundary_hypotheses(
            rt_values,
            intensity_values,
            candidate,
            candidate_id=candidate_id,
        )
        reference = _candidate_interval_reference(boundaries)
        scored.extend(
            _score_boundary(
                boundary,
                reference,
                rt_values,
                intensity_values,
                candidate,
                candidate_id,
                selected_candidate=candidate == selected_candidate,
                baseline_integration_method=baseline_integration_method,
            )
            for boundary in boundaries
        )
    selected_ids = _selected_top_boundary_ids(tuple(scored))
    return tuple(
        replace(
            boundary,
            evidence=replace(
                boundary.evidence,
                nonoverlap_selected=boundary.evidence.boundary_id in selected_ids,
            ),
        )
        for boundary in scored
    )


def _selected_top_boundary_ids(
    scored_boundaries: tuple[_ScoredBoundary, ...],
) -> set[str]:
    top_by_candidate: dict[str, _ScoredBoundary] = {}
    for boundary in scored_boundaries:
        current = top_by_candidate.get(boundary.evidence.candidate_id)
        if current is None or _top_boundary_rank(boundary) > _top_boundary_rank(
            current
        ):
            top_by_candidate[boundary.evidence.candidate_id] = boundary
    intervals = tuple(
        WeightedInterval(
            item_id=boundary.evidence.boundary_id,
            left=boundary.evidence.rt_left_min,
            right=boundary.evidence.rt_right_min,
            weight=boundary.score,
            selected_priority=1 if boundary.evidence.selected_candidate else 0,
            candidate_interval_priority=(
                1 if boundary.evidence.is_candidate_interval else 0
            ),
        )
        for boundary in top_by_candidate.values()
    )
    return {
        interval.item_id
        for interval in select_weighted_nonoverlap_intervals(intervals)
    }


def _score_boundary(
    boundary: BoundaryHypothesis,
    reference: BoundaryHypothesis,
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    candidate: PeakCandidate,
    candidate_id: str,
    *,
    selected_candidate: bool,
    baseline_integration_method: BaselineMethod,
) -> _ScoredBoundary:
    baseline = integrate_with_baseline(
        intensity_values,
        rt_values,
        boundary.left_index,
        boundary.right_index,
        baseline_method=baseline_integration_method,
    )
    score = score_boundary_hypothesis(
        boundary,
        reference,
        baseline_score=baseline.baseline_score,
        trace_continuity=trace_continuity_score(
            intensity_values,
            left=boundary.left_index,
            right=boundary.right_index,
        ),
    )
    return _ScoredBoundary(
        boundary=boundary,
        evidence=RegionBoundaryEvidence(
            boundary_id=boundary.boundary_id,
            candidate_id=candidate_id,
            proposal_sources=candidate.proposal_sources,
            boundary_sources=boundary.sources,
            selected_candidate=selected_candidate,
            is_candidate_interval="candidate_interval" in boundary.sources,
            nonoverlap_selected=False,
            rt_left_min=boundary.rt_left_min,
            rt_apex_min=boundary.rt_apex_min,
            rt_right_min=boundary.rt_right_min,
            area_raw_counts_seconds=boundary.area_raw_counts_seconds,
            boundary_score=score.score,
            scan_count=boundary.scan_count,
            support_labels=score.support_labels,
            concern_labels=score.concern_labels,
        ),
        score=score.score,
    )


def _audit_candidate_id(index: int, candidate: PeakCandidate) -> str:
    return "|".join(
        (
            f"candidate-{index}",
            ";".join(candidate.proposal_sources),
            f"{candidate.selection_apex_rt:.5f}",
        )
    )


def _candidate_interval_reference(
    boundaries: tuple[BoundaryHypothesis, ...],
) -> BoundaryHypothesis:
    for boundary in boundaries:
        if "candidate_interval" in boundary.sources:
            return boundary
    if not boundaries:
        raise ValueError("region audit requires at least one boundary hypothesis")
    return boundaries[0]


def _top_boundary_rank(boundary: _ScoredBoundary) -> tuple[int, int, float]:
    return (
        boundary.score,
        1 if boundary.evidence.is_candidate_interval else 0,
        boundary.evidence.area_raw_counts_seconds,
    )


def build_peak_region_audit_summary(
    rt_values: np.ndarray,
    intensity_values: np.ndarray,
    result: PeakDetectionResult,
    config: ExtractionConfig,
    *,
    include_cwt: bool = True,
    trace_group: TraceGroup | None = None,
) -> PeakRegionAuditSummary:
    if result.status != "OK" or result.peak is None:
        return EMPTY_REGION_AUDIT
    if trace_group is not None:
        trace = trace_group.primary_trace
        rt_values = trace.rt
        intensity_values = trace.intensity

    if include_cwt:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            audited_result = add_cwt_proposals_for_audit(
                result,
                rt_values,
                intensity_values,
                config,
            )
    else:
        audited_result = result
    selected = _selected_candidate(audited_result)
    if selected is None:
        return PeakRegionAuditSummary(candidate_count=len(audited_result.candidates))

    candidates_result = PeakCandidatesResult(
        status="OK",
        candidates=audited_result.candidates,
        n_points=audited_result.n_points,
        max_smoothed=audited_result.max_smoothed,
        n_prominent_peaks=audited_result.n_prominent_peaks,
    )
    try:
        decision = decide_region_selection(
            tuple(
                scored.evidence
                for scored in _scored_region_boundaries_for_candidates(
                    rt_values,
                    intensity_values,
                    candidates_result.candidates,
                    selected,
                )
            )
        )
    except (TypeError, ValueError) as exc:
        decision = RegionSelectionDecision(
            shadow_status="skipped_invalid_trace",
            shadow_verdict="insufficient_evidence",
            review_reason=str(exc),
        )
    return _summary_from_decision(
        decision,
        selected=selected,
        candidate_count=len(candidates_result.candidates),
        integration_audit=build_cell_integration_audit_summary(
            rt_values,
            intensity_values,
            peak_start_rt=selected.peak.peak_start,
            peak_end_rt=selected.peak.peak_end,
            raw_area=selected.peak.area,
            baseline_integration_method=getattr(
                config,
                "baseline_integration_method",
                "asls",
            ),
            baseline_audit_method=getattr(config, "baseline_audit_method", ""),
        ),
    )


def _summary_from_decision(
    decision: RegionSelectionDecision,
    *,
    selected: PeakCandidate,
    candidate_count: int,
    integration_audit: CellIntegrationAuditSummary | None = None,
) -> PeakRegionAuditSummary:
    local_mixture = classify_local_mixture(decision)
    return PeakRegionAuditSummary(
        candidate_count=candidate_count,
        selected_proposal_sources=selected.proposal_sources,
        selected_merge_note=selected.merge_note,
        shadow_status=decision.shadow_status,
        shadow_verdict=decision.shadow_verdict,
        merge_suggestion_source=decision.merge_suggestion_source,
        area_ratio=decision.area_ratio,
        selected_interval_count=decision.selected_interval_count,
        selected_interval_gap_max_min=decision.selected_interval_gap_max_min,
        local_mixture_diagnostic=local_mixture.label,
        local_mixture_reason=local_mixture.reason,
        review_reason=decision.review_reason,
        decision_status=str(decision.decision_status),
        decision_class=str(decision.decision_class),
        product_action=str(decision.product_action),
        promotion_reason=decision.promotion_reason,
        baseline_method=decision.baseline_method,
        integration_audit=integration_audit,
    )


def _selected_candidate(result: PeakDetectionResult) -> PeakCandidate | None:
    peak = result.peak
    if peak is None or not result.candidates:
        return None
    for candidate in result.candidates:
        if _same_peak(candidate.peak, peak):
            return candidate
    return min(
        result.candidates,
        key=lambda candidate: abs(candidate.selection_apex_rt - peak.rt),
    )


def _same_peak(candidate_peak: PeakResult, selected_peak: PeakResult) -> bool:
    return (
        candidate_peak == selected_peak
        or (
            abs(candidate_peak.rt - selected_peak.rt) <= 1e-9
            and abs(candidate_peak.peak_start - selected_peak.peak_start) <= 1e-9
            and abs(candidate_peak.peak_end - selected_peak.peak_end) <= 1e-9
        )
    )
