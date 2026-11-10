"""Resolve one exact DDA/ddMS2 CID-NL event to one physical MS1 peak."""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_evidence import (
    CidNlEvidenceObservation,
)
from xic_extractor.peak_detection.cid_nl_peak_support_models import (
    RELATIONSHIP,
    DdaCidNlPeakSupport,
    DdaCidNlPeakSupportContractError,
    DdaCidNlPeakSupportPolicy,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.sample_metadata import SampleMetadata


def resolve_dda_cid_nl_peak_support(
    *,
    sample: SampleMetadata,
    canonical_evidence: CidNlEvidenceObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupport:
    """Resolve one DDA event to exactly one supplied physical MS1 observation."""

    if not isinstance(policy, DdaCidNlPeakSupportPolicy):
        raise DdaCidNlPeakSupportContractError(
            "policy must be owner-normalized DdaCidNlPeakSupportPolicy"
        )
    _validate_candidate_peaks(candidate_peaks)

    observation = canonical_evidence
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    if precursor.reference_mz is None or precursor.status not in {
        "compatible",
        "neutral_loss_mismatch",
    }:
        raise DdaCidNlPeakSupportContractError(
            "CID-NL evidence lacks independent precursor isolation support"
        )
    if not policy.accepts_precursor(precursor):
        raise DdaCidNlPeakSupportContractError(
            "CID-NL evidence does not satisfy the strict neutral-loss tolerance "
            "or precursor reference requirement"
        )
    matching_peaks = tuple(
        peak
        for peak in candidate_peaks
        if _matches_observation(
            canonical_evidence,
            peak,
            policy=policy,
        )
    )
    if not matching_peaks:
        raise DdaCidNlPeakSupportContractError(
            "missing physical MS1 peak matching the DDA CID-NL event"
        )
    if len(matching_peaks) != 1:
        raise DdaCidNlPeakSupportContractError(
            "DDA CID-NL event matches ambiguous physical MS1 peak observations"
        )

    return _support(
        sample=sample,
        canonical_evidence=canonical_evidence,
        peak_observation=matching_peaks[0],
        candidate_peaks=candidate_peaks,
        policy=policy,
    )


def _support(
    *,
    sample: SampleMetadata,
    canonical_evidence: CidNlEvidenceObservation,
    peak_observation: PeakObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupport:
    observation = canonical_evidence
    integration = peak_observation.integration
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    assert precursor.reference_mz is not None
    assert precursor.observed_loss_error_ppm is not None
    return DdaCidNlPeakSupport(
        sample_key=sample.sample_name,
        evidence=observation,
        peak_observation=peak_observation,
        candidate_peaks=tuple(
            sorted(candidate_peaks, key=lambda peak: peak.measured_order_key)
        ),
        acquisition_mode=observation.acquisition_mode,
        dda_scan_number=observation.scan_number,
        scan_rt_min=observation.scan_rt_min,
        peak_rt_min=integration.rt_left_min,
        peak_rt_max=integration.rt_right_min,
        precursor_mz=precursor.reference_mz,
        peak_mz=peak_observation.mz,
        precursor_delta_da=precursor.reference_mz - peak_observation.mz,
        observed_loss_error_ppm=precursor.observed_loss_error_ppm,
        observed_product_mz=observation.product_mz,
        configured_neutral_loss_da=observation.configured_neutral_loss_da,
        policy=policy,
        relationship=RELATIONSHIP,
    )


def _matches_observation(
    canonical_evidence: CidNlEvidenceObservation,
    peak_observation: PeakObservation,
    *,
    policy: DdaCidNlPeakSupportPolicy,
) -> bool:
    observation = canonical_evidence
    integration = peak_observation.integration
    precursor = observation.assess_precursor_loss(policy.neutral_loss_tolerance_ppm)
    return (
        policy.accepts_precursor(precursor)
        and precursor.reference_mz is not None
        and integration.rt_left_min <= observation.support_rt_min
        and observation.scan_rt_min <= integration.rt_right_min
        and abs(precursor.reference_mz - peak_observation.mz)
        <= policy.precursor_tolerance_da
    )


def _validate_candidate_peaks(
    candidate_peaks: object,
) -> tuple[PeakObservation, ...]:
    if not isinstance(candidate_peaks, tuple):
        raise DdaCidNlPeakSupportContractError(
            "candidate_peaks must be a tuple of owner-backed observations"
        )
    if not candidate_peaks:
        raise DdaCidNlPeakSupportContractError(
            "missing candidate physical MS1 peak observations"
        )
    if any(not isinstance(peak, PeakObservation) for peak in candidate_peaks):
        raise DdaCidNlPeakSupportContractError(
            "candidate_peaks must contain owner-backed PeakObservation values"
        )
    typed_peaks = candidate_peaks
    keys = tuple(peak.physical_peak_key for peak in typed_peaks)
    if len(set(keys)) != len(keys):
        raise DdaCidNlPeakSupportContractError(
            "candidate physical MS1 peak observations are ambiguous"
        )
    return typed_peaks


__all__ = [
    "DdaCidNlPeakSupport",
    "DdaCidNlPeakSupportContractError",
    "DdaCidNlPeakSupportPolicy",
    "RELATIONSHIP",
    "resolve_dda_cid_nl_peak_support",
]
