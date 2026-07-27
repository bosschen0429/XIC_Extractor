"""Targeted process-result handoff into the mechanical execution runtime.

The Targeted producer owns the result-envelope role and canonical result
identity payload.  The shared evidence-artifact module owns immutable exact-byte
references, while the runtime only settles opaque manifest work keys.  Legacy
``RawFileExtractionResult`` values are deliberately not serialized or hashed by
this boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

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
    AttemptFinalizationState,
    CoordinatorTerminalRecord,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
    accept_supervisor_finalization,
    begin_attempt_finalization,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class TargetedRuntimeAdapterError(ValueError):
    """Raised when a Targeted process result cannot settle its runtime work."""


_SCHEMA_VERSION = "targeted_process_result_identity_v1"
_ARTIFACT_SEMANTIC_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.extraction.targeted_runtime_adapter",
    semantic_role="targeted_process_worker_result_identity",
    schema_version=_SCHEMA_VERSION,
    validation_status="diagnostic_only",
    media_type="application/vnd.xic.targeted-process-result-identity+json",
    serialization_format="canonical-json-v1",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class TargetedProcessResultRef:
    """Targeted-owned immutable result ref bound to one manifest work key."""

    work_unit_key: str
    owner_result_content_sha256: str
    owner_result_byte_size: int
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        key = _required_text(self.work_unit_key, field="work_unit_key")
        if (
            not isinstance(self.owner_result_content_sha256, str)
            or _SHA256_RE.fullmatch(self.owner_result_content_sha256) is None
        ):
            raise TargetedRuntimeAdapterError(
                "owner_result_content_sha256 must be a lower-case SHA-256 digest"
            )
        if (
            isinstance(self.owner_result_byte_size, bool)
            or not isinstance(self.owner_result_byte_size, int)
            or self.owner_result_byte_size < 0
        ):
            raise TargetedRuntimeAdapterError(
                "owner_result_byte_size must be a non-negative integer"
            )
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise TargetedRuntimeAdapterError(
                "artifact must be an owner-minted EvidenceArtifactRef"
            )
        if self.artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
            raise TargetedRuntimeAdapterError(
                "artifact must use the Targeted process-result semantic contract"
            )
        try:
            verify_evidence_artifact_payload(
                self.artifact,
                _canonical_result_identity_bytes(
                    work_unit_key=key,
                    owner_result_content_sha256=self.owner_result_content_sha256,
                    owner_result_byte_size=self.owner_result_byte_size,
                ),
            )
        except ValueError as exc:
            raise TargetedRuntimeAdapterError(
                "artifact does not bind this Targeted result identity"
            ) from exc


@dataclass(frozen=True, slots=True)
class TargetedProcessWorkerResultEnvelope:
    """One pickleable producer-owned result ready for runtime settlement."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    outcome: WorkUnitTerminalOutcome
    owner_result_ref: TargetedProcessResultRef | None = None
    failure_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise TargetedRuntimeAdapterError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        artifact = None
        if self.outcome is WorkUnitTerminalOutcome.SUCCEEDED:
            if not isinstance(self.owner_result_ref, TargetedProcessResultRef):
                raise TargetedRuntimeAdapterError(
                    "succeeded result envelope requires a TargetedProcessResultRef"
                )
            if self.owner_result_ref.work_unit_key != self.work_unit_key:
                raise TargetedRuntimeAdapterError(
                    "owner_result_ref must bind the envelope work_unit_key"
                )
            artifact = self.owner_result_ref.artifact
        elif self.owner_result_ref is not None:
            raise TargetedRuntimeAdapterError(
                "only succeeded result envelope may carry owner_result_ref"
            )
        try:
            WorkUnitSettlement(
                attempt=self.attempt,
                work_unit_key=self.work_unit_key,
                outcome=self.outcome,
                owner_result_ref=artifact,
            )
        except ValueError as exc:
            raise TargetedRuntimeAdapterError(str(exc)) from exc
        if self.outcome is WorkUnitTerminalOutcome.SUCCEEDED:
            if self.failure_message is not None:
                raise TargetedRuntimeAdapterError(
                    "succeeded result envelope cannot carry failure_message"
                )
        elif (
            not isinstance(self.failure_message, str)
            or not self.failure_message.strip()
        ):
            raise TargetedRuntimeAdapterError(
                "failed or cancelled result envelope requires failure_message"
            )


