"""Alignment adapter over the shared mechanical attempt runtime.

This module does not select peaks, interpret Alignment content, or write product
artifacts.  It binds opaque producer bytes to the existing evidence reference
and settles the existing Alignment spawn runner through the shared lifecycle
contracts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from xic_extractor.alignment.process_execution import run_process_jobs
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import (
    RuntimeObservabilityContractError,
    validate_runtime_acquisition_metrics,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeAttemptAggregate,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
)
from xic_extractor.peak_detection.observation_batch import (
    PeakObservationBatch,
    canonical_peak_observation_batch_bytes,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSamplePeakGroupHypothesis,
    )


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
    cross_sample_peak_groups: tuple[CrossSamplePeakGroupHypothesis, ...] | None = None
    cross_sample_peak_group_upstream_artifact_ids: tuple[str, ...] | None = None

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
        if self.cross_sample_peak_groups is not None:
            _validate_cross_sample_peak_group_result(self)
        elif self.cross_sample_peak_group_upstream_artifact_ids is not None:
            raise AlignmentRuntimeAdapterError(
                "cross-sample peak-group upstream IDs require a scientific result"
            )


@dataclass(frozen=True, slots=True)
class AlignmentRuntimeApplicationResult:
    runtime: AttemptRuntime
    finalization: RuntimeFinalizationReceipt
    successful_observation_batches: tuple[tuple[str, PeakObservationBatch], ...] = ()
    successful_cross_sample_peak_groups: tuple[
        tuple[str, tuple[CrossSamplePeakGroupHypothesis, ...]], ...
    ] = ()

    def __post_init__(self) -> None:
        attempt = self.runtime.manifest.attempt
        if not isinstance(self.finalization, RuntimeFinalizationReceipt):
            raise AlignmentRuntimeAdapterError(
                "finalization must be a RuntimeFinalizationReceipt"
            )
        if self.finalization.coordinator.state.runtime != self.runtime:
            raise AlignmentRuntimeAdapterError(
                "coordinator record must bind the settled Alignment runtime"
            )
        if self.finalization.supervisor.attempt != attempt:
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
                for record in self.finalization.coordinator.state.artifacts
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
        group_keys = tuple(
            key for key, _groups in self.successful_cross_sample_peak_groups
        )
        if group_keys != tuple(
            key for key in self.runtime.manifest.work_unit_keys if key in group_keys
        ):
            raise AlignmentRuntimeAdapterError(
                "successful_cross_sample_peak_groups must follow manifest order"
            )
        if len(group_keys) != len(set(group_keys)):
            raise AlignmentRuntimeAdapterError(
                "successful cross-sample peak groups must bind unique manifest work"
            )
        if self.runtime.integrity_failure is not None and group_keys:
            raise AlignmentRuntimeAdapterError(
                "integrity-failed runtime cannot expose cross-sample peak groups"
            )
        for key, groups in self.successful_cross_sample_peak_groups:
            settlement = settlements_by_key.get(key)
            if (
                settlement is None
                or settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
                or settlement.owner_result_ref is None
            ):
                raise AlignmentRuntimeAdapterError(
                    "cross-sample peak groups must bind accepted successful work"
                )
            _validate_cross_sample_peak_group_binding(
                settlement=settlement,
                groups=groups,
                upstream_artifact_ids=(
                    settlement.owner_result_ref.upstream_artifact_ids
                ),
            )
        if (
            self.successful_observation_batches
            or self.finalization.supervisor.terminal_outcome
            is AttemptTerminalOutcome.SUCCEEDED
        ):
            _validate_observation_batch_acquisition_metrics(
                self.finalization.observability_ledger,
                self.successful_observation_batches,
            )

    @property
    def coordinator_record(self) -> CoordinatorTerminalRecord:
        """Compatibility projection from the single accepted receipt."""

        return self.finalization.coordinator

    @property
    def finalization_record(self) -> SupervisorFinalizationRecord:
        """Compatibility projection from the single accepted receipt."""

        return self.finalization.supervisor


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
        producer_processing_request_id=(processing_attempt.processing_request_id),
        producer_processing_attempt_id=(processing_attempt.processing_attempt_id),
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
    cancellation_factory: Callable[[Any], AlignmentRuntimeProcessResult] | None = None,
    max_workers: int,
    resource_reservations: Sequence[RuntimeResourceReservation],
    telemetry_provider: RuntimeTelemetryProvider,
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
    normalized_jobs = tuple(jobs)
    recovery_marker.record_launch(runtime)
    successful_observation_batches: dict[str, PeakObservationBatch] = {}
    successful_cross_sample_peak_groups: dict[
        str, tuple[CrossSamplePeakGroupHypothesis, ...]
    ] = {}
    observed_results: tuple[AlignmentRuntimeProcessResult, ...] = ()

    def provide_owner_batch(
        _aggregate: RuntimeAttemptAggregate,
    ) -> RuntimeWorkerBatchReceipt:
        nonlocal observed_results
        if runtime.history.cancellation_accepted:
            if cancellation_factory is None:
                raise AlignmentRuntimeAdapterError(
                    "cancelled Alignment runtime requires cancellation_factory"
                )
            observed = tuple(
                cancellation_factory(job) for job in normalized_jobs
            )
        else:
            observed = tuple(
                run_process_jobs(
                    normalized_jobs,
                    worker=worker,
                    error_factory=error_factory,
                    max_workers=max_workers,
                    executor_factory=executor_factory,
                )
            )
        if any(
            not isinstance(item, AlignmentRuntimeProcessResult) for item in observed
        ):
            raise AlignmentRuntimeAdapterError(
                "Alignment runner returned an invalid runtime process result"
            )
        manifest_order = {
            key: index for index, key in enumerate(runtime.manifest.work_unit_keys)
        }
        ordered = tuple(
            sorted(
                observed,
                key=lambda item: manifest_order.get(
                    item.settlement.work_unit_key,
                    len(manifest_order),
                ),
            ),
        )
        observed_results = ordered
        for item in ordered:
            settlement = item.settlement
            if settlement.attempt != runtime.manifest.attempt:
                raise AlignmentRuntimeAdapterError(
                    "Alignment result references a different runtime attempt"
                )
            if (
                settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
                and item.peak_observation_batch is not None
            ):
                _coalesce_observation_batch(
                    successful_observation_batches,
                    work_unit_key=settlement.work_unit_key,
                    batch=item.peak_observation_batch,
                )
        return RuntimeWorkerBatchReceipt(
            settlements=tuple(item.settlement for item in ordered),
            resource_settlements=tuple(
                RuntimeResourceSettlementReceipt(
                    attempt=item.settlement.attempt,
                    resource_key=item.resource_key,
                    open_outcome=item.open_outcome,
                    cleanup_outcome=item.cleanup_outcome,
                )
                for item in ordered
            ),
            artifact_registrations=tuple(
                RuntimeArtifactRegistrationReceipt(
                    work_unit_key=item.settlement.work_unit_key,
                    artifact=artifact,
                )
                for item in ordered
                for artifact in item.runtime_artifacts
            ),
            acquisitions=tuple(
                RuntimeAcquisitionReceipt(
                    work_unit_key=item.settlement.work_unit_key,
                    acquisition=item.peak_observation_batch.acquisition,
                )
                for item in ordered
                if item.peak_observation_batch is not None
            ),
        )

    finalization = execute_runtime_attempt(
        runtime,
        resource_reservations,
        worker_batch_provider=provide_owner_batch,
        telemetry_provider=telemetry_provider,
        persister=recovery_marker,
    )
    current = finalization.coordinator.state
    accepted_by_key = {
        settlement.work_unit_key: settlement
        for settlement in current.runtime.settlements
    }
    for item in observed_results:
        settlement = item.settlement
        if (
            accepted_by_key.get(settlement.work_unit_key) != settlement
            or settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
            or item.cross_sample_peak_groups is None
        ):
            continue
        _coalesce_cross_sample_peak_groups(
            successful_cross_sample_peak_groups,
            work_unit_key=settlement.work_unit_key,
            groups=item.cross_sample_peak_groups,
        )
    if (
        not current.runtime.manifest_preflight_failed
        and not current.runtime.all_manifest_work_settled
    ):
        settled_keys = {
            settlement.work_unit_key for settlement in current.runtime.settlements
        }
        missing = tuple(
            key
            for key in current.runtime.manifest.work_unit_keys
            if key not in settled_keys
        )
        raise AlignmentRuntimeAdapterError(
            f"Alignment results did not settle manifest work: {missing!r}"
        )
    if current.runtime.integrity_failure is not None or current.failures:
        successful_observation_batches.clear()
        successful_cross_sample_peak_groups.clear()
    return AlignmentRuntimeApplicationResult(
        runtime=current.runtime,
        finalization=finalization,
        successful_observation_batches=tuple(
            (key, successful_observation_batches[key])
            for key in current.runtime.manifest.work_unit_keys
            if key in successful_observation_batches
        ),
        successful_cross_sample_peak_groups=tuple(
            (key, successful_cross_sample_peak_groups[key])
            for key in current.runtime.manifest.work_unit_keys
            if key in successful_cross_sample_peak_groups
        ),
    )


def canonical_alignment_peak_group_result_bytes(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> bytes:
    """Serialize one Alignment-owned scientific result without runtime policy."""

    from xic_extractor.alignment.cross_sample_peak_groups import (
        CrossSampleGroupIdentity,
        CrossSamplePeakGroupHypothesis,
        validate_cross_sample_group_population,
        validate_cross_sample_peak_group_identity,
    )

    if not isinstance(groups, tuple) or any(
        not isinstance(group, CrossSamplePeakGroupHypothesis) for group in groups
    ):
        raise AlignmentRuntimeAdapterError(
            "groups must be a tuple of CrossSamplePeakGroupHypothesis values"
        )
    try:
        identities = []
        for group in groups:
            validate_cross_sample_peak_group_identity(
                group,
                require_canonical=True,
            )
            identities.append(
                CrossSampleGroupIdentity(
                    group_hypothesis_id=group.group_hypothesis_id,
                    processing_request_id=group.processing_request_id,
                    member_subjects=group.member_subjects,
                    basis_bytes=group.identity_basis_bytes,
                    disposition=group.disposition,
                )
            )
        validate_cross_sample_group_population(tuple(identities))
    except ValueError as exc:
        raise AlignmentRuntimeAdapterError(
            "CrossSample canonical identity did not replay from its exact basis: "
            f"{exc}"
        ) from exc
    return (
        json.dumps(
            {
                "groups": [_jsonable_alignment_group(group) for group in groups],
                "schema_version": "alignment_peak_group_result_v1",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _jsonable_alignment_group(group: CrossSamplePeakGroupHypothesis) -> object:
    """Render retained canonical UTF-8 identity bases without losing bytes."""

    def render(value: object) -> object:
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AlignmentRuntimeAdapterError(
                    "CrossSample identity basis bytes must be UTF-8"
                ) from exc
        if isinstance(value, dict):
            return {key: render(nested) for key, nested in value.items()}
        if isinstance(value, tuple):
            return [render(item) for item in value]
        if isinstance(value, list):
            return [render(item) for item in value]
        return value

    return render(asdict(group))


def resolve_alignment_application_peak_group_result(
    application: AlignmentRuntimeApplicationResult,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> tuple[str, EvidenceArtifactRef]:
    """Resolve one exact owner-validated group carrier from an application result."""

    if not isinstance(application, AlignmentRuntimeApplicationResult):
        raise AlignmentRuntimeAdapterError(
            "application must be AlignmentRuntimeApplicationResult"
        )
    if application.runtime.integrity_failure is not None:
        raise AlignmentRuntimeAdapterError(
            "integrity-failed Alignment runtime cannot resolve scientific results"
        )
    expected = canonical_alignment_peak_group_result_bytes(groups)
    matches = tuple(
        (work_unit_key, observed_groups)
        for work_unit_key, observed_groups in (
            application.successful_cross_sample_peak_groups
        )
        if canonical_alignment_peak_group_result_bytes(observed_groups) == expected
    )
    if len(matches) != 1:
        raise AlignmentRuntimeAdapterError(
            "Alignment application must contain exactly one matching group result"
        )
    work_unit_key, observed_groups = matches[0]
    settlement = next(
        (
            item
            for item in application.runtime.settlements
            if item.work_unit_key == work_unit_key
        ),
        None,
    )
    if settlement is None or settlement.owner_result_ref is None:
        raise AlignmentRuntimeAdapterError(
            "matching Alignment group result is missing its owner artifact"
        )
    _validate_cross_sample_peak_group_binding(
        settlement=settlement,
        groups=observed_groups,
        upstream_artifact_ids=settlement.owner_result_ref.upstream_artifact_ids,
    )
    return work_unit_key, settlement.owner_result_ref


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
        raise AlignmentRuntimeAdapterError(
            "runtime acquisition metrics must match accepted observation batches"
        ) from exc


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


def _validate_cross_sample_peak_group_result(
    result: AlignmentRuntimeProcessResult,
) -> None:
    groups = result.cross_sample_peak_groups
    if groups is None:
        raise AlignmentRuntimeAdapterError(
            "cross_sample_peak_groups must be owner-produced"
        )
    settlement = result.settlement
    if settlement.outcome is not WorkUnitTerminalOutcome.SUCCEEDED:
        raise AlignmentRuntimeAdapterError(
            "only successful Alignment work may carry cross-sample peak groups"
        )
    upstream_artifact_ids = result.cross_sample_peak_group_upstream_artifact_ids
    if upstream_artifact_ids is None:
        raise AlignmentRuntimeAdapterError(
            "cross-sample peak groups require caller-provided upstream artifact IDs"
        )
    _validate_cross_sample_peak_group_binding(
        settlement=settlement,
        groups=groups,
        upstream_artifact_ids=upstream_artifact_ids,
    )


def _validate_cross_sample_peak_group_binding(
    *,
    settlement: WorkUnitSettlement,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    upstream_artifact_ids: tuple[str, ...],
) -> None:
    artifact = settlement.owner_result_ref
    if artifact is None:
        raise AlignmentRuntimeAdapterError(
            "cross-sample peak groups require an Alignment result artifact"
        )
    if artifact.semantic_contract != _ARTIFACT_SEMANTIC_CONTRACT:
        raise AlignmentRuntimeAdapterError(
            "cross-sample peak groups require an Alignment-owned result artifact"
        )
    if not isinstance(upstream_artifact_ids, tuple) or any(
        not isinstance(artifact_id, str) or not artifact_id
        for artifact_id in upstream_artifact_ids
    ):
        raise AlignmentRuntimeAdapterError(
            "cross-sample peak-group upstream artifact IDs must be an explicit tuple"
        )
    if artifact.upstream_artifact_ids != upstream_artifact_ids:
        raise AlignmentRuntimeAdapterError(
            "Alignment result artifact must close the caller-provided upstream IDs"
        )
    payload = canonical_alignment_peak_group_result_bytes(groups)
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
            "Alignment result artifact does not bind the cross-sample peak groups"
        ) from exc


def _coalesce_cross_sample_peak_groups(
    results: dict[str, tuple[CrossSamplePeakGroupHypothesis, ...]],
    *,
    work_unit_key: str,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
) -> None:
    existing = results.get(work_unit_key)
    if existing is None:
        results[work_unit_key] = groups
        return
    if canonical_alignment_peak_group_result_bytes(existing) != (
        canonical_alignment_peak_group_result_bytes(groups)
    ):
        raise AlignmentRuntimeAdapterError(
            "replayed Alignment cross-sample peak groups conflict with accepted work"
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
    "canonical_alignment_peak_group_result_bytes",
    "execute_alignment_process_runtime",
    "mint_alignment_process_result_artifact",
    "resolve_alignment_application_peak_group_result",
]
