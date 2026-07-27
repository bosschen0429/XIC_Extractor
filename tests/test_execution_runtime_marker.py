from __future__ import annotations

import json
from pathlib import Path

import pytest

from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    settle_work_unit,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    begin_attempt_finalization,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    register_runtime_artifact,
    reserve_runtime_resource,
    settle_runtime_resource,
)
from xic_extractor.execution_runtime_marker import (
    OwnerPathRuntimeMarker,
    RuntimeMarkerState,
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
            resource_key="process:sample:a",
            worker_key="worker:1",
            source_reference="no-raw:sample:a",
            compatibility_token="alignment:no-raw:v1",
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
    proposed = mint_supervisor_finalization(coordinator)
    marker = OwnerPathRuntimeMarker(
        tmp_path / "attempt.runtime.json",
        "runtime-attempt-artifacts:a",
    )
    marker.record_launch(_runtime(attempt))

    accepted = marker(coordinator, proposed)
    recovered = marker.recover_after_coordinator_loss(attempt)

    assert accepted == proposed
    assert marker.read().state is RuntimeMarkerState.FINALIZED
    assert marker(coordinator, proposed) == accepted
    with pytest.raises(RuntimeFinalizationContractError, match="already durably"):
        marker.record_launch(_runtime(attempt))
    assert recovered.terminal_outcome is AttemptTerminalOutcome.SUCCEEDED
    assert recovered.finalization_acknowledged is True
    assert recovered.quarantine_artifact_namespace is False
    assert recovered.artifact_dispositions == (
        (
            reference.artifact.evidence_artifact_id,
            RuntimeArtifactDisposition.OFFERED_TO_OWNER,
        ),
    )


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
            owner_completeness_attestation="runtime-marker-test:v1",
        )
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
        scientific_input_schema_version="runtime_marker_test_v1",
        processing_algorithm_version="runtime_marker_test_v1",
        processing_contract_version="processing_identity_v1",
        scientific_inputs={"method": "cid_nl", "neutral_loss_da": 116.0},
    )
    return issue_processing_attempt(request)
