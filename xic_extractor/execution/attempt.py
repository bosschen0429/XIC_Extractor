"""Single construction and projection authority for one runtime attempt.

Workflow adapters translate their worker outputs into the typed receipts in
this module.  This owner applies the existing reducers, derives the complete
observability ledger from accepted facts plus narrow external telemetry
samples, and emits one content-bound finalization receipt.  It does not collect
live telemetry, choose storage, or acquire scientific/product authority.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from dataclasses import InitVar, dataclass, field, replace
from typing import final

from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution._finalization import (
    AttemptFinalizationState,
    CoordinatorTerminalRecord,
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationEventKind,
    SupervisorFinalizationRecord,
    _record_runtime_resource_cleanups,
    _record_runtime_resource_opens,
    _register_runtime_artifacts,
    _reserve_runtime_resources,
    begin_attempt_finalization,
    mint_supervisor_finalization,
    propose_attempt_terminal,
)
from xic_extractor.execution._lifecycle import (
    AttemptLifecycleEventKind,
    AttemptRuntime,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
    _settle_work_units,
    _start_runtime_attempt,
    append_attempt_lifecycle_event,
)
from xic_extractor.execution._lifecycle import (
    mint_work_unit_settlement as _mint_work_unit_settlement,
)
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
    _index_event_offsets,
    _mint_runtime_observability_ledger,
)
from xic_extractor.execution._receipts import (
    _RUNTIME_OWNER_MEASUREMENT_SOURCE,
    _RUNTIME_OWNER_MINT_TOKEN,
    RuntimeAcquisitionMetrics,
    RuntimeAcquisitionReceipt,
    RuntimeArtifactIoSample,
    RuntimeArtifactRegistrationReceipt,
    RuntimeCancellationRequest,
    RuntimeExecutionPhase,
    RuntimeExecutionPhaseError,
    RuntimeFinalizationPersister,
    RuntimeFinalizationReceipt,
    RuntimeProcessTreePeakRssSample,
    RuntimeRawOpenMetrics,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeTelemetrySamples,
    RuntimeWorkerBatchProvider,
    RuntimeWorkerBatchReceipt,
    RuntimeWorkerPeakRssSample,
    _acquisition_receipts_match,
    _required_text,
    accept_runtime_finalization_receipt,
    compact_runtime_acquisition_receipt,
    derive_runtime_acquisition_metrics,
    derive_runtime_raw_open_metrics,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class RuntimeCancellationIngress:
    """Thread-safe, attempt-bound cancellation ingress for one runtime attempt."""

    def __init__(self, attempt: ProcessingAttemptIdentity) -> None:
        if not isinstance(attempt, ProcessingAttemptIdentity):
            raise RuntimeFinalizationContractError(
                "cancellation ingress attempt must be owner-issued"
            )
        self._attempt = attempt
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._request: RuntimeCancellationRequest | None = None
        self._cutoff = False

    @property
    def attempt(self) -> ProcessingAttemptIdentity:
        return self._attempt

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def accepted_request(self) -> RuntimeCancellationRequest | None:
        with self._lock:
            return self._request

    def request_cancellation(
        self,
        request: RuntimeCancellationRequest,
    ) -> bool:
        """Submit one cancellation request.

        Raises RuntimeFinalizationContractError if request references a
        different attempt.  Returns True if this call transitioned state to
        cancelled, False if already cancelled or past cutoff.
        """
        if not isinstance(request, RuntimeCancellationRequest):
            raise RuntimeFinalizationContractError(
                "cancellation request must be a RuntimeCancellationRequest"
            )
        if request.attempt != self._attempt:
            raise RuntimeFinalizationContractError(
                "cancellation request references a different runtime attempt"
            )
        with self._lock:
            if self._cutoff or self._request is not None:
                return False
            self._request = request
            self._event.set()
            return True

    def close(self) -> RuntimeCancellationRequest | None:
        """Mark terminal cutoff; late cancellation requests are ignored."""
        with self._lock:
            self._cutoff = True
            return self._request


@final
@dataclass(frozen=True, slots=True)
class RuntimeAttemptAggregate:
    """The sole runtime-owned state carried across adapter boundaries."""

    state: AttemptFinalizationState
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...] = ()
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeAttemptAggregate is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "RuntimeAttemptAggregate must be minted by the runtime owner"
            )

    @property
    def runtime(self) -> AttemptRuntime:
        return self.state.runtime


def _mint_runtime_attempt_aggregate(
    *,
    state: AttemptFinalizationState,
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...] = (),
) -> RuntimeAttemptAggregate:
    return RuntimeAttemptAggregate(
        state=state,
        acquisitions=acquisitions,
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


def _mint_runtime_finalization_receipt(
    *,
    coordinator: CoordinatorTerminalRecord,
    supervisor: SupervisorFinalizationRecord,
) -> RuntimeFinalizationReceipt:
    return RuntimeFinalizationReceipt(
        coordinator=coordinator,
        supervisor=supervisor,
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


def start_runtime_attempt(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
    cancellation_request: RuntimeCancellationRequest | None = None,
) -> AttemptRuntime:
    """Create one running, cancelled, or preflight-terminalized owner state."""

    if cancellation_request is not None:
        if not isinstance(cancellation_request, RuntimeCancellationRequest):
            raise RuntimeFinalizationContractError(
                "cancellation_request must be a RuntimeCancellationRequest or None"
            )
        if cancellation_request.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "cancellation request references a different runtime attempt"
            )
    runtime = _start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )
    if cancellation_request is None or runtime.manifest_preflight_failed:
        return runtime
    return replace(
        runtime,
        history=append_attempt_lifecycle_event(
            runtime.history,
            AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
        ),
    )


def mint_work_unit_settlement(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    outcome: WorkUnitTerminalOutcome,
    owner_result_ref: EvidenceArtifactRef | None = None,
    failure_detail: WorkUnitFailureDetail | None = None,
) -> WorkUnitSettlement:
    """Mint one typed work settlement through the runtime authority facade."""

    return _mint_work_unit_settlement(
        attempt=attempt,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=owner_result_ref,
        failure_detail=failure_detail,
    )


def _prepare_runtime_attempt(
    runtime: AttemptRuntime,
    reservations: Sequence[RuntimeResourceReservation],
) -> RuntimeAttemptAggregate:
    """Create the one aggregate and reserve resources before dispatch."""

    if not isinstance(runtime, AttemptRuntime):
        raise RuntimeFinalizationContractError("runtime must be an AttemptRuntime")
    state = begin_attempt_finalization(runtime)
    if runtime.manifest_preflight_failed:
        return _mint_runtime_attempt_aggregate(state=state)
    if isinstance(reservations, (str, bytes)):
        raise RuntimeFinalizationContractError(
            "reservations must contain RuntimeResourceReservation values"
        )
    try:
        batch = tuple(reservations)
    except TypeError as exc:
        raise RuntimeFinalizationContractError(
            "reservations must contain RuntimeResourceReservation values"
        ) from exc

    def checked_reservations() -> Iterable[RuntimeResourceReservation]:
        for reservation in batch:
            if not isinstance(reservation, RuntimeResourceReservation):
                raise RuntimeFinalizationContractError(
                    "reservations must contain RuntimeResourceReservation values"
                )
            yield reservation

    state = _reserve_runtime_resources(state, checked_reservations())
    return _mint_runtime_attempt_aggregate(state=state)


def _apply_runtime_worker_batch(
    aggregate: RuntimeAttemptAggregate,
    batch: RuntimeWorkerBatchReceipt,
) -> RuntimeAttemptAggregate:
    """Apply one translated batch in owner-defined canonical order."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not isinstance(batch, RuntimeWorkerBatchReceipt):
        raise RuntimeFinalizationContractError(
            "batch must be a RuntimeWorkerBatchReceipt"
        )
    if aggregate.runtime.manifest_preflight_failed:
        raise RuntimeFinalizationContractError(
            "preflight-terminalized attempt cannot accept worker facts"
        )
    state = aggregate.state
    attempt = state.runtime.manifest.attempt
    manifest_order = {
        key: index for index, key in enumerate(state.runtime.manifest.work_unit_keys)
    }
    resource_order = {
        record.reservation.resource_key: index
        for index, record in enumerate(state.resources)
    }
    resource_settlements = tuple(
        sorted(
            batch.resource_settlements,
            key=lambda item: resource_order.get(
                item.resource_key,
                len(resource_order),
            ),
        )
    )
    for resource_receipt in resource_settlements:
        if resource_receipt.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "resource receipt references a different runtime attempt"
            )
    state = _record_runtime_resource_opens(
        state,
        ((item.resource_key, item.open_outcome) for item in resource_settlements),
    )

    def ordered_settlements() -> Iterable[WorkUnitSettlement]:
        for settlement in sorted(
            batch.settlements,
            key=lambda item: manifest_order.get(
                item.work_unit_key, len(manifest_order)
            ),
        ):
            if settlement.attempt != attempt:
                raise RuntimeFinalizationContractError(
                    "work settlement references a different runtime attempt"
                )
            yield settlement

    if batch.settlements:
        state = replace(
            state,
            runtime=_settle_work_units(state.runtime, ordered_settlements()),
        )

    state = _register_runtime_artifacts(
        state,
        (
            (registration.work_unit_key, registration.artifact)
            for registration in sorted(
                batch.artifact_registrations,
                key=lambda item: (
                    manifest_order.get(item.work_unit_key, len(manifest_order)),
                    item.artifact.evidence_artifact_id,
                ),
            )
        ),
    )

    state = _record_runtime_resource_cleanups(
        state,
        ((item.resource_key, item.cleanup_outcome) for item in resource_settlements),
    )

    accepted_acquisitions: dict[str, list[RuntimeAcquisitionReceipt]] = {}
    for existing in aggregate.acquisitions:
        accepted_acquisitions.setdefault(existing.work_unit_key, []).append(existing)
    settlements_by_key = {
        item.work_unit_key: item for item in state.runtime.settlements
    }
    submitted_work_keys = {item.work_unit_key for item in batch.settlements}
    for acquisition_receipt in sorted(
        batch.acquisitions,
        key=lambda item: manifest_order.get(item.work_unit_key, len(manifest_order)),
    ):
        accepted_settlement = settlements_by_key.get(acquisition_receipt.work_unit_key)
        if (
            accepted_settlement is None
            or acquisition_receipt.work_unit_key not in submitted_work_keys
        ):
            raise RuntimeFinalizationContractError(
                "acquisition receipt requires a submitted terminal work result"
            )
        existing_for_work = accepted_acquisitions.setdefault(
            acquisition_receipt.work_unit_key,
            [],
        )
        replay_index = next(
            (
                index
                for index, existing in enumerate(existing_for_work)
                if _acquisition_receipts_match(existing, acquisition_receipt)
            ),
            None,
        )
        if replay_index is not None:
            existing_for_work[replay_index] = acquisition_receipt
            continue
        acquisition = acquisition_receipt.acquisition
        acquisition_identity = (
            None
            if isinstance(acquisition, RuntimeAcquisitionMetrics)
            else acquisition.capability.capability_id
        )
        if any(
            isinstance(existing.acquisition, RuntimeAcquisitionMetrics)
            or acquisition_identity is None
            or existing.acquisition.capability.capability_id == acquisition_identity
            for existing in existing_for_work
        ):
            raise RuntimeFinalizationContractError(
                "conflicting acquisition receipt for one work unit"
            )
        existing_for_work.append(acquisition_receipt)

    return _mint_runtime_attempt_aggregate(
        state=state,
        acquisitions=tuple(
            acquisition
            for key in state.runtime.manifest.work_unit_keys
            for acquisition in accepted_acquisitions.get(key, ())
        ),
    )


