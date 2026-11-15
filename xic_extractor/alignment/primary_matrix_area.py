from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from xic_extractor.peak_detection.ms1_morphology import MS1_MORPHOLOGY_AREA_SOURCE

if TYPE_CHECKING:
    from xic_extractor.peak_detection.integration_result import IntegrationResult

MISSING_MS1_MORPHOLOGY_AREA = "missing_ms1_morphology_area"
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
    integration: IntegrationResult | None,
) -> PrimaryMatrixAreaDecision:
    if (
        integration is not None
        and integration.ms1_morphology_area_source in PRIMARY_MATRIX_AREA_SOURCES
        and (area := _valid_area(integration.area_ms1_morphology)) is not None
    ):
        return PrimaryMatrixAreaDecision(
            value=area,
            source=integration.ms1_morphology_area_source,
            reason="",
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
