from __future__ import annotations

import csv
import hashlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path

import pytest

from tests.package2_no_raw_fixture import Package2NoRawFixture
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_hypothesis_backfill import (
    FrozenNoRawBackfillSource,
    _complete_inputs,
    _peak_config,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeProcessResult,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.config import Target
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeArtifactRegistrationReceipt,
    RuntimeWorkerBatchReceipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodParityError,
    TargetedDiagnosticMethodRequest,
    canonical_targeted_diagnostic_method_bytes,
    rollback_targeted_diagnostic_delivery,
    run_targeted_diagnostic_method,
    settle_targeted_diagnostic_delivery,
    targeted_attempt_namespace_component,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeWorkerBatch,
    mint_targeted_process_result_ref,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    build_targeted_application_request,
)
from xic_extractor.extractor import FileResult, RunOutput
from xic_extractor.output.csv_writers import write_wide_csv
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)

_FROZEN_LEGACY_CSV_SIZE = 403
_FROZEN_LEGACY_CSV_SHA256 = (
    "f0a471bd81046cefaa924d619b934957505ee22591b17820a73143f7b4126937"
)
_VALIDATION_PACKET = Path("docs/superpowers/validation/package2_targeted_method_v1")
_FROZEN_DIFFERENCE_MANIFEST_SHA256 = (
    "377932de8bac5a929467a108658a3f9c5899a8b0e78fb7c05e277cacec0f2755"
)
_FROZEN_TRACE_ORACLE = {
    "observed-a": (
        "xic.peak-observation-content.v1:"
        "7f4d0a7d42744145df0548360c287f3216e2048115b30a160eba8401d4572b01",
        "d2231dd5dfa0fc8bffaef64012e348659e8fea7a23f3eaa5912c9b77e789ff70",
        "191de630bb7e9817ced1934ea374671a075e9426c5b32973752db7b0294b9145",
    ),
    "observed-b": (
        "xic.peak-observation-content.v1:"
        "3409c591788409f9c20ea74e733cf34cfc12a660d62638e83e3d0a990b631a98",
        "46c4378c16bf340417a72c2e93afd816534244e428416573806af80f39932777",
        "6b7a814a0149b4d23ddc66e1563fa8b3720cccf6d69298f19ec3c200cf47da9b",
    ),
}
_FROZEN_HYPOTHESIS_ORACLE = {
    "observed-a": (
        "xic.peak-hypothesis.v1:"
        "87675a5a6d51d22519a8fabbe877b0c26d060672ec2a7ea542c0e71059ad3d7b"
    ),
    "observed-b": (
        "xic.peak-hypothesis.v1:"
        "30779caaa6d08f18c0dc72ec7d80ec293767cb7a59abaceb7b0fac0c21b60a5b"
    ),
}


@dataclass(frozen=True)
class _FrozenLegacyResult:
    reported_rt: float | None
    reported_peak_area: float | None
    reported_peak_intensity: float | None
    reported_peak_start: float | None
    reported_peak_end: float | None
    reported_peak_width: float | None
    confidence: str
    reason: str
    targeted_product_projection: TargetedProductProjection
    nl_token: str | None = None


@dataclass(frozen=True)
class _MethodProcessJob:
    attempt: object
    work_unit_key: str
    resource_key: str
    payload: bytes


def _targeted_method_worker(job: _MethodProcessJob):
    reference = mint_targeted_process_result_ref(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=job.payload,
    )
    return (
        TargetedProcessWorkerResultEnvelope(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=reference,
        ),
        TargetedProcessResourceReport(
            attempt=job.attempt,
            resource_key=job.resource_key,
            open_outcome=RuntimeResourceOpenOutcome.OPENED,
            cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        ),
    )


def _alignment_method_worker(job: _MethodProcessJob) -> AlignmentRuntimeProcessResult:
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=job.payload,
    )
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact,),
    )


