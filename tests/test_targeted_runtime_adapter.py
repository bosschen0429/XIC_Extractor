from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path

import pytest

from tests.runtime_observability_fixtures import (
    build_runtime_observability_ledger,
)
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlementIntegrityFailureKind,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessResultRef,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeWorkerBatch,
    canonical_targeted_process_result_identity_bytes,
    mint_targeted_process_result_ref,
    settle_targeted_process_worker_results,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    ProcessingRequestIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def _build_spawned_envelope(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    payload: bytes,
) -> TargetedProcessWorkerResultEnvelope:
    return _success(
        attempt,
        work_unit_key,
        _result_ref(attempt, work_unit_key, payload),
    )


def _build_spawned_lifecycle_batch(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    payload: bytes,
) -> TargetedRuntimeWorkerBatch:
    reference = _result_ref(attempt, work_unit_key, payload)
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(_success(attempt, work_unit_key, reference),),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=attempt,
                resource_key="session:a",
                open_outcome=RuntimeResourceOpenOutcome.OPENED,
                cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            ),
        ),
        runtime_artifact_refs=(reference,),
    )


def _build_spawned_worker_loss_batch(
    attempt: ProcessingAttemptIdentity,
) -> TargetedRuntimeWorkerBatch:
    partial = _result_ref(attempt, "raw:a", b"partial-before-worker-loss")
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(
            TargetedProcessWorkerResultEnvelope(
                attempt=attempt,
                work_unit_key="raw:a",
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.WORKER_LOSS,
                    stage="targeted-process-worker",
                    source_reference="no-raw:raw:a",
                    message="worker lost",
                ),
            ),
        ),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=attempt,
                resource_key="session:a",
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                ),
                cleanup_outcome=(
                    RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                ),
            ),
        ),
        runtime_artifact_refs=(partial,),
    )


def test_targeted_application_service_settles_reordered_process_results(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt, ("raw:a", "raw:b"))
    runner_calls: list[tuple[object, AttemptRuntime]] = []

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("runtime consumer must not call the legacy runner")

    def runtime_runner(request, actual_runtime):
        runner_calls.append((request, actual_runtime))
        with ProcessPoolExecutor(
            max_workers=2,
            mp_context=get_context("spawn"),
        ) as executor:
            futures = (
                executor.submit(
                    _build_spawned_envelope,
                    attempt,
                    "raw:b",
                    b"owner-b",
                ),
                executor.submit(
                    _build_spawned_envelope,
                    attempt,
                    "raw:a",
                    b"owner-a",
                ),
            )
            return tuple(future.result(timeout=15) for future in futures)

    service = TargetedApplicationService(
        legacy_runner,
        runtime_runner=runtime_runner,
    )
    result = service.execute_runtime(
        build_targeted_application_request(_config(), [_target()]),
        runtime,
    )

    assert len(runner_calls) == 1
    assert runner_calls[0][1] is runtime
    assert result.runtime.all_manifest_work_settled is True
    assert result.runtime.must_fail is False
    assert result.successful_result_refs == (
        ("raw:a", _result_ref(attempt, "raw:a", b"owner-a")),
        ("raw:b", _result_ref(attempt, "raw:b", b"owner-b")),
    )


