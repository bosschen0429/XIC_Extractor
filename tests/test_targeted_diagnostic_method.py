from __future__ import annotations

import hashlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path

import pytest

from tests.processing_identity_no_raw_fixture import ProcessingIdentityNoRawFixture
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
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodParityError,
    TargetedDiagnosticMethodRequest,
    canonical_targeted_diagnostic_method_bytes,
    run_targeted_diagnostic_method,
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
from xic_extractor.peak_detection.targeted_product_projection import (
    TargetedProductProjection,
)

_FROZEN_TRACE_ORACLE = {
    "observed-a": (
        "xic.peak-observation-content:"
        "b8304b3f3ccf59ffcf8f23e6adbbe4c6581d1488f17858531b92480d4d9423c7",
        "5f1be40ecd3a2a0004133b8697f1bdd45a8f04af72a7c051e71442b4208890f1",
        "57b803307f7ecb009aa32b38fec8813aa3dbdefbe0454c1157b0926d41511827",
    ),
    "observed-b": (
        "xic.peak-observation-content:"
        "da504262c5a2637f5b410420297b376a47f01c6fd6d6fbf2b86526f91fa513e2",
        "62a28a29f663ce814e9b6b4a18ebd10ab25caf477de3e01a1bfa0ac2d6ee95b3",
        "803e310b99dbfcefe5680a155a9e08938a296c9700bb802394d7defef4be0ece",
    ),
}
_FROZEN_HYPOTHESIS_ORACLE = {
    # Support content now records loss error and the quadrature window in Da.
    "observed-a": (
        "xic.peak-hypothesis:"
        "9ca2cb8a6921a95340603d7a76c35502b2e2426b63fbff85e4931e5e49d72abc"
    ),
    "observed-b": (
        "xic.peak-hypothesis:"
        "c1f77e83b27aacd60fba2993ff97a2d907a1298bf05e483426cb7d8f5c205f28"
    ),
}


@dataclass(frozen=True)
class _FrozenLegacyResult:
    reported_rt: float | None
    reported_peak_area: float | None
    reported_peak_intensity: float | None
    reported_smoothed_peak_intensity: float | None
    reported_peak_start: float | None
    reported_peak_end: float | None
    reported_peak_width: float | None
    confidence: str
    reason: str
    targeted_product_projection: TargetedProductProjection
    nl_token: str | None = None


class _EqualLegacyFloat(float):
    """Equal comparator value whose type reveals accidental legacy propagation."""


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