def _alignment_method_error(
    job: _MethodProcessJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="alignment-process-worker",
                source_reference=f"no-raw:{job.work_unit_key}",
                message=str(error),
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def test_complete_method_is_hard_exact_and_rollback_preserves_legacy_current(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    service = TargetedApplicationService(
        _legacy_runner_must_not_be_called,
        diagnostic_method_runner=run_targeted_diagnostic_method,
    )

    result = service.execute_diagnostic_method(request)
    reordered = service.execute_diagnostic_method(
        replace(request, alignment_inputs=tuple(reversed(request.alignment_inputs)))
    )

    assert canonical_targeted_diagnostic_method_bytes(result) == (
        canonical_targeted_diagnostic_method_bytes(reordered)
    )
    assert [cell.status for cell in result.cells] == [
        "ambiguous",
        "backfilled",
        "failed",
        "observed",
        "observed",
        "true_missing",
    ]
    assert _trace_oracle(request) == _FROZEN_TRACE_ORACLE
    assert _hypothesis_oracle(request) == _FROZEN_HYPOTHESIS_ORACLE
    assert result.groups[0].owner_ids == tuple(_FROZEN_HYPOTHESIS_ORACLE.values())
    backfilled = next(cell for cell in result.cells if cell.status == "backfilled")
    assert backfilled.selected_integration is not None
    assert (
        backfilled.selected_integration.rt_apex_min,
        backfilled.selected_integration.area_raw_counts_seconds,
        backfilled.selected_integration.area_ms1_morphology,
    ) == (8.01, 2951.9999999999864, 2835.100474933266)
    target = request.targets[0]
    baseline_config = replace(
        request.peak_config,
        output_csv=tmp_path / "legacy" / "xic_results.csv",
    )
    write_wide_csv(
        baseline_config,
        request.targets,
        request.authoritative_legacy_output.file_results,
    )
    legacy_bytes = baseline_config.output_csv.read_bytes()
    assert len(legacy_bytes) == _FROZEN_LEGACY_CSV_SIZE
    assert hashlib.sha256(legacy_bytes).hexdigest() == _FROZEN_LEGACY_CSV_SHA256
    targeted_finalization = _finalization(
        fixture,
        owner="targeted",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        payload=canonical_targeted_diagnostic_method_bytes(result),
    )
    alignment_finalization = _finalization(
        fixture,
        owner="alignment",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        payload=canonical_targeted_diagnostic_method_bytes(result),
    )

    delivery = settle_targeted_diagnostic_delivery(
        result,
        targeted_finalization=targeted_finalization,
        alignment_finalization=alignment_finalization,
        config=request.peak_config,
        targets=(target,),
        authoritative_legacy_csv_bytes=legacy_bytes,
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "sealed_diagnostic"
    assert delivery.diagnostic_csv_path is not None
    assert delivery.diagnostic_csv_path.read_bytes() == legacy_bytes
    assert delivery.diagnostic_csv_path.parent.name.startswith("attempt-")
    assert ":" not in delivery.diagnostic_csv_path.parent.name
    assert delivery.diagnostic_csv_sha256 == hashlib.sha256(legacy_bytes).hexdigest()
    assert delivery.active_output is request.authoritative_legacy_output
    assert delivery.prior_current_delivery_id == "legacy-seal:prior-current"
    assert not tuple(tmp_path.rglob("*.xlsx"))
    assert not tuple(tmp_path.rglob("*matrix*"))

    rolled_back = rollback_targeted_diagnostic_delivery(delivery, result)
    assert rolled_back.status == "rolled_back"
    assert rolled_back.active_output is request.authoritative_legacy_output
    assert rolled_back.prior_current_delivery_id == "legacy-seal:prior-current"
    assert rolled_back.diagnostic_csv_path == delivery.diagnostic_csv_path


def test_targeted_scheduler_and_alignment_process_runner_consume_method_receipt(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    payload = canonical_targeted_diagnostic_method_bytes(result)
    targeted_job = _MethodProcessJob(
        fixture.attempt,
        "package2:targeted-method",
        "process:package2:targeted-method",
        payload,
    )
    alignment_job = _MethodProcessJob(
        fixture.attempt,
        "package2:alignment-method",
        "process:package2:alignment-method",
        payload,
    )

    def lifecycle_runner(_request, _state):
        with ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        ) as executor:
            envelope, report = executor.submit(
                _targeted_method_worker,
                targeted_job,
            ).result(timeout=15)
        return TargetedRuntimeWorkerBatch(
            result_envelopes=(envelope,),
            resource_reports=(report,),
            runtime_artifact_refs=(envelope.owner_result_ref,),
        )

    service = TargetedApplicationService(
        _legacy_runner_must_not_be_called,
        runtime_lifecycle_runner=lifecycle_runner,
        runtime_recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "targeted-runtime-marker.json",
            "package2-targeted-method:test",
        ),
        diagnostic_method_runner=run_targeted_diagnostic_method,
    )
    targeted = service.execute_runtime_lifecycle(
        build_targeted_application_request(request.peak_config, list(request.targets)),
        _runtime(fixture, targeted_job.work_unit_key),
        resource_reservations=(_reservation(fixture, targeted_job),),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
    )
    alignment = execute_alignment_process_runtime(
        _runtime(fixture, alignment_job.work_unit_key),
        (alignment_job,),
        worker=_alignment_method_worker,
        error_factory=_alignment_method_error,
        max_workers=1,
        resource_reservations=(_reservation(fixture, alignment_job),),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=OwnerPathRuntimeMarker(
            tmp_path / "alignment-runtime-marker.json",
            "package2-alignment-method:test",
        ),
    )

    assert targeted.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert alignment.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert targeted.settlement.successful_result_refs[0][
        1
    ].owner_result_content_sha256 == (hashlib.sha256(payload).hexdigest())
    assert alignment.runtime.settlements[0].owner_result_ref is not None


@pytest.mark.parametrize(
    "outcome",
    [WorkUnitTerminalOutcome.FAILED, WorkUnitTerminalOutcome.CANCELLED],
)
def test_failed_or_cancelled_successor_attempt_is_quarantined_before_writer(
    tmp_path: Path,
    outcome: WorkUnitTerminalOutcome,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    successful = _finalization(
        fixture,
        owner="alignment",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        payload=canonical_targeted_diagnostic_method_bytes(result),
    )
    unsuccessful = _finalization(
        fixture,
        owner="targeted",
        outcome=outcome,
        payload=b"",
    )

    delivery = settle_targeted_diagnostic_delivery(
        result,
        targeted_finalization=unsuccessful,
        alignment_finalization=successful,
        config=request.peak_config,
        targets=request.targets,
        authoritative_legacy_csv_bytes=b"must-not-be-read",
        prior_current_delivery_id="legacy-seal:prior-current",
    )

    assert delivery.status == "quarantined"
    assert delivery.active_output is request.authoritative_legacy_output
    assert delivery.diagnostic_csv_path is None
    assert delivery.prior_current_delivery_id == "legacy-seal:prior-current"
    assert not (request.peak_config.output_csv.parent / "successor_attempts").exists()


def test_semantic_area_difference_stops_before_delivery(tmp_path: Path) -> None:
    request, _fixture = _method_request(tmp_path)
    legacy = request.authoritative_legacy_output
    changed_files = list(legacy.file_results)
    index = next(
        index
        for index, file_result in enumerate(changed_files)
        if file_result.sample_name == "observed-a"
    )
    original_file = changed_files[index]
    original_result = original_file.results["Analyte"]
    assert isinstance(original_result, _FrozenLegacyResult)
    assert original_result.reported_peak_area is not None
    changed_result = replace(
        original_result,
        reported_peak_area=original_result.reported_peak_area + 1.0,
    )
    changed_files[index] = FileResult(
        sample_name=original_file.sample_name,
        results={"Analyte": changed_result},
    )
    changed_request = replace(
        request,
        authoritative_legacy_output=RunOutput(changed_files, []),
    )

    with pytest.raises(
        TargetedDiagnosticMethodParityError,
        match="selected peak/boundary/area/count",
    ):
        run_targeted_diagnostic_method(changed_request)


def test_legacy_writer_target_must_match_exact_owner_definition(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    changed_target = replace(
        request.targets[0],
        ppm_tol=request.targets[0].ppm_tol + 1.0,
    )

    with pytest.raises(
        ValueError,
        match="exact owner-resolved definition",
    ):
        run_targeted_diagnostic_method(
            replace(request, targets=(changed_target,))
        )


def test_successor_reason_difference_stops_before_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import xic_extractor.extraction.targeted_diagnostic_method as method_module

    request, _fixture = _method_request(tmp_path)
    original_builder = method_module.build_targeted_hypothesis_cells

    def tampered_builder(*args, **kwargs):
        cells = original_builder(*args, **kwargs)
        return tuple(
            replace(cell, reason="caller-substituted-successor-reason")
            if cell.status == "observed"
            else cell
            for cell in cells
        )

    monkeypatch.setattr(
        method_module,
        "build_targeted_hypothesis_cells",
        tampered_builder,
    )
    with pytest.raises(
        TargetedDiagnosticMethodParityError,
        match="non-canonical owner reason",
    ):
        run_targeted_diagnostic_method(request)


def test_exact_byte_difference_is_retained_in_attempt_namespace(tmp_path: Path) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    payload = canonical_targeted_diagnostic_method_bytes(result)
    targeted = _finalization(
        fixture,
        owner="targeted",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        payload=payload,
    )
    alignment = _finalization(
        fixture,
        owner="alignment",
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        payload=payload,
    )

    with pytest.raises(TargetedDiagnosticMethodParityError, match="bytes differ"):
        settle_targeted_diagnostic_delivery(
            result,
            targeted_finalization=targeted,
            alignment_finalization=alignment,
            config=request.peak_config,
            targets=request.targets,
            authoritative_legacy_csv_bytes=b"different\n",
            prior_current_delivery_id="legacy-seal:prior-current",
        )
    difference_path = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(result.processing_attempt_id)
        / "xic_results.csv"
    )
    assert difference_path.is_file()
    assert difference_path.read_bytes() != b"different\n"


def test_package2_validation_packet_is_exact_zero_and_complete() -> None:
    manifest_path = _VALIDATION_PACKET / "difference_manifest.tsv"
    manifest_bytes = manifest_path.read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        _FROZEN_DIFFERENCE_MANIFEST_SHA256
    )
    with manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))

    assert len(rows) == 13
    assert all(row["old_value"] == row["new_value"] for row in rows)
    assert all(row["classification"].startswith("exact_zero_") for row in rows)
    assert {row["stage"] for row in rows} == {
        "trace",
        "hypothesis",
        "alignment",
        "backfill",
        "selection",
        "selected_result",
        "xic_results_csv",
        "matrix",
        "writer",
    }
    assert any(_FROZEN_LEGACY_CSV_SHA256 in row["old_value"] for row in rows)
    assert any(
        "6792d0fcd4d15b3cf23f2ce9e174fdbf96ed8399" in row["old_value"] for row in rows
    )
    inventory = (_VALIDATION_PACKET / "artifact_inventory.tsv").read_text(
        encoding="utf-8"
    )
    assert _FROZEN_DIFFERENCE_MANIFEST_SHA256 in inventory
    assert _FROZEN_LEGACY_CSV_SHA256 in inventory


