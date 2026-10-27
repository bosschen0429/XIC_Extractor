"""Immutable receipts, metrics, and sampling contracts for runtime execution."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionReceipt,
    TraceAcquisitionBatch,
    TraceAcquisitionCapability,
)
from xic_extractor.execution._finalization import (
    CoordinatorTerminalRecord,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    SupervisorFinalizationRecord,
)
from xic_extractor.execution._lifecycle import WorkUnitSettlement
from xic_extractor.execution._observability import (
    RuntimeMetricAvailability,
    RuntimeObservabilityLedger,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    ProcessingAttemptIdentity,
)

_RUNTIME_OWNER_MEASUREMENT_SOURCE = "execution_runtime_owner"


class RuntimeExecutionPhase(str, Enum):
    """The direct provider boundary at which one runtime attempt failed."""

    WORKER_BATCH_PROVIDER = "worker_batch_provider"
    TELEMETRY_PROVIDER = "telemetry_provider"
    PERSISTER = "persister"


class RuntimeExecutionPhaseError(RuntimeError):
    """An owner-classified exception raised by a runtime provider boundary."""

    attempt: ProcessingAttemptIdentity
    phase: RuntimeExecutionPhase

    def __init__(
        self,
        attempt: ProcessingAttemptIdentity,
        phase: RuntimeExecutionPhase,
    ) -> None:
        if not isinstance(attempt, ProcessingAttemptIdentity):
            raise RuntimeFinalizationContractError(
                "phase error attempt must be owner-issued"
            )
        if not isinstance(phase, RuntimeExecutionPhase):
            raise RuntimeFinalizationContractError(
                "phase error phase must be a RuntimeExecutionPhase"
            )
        self.attempt = attempt
        self.phase = phase
        super().__init__(
            f"runtime attempt {attempt.processing_attempt_id} failed during "
            f"{phase.value}"
        )


@dataclass(frozen=True, slots=True)
class RuntimeCancellationRequest:
    """One owner-bound request to accept cancellation for a runtime attempt."""

    attempt: ProcessingAttemptIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise RuntimeFinalizationContractError(
                "cancellation request attempt must be owner-issued"
            )


@dataclass(frozen=True, slots=True)
class RuntimeResourceSettlementReceipt:
    """One child/adapter report for an already reserved runtime resource."""

    attempt: ProcessingAttemptIdentity
    resource_key: str
    open_outcome: RuntimeResourceOpenOutcome
    cleanup_outcome: RuntimeResourceCleanupOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, ProcessingAttemptIdentity):
            raise RuntimeFinalizationContractError(
                "resource receipt attempt must be owner-issued"
            )
        _required_text(self.resource_key, field="resource_key")
        if not isinstance(self.open_outcome, RuntimeResourceOpenOutcome):
            raise RuntimeFinalizationContractError(
                "open_outcome must be a RuntimeResourceOpenOutcome"
            )
        if not isinstance(self.cleanup_outcome, RuntimeResourceCleanupOutcome):
            raise RuntimeFinalizationContractError(
                "cleanup_outcome must be a RuntimeResourceCleanupOutcome"
            )


@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionMetrics:
    logical_xic_request_count: int
    xic_batch_count: int
    adapter_chromatogram_call_count: int
    trace_point_count: int

    def __post_init__(self) -> None:
        for field_name in (
            "logical_xic_request_count",
            "xic_batch_count",
            "adapter_chromatogram_call_count",
            "trace_point_count",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeFinalizationContractError(
                    f"{field_name} must be a non-negative integer"
                )


@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionReceipt:
    """Observed acquisition facts correlated to one submitted terminal result."""

    work_unit_key: str
    acquisition: TraceAcquisitionBatch | RuntimeAcquisitionMetrics
    acquisition_capability: TraceAcquisitionCapability | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        if isinstance(self.acquisition, TraceAcquisitionBatch):
            capability = self.acquisition.capability
            if self.acquisition_capability not in (None, capability):
                raise RuntimeFinalizationContractError(
                    "acquisition receipt capability does not match its batch"
                )
            object.__setattr__(self, "acquisition_capability", capability)
        elif isinstance(self.acquisition, RuntimeAcquisitionMetrics):
            if not isinstance(self.acquisition_capability, TraceAcquisitionCapability):
                raise RuntimeFinalizationContractError(
                    "compacted acquisition requires its capability"
                )
        else:
            raise RuntimeFinalizationContractError(
                "acquisition must be an owner-produced TraceAcquisitionBatch or metrics"
            )


def derive_runtime_acquisition_metrics(
    acquisitions: Sequence[TraceAcquisitionBatch | RuntimeAcquisitionMetrics],
) -> RuntimeAcquisitionMetrics:
    """Aggregate only acquisition-owner receipts into runtime-owned counts."""

    if isinstance(acquisitions, (str, bytes)):
        raise RuntimeFinalizationContractError(
            "acquisitions must contain TraceAcquisitionBatch receipts"
        )
    try:
        batch = tuple(acquisitions)
    except TypeError as exc:
        raise RuntimeFinalizationContractError(
            "acquisitions must contain TraceAcquisitionBatch receipts"
        ) from exc
    if any(
        type(item) not in (TraceAcquisitionBatch, RuntimeAcquisitionMetrics)
        for item in batch
    ):
        raise RuntimeFinalizationContractError(
            "acquisitions must contain TraceAcquisitionBatch receipts"
        )
    return RuntimeAcquisitionMetrics(
        logical_xic_request_count=sum(item.logical_xic_request_count for item in batch),
        xic_batch_count=sum(item.xic_batch_count for item in batch),
        adapter_chromatogram_call_count=sum(
            item.adapter_chromatogram_call_count for item in batch
        ),
        trace_point_count=sum(item.trace_point_count for item in batch),
    )


def compact_runtime_acquisition_receipt(
    receipt: RuntimeAcquisitionReceipt,
) -> RuntimeAcquisitionReceipt:
    """Release scientific arrays after their consumer has sealed its result."""
    if type(receipt) is not RuntimeAcquisitionReceipt:
        raise RuntimeFinalizationContractError(
            "compaction requires a runtime acquisition receipt"
        )
    if isinstance(receipt.acquisition, RuntimeAcquisitionMetrics):
        return receipt
    return RuntimeAcquisitionReceipt(
        work_unit_key=receipt.work_unit_key,
        acquisition=derive_runtime_acquisition_metrics((receipt.acquisition,)),
        acquisition_capability=receipt.acquisition_capability,
    )


@dataclass(frozen=True, slots=True)
class RuntimeRawOpenMetrics:
    """Runtime-owned projection for exactly one planned RAW-open session."""

    raw_open_count: int | None
    unavailable_reason: str

    def __post_init__(self) -> None:
        if self.raw_open_count is None:
            _required_text(self.unavailable_reason, field="unavailable_reason")
            return
        if self.raw_open_count != 1:
            raise RuntimeFinalizationContractError(
                "raw_open_count must be exactly one for a receipted session"
            )
        if self.unavailable_reason:
            raise RuntimeFinalizationContractError(
                "measured raw_open_count cannot carry an unavailable reason"
            )


def derive_runtime_raw_open_metrics(
    acquisition_source: AcquisitionSourceIdentity | None,
    receipts: Sequence[RawAcquisitionSessionReceipt],
) -> RuntimeRawOpenMetrics:
    """Accept only the receipt bound to the existing job/sample source."""

    if isinstance(receipts, (str, bytes)):
        raise RuntimeFinalizationContractError(
            "RAW-open receipts must contain typed session receipts"
        )
    try:
        receipt_batch = tuple(receipts)
    except TypeError as exc:
        raise RuntimeFinalizationContractError(
            "RAW-open receipts must contain typed session receipts"
        ) from exc
    if any(
        not isinstance(receipt, RawAcquisitionSessionReceipt)
        for receipt in receipt_batch
    ):
        raise RuntimeFinalizationContractError(
            "RAW-open receipts must contain typed session receipts"
        )
    if len(receipt_batch) > 1:
        raise RuntimeFinalizationContractError(
            "duplicate RAW-open receipts for one session are forbidden"
        )
    if acquisition_source is None:
        if receipt_batch:
            raise RuntimeFinalizationContractError(
                "RAW-open receipt has no matching job source"
            )
        return RuntimeRawOpenMetrics(
            raw_open_count=None,
            unavailable_reason="RAW opener has no bound acquisition source",
        )
    if not isinstance(acquisition_source, AcquisitionSourceIdentity):
        raise RuntimeFinalizationContractError(
            "acquisition_source must be an AcquisitionSourceIdentity"
        )
    if not receipt_batch:
        return RuntimeRawOpenMetrics(
            raw_open_count=None,
            unavailable_reason="process result carries no owner RAW-open receipt",
        )
    if receipt_batch[0].acquisition_source != acquisition_source:
        raise RuntimeFinalizationContractError(
            "RAW-open receipt is bound to a different acquisition source"
        )
    return RuntimeRawOpenMetrics(
        raw_open_count=1,
        unavailable_reason="",
    )


@dataclass(frozen=True, slots=True)
class RuntimeWorkerBatchReceipt:
    """Workflow-neutral closed facts returned from one adapter batch."""

    settlements: tuple[WorkUnitSettlement, ...]
    resource_settlements: tuple[RuntimeResourceSettlementReceipt, ...] = ()
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...] = ()

    def __post_init__(self) -> None:
        _require_tuple_of(
            self.settlements,
            WorkUnitSettlement,
            field="settlements",
        )
        _require_tuple_of(
            self.resource_settlements,
            RuntimeResourceSettlementReceipt,
            field="resource_settlements",
        )
        _require_tuple_of(
            self.acquisitions,
            RuntimeAcquisitionReceipt,
            field="acquisitions",
        )


@dataclass(frozen=True, slots=True)
class RuntimeArtifactIoSample:
    """External observation of runtime-mediated artifact I/O only."""

    read_count: int
    read_bytes: int
    write_count: int
    write_bytes: int

    def __post_init__(self) -> None:
        for field_name in (
            "read_count",
            "read_bytes",
            "write_count",
            "write_bytes",
        ):
            _nonnegative_integer(getattr(self, field_name), field=field_name)


@dataclass(frozen=True, slots=True)
class RuntimeWorkerPeakRssSample:
    """One externally observed peak-RSS result for an owner-derived worker key."""

    worker_key: str
    availability: RuntimeMetricAvailability
    value: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _required_text(self.worker_key, field="worker_key")
        _validate_rss_sample(
            self.availability,
            self.value,
            self.reason,
            field="worker_peak_rss_bytes",
        )


@dataclass(frozen=True, slots=True)
class RuntimeProcessTreePeakRssSample:
    """Externally observed peak RSS for the parent-plus-child process tree."""

    availability: RuntimeMetricAvailability
    value: int | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_rss_sample(
            self.availability,
            self.value,
            self.reason,
            field="process_tree_peak_rss_bytes",
        )


@dataclass(frozen=True, slots=True)
class RuntimeTelemetrySamples:
    """Narrow external samples; lifecycle topology and counts are not caller data."""

    event_monotonic_offsets_ns: tuple[int, ...] | None
    artifact_io: RuntimeArtifactIoSample | None
    process_tree_peak_rss: RuntimeProcessTreePeakRssSample
    worker_peak_rss: tuple[RuntimeWorkerPeakRssSample, ...] = ()
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.event_monotonic_offsets_ns is not None and (
            not isinstance(self.event_monotonic_offsets_ns, tuple)
            or any(
                isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
                for offset in self.event_monotonic_offsets_ns
            )
        ):
            raise RuntimeFinalizationContractError(
                "event_monotonic_offsets_ns must contain non-negative integers"
            )
        if self.artifact_io is not None and not isinstance(
            self.artifact_io, RuntimeArtifactIoSample
        ):
            raise RuntimeFinalizationContractError(
                "artifact_io must be a RuntimeArtifactIoSample when observed"
            )
        if self.event_monotonic_offsets_ns is None or self.artifact_io is None:
            _required_text(self.unavailable_reason, field="unavailable_reason")
        elif self.unavailable_reason is not None:
            raise RuntimeFinalizationContractError(
                "measured telemetry cannot carry an unavailable reason"
            )
        if not isinstance(
            self.process_tree_peak_rss,
            RuntimeProcessTreePeakRssSample,
        ):
            raise RuntimeFinalizationContractError(
                "process_tree_peak_rss must be a RuntimeProcessTreePeakRssSample"
            )
        _require_tuple_of(
            self.worker_peak_rss,
            RuntimeWorkerPeakRssSample,
            field="worker_peak_rss",
        )
        worker_keys = tuple(item.worker_key for item in self.worker_peak_rss)
        if len(worker_keys) != len(set(worker_keys)):
            raise RuntimeFinalizationContractError(
                "worker_peak_rss samples must have unique worker keys"
            )


@dataclass(frozen=True, slots=True)
class RuntimeFinalizationReceipt:
    """One accepted pair of coordinator and supervisor terminal facts."""

    coordinator: CoordinatorTerminalRecord
    supervisor: SupervisorFinalizationRecord

    @property
    def observability_ledger(self) -> RuntimeObservabilityLedger:
        return self.supervisor.observability_ledger


RuntimeTelemetryProvider = Callable[
    [CoordinatorTerminalRecord],
    RuntimeTelemetrySamples,
]
RuntimeWorkerBatchProvider = Callable[
    [Any],
    RuntimeWorkerBatchReceipt,
]
RuntimeFinalizationPersister = Callable[
    [RuntimeFinalizationReceipt],
    RuntimeFinalizationReceipt,
]


def accept_runtime_finalization_receipt(
    expected: RuntimeFinalizationReceipt,
    submitted: RuntimeFinalizationReceipt,
    *,
    existing: RuntimeFinalizationReceipt | None = None,
) -> RuntimeFinalizationReceipt:
    """Accept the persisted finalization; identical replay is idempotent."""

    if not isinstance(expected, RuntimeFinalizationReceipt):
        raise RuntimeFinalizationContractError(
            "expected must be a RuntimeFinalizationReceipt"
        )
    if not isinstance(submitted, RuntimeFinalizationReceipt) or submitted != expected:
        raise RuntimeFinalizationContractError(
            "persisted runtime finalization conflicts with the owner receipt"
        )
    if existing is None:
        return submitted
    if not isinstance(existing, RuntimeFinalizationReceipt) or existing != submitted:
        raise RuntimeFinalizationContractError(
            "conflicting runtime finalization receipt already exists"
        )
    return existing


def _validate_rss_sample(
    availability: RuntimeMetricAvailability,
    value: int | None,
    reason: str | None,
    *,
    field: str,
) -> None:
    if not isinstance(availability, RuntimeMetricAvailability):
        raise RuntimeFinalizationContractError(
            f"{field} availability must be RuntimeMetricAvailability"
        )
    if availability is RuntimeMetricAvailability.MEASURED:
        _nonnegative_integer(value, field=field)
        if reason is not None:
            raise RuntimeFinalizationContractError(
                f"measured {field} cannot carry a reason"
            )
        return
    if value is not None:
        raise RuntimeFinalizationContractError(
            f"unavailable {field} cannot carry a value"
        )
    _required_text(reason, field=f"{field}_reason")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RuntimeFinalizationContractError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _nonnegative_integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeFinalizationContractError(
            f"{field} must be a non-negative integer"
        )
    return value


def _require_tuple_of(value: object, item_type: type, *, field: str) -> None:
    if not isinstance(value, tuple) or any(
        not isinstance(item, item_type) for item in value
    ):
        raise RuntimeFinalizationContractError(
            f"{field} must be a tuple of {item_type.__name__} values"
        )


def _acquisition_receipts_match(
    first: RuntimeAcquisitionReceipt,
    second: RuntimeAcquisitionReceipt,
) -> bool:
    """Compare replayed runtime counts for the same acquisition capability."""

    return (
        first.work_unit_key == second.work_unit_key
        and first.acquisition_capability == second.acquisition_capability
        and derive_runtime_acquisition_metrics((first.acquisition,))
        == derive_runtime_acquisition_metrics((second.acquisition,))
    )


__all__ = (
    "RuntimeAcquisitionMetrics",
    "RuntimeAcquisitionReceipt",
    "RuntimeArtifactIoSample",
    "RuntimeCancellationRequest",
    "RuntimeExecutionPhase",
    "RuntimeExecutionPhaseError",
    "RuntimeFinalizationPersister",
    "RuntimeFinalizationReceipt",
    "RuntimeProcessTreePeakRssSample",
    "RuntimeRawOpenMetrics",
    "RuntimeResourceSettlementReceipt",
    "RuntimeTelemetryProvider",
    "RuntimeTelemetrySamples",
    "RuntimeWorkerBatchProvider",
    "RuntimeWorkerBatchReceipt",
    "RuntimeWorkerPeakRssSample",
    "accept_runtime_finalization_receipt",
    "compact_runtime_acquisition_receipt",
    "derive_runtime_acquisition_metrics",
    "derive_runtime_raw_open_metrics",
)
