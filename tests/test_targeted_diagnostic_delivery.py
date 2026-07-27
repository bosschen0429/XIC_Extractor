from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_targeted_diagnostic_method import _method_request
from tests.test_targeted_method_runtime import (
    _alignment_job,
    _completion_spec,
    _marker,
    _reservations,
    _runtime,
)
from xic_extractor.alignment.runtime_adapter import (
    mint_alignment_process_result_artifact,
)
from xic_extractor.evidence_artifacts import (
    EvidenceArtifactRef,
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
    verify_evidence_artifact_payload,
)
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    settle_work_unit,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceReservation,
    begin_attempt_finalization,
    mark_worker_lost,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    register_runtime_artifact,
    reserve_runtime_resource,
)
from xic_extractor.extraction.targeted_diagnostic_delivery import (
    TargetedDiagnosticDeliveryError,
    rollback_targeted_diagnostic_delivery_candidate,
    settle_targeted_diagnostic_delivery_candidate,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    canonical_targeted_diagnostic_method_bytes,
    run_targeted_diagnostic_method,
)
from xic_extractor.extraction.targeted_method_runtime import (
    build_targeted_method_completion_jobs,
    execute_targeted_method_alignment_runtime,
    execute_targeted_method_completion_runtime,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    mint_targeted_process_result_ref,
)
from xic_extractor.output.csv_writers import write_wide_csv

_CSV_BYTES = b"legacy,current,remains,authoritative\n"


def test_true_spawned_method_stages_hard_exact_candidate_and_rolls_back(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    alignment_job = _alignment_job(
        request,
        fixture.attempt,
        "alignment:method",
    )
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(fixture.attempt, ("alignment:method",)),
        (alignment_job,),
        max_workers=1,
        resource_reservations=_reservations((alignment_job,)),
        recovery_marker=_marker(tmp_path, "delivery-alignment"),
    )
    completion_jobs = build_targeted_method_completion_jobs(
        alignment,
        (_completion_spec(request),),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(fixture.attempt, ("targeted:method",)),
        completion_jobs,
        max_workers=1,
        resource_reservations=_reservations(completion_jobs),
        recovery_marker=_marker(tmp_path, "delivery-targeted"),
    )
    result = targeted.settlement.successful_targeted_method_results[0][1]
    alignment_artifact = alignment.runtime.settlements[0].owner_result_ref
    targeted_artifact = targeted.settlement.runtime.settlements[0].owner_result_ref
    assert alignment_artifact is not None
    assert targeted_artifact is not None

    output_config = replace(
        request.peak_config,
        output_csv=tmp_path / "legacy" / "xic_results.csv",
    )
    write_wide_csv(
        output_config,
        request.targets,
        request.authoritative_legacy_output.file_results,
    )
    legacy_bytes = output_config.output_csv.read_bytes()
    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=targeted.finalization_record,
        alignment_finalization=alignment.finalization_record,
        targeted_runtime_artifacts=(targeted_artifact,),
        alignment_runtime_artifacts=(alignment_artifact,),
        candidate_csv_bytes=legacy_bytes,
        authoritative_legacy_csv_bytes=legacy_bytes,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "staged_candidate"
    assert delivery.diagnostic_csv_artifact is not None
    verify_evidence_artifact_payload(delivery.diagnostic_csv_artifact, legacy_bytes)
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(delivery)
    assert rolled_back.status == "rolled_back"
    assert rolled_back.active_output == request.authoritative_legacy_output
    assert rolled_back.prior_current_delivery_id == "legacy-seal:prior-current"
    assert rolled_back.lineage_artifacts == delivery.lineage_artifacts
    assert rolled_back.stage_candidate == delivery.stage_candidate
    assert not tuple(tmp_path.rglob("*.xlsx"))
    assert not tuple(tmp_path.rglob("*matrix*"))


def test_success_stages_only_delivery_lineage_candidates(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    targeted, alignment = _runtime_owner_artifacts(request.alignment_inputs, fixture)
    targeted_finalization = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=targeted,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )

    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=targeted_finalization,
        alignment_finalization=alignment_finalization,
        targeted_runtime_artifacts=(targeted,),
        alignment_runtime_artifacts=(alignment,),
        candidate_csv_bytes=_CSV_BYTES,
        authoritative_legacy_csv_bytes=_CSV_BYTES,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "staged_candidate"
    assert delivery.active_output is request.authoritative_legacy_output
    assert delivery.prior_current_delivery_id == "legacy-seal:prior-current"
    assert delivery.stage_candidate is not None
    assert delivery.seal_candidate is not None
    assert delivery.seal_candidate.stage == delivery.stage_candidate
    assert len(delivery.revisions) == len(request.alignment_inputs)
    assert delivery.method_result_artifact is not None
    assert delivery.diagnostic_csv_artifact is not None
    verify_evidence_artifact_payload(delivery.diagnostic_csv_artifact, _CSV_BYTES)
    lineage_ids = {
        artifact.evidence_artifact_id for artifact in delivery.lineage_artifacts
    }
    for item in request.alignment_inputs:
        assert {
            artifact.evidence_artifact_id
            for artifact in item.candidate_set.lineage_artifacts
        }.issubset(lineage_ids)


