"""Owner-backed identity composition for the existing ``PeakHypothesis``.

This module does not define another hypothesis model.  It composes already
owner-minted physical observations and evidence refs into the existing model,
without selecting a peak or granting product authority.
"""

from __future__ import annotations

import hashlib
import json

from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
)
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

SCHEMA_VERSION = "peak_hypothesis"
HYPOTHESIS_METHOD_DDA_CID_NL = "dda_cid_nl"
HYPOTHESIS_METHOD_MS1_OBSERVATION = "ms1_physical_observation"
ANALYSIS_MODE_WORKFLOW_NEUTRAL = OWNER_BACKED_ANALYSIS_MODE
ARTIFACT_SEMANTIC_ROLE = "owner_backed_peak_hypothesis"
VALIDATION_STATUS = "diagnostic_only"
MEDIA_TYPE = "application/json"
SERIALIZATION_FORMAT = "canonical-json-utf8-lf"

_HYPOTHESIS_ID_PREFIX = OWNER_BACKED_HYPOTHESIS_ID_PREFIX
_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.peak_hypothesis_identity",
    semantic_role=ARTIFACT_SEMANTIC_ROLE,
    schema_version=SCHEMA_VERSION,
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)
_OBSERVATION_ARTIFACT_SEMANTIC_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.peak_detection.peak_hypothesis_identity",
    semantic_role="owner_backed_ms1_observation_hypothesis",
    schema_version="peak_hypothesis_ms1_observation",
    validation_status=VALIDATION_STATUS,
    media_type=MEDIA_TYPE,
    serialization_format=SERIALIZATION_FORMAT,
)


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
    payload_bytes = canonical_owner_backed_peak_hypothesis_bytes(
        hypothesis_id=hypothesis_id,
        peak_observation=peak_observation,
        chromatographic_support=chromatographic_support,
    )
    artifact = mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=payload_bytes,
        upstream_artifacts=(
            peak_observation.artifact,
            chromatographic_support.artifact,
        ),
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
        artifact=artifact,
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
    artifact = mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_OBSERVATION_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=canonical_ms1_observation_peak_hypothesis_bytes(
            hypothesis_id=hypothesis_id,
            peak_observation=peak_observation,
        ),
        upstream_artifacts=(peak_observation.artifact,),
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
        artifact=artifact,
    )


def validate_owner_backed_peak_hypothesis_instance(
    hypothesis: PeakHypothesis,
) -> None:
    """Validate the immutable owner-backed branch of ``PeakHypothesis``."""

    if hypothesis.hypothesis_method == HYPOTHESIS_METHOD_MS1_OBSERVATION:
        _validate_ms1_observation_hypothesis_instance(hypothesis)
        return
    if hypothesis.hypothesis_method != HYPOTHESIS_METHOD_DDA_CID_NL:
        raise PeakHypothesisContractError(
            f"hypothesis_method must be {HYPOTHESIS_METHOD_DDA_CID_NL!r}"
        )
    if (
        hypothesis.trace_group_id
        or hypothesis.target_label
        or hypothesis.role
        or hypothesis.istd_pair
    ):
        raise PeakHypothesisContractError(
            "owner-backed CID-NL hypotheses cannot mint Targeted context"
        )
    if hypothesis.analysis_mode != ANALYSIS_MODE_WORKFLOW_NEUTRAL:
        raise PeakHypothesisContractError(
            "owner-backed CID-NL hypothesis must remain workflow neutral"
        )
    if (
        not isinstance(hypothesis.peak_observation_refs, tuple)
        or len(hypothesis.peak_observation_refs) != 1
        or not isinstance(hypothesis.peak_observation_refs[0], PeakObservationRef)
    ):
        raise PeakHypothesisContractError(
            "CID-NL hypothesis requires exactly one owner-minted peak observation"
        )
    peak_observation = hypothesis.peak_observation_refs[0]
    chromatographic_support = hypothesis.evidence.cid_nl_chromatographic_support_ref
    if not isinstance(
        chromatographic_support,
        CidNlChromatographicSupportRef,
    ):
        raise PeakHypothesisContractError(
            "CID-NL hypothesis requires owner-minted chromatographic support"
        )
    _validate_reference_content_pair(peak_observation, chromatographic_support)
    if hypothesis.integration != peak_observation.content.integration:
        raise PeakHypothesisContractError(
            "hypothesis integration must be the referenced physical observation"
        )
    if hypothesis.resolver_mode != peak_observation.generation_provenance.resolver_mode:
        raise PeakHypothesisContractError(
            "hypothesis resolver_mode mismatches observation provenance"
        )
    if hypothesis.evidence != EvidenceVector(
        cid_nl_chromatographic_support_ref=chromatographic_support,
    ):
        raise PeakHypothesisContractError(
            "owner-backed CID-NL evidence cannot carry selection projections"
        )
    if hypothesis.audit != AuditTrail(
        proposal_sources=peak_observation.generation_provenance.proposal_sources,
    ):
        raise PeakHypothesisContractError(
            "owner-backed CID-NL audit cannot carry selection authority"
        )
    expected_hypothesis_id = _hypothesis_id(
        peak_observation,
        chromatographic_support,
    )
    if hypothesis.hypothesis_id != expected_hypothesis_id:
        raise PeakHypothesisContractError(
            "hypothesis_id mismatches owner-backed content"
        )
    _validate_hypothesis_artifact(
        hypothesis,
        peak_observation=peak_observation,
        chromatographic_support=chromatographic_support,
    )


