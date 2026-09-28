from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE

MISSING_MS1_MORPHOLOGY_AREA = "missing_ms1_morphology_area"
MISSING_ASLS_PRIMARY_AREA = MISSING_MS1_MORPHOLOGY_AREA
ASLS_PRIMARY_MATRIX_AREA_SOURCE = "asls_baseline_corrected"
MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE = MS1_MORPHOLOGY_AREA_SOURCE
CANONICAL_PHYSICAL_PRIMARY_MATRIX_AREA_SOURCE = (
    "trapezoid_counts_seconds_on_canonical_signal"
)
OBSERVED_CENSORED_PHYSICAL_PRIMARY_MATRIX_AREA_SOURCE = (
    "trapezoid_counts_seconds_on_observed_censored_signal"
)
PRIMARY_MATRIX_AREA_SOURCES = frozenset(
    {
        MS1_MORPHOLOGY_PRIMARY_MATRIX_AREA_SOURCE,
        CANONICAL_PHYSICAL_PRIMARY_MATRIX_AREA_SOURCE,
        OBSERVED_CENSORED_PHYSICAL_PRIMARY_MATRIX_AREA_SOURCE,
    }
)


@dataclass(frozen=True)
class PrimaryMatrixAreaDecision:
    value: float | None
    source: str
    reason: str


def primary_matrix_area_from_integration(
    integration: Any | None,
) -> PrimaryMatrixAreaDecision:
    if integration is None:
        return PrimaryMatrixAreaDecision(
            value=None,
            source="",
            reason=MISSING_MS1_MORPHOLOGY_AREA,
        )
    area_source = getattr(integration, "ms1_morphology_area_source", "")
    if area_source in PRIMARY_MATRIX_AREA_SOURCES:
        morphology_area = _valid_area(getattr(integration, "area_ms1_morphology", None))
        if morphology_area is not None:
            return PrimaryMatrixAreaDecision(
                value=morphology_area,
                source=area_source,
                reason="",
            )
    if getattr(integration, "baseline_type", "") != "asls":
        return PrimaryMatrixAreaDecision(
            value=None,
            source="",
            reason=MISSING_MS1_MORPHOLOGY_AREA,
        )
    area = _valid_area(getattr(integration, "area_baseline_corrected", None))
    if area is None:
        return PrimaryMatrixAreaDecision(
            value=None,
            source="",
            reason=MISSING_MS1_MORPHOLOGY_AREA,
        )
    return PrimaryMatrixAreaDecision(
        value=None,
        source="",
        reason=MISSING_MS1_MORPHOLOGY_AREA,
    )


def _valid_area(value: object) -> float | None:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        return None
    return float(value)
