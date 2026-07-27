"""Alignment adapter over the shared mechanical attempt runtime.

This module does not select peaks, interpret Alignment content, or write product
artifacts.  It binds opaque producer bytes to the existing evidence reference
and settles the existing Alignment spawn runner through Package 0 lifecycle
contracts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from xic_extractor.alignment.process_execution import run_process_jobs
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
    settle_work_unit,
)
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
    begin_attempt_finalization,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    canonical_peak_observation_batch_bytes,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class AlignmentRuntimeAdapterError(ValueError):
    """Raised when Alignment process results cannot close shared runtime work."""


_SCHEMA_VERSION = "alignment_process_result_identity_v1"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.alignment.runtime_adapter",
    semantic_role="alignment_process_worker_result_identity",
    schema_version=_SCHEMA_VERSION,
    validation_status="diagnostic_only",
    media_type="application/vnd.xic.alignment-process-result-identity+json",
    serialization_format="canonical-json-v1",
)


@dataclass(frozen=True, slots=True)
class AlignmentRuntimeProcessResult:
    """One pickleable Alignment process result plus mechanical resource facts."""

    settlement: WorkUnitSettlement
    resource_key: str
    open_outcome: RuntimeResourceOpenOutcome
    cleanup_outcome: RuntimeResourceCleanupOutcome
    runtime_artifacts: tuple[EvidenceArtifactRef, ...] = ()
    peak_observation_batch: PeakObservationBatch | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, WorkUnitSettlement):
            raise AlignmentRuntimeAdapterError(
                "settlement must be a WorkUnitSettlement"
            )
        _required_text(self.resource_key, field="resource_key")
        if not isinstance(self.open_outcome, RuntimeResourceOpenOutcome):
            raise AlignmentRuntimeAdapterError(
                "open_outcome must be a RuntimeResourceOpenOutcome"
            )
        if not isinstance(self.cleanup_outcome, RuntimeResourceCleanupOutcome):
            raise AlignmentRuntimeAdapterError(
                "cleanup_outcome must be a RuntimeResourceCleanupOutcome"
            )
        if not isinstance(self.runtime_artifacts, tuple) or any(
            not isinstance(artifact, EvidenceArtifactRef)
            for artifact in self.runtime_artifacts
        ):
            raise AlignmentRuntimeAdapterError(
                "runtime_artifacts must contain EvidenceArtifactRef values"
            )
        if (
            self.settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and self.settlement.owner_result_ref not in self.runtime_artifacts
        ):
            raise AlignmentRuntimeAdapterError(
                "successful settlement artifact must be registered by the worker"
            )
        if any(
            artifact.producer_processing_request_id
            != self.settlement.attempt.processing_request_id
            or artifact.producer_processing_attempt_id
            != self.settlement.attempt.processing_attempt_id
            for artifact in self.runtime_artifacts
        ):
            raise AlignmentRuntimeAdapterError(
                "runtime artifacts must bind the worker settlement attempt"
            )
        if self.peak_observation_batch is not None:
            _validate_peak_observation_result(self)


@dataclass(frozen=True, slots=True)
class AlignmentRuntimeApplicationResult:
    runtime: AttemptRuntime
    coordinator_record: CoordinatorTerminalRecord
    finalization_record: SupervisorFinalizationRecord
    successful_observation_batches: tuple[
        tuple[str, PeakObservationBatch], ...
    ] = ()

    def __post_init__(self) -> None:
        attempt = self.runtime.manifest.attempt
        if self.coordinator_record.state.runtime != self.runtime:
            raise AlignmentRuntimeAdapterError(
                "coordinator record must bind the settled Alignment runtime"
            )
        if self.finalization_record.attempt != attempt:
            raise AlignmentRuntimeAdapterError(
                "finalization record must bind the Alignment runtime attempt"
            )
        observation_keys = tuple(
            key for key, _batch in self.successful_observation_batches
        )
        if observation_keys != tuple(
            key
            for key in self.runtime.manifest.work_unit_keys
            if key in observation_keys
        ):
            raise AlignmentRuntimeAdapterError(
                "successful_observation_batches must follow manifest order"
            )
        if len(observation_keys) != len(set(observation_keys)) or any(
            not isinstance(batch, PeakObservationBatch)
            for _key, batch in self.successful_observation_batches
        ):
            raise AlignmentRuntimeAdapterError(
                "successful observation batches must bind unique manifest work"
            )
        settlements_by_key = {
            settlement.work_unit_key: settlement
            for settlement in self.runtime.settlements
        }
        registered_by_key = {
            key: {
                record.artifact.evidence_artifact_id
                for record in self.coordinator_record.state.artifacts
                if record.work_unit_key == key
            }
            for key in observation_keys
        }
        for key, batch in self.successful_observation_batches:
            settlement = settlements_by_key.get(key)
            if (
                settlement is None
                or settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
                or settlement.owner_result_ref is None
            ):
                raise AlignmentRuntimeAdapterError(
                    "successful observation batch must bind accepted successful work"
                )
            _validate_alignment_application_batch(
                settlement=settlement,
                batch=batch,
                registered_artifact_ids=registered_by_key[key],
            )


def mint_alignment_process_result_artifact(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    owner_result_payload: bytes,
    upstream_artifacts: Sequence[EvidenceArtifactRef] = (),
) -> EvidenceArtifactRef:
    """Mint one Alignment-owned ref over exact opaque process-result bytes."""

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise AlignmentRuntimeAdapterError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    key = _required_text(work_unit_key, field="work_unit_key")
    if not isinstance(owner_result_payload, bytes):
        raise AlignmentRuntimeAdapterError("owner_result_payload must be exact bytes")
    identity_bytes = _canonical_result_identity_bytes(
        work_unit_key=key,
        content_sha256=hashlib.sha256(owner_result_payload).hexdigest(),
        byte_size=len(owner_result_payload),
    )
    return _mint_evidence_artifact_ref(
        producer_processing_request_id=(
            processing_attempt.processing_request_id
        ),
        producer_processing_attempt_id=(
            processing_attempt.processing_attempt_id
        ),
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=identity_bytes,
        upstream_artifacts=upstream_artifacts,
    )


def execute_alignment_process_runtime(
    runtime: AttemptRuntime,
    jobs: Iterable[Any],
    *,
    worker: Callable[[Any], AlignmentRuntimeProcessResult],
    error_factory: Callable[[Any, Exception], AlignmentRuntimeProcessResult],
    max_workers: int,
    resource_reservations: Sequence[RuntimeResourceReservation],
    recovery_marker: OwnerPathRuntimeMarker,
    executor_factory: Callable[..., Any] | None = None,
) -> AlignmentRuntimeApplicationResult:
    """Run Alignment's existing spawn backend through the shared lifecycle."""

    if not isinstance(runtime, AttemptRuntime):
        raise AlignmentRuntimeAdapterError("runtime must be an AttemptRuntime")
    if not isinstance(recovery_marker, OwnerPathRuntimeMarker):
        raise AlignmentRuntimeAdapterError(
            "recovery_marker must be an OwnerPathRuntimeMarker"
        )
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise AlignmentRuntimeAdapterError("max_workers must be a positive integer")
    state = begin_attempt_finalization(runtime)
    for reservation in resource_reservations:
        if not isinstance(reservation, RuntimeResourceReservation):
            raise AlignmentRuntimeAdapterError(
                "resource_reservations must contain RuntimeResourceReservation values"
            )
        state = reserve_runtime_resource(state, reservation)
    recovery_marker.record_launch(runtime)
    observed = run_process_jobs(
        jobs,
        worker=worker,
        error_factory=error_factory,
        max_workers=max_workers,
        executor_factory=executor_factory,
    )
    if any(not isinstance(item, AlignmentRuntimeProcessResult) for item in observed):
        raise AlignmentRuntimeAdapterError(
            "Alignment runner returned an invalid runtime process result"
        )
    manifest_order = {
        key: index for index, key in enumerate(runtime.manifest.work_unit_keys)
    }
    ordered = sorted(
        observed,
        key=lambda item: manifest_order.get(
            item.settlement.work_unit_key,
            len(manifest_order),
        ),
    )
    current = state
    successful_observation_batches: dict[str, PeakObservationBatch] = {}
    for item in ordered:
        settlement = item.settlement
        if settlement.attempt != runtime.manifest.attempt:
            raise AlignmentRuntimeAdapterError(
                "Alignment result references a different runtime attempt"
            )
        settled_runtime = settle_work_unit(
            current.runtime,
            work_unit_key=settlement.work_unit_key,
            outcome=settlement.outcome,
            owner_result_ref=settlement.owner_result_ref,
        )
        current = replace(current, runtime=settled_runtime)
        for artifact in item.runtime_artifacts:
            current = register_runtime_artifact(
                current,
                work_unit_key=settlement.work_unit_key,
                artifact=artifact,
            )
        current = settle_runtime_resource(
            current,
            resource_key=item.resource_key,
            open_outcome=item.open_outcome,
            cleanup_outcome=item.cleanup_outcome,
        )
        accepted_settlement = next(
            (
                accepted
                for accepted in current.runtime.settlements
                if accepted.work_unit_key == settlement.work_unit_key
            ),
            None,
        )
        if (
            accepted_settlement == settlement
            and settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and item.peak_observation_batch is not None
        ):
            _coalesce_observation_batch(
                successful_observation_batches,
                work_unit_key=settlement.work_unit_key,
                batch=item.peak_observation_batch,
            )
    if not current.runtime.all_manifest_work_settled:
        settled_keys = {
            settlement.work_unit_key
            for settlement in current.runtime.settlements
        }
        missing = tuple(
            key
            for key in current.runtime.manifest.work_unit_keys
            if key not in settled_keys
        )
        raise AlignmentRuntimeAdapterError(
            f"Alignment results did not settle manifest work: {missing!r}"
        )
    coordinator = propose_attempt_terminal(current)
    proposed = mint_supervisor_finalization(coordinator)
    accepted = recovery_marker(coordinator, proposed)
    if current.runtime.integrity_failure is not None or current.failures:
        successful_observation_batches.clear()
    return AlignmentRuntimeApplicationResult(
        runtime=current.runtime,
        coordinator_record=coordinator,
        finalization_record=accepted,
        successful_observation_batches=tuple(
            (key, successful_observation_batches[key])
            for key in current.runtime.manifest.work_unit_keys
            if key in successful_observation_batches
        ),
    )