def test_targeted_application_service_closes_true_process_lifecycle(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt, ("raw:a",))
    observed_states = []
    finalized = []

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("runtime lifecycle must not call the legacy runner")

    def lifecycle_runner(_request, state):
        observed_states.append(state)
        assert len(state.resources) == 1
        assert state.resources[0].open_outcome is None
        assert state.events[-1].kind is RuntimeLifecycleEventKind.RESOURCE_RESERVED
        with ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        ) as executor:
            return executor.submit(
                _build_spawned_lifecycle_batch,
                attempt,
                "raw:a",
                b"spawned-lifecycle-result",
            ).result(timeout=15)

    def supervisor_finalizer(coordinator, proposed):
        finalized.append((coordinator, proposed))
        return proposed

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_supervisor_finalizer=supervisor_finalizer,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        runtime,
        resource_reservations=(_reservation(attempt),),
        observability_ledger=_ledger(
            attempt,
            _build_spawned_lifecycle_batch(
                attempt,
                "raw:a",
                b"spawned-lifecycle-result",
            ),
        ),
    )

    assert len(observed_states) == 1
    assert len(finalized) == 1
    assert result.settlement.runtime.all_manifest_work_settled is True
    assert result.finalization_record.artifact_dispositions == (
        (
            _result_ref(
                attempt,
                "raw:a",
                b"spawned-lifecycle-result",
            ).artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.OFFERED_TO_OWNER,
        ),
    )


def test_targeted_lifecycle_uses_owner_path_marker_as_durable_finalizer(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "targeted.runtime.json",
        "targeted-runtime-artifacts:test",
    )
    batch = _build_spawned_lifecycle_batch(
        attempt,
        "raw:a",
        b"durable-targeted-result",
    )

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("runtime lifecycle must not call the legacy runner")

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lambda _request, _state: batch,
        runtime_recovery_marker=marker,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        _runtime(attempt, ("raw:a",)),
        resource_reservations=(_reservation(attempt),),
        observability_ledger=_ledger(attempt, batch),
    )

    recovered = marker.recover_after_coordinator_loss(attempt)
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is result.finalization_record.terminal_outcome
    assert recovered.artifact_dispositions == (
        result.finalization_record.artifact_dispositions
    )


def test_targeted_lifecycle_fails_closed_without_exact_supervisor_ack(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    reference = _result_ref(attempt, "raw:a", b"result")
    batch = TargetedRuntimeWorkerBatch(
        result_envelopes=(_success(attempt, "raw:a", reference),),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=attempt,
                resource_key="session:a",
                open_outcome=RuntimeResourceOpenOutcome.OPENED,
                cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            ),
        ),
        runtime_artifact_refs=(reference,),
    )

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("runtime lifecycle must not call the legacy runner")

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lambda _request, _state: batch,
        runtime_supervisor_finalizer=lambda _coordinator, proposed: replace(
            proposed,
            coordinator_record_sha256="0" * 64,
        ),
    )

    with pytest.raises(RuntimeFinalizationContractError, match="conflicts"):
        service.execute_runtime_lifecycle(
            build_targeted_application_request(_config(), [_target()]),
            _runtime(attempt, ("raw:a",)),
            resource_reservations=(_reservation(attempt),),
            observability_ledger=_ledger(attempt, batch),
        )


def test_targeted_spawned_worker_loss_quarantines_partial_artifact(
    tmp_path: Path,
) -> None:
    from xic_extractor.execution_runtime import AttemptTerminalOutcome
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    expected_batch = _build_spawned_worker_loss_batch(attempt)

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("runtime lifecycle must not call the legacy runner")

    def lifecycle_runner(_request, _state):
        with ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        ) as executor:
            return executor.submit(
                _build_spawned_worker_loss_batch,
                attempt,
            ).result(timeout=15)

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_supervisor_finalizer=lambda _coordinator, proposed: proposed,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        _runtime(attempt, ("raw:a",)),
        resource_reservations=(_reservation(attempt),),
        observability_ledger=_ledger(attempt, expected_batch),
    )

    assert result.finalization_record.terminal_outcome is AttemptTerminalOutcome.FAILED
    failure_detail = result.settlement.runtime.settlements[0].failure_detail
    assert failure_detail is not None
    assert failure_detail.category is WorkUnitFailureCategory.WORKER_LOSS
    assert failure_detail.message == "worker lost"
    assert result.finalization_record.artifact_dispositions == (
        (
            _result_ref(
                attempt,
                "raw:a",
                b"partial-before-worker-loss",
            ).artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,
        ),
    )