@dataclass(frozen=True, slots=True)
class TargetedRuntimeApplicationResult:
    """Settled Targeted runtime state and manifest-ordered successful refs."""

    runtime: AttemptRuntime
    successful_result_refs: tuple[tuple[str, TargetedProcessResultRef], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, AttemptRuntime):
            raise TargetedRuntimeAdapterError("runtime must be an AttemptRuntime")
        expected_keys = tuple(
            settlement.work_unit_key
            for key in self.runtime.manifest.work_unit_keys
            for settlement in self.runtime.settlements
            if settlement.work_unit_key == key
            and settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
        )
        if expected_keys != tuple(key for key, _ref in self.successful_result_refs):
            raise TargetedRuntimeAdapterError(
                "successful_result_refs must follow settled manifest order"
            )
        by_key = {
            settlement.work_unit_key: settlement
            for settlement in self.runtime.settlements
        }
        for key, reference in self.successful_result_refs:
            if (
                not isinstance(reference, TargetedProcessResultRef)
                or reference.work_unit_key != key
                or reference.artifact != by_key[key].owner_result_ref
            ):
                raise TargetedRuntimeAdapterError(
                    "successful result ref must match the accepted settlement"
                )


@dataclass(frozen=True, slots=True)
class TargetedProcessResourceReport:
    """Pickleable child report for one parent-reserved runtime resource."""

    attempt: ProcessingAttemptIdentity
    resource_key: str
    open_outcome: RuntimeResourceOpenOutcome
    cleanup_outcome: RuntimeResourceCleanupOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise TargetedRuntimeAdapterError(
                "attempt must be an owner-issued ProcessingAttemptIdentity"
            )
        _required_text(self.resource_key, field="resource_key")
        if not isinstance(self.open_outcome, RuntimeResourceOpenOutcome):
            raise TargetedRuntimeAdapterError(
                "open_outcome must be a RuntimeResourceOpenOutcome"
            )
        if not isinstance(self.cleanup_outcome, RuntimeResourceCleanupOutcome):
            raise TargetedRuntimeAdapterError(
                "cleanup_outcome must be a RuntimeResourceCleanupOutcome"
            )


@dataclass(frozen=True, slots=True)
class TargetedRuntimeWorkerBatch:
    """Closed worker batch with work, resource, and temporary-artifact facts."""

    result_envelopes: tuple[TargetedProcessWorkerResultEnvelope, ...]
    resource_reports: tuple[TargetedProcessResourceReport, ...]
    runtime_artifact_refs: tuple[TargetedProcessResultRef, ...]

    def __post_init__(self) -> None:
        _require_tuple_of(
            self.result_envelopes,
            TargetedProcessWorkerResultEnvelope,
            field="result_envelopes",
        )
        _require_tuple_of(
            self.resource_reports,
            TargetedProcessResourceReport,
            field="resource_reports",
        )
        _require_tuple_of(
            self.runtime_artifact_refs,
            TargetedProcessResultRef,
            field="runtime_artifact_refs",
        )
        artifact_ids = tuple(
            reference.artifact.evidence_artifact_id
            for reference in self.runtime_artifact_refs
        )
        if len(artifact_ids) != len(set(artifact_ids)):
            raise TargetedRuntimeAdapterError(
                "runtime_artifact_refs must contain unique artifacts"
            )
        registered_ids = set(artifact_ids)
        missing_success_refs = tuple(
            envelope.owner_result_ref.artifact.evidence_artifact_id
            for envelope in self.result_envelopes
            if envelope.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and envelope.owner_result_ref is not None
            and envelope.owner_result_ref.artifact.evidence_artifact_id
            not in registered_ids
        )
        if missing_success_refs:
            raise TargetedRuntimeAdapterError(
                "every successful result ref must be a registered runtime artifact"
            )


@dataclass(frozen=True, slots=True)
class TargetedRuntimeLifecycleApplicationResult:
    """Targeted settlement plus an accepted atomic terminal acknowledgement."""

    settlement: TargetedRuntimeApplicationResult
    coordinator_record: CoordinatorTerminalRecord
    finalization_record: SupervisorFinalizationRecord

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, TargetedRuntimeApplicationResult):
            raise TargetedRuntimeAdapterError(
                "settlement must be a TargetedRuntimeApplicationResult"
            )
        if not isinstance(self.coordinator_record, CoordinatorTerminalRecord):
            raise TargetedRuntimeAdapterError(
                "coordinator_record must be a CoordinatorTerminalRecord"
            )
        if not isinstance(self.finalization_record, SupervisorFinalizationRecord):
            raise TargetedRuntimeAdapterError(
                "finalization_record must be a SupervisorFinalizationRecord"
            )
        if (
            self.coordinator_record.state.runtime != self.settlement.runtime
            or self.finalization_record.attempt
            != self.settlement.runtime.manifest.attempt
        ):
            raise TargetedRuntimeAdapterError(
                "lifecycle result must bind one settled runtime attempt"
            )