@pytest.mark.parametrize(
    "outcome",
    [WorkUnitTerminalOutcome.FAILED, WorkUnitTerminalOutcome.CANCELLED],
)
def test_failed_or_cancelled_attempt_is_quarantined(
    tmp_path: Path,
    outcome: WorkUnitTerminalOutcome,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    _targeted, alignment = _runtime_owner_artifacts(request.alignment_inputs, fixture)
    unsuccessful = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=outcome,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )

    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=unsuccessful,
        alignment_finalization=alignment_finalization,
        targeted_runtime_artifacts=(),
        alignment_runtime_artifacts=(alignment,),
        candidate_csv_bytes=b"must-not-be-compared",
        authoritative_legacy_csv_bytes=b"different",
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "quarantined"
    assert delivery.stage_candidate is None
    assert delivery.seal_candidate is None
    assert delivery.diagnostic_csv_artifact is None
    assert delivery.active_output is request.authoritative_legacy_output


def test_worker_loss_is_quarantined_with_runtime_evidence(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    targeted, alignment = _runtime_owner_artifacts(request.alignment_inputs, fixture)
    worker_loss = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=targeted,
        worker_loss=True,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )

    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=worker_loss,
        alignment_finalization=alignment_finalization,
        targeted_runtime_artifacts=(targeted,),
        alignment_runtime_artifacts=(alignment,),
        candidate_csv_bytes=_CSV_BYTES,
        authoritative_legacy_csv_bytes=_CSV_BYTES,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "quarantined"
    assert "failed" in delivery.quarantine_reason
    assert targeted in delivery.lineage_artifacts


def test_partial_and_conflicting_replay_never_stage(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    targeted, alignment = _runtime_owner_artifacts(request.alignment_inputs, fixture)
    targeted_finalization = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=targeted,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )
    kwargs = dict(
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=targeted_finalization,
        alignment_finalization=alignment_finalization,
        alignment_runtime_artifacts=(alignment,),
        candidate_csv_bytes=_CSV_BYTES,
        authoritative_legacy_csv_bytes=_CSV_BYTES,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    partial = settle_targeted_diagnostic_delivery_candidate(
        result,
        targeted_runtime_artifacts=(),
        **kwargs,
    )
    assert partial.status == "quarantined"
    assert "partial" in partial.quarantine_reason

    conflicting = replace(
        targeted_finalization,
        artifact_dispositions=(
            *targeted_finalization.artifact_dispositions,
            (
                targeted.evidence_artifact_id,
                RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC,
            ),
        ),
    )
    conflict_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key != "targeted_finalization"
    }
    conflict = settle_targeted_diagnostic_delivery_candidate(
        result,
        targeted_finalization=conflicting,
        targeted_runtime_artifacts=(targeted,),
        **conflict_kwargs,
    )
    assert conflict.status == "quarantined"
    assert "conflicting" in conflict.quarantine_reason


