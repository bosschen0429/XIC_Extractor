"""Bind one decided DDA/ddMS2 CID-NL event to its physical MS1 peak."""

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
from xic_extractor.precursor_evidence import PrecursorLossAssessment
from xic_extractor.sample_metadata import SampleMetadata


def bind_dda_cid_nl_peak_support(
    *,
    sample: SampleMetadata,
    evidence: CidNlEvidenceObservation,
    precursor: PrecursorLossAssessment,
    peak_observation: PeakObservation,
    candidate_peaks: tuple[PeakObservation, ...],
    policy: DdaCidNlPeakSupportPolicy,
) -> DdaCidNlPeakSupport:
    """Record a relation answer over the measured-order candidate universe."""

    integration = peak_observation.integration
    assert precursor.reference_mz is not None
    assert precursor.observed_loss_error_ppm is not None
    return DdaCidNlPeakSupport(
        sample_key=sample.sample_name,
        evidence=evidence,
        peak_observation=peak_observation,
        candidate_peaks=candidate_peaks,
        acquisition_mode=evidence.acquisition_mode,
        dda_scan_number=evidence.scan_number,
        scan_rt_min=evidence.scan_rt_min,
        peak_rt_min=integration.rt_left_min,
        peak_rt_max=integration.rt_right_min,
        precursor_mz=precursor.reference_mz,
        peak_mz=peak_observation.mz,
        precursor_delta_da=precursor.reference_mz - peak_observation.mz,
        observed_loss_error_ppm=precursor.observed_loss_error_ppm,
        observed_product_mz=evidence.product_mz,
        configured_neutral_loss_da=evidence.configured_neutral_loss_da,
        policy=policy,
        relationship=RELATIONSHIP,
    )


__all__ = [
    "DdaCidNlPeakSupport",
    "DdaCidNlPeakSupportContractError",
    "DdaCidNlPeakSupportPolicy",
    "RELATIONSHIP",
    "bind_dda_cid_nl_peak_support",
]
