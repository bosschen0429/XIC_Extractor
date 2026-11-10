"""Collapse repeated strict DDA scans into one chromatographic support event.

The CID-NL owner composes already validated scan-to-peak relations. Multiple
scans inside one physical MS1 peak remain distinct scan evidence but count as
one chromatographic support event. This module grants no hypothesis selection
or product authority.
"""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    RELATIONSHIP,
    CidNlChromatographicSupport,
    CidNlChromatographicSupportContractError,
    DdaCidNlScanSupportMember,
    _member_sort_key,
)
from xic_extractor.peak_detection.cid_nl_evidence import (
    ACQUISITION_MODE,
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_peak_support import (
    DdaCidNlPeakSupport,
)
from xic_extractor.sample_metadata import SampleMetadata


def compose_cid_nl_chromatographic_support(
    *,
    sample: SampleMetadata,
    scan_support_refs: tuple[DdaCidNlPeakSupport, ...],
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
) -> CidNlChromatographicSupport:
    """Compose repeated strict scan support into one physical-peak event."""

    supports = _validated_scan_supports(
        scan_support_refs,
        sample=sample,
        evidence_refs=evidence_refs,
    )
    return _support_from_scans(
        sample=sample, supports=supports, evidence_refs=evidence_refs
    )


def _support_from_scans(
    *,
    sample: SampleMetadata,
    supports: tuple[DdaCidNlPeakSupport, ...],
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
) -> CidNlChromatographicSupport:
    first = supports[0]
    loss_by_observation = {ref: ref.configured_neutral_loss_da for ref in evidence_refs}
    members = tuple(
        sorted(
            (
                DdaCidNlScanSupportMember(
                    support=support,
                    configured_neutral_loss_da=loss_by_observation.get(
                        support.evidence
                    ),
                )
                for support in supports
            ),
            key=_member_sort_key,
        )
    )
    return CidNlChromatographicSupport(
        sample_key=sample.sample_name,
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
    sample: SampleMetadata,
    evidence_refs: tuple[CidNlEvidenceObservation, ...] = (),
) -> tuple[DdaCidNlPeakSupport, ...]:
    if not isinstance(scan_support_refs, tuple) or not scan_support_refs:
        raise CidNlChromatographicSupportContractError(
            "missing strict DDA scan support"
        )
    if any(
        not isinstance(reference, DdaCidNlPeakSupport)
        for reference in scan_support_refs
    ):
        raise CidNlChromatographicSupportContractError(
            "scan_support_refs must contain owner-backed DDA peak support"
        )
    supports = scan_support_refs
    loss_by_observation = {}
    if evidence_refs:
        for evidence in evidence_refs:
            loss_by_observation[evidence] = evidence.configured_neutral_loss_da
        if len(loss_by_observation) != len(evidence_refs) or set(
            loss_by_observation
        ) != {support.evidence for support in supports}:
            raise CidNlChromatographicSupportContractError(
                "measured loss evidence must exactly close scan supports"
            )
    for support in supports:
        if support.sample_key != sample.sample_name:
            raise CidNlChromatographicSupportContractError(
                "DDA scan support references a different sample"
            )
    scan_numbers = tuple(
        (
            support.dda_scan_number,
            loss_by_observation.get(support.evidence),
        )
        for support in supports
    )
    if len(set(scan_numbers)) != len(scan_numbers):
        raise CidNlChromatographicSupportContractError(
            "duplicate DDA scan support cannot multiply one event"
        )
    if any(
        support.peak_observation != supports[0].peak_observation
        for support in supports[1:]
    ):
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references more than one physical MS1 peak"
        )
    if any(
        support.candidate_peaks != supports[0].candidate_peaks
        for support in supports[1:]
    ):
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references a different candidate universe"
        )
    if len({support.policy for support in supports}) != 1:
        raise CidNlChromatographicSupportContractError(
            "DDA scan support references a different support policy"
        )
    if any(support.acquisition_mode != ACQUISITION_MODE for support in supports):
        raise CidNlChromatographicSupportContractError(
            f"DDA scan support acquisition mode must be {ACQUISITION_MODE!r}"
        )
    return tuple(
        sorted(
            supports,
            key=lambda support: (
                support.dda_scan_number,
                loss_by_observation.get(support.evidence) or 0.0,
            ),
        )
    )


__all__ = [
    "CidNlChromatographicSupport",
    "CidNlChromatographicSupportContractError",
    "DdaCidNlScanSupportMember",
    "RELATIONSHIP",
    "compose_cid_nl_chromatographic_support",
]