def canonical_targeted_process_result_identity_bytes(
    *,
    work_unit_key: str,
    owner_result_payload: bytes,
) -> bytes:
    """Bind one opaque owner payload by exact digest without serializing it."""

    normalized_key = _required_text(work_unit_key, field="work_unit_key")
    if not isinstance(owner_result_payload, bytes):
        raise TargetedRuntimeAdapterError("owner_result_payload must be exact bytes")
    return _canonical_result_identity_bytes(
        work_unit_key=normalized_key,
        owner_result_content_sha256=hashlib.sha256(owner_result_payload).hexdigest(),
        owner_result_byte_size=len(owner_result_payload),
    )


def _canonical_result_identity_bytes(
    *,
    work_unit_key: str,
    owner_result_content_sha256: str,
    owner_result_byte_size: int,
) -> bytes:
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "work_unit_key": work_unit_key,
        "owner_result_content_sha256": owner_result_content_sha256,
        "owner_result_byte_size": owner_result_byte_size,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def mint_targeted_process_result_ref(
    *,
    processing_attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    owner_result_payload: bytes,
    upstream_artifacts: Sequence[EvidenceArtifactRef] = (),
) -> TargetedProcessResultRef:
    """Mint the Targeted producer's immutable result identity reference."""

    if not isinstance(processing_attempt, ProcessingAttemptIdentity):
        raise TargetedRuntimeAdapterError(
            "processing_attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    key = _required_text(work_unit_key, field="work_unit_key")
    if not isinstance(owner_result_payload, bytes):
        raise TargetedRuntimeAdapterError("owner_result_payload must be exact bytes")
    content_sha256 = hashlib.sha256(owner_result_payload).hexdigest()
    byte_size = len(owner_result_payload)
    artifact = _mint_evidence_artifact_ref(
        producer_processing_request_id=processing_attempt.processing_request_id,
        producer_processing_attempt_id=processing_attempt.processing_attempt_id,
        semantic_contract=_ARTIFACT_SEMANTIC_CONTRACT,
        payload_bytes=_canonical_result_identity_bytes(
            work_unit_key=key,
            owner_result_content_sha256=content_sha256,
            owner_result_byte_size=byte_size,
        ),
        upstream_artifacts=upstream_artifacts,
    )
    return TargetedProcessResultRef(
        work_unit_key=key,
        owner_result_content_sha256=content_sha256,
        owner_result_byte_size=byte_size,
        artifact=artifact,
    )


def settle_targeted_process_worker_results(
    runtime: AttemptRuntime,
    envelopes: Sequence[TargetedProcessWorkerResultEnvelope],
) -> TargetedRuntimeApplicationResult:
    """Settle a closed Targeted result batch through the existing reducer."""

    if not isinstance(runtime, AttemptRuntime):
        raise TargetedRuntimeAdapterError("runtime must be an AttemptRuntime")
    if isinstance(envelopes, (str, bytes)):
        raise TargetedRuntimeAdapterError(
            "envelopes must contain TargetedProcessWorkerResultEnvelope values"
        )
    try:
        batch = tuple(envelopes)
    except TypeError as exc:
        raise TargetedRuntimeAdapterError(
            "envelopes must contain TargetedProcessWorkerResultEnvelope values"
        ) from exc
    current = runtime
    accepted_refs: dict[str, TargetedProcessResultRef] = {}
    for envelope in batch:
        if not isinstance(envelope, TargetedProcessWorkerResultEnvelope):
            raise TargetedRuntimeAdapterError(
                "envelopes must contain TargetedProcessWorkerResultEnvelope values"
            )
        if envelope.attempt != current.manifest.attempt:
            raise TargetedRuntimeAdapterError(
                "worker result envelope references a different runtime attempt"
            )
        current = settle_work_unit(
            current,
            work_unit_key=envelope.work_unit_key,
            outcome=envelope.outcome,
            owner_result_ref=(
                None
                if envelope.owner_result_ref is None
                else envelope.owner_result_ref.artifact
            ),
        )
        if (
            envelope.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and envelope.work_unit_key
            in {settlement.work_unit_key for settlement in current.settlements}
            and envelope.work_unit_key not in accepted_refs
        ):
            if not isinstance(envelope.owner_result_ref, TargetedProcessResultRef):
                raise TargetedRuntimeAdapterError(
                    "succeeded envelope lost its TargetedProcessResultRef"
                )
            accepted_refs[envelope.work_unit_key] = envelope.owner_result_ref
    if not current.all_manifest_work_settled:
        missing = tuple(
            key
            for key in current.manifest.work_unit_keys
            if key
            not in {settlement.work_unit_key for settlement in current.settlements}
        )
        raise TargetedRuntimeAdapterError(
            f"worker result batch did not settle manifest work: {missing!r}"
        )
    refs = tuple(
        (key, accepted_refs[key])
        for key in current.manifest.work_unit_keys
        if key in accepted_refs
    )
    return TargetedRuntimeApplicationResult(
        runtime=current,
        successful_result_refs=refs,
    )


def prepare_targeted_runtime_lifecycle(
    runtime: AttemptRuntime,
    reservations: Sequence[RuntimeResourceReservation],
) -> AttemptFinalizationState:
    """Reserve every declared resource before the Targeted runner is invoked."""

    state = begin_attempt_finalization(runtime)
    if isinstance(reservations, (str, bytes)):
        raise TargetedRuntimeAdapterError(
            "reservations must contain RuntimeResourceReservation values"
        )
    try:
        reservation_batch = tuple(reservations)
    except TypeError as exc:
        raise TargetedRuntimeAdapterError(
            "reservations must contain RuntimeResourceReservation values"
        ) from exc
    for reservation in reservation_batch:
        if not isinstance(reservation, RuntimeResourceReservation):
            raise TargetedRuntimeAdapterError(
                "reservations must contain RuntimeResourceReservation values"
            )
        state = reserve_runtime_resource(state, reservation)
    return state


def finalize_targeted_runtime_worker_batch(
    state: AttemptFinalizationState,
    batch: TargetedRuntimeWorkerBatch,
    *,
    supervisor_finalizer: Callable[
        [CoordinatorTerminalRecord, SupervisorFinalizationRecord],
        SupervisorFinalizationRecord,
    ],
) -> TargetedRuntimeLifecycleApplicationResult:
    """Close one Targeted worker batch and require supervisor acknowledgement."""

    if not isinstance(state, AttemptFinalizationState):
        raise TargetedRuntimeAdapterError("state must be an AttemptFinalizationState")
    if not isinstance(batch, TargetedRuntimeWorkerBatch):
        raise TargetedRuntimeAdapterError("batch must be a TargetedRuntimeWorkerBatch")
    if not callable(supervisor_finalizer):
        raise TargetedRuntimeAdapterError("supervisor_finalizer must be callable")
    attempt = state.runtime.manifest.attempt
    if any(report.attempt != attempt for report in batch.resource_reports):
        raise TargetedRuntimeAdapterError(
            "resource report references a different runtime attempt"
        )
    settlement = settle_targeted_process_worker_results(
        state.runtime,
        batch.result_envelopes,
    )
    current = AttemptFinalizationState(
        runtime=settlement.runtime,
        resources=state.resources,
        artifacts=state.artifacts,
        events=state.events,
        failures=state.failures,
    )
    for reference in batch.runtime_artifact_refs:
        current = register_runtime_artifact(
            current,
            work_unit_key=reference.work_unit_key,
            artifact=reference.artifact,
        )
    for report in batch.resource_reports:
        current = settle_runtime_resource(
            current,
            resource_key=report.resource_key,
            open_outcome=report.open_outcome,
            cleanup_outcome=report.cleanup_outcome,
        )
    coordinator = propose_attempt_terminal(current)
    proposed = mint_supervisor_finalization(coordinator)
    submitted = supervisor_finalizer(coordinator, proposed)
    accepted = accept_supervisor_finalization(coordinator, submitted)
    return TargetedRuntimeLifecycleApplicationResult(
        settlement=settlement,
        coordinator_record=coordinator,
        finalization_record=accepted,
    )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedRuntimeAdapterError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _require_tuple_of(value: object, item_type: type, *, field: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise TargetedRuntimeAdapterError(
            f"{field} must be a tuple of {item_type.__name__} values"
        )


__all__ = [
    "TargetedProcessResourceReport",
    "TargetedProcessResultRef",
    "TargetedProcessWorkerResultEnvelope",
    "TargetedRuntimeAdapterError",
    "TargetedRuntimeApplicationResult",
    "TargetedRuntimeLifecycleApplicationResult",
    "TargetedRuntimeWorkerBatch",
    "canonical_targeted_process_result_identity_bytes",
    "finalize_targeted_runtime_worker_batch",
    "mint_targeted_process_result_ref",
    "prepare_targeted_runtime_lifecycle",
    "settle_targeted_process_worker_results",
]
