"""Owner-backed identity composition for the existing ``PeakHypothesis``.

This module does not define another hypothesis model.  It composes already
owner-minted physical observations and evidence refs into the existing model,
without selecting a peak or granting product authority.
"""

from __future__ import annotations

from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    CidNlChromatographicSupport,
)
from xic_extractor.peak_detection.hypotheses import (
    OWNER_BACKED_ANALYSIS_MODE,
    AuditTrail,
    EvidenceVector,
    PeakHypothesis,
)
from xic_extractor.peak_detection.observations import (
    PeakObservation,
)
from xic_extractor.sample_metadata import SampleMetadata

HYPOTHESIS_METHOD_DDA_CID_NL = "dda_cid_nl"
HYPOTHESIS_METHOD_MS1_OBSERVATION = "ms1_physical_observation"
ANALYSIS_MODE_WORKFLOW_NEUTRAL = OWNER_BACKED_ANALYSIS_MODE


class PeakHypothesisContractError(ValueError):
    """Raised when owner-backed hypothesis references do not close."""


def compose_cid_nl_peak_hypothesis(
    *,
    sample: SampleMetadata,
    peak_observation: PeakObservation,
    chromatographic_support: CidNlChromatographicSupport,
) -> PeakHypothesis:
    """Compose one DDA CID-NL event into the existing hypothesis model."""

    _validate_reference_pair(
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
                for member in chromatographic_support.scan_supports
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
        integration=peak_observation.integration,
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
    peak_observation: PeakObservation,
) -> PeakHypothesis:
    """Compose one physical MS1 observation without training evidence."""

    _validate_observation_reference(peak_observation=peak_observation)
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
        integration=peak_observation.integration,
        evidence=EvidenceVector(),
        audit=AuditTrail(
            proposal_sources=peak_observation.generation_provenance.proposal_sources,
        ),
        hypothesis_method=HYPOTHESIS_METHOD_MS1_OBSERVATION,
        peak_observation_refs=(peak_observation,),
    )


def _validate_observation_reference(
    *,
    peak_observation: object,
) -> None:
    if not isinstance(peak_observation, PeakObservation):
        raise PeakHypothesisContractError(
            "peak observation must be an owner-minted PeakObservation"
        )


def measured_hypothesis_key(method: str, peak_observation: PeakObservation) -> str:
    """In-process key from the method and the measured selected MS1 peak.

    DDA hypotheses append their supporting scan numbers, so two support
    events on one physical peak stay distinct without hashing their content.
    """

    peak = peak_observation
    return "|".join(
        (
            peak.sample_key,
            method,
            repr(peak.mz),
            repr(peak.integration.rt_apex_min),
            repr(peak.integration.height_raw),
            repr(peak.rt[0]),
            repr(peak.rt[-1]),
            str(len(peak.rt)),
        )
    )


def _validate_reference_pair(
    *,
    sample: SampleMetadata,
    peak_observation: object,
    chromatographic_support: object,
) -> None:
    if not isinstance(peak_observation, PeakObservation):
        raise PeakHypothesisContractError(
            "peak observation must be an owner-minted PeakObservation"
        )
    if not isinstance(
        chromatographic_support,
        CidNlChromatographicSupport,
    ):
        raise PeakHypothesisContractError(
            "chromatographic support must be an owner-minted reference"
        )
    _validate_reference_content_pair(peak_observation, chromatographic_support)
    if chromatographic_support.sample_key != sample.sample_name:
        raise PeakHypothesisContractError(
            "chromatographic support references a different sample"
        )


def _validate_reference_content_pair(
    peak_observation: PeakObservation,
    chromatographic_support: CidNlChromatographicSupport,
) -> None:
    if peak_observation.sample_key != chromatographic_support.sample_key:
        raise PeakHypothesisContractError(
            "peak observation and chromatographic support reference different samples"
        )
    if chromatographic_support.peak_observation != peak_observation:
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
