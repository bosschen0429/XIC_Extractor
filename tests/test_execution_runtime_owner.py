from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    canonical_coordinator_terminal_record_bytes,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import (
    RuntimeMetricAvailability,
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeAttemptAggregate,
    RuntimeCancellationRequest,
    RuntimeFinalizationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeWorkerBatchReceipt,
    _apply_runtime_worker_batch,
    _derive_runtime_observability_ledger,
    _finalize_runtime_attempt,
    _prepare_runtime_attempt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    mint_targeted_process_result_ref,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_owner_start_accepts_exact_typed_cancellation_request(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-start")

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test:v1",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )

    assert runtime.history.cancellation_accepted is True
    assert runtime.history.events[-1].kind is (
        AttemptLifecycleEventKind.CANCELLATION_ACCEPTED
    )


def test_owner_start_rejects_foreign_cancellation_request(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-start")
    foreign = _attempt(tmp_path, stem="foreign-cancellation")

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="different runtime attempt",
    ):
        start_runtime_attempt(
            attempt=attempt,
            work_unit_keys=("raw:a",),
            owner_completeness_attestation="runtime-owner-test:v1",
            cancellation_request=RuntimeCancellationRequest(attempt=foreign),
        )


def test_owner_start_does_not_relabel_rejected_manifest_as_cancelled(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-invalid-manifest")

    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=(),
        owner_completeness_attestation="runtime-owner-test:v1",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )

    assert runtime.manifest_preflight_failed is True
    assert runtime.history.cancellation_accepted is False
    assert runtime.history.events[-1].proposed_outcome is AttemptTerminalOutcome.FAILED


def test_owner_accepted_cancellation_closes_without_worker_dispatch(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="cancelled-execution")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a", "raw:b"),
        owner_completeness_attestation="runtime-owner-test:v1",
        cancellation_request=RuntimeCancellationRequest(attempt=attempt),
    )
    worker_calls: list[str] = []
    persisted: list[RuntimeFinalizationReceipt] = []

    def persist(
        proposed: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt:
        persisted.append(proposed)
        return proposed

    receipt = execute_runtime_attempt(
        runtime,
        (_reservation(attempt),),
        worker_batch_provider=lambda _aggregate: (
            worker_calls.append("called") or RuntimeWorkerBatchReceipt(settlements=())
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=persist,
    )

    state = receipt.coordinator.state
    assert worker_calls == []
    assert persisted == [receipt]
    assert receipt.coordinator.proposed_outcome is AttemptTerminalOutcome.CANCELLED
    assert tuple(
        (settlement.work_unit_key, settlement.outcome)
        for settlement in state.runtime.settlements
    ) == (
        ("raw:a", WorkUnitTerminalOutcome.CANCELLED),
        ("raw:b", WorkUnitTerminalOutcome.CANCELLED),
    )
    assert all(
        settlement.owner_result_ref is None
        and settlement.failure_detail is not None
        and settlement.failure_detail.category is WorkUnitFailureCategory.CANCELLATION
        and settlement.failure_detail.stage == "runtime-owner-pre-dispatch"
        for settlement in state.runtime.settlements
    )
    assert tuple(
        (resource.open_outcome, resource.cleanup_outcome)
        for resource in state.resources
    ) == (
        (
            RuntimeResourceOpenOutcome.NOT_OPENED,
            RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )
    assert state.artifacts == ()
    assert "worker_started" not in tuple(
        event.kind for event in receipt.observability_ledger.events
    )


def test_owner_aggregate_derives_lifecycle_metrics_and_one_receipt(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test:v1",
    )
    result_ref = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"runtime-owner-result",
    )
    aggregate = _prepare_runtime_attempt(runtime, (_reservation(attempt),))
    aggregate = _apply_runtime_worker_batch(
        aggregate,
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=result_ref.artifact,
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="session:a",
                    open_outcome=RuntimeResourceOpenOutcome.OPENED,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt(
                    work_unit_key="raw:a",
                    artifact=result_ref.artifact,
                ),
            ),
        ),
    )

    receipt = _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert isinstance(receipt, RuntimeFinalizationReceipt)
    assert receipt.coordinator.state.runtime.all_manifest_work_settled is True
    assert receipt.supervisor.observability_ledger.metrics[0].name == ("raw_open_count")
    metrics = {
        metric.name: metric.value
        for metric in receipt.observability_ledger.metrics
        if metric.scope_key == attempt.processing_attempt_id
    }
    assert metrics["raw_open_count"] == 1
    assert metrics["logical_xic_request_count"] == 0
    assert metrics["artifact_write_count"] == 0
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    assert "worker_started" not in event_kinds
    assert event_kinds[:3] == (
        "attempt_queued",
        "manifest_closed",
        "attempt_started",
    )
    assert event_kinds[-3:] == (
        "terminal_acknowledged",
        "artifact_offered_to_owner",
        "terminal_outcome",
    )
    coordinator_event_kinds = tuple(
        event.kind.value for event in receipt.coordinator.state.events
    )
    assert (
        coordinator_event_kinds.index("resource_reserved")
        < coordinator_event_kinds.index("resource_opened")
        < coordinator_event_kinds.index("artifact_registered")
        < coordinator_event_kinds.index("resource_cleanup_succeeded")
        < coordinator_event_kinds.index("terminal_proposal")
    )
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index("resource_opened")
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("artifact_registered")
        < event_kinds.index("resource_cleanup_succeeded")
        < event_kinds.index("terminal_proposal")
    )

    coordinator_payload = json.loads(
        canonical_coordinator_terminal_record_bytes(receipt.coordinator)
    )
    observability_payload = json.loads(
        canonical_runtime_observability_ledger_bytes(receipt.observability_ledger)
    )
    assert coordinator_payload["schema_version"] == (
        "execution_runtime_coordinator_terminal_v4"
    )
    assert observability_payload["schema_version"] == (
        "execution_runtime_observability_ledger_v1"
    )
    assert {
        metric.measurement_source for metric in receipt.observability_ledger.metrics
    } == {"execution_runtime_owner:v1"}

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="RuntimeAttemptAggregate must be minted by the runtime owner",
    ):
        RuntimeAttemptAggregate(
            state=aggregate.state,
            acquisitions=aggregate.acquisitions,
            _mint_token=object(),
        )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="RuntimeFinalizationReceipt must be minted by the runtime owner",
    ):
        RuntimeFinalizationReceipt(
            coordinator=receipt.coordinator,
            supervisor=receipt.supervisor,
            _mint_token=object(),
        )
    assert not hasattr(aggregate, "_mint_token")
    assert not hasattr(receipt, "_mint_token")
    with pytest.raises(ValueError, match="InitVar '_mint_token'"):
        replace(aggregate)
    with pytest.raises(ValueError, match="InitVar '_mint_token'"):
        replace(receipt)
    with pytest.raises(TypeError, match="RuntimeAttemptAggregate is final"):
        type("ForgedAggregate", (RuntimeAttemptAggregate,), {})
    with pytest.raises(TypeError, match="RuntimeFinalizationReceipt is final"):
        type("ForgedReceipt", (RuntimeFinalizationReceipt,), {})

    foreign_attempt = _attempt(tmp_path, stem="foreign-aggregate")
    foreign_aggregate = _prepare_runtime_attempt(
        start_runtime_attempt(
            attempt=foreign_attempt,
            work_unit_keys=("raw:a",),
            owner_completeness_attestation="runtime-owner-test:v1",
        ),
        (),
    )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="exact runtime aggregate state",
    ):
        _derive_runtime_observability_ledger(
            foreign_aggregate,
            receipt.coordinator,
            telemetry=build_runtime_telemetry_samples_from_coordinator(
                receipt.coordinator
            ),
        )


