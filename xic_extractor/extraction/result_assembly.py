from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.models import Target
from xic_extractor.evidence.neutral_loss import (
    CandidateMS2Evidence,
    MS2ProductEvidence,
    NLResult,
)
from xic_extractor.extraction.targeted_product_projection import (
    project_targeted_result,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlEvaluationReceipt,
    CidNlMethodRelations,
)
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.models import PeakCandidate, PeakDetectionResult
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.traces import Trace

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult


def build_extraction_result(
    *,
    peak_result: PeakDetectionResult,
    nl_result: NLResult | None,
    candidate_ms2_evidence: CandidateMS2Evidence | None,
    target: Target,
    candidate: PeakCandidate | None,
    scoring_context_builder: Any | None,
    selected_hypothesis: PeakHypothesis | None = None,
    selection_decision: PeakHypothesisSelectionDecision | None = None,
    acquisition_trace: Trace | None = None,
    physical_evaluation: PhysicalPeakEvaluation | None = None,
    candidate_physical_evaluations: tuple[PhysicalPeakEvaluation, ...] = (),
    cid_nl_products: tuple[MS2ProductEvidence, ...] = (),
    cid_nl_evaluation_receipt: CidNlEvaluationReceipt | None = None,
    cid_nl_relations: CidNlMethodRelations | None = None,
    cid_nl_candidate_evidence: tuple[CandidateMS2Evidence, ...] = (),
    selected_cid_nl_candidate_index: int | None = None,
    sample_name: str = "",
) -> ExtractionResult:
    if selected_hypothesis is not None:
        validate_peak_hypothesis_selection_authority(
            selected_hypothesis,
            consumer="build_extraction_result",
        )
    from xic_extractor.extraction import result_models

    resolved_selection_decision = _result_selection_decision(
        peak_result,
        selected_hypothesis,
        selection_decision,
    )
    quality_penalty = 0
    quality_flags: tuple[str, ...] = ()
    if candidate is not None:
        quality_penalty, _ = candidate_quality_penalty(candidate)
        quality_flags = tuple(
            str(flag) for flag in getattr(candidate, "quality_flags", ())
        )

    result = result_models.ExtractionResult(
        peak_result=peak_result,
        nl=nl_result,
        candidate_ms2_evidence=candidate_ms2_evidence,
        target_label=_result_target_label(target, selected_hypothesis),
        role=_result_role(target, selected_hypothesis),
        istd_pair=_result_istd_pair(target, selected_hypothesis),
        confidence=_result_confidence(peak_result, resolved_selection_decision),
        reason=_result_reason(peak_result, resolved_selection_decision),
        severities=peak_result.severities,
        prior_rt=getattr(scoring_context_builder, "rt_prior", None),
        prior_source=getattr(scoring_context_builder, "prior_source", ""),
        quality_penalty=quality_penalty,
        quality_flags=_result_quality_flags(quality_flags, selected_hypothesis),
        score_breakdown=peak_result.score_breakdown,
        selected_hypothesis=selected_hypothesis,
        selection_decision=resolved_selection_decision,
        acquisition_trace=acquisition_trace,
        physical_evaluation=physical_evaluation,
        candidate_physical_evaluations=candidate_physical_evaluations,
        cid_nl_products=cid_nl_products,
        cid_nl_evaluation_receipt=cid_nl_evaluation_receipt,
        cid_nl_relations=cid_nl_relations,
        cid_nl_candidate_evidence=cid_nl_candidate_evidence,
        selected_cid_nl_candidate_index=selected_cid_nl_candidate_index,
    )
    return replace(
        result,
        targeted_product_projection=project_targeted_result(
            result,
            target=target,
            sample_name=sample_name,
        ),
    )


def reproject_extraction_result(
    result: ExtractionResult,
    *,
    target: Target,
    sample_name: str,
    selection_decision: PeakHypothesisSelectionDecision | None = None,
) -> ExtractionResult:
    resolved_selection_decision = (
        selection_decision
        if selection_decision is not None
        else result.selection_decision
    )
    updated = replace(
        result,
        selection_decision=resolved_selection_decision,
        confidence=_result_confidence(
            result.peak_result,
            resolved_selection_decision,
        ),
        reason=_result_reason(
            result.peak_result,
            resolved_selection_decision,
        ),
    )
    return replace(
        updated,
        targeted_product_projection=project_targeted_result(
            updated,
            target=target,
            sample_name=sample_name,
        ),
    )


def _result_target_label(
    target: Target,
    selected_hypothesis: PeakHypothesis | None,
) -> str:
    if selected_hypothesis is not None:
        return selected_hypothesis.target_label
    return target.label


def _result_role(
    target: Target,
    selected_hypothesis: PeakHypothesis | None,
) -> str:
    if selected_hypothesis is not None:
        return selected_hypothesis.role
    return "ISTD" if target.is_istd else "Analyte"


def _result_istd_pair(
    target: Target,
    selected_hypothesis: PeakHypothesis | None,
) -> str:
    if selected_hypothesis is not None:
        return selected_hypothesis.istd_pair
    return target.istd_pair


def _result_selection_decision(
    peak_result: PeakDetectionResult,
    selected_hypothesis: PeakHypothesis | None,
    selection_decision: PeakHypothesisSelectionDecision | None,
) -> PeakHypothesisSelectionDecision | None:
    if selection_decision is not None:
        return selection_decision
    if selected_hypothesis is None:
        return None
    return selection_decision_from_hypothesis(
        selected_hypothesis,
        peak_result=peak_result,
    )


def _result_confidence(
    peak_result: PeakDetectionResult,
    selection_decision: PeakHypothesisSelectionDecision | None,
) -> str:
    if selection_decision is not None:
        return selection_decision.projected_confidence
    if peak_result.peak is None:
        return ""
    return peak_result.confidence or "HIGH"


def _result_reason(
    peak_result: PeakDetectionResult,
    selection_decision: PeakHypothesisSelectionDecision | None,
) -> str:
    if selection_decision is not None:
        return selection_decision.projected_reason
    return peak_result.reason or ""


def _result_quality_flags(
    quality_flags: tuple[str, ...],
    selected_hypothesis: PeakHypothesis | None,
) -> tuple[str, ...]:
    if selected_hypothesis is not None:
        return selected_hypothesis.evidence.quality_flags
    return quality_flags