def test_complete_method_projection_is_serial_reorder_exact(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
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
    assert {
        projection.sample_stem: projection.nl_token
        for projection in result.projections
        if projection.status == "observed"
    } == {"observed-a": "OK", "observed-b": "OK"}
    assert {
        projection.sample_stem: projection.reason
        for projection in result.projections
        if projection.status == "observed"
    } == {
        "observed-a": (
            "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl"
        ),
        "observed-b": (
            "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl"
        ),
    }
    backfilled = next(cell for cell in result.cells if cell.status == "backfilled")
    assert backfilled.selected_integration is not None
    assert (
        backfilled.selected_integration.rt_apex_min,
        backfilled.selected_integration.area_raw_counts_seconds,
        backfilled.selected_integration.area_ms1_morphology,
    ) == (8.01, 2951.9999999999864, 2835.100474933266)


def test_targeted_scheduler_and_alignment_process_runner_consume_method_receipt(
    tmp_path: Path,
) -> None:
    request, fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    payload = canonical_targeted_diagnostic_method_bytes(result)
    targeted_job = _MethodProcessJob(
        fixture.attempt,
        "processing_identity:targeted-method",
        "process:processing_identity:targeted-method",
        payload,
    )
    alignment_job = _MethodProcessJob(
        fixture.attempt,
        "processing_identity:alignment-method",
        "process:processing_identity:alignment-method",
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
            "processing-identity-targeted-method:test",
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
            "processing-identity-alignment-method:test",
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


def test_projection_values_come_from_successor_owner_not_equal_legacy_objects(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    legacy = request.authoritative_legacy_output
    files = list(legacy.file_results)
    index = next(
        index
        for index, file_result in enumerate(files)
        if file_result.sample_name == "observed-a"
    )
    file_result = files[index]
    original = file_result.results["Analyte"]
    assert isinstance(original, _FrozenLegacyResult)
    legacy_values = (
        original.reported_rt,
        original.reported_peak_area,
        original.reported_peak_intensity,
        original.reported_peak_start,
        original.reported_peak_end,
        original.reported_peak_width,
        original.reported_smoothed_peak_intensity,
    )
    assert all(value is not None for value in legacy_values)
    substituted = replace(
        original,
        reported_rt=_EqualLegacyFloat(original.reported_rt),
        reported_peak_area=_EqualLegacyFloat(original.reported_peak_area),
        reported_peak_intensity=_EqualLegacyFloat(original.reported_peak_intensity),
        reported_peak_start=_EqualLegacyFloat(original.reported_peak_start),
        reported_peak_end=_EqualLegacyFloat(original.reported_peak_end),
        reported_peak_width=_EqualLegacyFloat(original.reported_peak_width),
        reported_smoothed_peak_intensity=_EqualLegacyFloat(
            original.reported_smoothed_peak_intensity
        ),
    )
    files[index] = FileResult(
        sample_name=file_result.sample_name,
        results={"Analyte": substituted},
    )

    result = run_targeted_diagnostic_method(
        replace(request, authoritative_legacy_output=RunOutput(files, []))
    )
    projection = next(
        item for item in result.projections if item.sample_stem == "observed-a"
    )
    cell = next(item for item in result.cells if item.sample_stem == "observed-a")
    assert cell.selected_integration is not None
    assert projection.rt is cell.selected_integration.rt_apex_min
    assert projection.intensity_smoothed is cell.selected_integration.height_smoothed
    assert all(
        type(value) is float
        for value in (
            projection.rt,
            projection.area,
            projection.intensity,
            projection.intensity_smoothed,
            projection.peak_start,
            projection.peak_end,
            projection.peak_width,
        )
    )
    assert projection.counted_detection is True


def test_canonical_method_bytes_bind_full_backfill_scientific_content(
    tmp_path: Path,
) -> None:
    request, _fixture = _method_request(tmp_path)
    result = run_targeted_diagnostic_method(request)
    index = next(
        index for index, cell in enumerate(result.cells) if cell.status == "backfilled"
    )
    cell = result.cells[index]
    assert cell.selected_integration is not None
    assert cell.backfill_cell is not None
    changed_integration = replace(
        cell.selected_integration,
        area_raw_counts_seconds=(
            cell.selected_integration.area_raw_counts_seconds + 1.0
        ),
    )
    changed_cells = list(result.cells)
    changed_cells[index] = replace(cell, selected_integration=changed_integration)
    integration_tampered = replace(result, cells=tuple(changed_cells))
    assert canonical_targeted_diagnostic_method_bytes(integration_tampered) != (
        canonical_targeted_diagnostic_method_bytes(result)
    )

    changed_cells[index] = replace(
        cell,
        backfill_cell=replace(
            cell.backfill_cell,
            trace_quality="tampered-owner-backfill-trace-quality",
        ),
    )
    backfill_tampered = replace(result, cells=tuple(changed_cells))
    assert canonical_targeted_diagnostic_method_bytes(backfill_tampered) != (
        canonical_targeted_diagnostic_method_bytes(result)
    )


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
        run_targeted_diagnostic_method(replace(request, targets=(changed_target,)))


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


def _method_request(
    tmp_path: Path,
) -> tuple[TargetedDiagnosticMethodRequest, ProcessingIdentityNoRawFixture]:
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
        prior_current_delivery_id="legacy-seal:prior-current",
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
        reported_smoothed_peak_intensity=intensity,
        reported_peak_start=start,
        reported_peak_end=end,
        reported_peak_width=width,
        confidence=confidence,
        reason=(
            "decision: accepted; evidence: ms1_coherent; candidate_aligned_ms2_nl"
            if confidence == "HIGH"
            else "legacy authoritative selected result"
        ),
        targeted_product_projection=_projection(counted=True),
        nl_token="OK" if confidence == "HIGH" else None,
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
                reported_smoothed_peak_intensity=None,
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


def _runtime(fixture: ProcessingIdentityNoRawFixture, work_unit_key: str):
    return start_runtime_attempt(
        attempt=fixture.attempt,
        work_unit_keys=(work_unit_key,),
        owner_completeness_attestation="processing_identity-targeted-method",
    )


def _reservation(
    fixture: ProcessingIdentityNoRawFixture,
    job: _MethodProcessJob,
) -> RuntimeResourceReservation:
    return RuntimeResourceReservation(
        attempt=fixture.attempt,
        kind=RuntimeResourceKind.PROCESS_WORKER,
        resource_key=job.resource_key,
        worker_key=f"worker:{job.work_unit_key}",
        source_reference=f"synthetic-no-raw:{job.work_unit_key}",
        compatibility_token="processing_identity-targeted-method",
    )


def _legacy_runner_must_not_be_called(*_args, **_kwargs):
    raise AssertionError("diagnostic method must not invoke the legacy runner")
