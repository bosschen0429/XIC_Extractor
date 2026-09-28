from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

ProductState = Literal[
    "detected_clean",
    "detected_flagged",
    "not_counted",
    "excluded",
    "ambiguous",
]
ReviewState = Literal["none", "flagged", "review_required"]
LegacyAuthorityStatus = Literal["evidence_only", "diagnostic_only", "retired"]


@dataclass(frozen=True)
class TargetedPriorContext:
    role: str
    expected_present: bool = False
    target_label: str = ""
    istd_pair: str = ""

    @property
    def is_istd(self) -> bool:
        return self.role.upper() == "ISTD"


@dataclass(frozen=True)
class TargetedProductProjection:
    product_state: ProductState
    counted_detection: bool
    review_state: ReviewState
    projection_reason: str
    support_reasons: tuple[str, ...] = ()
    review_reasons: tuple[str, ...] = ()
    conflict_reasons: tuple[str, ...] = ()
    not_counted_reasons: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    legacy_evidence: dict[str, str] = field(default_factory=dict)
    legacy_authority_status: LegacyAuthorityStatus = "evidence_only"
    benchmark_eligibility_state: str = ""


def build_targeted_product_projection(
    context: TargetedPriorContext,
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