def _method_request(
    tmp_path: Path,
) -> tuple[TargetedDiagnosticMethodRequest, Package2NoRawFixture]:
    fixture, inputs = _complete_inputs(tmp_path)
    target = fixture.target.definition.target
    request = TargetedDiagnosticMethodRequest(
        alignment_inputs=inputs,
        backfill_sources={
            "eligible-backfill": FrozenNoRawBackfillSource(
                center=8.01,
                height=600.0,
            ),
            "true-missing": FrozenNoRawBackfillSource(center=8.01, height=0.0),
        },
        alignment_config=AlignmentConfig(),
        peak_config=_peak_config(tmp_path),
        targets=(target,),
        processing_attempt=fixture.attempt,
        authoritative_legacy_output=_legacy_output(target),
    )
    return request, fixture


def _trace_oracle(request: TargetedDiagnosticMethodRequest):
    return {
        item.sample.metadata.raw_stem: (
            item.candidate_set.candidate_hypotheses[0]
            .peak_observation_refs[0]
            .content.peak_observation_content_id,
            item.candidate_set.candidate_hypotheses[0]
            .peak_observation_refs[0]
            .generation_provenance.full_trace_sha256,
            item.candidate_set.candidate_hypotheses[0]
            .peak_observation_refs[0]
            .generation_provenance.selection_trace_sha256,
        )
        for item in request.alignment_inputs
        if item.sample.metadata.raw_stem in _FROZEN_TRACE_ORACLE
    }