def _finalize_runtime_attempt(
    aggregate: RuntimeAttemptAggregate,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    persister: RuntimeFinalizationPersister,
) -> RuntimeFinalizationReceipt:
    """Derive, persist, and accept one canonical terminal receipt."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not callable(telemetry_provider):
        raise RuntimeFinalizationContractError("telemetry_provider must be callable")
    if not callable(persister):
        raise RuntimeFinalizationContractError("persister must be callable")
    coordinator = propose_attempt_terminal(aggregate.state)
    terminal_aggregate = _mint_runtime_attempt_aggregate(
        state=coordinator.state,
        acquisitions=aggregate.acquisitions,
    )
    try:
        telemetry = telemetry_provider(coordinator)
    except Exception as exc:
        raise RuntimeExecutionPhaseError(
            coordinator.state.runtime.manifest.attempt,
            RuntimeExecutionPhase.TELEMETRY_PROVIDER,
        ) from exc
    ledger = _derive_runtime_observability_ledger(
        terminal_aggregate,
        coordinator,
        telemetry=telemetry,
    )
    supervisor = mint_supervisor_finalization(
        coordinator,
        observability_ledger=ledger,
    )
    proposed = _mint_runtime_finalization_receipt(
        coordinator=coordinator,
        supervisor=supervisor,
    )
    try:
        submitted = persister(proposed)
    except Exception as exc:
        raise RuntimeExecutionPhaseError(
            coordinator.state.runtime.manifest.attempt,
            RuntimeExecutionPhase.PERSISTER,
        ) from exc
    return accept_runtime_finalization_receipt(proposed, submitted)


def _accept_cancellation_into_aggregate(
    aggregate: RuntimeAttemptAggregate,
) -> RuntimeAttemptAggregate:
    new_history = append_attempt_lifecycle_event(
        aggregate.runtime.history,
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
    )
    first_sequence = len(new_history.events) + 1
    shifted_events = tuple(
        replace(ev, sequence=first_sequence + idx)
        for idx, ev in enumerate(aggregate.state.events)
    )
    return _mint_runtime_attempt_aggregate(
        state=replace(
            aggregate.state,
            runtime=replace(
                aggregate.runtime,
                history=new_history,
            ),
            events=shifted_events,
        ),
        acquisitions=aggregate.acquisitions,
    )


def execute_runtime_attempt(
    runtime: AttemptRuntime,
    reservations: Sequence[RuntimeResourceReservation],
    *,
    worker_batch_provider: RuntimeWorkerBatchProvider,
    telemetry_provider: RuntimeTelemetryProvider,
    persister: RuntimeFinalizationPersister,
    cancellation_ingress: RuntimeCancellationIngress | None = None,
) -> RuntimeFinalizationReceipt:
    """Prepare, optionally dispatch, settle, and finalize one runtime attempt.

    Rejected manifest preflight remains a first-class owner state: the worker
    provider is not invoked, no resources are reserved, and the same terminal
    receipt/persistence path closes the attempt. Accepted pre-dispatch
    cancellation likewise skips the worker provider; the owner settles every
    manifest work unit as cancelled and closes reserved resources as not
    opened. Consumers therefore cannot split preparation or settlement from
    finalization.
    """

    if not callable(worker_batch_provider):
        raise RuntimeFinalizationContractError("worker_batch_provider must be callable")
    if cancellation_ingress is not None:
        if not isinstance(cancellation_ingress, RuntimeCancellationIngress):
            raise RuntimeFinalizationContractError(
                "cancellation_ingress must be a RuntimeCancellationIngress or None"
            )
        if cancellation_ingress.attempt != runtime.manifest.attempt:
            raise RuntimeFinalizationContractError(
                "cancellation ingress references a different runtime attempt"
            )
    aggregate = _prepare_runtime_attempt(runtime, reservations)
    if (
        cancellation_ingress is not None
        and cancellation_ingress.accepted_request is not None
        and not aggregate.runtime.history.cancellation_accepted
    ):
        aggregate = _accept_cancellation_into_aggregate(aggregate)
    if aggregate.runtime.history.cancellation_accepted:
        attempt = aggregate.runtime.manifest.attempt
        aggregate = _apply_runtime_worker_batch(
            aggregate,
            RuntimeWorkerBatchReceipt(
                settlements=tuple(
                    mint_work_unit_settlement(
                        attempt=attempt,
                        work_unit_key=work_unit_key,
                        outcome=WorkUnitTerminalOutcome.CANCELLED,
                        failure_detail=WorkUnitFailureDetail(
                            category=WorkUnitFailureCategory.CANCELLATION,
                            stage="runtime-owner-pre-dispatch",
                            source_reference=(
                                f"runtime-owner:{attempt.processing_attempt_id}"
                            ),
                            message=(
                                "attempt cancellation accepted before worker dispatch"
                            ),
                        ),
                    )
                    for work_unit_key in aggregate.runtime.manifest.work_unit_keys
                ),
                resource_settlements=tuple(
                    RuntimeResourceSettlementReceipt(
                        attempt=attempt,
                        resource_key=record.reservation.resource_key,
                        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
                        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                    )
                    for record in aggregate.state.resources
                ),
            ),
        )
    elif not aggregate.runtime.manifest_preflight_failed:
        try:
            worker_batch = worker_batch_provider(aggregate)
        except Exception as exc:
            if cancellation_ingress is not None:
                cancellation_ingress.close()
            raise RuntimeExecutionPhaseError(
                aggregate.runtime.manifest.attempt,
                RuntimeExecutionPhase.WORKER_BATCH_PROVIDER,
            ) from exc
        accepted_cancel: RuntimeCancellationRequest | None = None
        if cancellation_ingress is not None:
            accepted_cancel = cancellation_ingress.close()
        if (
            accepted_cancel is not None
            and not aggregate.runtime.history.cancellation_accepted
        ):
            aggregate = _accept_cancellation_into_aggregate(aggregate)
        aggregate = _apply_runtime_worker_batch(
            aggregate,
            worker_batch,
        )
    if cancellation_ingress is not None:
        cancellation_ingress.close()
    return _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=telemetry_provider,
        persister=persister,
    )


def _derive_runtime_observability_ledger(
    aggregate: RuntimeAttemptAggregate,
    coordinator: CoordinatorTerminalRecord,
    *,
    telemetry: RuntimeTelemetrySamples,
) -> RuntimeObservabilityLedger:
    """Project owner facts and narrow telemetry into observability."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    if coordinator.state != aggregate.state:
        raise RuntimeFinalizationContractError(
            "coordinator must bind the exact runtime aggregate state"
        )
    acquisition_batch = aggregate.acquisitions
    if not isinstance(telemetry, RuntimeTelemetrySamples):
        raise RuntimeFinalizationContractError(
            "telemetry must be RuntimeTelemetrySamples"
        )
    state = coordinator.state
    attempt = state.runtime.manifest.attempt
    shapes = _runtime_observation_shapes(coordinator)
    offsets = telemetry.event_monotonic_offsets_ns
    if offsets is not None and len(offsets) != len(shapes):
        raise RuntimeFinalizationContractError(
            "telemetry offsets must match the owner-derived event count"
        )
    if offsets is not None and (
        not offsets or offsets[0] != 0 or offsets != tuple(sorted(offsets))
    ):
        raise RuntimeFinalizationContractError(
            "telemetry offsets must start at zero and be monotonic"
        )
    events = tuple(
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=index,
            monotonic_offset_ns=(None if offsets is None else offsets[index - 1]),
            kind=kind,
            subject=subject,
            subject_key=subject_key,
        )
        for index, (kind, subject, subject_key) in enumerate(shapes, start=1)
    )
    worker_keys = _started_worker_keys(state)
    supplied_worker_keys = tuple(
        sample.worker_key for sample in telemetry.worker_peak_rss
    )
    if set(supplied_worker_keys) != set(worker_keys):
        raise RuntimeFinalizationContractError(
            "worker RSS samples must exactly match owner-derived worker keys"
        )
    worker_samples = {sample.worker_key: sample for sample in telemetry.worker_peak_rss}
    acquisition_metrics = derive_runtime_acquisition_metrics(
        tuple(item.acquisition for item in acquisition_batch)
    )
    acquisition_counts = {
        name: getattr(acquisition_metrics, name)
        for name in (
            "logical_xic_request_count",
            "xic_batch_count",
            "adapter_chromatogram_call_count",
            "trace_point_count",
        )
    }
    count_values = {
        "raw_open_count": sum(
            record.reservation.kind is RuntimeResourceKind.ACQUISITION_SESSION
            and record.open_outcome is RuntimeResourceOpenOutcome.OPENED
            for record in state.resources
        ),
        **acquisition_counts,
    }
    artifact_io = telemetry.artifact_io
    if artifact_io is not None:
        count_values.update(
            artifact_read_count=artifact_io.read_count,
            artifact_read_bytes=artifact_io.read_bytes,
            artifact_write_count=artifact_io.write_count,
            artifact_write_bytes=artifact_io.write_bytes,
        )
    if state.runtime.manifest_preflight_failed and (
        offsets is None or artifact_io is None
    ):
        raise RuntimeFinalizationContractError(
            "manifest-preflight failure requires observed zero-work telemetry"
        )
    if state.runtime.manifest_preflight_failed and any(count_values.values()):
        raise RuntimeFinalizationContractError(
            "manifest-preflight failure cannot report runtime work or I/O"
        )
    metrics: list[RuntimeMetricRecord] = []
    for name in (
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
    ):
        metrics.append(
            _measured_metric(
                name=name,
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                unit="bytes" if name.endswith("_bytes") else "count",
                aggregation="sum",
                value=count_values[name],
            )
        )
    for name in (
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
    ):
        if artifact_io is not None:
            metrics.append(
                _measured_metric(
                    name=name,
                    scope=RuntimeMetricScope.ATTEMPT,
                    scope_key=attempt.processing_attempt_id,
                    unit="bytes" if name.endswith("_bytes") else "count",
                    aggregation="sum",
                    value=count_values[name],
                )
            )
        else:
            metrics.append(
                _unsupported_metric(
                    name=name,
                    scope=RuntimeMetricScope.ATTEMPT,
                    scope_key=attempt.processing_attempt_id,
                    unit="bytes" if name.endswith("_bytes") else "count",
                    aggregation="sum",
                    reason=_required_text(
                        telemetry.unavailable_reason, field="unavailable_reason"
                    ),
                )
            )
    if state.runtime.manifest_preflight_failed:
        metrics.extend(
            _preflight_terminal_metrics(
                attempt=attempt,
                events=events,
            )
        )
        return _mint_runtime_observability_ledger(
            attempt=attempt,
            work_unit_keys=(),
            events=events,
            metrics=tuple(metrics),
        )
    if offsets is None:
        unavailable_reason = _required_text(
            telemetry.unavailable_reason, field="unavailable_reason"
        )
        metrics.extend(
            _unsupported_metric(
                name=name,
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                unit="seconds",
                aggregation="last",
                reason=unavailable_reason,
            )
            for name in ("coordinator_work_seconds", "attempt_wall_seconds")
        )
        metrics.extend(
            _unsupported_metric(
                name="stage_or_unit_elapsed_seconds",
                scope=RuntimeMetricScope.WORK_UNIT,
                scope_key=work_unit_key,
                unit="seconds",
                aggregation="sum",
                reason=unavailable_reason,
            )
            for work_unit_key in state.runtime.manifest.work_unit_keys
        )
    else:
        event_offsets = _index_event_offsets(events)
        metrics.extend(
            (
                _measured_metric(
                    name="coordinator_work_seconds",
                    scope=RuntimeMetricScope.ATTEMPT,
                    scope_key=attempt.processing_attempt_id,
                    unit="seconds",
                    aggregation="last",
                    value=(
                        _event_offset(event_offsets, "terminal_proposal")
                        - _event_offset(event_offsets, "attempt_started")
                    )
                    / 1_000_000_000,
                ),
                _measured_metric(
                    name="attempt_wall_seconds",
                    scope=RuntimeMetricScope.ATTEMPT,
                    scope_key=attempt.processing_attempt_id,
                    unit="seconds",
                    aggregation="last",
                    value=(
                        _event_offset(event_offsets, "terminal_outcome")
                        - _event_offset(event_offsets, "attempt_queued")
                    )
                    / 1_000_000_000,
                ),
            )
        )
        metrics.extend(
            _measured_metric(
                name="stage_or_unit_elapsed_seconds",
                scope=RuntimeMetricScope.WORK_UNIT,
                scope_key=work_unit_key,
                unit="seconds",
                aggregation="sum",
                value=(
                    _event_offset(event_offsets, "work_unit_settled", work_unit_key)
                    - _event_offset(event_offsets, "work_unit_started", work_unit_key)
                )
                / 1_000_000_000,
            )
            for work_unit_key in state.runtime.manifest.work_unit_keys
        )
    if worker_keys:
        metrics.extend(
            _rss_metric(
                name="worker_peak_rss_bytes",
                scope=RuntimeMetricScope.WORKER,
                scope_key=worker_key,
                availability=worker_samples[worker_key].availability,
                value=worker_samples[worker_key].value,
                reason=worker_samples[worker_key].reason,
            )
            for worker_key in worker_keys
        )
    else:
        metrics.append(
            _rss_metric(
                name="worker_peak_rss_bytes",
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                value=None,
                reason="not_applicable_serial",
            )
        )
    metrics.append(
        _rss_metric(
            name="process_tree_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt.processing_attempt_id,
            availability=telemetry.process_tree_peak_rss.availability,
            value=telemetry.process_tree_peak_rss.value,
            reason=telemetry.process_tree_peak_rss.reason,
        )
    )
    return _mint_runtime_observability_ledger(
        attempt=attempt,
        work_unit_keys=state.runtime.manifest.work_unit_keys,
        events=events,
        metrics=tuple(metrics),
    )


