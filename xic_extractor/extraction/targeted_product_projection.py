from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from statistics import median, quantiles
from typing import TYPE_CHECKING

from xic_extractor.configuration.models import Target
from xic_extractor.output.targeted_product_model import (
    ProductState,
    ReviewState,
    TargetedProductContext,
    TargetedProductProjection,
)
from xic_extractor.peak_detection.evidence_semantics import EvidenceDecisionSemantics
from xic_extractor.peak_detection.scoring_quality import hard_quality_flags
from xic_extractor.peak_detection.selection_decision import (
    PeakHypothesisSelectionDecision,
)

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import ExtractionResult


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
    conflicts = _merge_reasons(
        () if semantics is None else semantics.conflict_reasons,
        () if semantics is None else semantics.ambiguity_reasons,
        () if selection_decision is None else selection_decision.conflict_reasons,
        () if selection_decision is None else selection_decision.ambiguity_reasons,
    )
    review = _merge_reasons(
        () if semantics is None else semantics.review_reasons,
        () if selection_decision is None else selection_decision.review_reasons,
    )
    if hard_quality_flags(result.quality_flags):
        review = _merge_reasons(review, ("trace_morphology_review",))
    if (
        result.physical_evaluation is not None
        and result.physical_evaluation.requires_overlap_review
    ):
        review = _merge_reasons(review, ("physical_shoulder_overlap",))
    not_counted = _typed_not_counted_reasons(
        _merge_reasons(
            () if semantics is None else semantics.not_counted_reasons,
            ()
            if selection_decision is None
            else selection_decision.not_counted_reasons,
        )
    )
    exclusions: tuple[str, ...] = ()
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
            neutral_loss_required=target.neutral_loss_da is not None,
        ),
        rt=result.reported_rt,
        area=result.reported_peak_area,
        confidence=result.confidence,
        nl_status=nl_status,
        cid_nl_supported=result.selected_cid_nl_supported,
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
    if _paired_analyte_has_anchor_supported_peak(result, target):
        reasons.append("role_aware_rt_support")
        reasons.append("paired_istd_anchor_support")
    return _unique(reasons)


def _paired_analyte_has_anchor_supported_peak(
    result: ExtractionResult,
    target: Target,
) -> bool:
    if (
        result.role.upper() != "ANALYTE"
        or not target.istd_pair
        or not _positive(result.reported_rt)
        or not _positive(result.reported_peak_area)
    ):
        return False
    reference_rt = result.peak_result.paired_istd_anchor_rt
    if reference_rt is None or not math.isfinite(reference_rt):
        return False
    return _reported_interval_contains_rt(result, reference_rt)


def _reported_interval_contains_rt(
    result: ExtractionResult,
    reference_rt: float,
) -> bool:
    start = result.reported_peak_start
    end = result.reported_peak_end
    if start is None or end is None:
        return False
    return start <= reference_rt <= end


def _typed_not_counted_reasons(reasons: tuple[str, ...]) -> tuple[str, ...]:
    legacy_compatibility_reasons = {
        "legacy_review_only_projection",
        "missing_ms2_compatibility_cap",
        "zero_area_compatibility_cap",
        "hard_quality_flag_compatibility_cap",
        "paired_istd_rt_mismatch_policy",
        "paired_istd_rt_mismatch_cap",
        "anchor_mismatch_compatibility_cap",
    }
    return tuple(
        reason for reason in reasons if reason not in legacy_compatibility_reasons
    )


_MISSING_DIRECT_NL_REVIEW = frozenset(
    {
        "plausible_nl_dropout_review",
        "missing_ms2_not_observed",
        "missing_nl_support_review",
    }
)
_MISSING_DIRECT_NL_NOT_COUNTED = frozenset(
    {
        "cid_nl_method_not_supported",
        "missing_ms2_policy_not_counted",
        "istd_nl_fail_without_dropout_support",
    }
)
_COHERENT_MS1_PEAK = frozenset({"ms1_peak_present", "ms1_coherent", "trace_coherent"})
# Backfill acceptance qualified the recovered peak's shape; the first capture's
# coherence facts may describe the peak it replaced.
_BACKFILL_LOCALIZATION = "cross_sample_backfill_localization"
# The Backfill apex floor: closer RT agreement is not resolvable (12 s).
_PAIRED_OFFSET_FLOOR_MIN = 0.2


@dataclass(frozen=True)
class PairedIstdPositions:
    """Where analyte peaks sit relative to their counted paired isotope ISTD."""

    identified: frozenset[str] = frozenset()
    # Sample -> minutes the selected peak lies from the paired-ISTD position.
    off_position: Mapping[str, float] = field(default_factory=dict)


