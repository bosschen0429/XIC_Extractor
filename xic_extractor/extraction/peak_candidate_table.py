from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import cast

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.extraction.peak_hypothesis_adapter import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.neutral_loss import CandidateMS2Evidence
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.candidate_scoring import score_candidate
from xic_extractor.peak_detection.cwt import add_cwt_proposals_for_audit
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    hypothesis_audit_id,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakCandidateScore,
    PeakDetectionResult,
)
from xic_extractor.peak_detection.ms1_morphology import (
    configured_morphology_window_points,
)
from xic_extractor.peak_detection.scoring_models import (
    ScoredCandidate,
    ScoringContext,
)
from xic_extractor.peak_detection.traces import TraceGroup
from xic_extractor.sample_groups import classify_sample_group

PeakCandidateTableRow = dict[str, str]

PEAK_CANDIDATE_HEADERS = (
    "sample_name",
    "group",
    "target_label",
    "role",
    "istd_pair",
    "analysis_mode",
    "resolver_mode",
    "candidate_id",
    "proposal_sources",
    "proposal_count",
    "source_apex_rank",
    "merge_note",
    "rt_left_min",
    "rt_apex_min",
    "rt_right_min",
    "raw_apex_rt_min",
    "rt_width_min",
    "selection_apex_intensity",
    "raw_apex_intensity",
    "prominence",
    "area_raw_counts_seconds",
    "area_baseline_corrected",
    "area_ms1_morphology",
    "ms1_morphology_area_source",
    "ms1_morphology_trace_method",
    "ms1_morphology_trace_window_points",
    "ms1_morphology_trace_effective_points",
    "area_uncertainty",
    "area_uncertainty_formula_version",
    "baseline_residual_mad",
    "area_uncertainty_noise_source",
    "quality_flags",
    "region_scan_count",
    "region_duration_min",
    "region_edge_ratio",
    "region_trace_continuity",
    "ms2_present",
    "nl_match",
    "ms2_trace_strength",
    "rt_prior_min",
    "rt_prior_sigma",
    "confidence",
    "raw_score",
    "support_labels",
    "concern_labels",
    "cap_labels",
    "reason",
    "selected",
    "selection_rank",
    "selection_reference_rt_min",
    "rejection_reason",
    "nl_status",
    "best_loss_ppm",
    "best_ms2_scan_rt_min",
    "apex_ms2_delta_min",
    "best_product_base_ratio",
    "trigger_scan_count",
    "strict_nl_scan_count",
    "ms1_peak_group_source",
    "ms1_peak_group_rt_min",
    "ms1_peak_group_rt_max",
    "ms1_peak_group_trigger_scan_count",
    "ms1_peak_group_strict_nl_scan_count",
    "ms1_peak_group_strict_nl_event_count",
    "outside_ms1_peak_group_trigger_scan_count",
    "outside_ms1_peak_group_strict_nl_scan_count",
    "ms2_alignment_source",
    "diagnostic_product_absence_reason",
    "nearest_product_loss_ppm",
    "nearest_product_base_ratio",
    "nearest_product_mz",
    "safe_merge_promotion_source",
    "safe_merge_promotion_shadow_boundary_id",
    "safe_merge_promotion_area_ratio",
    "safe_merge_promotion_selected_interval_count",
    "safe_merge_promotion_selected_interval_gap_max_min",
    "safe_merge_rejection_reason",
)

def candidate_audit_id(
    *,
    sample_name: str,
    target_label: str,
    resolver_mode: str,
    candidate: PeakCandidate,
) -> str:
    return hypothesis_audit_id(
        sample_name=sample_name,
        target_label=target_label,
        resolver_mode=resolver_mode,
        candidate=candidate,
    )


def build_peak_candidate_rows(
    *,
    sample_name: str,
    target_label: str,
    role: str,
    istd_pair: str,
    resolver_mode: str,
    peak_result: PeakDetectionResult,
    group: str | None = None,
    candidate_ms2_evidence: Mapping[PeakCandidate, CandidateMS2Evidence] | None = None,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
) -> list[PeakCandidateTableRow]:
    sample_group = group or classify_sample_group(sample_name)
    hypotheses = build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target_label,
        role=role,
        istd_pair=istd_pair,
        resolver_mode=resolver_mode,
        peak_result=peak_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
    )
    return build_peak_candidate_rows_from_hypotheses(
        sample_name=sample_name,
        group=sample_group,
        hypotheses=hypotheses,
    )