def _validate_ms1_observation_hypothesis_instance(
    hypothesis: PeakHypothesis,
) -> None:
    if (
        hypothesis.trace_group_id
        or hypothesis.target_label
        or hypothesis.role
        or hypothesis.istd_pair
    ):
        raise PeakHypothesisContractError(
            "owner-backed MS1 hypotheses cannot mint Targeted context"
        )
    if hypothesis.analysis_mode != ANALYSIS_MODE_WORKFLOW_NEUTRAL:
        raise PeakHypothesisContractError(
            "owner-backed MS1 hypothesis must remain workflow neutral"
        )
    if (
        not isinstance(hypothesis.peak_observation_refs, tuple)
        or len(hypothesis.peak_observation_refs) != 1
        or not isinstance(hypothesis.peak_observation_refs[0], PeakObservationRef)
    ):
        raise PeakHypothesisContractError(
            "MS1 hypothesis requires exactly one owner-minted peak observation"
        )
    observation = hypothesis.peak_observation_refs[0]
    if hypothesis.integration != observation.content.integration:
        raise PeakHypothesisContractError(
            "hypothesis integration must be the referenced physical observation"
        )
    if hypothesis.resolver_mode != observation.generation_provenance.resolver_mode:
        raise PeakHypothesisContractError(
            "hypothesis resolver_mode mismatches observation provenance"
        )
    if hypothesis.evidence != EvidenceVector():
        raise PeakHypothesisContractError(
            "observation-only hypothesis cannot carry training evidence"
        )
    if hypothesis.audit != AuditTrail(
        proposal_sources=observation.generation_provenance.proposal_sources,
    ):
        raise PeakHypothesisContractError(
            "observation-only hypothesis audit cannot carry selection authority"
        )
    if hypothesis.hypothesis_id != _observation_hypothesis_id(observation):
        raise PeakHypothesisContractError(
            "hypothesis_id mismatches owner-backed MS1 observation"
        )
    _validate_observation_hypothesis_artifact(hypothesis, observation)