def paired_istd_positions(
    analyte: Mapping[str, ExtractionResult],
    istd: Mapping[str, ExtractionResult],
    *,
    expected_offset_min: float = 0.0,
) -> PairedIstdPositions:
    """Judge analyte peaks against the paired isotope ISTD position.

    An isotope-labelled ISTD co-elutes with its analyte, so once the ISTD is
    counted in a sample (directly or by accepted Backfill) it fixes where the
    analyte must be. A coherent MS1 peak there takes its chemical identity from
    the ISTD whether or not its own neutral loss was observed. A peak elsewhere
    is another signal, even with neutral-loss support. Confirmed pairs refine
    the batch offset and tolerance only when consistent with the configured offset;
    otherwise that offset and the 12 s floor apply. Off-position peaks are ND.
    The number of other confirmed samples never gates identity: the current sample still
    needs a valid ISTD, qualified peak and no blockers.
    """
    counted_istd = {
        sample: rt
        for sample, result in istd.items()
        if _counted(result) and (rt := _finite(result.reported_rt)) is not None
    }
    offsets = [
        rt - counted_istd[sample]
        for sample, result in analyte.items()
        if sample in counted_istd
        and _counted(result)
        and (rt := _finite(result.reported_rt)) is not None
        # A contradictory counted peak cannot calibrate away its own mismatch.
        and abs(rt - counted_istd[sample] - expected_offset_min)
        <= _PAIRED_OFFSET_FLOOR_MIN
    ]
    calibrated = len(offsets) >= 2
    center = median(offsets) if calibrated else expected_offset_min
    tolerance = _PAIRED_OFFSET_FLOOR_MIN
    if calibrated:
        residuals = [abs(offset - center) for offset in offsets]
        tolerance = max(tolerance, quantiles(residuals, n=10, method="inclusive")[-1])
    identified = set()
    off_position = {}
    for sample, result in analyte.items():
        projection = result.targeted_product_projection
        rt = _finite(result.reported_rt)
        if projection is None or sample not in counted_istd or rt is None:
            continue
        deviation = rt - counted_istd[sample] - center
        if abs(deviation) > tolerance:
            off_position[sample] = deviation
        elif (
            not projection.counted_detection
            and not projection.conflict_reasons
            and not projection.exclusion_reasons
            and (
                _COHERENT_MS1_PEAK <= set(projection.support_reasons)
                or _BACKFILL_LOCALIZATION in projection.support_reasons
            )
            and set(projection.not_counted_reasons) <= _MISSING_DIRECT_NL_NOT_COUNTED
        ):
            identified.add(sample)
    return PairedIstdPositions(frozenset(identified), off_position)


def _counted(result: ExtractionResult) -> bool:
    projection = result.targeted_product_projection
    return projection is not None and projection.counted_detection


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def _positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


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
    cid_nl_supported: bool = False,
    backfill_supported: bool = False,
    paired_istd_identity: bool = False,
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
    # RT priors and legacy scores describe how the peak was located, not an
    # unresolved action. Keep them in the source evidence, not the product queue.
    context_only = {
        "targeted_rt_review",
        "paired_istd_rt_review",
        "legacy_confidence_review",
        "backfill_identity_confirmed",
    }
    review = _stable_reasons(tuple(r for r in review_reasons if r not in context_only))
    localization_conflicts = {"targeted_rt_conflict", "anchor_conflict"}
    review_categories = {
        "trace_morphology_conflict": "trace_morphology_review",
        "hard_local_quality_conflict": "trace_morphology_review",
        "hard_quality_flag_conflict": "trace_morphology_review",
        "candidate_aligned_ms2_nl_conflict": "missing_nl_support_review",
    }
    review = _merge_reasons(
        review,
        tuple(
            review_categories[reason]
            for reason in conflict_reasons
            if reason in review_categories
        ),
    )
    conflicts = _stable_reasons(
        tuple(
            reason
            for reason in conflict_reasons
            if reason not in review_categories and reason not in localization_conflicts
        )
    )
    not_counted = _stable_reasons(not_counted_reasons)
    if (
        context.neutral_loss_required
        and not (cid_nl_supported or (context.is_istd and backfill_supported))
        and not not_counted
    ):
        not_counted = _merge_reasons(not_counted, ("cid_nl_method_not_supported",))
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

    if backfill_supported and not context.is_istd:
        # Accepted Backfill located a physically qualified peak; it localizes
        # but does not identify an analyte by itself.
        support = _merge_reasons(support, (_BACKFILL_LOCALIZATION,))
    identity = (
        "cross_sample_backfill_identity"
        if context.is_istd and backfill_supported
        else "paired_istd_identity"
        if not context.is_istd and paired_istd_identity
        else None
    )
    if identity is not None:
        support = _merge_reasons(support, (identity,))
        # Identity from Backfill or the paired ISTD resolves missing direct
        # NL/MS2; independent morphology or overlap concerns remain actionable.
        resolved_identity_reviews = {
            "targeted_retention_context_unavailable",
            "targeted_reference_displacement",
            "targeted_retention_transfer_uncertain",
            "targeted_competing_regions",
        }
        review = tuple(
            r
            for r in review
            if r not in _MISSING_DIRECT_NL_REVIEW and r not in resolved_identity_reviews
        )
        not_counted = tuple(
            r for r in not_counted if r not in _MISSING_DIRECT_NL_NOT_COUNTED
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