def test_owner_rejects_telemetry_that_omits_derived_events(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path, stem="missing-offset")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test:v1",
    )
    result_ref = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="raw:a",
        owner_result_payload=b"runtime-owner-result",
    )
    aggregate = _apply_runtime_worker_batch(
        _prepare_runtime_attempt(runtime, (_reservation(attempt),)),
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                    owner_result_ref=result_ref.artifact,
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="session:a",
                    open_outcome=RuntimeResourceOpenOutcome.OPENED,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
            artifact_registrations=(
                RuntimeArtifactRegistrationReceipt("raw:a", result_ref.artifact),
            ),
        ),
    )

    def missing_offset(coordinator):
        samples = build_runtime_telemetry_samples_from_coordinator(coordinator)
        return type(samples)(
            event_monotonic_offsets_ns=(samples.event_monotonic_offsets_ns[:-1]),
            artifact_io=samples.artifact_io,
            process_tree_peak_rss=samples.process_tree_peak_rss,
            worker_peak_rss=samples.worker_peak_rss,
        )

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="owner-derived event count",
    ):
        _finalize_runtime_attempt(
            aggregate,
            telemetry_provider=missing_offset,
            persister=lambda proposed: proposed,
        )


@pytest.mark.parametrize(
    ("work_unit_keys", "owner_completeness_attestation"),
    (
        ((), "runtime-owner-test:v1"),
        (("raw:a",), ""),
        (("raw:a",), None),
    ),
)
def test_owner_preflight_failure_uses_finalization_and_recovery_path(
    tmp_path: Path,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
) -> None:
    attempt = _attempt(tmp_path, stem="preflight-failure")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "preflight.runtime.json",
        "runtime-attempt-artifacts:preflight",
    )
    worker_calls: list[str] = []

    assert runtime.manifest_preflight_failed is True
    assert tuple(event.kind for event in runtime.history.events) == (
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
        AttemptLifecycleEventKind.MANIFEST_PREFLIGHT_FAILED,
        AttemptLifecycleEventKind.TERMINAL_PROPOSAL,
    )
    marker.record_launch(runtime)
    receipt = execute_runtime_attempt(
        runtime,
        (_reservation(attempt),),
        worker_batch_provider=lambda _aggregate: (
            worker_calls.append("called") or RuntimeWorkerBatchReceipt(settlements=())
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=marker,
    )

    assert worker_calls == []
    assert receipt.coordinator.state.resources == ()
    assert receipt.coordinator.proposed_outcome is AttemptTerminalOutcome.FAILED
    assert tuple(event.kind for event in receipt.observability_ledger.events) == (
        "attempt_queued",
        "manifest_preflight_failed",
        "terminal_proposal",
        "terminal_acknowledged",
        "terminal_outcome",
    )
    metrics = {metric.name: metric for metric in receipt.observability_ledger.metrics}
    for name in (
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
    ):
        assert metrics[name].value == 0
    for name in (
        "coordinator_work_seconds",
        "stage_or_unit_elapsed_seconds",
        "worker_peak_rss_bytes",
        "process_tree_peak_rss_bytes",
    ):
        assert metrics[name].availability is RuntimeMetricAvailability.UNSUPPORTED
        assert metrics[name].reason == "not_started_preflight_failure"
    recovered = marker.recover_after_coordinator_loss(attempt)
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED


@pytest.mark.parametrize(
    "open_outcome",
    (
        RuntimeResourceOpenOutcome.NOT_OPENED,
        RuntimeResourceOpenOutcome.OPEN_FAILED,
        RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED,
    ),
)
def test_unopened_process_resource_does_not_invent_worker_topology(
    tmp_path: Path,
    open_outcome: RuntimeResourceOpenOutcome,
) -> None:
    attempt = _attempt(tmp_path, stem=f"unopened-worker-{open_outcome.value}")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test:v1",
    )
    reservation = RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key="process:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test:v1",
    )
    aggregate = _apply_runtime_worker_batch(
        _prepare_runtime_attempt(runtime, (reservation,)),
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.FAILED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.EXECUTION_ERROR,
                        stage="runtime-owner-test",
                        source_reference="raw:a",
                        message="worker did not open",
                    ),
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="process:a",
                    open_outcome=open_outcome,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
        ),
    )

    receipt = _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert "worker_started" not in {
        event.kind for event in receipt.observability_ledger.events
    }
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    open_event_kind = {
        RuntimeResourceOpenOutcome.NOT_OPENED: "resource_not_opened",
        RuntimeResourceOpenOutcome.OPEN_FAILED: "resource_open_failed",
        RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED: (
            "resource_condition_not_activated"
        ),
    }[open_outcome]
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index(open_event_kind)
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("resource_cleanup_succeeded")
        < event_kinds.index("terminal_proposal")
    )
    worker_metric = next(
        metric
        for metric in receipt.observability_ledger.metrics
        if metric.name == "worker_peak_rss_bytes"
    )
    assert worker_metric.scope_key == attempt.processing_attempt_id
    assert worker_metric.availability is RuntimeMetricAvailability.UNSUPPORTED
    assert worker_metric.reason == "not_applicable_serial"