def canonical_owner_backed_peak_hypothesis_bytes(
    *,
    hypothesis_id: str,
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> bytes:
    """Serialize content identity plus exact upstream content provenance."""

    expected_id = _hypothesis_id(peak_observation, chromatographic_support)
    if hypothesis_id != expected_id:
        raise PeakHypothesisContractError(
            "hypothesis_id mismatches canonical owner-backed content"
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "validation_status": VALIDATION_STATUS,
        "hypothesis_id": hypothesis_id,
        "content": _hypothesis_content_basis(
            peak_observation,
            chromatographic_support,
        ),
        "exact_upstream_content": {
            "peak_observation_artifact_content_id": (
                peak_observation.artifact.content_identity.artifact_content_id
            ),
            "cid_nl_chromatographic_support_artifact_content_id": (
                chromatographic_support.artifact.content_identity.artifact_content_id
            ),
        },
    }
    return _canonical_json_bytes(payload)


def canonical_ms1_observation_peak_hypothesis_bytes(
    *,
    hypothesis_id: str,
    peak_observation: PeakObservationRef,
) -> bytes:
    expected_id = _observation_hypothesis_id(peak_observation)
    if hypothesis_id != expected_id:
        raise PeakHypothesisContractError(
            "hypothesis_id mismatches canonical MS1 observation content"
        )
    return _canonical_json_bytes(
        {
            "schema_version": "peak_hypothesis_ms1_observation",
            "validation_status": VALIDATION_STATUS,
            "hypothesis_id": hypothesis_id,
            "content": _observation_hypothesis_content_basis(peak_observation),
            "exact_upstream_content": {
                "peak_observation_artifact_content_id": (
                    peak_observation.artifact.content_identity.artifact_content_id
                ),
            },
        }
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


def _validate_observation_hypothesis_artifact(
    hypothesis: PeakHypothesis,
    peak_observation: PeakObservationRef,
) -> None:
    artifact = hypothesis.artifact
    if not isinstance(artifact, EvidenceArtifactRef):
        raise PeakHypothesisContractError(
            "owner-backed hypothesis requires an immutable evidence artifact"
        )
    if artifact.semantic_contract != _OBSERVATION_ARTIFACT_SEMANTIC_CONTRACT:
        raise PeakHypothesisContractError(
            "hypothesis artifact semantic contract is not MS1 observation-owned"
        )
    if artifact.upstream_artifact_ids != (
        peak_observation.artifact.evidence_artifact_id,
    ):
        raise PeakHypothesisContractError(
            "MS1 hypothesis artifact does not close its physical observation"
        )
    if artifact.producer_processing_request_id != (
        peak_observation.artifact.producer_processing_request_id
    ) or artifact.producer_processing_attempt_id != (
        peak_observation.artifact.producer_processing_attempt_id
    ):
        raise PeakHypothesisContractError(
            "MS1 hypothesis artifact references a different processing owner"
        )


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
    request_ids = {
        peak_observation.artifact.producer_processing_request_id,
        chromatographic_support.artifact.producer_processing_request_id,
    }
    if len(request_ids) != 1:
        raise PeakHypothesisContractError(
            "owner-backed references use different processing requests"
        )
    attempt_ids = {
        peak_observation.artifact.producer_processing_attempt_id,
        chromatographic_support.artifact.producer_processing_attempt_id,
    }
    if len(attempt_ids) != 1:
        raise PeakHypothesisContractError(
            "owner-backed references use different processing attempts"
        )


def _validate_hypothesis_artifact(
    hypothesis: PeakHypothesis,
    *,
    peak_observation: PeakObservationRef,
    chromatographic_support: CidNlChromatographicSupportRef,
) -> None:
    artifact = hypothesis.artifact
    if not isinstance(artifact, EvidenceArtifactRef):
        raise PeakHypothesisContractError(
            "owner-backed hypothesis requires an immutable evidence artifact"
        )
    if artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
        raise PeakHypothesisContractError(
            "hypothesis artifact semantic contract is not owner-backed"
        )
    expected_upstreams = tuple(
        sorted(
            (
                peak_observation.artifact.evidence_artifact_id,
                chromatographic_support.artifact.evidence_artifact_id,
            )
        )
    )
    if artifact.upstream_artifact_ids != expected_upstreams:
        raise PeakHypothesisContractError(
            "hypothesis artifact does not close exact upstream references"
        )
    if artifact.producer_processing_request_id != (
        peak_observation.artifact.producer_processing_request_id
    ) or artifact.producer_processing_attempt_id != (
        peak_observation.artifact.producer_processing_attempt_id
    ):
        raise PeakHypothesisContractError(
            "hypothesis artifact references a different processing owner"
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
    "ARTIFACT_SEMANTIC_ROLE",
    "HYPOTHESIS_METHOD_DDA_CID_NL",
    "HYPOTHESIS_METHOD_MS1_OBSERVATION",
    "PeakHypothesisContractError",
    "SCHEMA_VERSION",
    "canonical_owner_backed_peak_hypothesis_bytes",
    "canonical_ms1_observation_peak_hypothesis_bytes",
    "compose_cid_nl_peak_hypothesis",
    "compose_ms1_observation_peak_hypothesis",
]
