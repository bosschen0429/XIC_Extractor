from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path

import pytest

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    WorkUnitSettlementIntegrityFailureKind,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResultRef,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
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
        failure_message="worker failed",
    )

    failed_result = settle_targeted_process_worker_results(
        failed_runtime,
        (failed,),
    )

    assert failed_result.runtime.all_manifest_work_settled is True
    assert failed_result.runtime.must_fail is True
    assert failed_result.successful_result_refs == ()

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
