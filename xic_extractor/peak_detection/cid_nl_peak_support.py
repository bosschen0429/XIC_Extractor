"""Bind one decided DDA/ddMS2 CID-NL event to its physical MS1 peak."""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    RELATIONSHIP,
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.precursor_evidence import PrecursorLossAssessment


def bind_dda_cid_nl_peak_support(
    *,
    evidence: CidNlEvidenceObservation,
    precursor: PrecursorLossAssessment,
    peak_observation: PeakObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupport:
    """Record a relation answer over the measured-order candidate universe."""

    return DdaCidNlPeakSupport(
        evidence=evidence,
        peak_observation=peak_observation,
        candidate_peaks=candidate_peaks,
        precursor=precursor,
        policy=policy,
        relationship=RELATIONSHIP,
    )


__all__ = [
    "DdaCidNlPeakSupport",
    "DdaCidNlPeakSupportPolicy",
    "RELATIONSHIP",
    "bind_dda_cid_nl_peak_support",
]
