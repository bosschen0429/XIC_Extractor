from __future__ import annotations

import math
from typing import TYPE_CHECKING

from xic_extractor.configuration.models import Target
from xic_extractor.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.output.targeted_product_model import (
    OWN_MAX_SAME_PEAK_SUPPORT_REASON,
    ProductState,
    ReviewState,
    TargetedProductContext,
    TargetedProductProjection,
)
from xic_extractor.peak_detection.scoring_quality import hard_quality_flags
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)
from xic_extractor.target_sample_applicability import (
    target_sample_exclusion_reasons,
    target_sample_is_applicable,
)

if TYPE_CHECKING:
    from xic_extractor.extractor import ExtractionResult


def project_targeted_result(
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
        TargetedProductContext(
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
            and not _downgrade_rt_conflict_to_nl_supported_analyte_review(
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


def _downgrade_rt_conflict_to_nl_supported_analyte_review(
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
    return reason in {
        "trace_morphology_conflict",
        "hard_local_quality_conflict",
        "hard_quality_flag_conflict",
    } and (
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
            or _downgrade_rt_conflict_to_nl_supported_analyte_review(
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
    if (
        result.physical_evaluation is not None
        and result.physical_evaluation.requires_overlap_review
    ):
        reasons.append("physical_shoulder_overlap")
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
    if (
        nl_failed
        and result.role.upper() == "ISTD"
        and not ("plausible_dda_nl_dropout" in review_reasons and not conflict_reasons)
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
        dict.fromkeys(reason for group in groups for reason in group if reason)
    )


def build_targeted_product_projection(
    context: TargetedProductContext,
    *,
    rt: float | None,
    area: float | None,
    confidence: str,
    nl_status: str,
    support_reasons: tuple[str, ...] = (),
    review_reasons: tuple[str, ...] = (),
    conflict_reasons: tuple[str, ...] = (),
    not_counted_reasons: tuple[str, ...] = (),
    exclusion_reasons: tuple[str, ...] = (),
    legacy_evidence: dict[str, str] | None = None,
) -> TargetedProductProjection:
    legacy = {
        "confidence": confidence,
        "nl_status": nl_status,
        **(legacy_evidence or {}),
    }
    support = _stable_reasons(support_reasons)
    review = _stable_reasons(review_reasons)
    conflicts = _stable_reasons(conflict_reasons)
    not_counted = _stable_reasons(not_counted_reasons)
    exclusions = _stable_reasons(exclusion_reasons)

    missing_ms1 = _missing_positive_ms1_peak(rt, area)
    if exclusions:
        return _projection(
            "excluded",
            False,
            "review_required",
            support,
            review,
            conflicts,
            not_counted,
            exclusions,
            legacy,
        )
    if conflicts:
        return _projection(
            "ambiguous",
            False,
            "review_required",
            support,
            review,
            conflicts,
            not_counted,
            exclusions,
            legacy,
        )
    if missing_ms1:
        return _projection(
            "not_counted",
            False,
            "review_required",
            support,
            review,
            conflicts,
            ("missing_positive_ms1_peak",),
            exclusions,
            legacy,
        )

    plausible_dropout = "plausible_dda_nl_dropout" in review
    if context.is_istd and context.expected_present and plausible_dropout:
        return _projection(
            "detected_flagged",
            True,
            "flagged",
            _stable_reasons((*support, "role_aware_istd_expected_present")),
            review,
            conflicts,
            (),
            exclusions,
            legacy,
        )

    if not_counted:
        return _projection(
            "not_counted",
            False,
            "review_required",
            support,
            review,
            conflicts,
            not_counted,
            exclusions,
            legacy,
        )

    if review:
        return _projection(
            "detected_flagged",
            True,
            "flagged",
            support,
            review,
            conflicts,
            (),
            exclusions,
            legacy,
        )
    return _projection(
        "detected_clean",
        True,
        "none",
        support,
        review,
        conflicts,
        (),
        exclusions,
        legacy,
    )


def _projection(
    product_state: ProductState,
    counted_detection: bool,
    review_state: ReviewState,
    support_reasons: tuple[str, ...],
    review_reasons: tuple[str, ...],
    conflict_reasons: tuple[str, ...],
    not_counted_reasons: tuple[str, ...],
    exclusion_reasons: tuple[str, ...],
    legacy_evidence: dict[str, str],
) -> TargetedProductProjection:
    return TargetedProductProjection(
        product_state=product_state,
        counted_detection=counted_detection,
        review_state=review_state,
        projection_reason=_projection_reason(
            product_state,
            support_reasons,
            review_reasons,
            conflict_reasons,
            not_counted_reasons,
            exclusion_reasons,
        ),
        support_reasons=support_reasons,
        review_reasons=review_reasons,
        conflict_reasons=conflict_reasons,
        not_counted_reasons=not_counted_reasons,
        exclusion_reasons=exclusion_reasons,
        legacy_evidence=legacy_evidence,
        legacy_authority_status="evidence_only",
    )


def _projection_reason(
    product_state: ProductState,
    support_reasons: tuple[str, ...],
    review_reasons: tuple[str, ...],
    conflict_reasons: tuple[str, ...],
    not_counted_reasons: tuple[str, ...],
    exclusion_reasons: tuple[str, ...],
) -> str:
    sections = [f"decision: {product_state}"]
    for label, reasons in (
        ("support", support_reasons),
        ("review", review_reasons),
        ("conflict", conflict_reasons),
        ("not_counted", not_counted_reasons),
        ("excluded", exclusion_reasons),
    ):
        if reasons:
            sections.append(f"{label}: {', '.join(reasons)}")
    return "; ".join(sections)


def _missing_positive_ms1_peak(rt: float | None, area: float | None) -> bool:
    return (
        rt is None
        or area is None
        or not math.isfinite(rt)
        or not math.isfinite(area)
        or area <= 0
    )


def _stable_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for reason in reasons if reason))
