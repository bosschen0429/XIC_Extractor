import math
from dataclasses import replace
from pathlib import Path

import pytest

from tests.runtime_observability_fixtures import (
    build_runtime_observability_ledger,
)
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
    assert (
        next(
            metric for metric in ledger.metrics if metric.name == "raw_open_count"
        ).value
        == 0
    )


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


def test_runtime_observability_requires_measured_metrics_after_attempt_start(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    unavailable = tuple(
        replace(
            metric,
            availability=RuntimeMetricAvailability.UNSUPPORTED,
            value=None,
            reason="not captured",
        )
        for metric in _metrics(attempt.processing_attempt_id)
    )

    with pytest.raises(RuntimeObservabilityContractError, match="measured"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=_events(attempt),
            metrics=unavailable,
        )


def test_runtime_observability_requires_complete_terminal_lifecycle(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    omitted = {
        "attempt_started",
        "terminal_proposal",
        "terminal_acknowledged",
    }
    events = tuple(
        replace(event, sequence=index, monotonic_offset_ns=(index - 1) * 10)
        for index, event in enumerate(
            (event for event in _events(attempt) if event.kind not in omitted),
            start=1,
        )
    )

    with pytest.raises(RuntimeObservabilityContractError, match="lifecycle"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=events,
            metrics=_metrics(attempt.processing_attempt_id),
        )


@pytest.mark.parametrize(
    "replacement",
    (
        {"scope": RuntimeMetricScope.STAGE},
        {"scope_key": "wrong-attempt"},
        {"unit": "seconds"},
        {"aggregation": "max"},
    ),
)
def test_runtime_observability_rejects_wrong_required_metric_shape(
    tmp_path: Path,
    replacement: dict[str, object],
) -> None:
    attempt = _attempt(tmp_path)
    metrics = list(_metrics(attempt.processing_attempt_id))
    metrics[0] = replace(metrics[0], **replacement)

    with pytest.raises(RuntimeObservabilityContractError, match="metric contract"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=_events(attempt),
            metrics=tuple(metrics),
        )


def test_runtime_observability_rejects_duplicate_or_unknown_metrics(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    metrics = _metrics(attempt.processing_attempt_id)
    duplicate = (*metrics, metrics[0])
    unknown = (*metrics, replace(metrics[0], name="unregistered_metric"))

    for invalid in (duplicate, unknown):
        with pytest.raises(RuntimeObservabilityContractError, match="metric"):
            RuntimeObservabilityLedger(
                attempt=attempt,
                work_unit_keys=("sample-a:target-a",),
                events=_events(attempt),
                metrics=invalid,
            )


def test_runtime_observability_preserves_one_rss_metric_per_observed_worker(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    proposal_index = next(
        index for index, event in enumerate(events) if event.kind == "terminal_proposal"
    )
    events[proposal_index:proposal_index] = (
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=1,
            monotonic_offset_ns=1,
            kind="worker_started",
            subject=RuntimeObservationSubject.WORKER,
            subject_key="worker:1",
        ),
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

    wrong_worker = tuple(
        replace(metric, scope_key="worker:other")
        if metric.name == "worker_peak_rss_bytes"
        and metric.scope_key == "worker:2"
        else metric
        for metric in ledger.metrics
    )
    with pytest.raises(RuntimeObservabilityContractError, match="worker"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=events,
            metrics=wrong_worker,
        )


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


def test_runtime_observability_rejects_elapsed_metrics_that_conflict_with_events(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    metrics = tuple(
        replace(metric, value=1.0)
        if metric.name == "coordinator_work_seconds"
        else metric
        for metric in _metrics(attempt.processing_attempt_id)
    )

    with pytest.raises(RuntimeObservabilityContractError, match="event offsets"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=_events(attempt),
            metrics=metrics,
        )


def test_runtime_observability_rejects_incomplete_or_malformed_resource_lifecycle(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    ledger = build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("sample-a:target-a",),
        worker_keys=("worker:1",),
        resource_lifecycles=(
            (
                "session:a",
                "resource_opened",
                "resource_cleanup_succeeded",
            ),
        ),
    )
    reserved = _event_index(ledger.events, "resource_reserved")
    opened = _event_index(ledger.events, "resource_opened")
    cleanup = _event_index(ledger.events, "resource_cleanup_succeeded")
    missing = (*ledger.events[:cleanup], *ledger.events[cleanup + 1 :])
    duplicate = (
        *ledger.events[: opened + 1],
        ledger.events[opened],
        *ledger.events[opened + 1 :],
    )
    reordered = list(ledger.events)
    reordered[reserved], reordered[opened] = reordered[opened], reordered[reserved]
    wrong_subject = list(ledger.events)
    wrong_subject[opened] = replace(
        wrong_subject[opened],
        subject=RuntimeObservationSubject.ATTEMPT,
    )

    for invalid, match in (
        (missing, "exactly one"),
        (duplicate, "exactly one"),
        (tuple(reordered), "lifecycle order"),
        (tuple(wrong_subject), "resource subject"),
    ):
        with pytest.raises(RuntimeObservabilityContractError, match=match):
            RuntimeObservabilityLedger(
                attempt=attempt,
                work_unit_keys=ledger.work_unit_keys,
                events=_resequence(invalid),
                metrics=ledger.metrics,
            )


def test_runtime_observability_rejects_incomplete_or_malformed_artifact_lifecycle(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    ledger = build_runtime_observability_ledger(
        attempt,
        work_unit_keys=("sample-a:target-a",),
        worker_keys=("worker:1",),
        artifact_dispositions=(("artifact:a", "artifact_offered_to_owner"),),
    )
    registered = _event_index(ledger.events, "artifact_registered")
    disposed = _event_index(ledger.events, "artifact_offered_to_owner")
    missing = (*ledger.events[:disposed], *ledger.events[disposed + 1 :])
    duplicate = (
        *ledger.events[: disposed + 1],
        ledger.events[disposed],
        *ledger.events[disposed + 1 :],
    )
    reordered = list(ledger.events)
    reordered[registered], reordered[disposed] = (
        reordered[disposed],
        reordered[registered],
    )
    wrong_subject = list(ledger.events)
    wrong_subject[registered] = replace(
        wrong_subject[registered],
        subject=RuntimeObservationSubject.ATTEMPT,
    )

    for invalid, match in (
        (missing, "exactly one"),
        (duplicate, "exactly one"),
        (tuple(reordered), "lifecycle order"),
        (tuple(wrong_subject), "artifact subject"),
    ):
        with pytest.raises(RuntimeObservabilityContractError, match=match):
            RuntimeObservabilityLedger(
                attempt=attempt,
                work_unit_keys=ledger.work_unit_keys,
                events=_resequence(invalid),
                metrics=ledger.metrics,
            )


@pytest.mark.parametrize(
    ("metric_name", "value"),
    (
        ("attempt_wall_seconds", math.nan),
        ("coordinator_work_seconds", math.inf),
        ("raw_open_count", 0.5),
        ("artifact_write_bytes", 1.5),
    ),
)
def test_runtime_observability_rejects_nonfinite_or_fractional_measurements(
    tmp_path: Path,
    metric_name: str,
    value: float,
) -> None:
    attempt = _attempt(tmp_path)

    with pytest.raises(RuntimeObservabilityContractError, match="finite|integer"):
        metrics = tuple(
            replace(metric, value=value) if metric.name == metric_name else metric
            for metric in _metrics(attempt.processing_attempt_id)
        )
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=_events(attempt),
            metrics=metrics,
        )


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


def test_runtime_observability_rejects_out_of_order_work_lifecycle(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    events[3:6] = (
        replace(events[3], kind="work_unit_settled"),
        replace(events[4], kind="work_unit_submitted"),
        replace(events[5], kind="work_unit_started"),
    )

    with pytest.raises(RuntimeObservabilityContractError, match="lifecycle order"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=tuple(events),
            metrics=_metrics(attempt.processing_attempt_id),
        )


def test_runtime_observability_rejects_duplicate_work_lifecycle_event(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    events.insert(4, replace(events[3]))
    events = [
        replace(
            event,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
        )
        for index, event in enumerate(events, start=1)
    ]

    with pytest.raises(RuntimeObservabilityContractError, match="exactly one"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=tuple(events),
            metrics=_metrics(attempt.processing_attempt_id),
        )


def test_runtime_observability_rejects_undeclared_work_event(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    events[3] = replace(events[3], subject_key="sample-b:target-b")

    with pytest.raises(RuntimeObservabilityContractError, match="undeclared"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=tuple(events),
            metrics=_metrics(attempt.processing_attempt_id),
        )


def test_runtime_observability_rejects_work_lifecycle_kind_on_wrong_subject(
    tmp_path: Path,
) -> None:
    attempt = _attempt(tmp_path)
    events = list(_events(attempt))
    events.insert(
        6,
        replace(
            events[3],
            kind="work_unit_submitted",
            subject=RuntimeObservationSubject.ATTEMPT,
            subject_key="attempt",
        ),
    )
    events = [
        replace(
            event,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
        )
        for index, event in enumerate(events, start=1)
    ]

    with pytest.raises(RuntimeObservabilityContractError, match="work_unit subject"):
        RuntimeObservabilityLedger(
            attempt=attempt,
            work_unit_keys=("sample-a:target-a",),
            events=tuple(events),
            metrics=_metrics(attempt.processing_attempt_id),
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
        ("worker_started", RuntimeObservationSubject.WORKER, "worker:1"),
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
                ("work_unit_started", "sample-a:target-a"): 40,
                ("work_unit_settled", "sample-a:target-a"): 50,
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