def test_targeted_result_envelope_and_ref_cross_true_spawn(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)

    with ProcessPoolExecutor(
        max_workers=1,
        mp_context=get_context("spawn"),
    ) as executor:
        observed = executor.submit(
            _build_spawned_envelope,
            attempt,
            "raw:a",
            b"spawn-owner-result",
        ).result(timeout=15)

    assert observed == _success(
        attempt,
        "raw:a",
        _result_ref(attempt, "raw:a", b"spawn-owner-result"),
    )


def test_targeted_runtime_adapter_keeps_first_ref_and_records_conflict(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt, ("raw:a",))
    first = _success(attempt, "raw:a", _result_ref(attempt, "raw:a", b"first"))
    different = _success(
        attempt,
        "raw:a",
        _result_ref(attempt, "raw:a", b"different"),
    )

    result = settle_targeted_process_worker_results(
        runtime,
        (first, first, different),
    )

    assert len(result.runtime.settlements) == 1
    assert result.runtime.integrity_failure is not None
    assert (
        result.runtime.integrity_failure.kind
        is WorkUnitSettlementIntegrityFailureKind.CONFLICTING_SETTLEMENT
    )
    assert result.runtime.must_fail is True
    assert result.successful_result_refs == (("raw:a", first.owner_result_ref),)


def test_targeted_runtime_adapter_closes_failed_work_and_rejects_missing_work(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    failed_runtime = _runtime(attempt, ("raw:a",))
    failed = TargetedProcessWorkerResultEnvelope(
        attempt=attempt,
        work_unit_key="raw:a",
        outcome=WorkUnitTerminalOutcome.FAILED,
        failure_detail=WorkUnitFailureDetail(
            category=WorkUnitFailureCategory.EXECUTION_ERROR,
            stage="targeted-process-worker",
            source_reference="no-raw:raw:a",
            message="worker failed",
        ),
    )

    failed_result = settle_targeted_process_worker_results(
        failed_runtime,
        (failed,),
    )

    assert failed_result.runtime.all_manifest_work_settled is True
    assert failed_result.runtime.must_fail is True
    assert failed_result.successful_result_refs == ()
    failure_detail = failed_result.runtime.settlements[0].failure_detail
    assert failure_detail is not None
    assert failure_detail.message == "worker failed"

    with pytest.raises(TargetedRuntimeAdapterError, match="did not settle"):
        settle_targeted_process_worker_results(
            _runtime(attempt, ("raw:a", "raw:b")),
            (_success(attempt, "raw:a", _result_ref(attempt, "raw:a", b"a")),),
        )


def test_targeted_result_ref_and_runtime_attempt_substitution_fail_closed(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    other_attempt = issue_processing_attempt(_request(tmp_path))
    wrong_ref = _result_ref(other_attempt, "raw:a", b"wrong-attempt")

    with pytest.raises(TargetedRuntimeAdapterError, match="request and attempt"):
        _success(attempt, "raw:a", wrong_ref)

    with pytest.raises(TargetedRuntimeAdapterError, match="work_unit_key"):
        _success(
            attempt,
            "raw:a",
            _result_ref(attempt, "raw:b", b"wrong-work-key"),
        )

    other_envelope = _success(
        other_attempt,
        "raw:a",
        _result_ref(other_attempt, "raw:a", b"other"),
    )
    with pytest.raises(TargetedRuntimeAdapterError, match="different runtime"):
        settle_targeted_process_worker_results(
            _runtime(attempt, ("raw:a",)),
            (other_envelope,),
        )


def test_targeted_result_ref_binds_exact_owner_payload_without_legacy_dto(
    tmp_path: Path,
) -> None:
    from xic_extractor.evidence_artifacts import verify_evidence_artifact_payload

    attempt = _attempt(tmp_path)
    payload = b"canonical-owner-result"
    reference = _result_ref(attempt, "raw:a", payload)
    identity_bytes = canonical_targeted_process_result_identity_bytes(
        work_unit_key="raw:a",
        owner_result_payload=payload,
    )

    verify_evidence_artifact_payload(reference.artifact, identity_bytes)
    assert reference != _result_ref(attempt, "raw:a", b"different")
    assert b"canonical-owner-result" not in identity_bytes
    with pytest.raises(TargetedRuntimeAdapterError, match="does not bind"):
        replace(
            reference,
            owner_result_byte_size=reference.owner_result_byte_size + 1,
        )


def _success(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    result_ref: TargetedProcessResultRef,
) -> TargetedProcessWorkerResultEnvelope:
    return TargetedProcessWorkerResultEnvelope(
        attempt=attempt,
        work_unit_key=work_unit_key,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=result_ref,
    )


def _result_ref(
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    payload: bytes,
) -> TargetedProcessResultRef:
    return mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key=work_unit_key,
        owner_result_payload=payload,
    )


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
) -> AttemptRuntime:
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation="targeted-runtime-adapter:v1",
        )
    )


