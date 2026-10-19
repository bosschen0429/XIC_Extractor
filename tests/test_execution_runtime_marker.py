from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import xic_extractor.execution.recovery as runtime_marker_module
from tests.runtime_observability_fixtures import (
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
    settle_work_unit,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.runtime_telemetry_fixtures import (
    mint_test_process_result_ref as mint_targeted_process_result_ref,
)
from xic_extractor.execution._finalization import (
    CoordinatorTerminalRecord,
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    begin_attempt_finalization,
    canonical_coordinator_terminal_record_bytes,
    mint_supervisor_finalization,
    propose_attempt_terminal,
)
from xic_extractor.execution._lifecycle import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.execution._observability import (
    canonical_runtime_observability_ledger_bytes,
)
from xic_extractor.execution.attempt import (
    _derive_runtime_observability_ledger,
    _mint_runtime_attempt_aggregate,
    _mint_runtime_finalization_receipt,
)
from xic_extractor.execution.recovery import (
    OwnerPathRuntimeMarker,
    RuntimeMarkerState,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingAttemptIdentity,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_launch_marker_makes_pre_ack_coordinator_loss_fail_closed(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )

    launch = marker.record_launch(runtime)
    recovered = marker.recover_after_coordinator_loss(attempt)

    assert launch.state is RuntimeMarkerState.LAUNCHED
    assert marker.record_launch(runtime) == launch
    assert recovered.terminal_outcome is AttemptTerminalOutcome.FAILED
    assert recovered.finalization_acknowledged is False
    assert recovered.quarantine_artifact_namespace is True
    assert recovered.artifact_namespace == "runtime-attempt-artifacts:a"
    assert recovered.artifact_dispositions == ()


def test_finalization_atomically_replaces_launch_and_survives_loss(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    reference = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="sample:a",
        owner_result_payload=b"no-raw-result",
    )
    runtime = settle_work_unit(
        runtime,
        work_unit_key="sample:a",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=reference.artifact,
    )
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        RuntimeResourceReservation(
            attempt=attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key="process:sample:a",
            worker_key="worker:1",
            source_reference="no-raw:sample:a",
            compatibility_token="alignment:no-raw",
        ),
    )
    state = settle_runtime_resource(
        state,
        resource_key="process:sample:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    state = register_runtime_artifact(
        state,
        work_unit_key="sample:a",
        artifact=reference.artifact,
    )
    coordinator = propose_attempt_terminal(state)
    ledger = _ledger(coordinator)
    proposed = mint_supervisor_finalization(
        coordinator,
        observability_ledger=ledger,
    )
    receipt = _mint_runtime_finalization_receipt(
        coordinator=coordinator,
        supervisor=proposed,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))

    accepted = marker(receipt)
    recovered = marker.recover_after_coordinator_loss(attempt)

    assert accepted == receipt
    payload = json.loads(marker.path.read_bytes())
    assert payload["schema_version"] == "execution_runtime_recovery_marker"
    assert marker.read().state is RuntimeMarkerState.FINALIZED
    assert marker(receipt) == accepted
    with pytest.raises(RuntimeFinalizationContractError, match="already durably"):
        marker.record_launch(_runtime(attempt))
    assert recovered.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert recovered.finalization_acknowledged is True
    assert recovered.quarantine_artifact_namespace is False
    assert recovered.coordinator_record_bytes == (
        canonical_coordinator_terminal_record_bytes(coordinator)
    )
    assert recovered.observability_ledger_bytes == (
        canonical_runtime_observability_ledger_bytes(ledger)
    )
    assert recovered.artifact_dispositions == (
        (
            reference.artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.OFFERED_TO_OWNER,
        ),
    )


