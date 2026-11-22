"""Set-level CID-NL scan relations that cannot grant individual SUPPORT.

This owner is deliberately separate from ``DdaCidNlPeakSupport``.  It
captures boundary and positive-width overlapping cases without selecting
one peak or minting one vote per candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)

RELATIONSHIP = "supports_one_or_more_of"


class DdaCidNlCandidateSetRelationContractError(ValueError):
    """Raised when one scan cannot close a conservative candidate-set relation."""


@dataclass(frozen=True, slots=True)
class DdaCidNlCandidateSetRelation:
    """Immutable set-level relation; it carries no individual vote."""

    sample_key: str
    evidence: CidNlEvidenceObservation
    candidate_peaks: tuple[PeakObservation, ...]
    matched_candidates: tuple[PeakObservation, ...]
    acquisition_mode: str
    dda_scan_number: int
    scan_rt_min: float
    policy: DdaCidNlPeakSupportPolicy
    relationship: str = RELATIONSHIP

    def __post_init__(self) -> None:
        if (
            isinstance(self.dda_scan_number, bool)
            or not isinstance(self.dda_scan_number, int)
            or self.dda_scan_number <= 0
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "dda_scan_number must be a positive integer"
            )
        if isinstance(self.scan_rt_min, bool) or not isinstance(
            self.scan_rt_min, (int, float)
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "scan_rt_min must be finite and non-negative"
            )
        scan_rt = float(self.scan_rt_min)
        if scan_rt < 0.0 or not math.isfinite(scan_rt):
            raise DdaCidNlCandidateSetRelationContractError(
                "scan_rt_min must be finite and non-negative"
            )
        object.__setattr__(self, "scan_rt_min", 0.0 if scan_rt == 0.0 else scan_rt)


__all__ = [
    "DdaCidNlCandidateSetRelation",
    "DdaCidNlCandidateSetRelationContractError",
    "RELATIONSHIP",
]