def build_peak_candidate_rows_from_hypotheses(
    *,
    sample_name: str,
    hypotheses: tuple[PeakHypothesis, ...],
    group: str | None = None,
) -> list[PeakCandidateTableRow]:
    sample_group = group or classify_sample_group(sample_name)
    return [
        _row_from_hypothesis(
            sample_name=sample_name,
            group=sample_group,
            hypothesis=hypothesis,
        )
        for hypothesis in hypotheses
    ]


def with_product_selected_marker(
    hypotheses: tuple[PeakHypothesis, ...],
    selected_candidate_id: str | None,
    *,
    selected_hypothesis: PeakHypothesis | None = None,
) -> tuple[PeakHypothesis, ...]:
    selected_hypothesis_id = _product_selected_marker_hypothesis_id(
        hypotheses,
        selected_candidate_id,
        selected_hypothesis=selected_hypothesis,
    )
    if selected_hypothesis_id is None:
        return hypotheses
    updated: list[PeakHypothesis] = []
    for hypothesis in hypotheses:
        selected = hypothesis.hypothesis_id == selected_hypothesis_id
        selection_rank = 1 if selected else hypothesis.audit.selection_rank
        if not selected and selection_rank == 1:
            selection_rank = None
        updated.append(
            replace(
                hypothesis,
                audit=replace(
                    hypothesis.audit,
                    selected=selected,
                    selection_rank=selection_rank,
                ),
            )
        )
    return tuple(updated)


def _product_selected_marker_hypothesis_id(
    hypotheses: tuple[PeakHypothesis, ...],
    selected_candidate_id: str | None,
    *,
    selected_hypothesis: PeakHypothesis | None,
) -> str | None:
    if selected_candidate_id is not None:
        for hypothesis in hypotheses:
            if hypothesis.hypothesis_id == selected_candidate_id:
                return selected_candidate_id
    if selected_hypothesis is None:
        return None

    selected_key = _selected_marker_projection_key(selected_hypothesis)
    matches = [
        hypothesis
        for hypothesis in hypotheses
        if _selected_marker_projection_key(hypothesis) == selected_key
    ]
    if len(matches) != 1:
        return None
    return matches[0].hypothesis_id


def _selected_marker_projection_key(
    hypothesis: PeakHypothesis,
) -> tuple[str, str, str, str, str, str, str, str, str, str, str]:
    integration = hypothesis.integration
    return (
        hypothesis.trace_group_id,
        hypothesis.target_label,
        hypothesis.role,
        hypothesis.istd_pair,
        hypothesis.analysis_mode,
        hypothesis.resolver_mode,
        _format_float(integration.rt_left_min),
        _format_float(integration.rt_apex_min),
        _format_float(integration.rt_right_min),
        _format_float(integration.raw_apex_rt_min),
        _format_float(integration.area_raw_counts_seconds),
    )


def build_peak_candidate_audit_hypotheses(
    *,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate_ms2_builder: Callable[[PeakCandidate], CandidateMS2Evidence | None],
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
    audit_peak_result: PeakDetectionResult | None = None,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    istd_confidence_note: str | None = None,
    include_candidate_ms2_evidence: bool = True,
) -> tuple[PeakHypothesis, ...]:
    audited = audit_peak_result
    if audited is None:
        audited = (
            add_cwt_proposals_for_audit(peak_result, rt, intensity, config)
            if rt is not None and intensity is not None
            else peak_result
        )
    if scoring_context_builder is not None:
        audited = _with_rescored_audit_candidates(
            audited,
            peak_result,
            scoring_context_builder,
            istd_confidence_note=istd_confidence_note,
        )
    return build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        resolver_mode=config.resolver_mode,
        peak_result=audited,
        candidate_ms2_evidence=(
            _candidate_table_ms2_evidence(audited, candidate_ms2_builder)
            if include_candidate_ms2_evidence
            else None
        ),
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
        ms1_morphology_smoothing_window_points=(
            configured_morphology_window_points(config)
        ),
        baseline_integration_method=cast(
            BaselineMethod,
            config.baseline_integration_method,
        ),
    )


