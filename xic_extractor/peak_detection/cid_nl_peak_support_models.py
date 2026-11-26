"""Immutable values for one DDA CID-NL event-to-MS1-peak binding."""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.configuration.cid_nl_profile import PrecursorReferenceRequirement
from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.precursor_evidence import (
    PrecursorLossAssessment,
)

RELATIONSHIP = "strict_nl_inside_ms1_peak_scope"


class DdaCidNlPeakSupportContractError(ValueError):
    """Raised when a DDA CID-NL event cannot bind to one physical MS1 peak."""


@dataclass(frozen=True, slots=True)
class DdaCidNlPeakSupportPolicy:
    """Owner-normalized matching thresholds already present in method inputs."""

    precursor_tolerance_da: float
    neutral_loss_tolerance_ppm: float
    precursor_reference_requirement: PrecursorReferenceRequirement = "independent_mass"

    def __post_init__(self) -> None:
        precursor_tolerance = _finite_float(
            self.precursor_tolerance_da,
            field="precursor_tolerance_da",
        )
        neutral_loss_tolerance = _finite_float(
            self.neutral_loss_tolerance_ppm,
            field="neutral_loss_tolerance_ppm",
        )
        if precursor_tolerance <= 0.0:
            raise DdaCidNlPeakSupportContractError(
                "precursor_tolerance_da must be positive"
            )
        if neutral_loss_tolerance <= 0.0:
            raise DdaCidNlPeakSupportContractError(
                "neutral_loss_tolerance_ppm must be positive"
            )
        object.__setattr__(self, "precursor_tolerance_da", precursor_tolerance)
        object.__setattr__(
            self,
            "neutral_loss_tolerance_ppm",
            neutral_loss_tolerance,
        )

    def accepts_precursor(self, assessment: PrecursorLossAssessment) -> bool:
        return assessment.supports_reference(self.precursor_reference_requirement)


@dataclass(frozen=True, slots=True)
class DdaCidNlPeakSupport:
    """Immutable method relation between one DDA event and one MS1 peak."""

    evidence: CidNlEvidenceObservation
    peak_observation: PeakObservation
    candidate_peaks: tuple[PeakObservation, ...]
    precursor: PrecursorLossAssessment
    policy: DdaCidNlPeakSupportPolicy
    relationship: str

    @property
    def sample_key(self) -> str:
        return self.evidence.sample_key

    @property
    def dda_scan_number(self) -> int:
        return self.evidence.scan_number

    @property
    def configured_neutral_loss_da(self) -> float:
        return self.evidence.configured_neutral_loss_da


def _finite_float(value: float, *, field: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized):
        raise DdaCidNlPeakSupportContractError(f"{field} must be a finite number")
    return 0.0 if normalized == 0.0 else normalized


__all__ = [
    "DdaCidNlPeakSupport",
    "DdaCidNlPeakSupportContractError",
    "DdaCidNlPeakSupportPolicy",
    "RELATIONSHIP",
]
