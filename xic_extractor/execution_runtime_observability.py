"""Executable no-I/O observability contract for the execution-runtime owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from xic_extractor.processing_identity import ProcessingAttemptIdentity


class RuntimeObservabilityContractError(ValueError):
    """Raised when an attempt ledger or metric set is incomplete or ambiguous."""


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


@dataclass(frozen=True, slots=True)
class RuntimeObservabilityLedger:
    attempt: ProcessingAttemptIdentity
    work_unit_keys: tuple[str, ...]
    events: tuple[RuntimeObservationEvent, ...]
    metrics: tuple[RuntimeMetricRecord, ...]

    def __post_init__(self) -> None:
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
        if not isinstance(self.metrics, tuple) or any(
            not isinstance(metric, RuntimeMetricRecord) for metric in self.metrics
        ):
            raise RuntimeObservabilityContractError(
                "metrics must contain RuntimeMetricRecord values"
            )
        _validate_runtime_metrics(
            attempt,
            self.work_unit_keys,
            self.metrics,
            preflight_failed=preflight_failed,
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


def _validate_runtime_metrics(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
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
    for name, records in by_name.items():
        spec = _RUNTIME_METRIC_SPECS[name]
        if name == "stage_or_unit_elapsed_seconds" and not preflight_failed:
            if len(records) != len(work_unit_keys) or {
                record.scope_key for record in records
            } != set(work_unit_keys):
                raise RuntimeObservabilityContractError(
                    "stage_or_unit_elapsed_seconds requires one metric per work unit"
                )
        elif len(records) != 1:
            raise RuntimeObservabilityContractError(
                f"runtime metric requires exactly one canonical record: {name}"
            )
        expected_scope = (
            RuntimeMetricScope.ATTEMPT if preflight_failed else spec.running_scope
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


__all__ = [
    "REQUIRED_RUNTIME_METRIC_NAMES",
    "RuntimeMetricAvailability",
    "RuntimeMetricRecord",
    "RuntimeMetricScope",
    "RuntimeObservabilityContractError",
    "RuntimeObservabilityLedger",
    "RuntimeObservationEvent",
    "RuntimeObservationSubject",
]
