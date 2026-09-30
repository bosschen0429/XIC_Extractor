"""Select the Targeted product peak from hypothesis and approval evidence."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.evidence.neutral_loss import CandidateMS2Evidence
from xic_extractor.extraction.ms2_selection import selected_candidate_ms2_evidence
from xic_extractor.extraction.scoring_factory import selected_candidate
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses as build_peak_hypotheses,
)
from xic_extractor.extraction.trace_context import targeted_extraction_trace_group
from xic_extractor.peak_detection.baseline import BaselineMethod
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecords,
    PeakModelSelectionResult,
    expected_diff_approval_for_legacy_selection,
    expected_diff_approval_for_result,
    model_select_peak_hypothesis,
)
from xic_extractor.peak_detection.models import PeakDetectionResult
from xic_extractor.peak_detection.ms1_morphology import (
    configured_morphology_window_points,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.traces import TraceGroup
from xic_extractor.signal_processing import PeakCandidate
from xic_extractor.target_sample_applicability import (
    TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING,
    target_sample_is_applicable,
)


@dataclass(frozen=True)
class TargetedPeakSelection:
    candidate_ms2_evidence: CandidateMS2Evidence | None
    selected_hypothesis: PeakHypothesis | None
    selection_decision: PeakHypothesisSelectionDecision | None
    trace_group: TraceGroup | None
    model_selection_result: PeakModelSelectionResult | None = None


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
    sample_name: str,
    target: Target,
    peak_result: PeakDetectionResult,
    candidate: PeakCandidate | None,
    candidate_ms2_cache: dict[PeakCandidate, CandidateMS2Evidence],
    candidate_ms2_builder: Callable[[PeakCandidate], CandidateMS2Evidence | None],
    rt: object,
    intensity: object,
    rt_min: float,
    rt_max: float,
    expected_rt_min: float | None,
    paired_istd_anchor_rt: float | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
) -> TargetedPeakSelection:
    trace_group = targeted_extraction_trace_group(
        sample_name=sample_name,
        target=target,
        config=config,
        rt=rt,
        intensity=intensity,
        rt_min=rt_min,
        rt_max=rt_max,
        expected_rt_min=expected_rt_min,
    )
    candidate_ms2_evidence = selected_candidate_ms2_evidence(
        candidate,
        candidate_ms2_cache,
        candidate_ms2_builder,
    )
    hypotheses = build_production_peak_hypotheses(
        config=config,
        sample_name=sample_name,
        target=target,
        peak_result=peak_result,
        selected_candidate_ms2_evidence=candidate_ms2_evidence,
        rt=rt,
        intensity=intensity,
        trace_group=trace_group,
    )
    model_selection_result = _model_selection_result_for_handoff(
        hypotheses,
        sample_name=sample_name,
        target_label=target.label,
        expected_diff_approvals=model_selection_expected_diff_approvals,
    )
    model_selection_result = _targeted_product_switch_policy(
        model_selection_result,
        hypotheses=hypotheses,
        sample_name=sample_name,
        target=target,
        paired_istd_anchor_rt=paired_istd_anchor_rt,
    )
    legacy_selected_hypothesis = selected_peak_hypothesis(hypotheses)
    selected_hypothesis = _product_selected_hypothesis(
        hypotheses,
        model_selection_result,
        legacy_selected_hypothesis,
    )
    return TargetedPeakSelection(
        candidate_ms2_evidence=candidate_ms2_evidence,
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
        model_selection_result=model_selection_result,
    )


def _model_selection_result_for_handoff(
    hypotheses: tuple[PeakHypothesis, ...],
    *,
    sample_name: str,
    target_label: str,
    expected_diff_approvals: ExpectedDiffApprovalRecords | None,
) -> PeakModelSelectionResult | None:
    if not hypotheses:
        return None
    shadow_result = model_select_peak_hypothesis(hypotheses)
    approval = expected_diff_approval_for_result(
        shadow_result,
        expected_diff_approvals,
        sample_name=sample_name,
        target_label=target_label,
    )
    if approval is None:
        approval = expected_diff_approval_for_legacy_selection(
            shadow_result,
            expected_diff_approvals,
            sample_name=sample_name,
            target_label=target_label,
        )
    if approval is None:
        return shadow_result
    return model_select_peak_hypothesis(
        hypotheses,
        successor_selected_candidate_id=approval.successor_selected_candidate_id,
        expected_diff_approval=approval,
    )


def _targeted_product_switch_policy(
    model_selection_result: PeakModelSelectionResult | None,
    *,
    hypotheses: tuple[PeakHypothesis, ...],
    sample_name: str,
    target: Target,
    paired_istd_anchor_rt: float | None,
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
            evidence_sources=tuple(
                dict.fromkeys(
                    (
                        *model_selection_result.evidence_sources,
                        "target_applicable_strict_nl_successor",
                    )
                )
            ),
        )
    if (
        model_selection_result is None
        or not model_selection_result.product_switch_allowed
        or model_selection_result.selection_status != "expected_diff"
    ):
        return model_selection_result
    if target.is_istd:
        return model_selection_result
    if target.istd_pair and _finite_anchor_rt(paired_istd_anchor_rt):
        return model_selection_result
    reason = (
        "paired_istd_not_credible_in_sample"
        if target.istd_pair
        else "target_role_not_auto_reselection_eligible"
    )
    return replace(
        model_selection_result,
        selection_status="blocked_diff",
        product_switch_allowed=False,
        diff_reasons=tuple(
            dict.fromkeys(
                (
                    *model_selection_result.diff_reasons,
                    reason,
                )
            )
        ),
    )


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


def _finite_anchor_rt(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


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
                    confidence=peak_result.confidence
                    or hypothesis.evidence.confidence,
                    reason=peak_result.reason or hypothesis.evidence.reason,
                ),
            )
        )
    return tuple(updated)
