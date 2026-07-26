from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from xic_extractor.config import Target
from xic_extractor.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.extraction.targeted_projection_reasons import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
)
from xic_extractor.neutral_loss import CandidateMS2Evidence, NLResult
from xic_extractor.peak_detection.hypotheses import (
    PeakHypothesis,
    validate_peak_hypothesis_selection_authority,
)
from xic_extractor.peak_detection.model_selection import PeakModelSelectionResult
from xic_extractor.peak_detection.scoring_quality import (
    candidate_quality_penalty,
    hard_quality_flags,
)
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
    selection_decision_from_hypothesis,
)
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedPriorContext,
    TargetedProductProjection,
    build_targeted_product_projection,
)
from xic_extractor.signal_processing import PeakCandidate, PeakDetectionResult
from xic_extractor.target_sample_applicability import (
    TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING,
    target_sample_exclusion_reasons,
    target_sample_is_applicable,
)

if TYPE_CHECKING:
    from xic_extractor.extractor import ExtractionResult


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
    model_selection_result: PeakModelSelectionResult | None = None,
    sample_name: str = "",
) -> ExtractionResult:
    if selected_hypothesis is not None:
        validate_peak_hypothesis_selection_authority(
            selected_hypothesis,
            consumer="build_extraction_result",
        )
    from xic_extractor import extractor

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

    result = extractor.ExtractionResult(
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
        model_selection_result=model_selection_result,
    )
    return replace(
        result,
        targeted_product_projection=_targeted_product_projection(
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
        targeted_product_projection=_targeted_product_projection(
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


def _targeted_product_projection(
    result: ExtractionResult,
    *,
    target: Target,
    sample_name: str,
) -> TargetedProductProjection:
    semantics = (
        result.selected_hypothesis.evidence.decision_semantics
        if result.selected_hypothesis is not None
        else None
    )
    selection_decision = result.selection_decision
    support = _projection_support_reasons(result, semantics, target=target)
    if selection_decision is not None:
        support = _merge_reasons(
            support,
            _selection_reason_overlay(
                selection_decision,
                semantics,
                "support_reasons",
            ),
        )
    conflicts = _projection_conflict_reasons(
        result,
        semantics,
        target=target,
        sample_name=sample_name,
        support_reasons=support,
    )
    if selection_decision is not None:
        conflicts = _merge_reasons(
            conflicts,
            _selection_reason_overlay(
                selection_decision,
                semantics,
                "conflict_reasons",
            ),
            _selection_reason_overlay(
                selection_decision,
                semantics,
                "ambiguity_reasons",
            ),
        )
    review = _projection_review_reasons(
        result,
        semantics,
        conflicts,
        target=target,
        sample_name=sample_name,
        support_reasons=support,
    )
    if selection_decision is not None:
        review = _merge_reasons(
            review,
            _selection_reason_overlay(
                selection_decision,
                semantics,
                "review_reasons",
            ),
        )
    not_counted = _projection_not_counted_reasons(
        result,
        semantics,
        review,
        conflicts,
        target=target,
        support_reasons=support,
    )
    if selection_decision is not None:
        not_counted = _merge_reasons(
            not_counted,
            _typed_not_counted_reasons(
                _selection_reason_overlay(
                    selection_decision,
                    semantics,
                    "not_counted_reasons",
                )
            ),
        )
    exclusions = target_sample_exclusion_reasons(target, sample_name)
    if selection_decision is not None:
        exclusions = _merge_reasons(
            exclusions,
            _selection_reason_overlay(
                selection_decision,
                semantics,
                "exclusion_reasons",
            ),
        )
    nl_status = result.nl_token or ""
    return build_targeted_product_projection(
        TargetedPriorContext(
            role=result.role or ("ISTD" if target.is_istd else "Analyte"),
            expected_present=target.is_istd,
            target_label=result.target_label or target.label,
            istd_pair=result.istd_pair or target.istd_pair,
        ),
        rt=result.reported_rt,
        area=result.reported_peak_area,
        confidence=result.confidence,
        nl_status=nl_status,
        support_reasons=support,
        review_reasons=review,
        conflict_reasons=conflicts,
        not_counted_reasons=not_counted,
        exclusion_reasons=exclusions,
        legacy_evidence={
            "confidence": result.confidence,
            "nl_status": nl_status,
            "reason": result.reason,
        },
    )


def _projection_support_reasons(
    result: ExtractionResult,
    semantics: EvidenceDecisionSemantics | None,
    *,
    target: Target,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if _positive(result.reported_rt) and _positive(result.reported_peak_area):
        reasons.append("ms1_peak_present")
    reasons.extend(_projection_support_context(result, semantics, target=target))
    if not hard_quality_flags(result.quality_flags):
        reasons.append("trace_coherent")
    return _unique(reasons)


def _projection_support_context(
    result: ExtractionResult,
    semantics: EvidenceDecisionSemantics | None,
    *,
    target: Target,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if semantics is not None:
        reasons.extend(semantics.support_reasons)
    reasons.extend(_approved_expected_diff_support_reasons(result, target=target))
    if _paired_analyte_has_anchor_supported_peak(result, target):
        reasons.append("role_aware_rt_support")
        reasons.append("paired_istd_anchor_support")
    return _unique(reasons)


def _approved_expected_diff_support_reasons(
    result: ExtractionResult,
    *,
    target: Target,
) -> tuple[str, ...]:
    model = result.model_selection_result
    if (
        model is None
        or model.selection_status != "expected_diff"
        or not model.product_switch_allowed
        or not _positive(result.reported_rt)
        or not _positive(result.reported_peak_area)
        or not _rt_inside_target_window(result.reported_rt, target)
        or result.quality_flags
    ):
        return ()
    evidence_sources = {source.strip().lower() for source in model.evidence_sources}
    reasons: list[str] = []
    if "role_aware_rt" in evidence_sources:
        reasons.append("role_aware_rt_support")
    if (
        result.role.upper() == "ANALYTE"
        and target.istd_pair
        and "paired_area_ratio" in evidence_sources
    ):
        reasons.append("paired_area_ratio_support")
    if (
        result.role.upper() == "ANALYTE"
        and target.istd_pair
        and "own_max_same_peak" in evidence_sources
    ):
        reasons.append(OWN_MAX_SAME_PEAK_SUPPORT_REASON)
    return tuple(reasons)


def _projection_conflict_reasons(
    result: ExtractionResult,
    semantics: EvidenceDecisionSemantics | None,
    *,
    target: Target,
    support_reasons: tuple[str, ...],
    sample_name: str = "",
) -> tuple[str, ...]:
    reasons: list[str] = []
    if semantics is not None:
        reasons.extend(
            reason
            for reason in semantics.conflict_reasons
            if not _downgrade_nl_conflict_to_istd_review(result, reason)
            and not _downgrade_nl_conflict_to_paired_analyte_review(
                result,
                target,
                reason,
                support_reasons,
            )
            and not _downgrade_rt_conflict_to_istd_review(
                result,
                target,
                reason,
                support_reasons,
            )
            and not _downgrade_rt_conflict_to_rna_containing_target_review(
                result,
                target,
                reason,
                support_reasons,
                sample_name,
            )
            and not _downgrade_trace_conflict_to_istd_review(
                result,
                target,
                reason,
                support_reasons,
            )
            and not _downgrade_trace_conflict_to_paired_analyte_review(
                result,
                target,
                reason,
                support_reasons,
            )
        )
        reasons.extend(semantics.ambiguity_reasons)
    for flag in hard_quality_flags(result.quality_flags):
        if not _downgrade_quality_flag_to_review(
            result,
            target,
            support_reasons,
        ):
            reasons.append(f"hard_quality_flag:{flag}")
    return _unique(reasons)


def _downgrade_nl_conflict_to_istd_review(
    result: ExtractionResult,
    reason: str,
) -> bool:
    return (
        reason == "candidate_aligned_ms2_nl_conflict"
        and result.role.upper() == "ISTD"
        and result.nl_token == "NL_FAIL"
        and result.candidate_ms2_evidence is not None
        and result.candidate_ms2_evidence.strict_nl_scan_count == 0
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
    )


def _downgrade_nl_conflict_to_paired_analyte_review(
    result: ExtractionResult,
    target: Target,
    reason: str,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        reason == "candidate_aligned_ms2_nl_conflict"
        and result.role.upper() == "ANALYTE"
        and _paired_analyte_has_nl_dropout_supported_peak(
            result,
            target,
            support_reasons,
        )
    )


def _downgrade_rt_conflict_to_istd_review(
    result: ExtractionResult,
    target: Target,
    reason: str,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        reason == "targeted_rt_conflict"
        and result.role.upper() == "ISTD"
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and _rt_inside_target_window(result.reported_rt, target)
        and (
            "candidate_aligned_ms2_nl" in support_reasons
            or (
                result.nl_token in {"NL_FAIL", "NO_MS2"}
                and "ms1_coherent" in support_reasons
                and not hard_quality_flags(result.quality_flags)
            )
        )
    )


def _downgrade_rt_conflict_to_rna_containing_target_review(
    result: ExtractionResult,
    target: Target,
    reason: str,
    support_reasons: tuple[str, ...],
    sample_name: str,
) -> bool:
    support = set(support_reasons)
    return (
        reason == "targeted_rt_conflict"
        and result.role.upper() == "ANALYTE"
        and getattr(target, "sample_applicability", "all")
        == TARGET_SAMPLE_APPLICABILITY_RNA_CONTAINING
        and target_sample_is_applicable(target, sample_name)
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and _rt_inside_target_window(result.reported_rt, target)
        and "candidate_aligned_ms2_nl" in support
        and "ms1_coherent" in support
    )


def _downgrade_trace_conflict_to_istd_review(
    result: ExtractionResult,
    target: Target,
    reason: str,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        reason in {"trace_morphology_conflict", "hard_local_quality_conflict"}
        and result.role.upper() == "ISTD"
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and "candidate_aligned_ms2_nl" in support_reasons
        and (
            "role_aware_rt_support" in support_reasons
            or _rt_inside_target_window(result.reported_rt, target)
        )
    )


def _downgrade_trace_conflict_to_paired_analyte_review(
    result: ExtractionResult,
    target: Target,
    reason: str,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        reason
        in {
            "trace_morphology_conflict",
            "hard_local_quality_conflict",
            "hard_quality_flag_conflict",
        }
        and (
            _paired_analyte_has_product_supported_peak(
                result,
                target,
                support_reasons,
            )
            or (
                reason == "trace_morphology_conflict"
                and _paired_analyte_has_anchor_supported_peak(result, target)
            )
        )
    )


def _downgrade_quality_flag_to_review(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    return _istd_has_product_supported_peak(
        result,
        target,
        support_reasons,
    ) or _paired_analyte_has_product_supported_peak(
        result,
        target,
        support_reasons,
    )


def _istd_has_product_supported_peak(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        result.role.upper() == "ISTD"
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and "candidate_aligned_ms2_nl" in support_reasons
        and (
            "role_aware_rt_support" in support_reasons
            or _rt_inside_target_window(result.reported_rt, target)
        )
    )


def _paired_analyte_has_product_supported_peak(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    return (
        result.role.upper() == "ANALYTE"
        and bool(target.istd_pair)
        and result.confidence.upper() != "VERY_LOW"
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and _rt_inside_target_window(result.reported_rt, target)
        and result.peak_result.selection_reference_rt is not None
        and "candidate_aligned_ms2_nl" in support_reasons
    )


def _paired_analyte_has_anchor_supported_peak(
    result: ExtractionResult,
    target: Target,
) -> bool:
    if (
        result.role.upper() != "ANALYTE"
        or not target.istd_pair
        or not _positive(result.reported_rt)
        or not _positive(result.reported_peak_area)
        or not _rt_inside_target_window(result.reported_rt, target)
        or result.quality_flags
    ):
        return False
    reference_rt = result.peak_result.paired_istd_anchor_rt
    if reference_rt is None or not math.isfinite(reference_rt):
        return False
    return _reported_interval_contains_rt(result, reference_rt)


def _paired_analyte_has_pair_supported_peak(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    return _paired_analyte_has_anchor_supported_peak(
        result,
        target,
    ) or _paired_analyte_has_role_or_ratio_supported_peak(
        result,
        target,
        support_reasons,
    )


def _paired_analyte_has_nl_dropout_supported_peak(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    support = set(support_reasons)
    return (
        _paired_analyte_has_role_or_ratio_supported_peak(
            result,
            target,
            support_reasons,
        )
        and "paired_area_ratio_support" in support
        and OWN_MAX_SAME_PEAK_SUPPORT_REASON in support
        and bool(
            support
            & {
                "role_aware_rt_support",
                "paired_istd_anchor_support",
                "paired_istd_rt_within_1min_support",
            }
        )
    )


def _paired_analyte_has_role_or_ratio_supported_peak(
    result: ExtractionResult,
    target: Target,
    support_reasons: tuple[str, ...],
) -> bool:
    support = set(support_reasons)
    return (
        result.role.upper() == "ANALYTE"
        and bool(target.istd_pair)
        and _positive(result.reported_rt)
        and _positive(result.reported_peak_area)
        and _rt_inside_target_window(result.reported_rt, target)
        and not result.quality_flags
        and "ms1_coherent" in support
        and bool(
            support
            & {
                "role_aware_rt_support",
                "paired_area_ratio_support",
            }
        )
    )


def _reported_interval_contains_rt(
    result: ExtractionResult,
    reference_rt: float,
) -> bool:
    start = result.reported_peak_start
    end = result.reported_peak_end
    if start is None or end is None:
        return False
    return start <= reference_rt <= end


def _projection_review_reasons(
    result: ExtractionResult,
    semantics: EvidenceDecisionSemantics | None,
    conflict_reasons: tuple[str, ...],
    *,
    target: Target,
    support_reasons: tuple[str, ...],
    sample_name: str = "",
) -> tuple[str, ...]:
    reasons: list[str] = []
    if semantics is not None:
        reasons.extend(semantics.review_reasons)
        if "plausible_nl_dropout_review" in semantics.review_reasons:
            reasons.append("plausible_dda_nl_dropout")
        if (
            "candidate_aligned_ms2_nl_conflict" in semantics.conflict_reasons
            and _paired_analyte_has_pair_supported_peak(
                result,
                target,
                support_reasons,
            )
        ):
            reasons.append("paired_analyte_nl_review")
        if any(
            _downgrade_trace_conflict_to_istd_review(
                result,
                target,
                reason,
                support_reasons,
            )
            or _downgrade_trace_conflict_to_paired_analyte_review(
                result,
                target,
                reason,
                support_reasons,
            )
            for reason in semantics.conflict_reasons
        ):
            reasons.append("trace_morphology_review")
        if any(
            _downgrade_rt_conflict_to_istd_review(
                result,
                target,
                reason,
                support_reasons,
            )
            or _downgrade_rt_conflict_to_rna_containing_target_review(
                result,
                target,
                reason,
                support_reasons,
                sample_name,
            )
            for reason in semantics.conflict_reasons
        ):
            reasons.append("targeted_rt_review")
        has_raw_hard_flags = hard_quality_flags(result.quality_flags)
        if has_raw_hard_flags and _downgrade_quality_flag_to_review(
            result, target, support_reasons
        ):
            reasons.append("trace_morphology_review")
    if (
        result.role.upper() == "ISTD"
        and result.nl_token == "NL_FAIL"
        and not conflict_reasons
        and result.candidate_ms2_evidence is not None
        and result.candidate_ms2_evidence.strict_nl_scan_count == 0
    ):
        reasons.append("plausible_dda_nl_dropout")
    if result.confidence.upper() == "VERY_LOW" and not conflict_reasons:
        reasons.append("legacy_confidence_review")
    return _unique(reasons)


def _projection_not_counted_reasons(
    result: ExtractionResult,
    semantics: EvidenceDecisionSemantics | None,
    review_reasons: tuple[str, ...],
    conflict_reasons: tuple[str, ...],
    *,
    target: Target,
    support_reasons: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if semantics is not None:
        reasons.extend(_typed_not_counted_reasons(semantics.not_counted_reasons))
    nl_failed = result.nl_token == "NL_FAIL"
    no_ms2 = result.nl_token == "NO_MS2"
    paired_supported = _paired_analyte_has_nl_dropout_supported_peak(
        result,
        target,
        support_reasons,
    )
    if nl_failed and result.role.upper() != "ISTD" and not paired_supported:
        reasons.append("analyte_nl_fail_requires_policy")
    if no_ms2 and result.role.upper() != "ISTD" and not paired_supported:
        reasons.append("analyte_missing_ms2_requires_policy")
    if nl_failed and result.role.upper() == "ISTD" and not (
        "plausible_dda_nl_dropout" in review_reasons
        and not conflict_reasons
    ):
        reasons.append("istd_nl_fail_without_dropout_support")
    return _unique(reasons)


def _typed_not_counted_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    legacy_compatibility_reasons = {
        "legacy_review_only_projection",
        "missing_ms2_compatibility_cap",
        "zero_area_compatibility_cap",
        "hard_quality_flag_compatibility_cap",
    }
    return tuple(
        reason for reason in reasons if reason not in legacy_compatibility_reasons
    )


def _positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _rt_inside_target_window(rt: float | None, target: Target) -> bool:
    return rt is not None and target.rt_min <= rt <= target.rt_max


def _unique(reasons: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def _selection_reason_overlay(
    decision: PeakHypothesisSelectionDecision,
    semantics: EvidenceDecisionSemantics | None,
    field_name: str,
) -> tuple[str, ...]:
    existing = () if semantics is None else getattr(semantics, field_name)
    existing_set = set(existing)
    return tuple(
        reason
        for reason in getattr(decision, field_name)
        if reason and reason not in existing_set
    )


def _merge_reasons(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            reason
            for group in groups
            for reason in group
            if reason
        )
    )
