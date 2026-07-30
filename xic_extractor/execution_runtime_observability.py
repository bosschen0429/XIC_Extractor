"""Executable no-I/O observability contract for the execution-runtime owner."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import final

from xic_extractor.acquisition_contract import TraceAcquisitionBatch
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class RuntimeObservabilityContractError(ValueError):
    """Raised when an attempt ledger or metric set is incomplete or ambiguous."""


_RUNTIME_OBSERVABILITY_MINT_TOKEN = object()


class RuntimeObservationSubject(str, Enum):
    ATTEMPT = "attempt"
    WORK_UNIT = "work_unit"
    WORKER = "worker"
    RESOURCE = "resource"
    ARTIFACT = "artifact"


class RuntimeMetricScope(str, Enum):
    ATTEMPT = "attempt"
    STAGE = "stage"
    WORK_UNIT = "work_unit"
    WORKER = "worker"
    RESOURCE = "resource"


class RuntimeMetricAvailability(str, Enum):
    MEASURED = "measured"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _RuntimeMetricSpec:
    running_scope: RuntimeMetricScope
    unit: str
    aggregation: str
    running_must_be_measured: bool = True


REQUIRED_RUNTIME_METRIC_NAMES = (
    "raw_open_count",
    "logical_xic_request_count",
    "xic_batch_count",
    "adapter_chromatogram_call_count",
    "trace_point_count",
    "coordinator_work_seconds",
    "attempt_wall_seconds",
    "stage_or_unit_elapsed_seconds",
    "worker_peak_rss_bytes",
    "process_tree_peak_rss_bytes",
    "artifact_read_count",
    "artifact_read_bytes",
    "artifact_write_count",
    "artifact_write_bytes",
)

_RUNTIME_METRIC_SPECS = {
    "raw_open_count": _RuntimeMetricSpec(RuntimeMetricScope.ATTEMPT, "count", "sum"),
    "logical_xic_request_count": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "count", "sum"
    ),
    "xic_batch_count": _RuntimeMetricSpec(RuntimeMetricScope.ATTEMPT, "count", "sum"),
    "adapter_chromatogram_call_count": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "count", "sum"
    ),
    "trace_point_count": _RuntimeMetricSpec(RuntimeMetricScope.ATTEMPT, "count", "sum"),
    "coordinator_work_seconds": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "seconds", "last"
    ),
    "attempt_wall_seconds": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "seconds", "last"
    ),
    "stage_or_unit_elapsed_seconds": _RuntimeMetricSpec(
        RuntimeMetricScope.WORK_UNIT, "seconds", "sum"
    ),
    "worker_peak_rss_bytes": _RuntimeMetricSpec(
        RuntimeMetricScope.WORKER,
        "bytes",
        "max",
        running_must_be_measured=False,
    ),
    "process_tree_peak_rss_bytes": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT,
        "bytes",
        "max",
        running_must_be_measured=False,
    ),
    "artifact_read_count": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "count", "sum"
    ),
    "artifact_read_bytes": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "bytes", "sum"
    ),
    "artifact_write_count": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "count", "sum"
    ),
    "artifact_write_bytes": _RuntimeMetricSpec(
        RuntimeMetricScope.ATTEMPT, "bytes", "sum"
    ),
}

_WORK_UNIT_LIFECYCLE_KINDS = (
    "work_unit_submitted",
    "work_unit_started",
    "work_unit_settled",
)

_WORKER_LIFECYCLE_KINDS = {"worker_started"}

_RESOURCE_OPEN_EVENT_KINDS = {
    "resource_opened",
    "resource_open_failed",
    "resource_not_opened",
    "resource_open_state_unknown_after_worker_loss",
}
_RESOURCE_CLEANUP_EVENT_KINDS = {
    "resource_cleanup_succeeded",
    "resource_cleanup_failed",
    "resource_cleanup_unknown_after_worker_loss",
}
_RESOURCE_LIFECYCLE_KINDS = {
    "resource_reserved",
    *_RESOURCE_OPEN_EVENT_KINDS,
    *_RESOURCE_CLEANUP_EVENT_KINDS,
}
_ARTIFACT_DISPOSITION_EVENT_KINDS = {
    "artifact_offered_to_owner",
    "artifact_quarantined_diagnostic",
}
_ARTIFACT_LIFECYCLE_KINDS = {
    "artifact_registered",
    *_ARTIFACT_DISPOSITION_EVENT_KINDS,
}

_RUNNING_ATTEMPT_LIFECYCLE_KINDS = (
    "attempt_queued",
    "manifest_closed",
    "attempt_started",
    "terminal_proposal",
    "terminal_acknowledged",
    "terminal_outcome",
)

_PREFLIGHT_ATTEMPT_LIFECYCLE_KINDS = (
    "attempt_queued",
    "manifest_preflight_failed",
    "terminal_proposal",
    "terminal_acknowledged",
    "terminal_outcome",
)

_ATTEMPT_LIFECYCLE_SUBJECT_KEYS = {
    kind: "manifest" if kind == "manifest_closed" else "attempt"
    for kind in (
        *_RUNNING_ATTEMPT_LIFECYCLE_KINDS,
        *_PREFLIGHT_ATTEMPT_LIFECYCLE_KINDS,
    )
}

_PREFLIGHT_UNAVAILABLE_METRICS = {
    "coordinator_work_seconds",
    "stage_or_unit_elapsed_seconds",
    "worker_peak_rss_bytes",
    "process_tree_peak_rss_bytes",
}

_PREFLIGHT_ZERO_METRICS = (
    set(REQUIRED_RUNTIME_METRIC_NAMES)
    - _PREFLIGHT_UNAVAILABLE_METRICS
    - {"attempt_wall_seconds"}
)

_ACQUISITION_COUNT_METRIC_NAMES = (
    "logical_xic_request_count",
    "xic_batch_count",
    "adapter_chromatogram_call_count",
    "trace_point_count",
)


@dataclass(frozen=True, slots=True)
class RuntimeObservationEvent:
    attempt: ProcessingAttemptIdentity
    sequence: int
    monotonic_offset_ns: int
    kind: str
    subject: RuntimeObservationSubject
    subject_key: str

    def __post_init__(self) -> None:
        _require_attempt(self.attempt)
        _nonnegative_integer(self.monotonic_offset_ns, "monotonic_offset_ns")
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise RuntimeObservabilityContractError("sequence must be an integer")
        if self.sequence <= 0:
            raise RuntimeObservabilityContractError("sequence must be positive")
        _required_text(self.kind, "kind")
        if not isinstance(self.subject, RuntimeObservationSubject):
            raise RuntimeObservabilityContractError(
                "subject must be a RuntimeObservationSubject"
            )
        _required_text(self.subject_key, "subject_key")


@dataclass(frozen=True, slots=True)
class RuntimeMetricRecord:
    name: str
    scope: RuntimeMetricScope
    scope_key: str
    unit: str
    aggregation: str
    measurement_source: str
    availability: RuntimeMetricAvailability
    value: int | float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.name, "name")
        if not isinstance(self.scope, RuntimeMetricScope):
            raise RuntimeObservabilityContractError(
                "scope must be a RuntimeMetricScope"
            )
        _required_text(self.scope_key, "scope_key")
        _required_text(self.unit, "unit")
        _required_text(self.aggregation, "aggregation")
        _required_text(self.measurement_source, "measurement_source")
        if not isinstance(self.availability, RuntimeMetricAvailability):
            raise RuntimeObservabilityContractError(
                "availability must be a RuntimeMetricAvailability"
            )
        if self.availability is RuntimeMetricAvailability.MEASURED:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise RuntimeObservabilityContractError(
                    "measured metric requires a numeric value"
                )
            if not math.isfinite(self.value):
                raise RuntimeObservabilityContractError(
                    "measured metric value must be finite"
                )
            if self.unit in {"count", "bytes"} and not isinstance(self.value, int):
                raise RuntimeObservabilityContractError(
                    "count and byte metric values must be integers"
                )
            if self.reason is not None:
                raise RuntimeObservabilityContractError(
                    "measured metric cannot carry an unavailable reason"
                )
        else:
            if self.value is not None:
                raise RuntimeObservabilityContractError(
                    "unavailable metric cannot carry a value"
                )
            _required_text(self.reason, "reason")


@final
@dataclass(frozen=True, slots=True)
class RuntimeObservabilityLedger:
    attempt: ProcessingAttemptIdentity
    work_unit_keys: tuple[str, ...]
    events: tuple[RuntimeObservationEvent, ...]
    metrics: tuple[RuntimeMetricRecord, ...]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeObservabilityLedger is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OBSERVABILITY_MINT_TOKEN:
            raise RuntimeObservabilityContractError(
                "RuntimeObservabilityLedger must be minted by the runtime owner"
            )
        attempt = _require_attempt(self.attempt)
        if not isinstance(self.work_unit_keys, tuple) or any(
            not isinstance(key, str) or not key.strip() for key in self.work_unit_keys
        ):
            raise RuntimeObservabilityContractError(
                "work_unit_keys must be an owner-normalized tuple"
            )
        if len(set(self.work_unit_keys)) != len(self.work_unit_keys):
            raise RuntimeObservabilityContractError("work_unit_keys must be unique")
        if not isinstance(self.events, tuple) or not self.events:
            raise RuntimeObservabilityContractError("events must be a non-empty tuple")
        if any(not isinstance(event, RuntimeObservationEvent) for event in self.events):
            raise RuntimeObservabilityContractError(
                "events must contain RuntimeObservationEvent values"
            )
        if any(event.attempt != attempt for event in self.events):
            raise RuntimeObservabilityContractError(
                "every event must bind the ledger attempt"
            )
        if tuple(event.sequence for event in self.events) != tuple(
            range(1, len(self.events) + 1)
        ):
            raise RuntimeObservabilityContractError(
                "event sequences must be contiguous from one"
            )
        offsets = tuple(event.monotonic_offset_ns for event in self.events)
        if offsets[0] != 0 or offsets != tuple(sorted(offsets)):
            raise RuntimeObservabilityContractError(
                "event offsets must start at zero and be monotonic"
            )
        if self.events[0].kind != "attempt_queued":
            raise RuntimeObservabilityContractError(
                "attempt_queued must be the first observation"
            )
        if self.events[-1].kind != "terminal_outcome":
            raise RuntimeObservabilityContractError(
                "terminal_outcome must be the last observation"
            )
        preflight_failed = _validate_attempt_event_lifecycle(
            self.work_unit_keys,
            self.events,
        )
        _validate_worker_event_lifecycle(self.events)
        _validate_resource_artifact_event_lifecycle(self.events)
        _validate_cross_domain_event_lifecycle(
            self.events,
            preflight_failed=preflight_failed,
        )
        if not isinstance(self.metrics, tuple) or any(
            not isinstance(metric, RuntimeMetricRecord) for metric in self.metrics
        ):
            raise RuntimeObservabilityContractError(
                "metrics must contain RuntimeMetricRecord values"
            )
        _validate_runtime_metrics(
            attempt,
            self.work_unit_keys,
            self.events,
            self.metrics,
            preflight_failed=preflight_failed,
        )


def _mint_runtime_observability_ledger(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    events: tuple[RuntimeObservationEvent, ...],
    metrics: tuple[RuntimeMetricRecord, ...],
) -> RuntimeObservabilityLedger:
    """Mint one validated ledger for the runtime owner or owner-model tests."""

    return RuntimeObservabilityLedger(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        events=events,
        metrics=metrics,
        _mint_token=_RUNTIME_OBSERVABILITY_MINT_TOKEN,
    )


def canonical_runtime_observability_ledger_bytes(
    ledger: RuntimeObservabilityLedger,
) -> bytes:
    """Serialize one validated complete ledger for durable terminal binding."""

    if not isinstance(ledger, RuntimeObservabilityLedger):
        raise RuntimeObservabilityContractError(
            "ledger must be a RuntimeObservabilityLedger"
        )
    payload = {
        "events": [
            {
                "kind": event.kind,
                "monotonic_offset_ns": event.monotonic_offset_ns,
                "sequence": event.sequence,
                "subject": event.subject.value,
                "subject_key": event.subject_key,
            }
            for event in ledger.events
        ],
        "metrics": [
            {
                "aggregation": metric.aggregation,
                "availability": metric.availability.value,
                "measurement_source": metric.measurement_source,
                "name": metric.name,
                "reason": metric.reason,
                "scope": metric.scope.value,
                "scope_key": metric.scope_key,
                "unit": metric.unit,
                "value": metric.value,
            }
            for metric in sorted(
                ledger.metrics,
                key=lambda item: (item.name, item.scope.value, item.scope_key),
            )
        ],
        "processing_attempt_id": ledger.attempt.processing_attempt_id,
        "processing_request_id": ledger.attempt.processing_request_id,
        "schema_version": "execution_runtime_observability_ledger_v1",
        "work_unit_keys": list(ledger.work_unit_keys),
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def validate_runtime_acquisition_metrics(
    ledger: RuntimeObservabilityLedger,
    acquisitions: Sequence[TraceAcquisitionBatch],
) -> None:
    """Reconcile adapter-ledger counts with accepted acquisition receipts."""

    if not isinstance(ledger, RuntimeObservabilityLedger):
        raise RuntimeObservabilityContractError(
            "ledger must be a RuntimeObservabilityLedger"
        )
    if isinstance(acquisitions, (str, bytes)) or any(
        not isinstance(acquisition, TraceAcquisitionBatch)
        for acquisition in acquisitions
    ):
        raise RuntimeObservabilityContractError(
            "acquisitions must contain TraceAcquisitionBatch receipts"
        )
    expected = {
        name: sum(getattr(acquisition, name) for acquisition in acquisitions)
        for name in _ACQUISITION_COUNT_METRIC_NAMES
    }
    measured = {
        metric.name: metric
        for metric in ledger.metrics
        if metric.name in _ACQUISITION_COUNT_METRIC_NAMES
    }
    for name, expected_value in expected.items():
        metric = measured[name]
        if (
            metric.availability is not RuntimeMetricAvailability.MEASURED
            or metric.value != expected_value
        ):
            raise RuntimeObservabilityContractError(
                "runtime acquisition metrics conflict with accepted acquisition "
                f"batches: {name}"
            )


def _require_attempt(value: object) -> ProcessingAttemptIdentity:
    if not isinstance(value, ProcessingAttemptIdentity):
        raise RuntimeObservabilityContractError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return value


def _validate_work_event_lifecycle(
    work_unit_keys: tuple[str, ...],
    events: tuple[RuntimeObservationEvent, ...],
) -> None:
    declared = set(work_unit_keys)
    work_events = tuple(
        event
        for event in events
        if event.subject is RuntimeObservationSubject.WORK_UNIT
    )
    undeclared = sorted({event.subject_key for event in work_events} - declared)
    if undeclared:
        raise RuntimeObservabilityContractError(
            "work events reference undeclared work units: " + ", ".join(undeclared)
        )
    if any(
        event.kind in _WORK_UNIT_LIFECYCLE_KINDS
        and event.subject is not RuntimeObservationSubject.WORK_UNIT
        for event in events
    ):
        raise RuntimeObservabilityContractError(
            "work lifecycle events must use the work_unit subject"
        )
    for key in work_unit_keys:
        positions: list[int] = []
        for kind in _WORK_UNIT_LIFECYCLE_KINDS:
            matches = [
                index
                for index, event in enumerate(events)
                if event.subject is RuntimeObservationSubject.WORK_UNIT
                and event.subject_key == key
                and event.kind == kind
            ]
            if len(matches) != 1:
                raise RuntimeObservabilityContractError(
                    "each work unit requires exactly one submitted, started, "
                    "and settled event"
                )
            positions.append(matches[0])
        if positions != sorted(positions):
            raise RuntimeObservabilityContractError(
                "work lifecycle order must be submitted, started, then settled"
            )
    expected_lifecycle = tuple(
        (key, kind)
        for key in work_unit_keys
        for kind in _WORK_UNIT_LIFECYCLE_KINDS
    )
    observed_lifecycle = tuple(
        (event.subject_key, event.kind) for event in work_events
    )
    if observed_lifecycle != expected_lifecycle:
        raise RuntimeObservabilityContractError(
            "work lifecycle must follow manifest order with one complete "
            "submitted, started, and settled sequence per work unit"
        )


def _validate_attempt_event_lifecycle(
    work_unit_keys: tuple[str, ...],
    events: tuple[RuntimeObservationEvent, ...],
) -> bool:
    known_kinds = set(_ATTEMPT_LIFECYCLE_SUBJECT_KEYS)
    if any(
        event.kind in known_kinds
        and (
            event.subject is not RuntimeObservationSubject.ATTEMPT
            or event.subject_key != _ATTEMPT_LIFECYCLE_SUBJECT_KEYS[event.kind]
        )
        for event in events
    ):
        raise RuntimeObservabilityContractError(
            "attempt lifecycle events must use their canonical subject and key"
        )
    preflight_failed = any(
        event.kind == "manifest_preflight_failed" for event in events
    )
    lifecycle_kinds = (
        _PREFLIGHT_ATTEMPT_LIFECYCLE_KINDS
        if preflight_failed
        else _RUNNING_ATTEMPT_LIFECYCLE_KINDS
    )
    positions: list[int] = []
    for kind in lifecycle_kinds:
        matches = [
            index
            for index, event in enumerate(events)
            if event.kind == kind
            and event.subject is RuntimeObservationSubject.ATTEMPT
            and event.subject_key == _ATTEMPT_LIFECYCLE_SUBJECT_KEYS[kind]
        ]
        if len(matches) != 1:
            raise RuntimeObservabilityContractError(
                "attempt lifecycle requires exactly one queued/start-or-preflight/"
                "proposal/acknowledgement/outcome milestone"
            )
        positions.append(matches[0])
    if positions != sorted(positions):
        raise RuntimeObservabilityContractError(
            "attempt lifecycle milestones are out of order"
        )
    forbidden = (
        {"manifest_closed", "attempt_started"}
        if preflight_failed
        else {"manifest_preflight_failed"}
    )
    if any(event.kind in forbidden for event in events):
        raise RuntimeObservabilityContractError(
            "attempt lifecycle mixes running and preflight-failure paths"
        )
    if preflight_failed:
        if work_unit_keys or any(
            event.subject is RuntimeObservationSubject.WORK_UNIT for event in events
        ):
            raise RuntimeObservabilityContractError(
                "preflight-failure lifecycle cannot contain declared or observed work"
            )
    else:
        _validate_work_event_lifecycle(work_unit_keys, events)
        started_position = positions[
            _RUNNING_ATTEMPT_LIFECYCLE_KINDS.index("attempt_started")
        ]
        proposal_position = positions[
            _RUNNING_ATTEMPT_LIFECYCLE_KINDS.index("terminal_proposal")
        ]
        work_positions = [
            index
            for index, event in enumerate(events)
            if event.kind in _WORK_UNIT_LIFECYCLE_KINDS
        ]
        if work_positions and (
            min(work_positions) < started_position
            or max(work_positions) > proposal_position
        ):
            raise RuntimeObservabilityContractError(
                "work lifecycle must occur after attempt start and before proposal"
            )
    return preflight_failed


def _validate_worker_event_lifecycle(
    events: tuple[RuntimeObservationEvent, ...],
) -> None:
    if any(
        event.kind in _WORKER_LIFECYCLE_KINDS
        and event.subject is not RuntimeObservationSubject.WORKER
        for event in events
    ):
        raise RuntimeObservabilityContractError(
            "worker lifecycle events must use the worker subject"
        )
    if any(
        event.subject is RuntimeObservationSubject.WORKER
        and event.kind not in _WORKER_LIFECYCLE_KINDS
        for event in events
    ):
        raise RuntimeObservabilityContractError(
            "worker subject contains an unregistered lifecycle event"
        )
    worker_keys = {
        event.subject_key
        for event in events
        if event.subject is RuntimeObservationSubject.WORKER
    }
    for worker_key in worker_keys:
        matches = tuple(
            event
            for event in events
            if event.subject is RuntimeObservationSubject.WORKER
            and event.subject_key == worker_key
            and event.kind == "worker_started"
        )
        if len(matches) != 1:
            raise RuntimeObservabilityContractError(
                "each observed worker requires exactly one worker_started event"
            )


def _validate_resource_artifact_event_lifecycle(
    events: tuple[RuntimeObservationEvent, ...],
) -> None:
    for event in events:
        if event.kind in _RESOURCE_LIFECYCLE_KINDS and (
            event.subject is not RuntimeObservationSubject.RESOURCE
        ):
            raise RuntimeObservabilityContractError(
                "resource lifecycle events must use the resource subject"
            )
        if event.subject is RuntimeObservationSubject.RESOURCE and (
            event.kind not in _RESOURCE_LIFECYCLE_KINDS
        ):
            raise RuntimeObservabilityContractError(
                "resource subject contains an unregistered lifecycle event"
            )
        if event.kind in _ARTIFACT_LIFECYCLE_KINDS and (
            event.subject is not RuntimeObservationSubject.ARTIFACT
        ):
            raise RuntimeObservabilityContractError(
                "artifact lifecycle events must use the artifact subject"
            )
        if event.subject is RuntimeObservationSubject.ARTIFACT and (
            event.kind not in _ARTIFACT_LIFECYCLE_KINDS
        ):
            raise RuntimeObservabilityContractError(
                "artifact subject contains an unregistered lifecycle event"
            )

    resource_keys = {
        event.subject_key
        for event in events
        if event.subject is RuntimeObservationSubject.RESOURCE
    }
    for resource_key in resource_keys:
        positions = {
            kind: tuple(
                index
                for index, event in enumerate(events)
                if event.subject is RuntimeObservationSubject.RESOURCE
                and event.subject_key == resource_key
                and event.kind == kind
            )
            for kind in _RESOURCE_LIFECYCLE_KINDS
        }
        reserved = positions["resource_reserved"]
        opened = tuple(
            index for kind in _RESOURCE_OPEN_EVENT_KINDS for index in positions[kind]
        )
        cleaned = tuple(
            index for kind in _RESOURCE_CLEANUP_EVENT_KINDS for index in positions[kind]
        )
        if len(reserved) != 1 or len(opened) != 1 or len(cleaned) != 1:
            raise RuntimeObservabilityContractError(
                "each resource requires exactly one reserved, open, and cleanup event"
            )
        if not reserved[0] < opened[0] < cleaned[0]:
            raise RuntimeObservabilityContractError(
                "resource lifecycle order must be reserved, opened, then cleaned"
            )

    artifact_ids = {
        event.subject_key
        for event in events
        if event.subject is RuntimeObservationSubject.ARTIFACT
    }
    for artifact_id in artifact_ids:
        registered = tuple(
            index
            for index, event in enumerate(events)
            if event.subject is RuntimeObservationSubject.ARTIFACT
            and event.subject_key == artifact_id
            and event.kind == "artifact_registered"
        )
        disposed = tuple(
            index
            for index, event in enumerate(events)
            if event.subject is RuntimeObservationSubject.ARTIFACT
            and event.subject_key == artifact_id
            and event.kind in _ARTIFACT_DISPOSITION_EVENT_KINDS
        )
        if len(registered) != 1 or len(disposed) != 1:
            raise RuntimeObservabilityContractError(
                "each artifact requires exactly one registered and disposition event"
            )
        if registered[0] >= disposed[0]:
            raise RuntimeObservabilityContractError(
                "artifact lifecycle order must be registered before disposition"
            )

    proposal_position = next(
        index for index, event in enumerate(events) if event.kind == "terminal_proposal"
    )
    acknowledgement_position = next(
        index
        for index, event in enumerate(events)
        if event.kind == "terminal_acknowledged"
    )
    outcome_position = next(
        index for index, event in enumerate(events) if event.kind == "terminal_outcome"
    )
    coordinator_resource_artifact_positions = tuple(
        index
        for index, event in enumerate(events)
        if event.subject is RuntimeObservationSubject.RESOURCE
        or event.kind == "artifact_registered"
    )
    if any(
        position > proposal_position
        for position in coordinator_resource_artifact_positions
    ):
        raise RuntimeObservabilityContractError(
            "coordinator resource and artifact events must occur before "
            "terminal proposal"
        )
    disposition_positions = tuple(
        index
        for index, event in enumerate(events)
        if event.kind in _ARTIFACT_DISPOSITION_EVENT_KINDS
    )
    if any(
        not acknowledgement_position < position < outcome_position
        for position in disposition_positions
    ):
        raise RuntimeObservabilityContractError(
            "artifact dispositions must occur after terminal acknowledgement and "
            "before terminal outcome"
        )


def _validate_cross_domain_event_lifecycle(
    events: tuple[RuntimeObservationEvent, ...],
    *,
    preflight_failed: bool,
) -> None:
    runtime_subjects = {
        RuntimeObservationSubject.WORK_UNIT,
        RuntimeObservationSubject.WORKER,
        RuntimeObservationSubject.RESOURCE,
        RuntimeObservationSubject.ARTIFACT,
    }
    if preflight_failed:
        if any(event.subject in runtime_subjects for event in events):
            raise RuntimeObservabilityContractError(
                "preflight-failure lifecycle cannot contain runtime causal phases"
            )
        return

    def positions(*kinds: str) -> tuple[int, ...]:
        accepted = set(kinds)
        return tuple(
            index
            for index, event in enumerate(events)
            if event.kind in accepted
        )

    def require_before(
        earlier: tuple[int, ...],
        later: tuple[int, ...],
        detail: str,
    ) -> None:
        if earlier and later and max(earlier) >= min(later):
            raise RuntimeObservabilityContractError(
                f"runtime causal phases must order {detail}"
            )

    attempt_started = positions("attempt_started")
    reservations = positions("resource_reserved")
    open_outcomes = positions(*_RESOURCE_OPEN_EVENT_KINDS)
    workers = positions("worker_started")
    work_submitted = positions("work_unit_submitted")
    work_settled = positions("work_unit_settled")
    artifacts = positions("artifact_registered")
    cleanup_outcomes = positions(*_RESOURCE_CLEANUP_EVENT_KINDS)
    terminal_proposal = positions("terminal_proposal")
    runtime_positions = (
        *reservations,
        *open_outcomes,
        *workers,
        *work_submitted,
        *work_settled,
        *artifacts,
        *cleanup_outcomes,
    )
    if runtime_positions and max(attempt_started) >= min(runtime_positions):
        raise RuntimeObservabilityContractError(
            "runtime causal phases must begin after attempt_started"
        )
    require_before(reservations, open_outcomes, "reservations before open outcomes")
    require_before(open_outcomes, workers, "open outcomes before worker starts")
    require_before(
        open_outcomes,
        work_submitted,
        "open outcomes before work submission",
    )
    require_before(workers, work_submitted, "worker starts before work submission")
    require_before(
        work_settled,
        (*artifacts, *cleanup_outcomes),
        "settled work before artifacts and cleanup",
    )
    require_before(artifacts, cleanup_outcomes, "artifacts before resource cleanup")
    require_before(cleanup_outcomes, terminal_proposal, "cleanup before proposal")


def _validate_runtime_metrics(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    events: tuple[RuntimeObservationEvent, ...],
    metrics: tuple[RuntimeMetricRecord, ...],
    *,
    preflight_failed: bool,
) -> None:
    names = {metric.name for metric in metrics}
    unknown = names - set(REQUIRED_RUNTIME_METRIC_NAMES)
    if unknown:
        raise RuntimeObservabilityContractError(
            f"runtime metrics are not registered: {', '.join(sorted(unknown))}"
        )
    missing = set(REQUIRED_RUNTIME_METRIC_NAMES) - names
    if missing:
        raise RuntimeObservabilityContractError(
            f"required runtime metrics are missing: {', '.join(sorted(missing))}"
        )
    identities = tuple(
        (metric.name, metric.scope, metric.scope_key) for metric in metrics
    )
    if len(set(identities)) != len(identities):
        raise RuntimeObservabilityContractError(
            "runtime metric identity must be unique by name, scope, and scope_key"
        )
    by_name = {
        name: tuple(metric for metric in metrics if metric.name == name)
        for name in REQUIRED_RUNTIME_METRIC_NAMES
    }
    attempt_id = attempt.processing_attempt_id
    observed_worker_keys = {
        event.subject_key
        for event in events
        if event.subject is RuntimeObservationSubject.WORKER
    }
    for name, records in by_name.items():
        spec = _RUNTIME_METRIC_SPECS[name]
        if name == "stage_or_unit_elapsed_seconds" and not preflight_failed:
            if len(records) != len(work_unit_keys) or {
                record.scope_key for record in records
            } != set(work_unit_keys):
                raise RuntimeObservabilityContractError(
                    "stage_or_unit_elapsed_seconds requires one metric per work unit"
                )
        elif name == "worker_peak_rss_bytes" and not preflight_failed:
            if observed_worker_keys:
                if (
                    len(records) != len(observed_worker_keys)
                    or {record.scope_key for record in records} != observed_worker_keys
                ):
                    raise RuntimeObservabilityContractError(
                        "worker_peak_rss_bytes requires one metric per observed worker"
                    )
            elif (
                len(records) != 1
                or records[0].scope is not RuntimeMetricScope.ATTEMPT
                or records[0].scope_key != attempt_id
                or records[0].availability is not RuntimeMetricAvailability.UNSUPPORTED
                or records[0].reason != "not_applicable_serial"
            ):
                raise RuntimeObservabilityContractError(
                    "serial runtime requires one attempt-scoped unsupported "
                    "worker_peak_rss_bytes metric"
                )
        elif len(records) != 1:
            raise RuntimeObservabilityContractError(
                f"runtime metric requires exactly one canonical record: {name}"
            )
        expected_scope = (
            RuntimeMetricScope.ATTEMPT
            if preflight_failed
            else (
                RuntimeMetricScope.ATTEMPT
                if name == "worker_peak_rss_bytes" and not observed_worker_keys
                else spec.running_scope
            )
        )
        for record in records:
            if (
                record.scope is not expected_scope
                or record.unit != spec.unit
                or record.aggregation != spec.aggregation
            ):
                raise RuntimeObservabilityContractError(
                    f"runtime metric contract has wrong scope/unit/aggregation: {name}"
                )
            if record.scope is RuntimeMetricScope.ATTEMPT and (
                record.scope_key != attempt_id
            ):
                raise RuntimeObservabilityContractError(
                    f"runtime metric contract has wrong attempt scope_key: {name}"
                )
            if (
                record.availability is RuntimeMetricAvailability.MEASURED
                and record.value is not None
                and record.value < 0
            ):
                raise RuntimeObservabilityContractError(
                    f"runtime metric contract requires non-negative values: {name}"
                )
    if preflight_failed:
        _validate_preflight_metrics(by_name)
    else:
        for name, spec in _RUNTIME_METRIC_SPECS.items():
            if spec.running_must_be_measured and any(
                record.availability is not RuntimeMetricAvailability.MEASURED
                for record in by_name[name]
            ):
                raise RuntimeObservabilityContractError(
                    f"running attempt requires measured runtime metric: {name}"
                )
    _validate_elapsed_metrics(events, by_name, preflight_failed=preflight_failed)


def _validate_elapsed_metrics(
    events: tuple[RuntimeObservationEvent, ...],
    by_name: dict[str, tuple[RuntimeMetricRecord, ...]],
    *,
    preflight_failed: bool,
) -> None:
    def offset(kind: str, subject_key: str = "attempt") -> int:
        matches = tuple(
            event.monotonic_offset_ns
            for event in events
            if event.kind == kind and event.subject_key == subject_key
        )
        if len(matches) != 1:
            raise RuntimeObservabilityContractError(
                "elapsed metrics require one matching lifecycle event"
            )
        return matches[0]

    def require_span(metric: RuntimeMetricRecord, expected_seconds: float) -> None:
        if (
            metric.availability is not RuntimeMetricAvailability.MEASURED
            or metric.value is None
            or not math.isclose(
                float(metric.value),
                expected_seconds,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeObservabilityContractError(
                f"{metric.name} conflicts with lifecycle event offsets"
            )

    require_span(
        by_name["attempt_wall_seconds"][0],
        (offset("terminal_outcome") - offset("attempt_queued")) / 1_000_000_000,
    )
    if preflight_failed:
        return
    require_span(
        by_name["coordinator_work_seconds"][0],
        (offset("terminal_proposal") - offset("attempt_started")) / 1_000_000_000,
    )
    for metric in by_name["stage_or_unit_elapsed_seconds"]:
        require_span(
            metric,
            (
                offset("work_unit_settled", metric.scope_key)
                - offset("work_unit_started", metric.scope_key)
            )
            / 1_000_000_000,
        )


def _validate_preflight_metrics(
    by_name: dict[str, tuple[RuntimeMetricRecord, ...]],
) -> None:
    for name in _PREFLIGHT_ZERO_METRICS:
        record = by_name[name][0]
        if (
            record.availability is not RuntimeMetricAvailability.MEASURED
            or record.value != 0
        ):
            raise RuntimeObservabilityContractError(
                f"preflight runtime metric must be measured zero: {name}"
            )
    wall = by_name["attempt_wall_seconds"][0]
    if wall.availability is not RuntimeMetricAvailability.MEASURED:
        raise RuntimeObservabilityContractError(
            "preflight attempt_wall_seconds must be measured"
        )
    for name in _PREFLIGHT_UNAVAILABLE_METRICS:
        record = by_name[name][0]
        if (
            record.availability is not RuntimeMetricAvailability.UNSUPPORTED
            or record.reason != "not_started_preflight_failure"
        ):
            raise RuntimeObservabilityContractError(
                f"preflight runtime metric requires not-started unavailability: {name}"
            )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeObservabilityContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _nonnegative_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeObservabilityContractError(
            f"{field} must be a non-negative integer"
        )
    return value


__all__ = (
    "REQUIRED_RUNTIME_METRIC_NAMES",
    "RuntimeMetricAvailability",
    "RuntimeMetricScope",
    "RuntimeObservabilityContractError",
    "RuntimeObservationSubject",
    "canonical_runtime_observability_ledger_bytes",
    "validate_runtime_acquisition_metrics",
)
