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
    AttemptTerminalOutcome,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
)
from xic_extractor.execution_runtime_observability import (
    RuntimeObservabilityContractError,
    validate_runtime_acquisition_metrics,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeAttemptAggregate,
    RuntimeFinalizationPersister,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    canonical_peak_observation_batch_bytes,
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
    failure_detail: WorkUnitFailureDetail | None = None
    peak_observation_batch: PeakObservationBatch | None = None

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
            if self.peak_observation_batch is not None:
                _validate_peak_observation_result_binding(
                    self.owner_result_ref,
                    self.peak_observation_batch,
                    attempt=self.attempt,
                    work_unit_key=self.work_unit_key,
                )
        elif self.owner_result_ref is not None:
            raise TargetedRuntimeAdapterError(
                "only succeeded result envelope may carry owner_result_ref"
            )
        elif self.peak_observation_batch is not None:
            raise TargetedRuntimeAdapterError(
                "only succeeded result envelope may carry peak_observation_batch"
            )
        try:
            mint_work_unit_settlement(
                attempt=self.attempt,
                work_unit_key=self.work_unit_key,
                outcome=self.outcome,
                owner_result_ref=artifact,
                failure_detail=self.failure_detail,
            )
        except ValueError as exc:
            raise TargetedRuntimeAdapterError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class TargetedRuntimeApplicationResult:
    """Settled Targeted runtime state and manifest-ordered successful refs."""

    runtime: AttemptRuntime
    successful_result_refs: tuple[tuple[str, TargetedProcessResultRef], ...]
    successful_observation_batches: tuple[tuple[str, PeakObservationBatch], ...] = ()

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
        successful_keys = {key for key, _reference in self.successful_result_refs}
        observation_keys = tuple(
            key for key, _batch in self.successful_observation_batches
        )
        if observation_keys != tuple(
            key
            for key in self.runtime.manifest.work_unit_keys
            if key in observation_keys
        ):
            raise TargetedRuntimeAdapterError(
                "successful_observation_batches must follow settled manifest order"
            )
        if len(observation_keys) != len(set(observation_keys)) or any(
            key not in successful_keys or not isinstance(batch, PeakObservationBatch)
            for key, batch in self.successful_observation_batches
        ):
            raise TargetedRuntimeAdapterError(
                "successful observation batches must bind unique successful work"
            )
        references_by_key = dict(self.successful_result_refs)
        for key, batch in self.successful_observation_batches:
            _validate_peak_observation_result_binding(
                references_by_key[key],
                batch,
                attempt=self.runtime.manifest.attempt,
                work_unit_key=key,
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
        artifact_identities = tuple(
            (reference.work_unit_key, reference.artifact.evidence_artifact_id)
            for reference in self.runtime_artifact_refs
        )
        registered_identities = set(artifact_identities)
        missing_success_refs = tuple(
            (
                envelope.work_unit_key,
                envelope.owner_result_ref.artifact.evidence_artifact_id,
            )
            for envelope in self.result_envelopes
            if envelope.outcome is WorkUnitTerminalOutcome.SUCCEEDED
            and envelope.owner_result_ref is not None
            and (
                envelope.work_unit_key,
                envelope.owner_result_ref.artifact.evidence_artifact_id,
            )
            not in registered_identities
        )
        if missing_success_refs:
            raise TargetedRuntimeAdapterError(
                "every successful result ref must be a registered runtime artifact"
            )


@dataclass(frozen=True, slots=True)
class TargetedRuntimeLifecycleApplicationResult:
    """Targeted settlement plus an accepted atomic terminal acknowledgement."""

    settlement: TargetedRuntimeApplicationResult
    finalization: RuntimeFinalizationReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.settlement, TargetedRuntimeApplicationResult):
            raise TargetedRuntimeAdapterError(
                "settlement must be a TargetedRuntimeApplicationResult"
            )
        if not isinstance(self.finalization, RuntimeFinalizationReceipt):
            raise TargetedRuntimeAdapterError(
                "finalization must be a RuntimeFinalizationReceipt"
            )
        if (
            self.finalization.coordinator.state.runtime != self.settlement.runtime
            or self.finalization.supervisor.attempt
            != self.settlement.runtime.manifest.attempt
        ):
            raise TargetedRuntimeAdapterError(
                "lifecycle result must bind one settled runtime attempt"
            )
        if (
            self.settlement.successful_observation_batches
            or self.finalization.supervisor.terminal_outcome
            is AttemptTerminalOutcome.SUCCEEDED
        ):
            _validate_observation_batch_acquisition_metrics(
                self.finalization.observability_ledger,
                self.settlement.successful_observation_batches,
            )

    @property
    def coordinator_record(self) -> CoordinatorTerminalRecord:
        """Compatibility projection from the single accepted receipt."""

        return self.finalization.coordinator

    @property
    def finalization_record(self) -> SupervisorFinalizationRecord:
        """Compatibility projection from the single accepted receipt."""

        return self.finalization.supervisor


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


def execute_targeted_runtime_lifecycle(
    runtime: AttemptRuntime,
    resource_reservations: Sequence[RuntimeResourceReservation],
    *,
    worker_batch_provider: Callable[
        [RuntimeAttemptAggregate],
        TargetedRuntimeWorkerBatch,
    ],
    telemetry_provider: RuntimeTelemetryProvider,
    finalization_persister: RuntimeFinalizationPersister,
) -> TargetedRuntimeLifecycleApplicationResult:
    """Translate one Targeted run through the owner's atomic lifecycle entry."""

    if not isinstance(runtime, AttemptRuntime):
        raise TargetedRuntimeAdapterError("runtime must be an AttemptRuntime")
    if not callable(worker_batch_provider):
        raise TargetedRuntimeAdapterError("worker_batch_provider must be callable")
    observed_batch: TargetedRuntimeWorkerBatch | None = None

    def provide_owner_batch(
        aggregate: RuntimeAttemptAggregate,
    ) -> RuntimeWorkerBatchReceipt:
        nonlocal observed_batch
        batch = worker_batch_provider(aggregate)
        if not isinstance(batch, TargetedRuntimeWorkerBatch):
            raise TargetedRuntimeAdapterError(
                "worker_batch_provider must return a TargetedRuntimeWorkerBatch"
            )
        for envelope in batch.result_envelopes:
            if envelope.attempt != runtime.manifest.attempt:
                raise TargetedRuntimeAdapterError(
                    "worker result envelope references a different runtime attempt"
                )
        submitted_keys = {
            envelope.work_unit_key for envelope in batch.result_envelopes
        }
        missing = tuple(
            key
            for key in runtime.manifest.work_unit_keys
            if key not in submitted_keys
        )
        if missing:
            raise TargetedRuntimeAdapterError(
                f"worker result batch did not settle manifest work: {missing!r}"
            )
        observed_batch = batch
        return _targeted_owner_batch(batch)

    finalization = execute_runtime_attempt(
        runtime,
        resource_reservations,
        worker_batch_provider=provide_owner_batch,
        telemetry_provider=telemetry_provider,
        persister=finalization_persister,
    )
    closed_state = finalization.coordinator.state
    settlement = (
        TargetedRuntimeApplicationResult(
            runtime=closed_state.runtime,
            successful_result_refs=(),
            successful_observation_batches=(),
        )
        if observed_batch is None
        else _targeted_application_result(
            closed_state.runtime,
            observed_batch.result_envelopes,
        )
    )
    if closed_state.failures or closed_state.runtime.integrity_failure is not None:
        settlement = TargetedRuntimeApplicationResult(
            runtime=settlement.runtime,
            successful_result_refs=settlement.successful_result_refs,
            successful_observation_batches=(),
        )
    return TargetedRuntimeLifecycleApplicationResult(
        settlement=settlement,
        finalization=finalization,
    )


def _targeted_owner_batch(
    batch: TargetedRuntimeWorkerBatch,
) -> RuntimeWorkerBatchReceipt:
    registrations = [
        RuntimeArtifactRegistrationReceipt(
            work_unit_key=reference.work_unit_key,
            artifact=reference.artifact,
        )
        for reference in batch.runtime_artifact_refs
    ]
    acquisitions: list[RuntimeAcquisitionReceipt] = []
    for envelope in batch.result_envelopes:
        observation_batch = envelope.peak_observation_batch
        if observation_batch is None:
            continue
        registrations.extend(
            RuntimeArtifactRegistrationReceipt(
                work_unit_key=envelope.work_unit_key,
                artifact=artifact,
            )
            for artifact in observation_batch.observation_artifacts
        )
        acquisitions.append(
            RuntimeAcquisitionReceipt(
                work_unit_key=envelope.work_unit_key,
                acquisition=observation_batch.acquisition,
            )
        )
    return RuntimeWorkerBatchReceipt(
        settlements=tuple(
            _settlement_from_envelope(envelope) for envelope in batch.result_envelopes
        ),
        resource_settlements=tuple(
            RuntimeResourceSettlementReceipt(
                attempt=report.attempt,
                resource_key=report.resource_key,
                open_outcome=report.open_outcome,
                cleanup_outcome=report.cleanup_outcome,
            )
            for report in batch.resource_reports
        ),
        artifact_registrations=tuple(registrations),
        acquisitions=tuple(acquisitions),
    )


def _settlement_from_envelope(
    envelope: TargetedProcessWorkerResultEnvelope,
):
    return mint_work_unit_settlement(
        attempt=envelope.attempt,
        work_unit_key=envelope.work_unit_key,
        outcome=envelope.outcome,
        owner_result_ref=(
            None
            if envelope.owner_result_ref is None
            else envelope.owner_result_ref.artifact
        ),
        failure_detail=envelope.failure_detail,
    )


def _targeted_application_result(
    runtime: AttemptRuntime,
    envelopes: Sequence[TargetedProcessWorkerResultEnvelope],
) -> TargetedRuntimeApplicationResult:
    if not runtime.all_manifest_work_settled:
        missing = tuple(
            key
            for key in runtime.manifest.work_unit_keys
            if key not in {item.work_unit_key for item in runtime.settlements}
        )
        raise TargetedRuntimeAdapterError(
            f"worker result batch did not settle manifest work: {missing!r}"
        )
    accepted = {item.work_unit_key: item for item in runtime.settlements}
    accepted_refs: dict[str, TargetedProcessResultRef] = {}
    accepted_observations: dict[str, PeakObservationBatch] = {}
    for envelope in envelopes:
        if accepted.get(envelope.work_unit_key) != _settlement_from_envelope(envelope):
            continue
        if envelope.outcome is not WorkUnitTerminalOutcome.SUCCEEDED:
            continue
        reference = envelope.owner_result_ref
        if not isinstance(reference, TargetedProcessResultRef):
            raise TargetedRuntimeAdapterError(
                "succeeded envelope lost its TargetedProcessResultRef"
            )
        existing_ref = accepted_refs.get(envelope.work_unit_key)
        if existing_ref is not None and existing_ref != reference:
            raise TargetedRuntimeAdapterError(
                "replayed Targeted result ref conflicts with accepted work"
            )
        accepted_refs[envelope.work_unit_key] = reference
        if envelope.peak_observation_batch is not None:
            _coalesce_observation_batch(
                accepted_observations,
                work_unit_key=envelope.work_unit_key,
                batch=envelope.peak_observation_batch,
            )
    if runtime.integrity_failure is not None:
        accepted_observations.clear()
    return TargetedRuntimeApplicationResult(
        runtime=runtime,
        successful_result_refs=tuple(
            (key, accepted_refs[key])
            for key in runtime.manifest.work_unit_keys
            if key in accepted_refs
        ),
        successful_observation_batches=tuple(
            (key, accepted_observations[key])
            for key in runtime.manifest.work_unit_keys
            if key in accepted_observations
        ),
    )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedRuntimeAdapterError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _validate_observation_batch_acquisition_metrics(
    ledger,
    batches: tuple[tuple[str, PeakObservationBatch], ...],
) -> None:
    try:
        validate_runtime_acquisition_metrics(
            ledger,
            tuple(batch.acquisition for _key, batch in batches),
        )
    except RuntimeObservabilityContractError as exc:
        raise TargetedRuntimeAdapterError(
            "runtime acquisition metrics must match accepted observation batches"
        ) from exc


def _validate_peak_observation_result_binding(
    reference: TargetedProcessResultRef,
    batch: PeakObservationBatch,
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
) -> None:
    if not isinstance(batch, PeakObservationBatch):
        raise TargetedRuntimeAdapterError(
            "peak_observation_batch must be owner-produced"
        )
    if (
        batch.processing_request_id != attempt.processing_request_id
        or batch.processing_attempt_id != attempt.processing_attempt_id
    ):
        raise TargetedRuntimeAdapterError(
            "peak observation batch references a different processing attempt"
        )
    payload = canonical_peak_observation_batch_bytes(batch)
    if (
        reference.work_unit_key != work_unit_key
        or reference.owner_result_content_sha256 != hashlib.sha256(payload).hexdigest()
        or reference.owner_result_byte_size != len(payload)
    ):
        raise TargetedRuntimeAdapterError(
            "Targeted result ref does not bind the peak observation batch"
        )
    expected_upstreams = tuple(
        sorted(
            artifact.evidence_artifact_id for artifact in batch.observation_artifacts
        )
    )
    if reference.artifact.upstream_artifact_ids != expected_upstreams:
        raise TargetedRuntimeAdapterError(
            "Targeted result ref must close the exact observation artifacts"
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
        raise TargetedRuntimeAdapterError(
            "replayed Targeted observation batch conflicts with accepted work"
        )


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
    "execute_targeted_runtime_lifecycle",
    "mint_targeted_process_result_ref",
]