def _row_from_hypothesis(
    *,
    sample_name: str,
    group: str,
    hypothesis: PeakHypothesis,
) -> PeakCandidateTableRow:
    return {
        "sample_name": sample_name,
        "group": group,
        "target_label": hypothesis.target_label,
        "role": hypothesis.role,
        "istd_pair": hypothesis.istd_pair,
        "analysis_mode": hypothesis.analysis_mode,
        "resolver_mode": hypothesis.resolver_mode,
        "candidate_id": hypothesis.hypothesis_id,
        "proposal_sources": _join(hypothesis.audit.proposal_sources),
        "proposal_count": str(len(hypothesis.audit.proposal_sources)),
        "source_apex_rank": _format_optional_int(
            hypothesis.audit.source_apex_rank
        ),
        "merge_note": hypothesis.audit.merge_note,
        "rt_left_min": _format_float(hypothesis.integration.rt_left_min),
        "rt_apex_min": _format_float(hypothesis.integration.rt_apex_min),
        "rt_right_min": _format_float(hypothesis.integration.rt_right_min),
        "raw_apex_rt_min": _format_float(hypothesis.integration.raw_apex_rt_min),
        "rt_width_min": _format_float(hypothesis.integration.rt_width_min),
        "selection_apex_intensity": _format_float(
            hypothesis.integration.height_smoothed
        ),
        "raw_apex_intensity": _format_float(hypothesis.integration.height_raw),
        "prominence": _format_optional_float(hypothesis.evidence.prominence),
        "area_raw_counts_seconds": _format_float(
            hypothesis.integration.area_raw_counts_seconds,
            digits=2,
        ),
        "area_baseline_corrected": _format_optional_float(
            hypothesis.integration.area_baseline_corrected
        ),
        "area_ms1_morphology": _format_optional_float(
            hypothesis.integration.area_ms1_morphology
        ),
        "ms1_morphology_area_source": hypothesis.integration.ms1_morphology_area_source,
        "ms1_morphology_trace_method": (
            hypothesis.integration.ms1_morphology_trace_method
        ),
        "ms1_morphology_trace_window_points": _format_optional_int(
            hypothesis.integration.ms1_morphology_trace_window_points
        ),
        "ms1_morphology_trace_effective_points": _format_optional_int(
            hypothesis.integration.ms1_morphology_trace_effective_points
        ),
        "area_uncertainty": _format_optional_float(
            hypothesis.integration.area_uncertainty
        ),
        "area_uncertainty_formula_version": (
            hypothesis.integration.area_uncertainty_formula_version
        ),
        "baseline_residual_mad": _format_optional_float(
            hypothesis.integration.baseline_residual_mad
        ),
        "area_uncertainty_noise_source": (
            hypothesis.integration.area_uncertainty_noise_source
        ),
        "quality_flags": _join(hypothesis.evidence.quality_flags),
        "region_scan_count": _format_optional_int(
            hypothesis.evidence.region_scan_count
        ),
        "region_duration_min": _format_optional_float(
            hypothesis.evidence.region_duration_min
        ),
        "region_edge_ratio": _format_optional_float(
            hypothesis.evidence.region_edge_ratio
        ),
        "region_trace_continuity": _format_optional_float(
            hypothesis.evidence.region_trace_continuity
        ),
        "ms2_present": _format_optional_bool(hypothesis.evidence.ms2_present),
        "nl_match": _format_optional_bool(hypothesis.evidence.nl_match),
        "ms2_trace_strength": hypothesis.evidence.ms2_trace_strength,
        "rt_prior_min": _format_optional_float(hypothesis.evidence.rt_prior_min),
        "rt_prior_sigma": "",
        "confidence": hypothesis.evidence.confidence,
        "raw_score": _format_optional_int(hypothesis.evidence.raw_score),
        "support_labels": _join(hypothesis.evidence.support_labels),
        "concern_labels": _join(hypothesis.evidence.concern_labels),
        "cap_labels": _join(hypothesis.evidence.cap_labels),
        "reason": hypothesis.evidence.reason,
        "selected": "TRUE" if hypothesis.audit.selected else "FALSE",
        "selection_rank": _format_optional_int(hypothesis.audit.selection_rank),
        "selection_reference_rt_min": _format_optional_float(
            hypothesis.audit.selection_reference_rt_min
        ),
        "rejection_reason": hypothesis.audit.rejection_reason,
        "nl_status": hypothesis.evidence.nl_status,
        "best_loss_ppm": _format_optional_float(hypothesis.evidence.best_loss_ppm),
        "best_ms2_scan_rt_min": _format_optional_float(
            hypothesis.evidence.best_ms2_scan_rt_min
        ),
        "apex_ms2_delta_min": _format_optional_float(
            hypothesis.evidence.apex_ms2_delta_min
        ),
        "best_product_base_ratio": _format_optional_float(
            hypothesis.evidence.best_product_base_ratio
        ),
        "trigger_scan_count": _format_optional_int(
            hypothesis.evidence.trigger_scan_count
        ),
        "strict_nl_scan_count": _format_optional_int(
            hypothesis.evidence.strict_nl_scan_count
        ),
        "ms1_peak_group_source": hypothesis.evidence.ms1_peak_group_source,
        "ms1_peak_group_rt_min": _format_optional_float(
            hypothesis.evidence.ms1_peak_group_rt_min
        ),
        "ms1_peak_group_rt_max": _format_optional_float(
            hypothesis.evidence.ms1_peak_group_rt_max
        ),
        "ms1_peak_group_trigger_scan_count": _format_optional_int(
            hypothesis.evidence.ms1_peak_group_trigger_scan_count
        ),
        "ms1_peak_group_strict_nl_scan_count": _format_optional_int(
            hypothesis.evidence.ms1_peak_group_strict_nl_scan_count
        ),
        "ms1_peak_group_strict_nl_event_count": _format_optional_int(
            hypothesis.evidence.ms1_peak_group_strict_nl_event_count
        ),
        "outside_ms1_peak_group_trigger_scan_count": _format_optional_int(
            hypothesis.evidence.outside_ms1_peak_group_trigger_scan_count
        ),
        "outside_ms1_peak_group_strict_nl_scan_count": _format_optional_int(
            hypothesis.evidence.outside_ms1_peak_group_strict_nl_scan_count
        ),
        "ms2_alignment_source": hypothesis.evidence.ms2_alignment_source,
        "diagnostic_product_absence_reason": (
            hypothesis.evidence.diagnostic_product_absence_reason
        ),
        "nearest_product_loss_ppm": _format_optional_float(
            hypothesis.evidence.nearest_product_loss_ppm
        ),
        "nearest_product_base_ratio": _format_optional_float(
            hypothesis.evidence.nearest_product_base_ratio
        ),
        "nearest_product_mz": _format_optional_float(
            hypothesis.evidence.nearest_product_mz
        ),
        "safe_merge_promotion_source": (
            hypothesis.audit.safe_merge_promotion_source
        ),
        "safe_merge_promotion_shadow_boundary_id": (
            hypothesis.audit.safe_merge_promotion_shadow_boundary_id
        ),
        "safe_merge_promotion_area_ratio": _format_optional_float(
            hypothesis.audit.safe_merge_promotion_area_ratio
        ),
        "safe_merge_promotion_selected_interval_count": _format_optional_int(
            hypothesis.audit.safe_merge_promotion_selected_interval_count
        ),
        "safe_merge_promotion_selected_interval_gap_max_min": _format_optional_float(
            hypothesis.audit.safe_merge_promotion_selected_interval_gap_max_min
        ),
        "safe_merge_rejection_reason": hypothesis.audit.safe_merge_rejection_reason,
    }


