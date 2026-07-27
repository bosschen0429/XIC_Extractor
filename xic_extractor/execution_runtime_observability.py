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
        work_events = {
            key: {
                event.kind
                for event in self.events
                if event.subject is RuntimeObservationSubject.WORK_UNIT
                and event.subject_key == key
            }
            for key in self.work_unit_keys
        }
        required = {"work_unit_submitted", "work_unit_started", "work_unit_settled"}
        if any(kinds & required != required for kinds in work_events.values()):
            raise RuntimeObservabilityContractError(
                "each work unit requires submitted, started, and settled events"
            )
        if not isinstance(self.metrics, tuple) or any(
            not isinstance(metric, RuntimeMetricRecord) for metric in self.metrics
        ):
            raise RuntimeObservabilityContractError(
                "metrics must contain RuntimeMetricRecord values"
            )
        names = {metric.name for metric in self.metrics}
        missing = set(REQUIRED_RUNTIME_METRIC_NAMES) - names
        if missing:
            raise RuntimeObservabilityContractError(
                f"required runtime metrics are missing: {', '.join(sorted(missing))}"
            )


def _require_attempt(value: object) -> ProcessingAttemptIdentity:
    if not isinstance(value, ProcessingAttemptIdentity):
        raise RuntimeObservabilityContractError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )
    return value


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
