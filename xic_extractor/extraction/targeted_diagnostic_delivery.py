"""Candidate-only Targeted diagnostic delivery over closed runtime evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Literal

from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
)
from xic_extractor.artifact_lineage import canonical_evidence_artifact_closure
from xic_extractor.delivery_lineage import (
    TargetedDeliveryRevisionCandidate,
    TargetedDeliverySealCandidate,
    TargetedDeliveryStageCandidate,
    mint_targeted_delivery_cell_key,
    mint_targeted_delivery_revision_candidate,
    mint_targeted_delivery_seal_candidate,
    stage_targeted_delivery_candidate,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.execution_runtime import AttemptTerminalOutcome
from xic_extractor.execution_runtime_finalization_models import (
    RuntimeArtifactDisposition,
    SupervisorFinalizationRecord,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodResult,
    TargetedDiagnosticProjection,
    canonical_targeted_diagnostic_method_bytes,
)
from xic_extractor.extractor import RunOutput
from xic_extractor.processing_identity import ProcessingAttemptIdentity

TargetedDiagnosticDeliveryCandidateStatus = Literal[
    "staged_candidate",
    "quarantined",
    "rolled_back",
]

_PROJECTION_CONTRACT_VERSION = "targeted_diagnostic_projection_v1"
_METHOD_RESULT_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_diagnostic_delivery",
    semantic_role="targeted_diagnostic_method_result",
    schema_version="targeted_diagnostic_method_result_v1",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)
_CSV_OUTPUT_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_diagnostic_delivery",
    semantic_role="hard_exact_targeted_diagnostic_csv",
    schema_version="hard_exact_targeted_diagnostic_csv_v1",
    validation_status="diagnostic_only",
    media_type="text/csv",
    serialization_format="exact-bytes-v1",
)
_TARGETED_RUNTIME_OWNER = "xic_extractor.extraction.targeted_runtime_adapter"
_ALIGNMENT_RUNTIME_OWNER = "xic_extractor.alignment.runtime_adapter"


class TargetedDiagnosticDeliveryError(ValueError):
    """Raised when diagnostic delivery evidence cannot close exactly."""


@dataclass(frozen=True, slots=True)
class TargetedDiagnosticDeliveryCandidate:
    """In-memory candidate carrier that never changes product current authority."""

    status: TargetedDiagnosticDeliveryCandidateStatus
    attempt: ProcessingAttemptIdentity
    prior_current_delivery_id: str
    active_output: RunOutput
    lineage_artifacts: tuple[EvidenceArtifactRef, ...]
    method_result_artifact: EvidenceArtifactRef | None = None
    diagnostic_csv_artifact: EvidenceArtifactRef | None = None
    revisions: tuple[TargetedDeliveryRevisionCandidate, ...] = ()
    stage_candidate: TargetedDeliveryStageCandidate | None = None
    seal_candidate: TargetedDeliverySealCandidate | None = None
    quarantine_reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise TargetedDiagnosticDeliveryError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        _required_text(
            self.prior_current_delivery_id,
            field="prior_current_delivery_id",
        )
        if not isinstance(self.active_output, RunOutput):
            raise TargetedDiagnosticDeliveryError(
                "active_output must remain the authoritative legacy RunOutput"
            )
        try:
            closure = canonical_evidence_artifact_closure(
                processing_request_id=self.attempt.processing_request_id,
                processing_attempt_id=self.attempt.processing_attempt_id,
                artifacts=self.lineage_artifacts,
            )
        except ValueError as exc:
            raise TargetedDiagnosticDeliveryError(str(exc)) from exc
        if closure != self.lineage_artifacts:
            raise TargetedDiagnosticDeliveryError(
                "lineage_artifacts must be unique and canonical-sorted"
            )
        if self.status == "staged_candidate":
            _validate_staged_candidate(self)
        elif self.status == "quarantined":
            if not self.quarantine_reason:
                raise TargetedDiagnosticDeliveryError(
                    "quarantined delivery requires a reason"
                )
            if self.revisions or self.stage_candidate or self.seal_candidate:
                raise TargetedDiagnosticDeliveryError(
                    "quarantined delivery cannot retain staged candidates"
                )
            if self.diagnostic_csv_artifact is not None:
                raise TargetedDiagnosticDeliveryError(
                    "quarantined delivery cannot claim a hard-exact CSV artifact"
                )
        elif self.status == "rolled_back":
            if not self.quarantine_reason:
                raise TargetedDiagnosticDeliveryError(
                    "rolled-back delivery requires a rollback reason"
                )
        else:
            raise TargetedDiagnosticDeliveryError(
                "diagnostic delivery candidate status is unsupported"
            )


def settle_targeted_diagnostic_delivery_candidate(
    result: TargetedDiagnosticMethodResult,
    *,
    alignment_inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    attempt: ProcessingAttemptIdentity,
    targeted_finalization: SupervisorFinalizationRecord,
    alignment_finalization: SupervisorFinalizationRecord,
    targeted_runtime_artifacts: tuple[EvidenceArtifactRef, ...],
    alignment_runtime_artifacts: tuple[EvidenceArtifactRef, ...],
    candidate_csv_bytes: bytes,
    authoritative_legacy_csv_bytes: bytes,
    prior_current_delivery_id: str,
) -> TargetedDiagnosticDeliveryCandidate:
    """Mint delivery candidates only after exact runtime and legacy parity gates."""

    input_by_sample = _validate_method_context(
        result,
        alignment_inputs=alignment_inputs,
        attempt=attempt,
    )
    _required_text(prior_current_delivery_id, field="prior_current_delivery_id")
    _require_exact_bytes(candidate_csv_bytes, field="candidate_csv_bytes")
    _require_exact_bytes(
        authoritative_legacy_csv_bytes,
        field="authoritative_legacy_csv_bytes",
    )

    candidate_lineage = _candidate_lineage(alignment_inputs, attempt=attempt)
    gate_reason = _runtime_gate_reason(
        attempt=attempt,
        targeted_finalization=targeted_finalization,
        alignment_finalization=alignment_finalization,
        targeted_runtime_artifacts=targeted_runtime_artifacts,
        alignment_runtime_artifacts=alignment_runtime_artifacts,
    )
    runtime_lineage = _canonical_lineage(
        attempt,
        (
            *candidate_lineage,
            *targeted_runtime_artifacts,
            *alignment_runtime_artifacts,
        ),
    )
    if gate_reason:
        return _quarantined(
            result,
            attempt=attempt,
            prior_current_delivery_id=prior_current_delivery_id,
            lineage_artifacts=runtime_lineage,
            reason=gate_reason,
        )

    method_payload = canonical_targeted_diagnostic_method_bytes(result)
    method_result_artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=attempt.processing_request_id,
        producer_processing_attempt_id=attempt.processing_attempt_id,
        semantic_contract=_METHOD_RESULT_CONTRACT,
        payload_bytes=method_payload,
        upstream_artifacts=_method_roots(
            alignment_inputs,
            targeted_runtime_artifacts=targeted_runtime_artifacts,
            alignment_runtime_artifacts=alignment_runtime_artifacts,
        ),
    )
    method_lineage = _canonical_lineage(
        attempt,
        (*runtime_lineage, method_result_artifact),
    )
    if candidate_csv_bytes != authoritative_legacy_csv_bytes:
        return _quarantined(
            result,
            attempt=attempt,
            prior_current_delivery_id=prior_current_delivery_id,
            lineage_artifacts=method_lineage,
            method_result_artifact=method_result_artifact,
            reason="diagnostic CSV hard-exact conflict",
        )

    csv_artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=attempt.processing_request_id,
        producer_processing_attempt_id=attempt.processing_attempt_id,
        semantic_contract=_CSV_OUTPUT_CONTRACT,
        payload_bytes=candidate_csv_bytes,
        upstream_artifacts=(method_result_artifact,),
    )
    full_lineage = _canonical_lineage(
        attempt,
        (*method_lineage, csv_artifact),
    )
    projections = {item.sample_stem: item for item in result.projections}
    revisions = tuple(
        sorted(
            (
                _revision(
                    input_by_sample[sample_stem],
                    projections[sample_stem],
                    result=result,
                    attempt=attempt,
                    evidence_artifacts=full_lineage,
                )
                for sample_stem in input_by_sample
            ),
            key=lambda item: item.cell.delivery_cell_key_id,
        )
    )
    stage = stage_targeted_delivery_candidate(
        producer_attempt=attempt,
        revisions=revisions,
        output_artifacts=full_lineage,
    )
    seal = mint_targeted_delivery_seal_candidate(stage)
    return TargetedDiagnosticDeliveryCandidate(
        status="staged_candidate",
        attempt=attempt,
        prior_current_delivery_id=prior_current_delivery_id,
        active_output=result.authoritative_legacy_output,
        lineage_artifacts=full_lineage,
        method_result_artifact=method_result_artifact,
        diagnostic_csv_artifact=csv_artifact,
        revisions=revisions,
        stage_candidate=stage,
        seal_candidate=seal,
    )


def rollback_targeted_diagnostic_delivery_candidate(
    delivery: TargetedDiagnosticDeliveryCandidate,
) -> TargetedDiagnosticDeliveryCandidate:
    """Retain evidence while authoritative legacy current stays active."""

    if not isinstance(delivery, TargetedDiagnosticDeliveryCandidate):
        raise TargetedDiagnosticDeliveryError(
            "delivery must be a TargetedDiagnosticDeliveryCandidate"
        )
    return replace(
        delivery,
        status="rolled_back",
        quarantine_reason="whole attempt rolled back to authoritative legacy current",
    )


def _validate_method_context(
    result: object,
    *,
    alignment_inputs: object,
    attempt: object,
) -> dict[str, TargetedHypothesisAlignmentInput]:
    if not isinstance(result, TargetedDiagnosticMethodResult):
        raise TargetedDiagnosticDeliveryError(
            "result must be a TargetedDiagnosticMethodResult"
        )
    if not isinstance(attempt, ProcessingAttemptIdentity):
        raise TargetedDiagnosticDeliveryError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    if (
        result.processing_request_id != attempt.processing_request_id
        or result.processing_attempt_id != attempt.processing_attempt_id
    ):
        raise TargetedDiagnosticDeliveryError(
            "method result references a different processing attempt"
        )
    if not isinstance(alignment_inputs, tuple) or not alignment_inputs:
        raise TargetedDiagnosticDeliveryError(
            "alignment_inputs must be the complete immutable method input tuple"
        )
    if any(
        not isinstance(item, TargetedHypothesisAlignmentInput)
        for item in alignment_inputs
    ):
        raise TargetedDiagnosticDeliveryError(
            "alignment_inputs contain an invalid Targeted input"
        )
    input_by_sample = {
        item.sample.metadata.raw_stem: item for item in alignment_inputs
    }
    if len(input_by_sample) != len(alignment_inputs):
        raise TargetedDiagnosticDeliveryError(
            "alignment_inputs contain duplicate sample-target cells"
        )
    cell_by_sample = {item.sample_stem: item for item in result.cells}
    projection_by_sample = {item.sample_stem: item for item in result.projections}
    if (
        len(cell_by_sample) != len(result.cells)
        or len(projection_by_sample) != len(result.projections)
        or set(input_by_sample) != set(cell_by_sample)
        or set(input_by_sample) != set(projection_by_sample)
    ):
        raise TargetedDiagnosticDeliveryError(
            "method result and delivery input sample universes differ"
        )
    for sample_stem, item in input_by_sample.items():
        candidate_set = item.candidate_set
        if (
            candidate_set.processing_request_id != attempt.processing_request_id
            or candidate_set.processing_attempt_id != attempt.processing_attempt_id
            or candidate_set.canonical_sample_identity_id
            != item.sample.identity.sample_identity_id
            or candidate_set.target_snapshot_id != item.target.snapshot.snapshot_id
            or candidate_set.target_key_id
            != item.target.definition.target_key.target_key_id
            or candidate_set.target_definition_id
            != item.target.definition.target_definition_id
        ):
            raise TargetedDiagnosticDeliveryError(
                "candidate-set receipt mismatches exact delivery context"
            )
        if (
            item.selection.candidate_set_artifact_id
            != candidate_set.artifact.evidence_artifact_id
            or cell_by_sample[sample_stem].selection_evidence_artifact_id
            != item.selection.artifact.evidence_artifact_id
        ):
            raise TargetedDiagnosticDeliveryError(
                "method cell does not consume the exact candidate-set selection"
            )
    return input_by_sample


def _candidate_lineage(
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    *,
    attempt: ProcessingAttemptIdentity,
) -> tuple[EvidenceArtifactRef, ...]:
    return _canonical_lineage(
        attempt,
        tuple(
            artifact
            for item in inputs
            for artifact in (
                *item.candidate_set.lineage_artifacts,
                item.selection.artifact,
            )
        ),
    )


def _runtime_gate_reason(
    *,
    attempt: ProcessingAttemptIdentity,
    targeted_finalization: object,
    alignment_finalization: object,
    targeted_runtime_artifacts: object,
    alignment_runtime_artifacts: object,
) -> str:
    lanes = (
        (
            "targeted",
            targeted_finalization,
            targeted_runtime_artifacts,
            _TARGETED_RUNTIME_OWNER,
        ),
        (
            "alignment",
            alignment_finalization,
            alignment_runtime_artifacts,
            _ALIGNMENT_RUNTIME_OWNER,
        ),
    )
    observed_ids: set[str] = set()
    for lane, finalization, artifacts, owner_namespace in lanes:
        if not isinstance(finalization, SupervisorFinalizationRecord):
            return f"partial {lane} runtime finalization"
        if finalization.attempt != attempt:
            return f"conflicting {lane} runtime finalization attempt"
        if finalization.terminal_outcome is not AttemptTerminalOutcome.SUCCEEDED:
            return (
                f"{lane} runtime attempt {finalization.terminal_outcome.value}; "
                "delivery quarantined"
            )
        if not isinstance(artifacts, tuple) or any(
            not isinstance(artifact, EvidenceArtifactRef) for artifact in artifacts
        ):
            return f"partial {lane} runtime owner artifacts"
        if not artifacts:
            return f"partial {lane} runtime owner artifacts"
        if any(
            artifact.semantic_contract.owner_namespace != owner_namespace
            for artifact in artifacts
        ):
            return f"conflicting {lane} runtime owner artifact role"
        dispositions: dict[str, RuntimeArtifactDisposition] = {}
        for item in finalization.artifact_dispositions:
            if (
                not isinstance(item, tuple)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not isinstance(item[1], RuntimeArtifactDisposition)
            ):
                return f"conflicting {lane} runtime artifact disposition"
            artifact_id, disposition = item
            previous = dispositions.get(artifact_id)
            if previous is not None and previous is not disposition:
                return f"conflicting {lane} runtime artifact replay"
            if previous is not None:
                return f"conflicting {lane} duplicate runtime artifact replay"
            dispositions[artifact_id] = disposition
        for artifact in artifacts:
            artifact_id = artifact.evidence_artifact_id
            if (
                artifact.producer_processing_request_id
                != attempt.processing_request_id
                or artifact.producer_processing_attempt_id
                != attempt.processing_attempt_id
            ):
                return f"conflicting {lane} runtime artifact attempt"
            if (
                dispositions.get(artifact_id)
                is not RuntimeArtifactDisposition.OFFERED_TO_OWNER
            ):
                return f"partial {lane} work ref was not offered to owner"
            if artifact_id in observed_ids:
                return "conflicting cross-lane runtime artifact replay"
            observed_ids.add(artifact_id)
    return ""


def _method_roots(
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    *,
    targeted_runtime_artifacts: tuple[EvidenceArtifactRef, ...],
    alignment_runtime_artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    values = (
        *(item.candidate_set.artifact for item in inputs),
        *targeted_runtime_artifacts,
        *alignment_runtime_artifacts,
    )
    by_id = {item.evidence_artifact_id: item for item in values}
    return tuple(by_id[artifact_id] for artifact_id in sorted(by_id))


def _canonical_lineage(
    attempt: ProcessingAttemptIdentity,
    artifacts: tuple[EvidenceArtifactRef, ...],
) -> tuple[EvidenceArtifactRef, ...]:
    by_id: dict[str, EvidenceArtifactRef] = {}
    for artifact in artifacts:
        if not isinstance(artifact, EvidenceArtifactRef):
            raise TargetedDiagnosticDeliveryError(
                "lineage must contain EvidenceArtifactRef values"
            )
        existing = by_id.get(artifact.evidence_artifact_id)
        if existing is not None and existing != artifact:
            raise TargetedDiagnosticDeliveryError(
                "lineage contains conflicting artifact content"
            )
        by_id[artifact.evidence_artifact_id] = artifact
    try:
        return canonical_evidence_artifact_closure(
            processing_request_id=attempt.processing_request_id,
            processing_attempt_id=attempt.processing_attempt_id,
            artifacts=tuple(by_id.values()),
        )
    except ValueError as exc:
        raise TargetedDiagnosticDeliveryError(str(exc)) from exc


def _revision(
    item: TargetedHypothesisAlignmentInput,
    projection: TargetedDiagnosticProjection,
    *,
    result: TargetedDiagnosticMethodResult,
    attempt: ProcessingAttemptIdentity,
    evidence_artifacts: tuple[EvidenceArtifactRef, ...],
) -> TargetedDeliveryRevisionCandidate:
    cell = mint_targeted_delivery_cell_key(item.sample, item.target)
    if projection.counted_detection:
        return mint_targeted_delivery_revision_candidate(
            cell=cell,
            producer_attempt=attempt,
            projection_contract_version=_PROJECTION_CONTRACT_VERSION,
            evidence_artifacts=evidence_artifacts,
            selected_payload=_canonical_projection_bytes(projection),
        )
    matching_cell = next(
        cell for cell in result.cells if cell.sample_stem == projection.sample_stem
    )
    reason = matching_cell.reason or projection.reason or "not_counted"
    return mint_targeted_delivery_revision_candidate(
        cell=cell,
        producer_attempt=attempt,
        projection_contract_version=_PROJECTION_CONTRACT_VERSION,
        evidence_artifacts=evidence_artifacts,
        non_observation_reason=f"{matching_cell.status}:{reason}",
    )


def _canonical_projection_bytes(projection: TargetedDiagnosticProjection) -> bytes:
    payload = {
        "schema_version": _PROJECTION_CONTRACT_VERSION,
        "sample_stem": projection.sample_stem,
        "status": projection.status,
        "rt": projection.rt,
        "area": projection.area,
        "intensity": projection.intensity,
        "peak_start": projection.peak_start,
        "peak_end": projection.peak_end,
        "peak_width": projection.peak_width,
        "confidence": projection.confidence,
        "reason": projection.reason,
        "counted_detection": projection.counted_detection,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _quarantined(
    result: TargetedDiagnosticMethodResult,
    *,
    attempt: ProcessingAttemptIdentity,
    prior_current_delivery_id: str,
    lineage_artifacts: tuple[EvidenceArtifactRef, ...],
    reason: str,
    method_result_artifact: EvidenceArtifactRef | None = None,
) -> TargetedDiagnosticDeliveryCandidate:
    return TargetedDiagnosticDeliveryCandidate(
        status="quarantined",
        attempt=attempt,
        prior_current_delivery_id=prior_current_delivery_id,
        active_output=result.authoritative_legacy_output,
        lineage_artifacts=lineage_artifacts,
        method_result_artifact=method_result_artifact,
        quarantine_reason=reason,
    )


def _validate_staged_candidate(
    delivery: TargetedDiagnosticDeliveryCandidate,
) -> None:
    if (
        delivery.method_result_artifact is None
        or delivery.diagnostic_csv_artifact is None
        or not delivery.revisions
        or delivery.stage_candidate is None
        or delivery.seal_candidate is None
    ):
        raise TargetedDiagnosticDeliveryError(
            "staged delivery requires method, CSV, revision, stage, and seal candidates"
        )
    if delivery.quarantine_reason:
        raise TargetedDiagnosticDeliveryError(
            "staged delivery cannot carry a quarantine reason"
        )
    if delivery.stage_candidate.revisions != delivery.revisions:
        raise TargetedDiagnosticDeliveryError(
            "stage candidate revisions mismatch the delivery carrier"
        )
    if delivery.stage_candidate.output_artifacts != delivery.lineage_artifacts:
        raise TargetedDiagnosticDeliveryError(
            "stage candidate must bind the full diagnostic lineage"
        )
    if delivery.seal_candidate.stage != delivery.stage_candidate:
        raise TargetedDiagnosticDeliveryError(
            "seal candidate must bind the exact staged candidate"
        )
    lineage_ids = {
        artifact.evidence_artifact_id for artifact in delivery.lineage_artifacts
    }
    if (
        delivery.method_result_artifact.evidence_artifact_id not in lineage_ids
        or delivery.diagnostic_csv_artifact.evidence_artifact_id not in lineage_ids
    ):
        raise TargetedDiagnosticDeliveryError(
            "staged delivery artifacts are outside the full lineage"
        )


def _require_exact_bytes(value: object, *, field: str) -> bytes:
    if not isinstance(value, bytes):
        raise TargetedDiagnosticDeliveryError(f"{field} must be exact bytes")
    return value


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedDiagnosticDeliveryError(
            f"{field} must be non-empty normalized text"
        )
    return value


__all__ = [
    "TargetedDiagnosticDeliveryCandidate",
    "TargetedDiagnosticDeliveryCandidateStatus",
    "TargetedDiagnosticDeliveryError",
    "rollback_targeted_diagnostic_delivery_candidate",
    "settle_targeted_diagnostic_delivery_candidate",
]
