"""Collapse repeated strict DDA scans into one chromatographic support event.

The CID-NL owner composes already validated scan-to-peak relations. Multiple
scans inside one physical MS1 peak remain distinct scan evidence but count as
one chromatographic support event. This module grants no hypothesis selection
or product authority.
"""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    RELATIONSHIP,
    CidNlChromatographicSupportContent,
    CidNlChromatographicSupportContractError,
    CidNlChromatographicSupportRef,
    DdaCidNlScanSupportMember,
    _member_sort_key,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceRef,
    validate_cid_nl_evidence_binding,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupportRef,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def compose_cid_nl_chromatographic_support_ref(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    scan_support_refs: tuple[DdaCidNlPeakSupportRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
) -> CidNlChromatographicSupportRef:
    """Compose repeated strict scan support into one physical-peak event."""

    _validate_owner_sample(processing_owners, sample)
    supports = _validated_scan_supports(
        scan_support_refs,
        processing_owners=processing_owners,
        sample=sample,
        evidence_refs=evidence_refs,
    )
    content = _content_from_supports(
        sample=sample, supports=supports, evidence_refs=evidence_refs
    )
    return CidNlChromatographicSupportRef(content=content, evidence_refs=evidence_refs)


def _content_from_supports(
    *,
    sample: ResolvedCanonicalSampleIdentity,
    supports: tuple[DdaCidNlPeakSupportRef, ...],
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
) -> CidNlChromatographicSupportContent:
    first = supports[0].content
    loss_by_observation = {
        ref.observation: (ref.observation.configured_neutral_loss_da)
        for ref in evidence_refs
    }
    members = tuple(
        sorted(
            (
                DdaCidNlScanSupportMember(
                    support=support.content,
                    configured_neutral_loss_da=loss_by_observation.get(
                        support.content.evidence
                    ),
                )
                for support in supports
            ),
            key=_member_sort_key,
        )
    )
    return CidNlChromatographicSupportContent(
        sample_key=sample.identity.sample_key,
        peak_observation=first.peak_observation,
        candidate_peaks=first.candidate_peaks,
        acquisition_mode=first.acquisition_mode,
        policy=first.policy,
        scan_supports=members,
        strict_nl_scan_count=len(
            {member.support.dda_scan_number for member in members}
        ),
        chromatographic_support_event_count=1,
        relationship=RELATIONSHIP,
    )


def _validated_scan_supports(
    scan_support_refs: object,
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    evidence_refs: tuple[CidNlEvidenceRef, ...] = (),
) -> tuple[DdaCidNlPeakSupportRef, ...]:
    if not isinstance(scan_support_refs, tuple) or not scan_support_refs:
        raise CidNlChromatographicSupportContractError(
            "missing strict DDA scan support"
        )
    if any(
        not isinstance(reference, DdaCidNlPeakSupportRef)
        for reference in scan_support_refs
    ):
        raise CidNlChromatographicSupportContractError(
            "scan_support_refs must contain owner-backed DDA peak support"
        )
    supports = scan_support_refs
    loss_by_observation = {}
    if evidence_refs:
        for evidence in evidence_refs:
            validate_cid_nl_evidence_binding(
                evidence, processing_owners=processing_owners, sample=sample
            )
            loss_by_observation[evidence.observation] = (
                evidence.observation.configured_neutral_loss_da
            )
        if len(loss_by_observation) != len(evidence_refs) or set(
            loss_by_observation
        ) != {support.content.evidence for support in supports}:
            raise CidNlChromatographicSupportContractError(
                "measured loss evidence must exactly close scan supports"
            )
    for support in supports:
        if support.content.sample_key != sample.identity.sample_key:
            raise CidNlChromatographicSupportContractError(
                "DDA scan support references a different sample"
            )
    scan_numbers = tuple(
        (
            support.content.dda_scan_number,
            loss_by_observation.get(support.content.evidence),
        )
        for support in supports
    )
    if len(set(scan_numbers)) != len(scan_numbers):
        raise CidNlChromatographicSupportContractError(
            "duplicate DDA scan support cannot multiply one event"
        )
    if any(
        support.content.peak_observation != supports[0].content.peak_observation
        for support in supports[1:]
    ):
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references more than one physical MS1 peak"
        )
    if any(
        support.content.candidate_peaks != supports[0].content.candidate_peaks
        for support in supports[1:]
    ):
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references a different candidate universe"
        )
    if len({support.content.policy for support in supports}) != 1:
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references a different support policy"
        )
    if any(
        support.content.acquisition_mode != ACQUISITION_MODE for support in supports
    ):
        raise CidNlChromatographicSupportContractError(
            f"DDA scan support acquisition mode must be {ACQUISITION_MODE!r}"
        )
    return tuple(
        sorted(
            supports,
            key=lambda support: (
                support.content.dda_scan_number,
                loss_by_observation.get(support.content.evidence) or 0.0,
            ),
        )
    )


def _validate_owner_sample(
    processing_owners: object,
    sample: object,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise CidNlChromatographicSupportContractError(
            "processing_owners must be validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise CidNlChromatographicSupportContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if sample not in processing_owners.sample_resolutions:
        raise CidNlChromatographicSupportContractError(
            "chromatographic support references a different sample"
        )


__all__ = [
    "CidNlChromatographicSupportContent",
    "CidNlChromatographicSupportContractError",
    "CidNlChromatographicSupportRef",
    "DdaCidNlScanSupportMember",
    "RELATIONSHIP",
    "compose_cid_nl_chromatographic_support_ref",
]
