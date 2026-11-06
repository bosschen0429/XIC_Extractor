"""Set-level CID-NL scan relations that cannot grant individual SUPPORT.

This owner is deliberately separate from ``DdaCidNlPeakSupportRef``.  It
captures boundary and positive-width overlapping cases without selecting
one peak or minting one vote per candidate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceContractError,
    CidNlEvidenceObservation,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContent,
    PeakObservationRef,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

RELATIONSHIP = "supports_one_or_more_of"


class DdaCidNlCandidateSetRelationContractError(ValueError):
    """Raised when one scan cannot close a conservative candidate-set relation."""


@dataclass(frozen=True, slots=True)
class DdaCidNlCandidateSetRelationContent:
    """Immutable set-level relation; it carries no individual vote."""

    sample_key: str
    evidence: CidNlEvidenceObservation
    candidate_peaks: tuple[PeakObservationContent, ...]
    matched_candidates: tuple[PeakObservationContent, ...]
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


@dataclass(frozen=True, slots=True)
class DdaCidNlCandidateSetRelationRef:
    """Set-level relation plus the exact evidence and candidate references."""

    content: DdaCidNlCandidateSetRelationContent
    canonical_evidence: CidNlEvidenceRef
    candidate_peaks: tuple[PeakObservationRef, ...]


def resolve_dda_cid_nl_candidate_set_relation_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlCandidateSetRelationRef:
    """Resolve a non-unique zero-buffer case without selecting one candidate."""

    candidates = _validate_inputs(
        processing_owners=processing_owners,
        sample=sample,
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidate_peaks,
        policy=policy,
    )
    matched = _set_level_members(
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidates,
        policy=policy,
    )
    content = DdaCidNlCandidateSetRelationContent(
        sample_key=sample.identity.sample_key,
        evidence=canonical_evidence.observation,
        candidate_peaks=tuple(peak.content for peak in candidates),
        matched_candidates=tuple(peak.content for peak in matched),
        acquisition_mode=canonical_evidence.observation.acquisition_mode,
        dda_scan_number=canonical_evidence.observation.scan_number,
        scan_rt_min=canonical_evidence.observation.scan_rt_min,
        policy=policy,
    )
    return DdaCidNlCandidateSetRelationRef(
        content=content,
        canonical_evidence=canonical_evidence,
        candidate_peaks=candidates,
    )


def validate_dda_cid_nl_candidate_set_relation_binding(
    reference: DdaCidNlCandidateSetRelationRef,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
) -> None:
    """Reject request, attempt, sample, evidence, or universe substitution."""

    if not isinstance(reference, DdaCidNlCandidateSetRelationRef):
        raise DdaCidNlCandidateSetRelationContractError(
            "reference must be an owner-minted candidate-set relation"
        )
    expected = resolve_dda_cid_nl_candidate_set_relation_ref(
        processing_owners=processing_owners,
        sample=sample,
        canonical_evidence=reference.canonical_evidence,
        candidate_peaks=reference.candidate_peaks,
        policy=reference.content.policy,
    )
    if reference != expected:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate-set relation binding was substituted"
        )


def _validate_inputs(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: object,
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservationRef, ...]:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise DdaCidNlCandidateSetRelationContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise DdaCidNlCandidateSetRelationContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate-set relation references a sample outside the request"
        )
    if not isinstance(policy, DdaCidNlPeakSupportPolicy):
        raise DdaCidNlCandidateSetRelationContractError(
            "policy must be owner-normalized DDA CID-NL support policy"
        )
    try:
        validate_cid_nl_evidence_binding(
            canonical_evidence,
            processing_owners=processing_owners,
            sample=sample,
        )
    except CidNlEvidenceContractError as exc:
        raise DdaCidNlCandidateSetRelationContractError(str(exc)) from exc
    candidates = _canonical_candidates(candidate_peaks)
    observation = canonical_evidence.observation
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
    canonical_evidence: CidNlEvidenceRef,
    candidate_peaks: tuple[PeakObservationRef, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> tuple[PeakObservationRef, ...]:
    observation = canonical_evidence.observation
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    if not policy.accepts_precursor(precursor) or precursor.reference_mz is None:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL evidence lacks independent precursor isolation support"
        )
    mz_compatible = tuple(
        peak
        for peak in candidate_peaks
        if abs(precursor.reference_mz - peak.content.mz)
        <= policy.precursor_tolerance_da
    )
    containing = tuple(
        peak
        for peak in mz_compatible
        if peak.content.integration.rt_left_min <= observation.support_rt_min
        and observation.scan_rt_min <= peak.content.integration.rt_right_min
    )
    if not containing:
        raise DdaCidNlCandidateSetRelationContractError(
            "CID-NL scan does not relate to the declared candidate universe"
        )

    if len(containing) == 1:
        selected = containing[0]
        interval = selected.content.integration
        if (
            interval.rt_left_min < observation.scan_rt_min < interval.rt_right_min
            and not any(
                candidate is not selected
                and interval.rt_left_min < candidate.content.integration.rt_right_min
                and candidate.content.integration.rt_left_min < interval.rt_right_min
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
    left: PeakObservationRef,
    right: PeakObservationRef,
) -> bool:
    left_integration = left.content.integration
    right_integration = right.content.integration
    return (
        left_integration.rt_left_min <= right_integration.rt_right_min
        and right_integration.rt_left_min <= left_integration.rt_right_min
    )


def _canonical_candidates(value: object) -> tuple[PeakObservationRef, ...]:
    if not isinstance(value, tuple) or not value:
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be a non-empty immutable tuple"
        )
    if any(not isinstance(peak, PeakObservationRef) for peak in value):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must contain owner-backed observations"
        )
    candidates = tuple(sorted(value, key=_candidate_sort_key))
    keys = tuple(peak.content.physical_peak_key for peak in candidates)
    if len(set(keys)) != len(keys):
        raise DdaCidNlCandidateSetRelationContractError(
            "candidate_peaks must be unique"
        )
    return candidates


def _candidate_sort_key(
    peak: PeakObservationRef,
) -> tuple[float, float, tuple[float, ...], tuple[float, ...]]:
    return peak.content.measured_order_key


__all__ = [
    "DdaCidNlCandidateSetRelationContent",
    "DdaCidNlCandidateSetRelationContractError",
    "DdaCidNlCandidateSetRelationRef",
    "RELATIONSHIP",
    "resolve_dda_cid_nl_candidate_set_relation_ref",
    "validate_dda_cid_nl_candidate_set_relation_binding",
]
