"""Targeted-only synthetic delivery identity candidates; no writer authority."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.processing_identity import (
    ProcessingAttemptIdentity,
    canonical_current_attempt_artifact_closure,
    canonical_evidence_artifact_closure,
    retry_predecessor_attempt_ids,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity
from xic_extractor.validation_primitives import (
    require_lower_sha256,
    require_owner_normalized_text,
)


class DeliveryLineageContractError(ValueError):
    """Raised when synthetic Targeted delivery lineage is incomplete."""


@dataclass(frozen=True, slots=True)
class TargetedDeliveryCellKey:
    delivery_cell_key_id: str
    canonical_sample_identity_id: str
    target_key_id: str

    def __post_init__(self) -> None:
        _required_text(
            self.canonical_sample_identity_id,
            "canonical_sample_identity_id",
        )
        _required_text(self.target_key_id, "target_key_id")
        expected = _typed_id(
            "xic.targeted-delivery-cell.v1:",
            {
                "schema": "targeted_delivery_cell_v1",
                "projection": "targeted",
                "canonical_sample_identity_id": self.canonical_sample_identity_id,
                "target_key_id": self.target_key_id,
            },
        )
        if self.delivery_cell_key_id != expected:
            raise DeliveryLineageContractError(
                "delivery_cell_key_id mismatches owner-backed sample and target"
            )


@dataclass(frozen=True, slots=True)
class TargetedDeliveryRevisionCandidate:
    delivery_revision_id: str
    cell: TargetedDeliveryCellKey
    producer_attempt: ProcessingAttemptIdentity
    projection_contract_version: str
    evidence_artifacts: tuple[EvidenceArtifactRef, ...]
    dependency_processing_attempt_ids: tuple[str, ...]
    selected_payload_sha256: str | None = None
    non_observation_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.cell, TargetedDeliveryCellKey):
            raise DeliveryLineageContractError(
                "cell must be a TargetedDeliveryCellKey"
            )
        if not isinstance(self.producer_attempt, ProcessingAttemptIdentity):
            raise DeliveryLineageContractError(
                "producer_attempt must be an owner-issued attempt"
            )
        expected_dependency_attempt_ids = retry_predecessor_attempt_ids(
            self.producer_attempt
        )
        if self.dependency_processing_attempt_ids != expected_dependency_attempt_ids:
            raise DeliveryLineageContractError(
                "dependency processing attempts must equal the producer retry "
                "predecessor chain"
            )
        version = _required_text(
            self.projection_contract_version, "projection_contract_version"
        )
        if (self.selected_payload_sha256 is None) == (
            self.non_observation_reason is None
        ):
            raise DeliveryLineageContractError(
                "revision requires exactly one selected payload or "
                "non-observation reason"
            )
        selected_digest = None
        null_reason = None
        if self.selected_payload_sha256 is not None:
            selected_digest = _sha256(
                self.selected_payload_sha256, "selected_payload_sha256"
            )
        else:
            null_reason = _required_text(
                self.non_observation_reason, "non_observation_reason"
            )
        try:
            closure = canonical_evidence_artifact_closure(
                producer_attempt=self.producer_attempt,
                artifacts=self.evidence_artifacts,
            )
        except ValueError as exc:
            raise DeliveryLineageContractError(str(exc)) from exc
        if closure != self.evidence_artifacts:
            raise DeliveryLineageContractError(
                "evidence artifacts must be canonical-sorted"
            )
        expected = _typed_id(
            "xic.targeted-delivery-revision-candidate.v1:",
            _revision_basis(
                cell=self.cell,
                attempt=self.producer_attempt,
                projection_contract_version=version,
                evidence_artifacts=closure,
                dependency_processing_attempt_ids=(
                    self.dependency_processing_attempt_ids
                ),
                selected_payload_sha256=selected_digest,
                non_observation_reason=null_reason,
            ),
        )
        if self.delivery_revision_id != expected:
            raise DeliveryLineageContractError(
                "delivery_revision_id mismatches exact candidate content"
            )


@dataclass(frozen=True, slots=True)
class TargetedDeliveryStageCandidate:
    delivery_stage_id: str
    producer_attempt: ProcessingAttemptIdentity
    revisions: tuple[TargetedDeliveryRevisionCandidate, ...]
    output_artifacts: tuple[EvidenceArtifactRef, ...]
    lineage_manifest_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.producer_attempt, ProcessingAttemptIdentity):
            raise DeliveryLineageContractError(
                "producer_attempt must be an owner-issued attempt"
            )
        if not isinstance(self.revisions, tuple) or not self.revisions:
            raise DeliveryLineageContractError("revisions must be non-empty")
        if any(
            not isinstance(revision, TargetedDeliveryRevisionCandidate)
            or revision.producer_attempt != self.producer_attempt
            for revision in self.revisions
        ):
            raise DeliveryLineageContractError(
                "all revisions must bind the stage producer attempt"
            )
        cell_ids = tuple(
            revision.cell.delivery_cell_key_id for revision in self.revisions
        )
        if len(cell_ids) != len(set(cell_ids)):
            raise DeliveryLineageContractError(
                "a stage may contain only one revision per delivery cell"
            )
        if cell_ids != tuple(sorted(cell_ids)):
            raise DeliveryLineageContractError(
                "delivery revisions must be canonical-sorted by cell"
            )
        closure = canonical_current_attempt_artifact_closure(
            producer_attempt=self.producer_attempt,
            artifacts=self.output_artifacts,
        )
        if closure != self.output_artifacts:
            raise DeliveryLineageContractError(
                "output artifacts must be canonical-sorted"
            )
        manifest = _stage_manifest_basis(
            attempt=self.producer_attempt,
            revisions=self.revisions,
            output_artifacts=self.output_artifacts,
        )
        expected_manifest_sha = _canonical_sha256(manifest)
        if self.lineage_manifest_sha256 != expected_manifest_sha:
            raise DeliveryLineageContractError(
                "lineage_manifest_sha256 mismatches staged content"
            )
        expected_stage = _typed_id(
            "xic.targeted-delivery-stage-candidate.v1:",
            {"schema": "targeted_delivery_stage_candidate_v1", **manifest},
        )
        if self.delivery_stage_id != expected_stage:
            raise DeliveryLineageContractError(
                "delivery_stage_id mismatches staged content"
            )


@dataclass(frozen=True, slots=True)
class TargetedDeliverySealCandidate:
    delivery_seal_candidate_id: str
    stage: TargetedDeliveryStageCandidate

    def __post_init__(self) -> None:
        if not isinstance(self.stage, TargetedDeliveryStageCandidate):
            raise DeliveryLineageContractError(
                "stage must be a TargetedDeliveryStageCandidate"
            )
        expected = _typed_id(
            "xic.targeted-delivery-seal-candidate.v1:",
            {
                "schema": "targeted_delivery_seal_candidate_v1",
                "delivery_stage_id": self.stage.delivery_stage_id,
                "lineage_manifest_sha256": self.stage.lineage_manifest_sha256,
                "revision_ids": [
                    revision.delivery_revision_id for revision in self.stage.revisions
                ],
                "output_artifact_ids": [
                    artifact.evidence_artifact_id
                    for artifact in self.stage.output_artifacts
                ],
                "authority": "candidate_only",
            },
        )
        if self.delivery_seal_candidate_id != expected:
            raise DeliveryLineageContractError(
                "delivery_seal_candidate_id mismatches staged candidate content"
            )


def mint_targeted_delivery_cell_key(
    sample: ResolvedCanonicalSampleIdentity,
    target: ResolvedTargetDefinition,
) -> TargetedDeliveryCellKey:
    if not isinstance(sample, ResolvedCanonicalSampleIdentity):
        raise DeliveryLineageContractError(
            "sample must be an owner-resolved canonical sample identity"
        )
    if not isinstance(target, ResolvedTargetDefinition):
        raise DeliveryLineageContractError(
            "target must be an owner-resolved target definition"
        )
    basis = {
        "schema": "targeted_delivery_cell_v1",
        "projection": "targeted",
        "canonical_sample_identity_id": sample.identity.sample_identity_id,
        "target_key_id": target.definition.target_key.target_key_id,
    }
    return TargetedDeliveryCellKey(
        delivery_cell_key_id=_typed_id("xic.targeted-delivery-cell.v1:", basis),
        canonical_sample_identity_id=sample.identity.sample_identity_id,
        target_key_id=target.definition.target_key.target_key_id,
    )


def mint_targeted_delivery_revision_candidate(
    *,
    cell: TargetedDeliveryCellKey,
    producer_attempt: ProcessingAttemptIdentity,
    projection_contract_version: str,
    evidence_artifacts: tuple[EvidenceArtifactRef, ...],
    dependency_processing_attempt_ids: tuple[str, ...] | None = None,
    selected_payload: bytes | None = None,
    non_observation_reason: str | None = None,
) -> TargetedDeliveryRevisionCandidate:
    if (selected_payload is None) == (non_observation_reason is None):
        raise DeliveryLineageContractError(
            "revision requires exactly one selected payload or non-observation reason"
        )
    expected_dependency_attempt_ids = retry_predecessor_attempt_ids(producer_attempt)
    if (
        dependency_processing_attempt_ids is not None
        and dependency_processing_attempt_ids != expected_dependency_attempt_ids
    ):
        raise DeliveryLineageContractError(
            "dependency processing attempts must equal the producer retry "
            "predecessor chain"
        )
    selected_sha = (
        hashlib.sha256(selected_payload).hexdigest()
        if selected_payload is not None
        else None
    )
    basis = _revision_basis(
        cell=cell,
        attempt=producer_attempt,
        projection_contract_version=projection_contract_version,
        evidence_artifacts=evidence_artifacts,
        dependency_processing_attempt_ids=expected_dependency_attempt_ids,
        selected_payload_sha256=selected_sha,
        non_observation_reason=non_observation_reason,
    )
    return TargetedDeliveryRevisionCandidate(
        delivery_revision_id=_typed_id(
            "xic.targeted-delivery-revision-candidate.v1:", basis
        ),
        cell=cell,
        producer_attempt=producer_attempt,
        projection_contract_version=projection_contract_version,
        evidence_artifacts=evidence_artifacts,
        dependency_processing_attempt_ids=expected_dependency_attempt_ids,
        selected_payload_sha256=selected_sha,
        non_observation_reason=non_observation_reason,
    )


def stage_targeted_delivery_candidate(
    *,
    producer_attempt: ProcessingAttemptIdentity,
    revisions: tuple[TargetedDeliveryRevisionCandidate, ...],
    output_artifacts: tuple[EvidenceArtifactRef, ...],
) -> TargetedDeliveryStageCandidate:
    if not isinstance(revisions, tuple) or not revisions:
        raise DeliveryLineageContractError("revisions must be non-empty")
    if any(
        not isinstance(revision, TargetedDeliveryRevisionCandidate)
        for revision in revisions
    ):
        raise DeliveryLineageContractError(
            "revisions must contain TargetedDeliveryRevisionCandidate values"
        )
    cell_ids = tuple(revision.cell.delivery_cell_key_id for revision in revisions)
    if len(cell_ids) != len(set(cell_ids)):
        raise DeliveryLineageContractError(
            "a stage may contain only one revision per delivery cell"
        )
    manifest = _stage_manifest_basis(
        attempt=producer_attempt,
        revisions=revisions,
        output_artifacts=output_artifacts,
    )
    return TargetedDeliveryStageCandidate(
        delivery_stage_id=_typed_id(
            "xic.targeted-delivery-stage-candidate.v1:",
            {"schema": "targeted_delivery_stage_candidate_v1", **manifest},
        ),
        producer_attempt=producer_attempt,
        revisions=revisions,
        output_artifacts=output_artifacts,
        lineage_manifest_sha256=_canonical_sha256(manifest),
    )


def mint_targeted_delivery_seal_candidate(
    stage: TargetedDeliveryStageCandidate,
) -> TargetedDeliverySealCandidate:
    if not isinstance(stage, TargetedDeliveryStageCandidate):
        raise DeliveryLineageContractError(
            "stage must be a TargetedDeliveryStageCandidate"
        )
    basis = {
        "schema": "targeted_delivery_seal_candidate_v1",
        "delivery_stage_id": stage.delivery_stage_id,
        "lineage_manifest_sha256": stage.lineage_manifest_sha256,
        "revision_ids": [
            revision.delivery_revision_id for revision in stage.revisions
        ],
        "output_artifact_ids": [
            artifact.evidence_artifact_id for artifact in stage.output_artifacts
        ],
        "authority": "candidate_only",
    }
    return TargetedDeliverySealCandidate(
        delivery_seal_candidate_id=_typed_id(
            "xic.targeted-delivery-seal-candidate.v1:", basis
        ),
        stage=stage,
    )


def _revision_basis(
    *,
    cell: TargetedDeliveryCellKey,
    attempt: ProcessingAttemptIdentity,
    projection_contract_version: str,
    evidence_artifacts: tuple[EvidenceArtifactRef, ...],
    dependency_processing_attempt_ids: tuple[str, ...],
    selected_payload_sha256: str | None,
    non_observation_reason: str | None,
) -> dict[str, object]:
    return {
        "schema": "targeted_delivery_revision_candidate_v1",
        "delivery_cell_key_id": cell.delivery_cell_key_id,
        "processing_request_id": attempt.processing_request_id,
        "processing_attempt_id": attempt.processing_attempt_id,
        "projection_contract_version": projection_contract_version,
        "evidence_artifact_ids": [
            artifact.evidence_artifact_id for artifact in evidence_artifacts
        ],
        "dependency_processing_attempt_ids": list(dependency_processing_attempt_ids),
        "selected_payload_sha256": selected_payload_sha256,
        "non_observation_reason": non_observation_reason,
    }


def _stage_manifest_basis(
    *,
    attempt: ProcessingAttemptIdentity,
    revisions: tuple[TargetedDeliveryRevisionCandidate, ...],
    output_artifacts: tuple[EvidenceArtifactRef, ...],
) -> dict[str, object]:
    return {
        "processing_request_id": attempt.processing_request_id,
        "processing_attempt_id": attempt.processing_attempt_id,
        "revision_ids": [revision.delivery_revision_id for revision in revisions],
        "output_artifacts": [
            {
                "evidence_artifact_id": artifact.evidence_artifact_id,
                "content_sha256": artifact.content_identity.content_sha256,
                "byte_size": artifact.content_identity.byte_size,
            }
            for artifact in output_artifacts
        ],
    }


def _typed_id(prefix: str, basis: Mapping[str, object]) -> str:
    return f"{prefix}{_canonical_sha256(basis)}"


def _canonical_sha256(basis: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            basis, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _required_text(value: object, field: str) -> str:
    return require_owner_normalized_text(
        value, field=field, error_factory=DeliveryLineageContractError
    )


def _sha256(value: object, field: str) -> str:
    return require_lower_sha256(
        value,
        field=field,
        error_factory=lambda msg: DeliveryLineageContractError(
            f"{field} must be a lower-case SHA-256 digest"
        ),
    )



__all__ = [
    "DeliveryLineageContractError",
    "TargetedDeliveryCellKey",
    "TargetedDeliveryRevisionCandidate",
    "TargetedDeliverySealCandidate",
    "TargetedDeliveryStageCandidate",
    "mint_targeted_delivery_cell_key",
    "mint_targeted_delivery_revision_candidate",
    "mint_targeted_delivery_seal_candidate",
    "stage_targeted_delivery_candidate",
]
