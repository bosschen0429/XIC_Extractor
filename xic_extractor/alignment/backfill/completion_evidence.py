"""Exact completed-empty Backfill evidence and its canonical artifact payload."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from xic_extractor.acquisition_contract import TraceCoordinate
from xic_extractor.alignment.cross_sample_peak_groups import CrossSampleGroupIdentity
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    validate_cid_nl_method_assessment_binding,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

_COMPLETED_BACKFILL_CONTRACT = _mint_artifact_semantic_contract_ref(
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
    source_result_artifact: EvidenceArtifactRef
    initial_assessment_artifact: EvidenceArtifactRef
    backfill_assessment: CidNlMethodAssessmentRef
    trace_coordinate: TraceCoordinate
    acquisition_sha256: str
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        owners = self.processing_owners
        if not isinstance(owners, ProcessingOwnerReferences) or not any(
            self.sample is sample for sample in owners.sample_resolutions
        ):
            raise ValueError("completed backfill crossed its exact sample owners")
        replace(self.group_identity)
        replace(self.trace_coordinate)
        if self.group_identity.processing_request_id != (
            owners.request.processing_request_id
        ):
            raise ValueError("completed backfill crossed its group request")
        assessment = self.backfill_assessment
        validate_cid_nl_method_assessment_binding(
            assessment,
            processing_owners=owners,
            sample=self.sample,
            method_subject=assessment.content.method_subject,
        )
        receipt = assessment.content.evaluation_receipt
        request = self.trace_coordinate.request
        subject = assessment.content.method_subject
        if (
            assessment.content.detector_invocation_id != "untargeted-cid-nl-backfill"
            or assessment.content.technical_state != "evaluated"
            or assessment.content.method_result != "HOLD"
            or assessment.candidate_peaks
            or not receipt.search_completed
            or not receipt.acquisition_supported
            or request.mz != self.group_center_mz
            or request.ppm_tol != subject.precursor_tolerance_ppm
            or request.rt_min != receipt.query_rt_min
            or request.rt_max != receipt.query_rt_max
            or not request.rt_min <= self.group_center_rt <= request.rt_max
            or self.trace_coordinate.acquisition_source_id
            != assessment.content.acquisition_source_id
        ):
            raise ValueError("completed backfill does not close an exact empty epoch")
        if len(self.acquisition_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.acquisition_sha256
        ):
            raise ValueError("completed backfill requires its acquisition digest")
        for artifact in (
            self.source_result_artifact,
            self.initial_assessment_artifact,
            assessment.artifact,
            self.artifact,
        ):
            replace(artifact)
            if (
                artifact.producer_processing_request_id
                != owners.request.processing_request_id
                or artifact.producer_processing_attempt_id
                != owners.attempt.processing_attempt_id
            ):
                raise ValueError("completed backfill crossed its producer attempt")
        if (
            self.source_result_artifact.semantic_contract.owner_namespace
            != "xic_extractor.untargeted_cid_nl_real_data_runtime"
            or self.source_result_artifact.semantic_contract.semantic_role
            != "untargeted_cid_nl_real_data_sample_result"
            or self.initial_assessment_artifact.evidence_artifact_id
            not in self.source_result_artifact.upstream_artifact_ids
            or assessment.artifact.evidence_artifact_id
            not in self.source_result_artifact.upstream_artifact_ids
            or self.initial_assessment_artifact == assessment.artifact
            or self.artifact.semantic_contract != _COMPLETED_BACKFILL_CONTRACT
            or self.artifact.upstream_artifact_ids
            != tuple(
                sorted(
                    {
                        self.source_result_artifact.evidence_artifact_id,
                        assessment.artifact.evidence_artifact_id,
                    }
                )
            )
        ):
            raise ValueError("completed backfill lost its runtime artifact closure")
        verify_evidence_artifact_payload(
            self.artifact,
            canonical_backfill_completion_bytes(self),
        )


def canonical_backfill_completion_bytes(
    projection: BackfillCompletionProjection,
) -> bytes:
    return _completed_backfill_projection_bytes(
        processing_owners=projection.processing_owners,
        sample=projection.sample,
        group_identity=projection.group_identity,
        group_center_mz=projection.group_center_mz,
        group_center_rt=projection.group_center_rt,
        source_result_artifact=projection.source_result_artifact,
        initial_assessment_artifact=projection.initial_assessment_artifact,
        backfill_assessment=projection.backfill_assessment,
        trace_coordinate=projection.trace_coordinate,
        acquisition_sha256=projection.acquisition_sha256,
    )


def _completed_backfill_projection_bytes(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample: ResolvedCanonicalSampleIdentity,
    group_identity: CrossSampleGroupIdentity,
    group_center_mz: float,
    group_center_rt: float,
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
            "sample_identity_id": sample.identity.sample_identity_id,
            "sample_snapshot_id": sample.snapshot.snapshot_id,
            "sample_snapshot_sha256": sample.snapshot.content_sha256,
            "group_id": group_identity.group_hypothesis_id,
            "group_basis": group_identity.basis_bytes.hex(),
            "group_center_mz": group_center_mz,
            "group_center_rt": group_center_rt,
            "source_result_artifact_id": source_result_artifact.evidence_artifact_id,
            "initial_assessment_artifact_id": (
                initial_assessment_artifact.evidence_artifact_id
            ),
            "backfill_assessment_artifact_id": (
                backfill_assessment.artifact.evidence_artifact_id
            ),
            "trace_coordinate_id": trace_coordinate.trace_coordinate_id,
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
