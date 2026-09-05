from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.processing_identity_no_raw_fixture import (
    build_targeted_processing_identity_raw_fixture,
)
from tests.runtime_telemetry_fixtures import (
    build_runtime_telemetry_samples_from_coordinator,
)
from tests.test_targeted_real_data_adapter import (
    _current_candidates,
    _FakeRaw,
    _FakeThermoRaw,
    _peak_config,
    _settings,
    _working_imports,
)
from xic_extractor.execution_runtime import (
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeArtifactDisposition,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeCancellationRequest,
    start_runtime_attempt,
)
from xic_extractor.extraction import targeted_real_data_adapter as adapter_module
from xic_extractor.extraction import targeted_real_data_runtime as runtime_module
from xic_extractor.extraction.targeted_inputs import (
    targeted_real_data_candidate_universe_payload,
    validate_targeted_real_data_candidate_universe_payload,
)
from xic_extractor.extraction.targeted_real_data_runtime import (
    TargetedRealDataRuntimeError,
    TargetedRealDataRuntimeJob,
    execute_current_discovery_sample_target_runtime,
)
from xic_extractor.peak_detection import capture as capture_owner
from xic_extractor.processing_identity import IdentityContractError
from xic_extractor.raw_reader import (
    EXPECTED_THERMO_DLLS,
    open_raw,
    reset_reader_state,
)


def test_real_data_runtime_accounts_successful_raw_and_xic_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_variants: list[str] = []
    real_capture = capture_owner.capture_peak_hypotheses

    def _trace_capture(**kwargs):
        capture_variants.append(kwargs["scope"].variant)
        return real_capture(**kwargs)

    monkeypatch.setattr(capture_owner, "capture_peak_hypotheses", _trace_capture)
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    fixture = _bound_fixture(
        tmp_path,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        fixture=fixture,
        sample_stem="S1",
        source_raw=source_raw,
        tmp_path=tmp_path,
    )
    job = _job(
        fixture,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )

    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.adapter_result is not None
    assert capture_variants == ["targeted"]
    assert result.adapter_result.alignment_input.selection.status == "selected"
    assert result.lifecycle.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    )
    assert tuple(
        key
        for key, _acquisition in (
            result.lifecycle.settlement.successful_acquisition_batches
        )
    ) == (job.work_unit_key,)
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["raw_open_count"] == 1
    assert metrics["logical_xic_request_count"] == 1
    assert metrics["xic_batch_count"] == 1
    assert metrics["adapter_chromatogram_call_count"] == 1
    assert metrics["trace_point_count"] > 0
    assert vendor_raw.disposed is True
    assert result.result_ref is not None
    candidate_set = result.adapter_result.alignment_input.candidate_set
    selection = result.adapter_result.alignment_input.selection
    expected_artifact_ids = tuple(
        sorted(
            artifact.evidence_artifact_id
            for artifact in (
                *candidate_set.lineage_artifacts,
                selection.artifact,
                result.result_ref.artifact,
            )
        )
    )
    registered_artifact_ids = tuple(
        sorted(
            record.artifact.evidence_artifact_id
            for record in result.finalization.coordinator.state.artifacts
        )
    )
    assert registered_artifact_ids == expected_artifact_ids
    assert (
        tuple(
            sorted(
                artifact_id
                for artifact_id, disposition in (
                    result.finalization.supervisor.artifact_dispositions
                )
                if disposition is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            )
        )
        == expected_artifact_ids
    )


def test_real_data_runtime_terminalizes_adapter_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )[0]
    assert candidate.ms1_area is not None
    changed_candidates = (replace(candidate, ms1_area=candidate.ms1_area * 1.1),)
    fixture = _bound_fixture(
        tmp_path,
        candidates=changed_candidates,
        settings=settings,
        peak_config=peak_config,
    )
    vendor_raw = _install_runtime_raw(
        monkeypatch,
        fixture=fixture,
        sample_stem="S1",
        source_raw=source_raw,
        tmp_path=tmp_path,
    )
    job = _job(
        fixture,
        candidates=changed_candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )

    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.adapter_result is None
    assert result.lifecycle.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    settlement = result.lifecycle.settlement.runtime.settlements[0]
    assert settlement.outcome is WorkUnitTerminalOutcome.FAILED
    assert settlement.failure_detail is not None
    assert settlement.failure_detail.category is WorkUnitFailureCategory.EXECUTION_ERROR
    assert settlement.failure_detail.stage == "targeted-real-data-adapter"
    assert result.lifecycle.settlement.successful_acquisition_batches == ()
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["raw_open_count"] == 1
    assert metrics["logical_xic_request_count"] == 1
    assert metrics["xic_batch_count"] == 1
    assert metrics["adapter_chromatogram_call_count"] == 1
    assert metrics["trace_point_count"] > 0
    assert vendor_raw.disposed is True


