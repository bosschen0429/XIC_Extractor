"""Complete Targeted selection against the shared physical peak owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

import numpy as np

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.hypotheses import (
    CandidateMS2EvidenceMap,
    PeakHypothesis,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakDetectionResult
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


def build_production_peak_hypotheses(
    *,
    config: ExtractionConfig,
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate_ms2_evidence: CandidateMS2EvidenceMap | None = None,
    rt: object | None = None,
    intensity: object | None = None,
    trace_group: TraceGroup | None = None,
) -> tuple[PeakHypothesis, ...]:
    hypotheses = build_peak_hypotheses(
        sample_name=sample_name,
        target_label=target.label,
        role="ISTD" if target.is_istd else "Analyte",
        istd_pair=target.istd_pair,
        resolver_mode=config.resolver_mode,
        peak_result=peak_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
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
    candidate_ms2_evidence: CandidateMS2EvidenceMap | None,
    prepared_result: PreparedSignalResult,
    universe: PhysicalSegmentUniverse,
    selection_rt_min: float,
    selection_rt_max: float,
    candidate_evaluations: Mapping[PeakCandidate, PhysicalPeakEvaluation] | None = None,
) -> TargetedPeakSelection:
    """Return selection facts synced with physical availability and integration."""
    trace = trace_group.primary_trace
    hypotheses = build_production_peak_hypotheses(
        config=config,
        sample_name=trace_group.sample_name,
        target=target,
        peak_result=peak_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
        rt=np.asarray(trace.rt, dtype=float),
        intensity=np.asarray(trace.intensity, dtype=float),
        trace_group=trace_group,
    )
    selected_hypothesis = selected_peak_hypothesis(hypotheses)
    candidate = selected_candidate(peak_result)
    selected_ms2_evidence = (
        candidate_ms2_evidence.get(candidate)
        if candidate is not None and candidate_ms2_evidence is not None
        else None
    )
    selected_segment = selected_physical_segment(candidate, universe)
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
    physical_evaluation = (
        candidate_evaluations[candidate]
        if candidate is not None and candidate_evaluations is not None
        else answer_physical_peak_question(prepared_result, universe, question)
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
        selected_ms2_evidence = None
        selected_hypothesis = None
    elif (
        selected_hypothesis is not None and physical_evaluation.integration is not None
    ):
        selected_hypothesis = replace(
            selected_hypothesis,
            integration=physical_peak_observation_integration(physical_evaluation),
        )
    return TargetedPeakSelection(
        peak_result=peak_result,
        physical_evaluation=physical_evaluation,
        candidate_ms2_evidence=selected_ms2_evidence,
        selected_hypothesis=selected_hypothesis,
        selection_decision=(
            selection_decision_from_hypothesis(
                selected_hypothesis,
                peak_result=peak_result,
            )
            if selected_hypothesis is not None
            else None
        ),
        trace_group=trace_group,
    )


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
