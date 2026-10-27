"""Owner-backed identity composition for the existing ``PeakHypothesis``.

This module does not define another hypothesis model.  It composes already
owner-minted physical observations and evidence refs into the existing model,
without selecting a peak or granting product authority.
"""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    CidNlChromatographicSupportRef,
)
from xic_extractor.peak_detection.hypotheses import (
    OWNER_BACKED_ANALYSIS_MODE,
    AuditTrail,
    EvidenceVector,
    PeakHypothesis,
)
from xic_extractor.peak_detection.observations import (
    PeakObservationContractError,
    PeakObservationRef,
    validate_peak_observation_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

HYPOTHESIS_METHOD_DDA_CID_NL = "dda_cid_nl"
HYPOTHESIS_METHOD_MS1_OBSERVATION = "ms1_physical_observation"
ANALYSIS_MODE_WORKFLOW_NEUTRAL = OWNER_BACKED_ANALYSIS_MODE


class PeakHypothesisContractError(ValueError):
    """Raised when owner-backed hypothesis references do not close."""


def compose_cid_nl_peak_hypothesis(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> PeakHypothesis:
    """Compose one DDA CID-NL event into the existing hypothesis model."""

    _validate_reference_pair(
        processing_owners=processing_owners,
        sample=sample,
        peak_observation=peak_observation,
        chromatographic_support=chromatographic_support,
    )
    hypothesis_id = "|".join(
        (
            measured_hypothesis_key(HYPOTHESIS_METHOD_DDA_CID_NL, peak_observation),
            *(
                f"{member.support.dda_scan_number}:"
                f"{member.configured_neutral_loss_da!r}"
                for member in chromatographic_support.content.scan_supports
            ),
        )
    )
    return PeakHypothesis(
        hypothesis_id=hypothesis_id,
        trace_group_id="",
        target_label="",
        role="",
        istd_pair="",
        analysis_mode=ANALYSIS_MODE_WORKFLOW_NEUTRAL,
        resolver_mode=peak_observation.generation_provenance.resolver_mode,
        integration=peak_observation.content.integration,
        evidence=EvidenceVector(
            cid_nl_chromatographic_support_ref=chromatographic_support,
        ),
        audit=AuditTrail(
            proposal_sources=peak_observation.generation_provenance.proposal_sources,
        ),
        hypothesis_method=HYPOTHESIS_METHOD_DDA_CID_NL,
        peak_observation_refs=(peak_observation,),
    )


def compose_ms1_observation_peak_hypothesis(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    peak_observation: PeakObservationRef,
) -> PeakHypothesis:
    """Compose one physical MS1 observation without training evidence."""

    _validate_observation_reference(
        processing_owners=processing_owners,
        sample=sample,
        peak_observation=peak_observation,
    )
    hypothesis_id = measured_hypothesis_key(
        HYPOTHESIS_METHOD_MS1_OBSERVATION, peak_observation
    )
    return PeakHypothesis(
        hypothesis_id=hypothesis_id,
        trace_group_id="",
        target_label="",
        role="",
        istd_pair="",
        analysis_mode=ANALYSIS_MODE_WORKFLOW_NEUTRAL,
        resolver_mode=peak_observation.generation_provenance.resolver_mode,
        integration=peak_observation.content.integration,
        evidence=EvidenceVector(),
        audit=AuditTrail(
            proposal_sources=peak_observation.generation_provenance.proposal_sources,
        ),
        hypothesis_method=HYPOTHESIS_METHOD_MS1_OBSERVATION,
        peak_observation_refs=(peak_observation,),
    )


def _validate_observation_reference(
    *,
    processing_owners: object,
    sample: object,
    peak_observation: object,
) -> None:
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise PeakHypothesisContractError(
            "processing_owners must be a validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise PeakHypothesisContractError(
            "sample must be an owner-resolved canonical sample"
        )
    if not isinstance(peak_observation, PeakObservationRef):
        raise PeakHypothesisContractError(
            "peak observation must be an owner-minted PeakObservationRef"
        )
    try:
        validate_peak_observation_binding(
            peak_observation,
            processing_owners=processing_owners,
            sample=sample,
        )
    except PeakObservationContractError as exc:
        raise PeakHypothesisContractError(str(exc)) from exc


def measured_hypothesis_key(method: str, peak_observation: PeakObservationRef) -> str:
    """In-process key from the method and the measured selected MS1 peak.

    DDA hypotheses append their supporting scan numbers, so two support
    events on one physical peak stay distinct without hashing their content.
    """

    content = peak_observation.content
    return "|".join(
        (
            content.sample_key,
            method,
            repr(content.mz),
            repr(content.integration.rt_apex_min),
            repr(content.integration.height_raw),
            repr(content.rt[0]),
            repr(content.rt[-1]),
            str(len(content.rt)),
        )
    )


def _validate_reference_pair(
    *,
    processing_owners: object,
    sample: object,
    peak_observation: object,
    chromatographic_support: object,
) -> None:
    if not isinstance(peak_observation, PeakObservationRef):
        raise PeakHypothesisContractError(
            "peak observation must be an owner-minted PeakObservationRef"
        )
    if not isinstance(
        chromatographic_support,
        CidNlChromatographicSupportRef,
    ):
        raise PeakHypothesisContractError(
            "chromatographic support must be an owner-minted reference"
        )
    if not isinstance(processing_owners, ProcessingOwnerReferences):
        raise PeakHypothesisContractError(
            "processing_owners must be a validated ProcessingOwnerReferences"
        )
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise PeakHypothesisContractError(
            "sample must be an owner-resolved canonical sample"
        )
    try:
        validate_peak_observation_binding(
            peak_observation,
            processing_owners=processing_owners,
            sample=sample,
        )
    except PeakObservationContractError as exc:
        raise PeakHypothesisContractError(str(exc)) from exc
    _validate_reference_content_pair(peak_observation, chromatographic_support)
    if chromatographic_support.content.sample_key != sample.identity.sample_key:
        raise PeakHypothesisContractError(
            "chromatographic support references a different sample"
        )


def _validate_reference_content_pair(
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> None:
    if (
        peak_observation.content.sample_key
        != chromatographic_support.content.sample_key
    ):
        raise PeakHypothesisContractError(
            "peak observation and chromatographic support reference different samples"
        )
    if chromatographic_support.content.peak_observation != peak_observation.content:
        raise PeakHypothesisContractError(
            "chromatographic support references a different physical MS1 peak"
        )


__all__ = [
    "HYPOTHESIS_METHOD_DDA_CID_NL",
    "HYPOTHESIS_METHOD_MS1_OBSERVATION",
    "PeakHypothesisContractError",
    "compose_cid_nl_peak_hypothesis",
    "compose_ms1_observation_peak_hypothesis",
    "measured_hypothesis_key",
]