def append_peak_candidate_rows(
    rows: list[PeakCandidateTableRow] | None,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate_ms2_builder: Callable[[PeakCandidate], CandidateMS2Evidence | None],
    *,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
    audit_peak_result: PeakDetectionResult | None = None,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext] | None = None,
    istd_confidence_note: str | None = None,
) -> None:
    if not config.emit_peak_candidates or rows is None:
        return
    append_peak_candidate_rows_from_hypotheses(
        rows,
        config,
        sample_name,
        build_peak_candidate_audit_hypotheses(
            config=config,
            sample_name=sample_name,
            target=target,
            peak_result=peak_result,
            candidate_ms2_builder=candidate_ms2_builder,
            rt=rt,
            intensity=intensity,
            trace_group=trace_group,
            audit_peak_result=audit_peak_result,
            scoring_context_builder=scoring_context_builder,
            istd_confidence_note=istd_confidence_note,
        ),
    )


def append_peak_candidate_rows_from_hypotheses(
    rows: list[PeakCandidateTableRow] | None,
    config: ExtractionConfig,
    sample_name: str,
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    group: str | None = None,
) -> None:
    if not config.emit_peak_candidates or rows is None:
        return
    rows.extend(
        build_peak_candidate_rows_from_hypotheses(
            sample_name=sample_name,
            group=group,
            hypotheses=hypotheses,
        )
    )