def test_unknown_open_state_after_worker_loss_retains_worker_topology(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path, stem="unknown-open-worker-loss")
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test:v1",
    )
    reservation = RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key="process:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test:v1",
    )
    receipt = execute_runtime_attempt(
        runtime,
        (reservation,),
        worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.FAILED,
                    failure_detail=WorkUnitFailureDetail(
                        category=WorkUnitFailureCategory.WORKER_LOSS,
                        stage="runtime-owner-test",
                        source_reference="raw:a",
                        message="worker vanished after dispatch",
                    ),
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="process:a",
                    open_outcome=(
                        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                    ),
                    cleanup_outcome=(
                        RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                    ),
                ),
            ),
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )

    assert "worker_started" in {
        event.kind for event in receipt.observability_ledger.events
    }
    event_kinds = tuple(event.kind for event in receipt.observability_ledger.events)
    assert (
        event_kinds.index("resource_reserved")
        < event_kinds.index("resource_open_state_unknown_after_worker_loss")
        < event_kinds.index("worker_started")
        < event_kinds.index("work_unit_submitted")
        < event_kinds.index("work_unit_started")
        < event_kinds.index("work_unit_settled")
        < event_kinds.index("resource_cleanup_unknown_after_worker_loss")
        < event_kinds.index("terminal_proposal")
    )
    worker_metric = next(
        metric
        for metric in receipt.observability_ledger.metrics
        if metric.name == "worker_peak_rss_bytes" and metric.scope_key == "worker:a"
    )
    assert worker_metric.availability is RuntimeMetricAvailability.MEASURED
    assert worker_metric.value == 0


def _reservation(
    attempt: ProcessingAttemptIdentity,
) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.ACQUISITION_SESSION,
        resource_key="session:a",
        worker_key="worker:a",
        source_reference="no-raw:sample:a",
        compatibility_token="runtime-owner-test:v1",
    )


def _attempt(
    tmp_path: Path,
    *,
    stem: str = "sample",
) -> ProcessingAttemptIdentity:
    raw_path = tmp_path / f"{stem}.raw"
    raw_path.write_bytes(f"synthetic-no-raw:{stem}".encode())
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key=raw_path.name,
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="runtime_owner_test_v1",
        processing_algorithm_version="runtime_owner_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