def _hypothesis_oracle(request: TargetedDiagnosticMethodRequest):
    return {
        item.sample.metadata.raw_stem: (
            item.candidate_set.candidate_hypotheses[0].hypothesis_id
        )
        for item in request.alignment_inputs
        if item.sample.metadata.raw_stem in _FROZEN_HYPOTHESIS_ORACLE
    }


def _legacy_output(_target: Target) -> RunOutput:
    return RunOutput(
        file_results=[
            _missing_file("ambiguous", confidence="AMBIGUOUS"),
            _selected_file(
                "eligible-backfill",
                rt=8.01,
                area=2835.100474933266,
                intensity=600.0,
                start=7.93,
                end=8.09,
                width=0.16000000000000014,
                confidence="BACKFILLED",
            ),
            FileResult(
                sample_name="failed",
                results={},
                error="discovery_worker_failed",
            ),
            _selected_file(
                "observed-a",
                rt=8.0,
                area=3300.0,
                intensity=500.0,
                start=7.9,
                end=8.1,
                width=0.2,
                confidence="HIGH",
            ),
            _selected_file(
                "observed-b",
                rt=8.02,
                area=3300.0,
                intensity=500.0,
                start=7.92,
                end=8.12,
                width=0.2,
                confidence="HIGH",
            ),
            _missing_file("true-missing", confidence="NOT_DETECTED"),
        ],
        diagnostics=[],
    )


