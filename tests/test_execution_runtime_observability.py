from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.runtime_observability_fixtures import (
    build_runtime_observability_ledger,
)
from xic_extractor.execution import _observability as observability_owner
from xic_extractor.execution._finalization import RuntimeResourceKind
from xic_extractor.execution._observability import (
    REQUIRED_RUNTIME_METRIC_NAMES,
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityContractError,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
    validate_runtime_acquisition_metrics,
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
    assert (
        next(
            metric for metric in ledger.metrics if metric.name == "raw_open_count"
        ).value
        == 0
    )


def test_runtime_event_indexes_preserve_scan_bytes_and_rejection_results(
    tmp_path: Path, monkeypatch
) -> None:
    ledger = build_runtime_observability_ledger(
        _attempt(tmp_path),
        work_unit_keys=("shared", "other"),
        worker_keys=("shared", "other"),
        resource_lifecycles=tuple(
            (key, RuntimeResourceKind.ACQUISITION_SESSION, opened, cleaned)
            for key, opened, cleaned in (
                ("shared", "resource_opened", "resource_cleanup_succeeded"),
                ("other", "resource_not_opened", "resource_cleanup_succeeded"),
            )
        ),
    )

    def scanned_positions(events, subject):
        return SimpleNamespace(
            get=lambda key, default=(): tuple(
                index
                for index, event in enumerate(events)
                if event.subject is subject and (event.subject_key, event.kind) == key
            )
        )

    def scanned_offsets(events):
        return SimpleNamespace(
            get=lambda key, default=(): tuple(
                event.monotonic_offset_ns
                for event in events
                if (event.kind, event.subject_key) == key
            )
        )

    def observed(events):
        try:
            value = RuntimeObservabilityLedger(
                attempt=ledger.attempt,
                work_unit_keys=ledger.work_unit_keys,
                events=_resequence(events),
                metrics=ledger.metrics,
            )
        except RuntimeObservabilityContractError as exc:
            return type(exc), str(exc)
        return asdict(value)

    variants = [ledger.events]
    for kind in (
        "work_unit_started",
        "worker_started",
        "resource_opened",
    ):
        index = next(i for i, event in enumerate(ledger.events) if event.kind == kind)
        variants.append((*ledger.events[:index], *ledger.events[index + 1 :]))
        variants.append(
            (
                *ledger.events[:index],
                *ledger.events[index : index + 1],
                *ledger.events[index:],
            )
        )
    reversed_work = list(ledger.events)
    first = _event_index(ledger.events, "work_unit_submitted")
    reversed_work[first], reversed_work[first + 1] = (
        reversed_work[first + 1],
        reversed_work[first],
    )
    variants.append(tuple(reversed_work))
    for events in variants:
        actual = observed(events)
        with monkeypatch.context() as patch:
            patch.setattr(
                observability_owner, "_index_event_positions", scanned_positions
            )
            patch.setattr(observability_owner, "_index_event_offsets", scanned_offsets)
            expected = observed(events)
        assert actual == expected


def test_runtime_acquisition_metrics_treat_empty_projection_as_exact_zero(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    zero_ledger = build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("sample-a:target-a",),
        worker_keys=(),
    )
    validate_runtime_acquisition_metrics(zero_ledger, ())

    nonzero_ledger = build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("sample-a:target-a",),
        worker_keys=(),
        logical_xic_request_count=1,
        xic_batch_count=1,
        adapter_chromatogram_call_count=1,
        trace_point_count=10,
    )
    with pytest.raises(
        RuntimeObservabilityContractError,
        match="logical_xic_request_count",
    ):
        validate_runtime_acquisition_metrics(nonzero_ledger, ())


