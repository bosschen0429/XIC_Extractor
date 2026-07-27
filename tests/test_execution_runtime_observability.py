from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.execution_runtime_observability import (
    REQUIRED_RUNTIME_METRIC_NAMES,
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityContractError,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
)
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    build_acquisition_source_identity,
    build_processing_request_identity,
    issue_processing_attempt,
)


def test_runtime_observability_closes_work_offsets_and_metric_availability(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    ledger = RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=("sample-a:target-a",),
        events=_events(attempt),
        metrics=_metrics(attempt.processing_attempt_id),
    )

    assert ledger.events[0].monotonic_offset_ns == 0
    assert ledger.events[-1].kind == "terminal_outcome"
    assert {metric.name for metric in ledger.metrics} == set(
        REQUIRED_RUNTIME_METRIC_NAMES
    )
    assert next(
        metric for metric in ledger.metrics if metric.name == "raw_open_count"
    ).value == 0


def test_runtime_observability_rejects_nonmonotonic_offsets(tmp_path: Path) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    events[3] = replace(events[3], monotonic_offset_ns=1)

    with pytest.raises(RuntimeObservabilityContractError, match="monotonic"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=tuple(events),
            metrics=_metrics(attempt.processing_attempt_id),
        )


def test_runtime_observability_rejects_missing_metric_availability(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)

    with pytest.raises(RuntimeObservabilityContractError, match="missing"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=_events(attempt),
            metrics=_metrics(attempt.processing_attempt_id)[:-1],
        )


def _events(attempt):
    kinds = (
        ("attempt_queued", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest"),
        ("attempt_started", RuntimeObservationSubject.ATTEMPT, "attempt"),
        (
            "work_unit_submitted",
            RuntimeObservationSubject.WORK_UNIT,
            "sample-a:target-a",
        ),
        ("work_unit_started", RuntimeObservationSubject.WORK_UNIT, "sample-a:target-a"),
        ("work_unit_settled", RuntimeObservationSubject.WORK_UNIT, "sample-a:target-a"),
        ("terminal_proposal", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("terminal_acknowledged", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("terminal_outcome", RuntimeObservationSubject.ATTEMPT, "attempt"),
    )
    return tuple(
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
            kind=kind,
            subject=subject,
            subject_key=subject_key,
        )
        for index, (kind, subject, subject_key) in enumerate(kinds, start=1)
    )


def _metrics(attempt_id: str) -> tuple[RuntimeMetricRecord, ...]:
    count_metrics = {
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "artifact_read_count",
        "artifact_write_count",
    }
    measured = {
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "coordinator_work_seconds",
        "attempt_wall_seconds",
        "stage_or_unit_elapsed_seconds",
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
    }
    return tuple(
        RuntimeMetricRecord(
            name=name,
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit=(
                "count"
                if name in count_metrics
                else "bytes"
                if name.endswith("bytes")
                else "seconds"
            ),
            aggregation="sum" if name != "attempt_wall_seconds" else "last",
            measurement_source="package0-no-raw-fixture",
            availability=(
                RuntimeMetricAvailability.MEASURED
                if name in measured
                else RuntimeMetricAvailability.UNSUPPORTED
            ),
            value=0 if name in measured else None,
            reason=None if name in measured else "no child process in pure fixture",
        )
        for name in REQUIRED_RUNTIME_METRIC_NAMES
    )


def _attempt(tmp_path: Path):
    source_path = tmp_path / "source.raw"
    source_path.write_bytes(b"no-raw-contract-fixture")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
    request = build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample-a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="package0_fixture_v1",
        processing_algorithm_version="package0_fixture_v1",
        processing_contract_version="package0_fixture_v1",
        scientific_inputs={"neutral_loss_da": 116.0474},
    )
    return issue_processing_attempt(request)