def test_real_data_runtime_quarantines_artifacts_after_projection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    fixture = _bound_fixture(
        tmp_path,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    _install_runtime_raw(
        monkeypatch,
        fixture=fixture,
        sample_stem="S1",
        source_raw=source_raw,
        tmp_path=tmp_path,
    )

    def _projection_failure(**_kwargs: object):
        raise RuntimeError("projection failed after evidence mint")

    monkeypatch.setattr(
        adapter_module,
        "mint_targeted_cid_nl_projection_ref",
        _projection_failure,
    )
    job = _job(
        fixture,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )

    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.adapter_result is None
    assert result.finalization.supervisor.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert result.finalization.coordinator.state.artifacts
    assert all(
        disposition is RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
        for _artifact_id, disposition in (
            result.finalization.supervisor.artifact_dispositions
        )
    )
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["logical_xic_request_count"] == 1
    assert metrics["trace_point_count"] > 0


def test_real_data_runtime_quarantines_complete_result_after_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    fixture = _bound_fixture(
        tmp_path,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    _install_runtime_raw(
        monkeypatch,
        fixture=fixture,
        sample_stem="S1",
        source_raw=source_raw,
        tmp_path=tmp_path,
        dispose_error=RuntimeError("dispose failed"),
    )
    job = _job(
        fixture,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )

    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.adapter_result is None
    resource = result.finalization.coordinator.state.resources[0]
    assert resource.open_outcome is RuntimeResourceOpenOutcome.OPENED
    assert resource.cleanup_outcome is RuntimeResourceCleanupOutcome.FAILED
    assert result.finalization.coordinator.state.artifacts
    assert all(
        disposition is RuntimeArtifactDisposition.QUARANTINED_DIAGNOSTIC
        for _artifact_id, disposition in (
            result.finalization.supervisor.artifact_dispositions
        )
    )


def test_real_data_runtime_preflight_failure_skips_raw_and_finalizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
        current_discovery_settings=_settings(),
    )
    job = _job(
        fixture,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        tmp_path=tmp_path,
    )
    runtime = start_runtime_attempt(
        attempt=fixture.owners.attempt,
        work_unit_keys=(job.work_unit_key,),
        owner_completeness_attestation=None,
    )

    def _unexpected_open(*_args: object, **_kwargs: object):
        raise AssertionError("preflight failure must not open RAW")

    monkeypatch.setattr(runtime_module, "open_raw", _unexpected_open)
    result = execute_current_discovery_sample_target_runtime(
        runtime,
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.finalization.supervisor.terminal_outcome is (
        AttemptTerminalOutcome.FAILED
    )
    assert result.finalization.coordinator.state.resources == ()
    assert result.lifecycle.settlement.runtime.manifest_preflight_failed is True
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["raw_open_count"] == 0
    assert metrics["logical_xic_request_count"] == 0


def test_real_data_runtime_cancellation_skips_raw_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    fixture = _bound_fixture(
        tmp_path,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    job = _job(
        fixture,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )
    runtime = start_runtime_attempt(
        attempt=fixture.owners.attempt,
        work_unit_keys=(job.work_unit_key,),
        owner_completeness_attestation="targeted-real-data-runtime-test:v1",
        cancellation_request=RuntimeCancellationRequest(attempt=fixture.owners.attempt),
    )

    def _unexpected_open(*_args: object, **_kwargs: object):
        raise AssertionError("accepted cancellation must not open RAW")

    monkeypatch.setattr(runtime_module, "open_raw", _unexpected_open)
    result = execute_current_discovery_sample_target_runtime(
        runtime,
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.finalization.supervisor.terminal_outcome is (
        AttemptTerminalOutcome.CANCELLED
    )
    assert result.lifecycle.settlement.runtime.settlements[0].outcome is (
        WorkUnitTerminalOutcome.CANCELLED
    )
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["raw_open_count"] == 0
    assert metrics["logical_xic_request_count"] == 0


def test_real_data_runtime_result_rejects_unbound_acquisition_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_raw = _FakeRaw((8.0,))
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidates = _current_candidates(
        source_raw,
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )
    fixture = _bound_fixture(
        tmp_path,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
    )
    _install_runtime_raw(
        monkeypatch,
        fixture=fixture,
        sample_stem="S1",
        source_raw=source_raw,
        tmp_path=tmp_path,
    )
    job = _job(
        fixture,
        candidates=candidates,
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )
    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="exact adapter acquisition",
    ):
        replace(result, acquisition=None)


def test_real_data_runtime_keeps_empty_universe_inside_atomic_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=_peak_config(tmp_path),
        current_discovery_settings=_settings(),
    )
    job = _job(
        fixture,
        candidates=(),
        settings=_settings(),
        peak_config=_peak_config(tmp_path),
        tmp_path=tmp_path,
    )

    def _unexpected_open(*_args: object, **_kwargs: object):
        raise AssertionError("empty candidate universe must not open RAW")

    monkeypatch.setattr(runtime_module, "open_raw", _unexpected_open)
    result = execute_current_discovery_sample_target_runtime(
        _runtime(fixture, job.work_unit_key),
        job,
        telemetry_provider=build_runtime_telemetry_samples_from_coordinator,
        recovery_marker=_marker(tmp_path),
    )

    assert result.lifecycle.finalization_record.terminal_outcome is (
        AttemptTerminalOutcome.SUCCEEDED
    ), result.lifecycle.settlement.runtime.settlements[0].failure_detail
    assert result.adapter_result is not None
    assert result.adapter_result.alignment_input.selection.status == "no_candidate"
    assert result.lifecycle.settlement.successful_acquisition_batches == ()
    metrics = _attempt_metrics(result.lifecycle, fixture.owners.attempt)
    assert metrics["raw_open_count"] == 0
    assert metrics["logical_xic_request_count"] == 0
    assert metrics["xic_batch_count"] == 0
    assert metrics["adapter_chromatogram_call_count"] == 0
    assert metrics["trace_point_count"] == 0


def test_real_data_adapter_exposes_only_atomic_runtime_entry() -> None:
    import xic_extractor.extraction.targeted_real_data_adapter as adapter_module

    assert "adapt_current_discovery_sample_target" not in adapter_module.__all__
    assert runtime_module.__all__ == (
        "TargetedRealDataRuntimeApplicationResult",
        "TargetedRealDataRuntimeError",
        "TargetedRealDataRuntimeJob",
        "execute_current_discovery_sample_target_runtime",
    )


def test_real_data_runtime_job_rejects_unbound_peak_config(
    tmp_path: Path,
) -> None:
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=peak_config,
        current_discovery_settings=settings,
    )

    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="peak_config scientific settings do not match processing request",
    ):
        _job(
            fixture,
            candidates=(),
            settings=settings,
            peak_config=replace(peak_config, peak_rel_height=0.5),
            tmp_path=tmp_path,
        )


