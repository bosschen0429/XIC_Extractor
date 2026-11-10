"""Set-level CID-NL scan relations that cannot grant individual SUPPORT.

This owner is deliberately separate from ``DdaCidNlPeakSupport``.  It
captures boundary and positive-width overlapping cases without selecting
one peak or minting one vote per candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.sample_metadata import SampleMetadata

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
        if self.acquisition_mode != ACQUISITION_MODE:
            raise DdaCidNlCandidateSetRelationContractError(
                f"acquisition_mode must be {ACQUISITION_MODE!r}"
            )
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
        if not isinstance(self.policy, DdaCidNlPeakSupportPolicy):
            raise DdaCidNlCandidateSetRelationContractError(
                "policy must be owner-normalized DDA CID-NL support policy"
            )
        if self.relationship != RELATIONSHIP:
            raise DdaCidNlCandidateSetRelationContractError(
                f"relationship must be {RELATIONSHIP!r}"
            )
        object.__setattr__(self, "scan_rt_min", 0.0 if scan_rt == 0.0 else scan_rt)


def resolve_dda_cid_nl_candidate_set_relation(
    *,
    sample: SampleMetadata,
    canonical_evidence: CidNlEvidenceObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlCandidateSetRelation:
    """Resolve a non-unique zero-buffer case without selecting one candidate."""

    candidates = _validate_inputs(
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidate_peaks,
        policy=policy,
    )
    matched = _set_level_members(
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidates,
        policy=policy,
    )
    return DdaCidNlCandidateSetRelation(
        sample_key=sample.sample_name,
        evidence=canonical_evidence,
        candidate_peaks=candidates,
        matched_candidates=matched,
        acquisition_mode=canonical_evidence.acquisition_mode,
        dda_scan_number=canonical_evidence.scan_number,
        scan_rt_min=canonical_evidence.scan_rt_min,
        policy=policy,
    )


def _validate_inputs(
    *,
    canonical_evidence: CidNlEvidenceObservation,
    candidate_peaks: object,
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservation, ...]:
    if not isinstance(policy, DdaCidNlPeakSupportPolicy):
        raise DdaCidNlCandidateSetRelationContractError(
            "policy must be owner-normalized DDA CID-NL support policy"
        )
    candidates = _canonical_candidates(candidate_peaks)
    observation = canonical_evidence
    if not policy.accepts_precursor(
        observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    ):
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL evidence does not satisfy the strict neutral-loss tolerance "
            "or precursor reference requirement"
        )
    return candidates


def _set_level_members(
    *,
    canonical_evidence: CidNlEvidenceObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservation, ...]:
    observation = canonical_evidence
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    if not policy.accepts_precursor(precursor) or precursor.reference_mz is None:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL evidence lacks independent precursor isolation support"
        )
    mz_compatible = tuple(
        peak
        for peak in candidate_peaks
        if abs(precursor.reference_mz - peak.mz) <= policy.precursor_tolerance_da
    )
    containing = tuple(
        peak
        for peak in mz_compatible
        if peak.integration.rt_left_min <= observation.support_rt_min
        and observation.scan_rt_min <= peak.integration.rt_right_min
    )
    if not containing:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL scan does not relate to the declared candidate universe"
        )

    if len(containing) == 1:
        selected = containing[0]
        interval = selected.integration
        if (
            interval.rt_left_min < observation.scan_rt_min < interval.rt_right_min
            and not any(
                candidate is not selected
                and interval.rt_left_min < candidate.integration.rt_right_min
                and candidate.integration.rt_left_min < interval.rt_right_min
                for candidate in mz_compatible
            )
        ):
            raise DdaCidNlCandidateSetRelationContractError(
                "strictly unique relation belongs to individual support"
            )

    # Boundary sets retain touching neighbors after individual eligibility closes.
    component = set(containing)
    changed = True
    while changed:
        changed = False
        for candidate in mz_compatible:
            if candidate in component:
                continue
            if any(
                _intervals_touch_or_overlap(candidate, member) for member in component
            ):
                component.add(candidate)
                changed = True
    matched = tuple(sorted(component, key=_candidate_sort_key))
    return matched


def _intervals_touch_or_overlap(
    left: PeakObservation,
    right: PeakObservation,
) -> bool:
    left_integration = left.integration
    right_integration = right.integration
    return (
        left_integration.rt_left_min <= right_integration.rt_right_min
        and right_integration.rt_left_min <= left_integration.rt_right_min
    )


def _canonical_candidates(value: object) -> tuple[PeakObservation, ...]:
    if not isinstance(value, tuple) or not value:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be a non-empty immutable tuple"
        )
    if any(not isinstance(peak, PeakObservation) for peak in value):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must contain owner-backed observations"
        )
    candidates = tuple(sorted(value, key=_candidate_sort_key))
    keys = tuple(peak.physical_peak_key for peak in candidates)
    if len(set(keys)) != len(keys):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be unique"
        )
    return candidates


def _candidate_sort_key(
    peak: PeakObservation,
) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
    return peak.measured_order_key


__all__ = [
    "DdaCidNlCandidateSetRelation",
    "DdaCidNlCandidateSetRelationContractError",
    "RELATIONSHIP",
    "resolve_dda_cid_nl_candidate_set_relation",
]