def runtime_observation_event_count(
    coordinator: CoordinatorTerminalRecord,
) -> int:
    """Return the owner-derived event count expected from a telemetry provider."""

    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    return len(_runtime_observation_shapes(coordinator))


def runtime_started_worker_keys(
    coordinator: CoordinatorTerminalRecord,
) -> tuple[str, ...]:
    """Return worker identities proven started by finalized process resources."""

    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    return _started_worker_keys(coordinator.state)


def _runtime_observation_shapes(
    coordinator: CoordinatorTerminalRecord,
) -> tuple[tuple[str, RuntimeObservationSubject, str], ...]:
    state = coordinator.state
    shapes: list[tuple[str, RuntimeObservationSubject, str]] = []

    def append_lifecycle_phase(
        kinds: frozenset[RuntimeLifecycleEventKind],
        subject: RuntimeObservationSubject,
    ) -> None:
        for lifecycle_event in state.events:
            if lifecycle_event.kind not in kinds:
                continue
            if subject is RuntimeObservationSubject.ATTEMPT:
                subject_key = "attempt"
            else:
                lifecycle_subject_key = lifecycle_event.subject_key
                if lifecycle_subject_key is None:
                    raise RuntimeFinalizationContractError(
                        f"{subject.value} event is missing its owner correlation"
                    )
                subject_key = lifecycle_subject_key
            shapes.append((lifecycle_event.kind.value, subject, subject_key))

    for event in state.runtime.history.events:
        shapes.append((event.kind.value, RuntimeObservationSubject.ATTEMPT, "attempt"))
        if event.sequence == 1 and not state.runtime.manifest_preflight_failed:
            shapes.append(
                ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest")
            )
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.RESOURCE_RESERVED}),
        RuntimeObservationSubject.RESOURCE,
    )
    append_lifecycle_phase(
        frozenset(
            {
                RuntimeLifecycleEventKind.RESOURCE_OPENED,
                RuntimeLifecycleEventKind.RESOURCE_OPEN_FAILED,
                RuntimeLifecycleEventKind.RESOURCE_NOT_OPENED,
                RuntimeLifecycleEventKind.RESOURCE_CONDITION_NOT_ACTIVATED,
                RuntimeLifecycleEventKind.RESOURCE_OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
            }
        ),
        RuntimeObservationSubject.RESOURCE,
    )
    for worker_key in _started_worker_keys(state):
        shapes.append(("worker_started", RuntimeObservationSubject.WORKER, worker_key))
    for work_unit_key in state.runtime.manifest.work_unit_keys:
        shapes.extend(
            (
                (
                    "work_unit_submitted",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
                (
                    "work_unit_started",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
                (
                    "work_unit_settled",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
            )
        )
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.ARTIFACT_REGISTERED}),
        RuntimeObservationSubject.ARTIFACT,
    )
    append_lifecycle_phase(
        frozenset(
            {
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_SUCCEEDED,
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_FAILED,
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_UNKNOWN_AFTER_WORKER_LOSS,
            }
        ),
        RuntimeObservationSubject.RESOURCE,
    )
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.TERMINAL_PROPOSAL}),
        RuntimeObservationSubject.ATTEMPT,
    )
    shapes.append(
        (
            SupervisorFinalizationEventKind.TERMINAL_ACKNOWLEDGED.value,
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    )
    for artifact in state.artifacts:
        kind = (
            SupervisorFinalizationEventKind.ARTIFACT_OFFERED_TO_OWNER
            if artifact.proposed_disposition
            is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            else SupervisorFinalizationEventKind.ARTIFACT_QUARANTINED_DIAGNOSTIC
        )
        shapes.append(
            (
                kind.value,
                RuntimeObservationSubject.ARTIFACT,
                artifact.artifact.evidence_artifact_id,
            )
        )
    shapes.append(
        (
            SupervisorFinalizationEventKind.TERMINAL_OUTCOME.value,
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    )
    return tuple(shapes)


def _started_worker_keys(state: AttemptFinalizationState) -> tuple[str, ...]:
    started_outcomes = {
        RuntimeResourceOpenOutcome.OPENED,
        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
    }
    return tuple(
        dict.fromkeys(
            record.reservation.worker_key
            for record in state.resources
            if record.reservation.kind is RuntimeResourceKind.PROCESS_WORKER
            and record.open_outcome in started_outcomes
        )
    )


def _preflight_terminal_metrics(
    *,
    attempt: ProcessingAttemptIdentity,
    events: tuple[RuntimeObservationEvent, ...],
) -> tuple[RuntimeMetricRecord, ...]:
    attempt_id = attempt.processing_attempt_id
    unavailable_reason = "not_started_preflight_failure"
    event_offsets = _index_event_offsets(events)
    return (
        _unsupported_metric(
            name="coordinator_work_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="last",
            reason=unavailable_reason,
        ),
        _measured_metric(
            name="attempt_wall_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="last",
            value=(
                _event_offset(event_offsets, "terminal_outcome")
                - _event_offset(event_offsets, "attempt_queued")
            )
            / 1_000_000_000,
        ),
        _unsupported_metric(
            name="stage_or_unit_elapsed_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="sum",
            reason=unavailable_reason,
        ),
        _unsupported_metric(
            name="worker_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="bytes",
            aggregation="max",
            reason=unavailable_reason,
        ),
        _unsupported_metric(
            name="process_tree_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="bytes",
            aggregation="max",
            reason=unavailable_reason,
        ),
    )


def _event_offset(
    event_offsets: dict[tuple[str, str], tuple[int, ...]],
    kind: str,
    subject_key: str = "attempt",
) -> int:
    matches = event_offsets.get((kind, subject_key), ())
    if len(matches) != 1:
        raise RuntimeFinalizationContractError(
            "owner-derived elapsed metric lacks one correlated event"
        )
    return matches[0]


def _measured_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    unit: str,
    aggregation: str,
    value: int | float,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit=unit,
        aggregation=aggregation,
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=RuntimeMetricAvailability.MEASURED,
        value=value,
    )


def _rss_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    availability: RuntimeMetricAvailability,
    value: int | None,
    reason: str | None,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit="bytes",
        aggregation="max",
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=availability,
        value=value,
        reason=reason,
    )