def test_failed_attempt_retry_preserves_receipt_and_uses_new_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
        allow_failed_attempt_retry=True,
    )
    failed = _attempt(tmp_path, stem="first")
    retry = _attempt(tmp_path, stem="second")
    marker.record_launch(_runtime(failed))
    marker(_terminal_records(failed, outcome=WorkUnitTerminalOutcome.FAILED))
    failed_bytes = marker.path.read_bytes()
    original_write = OwnerPathRuntimeMarker._write

    def fail_new_launch(owner, snapshot):
        if owner.path == marker.path and snapshot.state is RuntimeMarkerState.LAUNCHED:
            raise OSError("injected launch write failure")
        return original_write(owner, snapshot)

    monkeypatch.setattr(OwnerPathRuntimeMarker, "_write", fail_new_launch)
    with pytest.raises(OSError, match="injected launch"):
        marker.record_launch(_runtime(retry))
    assert marker.path.read_bytes() == failed_bytes
    monkeypatch.setattr(OwnerPathRuntimeMarker, "_write", original_write)

    launch = marker.record_launch(_runtime(retry))
    archives = [path for path in tmp_path.glob("attempt.runtime.*.json")]
    assert len(archives) == 1
    assert archives[0].read_bytes() == failed_bytes
    old_marker = OwnerPathRuntimeMarker(archives[0], marker.artifact_namespace)
    assert old_marker.recover_after_coordinator_loss(failed).terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert launch.processing_attempt_id == retry.processing_attempt_id
    marker(_terminal_records(retry, outcome=WorkUnitTerminalOutcome.SUCCEEDED))
    assert marker.recover_after_coordinator_loss(retry).terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert archives[0].read_bytes() == failed_bytes


@pytest.mark.parametrize("terminal", [None, WorkUnitTerminalOutcome.SUCCEEDED])
def test_retry_option_does_not_replace_active_or_successful_attempt(
    tmp_path: Path, terminal: WorkUnitTerminalOutcome | None
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
        allow_failed_attempt_retry=True,
    )
    first = _attempt(tmp_path, stem="first")
    marker.record_launch(_runtime(first))
    if terminal is not None:
        marker(_terminal_records(first, outcome=terminal))
    original_bytes = marker.path.read_bytes()
    with pytest.raises(RuntimeFinalizationContractError, match="different attempt"):
        marker.record_launch(_runtime(_attempt(tmp_path, stem="second")))
    assert marker.path.read_bytes() == original_bytes


def test_marker_reader_rejects_obsolete_outer_schema(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:v3-compat",
    )
    marker.record_launch(_runtime(attempt))
    payload = json.loads(marker.path.read_bytes())
    payload["schema_version"] = "unsupported_runtime_marker"
    unsigned = {key: value for key, value in payload.items() if key != "marker_sha256"}
    payload["marker_sha256"] = hashlib.sha256(
        _canonical_json_bytes(unsigned)
    ).hexdigest()
    marker.path.write_bytes(_canonical_json_bytes(payload))

    with pytest.raises(RuntimeFinalizationContractError, match="schema_version"):
        marker.read()