def test_real_data_runtime_job_rejects_unbound_discovery_settings(
    tmp_path: Path,
) -> None:
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    fixture = build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=peak_config,
        current_discovery_settings=settings,
    )
    changed_settings = replace(
        settings,
        neutral_loss_profile=None,
        product_search_ppm=settings.product_search_ppm + 1.0,
    )

    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="Discovery scientific settings do not match processing request",
    ):
        _job(
            fixture,
            candidates=(),
            settings=changed_settings,
            peak_config=peak_config,
            tmp_path=tmp_path,
        )


def test_real_data_runtime_job_binds_complete_candidate_universe(
    tmp_path: Path,
) -> None:
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        _FakeRaw((8.0,)),
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )[0]
    second = replace(candidate, candidate_id=f"{candidate.candidate_id}:second")
    fixture = _bound_fixture(
        tmp_path,
        candidates=(candidate, second),
        settings=settings,
        peak_config=peak_config,
    )

    reordered = _job(
        fixture,
        candidates=(second, candidate),
        settings=settings,
        peak_config=peak_config,
        tmp_path=tmp_path,
    )
    assert reordered.candidates == (second, candidate)

    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="candidate universe does not match processing request",
    ):
        _job(
            fixture,
            candidates=(candidate,),
            settings=settings,
            peak_config=peak_config,
            tmp_path=tmp_path,
        )

    extra = replace(second, candidate_id=f"{second.candidate_id}:extra")
    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="candidate universe does not match processing request",
    ):
        _job(
            fixture,
            candidates=(candidate, second, extra),
            settings=settings,
            peak_config=peak_config,
            tmp_path=tmp_path,
        )

    assert candidate.ms1_area is not None
    changed = replace(candidate, ms1_area=candidate.ms1_area * 1.01)
    with pytest.raises(
        TargetedRealDataRuntimeError,
        match="candidate universe does not match processing request",
    ):
        _job(
            fixture,
            candidates=(changed, second),
            settings=settings,
            peak_config=peak_config,
            tmp_path=tmp_path,
        )


