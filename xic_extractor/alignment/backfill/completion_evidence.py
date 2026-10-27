"""Exact completed-empty Backfill evidence and its canonical artifact payload."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from xic_extractor.acquisition_contract import TraceCoordinate
from xic_extractor.alignment.cross_sample.peak_groups import CrossSampleGroupIdentity
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    mint_artifact_semantic_contract_ref,
    mint_evidence_artifact_ref,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_COMPLETED_BACKFILL_CONTRACT = mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_real_data_runtime",
    semantic_role="owner_backfill_completed_backfill_projection",
    schema_version="owner_backfill_completed_physical_query_projection",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf",
)


@dataclass(frozen=True, slots=True)
class BackfillCompletionProjection:
    """Runtime-issued completion evidence; it makes no physical absence claim."""

    processing_owners: ProcessingOwnerReferences
    sample: ResolvedCanonicalSampleIdentity
    group_identity: CrossSampleGroupIdentity
    group_center_mz: float
    group_center_rt: float
    query_rt: float
    query_rt_basis: str
    source_result_artifact: EvidenceArtifactRef
    initial_assessment_artifact: EvidenceArtifactRef
    backfill_assessment: CidNlMethodAssessmentRef
    trace_coordinate: TraceCoordinate
    acquisition_sha256: str
    artifact: EvidenceArtifactRef


def mint_backfill_completion_projection(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_identity: CrossSampleGroupIdentity,
    group_center_mz: float,
    group_center_rt: float,
    query_rt: float,
    query_rt_basis: str,
    source_result_artifact: EvidenceArtifactRef,
    initial_assessment_artifact: EvidenceArtifactRef,
    backfill_assessment: CidNlMethodAssessmentRef,
    trace_coordinate: TraceCoordinate,
    acquisition_sha256: str,
) -> BackfillCompletionProjection:
    """Mint one verified completion projection and its canonical evidence artifact."""
    payload_bytes = _completed_backfill_projection_bytes(
        processing_owners=processing_owners,
        sample=sample,
        group_identity=group_identity,
        group_center_mz=group_center_mz,
        group_center_rt=group_center_rt,
        query_rt=query_rt,
        query_rt_basis=query_rt_basis,
        source_result_artifact=source_result_artifact,
        initial_assessment_artifact=initial_assessment_artifact,
        backfill_assessment=backfill_assessment,
        trace_coordinate=trace_coordinate,
        acquisition_sha256=acquisition_sha256,
    )
    artifact = mint_evidence_artifact_ref(
        producer_processing_request_id=processing_owners.request.processing_request_id,
        producer_processing_attempt_id=processing_owners.attempt.processing_attempt_id,
        semantic_contract=_COMPLETED_BACKFILL_CONTRACT,
        payload_bytes=payload_bytes,
        upstream_artifacts=(
            source_result_artifact,
            backfill_assessment.artifact,
        ),
    )
    return BackfillCompletionProjection(
        processing_owners=processing_owners,
        sample=sample,
        group_identity=group_identity,
        group_center_mz=group_center_mz,
        group_center_rt=group_center_rt,
        query_rt=query_rt,
        query_rt_basis=query_rt_basis,
        source_result_artifact=source_result_artifact,
        initial_assessment_artifact=initial_assessment_artifact,
        backfill_assessment=backfill_assessment,
        trace_coordinate=trace_coordinate,
        acquisition_sha256=acquisition_sha256,
        artifact=artifact,
    )


def _completed_backfill_projection_bytes(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_identity: CrossSampleGroupIdentity,
    group_center_mz: float,
    group_center_rt: float,
    query_rt: float,
    query_rt_basis: str,
    source_result_artifact: EvidenceArtifactRef,
    initial_assessment_artifact: EvidenceArtifactRef,
    backfill_assessment: CidNlMethodAssessmentRef,
    trace_coordinate: TraceCoordinate,
    acquisition_sha256: str,
) -> bytes:
    return _canonical_proof_bytes(
        {
            "schema": "owner_backfill_completed_physical_query_projection",
            "processing_request_id": processing_owners.request.processing_request_id,
            "processing_attempt_id": processing_owners.attempt.processing_attempt_id,
            "sample_key": sample.identity.sample_key,
            "group_id": group_identity.group_hypothesis_id,
            "group_basis": group_identity.basis_bytes.hex(),
            "group_center_mz": group_center_mz,
            "group_center_rt": group_center_rt,
            "query_rt": query_rt,
            "query_rt_basis": query_rt_basis,
            "source_result_artifact_id": source_result_artifact.evidence_artifact_id,
            "initial_assessment_artifact_id": (
                initial_assessment_artifact.evidence_artifact_id
            ),
            "backfill_assessment_artifact_id": (
                backfill_assessment.artifact.evidence_artifact_id
            ),
            "trace_coordinate": asdict(trace_coordinate),
            "acquisition_sha256": acquisition_sha256,
        }
    )


def _canonical_proof_bytes(basis: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            basis,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
