from __future__ import annotations

from xic_extractor.execution_runtime_observability import (
    REQUIRED_RUNTIME_METRIC_NAMES,
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


def build_runtime_observability_ledger(
    attempt: ProcessingAttemptIdentity,
    *,
    work_unit_keys: tuple[str, ...],
    worker_keys: tuple[str, ...],
    raw_open_count: int = 0,
) -> RuntimeObservabilityLedger:
    event_shapes: list[tuple[str, RuntimeObservationSubject, str]] = [
        ("attempt_queued", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest"),
        ("attempt_started", RuntimeObservationSubject.ATTEMPT, "attempt"),
    ]
    for work_unit_key in work_unit_keys:
        event_shapes.extend(
            (
                (
                    "work_unit_submitted",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
                (
                    "work_unit_started",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
                (
                    "work_unit_settled",
                    RuntimeObservationSubject.WORK_UNIT,
                    work_unit_key,
                ),
            )
        )
    event_shapes.extend(
        ("worker_started", RuntimeObservationSubject.WORKER, worker_key)
        for worker_key in worker_keys
    )
    event_shapes.extend(
        (
            ("terminal_proposal", RuntimeObservationSubject.ATTEMPT, "attempt"),
            ("terminal_acknowledged", RuntimeObservationSubject.ATTEMPT, "attempt"),
            ("terminal_outcome", RuntimeObservationSubject.ATTEMPT, "attempt"),
        )
    )
    events = tuple(
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=index,
            monotonic_offset_ns=(index - 1) * 10,
            kind=kind,
            subject=subject,
            subject_key=subject_key,
        )
        for index, (kind, subject, subject_key) in enumerate(event_shapes, start=1)
    )
    count_names = {
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "artifact_read_count",
        "artifact_write_count",
    }
    last_names = {"coordinator_work_seconds", "attempt_wall_seconds"}
    max_names = {"worker_peak_rss_bytes", "process_tree_peak_rss_bytes"}
    metrics: list[RuntimeMetricRecord] = []
    for name in REQUIRED_RUNTIME_METRIC_NAMES:
        scope_keys = (
            work_unit_keys
            if name == "stage_or_unit_elapsed_seconds"
            else worker_keys
            if name == "worker_peak_rss_bytes"
            else (attempt.processing_attempt_id,)
        )
        scope = (
            RuntimeMetricScope.WORK_UNIT
            if name == "stage_or_unit_elapsed_seconds"
            else RuntimeMetricScope.WORKER
            if name == "worker_peak_rss_bytes"
            else RuntimeMetricScope.ATTEMPT
        )
        unit = (
            "count"
            if name in count_names
            else "seconds"
            if name in {*last_names, "stage_or_unit_elapsed_seconds"}
            else "bytes"
        )
        aggregation = (
            "last"
            if name in last_names
            else "max"
            if name in max_names
            else "sum"
        )
        for scope_key in scope_keys:
            metrics.append(
                RuntimeMetricRecord(
                    name=name,
                    scope=scope,
                    scope_key=scope_key,
                    unit=unit,
                    aggregation=aggregation,
                    measurement_source="package0-1-no-raw-fixture",
                    availability=RuntimeMetricAvailability.MEASURED,
                    value=raw_open_count if name == "raw_open_count" else 0,
                )
            )
    return RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        events=events,
        metrics=tuple(metrics),
    )
