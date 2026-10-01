from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OWN_MAX_SAME_PEAK_SUPPORT_REASON = "own_max_same_peak_support"

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
class TargetedProductContext:
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