def _canonical_result_identity_bytes(
    *,
    work_unit_key: str,
    content_sha256: str,
    byte_size: int,
) -> bytes:
    return json.dumps(
        {
            "byte_size": byte_size,
            "content_sha256": content_sha256,
            "schema_version": _SCHEMA_VERSION,
            "work_unit_key": work_unit_key,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AlignmentRuntimeAdapterError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _validate_peak_observation_result(
    result: AlignmentRuntimeProcessResult,
) -> None:
    batch = result.peak_observation_batch
    if not isinstance(batch, PeakObservationBatch):
        raise AlignmentRuntimeAdapterError(
            "peak_observation_batch must be owner-produced"
        )
    settlement = result.settlement
    if settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED:
        raise AlignmentRuntimeAdapterError(
            "only successful Alignment work may carry peak observations"
        )
    artifact = settlement.owner_result_ref
    if artifact is None:
        raise AlignmentRuntimeAdapterError(
            "peak observation work requires an Alignment result artifact"
        )
    if artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
        raise AlignmentRuntimeAdapterError(
            "peak observation work requires an Alignment-owned result artifact"
        )
    if (
        batch.processing_request_id != settlement.attempt.processing_request_id
        or batch.processing_attempt_id != settlement.attempt.processing_attempt_id
    ):
        raise AlignmentRuntimeAdapterError(
            "peak observation batch references a different processing attempt"
        )
    payload = canonical_peak_observation_batch_bytes(batch)
    try:
        verify_evidence_artifact_payload(
            artifact,
            _canonical_result_identity_bytes(
                work_unit_key=settlement.work_unit_key,
                content_sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
            ),
        )
    except ValueError as exc:
        raise AlignmentRuntimeAdapterError(
            "Alignment result artifact does not bind the peak observation batch"
        ) from exc
    expected_upstreams = tuple(
        sorted(
            observation_artifact.evidence_artifact_id
            for observation_artifact in batch.observation_artifacts
        )
    )
    if artifact.upstream_artifact_ids != expected_upstreams:
        raise AlignmentRuntimeAdapterError(
            "Alignment result artifact must close the exact observation artifacts"
        )
    registered_ids = {
        runtime_artifact.evidence_artifact_id
        for runtime_artifact in result.runtime_artifacts
    }
    required_ids = {artifact.evidence_artifact_id, *expected_upstreams}
    if not required_ids.issubset(registered_ids):
        raise AlignmentRuntimeAdapterError(
            "Alignment runtime artifacts must register result and observations"
        )


def _coalesce_observation_batch(
    batches: dict[str, PeakObservationBatch],
    *,
    work_unit_key: str,
    batch: PeakObservationBatch,
) -> None:
    existing = batches.get(work_unit_key)
    if existing is None:
        batches[work_unit_key] = batch
        return
    if canonical_peak_observation_batch_bytes(existing) != (
        canonical_peak_observation_batch_bytes(batch)
    ):
        raise AlignmentRuntimeAdapterError(
            "replayed Alignment observation batch conflicts with accepted work"
        )


def _validate_alignment_application_batch(
    *,
    settlement: WorkUnitSettlement,
    batch: PeakObservationBatch,
    registered_artifact_ids: set[str],
) -> None:
    artifact = settlement.owner_result_ref
    if artifact is None:
        raise AlignmentRuntimeAdapterError(
            "successful observation batch requires an accepted result artifact"
        )
    if artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
        raise AlignmentRuntimeAdapterError(
            "accepted observation batch requires an Alignment-owned result artifact"
        )
    if (
        batch.processing_request_id != settlement.attempt.processing_request_id
        or batch.processing_attempt_id != settlement.attempt.processing_attempt_id
    ):
        raise AlignmentRuntimeAdapterError(
            "successful observation batch references a different attempt"
        )
    payload = canonical_peak_observation_batch_bytes(batch)
    try:
        verify_evidence_artifact_payload(
            artifact,
            _canonical_result_identity_bytes(
                work_unit_key=settlement.work_unit_key,
                content_sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
            ),
        )
    except ValueError as exc:
        raise AlignmentRuntimeAdapterError(
            "accepted Alignment result does not bind the observation batch"
        ) from exc
    expected_upstreams = tuple(
        sorted(item.evidence_artifact_id for item in batch.observation_artifacts)
    )
    if artifact.upstream_artifact_ids != expected_upstreams:
        raise AlignmentRuntimeAdapterError(
            "accepted Alignment result must close the exact observation artifacts"
        )
    required_ids = {
        artifact.evidence_artifact_id,
        *(item.evidence_artifact_id for item in batch.observation_artifacts),
    }
    if not required_ids.issubset(registered_artifact_ids):
        raise AlignmentRuntimeAdapterError(
            "successful observation batch artifacts are not registered"
        )


__all__ = [
    "AlignmentRuntimeAdapterError",
    "AlignmentRuntimeApplicationResult",
    "AlignmentRuntimeProcessResult",
    "execute_alignment_process_runtime",
    "mint_alignment_process_result_artifact",
]