def _selected_file(
    sample_stem: str,
    *,
    rt: float,
    area: float,
    intensity: float,
    start: float,
    end: float,
    width: float,
    confidence: str,
) -> FileResult:
    result = _FrozenLegacyResult(
        reported_rt=rt,
        reported_peak_area=area,
        reported_peak_intensity=intensity,
        reported_peak_start=start,
        reported_peak_end=end,
        reported_peak_width=width,
        confidence=confidence,
        reason="legacy authoritative selected result",
        targeted_product_projection=_projection(counted=True),
    )
    return FileResult(sample_name=sample_stem, results={"Analyte": result})


def _missing_file(sample_stem: str, *, confidence: str) -> FileResult:
    return FileResult(
        sample_name=sample_stem,
        results={
            "Analyte": _FrozenLegacyResult(
                reported_rt=None,
                reported_peak_area=None,
                reported_peak_intensity=None,
                reported_peak_start=None,
                reported_peak_end=None,
                reported_peak_width=None,
                confidence=confidence,
                reason="legacy authoritative non-selected result",
                targeted_product_projection=_projection(counted=False),
            )
        },
    )


def _projection(*, counted: bool) -> TargetedProductProjection:
    return TargetedProductProjection(
        product_state="detected_clean" if counted else "not_counted",
        counted_detection=counted,
        review_state="none" if counted else "review_required",
        projection_reason="legacy authoritative projection",
    )


def _finalization(
    fixture: Package2NoRawFixture,
    *,
    owner: str,
    outcome: WorkUnitTerminalOutcome,
    payload: bytes,
):
    work_unit_key = f"package2:{owner}"
    history = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=fixture.attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    runtime = start_attempt_runtime(
        preflight_required_work_manifest(
            history,
            attempt=fixture.attempt,
            work_unit_keys=(work_unit_key,),
            owner_completeness_attestation="package2-targeted-method:v1",
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
    artifact = None
    if outcome is WorkUnitTerminalOutcome.SUCCEEDED:
        if owner == "targeted":
            artifact = mint_targeted_process_result_ref(
                processing_attempt=fixture.attempt,
                work_unit_key=work_unit_key,
                owner_result_payload=payload,
            ).artifact
        else:
            artifact = mint_alignment_process_result_artifact(
                processing_attempt=fixture.attempt,
                work_unit_key=work_unit_key,
                owner_result_payload=payload,
            )
    settlement = mint_work_unit_settlement(
        attempt=fixture.attempt,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=artifact,
        failure_detail=(
            None
            if outcome is WorkUnitTerminalOutcome.SUCCEEDED
            else WorkUnitFailureDetail(
                category=(
                    WorkUnitFailureCategory.CANCELLATION
                    if outcome is WorkUnitTerminalOutcome.CANCELLED
                    else WorkUnitFailureCategory.EXECUTION_ERROR
                ),
                stage=f"{owner}-process-worker",
                source_reference=f"no-raw:{work_unit_key}",
                message=f"simulated {outcome.value}",
            )
        ),
    )
    receipt = execute_runtime_attempt(
        runtime,
        (),
        worker_batch_provider=lambda _aggregate: RuntimeWorkerBatchReceipt(
            settlements=(settlement,),
            artifact_registrations=(
                ()
                if artifact is None
                else (
                    RuntimeArtifactRegistrationReceipt(
                        work_unit_key=work_unit_key,
                        artifact=artifact,
                    ),
                )
            ),
        ),
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        persister=lambda proposed: proposed,
    )
    finalization = receipt.supervisor
    expected = {
        WorkUnitTerminalOutcome.SUCCEEDED: AttemptTerminalOutcome.SUCCEEDED,
        WorkUnitTerminalOutcome.FAILED: AttemptTerminalOutcome.FAILED,
        WorkUnitTerminalOutcome.CANCELLED: AttemptTerminalOutcome.CANCELLED,
    }[outcome]
    assert finalization.terminal_outcome is expected
    return finalization


def _runtime(fixture: Package2NoRawFixture, work_unit_key: str):
    history = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=fixture.attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            history,
            attempt=fixture.attempt,
            work_unit_keys=(work_unit_key,),
            owner_completeness_attestation="package2-targeted-method:v1",
        )
    )


def _reservation(
    fixture: Package2NoRawFixture,
    job: _MethodProcessJob,
) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=fixture.attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key=job.resource_key,
        worker_key=f"worker:{job.work_unit_key}",
        source_reference=f"synthetic-no-raw:{job.work_unit_key}",
        compatibility_token="package2-targeted-method:v1",
    )


def _legacy_runner_must_not_be_called(*_args, **_kwargs):
    raise AssertionError("diagnostic method must not invoke the legacy runner")
