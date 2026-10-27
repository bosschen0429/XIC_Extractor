from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import xic_extractor.execution.recovery as runtime_marker_module
from tests.runtime_observability_fixtures import (
    reserve_runtime_resource,
    settle_runtime_resource,
    settle_work_unit,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from xic_extractor.execution._finalization import (
    CoordinatorTerminalRecord,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    begin_attempt_finalization,
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
from xic_extractor.execution.attempt import (
    RuntimeAttemptAggregate,
    RuntimeFinalizationReceipt,
    _derive_runtime_observability_ledger,
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


def test_launch_marker_persists_unfinished_attempt_scope(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )

    launch = marker.record_launch(runtime)
    persisted = marker.read()

    assert launch.state is RuntimeMarkerState.LAUNCHED
    assert marker.record_launch(runtime) == launch
    assert persisted == launch
    assert persisted.terminal_outcome is None


def test_finalization_atomically_replaces_launch_with_terminal_status(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    runtime = _runtime(attempt)
    runtime = settle_work_unit(
        runtime,
        work_unit_key="sample:a",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
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
    coordinator = propose_attempt_terminal(state)
    ledger = _ledger(coordinator)
    proposed = mint_supervisor_finalization(
        coordinator,
        observability_ledger=ledger,
    )
    receipt = RuntimeFinalizationReceipt(
        coordinator=coordinator,
        supervisor=proposed,
    )
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))

    accepted = marker(receipt)
    persisted = marker.read()

    assert accepted == receipt
    payload = json.loads(marker.path.read_bytes())
    assert payload["schema_version"] == "execution_runtime_status_marker.v3"
    assert marker.read().state is RuntimeMarkerState.FINALIZED
    assert marker(receipt) == accepted
    with pytest.raises(RuntimeFinalizationContractError, match="already durably"):
        marker.record_launch(_runtime(attempt))
    assert persisted.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert set(payload) == {
        "schema_version",
        "state",
        "processing_request_id",
        "processing_attempt_id",
        "artifact_namespace",
        "manifest_work_unit_keys",
        "terminal_outcome",
    }


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
    assert old_marker.read().terminal_outcome is (AttemptTerminalOutcome.FAILED)
    assert launch.processing_attempt_id == retry.processing_attempt_id
    marker(_terminal_records(retry, outcome=WorkUnitTerminalOutcome.SUCCEEDED))
    assert marker.read().terminal_outcome is (AttemptTerminalOutcome.SUCCEEDED)
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
    marker.path.write_bytes(_canonical_json_bytes(payload))

    with pytest.raises(RuntimeFinalizationContractError, match="schema_version"):
        marker.read()


@pytest.mark.parametrize("field", ["processing_request_id", "processing_attempt_id"])
def test_marker_reader_rejects_invalid_persisted_identity(
    tmp_path: Path, field: str
) -> None:
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json", "runtime-attempt-artifacts:a"
    )
    marker.record_launch(_runtime(_attempt(tmp_path)))
    payload = json.loads(marker.path.read_bytes())
    payload[field] = "../different-attempt"
    marker.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeFinalizationContractError, match="processing identity"):
        marker.read()


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


def test_marker_rejects_unsupported_schema_or_wrong_attempt_content(
    tmp_path: Path,
) -> None:
    marker_path = tmp_path / "attempt.runtime.json"
    marker_path.write_text('{"schema_version": "wrong"}', encoding="utf-8")
    marker = OwnerPathRuntimeMarker(marker_path, "runtime-attempt-artifacts:a")

    with pytest.raises(RuntimeFinalizationContractError, match="schema_version"):
        marker.read()

    marker_path.unlink()
    first = _attempt(tmp_path, stem="first")
    second = _attempt(tmp_path, stem="second")
    marker.record_launch(_runtime(first))
    with pytest.raises(RuntimeFinalizationContractError, match="different attempt"):
        marker.record_launch(_runtime(second))


def _terminal_records(
    attempt: ProcessingAttemptIdentity,
    *,
    outcome: WorkUnitTerminalOutcome,
):
    runtime = _runtime(attempt)
    runtime = settle_work_unit(
        runtime,
        work_unit_key="sample:a",
        outcome=outcome,
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
    coordinator = propose_attempt_terminal(state)
    return RuntimeFinalizationReceipt(
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
        RuntimeAttemptAggregate(state=coordinator.state),
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
