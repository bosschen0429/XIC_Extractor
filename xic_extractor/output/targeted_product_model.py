from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProductState = Literal[
    "detected_clean",
    "detected_flagged",
    "not_counted",
    "excluded",
    "ambiguous",
]
ReviewState = Literal["none", "flagged", "review_required"]


@dataclass(frozen=True)
class TargetedProductContext:
    role: str
    expected_present: bool = False
    target_label: str = ""
    istd_pair: str = ""
    neutral_loss_required: bool = False

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
    benchmark_eligibility_state: str = ""
