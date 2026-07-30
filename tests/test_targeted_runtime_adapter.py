from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path

import pytest

from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlementIntegrityFailureKind,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import start_runtime_attempt
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessResultRef,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeApplicationResult,
    TargetedRuntimeWorkerBatch,
    canonical_targeted_process_result_identity_bytes,
    execute_targeted_runtime_lifecycle,
    mint_targeted_process_result_ref,
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


def test_targeted_application_service_has_no_settle_only_runtime_bypass(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("contract inspection must not call the legacy runner")

    service = TargetedApplicationService(legacy_runner)

    assert not hasattr(service, "execute_runtime")
    assert build_targeted_application_request(_config(), [_target()]).targets


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

    def lifecycle_runner(_request, aggregate):
        state = aggregate.state
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

    def finalization_persister(proposed):
        finalized.append(proposed)
        return proposed

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_finalization_persister=finalization_persister,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        runtime,
        resource_reservations=(_reservation(attempt),),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
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
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    )

    recovered = marker.recover_after_coordinator_loss(attempt)
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is result.finalization_record.terminal_outcome
    assert recovered.artifact_dispositions == (
        result.finalization_record.artifact_dispositions
    )


def test_targeted_lifecycle_rejects_independent_dual_finalizers_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    marker_path = tmp_path / "targeted-dual-finalizer.runtime.json"
    marker = OwnerPathRuntimeMarker(
        marker_path,
        "targeted-runtime-artifacts:dual-finalizer",
    )
    resolution_calls: list[str] = []
    runner_calls: list[str] = []
    persister_calls: list[str] = []

    def unexpected_resolution(_request):
        resolution_calls.append("called")
        raise AssertionError("dual finalizers must fail before request resolution")

    def lifecycle_runner(_request, _aggregate):
        runner_calls.append("called")
        raise AssertionError("dual finalizers must fail before work dispatch")

    def finalization_persister(proposed):
        persister_calls.append("called")
        return proposed

    monkeypatch.setattr(
        "xic_extractor.extraction.targeted_service._resolve_application_request",
        unexpected_resolution,
    )
    service = TargetedApplicationService(
        lambda *_args, **_kwargs: None,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_finalization_persister=finalization_persister,
        runtime_recovery_marker=marker,
    )

    with pytest.raises(TargetedRuntimeAdapterError, match="mutually exclusive"):
        service.execute_runtime_lifecycle(
            build_targeted_application_request(_config(), [_target()]),
            _runtime(attempt, ("raw:a",)),
            resource_reservations=(_reservation(attempt),),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        )

    assert resolution_calls == []
    assert runner_calls == []
    assert persister_calls == []
    assert not marker_path.exists()


def test_targeted_preflight_failure_skips_runner_and_finalizes_marker(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "targeted-preflight.runtime.json",
        "targeted-runtime-artifacts:preflight",
    )
    runner_calls: list[str] = []

    def lifecycle_runner(_request, _aggregate):
        runner_calls.append("called")
        raise AssertionError("preflight failure must not dispatch Targeted work")

    def legacy_runner(*_args, **_kwargs):
        raise AssertionError("preflight lifecycle must not call the legacy runner")

    service = TargetedApplicationService(
        legacy_runner,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_recovery_marker=marker,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        start_runtime_attempt(
            attempt=attempt,
            work_unit_keys=(),
            owner_completeness_attestation="targeted-runtime-adapter:v1",
        ),
        resource_reservations=(_reservation(attempt),),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    )

    recovered = marker.recover_after_coordinator_loss(attempt)
    assert runner_calls == []
    assert result.finalization_record.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert result.settlement.runtime.manifest_preflight_failed is True
    assert result.coordinator_record.state.resources == ()
    assert recovered.finalization_acknowledged is True
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED


def test_targeted_lifecycle_fails_closed_without_exact_supervisor_ack(
    tmp_path: Path,
) -> None:
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
        runtime_finalization_persister=lambda _proposed: object(),
    )

    with pytest.raises(RuntimeFinalizationContractError, match="conflicts"):
        service.execute_runtime_lifecycle(
            build_targeted_application_request(_config(), [_target()]),
            _runtime(attempt, ("raw:a",)),
            resource_reservations=(_reservation(attempt),),
            telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        )


def test_targeted_spawned_worker_loss_quarantines_partial_artifact(
    tmp_path: Path,
) -> None:
    from xic_extractor.extraction.targeted_service import (
        TargetedApplicationService,
        build_targeted_application_request,
    )

    attempt = _attempt(tmp_path)

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
        runtime_finalization_persister=lambda proposed: proposed,
    )
    result = service.execute_runtime_lifecycle(
        build_targeted_application_request(_config(), [_target()]),
        _runtime(attempt, ("raw:a",)),
        resource_reservations=(_reservation(attempt),),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
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

    result = _execute_targeted_envelopes(
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

    failed_result = _execute_targeted_envelopes(
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
        _execute_targeted_envelopes(
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
        _execute_targeted_envelopes(
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


def _execute_targeted_envelopes(
    runtime: AttemptRuntime,
    envelopes: tuple[TargetedProcessWorkerResultEnvelope, ...],
) -> TargetedRuntimeApplicationResult:
    references = {
        envelope.owner_result_ref.artifact.evidence_artifact_id: (
            envelope.owner_result_ref
        )
        for envelope in envelopes
        if envelope.owner_result_ref is not None
    }
    lifecycle = execute_targeted_runtime_lifecycle(
        runtime,
        (),
        worker_batch_provider=lambda _aggregate: TargetedRuntimeWorkerBatch(
            result_envelopes=envelopes,
            resource_reports=(),
            runtime_artifact_refs=tuple(references.values()),
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        finalization_persister=lambda proposed: proposed,
    )
    return lifecycle.settlement


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
) -> AttemptRuntime:
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation="targeted-runtime-adapter:v1",
    )


def _reservation(attempt: ProcessingAttemptIdentity) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=attempt,
        kind=RuntimeResourceKind.ACQUISITION_SESSION,
        resource_key="session:a",
        worker_key="worker:1",
        source_reference="source:a",
        compatibility_token="targeted-runtime-test:v1",
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