def test_candidate_universe_replay_rejects_wrong_schema_partial_and_duplicate_rows(
    tmp_path: Path,
) -> None:
    settings = _settings()
    peak_config = _peak_config(tmp_path)
    candidate = _current_candidates(
        _FakeRaw((8.0,)),
        raw_file=tmp_path / "S1.synthetic",
        settings=settings,
        peak_config=peak_config,
    )[0]
    payload = targeted_real_data_candidate_universe_payload(
        {"S1": (candidate,)},
        expected_sample_stems=("S1",),
    )
    samples = payload["samples"]
    assert isinstance(samples, list)
    entry = samples[0]
    assert isinstance(entry, dict)
    malformed_payloads = (
        {**payload, "schema_version": "stale"},
        {**payload, "samples": []},
        {**payload, "samples": [entry, entry]},
        {
            **payload,
            "samples": [{**entry, "candidate_count": 2}],
        },
    )

    for malformed in malformed_payloads:
        with pytest.raises(IdentityContractError, match="candidate universe"):
            validate_targeted_real_data_candidate_universe_payload(
                malformed,
                expected_sample_stems=("S1",),
            )


def _job(
    fixture,
    *,
    candidates,
    settings,
    peak_config,
    tmp_path: Path,
) -> TargetedRealDataRuntimeJob:
    return TargetedRealDataRuntimeJob(
        processing_owners=fixture.owners,
        sample=fixture.sample("S1"),
        target=fixture.target,
        candidates=tuple(candidates),
        settings=settings,
        peak_config=peak_config,
        raw_path=tmp_path / "S1.synthetic",
        dll_dir=tmp_path,
        work_unit_key="targeted-real-data:S1:Analyte",
    )


def _bound_fixture(
    tmp_path: Path,
    *,
    candidates,
    settings,
    peak_config,
):
    return build_targeted_processing_identity_raw_fixture(
        tmp_path,
        sample_stems=("S1",),
        peak_config=peak_config,
        current_discovery_settings=settings,
        current_discovery_candidates_by_sample={"S1": tuple(candidates)},
    )


def _runtime(fixture, work_unit_key: str):
    return start_runtime_attempt(
        attempt=fixture.owners.attempt,
        work_unit_keys=(work_unit_key,),
        owner_completeness_attestation="targeted-real-data-runtime-test:v1",
    )


def _marker(tmp_path: Path) -> OwnerPathRuntimeMarker:
    return OwnerPathRuntimeMarker(
        tmp_path / "targeted-real-data.runtime.json",
        "targeted-real-data-runtime:test",
    )


def _install_runtime_raw(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fixture,
    sample_stem: str,
    source_raw: _FakeRaw,
    tmp_path: Path,
    dispose_error: Exception | None = None,
) -> _FakeThermoRaw:
    reset_reader_state()
    for filename in EXPECTED_THERMO_DLLS:
        (tmp_path / filename).write_bytes(b"dll")
    vendor_raw = _FakeThermoRaw(source_raw)
    if dispose_error is not None:

        def _failed_dispose() -> None:
            vendor_raw.disposed = True
            raise dispose_error

        monkeypatch.setattr(vendor_raw, "Dispose", _failed_dispose)

    def _open_runtime_raw(
        path: Path,
        dll_dir: Path,
        *,
        expected_acquisition_source,
    ):
        return open_raw(
            path,
            dll_dir,
            expected_acquisition_source=expected_acquisition_source,
            _import_module=_working_imports,
            _thermo=SimpleNamespace(
                raw_file_reader_adapter=SimpleNamespace(
                    FileFactory=lambda _path: vendor_raw
                ),
                device_ms="MS",
                mass_range_factory=lambda low, high: (low, high),
                trace_settings_factory=lambda *_args: SimpleNamespace(
                    MassRanges=None,
                    Filter=None,
                ),
                mass_range_trace_type="MassRange",
                ms2_order="Ms2",
            ),
        )

    monkeypatch.setattr(runtime_module, "open_raw", _open_runtime_raw)
    return vendor_raw


def _attempt_metrics(lifecycle, attempt) -> dict[str, int | float | None]:
    return {
        metric.name: metric.value
        for metric in lifecycle.finalization.observability_ledger.metrics
        if metric.scope_key == attempt.processing_attempt_id
    }