def test_runtime_observability_rejects_nonmonotonic_offsets(tmp_path: Path) -> None:
    from tests.runtime_telemetry_fixtures import (
        build_runtime_telemetry_samples_from_coordinator,
    )
    from xic_extractor.execution._finalization import (
        RuntimeFinalizationContractError,
        RuntimeResourceCleanupOutcome,
        RuntimeResourceKind,
        RuntimeResourceOpenOutcome,
        RuntimeResourceReservation,
    )
    from xic_extractor.execution.attempt import (
        RuntimeResourceSettlementReceipt,
        RuntimeWorkerBatchReceipt,
        WorkUnitTerminalOutcome,
        _apply_runtime_worker_batch,
        _finalize_runtime_attempt,
        _prepare_runtime_attempt,
        mint_work_unit_settlement,
        start_runtime_attempt,
    )

    attempt = _attempt(tmp_path)
    runtime = start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=("raw:a",),
        owner_completeness_attestation="runtime-owner-test",
    )
    aggregate = _apply_runtime_worker_batch(
        _prepare_runtime_attempt(
            runtime,
            (
                RuntimeResourceReservation(
                    attempt=attempt,
                    resource_key="session:a",
                    kind=RuntimeResourceKind.ACQUISITION_SESSION,
                    worker_key="worker:a",
                    source_reference="no-raw:sample:a",
                    compatibility_token="runtime-owner-test",
                ),
            ),
        ),
        RuntimeWorkerBatchReceipt(
            settlements=(
                mint_work_unit_settlement(
                    attempt=attempt,
                    work_unit_key="raw:a",
                    outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                ),
            ),
            resource_settlements=(
                RuntimeResourceSettlementReceipt(
                    attempt=attempt,
                    resource_key="session:a",
                    open_outcome=RuntimeResourceOpenOutcome.OPENED,
                    cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                ),
            ),
        ),
    )
    persisted = []

    def nonmonotonic_telemetry(coordinator):
        samples = build_runtime_telemetry_samples_from_coordinator(coordinator)
        offsets = list(samples.event_monotonic_offsets_ns)
        offsets[1] = 500
        offsets[2] = 200
        return type(samples)(
            event_monotonic_offsets_ns=tuple(offsets),
            artifact_io=samples.artifact_io,
            process_tree_peak_rss=samples.process_tree_peak_rss,
            worker_peak_rss=samples.worker_peak_rss,
        )

    with pytest.raises(RuntimeFinalizationContractError, match="monotonic"):
        _finalize_runtime_attempt(
            aggregate,
            telemetry_provider=nonmonotonic_telemetry,
            persister=lambda proposed: persisted.append(proposed),
        )
    assert persisted == []


def test_runtime_observability_preserves_one_rss_metric_per_observed_worker(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    work_submitted_index = next(
        index
        for index, event in enumerate(events)
        if event.kind == "work_unit_submitted"
    )
    events[work_submitted_index:work_submitted_index] = (
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=1,
            monotonic_offset_ns=1,
            kind="worker_started",
            subject=RuntimeObservationSubject.WORKER,
            subject_key="worker:2",
        ),
    )
    events = tuple(
        replace(event, sequence=index, monotonic_offset_ns=(index - 1) * 10)
        for index, event in enumerate(events, start=1)
    )
    metrics = list(_metrics(attempt.processing_attempt_id, events=events))
    worker_metric = next(
        metric for metric in metrics if metric.name == "worker_peak_rss_bytes"
    )
    metrics.remove(worker_metric)
    metrics.extend(
        (
            replace(
                worker_metric,
                scope_key="worker:1",
                availability=RuntimeMetricAvailability.MEASURED,
                value=1024,
                reason=None,
            ),
            replace(
                worker_metric,
                scope_key="worker:2",
                availability=RuntimeMetricAvailability.MEASURED,
                value=2048,
                reason=None,
            ),
        )
    )

    ledger = RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=("sample-a:target-a",),
        events=events,
        metrics=tuple(metrics),
    )

    assert {
        metric.scope_key
        for metric in ledger.metrics
        if metric.name == "worker_peak_rss_bytes"
    } == {"worker:1", "worker:2"}


def test_runtime_observability_represents_serial_mode_without_worker_process(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = tuple(
        replace(event, sequence=index, monotonic_offset_ns=(index - 1) * 10)
        for index, event in enumerate(
            (
                event
                for event in _events(attempt)
                if event.subject is not RuntimeObservationSubject.WORKER
            ),
            start=1,
        )
    )
    metrics = tuple(
        replace(
            metric,
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt.processing_attempt_id,
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            value=None,
            reason="not_applicable_serial",
        )
        if metric.name == "worker_peak_rss_bytes"
        else metric
        for metric in _metrics(attempt.processing_attempt_id, events=events)
    )

    ledger = RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=("sample-a:target-a",),
        events=events,
        metrics=metrics,
    )

    worker_rss = next(
        metric for metric in ledger.metrics if metric.name == "worker_peak_rss_bytes"
    )
    assert worker_rss.scope is RuntimeMetricScope.ATTEMPT
    assert worker_rss.reason == "not_applicable_serial"


def test_runtime_observability_accepts_preflight_failure_contract(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)

    ledger = RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=(),
        events=_preflight_events(attempt),
        metrics=_preflight_metrics(attempt.processing_attempt_id),
    )

    assert "attempt_started" not in {event.kind for event in ledger.events}


