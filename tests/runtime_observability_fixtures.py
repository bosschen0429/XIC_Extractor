from __future__ import annotations

from typing import Any

from xic_extractor.execution._finalization import (
    RuntimeResourceKind,
    _record_runtime_resource_cleanups,
    _record_runtime_resource_opens,
    _register_runtime_artifacts,
    _reserve_runtime_resources,
)
from xic_extractor.execution._lifecycle import (
    _settle_work_units,
    mint_work_unit_settlement,
)
from xic_extractor.execution._observability import (
    REQUIRED_RUNTIME_METRIC_NAMES,
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
    _mint_runtime_observability_ledger,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


def build_runtime_observability_ledger(
    attempt: ProcessingAttemptIdentity,
    *,
    work_unit_keys: tuple[str, ...],
    worker_keys: tuple[str, ...],
    raw_open_count: int | None = None,
    logical_xic_request_count: int = 0,
    xic_batch_count: int = 0,
    adapter_chromatogram_call_count: int = 0,
    trace_point_count: int = 0,
    resource_lifecycles: tuple[tuple[str, RuntimeResourceKind, str, str], ...] = (),
    artifact_dispositions: tuple[tuple[str, str], ...] = (),
) -> RuntimeObservabilityLedger:
    event_shapes: list[tuple[str, RuntimeObservationSubject, str]] = [
        ("attempt_queued", RuntimeObservationSubject.ATTEMPT, "attempt"),
        ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest"),
        ("attempt_started", RuntimeObservationSubject.ATTEMPT, "attempt"),
    ]
    event_shapes.extend(
        (
            "resource_reserved",
            RuntimeObservationSubject.RESOURCE,
            resource_key,
        )
        for (
            resource_key,
            _resource_kind,
            _open_kind,
            _cleanup_kind,
        ) in resource_lifecycles
    )
    event_shapes.extend(
        (open_kind, RuntimeObservationSubject.RESOURCE, resource_key)
        for (
            resource_key,
            _resource_kind,
            open_kind,
            _cleanup_kind,
        ) in resource_lifecycles
    )
    event_shapes.extend(
        ("worker_started", RuntimeObservationSubject.WORKER, worker_key)
        for worker_key in worker_keys
    )
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
        (
            "artifact_registered",
            RuntimeObservationSubject.ARTIFACT,
            artifact_id,
        )
        for artifact_id, _disposition_kind in artifact_dispositions
    )
    event_shapes.extend(
        (cleanup_kind, RuntimeObservationSubject.RESOURCE, resource_key)
        for (
            resource_key,
            _resource_kind,
            _open_kind,
            cleanup_kind,
        ) in resource_lifecycles
    )
    event_shapes.extend(
        (
            ("terminal_proposal", RuntimeObservationSubject.ATTEMPT, "attempt"),
            ("terminal_acknowledged", RuntimeObservationSubject.ATTEMPT, "attempt"),
        )
    )
    event_shapes.extend(
        (
            disposition_kind,
            RuntimeObservationSubject.ARTIFACT,
            artifact_id,
        )
        for artifact_id, disposition_kind in artifact_dispositions
    )
    event_shapes.append(
        ("terminal_outcome", RuntimeObservationSubject.ATTEMPT, "attempt")
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
    resolved_raw_open_count = (
        sum(
            resource_kind is RuntimeResourceKind.ACQUISITION_SESSION
            and open_kind == "resource_opened"
            for _key, resource_kind, open_kind, _cleanup in resource_lifecycles
        )
        if raw_open_count is None
        else raw_open_count
    )

    def offset(kind: str, subject_key: str = "attempt") -> int:
        return next(
            event.monotonic_offset_ns
            for event in events
            if event.kind == kind and event.subject_key == subject_key
        )

    metrics: list[RuntimeMetricRecord] = []
    for name in REQUIRED_RUNTIME_METRIC_NAMES:
        scope_keys = (
            work_unit_keys
            if name == "stage_or_unit_elapsed_seconds"
            else worker_keys or (attempt.processing_attempt_id,)
            if name == "worker_peak_rss_bytes"
            else (attempt.processing_attempt_id,)
        )
        if name == "stage_or_unit_elapsed_seconds":
            scope = RuntimeMetricScope.WORK_UNIT
        elif name == "worker_peak_rss_bytes" and worker_keys:
            scope = RuntimeMetricScope.WORKER
        else:
            scope = RuntimeMetricScope.ATTEMPT
        unit = (
            "count"
            if name in count_names
            else "seconds"
            if name in {*last_names, "stage_or_unit_elapsed_seconds"}
            else "bytes"
        )
        aggregation = (
            "last" if name in last_names else "max" if name in max_names else "sum"
        )
        for scope_key in scope_keys:
            value: int | float = 0
            if name == "raw_open_count":
                value = resolved_raw_open_count
            elif name == "logical_xic_request_count":
                value = logical_xic_request_count
            elif name == "xic_batch_count":
                value = xic_batch_count
            elif name == "adapter_chromatogram_call_count":
                value = adapter_chromatogram_call_count
            elif name == "trace_point_count":
                value = trace_point_count
            elif name == "coordinator_work_seconds":
                value = (
                    offset("terminal_proposal") - offset("attempt_started")
                ) / 1_000_000_000
            elif name == "attempt_wall_seconds":
                value = (
                    offset("terminal_outcome") - offset("attempt_queued")
                ) / 1_000_000_000
            elif name == "stage_or_unit_elapsed_seconds":
                value = (
                    offset("work_unit_settled", scope_key)
                    - offset("work_unit_started", scope_key)
                ) / 1_000_000_000
            worker_rss_supported = name != "worker_peak_rss_bytes" or bool(worker_keys)
            metrics.append(
                RuntimeMetricRecord(
                    name=name,
                    scope=scope,
                    scope_key=scope_key,
                    unit=unit,
                    aggregation=aggregation,
                    measurement_source="package0-1-no-raw-fixture",
                    availability=(
                        RuntimeMetricAvailability.MEASURED
                        if worker_rss_supported
                        else RuntimeMetricAvailability.UNSUPPORTED
                    ),
                    value=value if worker_rss_supported else None,
                    reason=None if worker_rss_supported else "not_applicable_serial",
                )
            )
    return _mint_runtime_observability_ledger(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        events=events,
        metrics=tuple(metrics),
    )


def reserve_runtime_resource(state: Any, reservation: Any) -> Any:
    return _reserve_runtime_resources(state, (reservation,))


def record_runtime_resource_open(
    state: Any, *, resource_key: str, outcome: Any
) -> Any:
    return _record_runtime_resource_opens(state, ((resource_key, outcome),))


def record_runtime_resource_cleanup(
    state: Any, *, resource_key: str, outcome: Any
) -> Any:
    return _record_runtime_resource_cleanups(state, ((resource_key, outcome),))


def settle_runtime_resource(
    state: Any, *, resource_key: str, open_outcome: Any, cleanup_outcome: Any
) -> Any:
    opened = _record_runtime_resource_opens(state, ((resource_key, open_outcome),))
    return _record_runtime_resource_cleanups(
        opened, ((resource_key, cleanup_outcome),)
    )


def register_runtime_artifact(
    state: Any, *, work_unit_key: str, artifact: Any
) -> Any:
    return _register_runtime_artifacts(state, ((work_unit_key, artifact),))


def settle_work_unit(runtime: Any, **kwargs: Any) -> Any:
    settlement = mint_work_unit_settlement(
        attempt=runtime.manifest.attempt, **kwargs
    )
    return _settle_work_units(runtime, (settlement,))
