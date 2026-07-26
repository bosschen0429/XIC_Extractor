from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.evidence_semantics import (
    CommonEvidence,
    EvidenceDecisionSemantics,
    EvidenceSignalSet,
    common_evidence_from_targeted_candidate,
    decision_semantics_from_signal_set,
)
from xic_extractor.neutral_loss import CandidateMS2Evidence
from xic_extractor.peak_detection.baseline import (
    BaselineMethod,
    asls_baseline,
    bounded_trace_interval,
    integrate_with_baseline,
)
from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    CidNlChromatographicSupportRef,
)
from xic_extractor.peak_detection.evidence_facts import (
    CandidateEvidenceFacts,
    decision_semantics_from_candidate_facts,
    projected_confidence_from_candidate_facts,
    projected_reason_from_candidate_facts,
)
from xic_extractor.peak_detection.integration_result import IntegrationResult
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.ms1_morphology import (
    DEFAULT_GAUSSIAN15_WINDOW_POINTS,
    MS1_MORPHOLOGY_AREA_SOURCE,
    gaussian15_positive_asls_residual_metrics,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.traces import TraceGroup

OWNER_BACKED_HYPOTHESIS_ID_PREFIX = "xic.peak-hypothesis.v1:"
OWNER_BACKED_ANALYSIS_MODE = "workflow_neutral"

_CONFIDENCE_RANK = {
    "HIGH": 0,
    "MEDIUM": 1,
    "LOW": 2,
    "VERY_LOW": 3,
    "": 4,
}


class PeakHypothesisAuthorityError(ValueError):
    """Raised when diagnostic identity is passed to selection or product code."""


@dataclass(frozen=True)
class EvidenceVector:
    """Audit evidence attached to a peak hypothesis.

    The legacy CWT fields mirror `PeakCandidate` audit-presence flags only
    and are not interpretable as CWT scale or ridge metrics.
    """

    confidence: str = ""
    projected_confidence: str = ""
    raw_score: int | None = None
    support_labels: tuple[str, ...] = ()
    concern_labels: tuple[str, ...] = ()
    cap_labels: tuple[str, ...] = ()
    reason: str = ""
    projected_reason: str = ""
    evidence_facts: CandidateEvidenceFacts | None = None
    quality_flags: tuple[str, ...] = ()
    prominence: float | None = None
    region_scan_count: int | None = None
    region_duration_min: float | None = None
    region_edge_ratio: float | None = None
    region_trace_continuity: float | None = None
    ms2_present: bool | None = None
    nl_match: bool | None = None
    ms2_trace_strength: str = ""
    nl_status: str = ""
    best_loss_ppm: float | None = None
    best_ms2_scan_rt_min: float | None = None
    apex_ms2_delta_min: float | None = None
    best_product_base_ratio: float | None = None
    trigger_scan_count: int | None = None
    strict_nl_scan_count: int | None = None
    ms1_peak_group_source: str = ""
    ms1_peak_group_rt_min: float | None = None
    ms1_peak_group_rt_max: float | None = None
    ms1_peak_group_trigger_scan_count: int | None = None
    ms1_peak_group_strict_nl_scan_count: int | None = None
    ms1_peak_group_strict_nl_event_count: int | None = None
    outside_ms1_peak_group_trigger_scan_count: int | None = None
    outside_ms1_peak_group_strict_nl_scan_count: int | None = None
    ms2_alignment_source: str = ""
    diagnostic_product_absence_reason: str = ""
    nearest_product_loss_ppm: float | None = None
    nearest_product_base_ratio: float | None = None
    nearest_product_mz: float | None = None
    rt_prior_min: float | None = None
    cwt_best_scale: float | None = None
    cwt_ridge_persistence: float | None = None
    boundary_score: float | None = None
    baseline_score: float | None = None
    common: CommonEvidence | None = None
    decision_semantics: EvidenceDecisionSemantics | None = None
    cid_nl_chromatographic_support_ref: CidNlChromatographicSupportRef | None = None


@dataclass(frozen=True)
class AuditTrail:
    proposal_sources: tuple[str, ...] = ()
    source_apex_rank: int | None = None
    merge_note: str = ""
    safe_merge_rejection_reason: str = ""
    safe_merge_promotion_source: str = ""
    safe_merge_promotion_shadow_boundary_id: str = ""
    safe_merge_promotion_area_ratio: float | None = None
    safe_merge_promotion_selected_interval_count: int | None = None
    safe_merge_promotion_selected_interval_gap_max_min: float | None = None
    selected: bool = False
    selection_rank: int | None = None
    selection_reference_rt_min: float | None = None
    rejection_reason: str = ""


@dataclass(frozen=True)
class PeakHypothesis:
    hypothesis_id: str
    trace_group_id: str
    target_label: str
    role: str
    istd_pair: str
    analysis_mode: str
    resolver_mode: str
    integration: IntegrationResult
    evidence: EvidenceVector
    audit: AuditTrail
    hypothesis_method: str = ""
    peak_observation_refs: tuple[PeakObservationRef, ...] = ()
    artifact: EvidenceArtifactRef | None = None

    @property
    def is_owner_backed(self) -> bool:
        return bool(
            self.hypothesis_method
            or self.peak_observation_refs
            or self.artifact is not None
            or self.evidence.cid_nl_chromatographic_support_ref is not None
            or self.analysis_mode == OWNER_BACKED_ANALYSIS_MODE
            or self.hypothesis_id.startswith(OWNER_BACKED_HYPOTHESIS_ID_PREFIX)
        )

    def __post_init__(self) -> None:
        if not self.is_owner_backed:
            return
        from xic_extractor.peak_detection.peak_hypothesis_identity import (
            validate_owner_backed_peak_hypothesis_instance,
        )

        validate_owner_backed_peak_hypothesis_instance(self)


def validate_peak_hypothesis_selection_authority(
    hypothesis: PeakHypothesis,
    *,
    consumer: str,
) -> None:
    """Reject diagnostic owner-backed identity at selection/product boundaries."""

    if hypothesis.is_owner_backed:
        raise PeakHypothesisAuthorityError(
            f"{consumer} cannot consume diagnostic-only owner-backed PeakHypothesis"
        )


def hypothesis_audit_id(
    *,
    sample_name: str,
    target_label: str,
    resolver_mode: str,
    candidate: PeakCandidate,
) -> str:
    return "|".join(
        (
            sample_name,
            target_label,
            resolver_mode,
            _join(candidate.proposal_sources),
            _format_float(candidate.selection_apex_rt, digits=5),
            _format_float(candidate.peak.peak_start, digits=5),
            _format_float(candidate.peak.peak_end, digits=5),
        )
    )


def build_peak_hypotheses(
    *,
    sample_name: str,
    target_label: str,
    role: str,
    istd_pair: str,
    resolver_mode: str,
    peak_result: PeakDetectionResult,
    candidate_ms2_evidence: Mapping[PeakCandidate, CandidateMS2Evidence] | None = None,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
    baseline_integration_method: BaselineMethod = "asls",
    count_no_ms2_as_detected: bool = False,
    ms1_morphology_smoothing_window_points: int = (DEFAULT_GAUSSIAN15_WINDOW_POINTS),
) -> tuple[PeakHypothesis, ...]:
    selected = _selected_candidate(peak_result)
    score_by_candidate = {
        score.candidate: score for score in peak_result.candidate_scores
    }
    rank_by_candidate = _rank_candidates(
        peak_result.candidate_scores,
        selected=selected,
    )
    selected_score = score_by_candidate.get(selected) if selected is not None else None
    evidence_by_candidate = candidate_ms2_evidence or {}
    trace_rt = rt
    trace_intensity = intensity
    if trace_group is not None:
        trace_rt = trace_group.primary_trace.rt
        trace_intensity = trace_group.primary_trace.intensity

    hypotheses: list[PeakHypothesis] = []
    for candidate in peak_result.candidates:
        score = score_by_candidate.get(candidate)
        evidence = evidence_by_candidate.get(candidate)
        is_selected = selected is not None and candidate == selected
        hypotheses.append(
            PeakHypothesis(
                hypothesis_id=hypothesis_audit_id(
                    sample_name=sample_name,
                    target_label=target_label,
                    resolver_mode=resolver_mode,
                    candidate=candidate,
                ),
                trace_group_id=trace_group.trace_group_id
                if trace_group is not None
                else _trace_group_id(
                    sample_name=sample_name,
                    target_label=target_label,
                    resolver_mode=resolver_mode,
                ),
                target_label=target_label,
                role=role,
                istd_pair=istd_pair,
                analysis_mode="targeted",
                resolver_mode=resolver_mode,
                integration=_integration_from_candidate(
                    candidate,
                    rt=trace_rt,
                    intensity=trace_intensity,
                    baseline_integration_method=baseline_integration_method,
                    ms1_morphology_smoothing_window_points=(
                        ms1_morphology_smoothing_window_points
                    ),
                ),
                evidence=_evidence_from_candidate(
                    candidate,
                    score,
                    evidence,
                    target_label=target_label,
                    count_no_ms2_as_detected=count_no_ms2_as_detected,
                ),
                audit=AuditTrail(
                    proposal_sources=candidate.proposal_sources,
                    source_apex_rank=candidate.source_apex_rank,
                    merge_note=candidate.merge_note,
                    safe_merge_rejection_reason=(candidate.safe_merge_rejection_reason),
                    safe_merge_promotion_source=(candidate.safe_merge_promotion_source),
                    safe_merge_promotion_shadow_boundary_id=(
                        candidate.safe_merge_promotion_shadow_boundary_id
                    ),
                    safe_merge_promotion_area_ratio=(
                        candidate.safe_merge_promotion_area_ratio
                    ),
                    safe_merge_promotion_selected_interval_count=(
                        candidate.safe_merge_promotion_selected_interval_count
                    ),
                    safe_merge_promotion_selected_interval_gap_max_min=(
                        candidate.safe_merge_promotion_selected_interval_gap_max_min
                    ),
                    selected=is_selected,
                    selection_rank=rank_by_candidate.get(candidate),
                    selection_reference_rt_min=peak_result.selection_reference_rt,
                    rejection_reason=""
                    if is_selected
                    else _rejection_reason(
                        candidate,
                        score,
                        selected,
                        selected_score,
                        selection_reference_rt=peak_result.selection_reference_rt,
                    ),
                ),
            )
        )
    return tuple(hypotheses)


def _trace_group_id(
    *,
    sample_name: str,
    target_label: str,
    resolver_mode: str,
) -> str:
    return "|".join((sample_name, target_label, resolver_mode))


def _integration_from_candidate(
    candidate: PeakCandidate,
    *,
    rt: object | None = None,
    intensity: object | None = None,
    baseline_integration_method: BaselineMethod = "asls",
    ms1_morphology_smoothing_window_points: int = (DEFAULT_GAUSSIAN15_WINDOW_POINTS),
) -> IntegrationResult:
    baseline = None
    morphology_metrics = None
    raw_scan_indices: tuple[int, ...] = ()
    if rt is not None and intensity is not None:
        rt_values = np.asarray(rt, dtype=float)
        intensity_values = np.asarray(intensity, dtype=float)
        left_index = _nearest_index(rt_values, candidate.peak.peak_start)
        right_index = _nearest_index(rt_values, candidate.peak.peak_end) + 1
        bounded_left, bounded_right = bounded_trace_interval(
            left_index,
            right_index,
            len(rt_values),
        )
        raw_scan_indices = tuple(range(bounded_left, bounded_right))
        baseline_values = (
            asls_baseline(intensity_values)
            if baseline_integration_method == "asls"
            else None
        )
        baseline = integrate_with_baseline(
            intensity_values,
            rt_values,
            left_index,
            right_index,
            baseline_method=baseline_integration_method,
            baseline_values=baseline_values,
        )
        if baseline_values is not None:
            morphology_metrics = gaussian15_positive_asls_residual_metrics(
                rt_values,
                intensity_values,
                baseline_values,
                bounded_left,
                bounded_right,
                window_points=ms1_morphology_smoothing_window_points,
            )
    return IntegrationResult(
        rt_left_min=candidate.peak.peak_start,
        rt_apex_min=candidate.selection_apex_rt,
        rt_right_min=candidate.peak.peak_end,
        raw_apex_rt_min=candidate.raw_apex_rt,
        rt_width_min=candidate.peak.peak_end - candidate.peak.peak_start,
        height_raw=candidate.raw_apex_intensity,
        height_smoothed=candidate.selection_apex_intensity,
        area_raw_counts_seconds=candidate.peak.area,
        area_baseline_corrected=(
            baseline.area_baseline_corrected if baseline is not None else None
        ),
        area_uncertainty=baseline.area_uncertainty if baseline is not None else None,
        area_uncertainty_formula_version=(
            baseline.area_uncertainty_formula_version if baseline is not None else ""
        ),
        baseline_residual_mad=(
            baseline.baseline_residual_mad if baseline is not None else None
        ),
        area_uncertainty_noise_source=(
            baseline.area_uncertainty_noise_source if baseline is not None else ""
        ),
        baseline_type=baseline.baseline_type if baseline is not None else "",
        baseline_score=baseline.baseline_score if baseline is not None else None,
        raw_scan_indices=raw_scan_indices,
        area_ms1_morphology=(
            morphology_metrics.area_positive_asls_residual
            if morphology_metrics is not None
            else None
        ),
        ms1_morphology_area_source=(
            MS1_MORPHOLOGY_AREA_SOURCE if morphology_metrics is not None else ""
        ),
        ms1_morphology_trace_method=(
            morphology_metrics.trace_method if morphology_metrics is not None else ""
        ),
        ms1_morphology_trace_window_points=(
            morphology_metrics.trace_window_points
            if morphology_metrics is not None
            else None
        ),
        ms1_morphology_trace_effective_points=(
            morphology_metrics.trace_effective_points
            if morphology_metrics is not None
            else None
        ),
    )


def _evidence_from_candidate(
    candidate: PeakCandidate,
    score: PeakCandidateScore | None,
    evidence: CandidateMS2Evidence | None,
    *,
    target_label: str,
    count_no_ms2_as_detected: bool = False,
) -> EvidenceVector:
    facts = score.evidence_facts if score is not None else None
    semantics = (
        decision_semantics_from_candidate_facts(
            facts,
            count_no_ms2_as_detected=count_no_ms2_as_detected,
        )
        if facts is not None
        else _legacy_decision_semantics(
            candidate,
            score,
            target_label=target_label,
            count_no_ms2_as_detected=count_no_ms2_as_detected,
        )
    )
    projected_confidence = (
        projected_confidence_from_candidate_facts(facts, semantics)
        if facts is not None
        else score.confidence
        if score is not None
        else ""
    )
    projected_reason = (
        projected_reason_from_candidate_facts(facts, semantics)
        if facts is not None
        else score.reason
        if score is not None
        else ""
    )
    common = common_evidence_from_targeted_candidate(
        candidate,
        score=score,
        candidate_ms2_evidence=evidence,
        target_label=target_label,
    )
    best_ms2_scan_rt_min = evidence.best_scan_rt if evidence is not None else None
    return EvidenceVector(
        confidence=score.confidence if score is not None else "",
        projected_confidence=projected_confidence,
        raw_score=score.raw_score if score is not None else None,
        support_labels=score.support_labels if score is not None else (),
        concern_labels=score.concern_labels if score is not None else (),
        cap_labels=score.cap_labels if score is not None else (),
        reason=score.reason if score is not None else "",
        projected_reason=projected_reason,
        evidence_facts=facts,
        quality_flags=tuple(str(flag) for flag in candidate.quality_flags),
        prominence=candidate.prominence,
        region_scan_count=candidate.region_scan_count,
        region_duration_min=candidate.region_duration_min,
        region_edge_ratio=candidate.region_edge_ratio,
        region_trace_continuity=candidate.region_trace_continuity,
        ms2_present=common.ms2_present,
        nl_match=common.nl_match,
        ms2_trace_strength=common.ms2_trace_strength,
        nl_status=evidence.nl_status if evidence is not None else "",
        best_loss_ppm=evidence.best_loss_ppm if evidence is not None else None,
        best_ms2_scan_rt_min=best_ms2_scan_rt_min,
        apex_ms2_delta_min=(
            abs(candidate.selection_apex_rt - best_ms2_scan_rt_min)
            if best_ms2_scan_rt_min is not None
            else None
        ),
        best_product_base_ratio=(
            evidence.best_product_base_ratio if evidence is not None else None
        ),
        trigger_scan_count=(
            evidence.trigger_scan_count if evidence is not None else None
        ),
        strict_nl_scan_count=(
            evidence.strict_nl_scan_count if evidence is not None else None
        ),
        ms1_peak_group_source=(
            evidence.ms1_peak_group_source if evidence is not None else ""
        ),
        ms1_peak_group_rt_min=(
            evidence.ms1_peak_group_rt_min if evidence is not None else None
        ),
        ms1_peak_group_rt_max=(
            evidence.ms1_peak_group_rt_max if evidence is not None else None
        ),
        ms1_peak_group_trigger_scan_count=(
            evidence.ms1_peak_group_trigger_scan_count if evidence is not None else None
        ),
        ms1_peak_group_strict_nl_scan_count=(
            evidence.ms1_peak_group_strict_nl_scan_count
            if evidence is not None
            else None
        ),
        ms1_peak_group_strict_nl_event_count=(
            evidence.ms1_peak_group_strict_nl_event_count
            if evidence is not None
            else None
        ),
        outside_ms1_peak_group_trigger_scan_count=(
            evidence.outside_ms1_peak_group_trigger_scan_count
            if evidence is not None
            else None
        ),
        outside_ms1_peak_group_strict_nl_scan_count=(
            evidence.outside_ms1_peak_group_strict_nl_scan_count
            if evidence is not None
            else None
        ),
        ms2_alignment_source=evidence.alignment_source if evidence is not None else "",
        diagnostic_product_absence_reason=(
            evidence.diagnostic_product_absence_reason if evidence is not None else ""
        ),
        nearest_product_loss_ppm=(
            evidence.nearest_product_loss_ppm if evidence is not None else None
        ),
        nearest_product_base_ratio=(
            evidence.nearest_product_base_ratio if evidence is not None else None
        ),
        nearest_product_mz=(
            evidence.nearest_product_mz if evidence is not None else None
        ),
        rt_prior_min=score.prior_rt if score is not None else None,
        cwt_best_scale=candidate.cwt_best_scale,
        cwt_ridge_persistence=candidate.cwt_ridge_persistence,
        common=common,
        decision_semantics=semantics,
    )


def _legacy_decision_semantics(
    candidate: PeakCandidate,
    score: PeakCandidateScore | None,
    *,
    target_label: str,
    count_no_ms2_as_detected: bool,
) -> EvidenceDecisionSemantics:
    common = common_evidence_from_targeted_candidate(
        candidate,
        score=score,
        target_label=target_label,
    )
    return decision_semantics_from_signal_set(
        EvidenceSignalSet(
            support_labels=score.support_labels if score is not None else (),
            concern_labels=score.concern_labels if score is not None else (),
            proposal_sources=candidate.proposal_sources,
            quality_flags=tuple(str(flag) for flag in candidate.quality_flags),
            ms2_present=common.ms2_present,
            nl_match=common.nl_match,
            raw_score=score.raw_score if score is not None else None,
            confidence=score.confidence if score is not None else "",
            cap_labels=score.cap_labels if score is not None else (),
            reason=score.reason if score is not None else "",
            count_no_ms2_as_detected=count_no_ms2_as_detected,
        )
    )


def _selected_candidate(peak_result: PeakDetectionResult) -> PeakCandidate | None:
    if peak_result.peak is None:
        return None
    for candidate in peak_result.candidates:
        if candidate.peak == peak_result.peak:
            return candidate
    return None


def _rank_candidates(
    scores: tuple[PeakCandidateScore, ...],
    *,
    selected: PeakCandidate | None,
) -> dict[PeakCandidate, int]:
    ranked = sorted(
        scores,
        key=lambda score: (
            0 if selected is not None and score.candidate == selected else 1,
            _CONFIDENCE_RANK.get(score.confidence, 4),
            -(score.raw_score if score.raw_score is not None else -10_000),
        ),
    )
    return {score.candidate: index + 1 for index, score in enumerate(ranked)}


def _rejection_reason(
    candidate: PeakCandidate,
    score: PeakCandidateScore | None,
    selected: PeakCandidate | None,
    selected_score: PeakCandidateScore | None,
    *,
    selection_reference_rt: float | None,
) -> str:
    if score is not None and selected_score is not None:
        if _confidence_rank(score) > _confidence_rank(selected_score):
            return "lower_confidence"
        if _raw_score(score) < _raw_score(selected_score):
            return "lower_score"
        if (
            selection_reference_rt is not None
            and selected is not None
            and abs(candidate.selection_apex_rt - selection_reference_rt)
            > abs(selected.selection_apex_rt - selection_reference_rt)
        ):
            return "farther_from_preferred_rt"
        if score.quality_penalty > selected_score.quality_penalty:
            return "quality_penalty"
    return "non_selected_candidate"


def _confidence_rank(score: PeakCandidateScore) -> int:
    return _CONFIDENCE_RANK.get(score.confidence, 4)


def _raw_score(score: PeakCandidateScore) -> int:
    if score.raw_score is None:
        return -10_000
    return score.raw_score


def _format_float(value: float, *, digits: int = 5) -> str:
    return f"{value:.{digits}f}"


def _join(values: tuple[str, ...]) -> str:
    return ";".join(values)


def _nearest_index(rt: np.ndarray, value: float) -> int:
    return int(np.argmin(np.abs(rt - value)))