def _events(attempt):
    kinds = (
        ("attempt_queued", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest"),
        ("attempt_started", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("worker_started", RuntimeObservationSubject.WORKER, "worker:1"),
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


def _event_index(
    events: tuple[RuntimeObservationEvent, ...],
    kind: str,
) -> int:
    return next(index for index, event in enumerate(events) if event.kind == kind)


def _resequence(
    events: tuple[RuntimeObservationEvent, ...],
) -> tuple[RuntimeObservationEvent, ...]:
    return tuple(
        replace(
            event,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
        )
        for index, event in enumerate(events, start=1)
    )


def _metrics(
    attempt_id: str,
    *,
    events: tuple[RuntimeObservationEvent, ...] | None = None,
) -> tuple[RuntimeMetricRecord, ...]:
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
    attempt_shapes = {
        "coordinator_work_seconds": ("seconds", "last"),
        "attempt_wall_seconds": ("seconds", "last"),
        "worker_peak_rss_bytes": ("bytes", "max"),
        "process_tree_peak_rss_bytes": ("bytes", "max"),
    }
    event_sequence = events

    def offset(kind: str, subject_key: str = "attempt") -> int:
        if event_sequence is None:
            defaults = {
                ("attempt_queued", "attempt"): 0,
                ("attempt_started", "attempt"): 20,
                ("work_unit_started", "sample-a:target-a"): 50,
                ("work_unit_settled", "sample-a:target-a"): 60,
                ("terminal_proposal", "attempt"): 70,
                ("terminal_outcome", "attempt"): 90,
            }
            return defaults[(kind, subject_key)]
        return next(
            event.monotonic_offset_ns
            for event in event_sequence
            if event.kind == kind and event.subject_key == subject_key
        )

    def value(name: str) -> int | float:
        if name == "coordinator_work_seconds":
            return (
                offset("terminal_proposal") - offset("attempt_started")
            ) / 1_000_000_000
        if name == "attempt_wall_seconds":
            return (
                offset("terminal_outcome") - offset("attempt_queued")
            ) / 1_000_000_000
        if name == "stage_or_unit_elapsed_seconds":
            return (
                offset("work_unit_settled", "sample-a:target-a")
                - offset("work_unit_started", "sample-a:target-a")
            ) / 1_000_000_000
        return 0

    return tuple(
        RuntimeMetricRecord(
            name=name,
            scope=(
                RuntimeMetricScope.WORK_UNIT
                if name == "stage_or_unit_elapsed_seconds"
                else RuntimeMetricScope.WORKER
                if name == "worker_peak_rss_bytes"
                else RuntimeMetricScope.ATTEMPT
            ),
            scope_key=(
                "sample-a:target-a"
                if name == "stage_or_unit_elapsed_seconds"
                else "worker:1"
                if name == "worker_peak_rss_bytes"
                else attempt_id
            ),
            unit=(
                attempt_shapes[name][0]
                if name in attempt_shapes
                else "count"
                if name in count_metrics
                else "bytes"
                if name.endswith("bytes")
                else "seconds"
            ),
            aggregation=(attempt_shapes[name][1] if name in attempt_shapes else "sum"),
            measurement_source="package0-no-raw-fixture",
            availability=(
                RuntimeMetricAvailability.MEASURED
                if name in measured
                else RuntimeMetricAvailability.UNSUPPORTED
            ),
            value=value(name) if name in measured else None,
            reason=None if name in measured else "no child process in pure fixture",
        )
        for name in REQUIRED_RUNTIME_METRIC_NAMES
    )


def _preflight_events(attempt) -> tuple[RuntimeObservationEvent, ...]:
    kinds = (
        "attempt_queued",
        "manifest_preflight_failed",
        "terminal_proposal",
        "terminal_acknowledged",
        "terminal_outcome",
    )
    return tuple(
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
            kind=kind,
            subject=RuntimeObservationSubject.ATTEMPT,
            subject_key="attempt",
        )
        for index, kind in enumerate(kinds, start=1)
    )


def _preflight_metrics(attempt_id: str) -> tuple[RuntimeMetricRecord, ...]:
    unavailable = {
        "coordinator_work_seconds",
        "stage_or_unit_elapsed_seconds",
        "worker_peak_rss_bytes",
        "process_tree_peak_rss_bytes",
    }
    metrics = []
    for metric in _metrics(attempt_id):
        if metric.name in unavailable:
            metrics.append(
                replace(
                    metric,
                    scope=RuntimeMetricScope.ATTEMPT,
                    scope_key=attempt_id,
                    availability=RuntimeMetricAvailability.UNSUPPORTED,
                    value=None,
                    reason="not_started_preflight_failure",
                )
            )
        else:
            metrics.append(
                replace(
                    metric,
                    value=4e-8 if metric.name == "attempt_wall_seconds" else 0,
                )
            )
    return tuple(metrics)


def _attempt(tmp_path: Path):
    source_path = tmp_path / "source.raw"
    source_path.write_bytes(b"no-raw-contract-fixture")
    source = build_acquisition_source_identity(
        source_path,
        acquisition_format="fixture",
        observation_namespace="package0:no-raw",
    )
    build_processing_request_identity(
        acquisition_sources=(source,),
        acquisition_bindings=(
            AcquisitionInputBinding(
                acquisition_source_id=source.acquisition_source_id,
                adapter_input_key="sample-a",
                injection_order=1,
            ),
        ),
        scientific_input_schema_version="package0_fixture",
        processing_algorithm_version="package0_fixture",
        processing_contract_version="package0_fixture",
        scientific_inputs={"neutral_loss_da": 116.0474},
    )
    return issue_processing_attempt()