def test_finalized_marker_bytes_bind_the_exact_observability_ledger(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    coordinator = _terminal_records(
        attempt,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
    ).coordinator
    first_ledger = _derive_runtime_observability_ledger(
        _mint_runtime_attempt_aggregate(state=coordinator.state),
        coordinator,
        telemetry=build_runtime_telemetry_samples_from_coordinator(
            coordinator,
            process_tree_peak_rss_bytes=1,
        ),
    )
    second_ledger = _derive_runtime_observability_ledger(
        _mint_runtime_attempt_aggregate(state=coordinator.state),
        coordinator,
        telemetry=build_runtime_telemetry_samples_from_coordinator(
            coordinator,
            process_tree_peak_rss_bytes=2,
        ),
    )
    first_marker = OwnerPathRuntimeMarker(
        tmp_path / "first.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    second_marker = OwnerPathRuntimeMarker(
        tmp_path / "second.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    for marker in (first_marker, second_marker):
        marker.record_launch(_runtime(attempt))

    first_marker(
        _mint_runtime_finalization_receipt(
            coordinator=coordinator,
            supervisor=mint_supervisor_finalization(
                coordinator,
                observability_ledger=first_ledger,
            ),
        ),
    )
    second_marker(
        _mint_runtime_finalization_receipt(
            coordinator=coordinator,
            supervisor=mint_supervisor_finalization(
                coordinator,
                observability_ledger=second_ledger,
            ),
        ),
    )

    first_recovery = first_marker.recover_after_coordinator_loss(attempt)
    second_recovery = second_marker.recover_after_coordinator_loss(attempt)
    assert first_marker.path.read_bytes() != second_marker.path.read_bytes()
    assert first_recovery.observability_ledger_bytes == (
        canonical_runtime_observability_ledger_bytes(first_ledger)
    )
    assert second_recovery.observability_ledger_bytes == (
        canonical_runtime_observability_ledger_bytes(second_ledger)
    )
    assert (
        first_recovery.observability_ledger_bytes
        != second_recovery.observability_ledger_bytes
    )


def test_recovery_rejects_coordinator_bytes_that_do_not_match_digest(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    receipt = _terminal_records(
        attempt,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))
    marker(receipt)
    payload = json.loads(marker.path.read_bytes())
    payload["coordinator_record_base64"] = base64.b64encode(
        b'{"tampered":true}'
    ).decode("ascii")
    unsigned = {key: value for key, value in payload.items() if key != "marker_sha256"}
    payload["marker_sha256"] = hashlib.sha256(
        _canonical_json_bytes(unsigned)
    ).hexdigest()
    marker.path.write_bytes(_canonical_json_bytes(payload))

    with pytest.raises(RuntimeFinalizationContractError, match="coordinator digest"):
        marker.recover_after_coordinator_loss(attempt)


def test_concurrent_different_launches_fail_closed_at_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    first = _attempt(tmp_path, stem="first")
    second = _attempt(tmp_path, stem="second")
    _delay_competing_writes(monkeypatch)

    outcomes = _run_concurrently(
        lambda: marker.record_launch(_runtime(first)),
        lambda: marker.record_launch(_runtime(second)),
    )

    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
    assert marker.read().processing_attempt_id in {
        first.processing_attempt_id,
        second.processing_attempt_id,
    }


def test_concurrent_conflicting_finalizations_fail_closed_at_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = _attempt(tmp_path)
    succeeded = _terminal_records(
        attempt,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
    )
    failed = _terminal_records(
        attempt,
        outcome=WorkUnitTerminalOutcome.FAILED,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))
    _delay_competing_writes(monkeypatch)

    outcomes = _run_concurrently(
        lambda: marker(succeeded),
        lambda: marker(failed),
    )

    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
    assert marker.read().terminal_outcome in {
        AttemptTerminalOutcome.SUCCEEDED,
        AttemptTerminalOutcome.FAILED,
    }


@pytest.mark.parametrize("release_before_denial", [False, True])
def test_windows_permission_race_while_acquiring_marker_lock_is_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_before_denial: bool,
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    real_open = os.open
    lock_path = tmp_path / ".attempt.runtime.json.lock"
    permission_races = 0

    def race_once(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        nonlocal permission_races
        if str(path).endswith(".lock") and permission_races == 0:
            permission_races += 1
            descriptor = real_open(path, flags, mode)
            os.close(descriptor)
            if release_before_denial:
                lock_path.unlink()
            raise PermissionError(13, "synthetic concurrent Windows lock denial", path)
        if str(path).endswith(".lock") and lock_path.exists():
            lock_path.unlink()
        return real_open(path, flags, mode)

    monkeypatch.setattr(runtime_marker_module.os, "open", race_once)

    snapshot = marker.record_launch(runtime)

    assert permission_races == 1
    assert snapshot.processing_attempt_id == attempt.processing_attempt_id
    assert marker.read() == snapshot


def test_windows_sharing_violation_retries_without_directory_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    real_open = os.open
    opened_paths: list[str] = []

    def sharing_violation_once(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        opened_paths.append(str(path))
        if str(path).endswith(".lock") and len(opened_paths) == 1:
            raise PermissionError(13, "synthetic sharing violation", path, 32)
        return real_open(path, flags, mode)

    monkeypatch.setattr(runtime_marker_module.os, "open", sharing_violation_once)

    snapshot = marker.record_launch(runtime)

    assert snapshot.processing_attempt_id == attempt.processing_attempt_id
    assert not any(path.endswith(".probe") for path in opened_paths)


def test_marker_lock_does_not_recast_unrelated_permission_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    runtime = _runtime(_attempt(tmp_path))
    lock_path = tmp_path / ".attempt.runtime.json.lock"
    lock_path.write_bytes(b"existing-lock")
    real_open = os.open

    def deny_lock(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        if Path(path).parent == tmp_path:
            raise PermissionError(13, "synthetic directory ACL denial", path)
        return real_open(path, flags, mode)

    monkeypatch.setattr(runtime_marker_module.os, "open", deny_lock)
    monkeypatch.setattr(runtime_marker_module, "_MARKER_LOCK_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(PermissionError, match="synthetic directory ACL denial"):
        marker.record_launch(runtime)


def test_marker_rejects_noncanonical_or_wrong_attempt_content(
    tmp_path: Path,
) -> None:
    marker_path = tmp_path / "attempt.runtime.json"
    marker_path.write_text('{"schema_version": "wrong"}', encoding="utf-8")
    marker = OwnerPathRuntimeMarker(marker_path, "runtime-attempt-artifacts:a")

    with pytest.raises(RuntimeFinalizationContractError, match="fields"):
        marker.read()

    marker_path.unlink()
    first = _attempt(tmp_path, stem="first")
    second = _attempt(tmp_path, stem="second")
    marker.record_launch(_runtime(first))
    with pytest.raises(RuntimeFinalizationContractError, match="different attempt"):
        marker.record_launch(_runtime(second))


def test_marker_rejects_canonical_but_digest_mismatched_content(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))
    payload = json.loads(marker.path.read_bytes())
    payload["artifact_namespace"] = "runtime-attempt-artifacts:tampered"
    marker.path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeFinalizationContractError, match="digest"):
        marker.read()


def _terminal_records(
    attempt: ProcessingAttemptIdentity,
    *,
    outcome: WorkUnitTerminalOutcome,
):
    runtime = _runtime(attempt)
    reference = mint_targeted_process_result_ref(
        processing_attempt=attempt,
        work_unit_key="sample:a",
        owner_result_payload=b"no-raw-result",
    )
    runtime = settle_work_unit(
        runtime,
        work_unit_key="sample:a",
        outcome=outcome,
        owner_result_ref=(
            reference.artifact if outcome is WorkUnitTerminalOutcome.SUCCEEDED else None
        ),
        failure_detail=(
            None
            if outcome is WorkUnitTerminalOutcome.SUCCEEDED
            else WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="runtime-marker-test",
                source_reference="no-raw:sample:a",
                message="worker failed before finalization",
            )
        ),
    )
    state = reserve_runtime_resource(
        begin_attempt_finalization(runtime),
        RuntimeResourceReservation(
            attempt=attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key="process:sample:a",
            worker_key="worker:1",
            source_reference="no-raw:sample:a",
            compatibility_token="alignment:no-raw",
        ),
    )
    state = settle_runtime_resource(
        state,
        resource_key="process:sample:a",
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )
    if outcome is WorkUnitTerminalOutcome.SUCCEEDED:
        state = register_runtime_artifact(
            state,
            work_unit_key="sample:a",
            artifact=reference.artifact,
        )
    coordinator = propose_attempt_terminal(state)
    return _mint_runtime_finalization_receipt(
        coordinator=coordinator,
        supervisor=mint_supervisor_finalization(
            coordinator,
            observability_ledger=_ledger(coordinator),
        ),
    )


def _delay_competing_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    original = OwnerPathRuntimeMarker._write
    barrier = threading.Barrier(2)

    def delayed(marker, snapshot):
        try:
            barrier.wait(timeout=0.25)
        except threading.BrokenBarrierError:
            pass
        return original(marker, snapshot)

    monkeypatch.setattr(OwnerPathRuntimeMarker, "_write", delayed)


def _run_concurrently(*calls):
    start = threading.Barrier(len(calls))

    def invoke(call):
        start.wait(timeout=2)
        try:
            return "ok", call()
        except RuntimeFinalizationContractError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        return tuple(executor.map(invoke, calls))


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _runtime(attempt: ProcessingAttemptIdentity) -> AttemptRuntime:
    queued = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            queued,
            attempt=attempt,
            work_unit_keys=("sample:a",),
            owner_completeness_attestation="runtime-marker-test",
        )
    )


def _ledger(coordinator: CoordinatorTerminalRecord):
    return _derive_runtime_observability_ledger(
        _mint_runtime_attempt_aggregate(state=coordinator.state),
        coordinator,
        telemetry=build_runtime_telemetry_samples_from_coordinator(coordinator),
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
                adapter_input_key=f"{stem}.raw",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="runtime_marker_test",
        processing_algorithm_version="runtime_marker_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