def test_csv_conflict_and_rollback_preserve_legacy_current_and_evidence(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    targeted, alignment = _runtime_owner_artifacts(request.alignment_inputs, fixture)
    targeted_finalization = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=targeted,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )
    kwargs = dict(
        alignment_inputs=request.alignment_inputs,
        attempt=fixture.attempt,
        targeted_finalization=targeted_finalization,
        alignment_finalization=alignment_finalization,
        targeted_runtime_artifacts=(targeted,),
        alignment_runtime_artifacts=(alignment,),
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    mismatch = settle_targeted_diagnostic_delivery_candidate(
        result,
        candidate_csv_bytes=b"candidate\n",
        authoritative_legacy_csv_bytes=b"legacy\n",
        **kwargs,
    )
    assert mismatch.status == "quarantined"
    assert mismatch.diagnostic_csv_artifact is None

    staged = settle_targeted_diagnostic_delivery_candidate(
        result,
        candidate_csv_bytes=_CSV_BYTES,
        authoritative_legacy_csv_bytes=_CSV_BYTES,
        **kwargs,
    )
    rolled_back = rollback_targeted_diagnostic_delivery_candidate(staged)

    assert rolled_back.status == "rolled_back"
    assert rolled_back.active_output is request.authoritative_legacy_output
    assert rolled_back.prior_current_delivery_id == "legacy-seal:prior-current"
    assert rolled_back.lineage_artifacts == staged.lineage_artifacts
    assert rolled_back.method_result_artifact == staged.method_result_artifact
    assert rolled_back.diagnostic_csv_artifact == staged.diagnostic_csv_artifact


def test_missing_runtime_upstream_is_lineage_tamper(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    roots = _candidate_roots(request.alignment_inputs)
    omitted = _opaque_artifact(fixture.attempt, b"omitted-runtime-ancestor")
    targeted = mint_targeted_process_result_ref(
        processing_attempt=fixture.attempt,
        work_unit_key="package2:targeted-delivery",
        owner_result_payload=canonical_targeted_diagnostic_method_bytes(result),
        upstream_artifacts=(*roots, omitted),
    ).artifact
    alignment = mint_alignment_process_result_artifact(
        processing_attempt=fixture.attempt,
        work_unit_key="package2:alignment-delivery",
        owner_result_payload=canonical_targeted_diagnostic_method_bytes(result),
        upstream_artifacts=roots,
    )
    targeted_finalization = _finalization(
        fixture,
        work_unit_key="package2:targeted-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=targeted,
    )
    alignment_finalization = _finalization(
        fixture,
        work_unit_key="package2:alignment-delivery",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        artifact=alignment,
    )

    with pytest.raises(TargetedDiagnosticDeliveryError, match="missing upstream"):
        settle_targeted_diagnostic_delivery_candidate(
            result,
            alignment_inputs=request.alignment_inputs,
            attempt=fixture.attempt,
            targeted_finalization=targeted_finalization,
            alignment_finalization=alignment_finalization,
            targeted_runtime_artifacts=(targeted,),
            alignment_runtime_artifacts=(alignment,),
            candidate_csv_bytes=_CSV_BYTES,
            authoritative_legacy_csv_bytes=_CSV_BYTES,
            prior_current_delivery_id="legacy-seal:prior-current",
        )


def _runtime_owner_artifacts(alignment_inputs, fixture):
    roots = _candidate_roots(alignment_inputs)
    payload = b"runtime-owner-result"
    targeted = mint_targeted_process_result_ref(
        processing_attempt=fixture.attempt,
        work_unit_key="package2:targeted-delivery",
        owner_result_payload=payload,
        upstream_artifacts=roots,
    ).artifact
    alignment = mint_alignment_process_result_artifact(
        processing_attempt=fixture.attempt,
        work_unit_key="package2:alignment-delivery",
        owner_result_payload=payload,
        upstream_artifacts=roots,
    )
    return targeted, alignment


def _candidate_roots(alignment_inputs) -> tuple[EvidenceArtifactRef, ...]:
    by_id = {
        item.candidate_set.artifact.evidence_artifact_id: item.candidate_set.artifact
        for item in alignment_inputs
    }
    return tuple(by_id[artifact_id] for artifact_id in sorted(by_id))


def _finalization(
    fixture,
    *,
    work_unit_key: str,
    outcome: WorkUnitTerminalOutcome,
    artifact: EvidenceArtifactRef | None = None,
    worker_loss: bool = False,
):
    history = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=fixture.attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            history,
            attempt=fixture.attempt,
            work_unit_keys=(work_unit_key,),
            owner_completeness_attestation="package2-delivery:test",
        )
    )
    if outcome is WorkUnitTerminalOutcome.CANCELLED:
        runtime = replace(
            runtime,
            history=append_attempt_lifecycle_event(
                runtime.history,
                AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
            ),
        )
    runtime = settle_work_unit(
        runtime,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=artifact,
    )
    state = begin_attempt_finalization(runtime)
    if artifact is not None:
        state = register_runtime_artifact(
            state,
            work_unit_key=work_unit_key,
            artifact=artifact,
        )
    if worker_loss:
        reservation = RuntimeResourceReservation(
            attempt=fixture.attempt,
            resource_key=f"resource:{work_unit_key}",
            worker_key=f"worker:{work_unit_key}",
            source_reference="synthetic:no-raw",
            compatibility_token="package2-delivery:test",
        )
        state = reserve_runtime_resource(state, reservation)
        state = mark_worker_lost(state, worker_key=reservation.worker_key)
    return mint_supervisor_finalization(propose_attempt_terminal(state))


def _opaque_artifact(attempt, payload: bytes) -> EvidenceArtifactRef:
    contract = _mint_artifact_semantic_contract_ref(
        owner_namespace="tests.targeted_diagnostic_delivery",
        semantic_role="omitted_runtime_ancestor",
        schema_version="test_v1",
        validation_status="diagnostic_only",
        media_type="application/octet-stream",
        serialization_format="exact-bytes-v1",
    )
    return _mint_evidence_artifact_ref(
        producer_processing_request_id=attempt.processing_request_id,
        producer_processing_attempt_id=attempt.processing_attempt_id,
        semantic_contract=contract,
        payload_bytes=payload,
    )