def _with_rescored_audit_candidates(
    audit_peak_result: PeakDetectionResult,
    original_peak_result: PeakDetectionResult,
    scoring_context_builder: Callable[[PeakCandidate], ScoringContext],
    *,
    istd_confidence_note: str | None,
) -> PeakDetectionResult:
    original_scores = {
        _candidate_score_key(score.candidate): score
        for score in original_peak_result.candidate_scores
    }
    candidate_scores: list[PeakCandidateScore] = []
    for candidate in audit_peak_result.candidates:
        original_score = original_scores.get(_candidate_score_key(candidate))
        if original_score is not None and not _needs_audit_rescore(candidate):
            candidate_scores.append(replace(original_score, candidate=candidate))
            continue
        context = scoring_context_builder(candidate)
        candidate_scores.append(
            _candidate_score_summary(
                score_candidate(
                    candidate,
                    context,
                    prior_rt=context.rt_prior,
                    istd_confidence_note=istd_confidence_note,
                )
            )
        )
    return replace(
        audit_peak_result,
        candidate_scores=tuple(candidate_scores),
    )


def _candidate_score_key(candidate: PeakCandidate) -> tuple[float, float, float]:
    return (
        candidate.selection_apex_rt,
        candidate.peak.peak_start,
        candidate.peak.peak_end,
    )


def _needs_audit_rescore(candidate: PeakCandidate) -> bool:
    sources = {str(source) for source in candidate.proposal_sources}
    return "centwave_cwt" in sources and bool(sources.difference({"centwave_cwt"}))


def _candidate_score_summary(scored_candidate: ScoredCandidate) -> PeakCandidateScore:
    evidence_score = scored_candidate.evidence_score
    return PeakCandidateScore(
        candidate=scored_candidate.candidate,
        confidence=scored_candidate.confidence.value,
        reason=scored_candidate.reason,
        raw_score=evidence_score.raw_score if evidence_score is not None else None,
        support_labels=(
            evidence_score.support_labels if evidence_score is not None else ()
        ),
        concern_labels=(
            evidence_score.concern_labels if evidence_score is not None else ()
        ),
        cap_labels=evidence_score.cap_labels if evidence_score is not None else (),
        prior_rt=scored_candidate.prior_rt,
        quality_penalty=scored_candidate.quality_penalty,
        selection_quality_penalty=scored_candidate.selection_quality_penalty,
        severities=scored_candidate.severities,
    )


def _candidate_table_ms2_evidence(
    peak_result: PeakDetectionResult,
    candidate_ms2_builder: Callable[[PeakCandidate], CandidateMS2Evidence | None],
) -> dict[PeakCandidate, CandidateMS2Evidence]:
    evidence_by_candidate: dict[PeakCandidate, CandidateMS2Evidence] = {}
    for candidate in peak_result.candidates:
        evidence = candidate_ms2_builder(candidate)
        if evidence is not None:
            evidence_by_candidate[candidate] = evidence
    return evidence_by_candidate


def _format_float(value: float, *, digits: int = 5) -> str:
    return f"{value:.{digits}f}"


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return ""
    return _format_float(value)


def _format_optional_int(value: int | None) -> str:
    if value is None:
        return ""
    return str(value)


def _format_optional_bool(value: bool | None) -> str:
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def _join(values: tuple[str, ...]) -> str:
    return ";".join(values)