def _reservation(attempt: ProcessingAttemptIdentity) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        resource_key="session:a",
        worker_key="worker:1",
        source_reference="source:a",
        compatibility_token="targeted-runtime-test:v1",
    )


def _ledger(
    attempt: ProcessingAttemptIdentity,
    batch: TargetedRuntimeWorkerBatch,
):
    failed = any(
        envelope.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
        for envelope in batch.result_envelopes
    )
    artifact_ids = tuple(
        dict.fromkeys(
            (
                *(
                    reference.artifact.evidence_artifact_id
                    for reference in batch.runtime_artifact_refs
                ),
                *(
                    artifact.evidence_artifact_id
                    for envelope in batch.result_envelopes
                    if envelope.peak_observation_batch is not None
                    for artifact in (
                        envelope.peak_observation_batch.observation_artifacts
                    )
                ),
            )
        )
    )
    return build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("raw:a",),
        worker_keys=("worker:1",),
        resource_lifecycles=tuple(
            (
                report.resource_key,
                f"resource_{report.open_outcome.value}",
                f"resource_cleanup_{report.cleanup_outcome.value}",
            )
            for report in batch.resource_reports
        ),
        artifact_dispositions=tuple(
            (
                artifact_id,
                (
                    "artifact_quarantined_diagnostic"
                    if failed
                    else "artifact_offered_to_owner"
                ),
            )
            for artifact_id in artifact_ids
        ),
    )


def _attempt(tmp_path: Path) -> ProcessingAttemptIdentity:
    return issue_processing_attempt(_request(tmp_path))


def _request(tmp_path: Path) -> ProcessingRequestIdentity:
    raw_path = tmp_path / "sample.raw"
    raw_path.write_bytes(b"synthetic-no-raw-content")
    source = build_acquisition_source_identity(
        raw_path,
        acquisition_format="thermo_raw",
        observation_namespace="thermo.raw.ms_controller_1",
    )
    return build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample.raw",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="targeted_runtime_adapter_test_v1",
        processing_algorithm_version="targeted_runtime_adapter_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )


def _config() -> ExtractionConfig:
    return ExtractionConfig(
        data_dir=Path("data"),
        dll_dir=Path("dll"),
        output_csv=Path("output/xic_results.csv"),
        diagnostics_csv=Path("output/xic_diagnostics.csv"),
        smooth_window=9,
        smooth_polyorder=2,
        peak_rel_height=0.02,
        peak_min_prominence_ratio=0.01,
        ms2_precursor_tol_da=0.5,
        nl_min_intensity_ratio=0.01,
    )


def _target() -> Target:
    return Target(
        label="Analyte",
        mz=250.0,
        rt_min=1.0,
        rt_max=2.0,
        ppm_tol=20.0,
        neutral_loss_da=116.0,
        nl_ppm_warn=20.0,
        nl_ppm_max=40.0,
        is_istd=False,
        istd_pair="",
    )
