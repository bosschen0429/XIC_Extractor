from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.acquisition_contract import (
    _mint_raw_acquisition_session_receipt,
    mint_raw_acquisition_session_capability,
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
    RuntimeArtifactIoSample,
    RuntimeArtifactRegistrationReceipt,
    RuntimeAttemptAggregate,
    RuntimeCancellationRequest,
    RuntimeFinalizationReceipt,
    RuntimePeakFactCostMetrics,
    RuntimePeakFactIdentityStatus,
    RuntimePeakFactOperation,
    RuntimePeakFactWorkReceipt,
    RuntimeRawOpenMetrics,
    RuntimeResourceSettlementReceipt,
    RuntimeWorkerBatchReceipt,
    _apply_runtime_worker_batch,
    _derive_runtime_observability_ledger,
    _finalize_runtime_attempt,
    _prepare_runtime_attempt,
    derive_runtime_peak_fact_cost_metrics,
    derive_runtime_raw_open_metrics,
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


def test_runtime_owner_derives_raw_open_only_from_matching_unique_receipt(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"raw-open-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    receipt = _mint_raw_acquisition_session_receipt(capability)

    measured = derive_runtime_raw_open_metrics(capability, (receipt,))
    unavailable = derive_runtime_raw_open_metrics(capability, ())

    assert measured.raw_open_count == 1
    assert measured.unavailable_reason == ""
    assert unavailable.raw_open_count is None
    assert unavailable.unavailable_reason
    with pytest.raises(RuntimeFinalizationContractError, match="duplicate"):
        derive_runtime_raw_open_metrics(capability, (receipt, receipt))

    foreign = mint_raw_acquisition_session_capability(source)
    with pytest.raises(RuntimeFinalizationContractError, match="capability"):
        derive_runtime_raw_open_metrics(foreign, (receipt,))

    with pytest.raises(
        RuntimeFinalizationContractError,
        match="minted by the runtime owner",
    ):
        RuntimeRawOpenMetrics(
            raw_open_count=999,
            unavailable_reason="",
            _mint_token=object(),
        )


def test_runtime_owner_derives_peak_fact_cost_from_typed_work_and_telemetry(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"peak-fact-runtime-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    raw_receipt = _mint_raw_acquisition_session_receipt(capability)
    work = (
        RuntimePeakFactWorkReceipt(
            work_unit_key="sealed:1",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                RuntimePeakFactOperation.INTEGRATION,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:1",
            group_hypothesis_id="GROUP:1",
            acquisition_source=source,
            elapsed_seconds=0.25,
        ),
        RuntimePeakFactWorkReceipt(
            work_unit_key="unsealed:2",
            sample_stem="sample-a",
            operations=(RuntimePeakFactOperation.TRACE_EVALUATION,),
            identity_status=RuntimePeakFactIdentityStatus.UNAVAILABLE,
        ),
    )

    metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=capability,
        raw_open_receipts=(raw_receipt,),
        work_receipts=work,
        artifact_io=RuntimeArtifactIoSample(
            read_count=0,
            read_bytes=0,
            write_count=0,
            write_bytes=0,
        ),
    )

    assert metrics.raw_open_count == 1
    assert metrics.trace_evaluation_count == 2
    assert metrics.smoothing_count == 1
    assert metrics.integration_count == 1
    assert metrics.artifact_round_trip_count == 0
    assert metrics.sealed_observation_count == 1
    assert metrics.identity_unavailable_count == 1
    assert metrics.elapsed_seconds == pytest.approx(0.25)
    assert metrics.measurement_source == "execution_runtime_owner:v1"
    assert metrics.count_scope == "owner_backfill_worker_peak_fact_runtime"

    with pytest.raises(RuntimeFinalizationContractError, match="duplicate"):
        derive_runtime_peak_fact_cost_metrics(
            raw_session_capability=capability,
            raw_open_receipts=(raw_receipt,),
            work_receipts=(work[0], work[0]),
            artifact_io=None,
        )
    foreign_path = tmp_path / "foreign.raw"
    foreign_path.write_bytes(b"foreign-peak-fact-runtime-owner")
    foreign_source = build_acquisition_source_identity(
        foreign_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    with pytest.raises(RuntimeFinalizationContractError, match="RAW session"):
        derive_runtime_peak_fact_cost_metrics(
            raw_session_capability=capability,
            raw_open_receipts=(raw_receipt,),
            work_receipts=(
                replace(
                    work[0],
                    work_unit_key="sealed:foreign",
                    acquisition_source=foreign_source,
                ),
            ),
            artifact_io=None,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="trace evaluation"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="unsealed-without-work",
            sample_stem="sample-a",
            operations=(),
            identity_status=RuntimePeakFactIdentityStatus.UNAVAILABLE,
        )
    with pytest.raises(RuntimeFinalizationContractError, match="finite and positive"):
        RuntimePeakFactWorkReceipt(
            work_unit_key="non-finite",
            sample_stem="sample-a",
            operations=(
                RuntimePeakFactOperation.TRACE_EVALUATION,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
            ),
            identity_status=RuntimePeakFactIdentityStatus.SEALED,
            owner_request_identity="owner-request:non-finite",
            group_hypothesis_id="GROUP:non-finite",
            acquisition_source=source,
            elapsed_seconds=float("nan"),
        )
    with pytest.raises(
        RuntimeFinalizationContractError,
        match="minted by the runtime owner",
    ):
        RuntimePeakFactCostMetrics(
            raw_open_count=1,
            raw_open_unavailable_reason="",
            trace_evaluation_count=2,
            smoothing_count=1,
            integration_count=1,
            artifact_round_trip_count=0,
            artifact_round_trip_unavailable_reason="",
            sealed_observation_count=1,
            identity_unavailable_count=1,
            elapsed_seconds=0.25,
            elapsed_unavailable_reason="",
            _mint_token=object(),
        )


def test_runtime_owner_keeps_missing_peak_fact_timing_and_artifacts_unavailable(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "missing.raw"
    raw_path.write_bytes(b"missing-peak-fact-runtime-owner")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo_raw_file_reader:Device.MS:1",
    )
    capability = mint_raw_acquisition_session_capability(source)
    metrics = derive_runtime_peak_fact_cost_metrics(
        raw_session_capability=capability,
        raw_open_receipts=(),
        work_receipts=(
            RuntimePeakFactWorkReceipt(
                work_unit_key="sealed:missing-time",
                sample_stem="sample-a",
                operations=(
                    RuntimePeakFactOperation.TRACE_EVALUATION,
                    RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                    RuntimePeakFactOperation.INTEGRATION,
                ),
                identity_status=RuntimePeakFactIdentityStatus.SEALED,
                owner_request_identity="owner-request:missing-time",
                group_hypothesis_id="GROUP:missing-time",
                acquisition_source=source,
            ),
        ),
        artifact_io=None,
    )

    assert metrics.raw_open_count is None
    assert metrics.raw_open_unavailable_reason
    assert metrics.artifact_round_trip_count is None
    assert metrics.artifact_round_trip_unavailable_reason
    assert metrics.elapsed_seconds is None
    assert metrics.elapsed_unavailable_reason


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
    with pytest.raises((TypeError, ValueError), match="InitVar '_mint_token'"):
        replace(aggregate)
    with pytest.raises((TypeError, ValueError), match="InitVar '_mint_token'"):
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
