"""Complete Targeted selection against the shared physical peak owner."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import cast

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.extraction.target_sample_applicability import (
    TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING,
    target_sample_is_applicable,
)
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.model_selection import (
    PeakModelSelectionResult,
    model_select_peak_hypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.peak_detection.ms1_morphology import (
    configured_morphology_window_points,
)
from xic_extractor.peak_detection.observations import (
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.physical_peak_candidate_adapter import (
    selected_physical_segment,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    answer_physical_peak_question,
    mint_physical_peak_question,
)
from xic_extractor.peak_detection.physical_peak_models import (
    PHYSICAL_PEAK_NO_SELECTION_POLICY,
    PHYSICAL_PEAK_POINT_POLICY,
    PhysicalPeakEvaluation,
    PhysicalSegmentUniverse,
    PreparedSignalResult,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.traces import TraceGroup


@dataclass(frozen=True)
class TargetedPeakSelection:
    peak_result: PeakDetectionResult
    physical_evaluation: PhysicalPeakEvaluation
    candidate_ms2_evidence: CandidateMS2Evidence | None
    selected_hypothesis: PeakHypothesis | None
    selection_decision: PeakHypothesisSelectionDecision | None
    trace_group: TraceGroup
    model_selection_result: PeakModelSelectionResult | None = None


@dataclass(frozen=True)
class _TargetedHypothesisSelection:
    selected_hypothesis: PeakHypothesis | None
    selection_decision: PeakHypothesisSelectionDecision | None
    model_selection_result: PeakModelSelectionResult | None


def build_production_peak_hypotheses(
    *,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    selected_candidate_ms2_evidence: CandidateMS2Evidence | None = None,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
) -> tuple[PeakHypothesis, ...]:
    candidate = selected_candidate(peak_result)
    evidence_by_candidate = (
        {candidate: selected_candidate_ms2_evidence}
        if candidate is not None and selected_candidate_ms2_evidence is not None
        else None
    )
    hypotheses = build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        candidate_ms2_evidence=evidence_by_candidate,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
        baseline_integration_method=cast(
            BaselineMethod,
            getattr(config, "baseline_integration_method", "asls"),
        ),
        count_no_ms2_as_detected=getattr(
            config,
            "count_no_ms2_as_detected",
            False,
        ),
        ms1_morphology_smoothing_window_points=(
            configured_morphology_window_points(config)
        ),
    )
    return _with_final_selected_result_evidence(hypotheses, peak_result)


def selected_peak_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.audit.selected:
            return hypothesis
    return None


def select_targeted_peak(
    *,
    config: ExtractionConfig,
    target: Target,
    peak_result: PeakDetectionResult,
    trace_group: TraceGroup,
    candidate_ms2_evidence: CandidateMS2Evidence | None,
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    selection_rt_min: float,
    selection_rt_max: float,
) -> TargetedPeakSelection:
    """Return selection facts synced with physical availability and integration."""
    hypothesis_selection = _select_targeted_hypothesis(
        config=config,
        target=target,
        peak_result=peak_result,
        trace_group=trace_group,
        candidate_ms2_evidence=candidate_ms2_evidence,
    )
    selected_segment = selected_physical_segment(
        selected_candidate(peak_result), universe
    )
    if selected_segment is not None:
        query_rt_min = selected_segment.apex_rt_min
    elif universe.state != "available":
        acquisition_trace = trace_group.primary_trace
        query_rt_min = (acquisition_trace.rt_min + acquisition_trace.rt_max) / 2
    else:
        # A wide context may contain segments outside the selection window.
        query_rt_min = (selection_rt_min + selection_rt_max) / 2
    question = mint_physical_peak_question(
        prepared_result.projection,
        universe,
        selection_subject_id=target.label,
        query_rt_min=query_rt_min,
        selection_policy_version=(
            PHYSICAL_PEAK_POINT_POLICY
            if selected_segment is not None
            else PHYSICAL_PEAK_NO_SELECTION_POLICY
        ),
    )
    physical_evaluation = answer_physical_peak_question(
        prepared_result, universe, question
    )
    if physical_evaluation.state != "available":
        # The final physical owner withdraws the ranked value and selection authority.
        peak_result = replace(
            peak_result,
            status=(
                "NO_SIGNAL"
                if physical_evaluation.reason == "no_signal_above_baseline"
                else "PEAK_NOT_FOUND"
            ),
            peak=None,
            candidates=(),
            n_prominent_peaks=0,
            confidence=None,
            reason=physical_evaluation.reason,
            severities=(),
            score_breakdown=(),
            candidate_scores=(),
        )
        candidate_ms2_evidence = None
        hypothesis_selection = _TargetedHypothesisSelection(None, None, None)
    elif (
        hypothesis_selection.selected_hypothesis is not None
        and physical_evaluation.integration is not None
    ):
        physical_hypothesis = replace(
            hypothesis_selection.selected_hypothesis,
            integration=physical_peak_observation_integration(physical_evaluation),
        )
        hypothesis_selection = replace(
            hypothesis_selection,
            selected_hypothesis=physical_hypothesis,
            selection_decision=selection_decision_from_hypothesis(
                physical_hypothesis,
                peak_result=peak_result,
            ),
        )
    return TargetedPeakSelection(
        peak_result=peak_result,
        physical_evaluation=physical_evaluation,
        candidate_ms2_evidence=candidate_ms2_evidence,
        selected_hypothesis=hypothesis_selection.selected_hypothesis,
        selection_decision=hypothesis_selection.selection_decision,
        trace_group=trace_group,
        model_selection_result=hypothesis_selection.model_selection_result,
    )


def _select_targeted_hypothesis(
    *,
    config: ExtractionConfig,
    target: Target,
    peak_result: PeakDetectionResult,
    trace_group: TraceGroup,
    candidate_ms2_evidence: CandidateMS2Evidence | None,
) -> _TargetedHypothesisSelection:
    """Apply the Targeted role policy to the ranked peak hypotheses."""
    sample_name = trace_group.sample_name
    trace = trace_group.primary_trace
    hypotheses = build_production_peak_hypotheses(
        config=config,
        sample_name=sample_name,
        target=target,
        peak_result=peak_result,
        selected_candidate_ms2_evidence=candidate_ms2_evidence,
        rt=np.asarray(trace.rt, dtype=float),
        intensity=np.asarray(trace.intensity, dtype=float),
        trace_group=trace_group,
    )
    model_selection_result = (
        model_select_peak_hypothesis(hypotheses) if hypotheses else None
    )
    model_selection_result = _targeted_product_switch_policy(
        model_selection_result,
        hypotheses=hypotheses,
        sample_name=sample_name,
        target=target,
    )
    legacy_selected_hypothesis = selected_peak_hypothesis(hypotheses)
    selected_hypothesis = _product_selected_hypothesis(
        hypotheses,
        model_selection_result,
        legacy_selected_hypothesis,
    )
    return _TargetedHypothesisSelection(
        selected_hypothesis=selected_hypothesis,
        selection_decision=(
            selection_decision_from_hypothesis(
                selected_hypothesis,
                peak_result=peak_result,
            )
            if selected_hypothesis is not None
            else None
        ),
        model_selection_result=model_selection_result,
    )


def _targeted_product_switch_policy(
    model_selection_result: PeakModelSelectionResult | None,
    *,
    hypotheses: tuple[PeakHypothesis, ...],
    sample_name: str,
    target: Target,
) -> PeakModelSelectionResult | None:
    if _target_applicable_strict_nl_successor_switch(
        model_selection_result,
        hypotheses=hypotheses,
        sample_name=sample_name,
        target=target,
    ):
        assert model_selection_result is not None
        return replace(
            model_selection_result,
            product_switch_allowed=True,
            diff_reasons=tuple(
                reason
                for reason in model_selection_result.diff_reasons
                if reason != "successor_selection_not_adopted"
            ),
            evidence_sources=tuple(
                dict.fromkeys(
                    (
                        *model_selection_result.evidence_sources,
                        "target_applicable_strict_nl_successor",
                    )
                )
            ),
        )
    return model_selection_result


def _target_applicable_strict_nl_successor_switch(
    model_selection_result: PeakModelSelectionResult | None,
    *,
    hypotheses: tuple[PeakHypothesis, ...],
    sample_name: str,
    target: Target,
) -> bool:
    if (
        model_selection_result is None
        or model_selection_result.product_switch_allowed
        or model_selection_result.selection_status != "expected_diff"
        or target.is_istd
        or getattr(target, "sample_applicability", "all")
        != TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING
        or not target_sample_is_applicable(target, sample_name)
    ):
        return False
    selected = _hypothesis_by_id(
        hypotheses,
        model_selection_result.selected_candidate_id,
    )
    if selected is None:
        return False
    if (
        not math.isfinite(selected.integration.rt_apex_min)
        or _active_integration_area(selected.integration) <= 0
    ):
        return False
    reasons = _hypothesis_reason_set(selected)
    return "strict_nl_ok" in reasons or "candidate_aligned_ms2_nl" in reasons


def _active_integration_area(integration: object) -> float:
    morphology_area = getattr(integration, "area_ms1_morphology", None)
    if morphology_area is not None:
        return float(morphology_area)
    return float(getattr(integration, "area_raw_counts_seconds", 0.0))


def _hypothesis_by_id(
    hypotheses: tuple[PeakHypothesis, ...],
    hypothesis_id: str,
) -> PeakHypothesis | None:
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id == hypothesis_id:
            return hypothesis
    return None


def _hypothesis_reason_set(hypothesis: PeakHypothesis) -> frozenset[str]:
    semantics = hypothesis.evidence.decision_semantics
    reasons: list[str] = [
        *hypothesis.evidence.support_labels,
        *hypothesis.evidence.concern_labels,
        *hypothesis.evidence.cap_labels,
    ]
    if semantics is not None:
        reasons.extend(semantics.support_reasons)
        reasons.extend(semantics.review_reasons)
        reasons.extend(semantics.conflict_reasons)
        reasons.extend(semantics.not_counted_reasons)
        reasons.extend(semantics.exclusion_reasons)
    return frozenset(reason for reason in reasons if reason)


def _product_selected_hypothesis(
    hypotheses: tuple[PeakHypothesis, ...],
    model_selection_result: PeakModelSelectionResult | None,
    legacy_selected_hypothesis: PeakHypothesis | None,
) -> PeakHypothesis | None:
    if (
        model_selection_result is None
        or not model_selection_result.product_switch_allowed
    ):
        return legacy_selected_hypothesis
    for hypothesis in hypotheses:
        if hypothesis.hypothesis_id == model_selection_result.selected_candidate_id:
            return hypothesis
    return legacy_selected_hypothesis


def _with_final_selected_result_evidence(
    hypotheses: tuple[PeakHypothesis, ...],
    peak_result: PeakDetectionResult,
) -> tuple[PeakHypothesis, ...]:
    if peak_result.confidence is None and not peak_result.reason:
        return hypotheses
    updated: list[PeakHypothesis] = []
    for hypothesis in hypotheses:
        if not hypothesis.audit.selected:
            updated.append(hypothesis)
            continue
        updated.append(
            replace(
                hypothesis,
                evidence=replace(
                    hypothesis.evidence,
                    confidence=peak_result.confidence or hypothesis.evidence.confidence,
                    reason=peak_result.reason or hypothesis.evidence.reason,
                ),
            )
        )
    return tuple(updated)