def _unsupported_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    unit: str,
    aggregation: str,
    reason: str,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit=unit,
        aggregation=aggregation,
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=RuntimeMetricAvailability.UNSUPPORTED,
        value=None,
        reason=reason,
    )


__all__ = [
    "RuntimeAcquisitionMetrics",
    "RuntimeAcquisitionReceipt",
    "RuntimeArtifactIoSample",
    "RuntimeArtifactRegistrationReceipt",
    "RuntimeAttemptAggregate",
    "RuntimeCancellationIngress",
    "RuntimeCancellationRequest",
    "RuntimeExecutionPhase",
    "RuntimeExecutionPhaseError",
    "RuntimeFinalizationPersister",
    "RuntimeFinalizationReceipt",
    "RuntimeProcessTreePeakRssSample",
    "RuntimeRawOpenMetrics",
    "RuntimeResourceSettlementReceipt",
    "RuntimeTelemetryProvider",
    "RuntimeTelemetrySamples",
    "RuntimeWorkerBatchReceipt",
    "RuntimeWorkerBatchProvider",
    "RuntimeWorkerPeakRssSample",
    "accept_runtime_finalization_receipt",
    "compact_runtime_acquisition_receipt",
    "derive_runtime_acquisition_metrics",
    "derive_runtime_raw_open_metrics",
    "execute_runtime_attempt",
    "mint_work_unit_settlement",
    "runtime_observation_event_count",
    "runtime_started_worker_keys",
    "start_runtime_attempt",
]
