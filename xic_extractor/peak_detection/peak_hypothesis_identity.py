"""Owner-backed identity composition for the existing ``PeakHypothesis``.

This module does not define another hypothesis model.  It composes already
owner-minted physical observations and evidence refs into the existing model,
without selecting a peak or granting product authority.
"""

from __future__ import annotations

import hashlib
import json

from xic_extractor.peak_detection.cid_nl_chromatographic_support_models import (
    CidNlChromatographicSupportRef,
)
from xic_extractor.peak_detection.hypotheses import (
    OWNER_BACKED_ANALYSIS_MODE,
    OWNER_BACKED_HYPOTHESIS_ID_PREFIX,
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

_HYPOTHESIS_ID_PREFIX = OWNER_BACKED_HYPOTHESIS_ID_PREFIX


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
    hypothesis_id = _hypothesis_id(peak_observation, chromatographic_support)
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
    hypothesis_id = _observation_hypothesis_id(peak_observation)
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


def _observation_hypothesis_id(peak_observation: PeakObservationRef) -> str:
    digest = hashlib.sha256(
        _canonical_json_bytes(_observation_hypothesis_content_basis(peak_observation))
    ).hexdigest()
    return f"{_HYPOTHESIS_ID_PREFIX}{digest}"


def _observation_hypothesis_content_basis(
    peak_observation: PeakObservationRef,
) -> dict[str, object]:
    return {
        "schema": "peak_hypothesis_ms1_observation_content",
        "hypothesis_method": HYPOTHESIS_METHOD_MS1_OBSERVATION,
        "sample_key": (peak_observation.content.sample_key),
        "peak_observation_content_ids": [
            peak_observation.content.peak_observation_content_id
        ],
        "evidence_artifact_content_ids": [],
    }


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
    artifact = chromatographic_support.artifact
    if (
        artifact.producer_processing_request_id
        != processing_owners.request.processing_request_id
    ):
        raise PeakHypothesisContractError(
            "chromatographic support references a different processing request"
        )
    if (
        artifact.producer_processing_attempt_id
        != processing_owners.attempt.processing_attempt_id
    ):
        raise PeakHypothesisContractError(
            "chromatographic support references a different processing attempt"
        )
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
    if (
        chromatographic_support.content.peak_observation_content_id
        != peak_observation.content.peak_observation_content_id
    ):
        raise PeakHypothesisContractError(
            "chromatographic support references a different physical MS1 peak"
        )


def _hypothesis_id(
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> str:
    digest = hashlib.sha256(
        _canonical_json_bytes(
            _hypothesis_content_basis(
                peak_observation,
                chromatographic_support,
            )
        )
    ).hexdigest()
    return f"{_HYPOTHESIS_ID_PREFIX}{digest}"


def _hypothesis_content_basis(
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> dict[str, object]:
    return {
        "schema": "peak_hypothesis_content",
        "hypothesis_method": HYPOTHESIS_METHOD_DDA_CID_NL,
        "sample_key": (peak_observation.content.sample_key),
        "peak_observation_content_ids": [
            peak_observation.content.peak_observation_content_id
        ],
        "evidence_artifact_content_ids": [
            chromatographic_support.artifact.content_identity.artifact_content_id
        ],
    }


def _canonical_json_bytes(value: object) -> bytes:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PeakHypothesisContractError(
            "owner-backed hypothesis payload must be canonical JSON"
        ) from exc
    return (serialized + "\n").encode("utf-8")


__all__ = [
    "HYPOTHESIS_METHOD_DDA_CID_NL",
    "HYPOTHESIS_METHOD_MS1_OBSERVATION",
    "PeakHypothesisContractError",
    "compose_cid_nl_peak_hypothesis",
    "compose_ms1_observation_peak_hypothesis",
]
