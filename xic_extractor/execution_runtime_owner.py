"""Single construction and projection authority for one runtime attempt.

Workflow adapters translate their worker outputs into the typed receipts in
this module.  This owner applies the existing reducers, derives the complete
observability ledger from accepted facts plus narrow external telemetry
samples, and emits one content-bound finalization receipt.  It does not collect
live telemetry, choose storage, or acquire scientific/product authority.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import InitVar, dataclass, field, replace
from enum import Enum
from typing import final

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    TraceAcquisitionBatch,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptRuntime,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitSettlement,
    WorkUnitTerminalOutcome,
    _start_runtime_attempt,
    append_attempt_lifecycle_event,
    settle_work_unit,
)
from xic_extractor.execution_runtime import (
    mint_work_unit_settlement as _mint_work_unit_settlement,
)
from xic_extractor.execution_runtime_finalization import (
    AttemptFinalizationState,
    CoordinatorTerminalRecord,
    RuntimeArtifactDisposition,
    RuntimeFinalizationContractError,
    RuntimeLifecycleEventKind,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
    SupervisorFinalizationEventKind,
    SupervisorFinalizationRecord,
    accept_supervisor_finalization,
    begin_attempt_finalization,
    mint_supervisor_finalization,
    propose_attempt_terminal,
    record_runtime_resource_cleanup,
    record_runtime_resource_open,
    register_runtime_artifact,
    reserve_runtime_resource,
)
from xic_extractor.execution_runtime_observability import (
    RuntimeMetricAvailability,
    RuntimeMetricRecord,
    RuntimeMetricScope,
    RuntimeObservabilityLedger,
    RuntimeObservationEvent,
    RuntimeObservationSubject,
    _mint_runtime_observability_ledger,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    ProcessingAttemptIdentity,
)

_RUNTIME_OWNER_MEASUREMENT_SOURCE = "execution_runtime_owner:v1"
_RUNTIME_OWNER_MINT_TOKEN = object()


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
class RuntimeArtifactRegistrationReceipt:
    """One owner-minted artifact bound to its manifest work unit."""

    work_unit_key: str
    artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        if not isinstance(self.artifact, EvidenceArtifactRef):
            raise RuntimeFinalizationContractError(
                "artifact registration requires an owner-minted EvidenceArtifactRef"
            )


@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionReceipt:
    """Observed acquisition facts correlated to one submitted terminal result."""

    work_unit_key: str
    acquisition: TraceAcquisitionBatch

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        if not isinstance(self.acquisition, TraceAcquisitionBatch):
            raise RuntimeFinalizationContractError(
                "acquisition must be an owner-produced TraceAcquisitionBatch"
            )


@final
@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionMetrics:
    logical_xic_request_count: int
    xic_batch_count: int
    adapter_chromatogram_call_count: int
    trace_point_count: int
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeAcquisitionMetrics is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "runtime acquisition metrics must be minted by the runtime owner"
            )
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


def derive_runtime_acquisition_metrics(
    acquisitions: Sequence[TraceAcquisitionBatch],
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
    if any(not isinstance(item, TraceAcquisitionBatch) for item in batch):
        raise RuntimeFinalizationContractError(
            "acquisitions must contain TraceAcquisitionBatch receipts"
        )
    return RuntimeAcquisitionMetrics(
        logical_xic_request_count=sum(
            item.logical_xic_request_count for item in batch
        ),
        xic_batch_count=sum(item.xic_batch_count for item in batch),
        adapter_chromatogram_call_count=sum(
            item.adapter_chromatogram_call_count for item in batch
        ),
        trace_point_count=sum(item.trace_point_count for item in batch),
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


@final
@dataclass(frozen=True, slots=True)
class RuntimeRawOpenMetrics:
    """Runtime-owned projection for exactly one planned RAW-open session."""

    raw_open_count: int | None
    unavailable_reason: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeRawOpenMetrics is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "runtime RAW-open metrics must be minted by the runtime owner"
            )
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
    capability: RawAcquisitionSessionCapability | None,
    receipts: Sequence[RawAcquisitionSessionReceipt],
) -> RuntimeRawOpenMetrics:
    """Accept only the receipt bound to this exact planned RAW-open session."""

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
    if capability is None:
        if receipt_batch:
            raise RuntimeFinalizationContractError(
                "RAW-open receipt has no matching parent capability"
            )
        return RuntimeRawOpenMetrics(
            raw_open_count=None,
            unavailable_reason="RAW opener carries no owner session capability",
            _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
        )
    if not isinstance(capability, RawAcquisitionSessionCapability):
        raise RuntimeFinalizationContractError(
            "capability must be an owner-minted RAW session capability"
        )
    if not receipt_batch:
        return RuntimeRawOpenMetrics(
            raw_open_count=None,
            unavailable_reason="process result carries no owner RAW-open receipt",
            _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
        )
    if receipt_batch[0].capability != capability:
        raise RuntimeFinalizationContractError(
            "RAW-open receipt is bound to a different session capability"
        )
    return RuntimeRawOpenMetrics(
        raw_open_count=1,
        unavailable_reason="",
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


@dataclass(frozen=True, slots=True)
class RuntimeWorkerBatchReceipt:
    """Workflow-neutral closed facts returned from one adapter batch."""

    settlements: tuple[WorkUnitSettlement, ...]
    resource_settlements: tuple[RuntimeResourceSettlementReceipt, ...] = ()
    artifact_registrations: tuple[RuntimeArtifactRegistrationReceipt, ...] = ()
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
            self.artifact_registrations,
            RuntimeArtifactRegistrationReceipt,
            field="artifact_registrations",
        )
        _require_tuple_of(
            self.acquisitions,
            RuntimeAcquisitionReceipt,
            field="acquisitions",
        )


@final
@dataclass(frozen=True, slots=True)
class RuntimeAttemptAggregate:
    """The sole runtime-owned state carried across adapter boundaries."""

    state: AttemptFinalizationState
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...] = ()
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeAttemptAggregate is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "RuntimeAttemptAggregate must be minted by the runtime owner"
            )
        if not isinstance(self.state, AttemptFinalizationState):
            raise RuntimeFinalizationContractError(
                "aggregate state must be an AttemptFinalizationState"
            )
        _require_tuple_of(
            self.acquisitions,
            RuntimeAcquisitionReceipt,
            field="acquisitions",
        )
        manifest_keys = set(self.state.runtime.manifest.work_unit_keys)
        keys = tuple(item.work_unit_key for item in self.acquisitions)
        if len(keys) != len(set(keys)):
            raise RuntimeFinalizationContractError(
                "aggregate accepts at most one acquisition receipt per work unit"
            )
        if any(key not in manifest_keys for key in keys):
            raise RuntimeFinalizationContractError(
                "acquisition receipts must bind declared manifest work"
            )

    @property
    def runtime(self) -> AttemptRuntime:
        return self.state.runtime


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


class RuntimePeakFactOperation(str, Enum):
    """Finite peak-fact work vocabulary accepted by the runtime owner."""

    TRACE_EVALUATION = "trace_evaluation"
    GAUSSIAN_SMOOTHING = "gaussian_smoothing"
    INTEGRATION = "integration"


class RuntimePeakFactIdentityStatus(str, Enum):
    """Whether one work receipt carries the sealed Owner Backfill identity."""

    SEALED = "sealed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RuntimePeakFactWorkReceipt:
    """Bounded external work facts for one Owner Backfill candidate."""

    work_unit_key: str
    sample_stem: str
    operations: tuple[RuntimePeakFactOperation, ...]
    identity_status: RuntimePeakFactIdentityStatus
    owner_request_identity: str | None = None
    group_hypothesis_id: str | None = None
    acquisition_source: AcquisitionSourceIdentity | None = None
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        _required_text(self.sample_stem, field="sample_stem")
        _require_tuple_of(
            self.operations,
            RuntimePeakFactOperation,
            field="operations",
        )
        if not isinstance(self.identity_status, RuntimePeakFactIdentityStatus):
            raise RuntimeFinalizationContractError(
                "identity_status must be a RuntimePeakFactIdentityStatus"
            )
        if self.identity_status is RuntimePeakFactIdentityStatus.SEALED:
            _required_text(
                self.owner_request_identity,
                field="owner_request_identity",
            )
            _required_text(
                self.group_hypothesis_id,
                field="group_hypothesis_id",
            )
            if not isinstance(self.acquisition_source, AcquisitionSourceIdentity):
                raise RuntimeFinalizationContractError(
                    "sealed peak-fact work requires owner acquisition identity"
                )
        elif any(
            value is not None
            for value in (
                self.owner_request_identity,
                self.group_hypothesis_id,
                self.acquisition_source,
            )
        ):
            raise RuntimeFinalizationContractError(
                "unavailable peak-fact identity cannot carry partial owner binding"
            )
        trace_evaluations = self.operations.count(
            RuntimePeakFactOperation.TRACE_EVALUATION
        )
        if trace_evaluations > 1:
            raise RuntimeFinalizationContractError(
                "one peak-fact work receipt accepts at most one trace evaluation"
            )
        derived_work = any(
            operation
            in {
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                RuntimePeakFactOperation.INTEGRATION,
            }
            for operation in self.operations
        )
        if derived_work and trace_evaluations != 1:
            raise RuntimeFinalizationContractError(
                "smoothing and integration require one trace evaluation"
            )
        if (
            self.identity_status is RuntimePeakFactIdentityStatus.UNAVAILABLE
            and trace_evaluations != 1
        ):
            raise RuntimeFinalizationContractError(
                "unavailable peak-fact identity requires one trace evaluation"
            )
        if self.elapsed_seconds is None:
            return
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not math.isfinite(float(self.elapsed_seconds))
            or self.elapsed_seconds <= 0
        ):
            raise RuntimeFinalizationContractError(
                "elapsed_seconds must be finite and positive when available"
            )
        if not derived_work:
            raise RuntimeFinalizationContractError(
                "elapsed_seconds requires smoothing or integration work"
            )


@final
@dataclass(frozen=True, slots=True)
class RuntimePeakFactCostMetrics:
    """Runtime-owner projection used by the Owner Backfill shadow gate."""

    raw_open_count: int | None
    raw_open_unavailable_reason: str
    trace_evaluation_count: int
    smoothing_count: int
    integration_count: int
    artifact_round_trip_count: int | None
    artifact_round_trip_unavailable_reason: str
    sealed_observation_count: int
    identity_unavailable_count: int
    elapsed_seconds: float | None
    elapsed_unavailable_reason: str
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimePeakFactCostMetrics is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "peak-fact cost metrics must be minted by the runtime owner"
            )
        for field_name in (
            "trace_evaluation_count",
            "smoothing_count",
            "integration_count",
            "sealed_observation_count",
            "identity_unavailable_count",
        ):
            _nonnegative_integer(getattr(self, field_name), field=field_name)
        _validate_optional_count_projection(
            self.raw_open_count,
            self.raw_open_unavailable_reason,
            field="raw_open_count",
        )
        _validate_optional_count_projection(
            self.artifact_round_trip_count,
            self.artifact_round_trip_unavailable_reason,
            field="artifact_round_trip_count",
        )
        _validate_optional_positive_duration(
            self.elapsed_seconds,
            self.elapsed_unavailable_reason,
            field="elapsed_seconds",
        )

    @property
    def measurement_source(self) -> str:
        return _RUNTIME_OWNER_MEASUREMENT_SOURCE

    @property
    def count_scope(self) -> str:
        return "owner_backfill_worker_peak_fact_runtime"


def derive_runtime_peak_fact_cost_metrics(
    *,
    raw_session_capability: RawAcquisitionSessionCapability | None,
    raw_open_receipts: Sequence[RawAcquisitionSessionReceipt],
    work_receipts: Iterable[RuntimePeakFactWorkReceipt],
    artifact_io: RuntimeArtifactIoSample | None,
) -> RuntimePeakFactCostMetrics:
    """Derive the narrow Owner Backfill cost projection from typed facts.

    This helper does not settle work or replace ``execute_runtime_attempt``.  It
    exists only for the in-worker shadow comparison, where full Alignment
    lifecycle integration is not an activated public path.
    """

    raw_open_metrics = derive_runtime_raw_open_metrics(
        raw_session_capability,
        raw_open_receipts,
    )
    if isinstance(work_receipts, (str, bytes)):
        raise RuntimeFinalizationContractError(
            "work_receipts must contain RuntimePeakFactWorkReceipt values"
        )
    try:
        receipt_iterator = iter(work_receipts)
    except TypeError as exc:
        raise RuntimeFinalizationContractError(
            "work_receipts must contain RuntimePeakFactWorkReceipt values"
        ) from exc
    seen_work_unit_keys: set[str] = set()
    trace_evaluation_count = 0
    smoothing_count = 0
    integration_count = 0
    sealed_observation_count = 0
    identity_unavailable_count = 0
    fact_receipt_count = 0
    timed_fact_receipt_count = 0
    elapsed_total = 0.0
    for receipt in receipt_iterator:
        if not isinstance(receipt, RuntimePeakFactWorkReceipt):
            raise RuntimeFinalizationContractError(
                "work_receipts must contain RuntimePeakFactWorkReceipt values"
            )
        if receipt.work_unit_key in seen_work_unit_keys:
            raise RuntimeFinalizationContractError(
                "duplicate peak-fact work-unit keys are forbidden"
            )
        seen_work_unit_keys.add(receipt.work_unit_key)
        if (
            raw_session_capability is not None
            and receipt.identity_status is RuntimePeakFactIdentityStatus.SEALED
            and receipt.acquisition_source
            != raw_session_capability.acquisition_source
        ):
            raise RuntimeFinalizationContractError(
                "peak-fact work identity crosses the RAW session capability"
            )
        trace_evaluation_count += receipt.operations.count(
            RuntimePeakFactOperation.TRACE_EVALUATION
        )
        receipt_smoothing_count = receipt.operations.count(
            RuntimePeakFactOperation.GAUSSIAN_SMOOTHING
        )
        receipt_integration_count = receipt.operations.count(
            RuntimePeakFactOperation.INTEGRATION
        )
        smoothing_count += receipt_smoothing_count
        integration_count += receipt_integration_count
        sealed_observation_count += (
            receipt.identity_status is RuntimePeakFactIdentityStatus.SEALED
        )
        identity_unavailable_count += (
            receipt.identity_status is RuntimePeakFactIdentityStatus.UNAVAILABLE
        )
        if receipt_smoothing_count or receipt_integration_count:
            fact_receipt_count += 1
            if receipt.elapsed_seconds is not None:
                timed_fact_receipt_count += 1
                elapsed_total += float(receipt.elapsed_seconds)

    if fact_receipt_count == 0:
        elapsed_seconds = None
        elapsed_unavailable_reason = "no available peak-fact work observation"
    elif timed_fact_receipt_count != fact_receipt_count:
        elapsed_seconds = None
        elapsed_unavailable_reason = (
            "one or more peak-fact work receipts lack elapsed observation"
        )
    else:
        elapsed_seconds = elapsed_total
        elapsed_unavailable_reason = ""

    if artifact_io is None:
        artifact_round_trip_count = None
        artifact_round_trip_unavailable_reason = (
            "peak-fact stage carries no typed artifact-I/O telemetry"
        )
    elif not isinstance(artifact_io, RuntimeArtifactIoSample):
        raise RuntimeFinalizationContractError(
            "artifact_io must be a RuntimeArtifactIoSample when available"
        )
    elif (
        artifact_io.read_count == 0
        and artifact_io.read_bytes == 0
        and artifact_io.write_count == 0
        and artifact_io.write_bytes == 0
    ):
        artifact_round_trip_count = 0
        artifact_round_trip_unavailable_reason = ""
    else:
        artifact_round_trip_count = None
        artifact_round_trip_unavailable_reason = (
            "aggregate artifact I/O does not prove correlated round trips"
        )

    return RuntimePeakFactCostMetrics(
        raw_open_count=raw_open_metrics.raw_open_count,
        raw_open_unavailable_reason=raw_open_metrics.unavailable_reason,
        trace_evaluation_count=trace_evaluation_count,
        smoothing_count=smoothing_count,
        integration_count=integration_count,
        artifact_round_trip_count=artifact_round_trip_count,
        artifact_round_trip_unavailable_reason=(
            artifact_round_trip_unavailable_reason
        ),
        sealed_observation_count=sealed_observation_count,
        identity_unavailable_count=identity_unavailable_count,
        elapsed_seconds=elapsed_seconds,
        elapsed_unavailable_reason=elapsed_unavailable_reason,
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


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

    event_monotonic_offsets_ns: tuple[int, ...]
    artifact_io: RuntimeArtifactIoSample
    process_tree_peak_rss: RuntimeProcessTreePeakRssSample
    worker_peak_rss: tuple[RuntimeWorkerPeakRssSample, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.event_monotonic_offsets_ns, tuple) or any(
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 0
            for offset in self.event_monotonic_offsets_ns
        ):
            raise RuntimeFinalizationContractError(
                "event_monotonic_offsets_ns must contain non-negative integers"
            )
        if not isinstance(self.artifact_io, RuntimeArtifactIoSample):
            raise RuntimeFinalizationContractError(
                "artifact_io must be a RuntimeArtifactIoSample"
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


@final
@dataclass(frozen=True, slots=True)
class RuntimeFinalizationReceipt:
    """One accepted pair of coordinator and supervisor terminal facts."""

    coordinator: CoordinatorTerminalRecord
    supervisor: SupervisorFinalizationRecord
    _mint_token: InitVar[object] = field(kw_only=True)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("RuntimeFinalizationReceipt is final")

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _RUNTIME_OWNER_MINT_TOKEN:
            raise RuntimeFinalizationContractError(
                "RuntimeFinalizationReceipt must be minted by the runtime owner"
            )
        if not isinstance(self.coordinator, CoordinatorTerminalRecord):
            raise RuntimeFinalizationContractError(
                "coordinator must be a CoordinatorTerminalRecord"
            )
        if not isinstance(self.supervisor, SupervisorFinalizationRecord):
            raise RuntimeFinalizationContractError(
                "supervisor must be a SupervisorFinalizationRecord"
            )
        accept_supervisor_finalization(
            self.coordinator,
            self.supervisor,
            observability_ledger=self.supervisor.observability_ledger,
        )

    @property
    def observability_ledger(self) -> RuntimeObservabilityLedger:
        return self.supervisor.observability_ledger


RuntimeTelemetryProvider = Callable[
    [CoordinatorTerminalRecord],
    RuntimeTelemetrySamples,
]
RuntimeWorkerBatchProvider = Callable[
    [RuntimeAttemptAggregate],
    RuntimeWorkerBatchReceipt,
]
RuntimeFinalizationPersister = Callable[
    [RuntimeFinalizationReceipt],
    RuntimeFinalizationReceipt,
]


def _mint_runtime_attempt_aggregate(
    *,
    state: AttemptFinalizationState,
    acquisitions: tuple[RuntimeAcquisitionReceipt, ...] = (),
) -> RuntimeAttemptAggregate:
    return RuntimeAttemptAggregate(
        state=state,
        acquisitions=acquisitions,
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


def _mint_runtime_finalization_receipt(
    *,
    coordinator: CoordinatorTerminalRecord,
    supervisor: SupervisorFinalizationRecord,
) -> RuntimeFinalizationReceipt:
    return RuntimeFinalizationReceipt(
        coordinator=coordinator,
        supervisor=supervisor,
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


def start_runtime_attempt(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    owner_completeness_attestation: str | None,
    cancellation_request: RuntimeCancellationRequest | None = None,
) -> AttemptRuntime:
    """Create one running, cancelled, or preflight-terminalized owner state."""

    if cancellation_request is not None:
        if not isinstance(cancellation_request, RuntimeCancellationRequest):
            raise RuntimeFinalizationContractError(
                "cancellation_request must be a RuntimeCancellationRequest or None"
            )
        if cancellation_request.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "cancellation request references a different runtime attempt"
            )
    runtime = _start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=owner_completeness_attestation,
    )
    if cancellation_request is None or runtime.manifest_preflight_failed:
        return runtime
    return replace(
        runtime,
        history=append_attempt_lifecycle_event(
            runtime.history,
            AttemptLifecycleEventKind.CANCELLATION_ACCEPTED,
        ),
    )


def mint_work_unit_settlement(
    *,
    attempt: ProcessingAttemptIdentity,
    work_unit_key: str,
    outcome: WorkUnitTerminalOutcome,
    owner_result_ref: EvidenceArtifactRef | None = None,
    failure_detail: WorkUnitFailureDetail | None = None,
) -> WorkUnitSettlement:
    """Mint one typed work settlement through the runtime authority facade."""

    return _mint_work_unit_settlement(
        attempt=attempt,
        work_unit_key=work_unit_key,
        outcome=outcome,
        owner_result_ref=owner_result_ref,
        failure_detail=failure_detail,
    )


def _prepare_runtime_attempt(
    runtime: AttemptRuntime,
    reservations: Sequence[RuntimeResourceReservation],
) -> RuntimeAttemptAggregate:
    """Create the one aggregate and reserve resources before dispatch."""

    if not isinstance(runtime, AttemptRuntime):
        raise RuntimeFinalizationContractError("runtime must be an AttemptRuntime")
    state = begin_attempt_finalization(runtime)
    if runtime.manifest_preflight_failed:
        return _mint_runtime_attempt_aggregate(state=state)
    if isinstance(reservations, (str, bytes)):
        raise RuntimeFinalizationContractError(
            "reservations must contain RuntimeResourceReservation values"
        )
    try:
        batch = tuple(reservations)
    except TypeError as exc:
        raise RuntimeFinalizationContractError(
            "reservations must contain RuntimeResourceReservation values"
        ) from exc
    for reservation in batch:
        if not isinstance(reservation, RuntimeResourceReservation):
            raise RuntimeFinalizationContractError(
                "reservations must contain RuntimeResourceReservation values"
            )
        state = reserve_runtime_resource(state, reservation)
    return _mint_runtime_attempt_aggregate(state=state)


def _apply_runtime_worker_batch(
    aggregate: RuntimeAttemptAggregate,
    batch: RuntimeWorkerBatchReceipt,
) -> RuntimeAttemptAggregate:
    """Apply one translated batch in owner-defined canonical order."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not isinstance(batch, RuntimeWorkerBatchReceipt):
        raise RuntimeFinalizationContractError(
            "batch must be a RuntimeWorkerBatchReceipt"
        )
    if aggregate.runtime.manifest_preflight_failed:
        raise RuntimeFinalizationContractError(
            "preflight-terminalized attempt cannot accept worker facts"
        )
    state = aggregate.state
    attempt = state.runtime.manifest.attempt
    manifest_order = {
        key: index for index, key in enumerate(state.runtime.manifest.work_unit_keys)
    }
    resource_order = {
        record.reservation.resource_key: index
        for index, record in enumerate(state.resources)
    }
    resource_settlements = tuple(
        sorted(
            batch.resource_settlements,
            key=lambda item: resource_order.get(
                item.resource_key,
                len(resource_order),
            ),
        )
    )
    for resource_receipt in resource_settlements:
        if resource_receipt.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "resource receipt references a different runtime attempt"
            )
    for resource_receipt in resource_settlements:
        state = record_runtime_resource_open(
            state,
            resource_key=resource_receipt.resource_key,
            outcome=resource_receipt.open_outcome,
        )

    for settlement in sorted(
        batch.settlements,
        key=lambda item: manifest_order.get(item.work_unit_key, len(manifest_order)),
    ):
        if settlement.attempt != attempt:
            raise RuntimeFinalizationContractError(
                "work settlement references a different runtime attempt"
            )
        runtime = settle_work_unit(
            state.runtime,
            work_unit_key=settlement.work_unit_key,
            outcome=settlement.outcome,
            owner_result_ref=settlement.owner_result_ref,
            failure_detail=settlement.failure_detail,
        )
        state = replace(state, runtime=runtime)

    for registration in sorted(
        batch.artifact_registrations,
        key=lambda item: (
            manifest_order.get(item.work_unit_key, len(manifest_order)),
            item.artifact.evidence_artifact_id,
        ),
    ):
        state = register_runtime_artifact(
            state,
            work_unit_key=registration.work_unit_key,
            artifact=registration.artifact,
        )

    for resource_receipt in resource_settlements:
        state = record_runtime_resource_cleanup(
            state,
            resource_key=resource_receipt.resource_key,
            outcome=resource_receipt.cleanup_outcome,
        )

    accepted_acquisitions = {
        item.work_unit_key: item for item in aggregate.acquisitions
    }
    settlements_by_key = {
        item.work_unit_key: item for item in state.runtime.settlements
    }
    submitted_work_keys = {
        item.work_unit_key
        for item in batch.settlements
    }
    for acquisition_receipt in sorted(
        batch.acquisitions,
        key=lambda item: manifest_order.get(item.work_unit_key, len(manifest_order)),
    ):
        accepted_settlement = settlements_by_key.get(acquisition_receipt.work_unit_key)
        if (
            accepted_settlement is None
            or acquisition_receipt.work_unit_key not in submitted_work_keys
        ):
            raise RuntimeFinalizationContractError(
                "acquisition receipt requires a submitted terminal work result"
            )
        existing = accepted_acquisitions.get(acquisition_receipt.work_unit_key)
        if existing is not None and not _acquisition_receipts_match(
            existing,
            acquisition_receipt,
        ):
            raise RuntimeFinalizationContractError(
                "conflicting acquisition receipt for one work unit"
            )
        accepted_acquisitions[acquisition_receipt.work_unit_key] = acquisition_receipt

    return _mint_runtime_attempt_aggregate(
        state=state,
        acquisitions=tuple(
            accepted_acquisitions[key]
            for key in state.runtime.manifest.work_unit_keys
            if key in accepted_acquisitions
        ),
    )


def _finalize_runtime_attempt(
    aggregate: RuntimeAttemptAggregate,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    persister: RuntimeFinalizationPersister,
) -> RuntimeFinalizationReceipt:
    """Derive, persist, and accept one canonical terminal receipt."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not callable(telemetry_provider):
        raise RuntimeFinalizationContractError("telemetry_provider must be callable")
    if not callable(persister):
        raise RuntimeFinalizationContractError("persister must be callable")
    coordinator = propose_attempt_terminal(aggregate.state)
    terminal_aggregate = _mint_runtime_attempt_aggregate(
        state=coordinator.state,
        acquisitions=aggregate.acquisitions,
    )
    telemetry = telemetry_provider(coordinator)
    ledger = _derive_runtime_observability_ledger(
        terminal_aggregate,
        coordinator,
        telemetry=telemetry,
    )
    supervisor = mint_supervisor_finalization(
        coordinator,
        observability_ledger=ledger,
    )
    proposed = _mint_runtime_finalization_receipt(
        coordinator=coordinator,
        supervisor=supervisor,
    )
    submitted = persister(proposed)
    return accept_runtime_finalization_receipt(proposed, submitted)


def execute_runtime_attempt(
    runtime: AttemptRuntime,
    reservations: Sequence[RuntimeResourceReservation],
    *,
    worker_batch_provider: RuntimeWorkerBatchProvider,
    telemetry_provider: RuntimeTelemetryProvider,
    persister: RuntimeFinalizationPersister,
) -> RuntimeFinalizationReceipt:
    """Prepare, optionally dispatch, settle, and finalize one runtime attempt.

    Rejected manifest preflight remains a first-class owner state: the worker
    provider is not invoked, no resources are reserved, and the same terminal
    receipt/persistence path closes the attempt. Accepted pre-dispatch
    cancellation likewise skips the worker provider; the owner settles every
    manifest work unit as cancelled and closes reserved resources as not
    opened. Consumers therefore cannot split preparation or settlement from
    finalization.
    """

    if not callable(worker_batch_provider):
        raise RuntimeFinalizationContractError("worker_batch_provider must be callable")
    aggregate = _prepare_runtime_attempt(runtime, reservations)
    if aggregate.runtime.history.cancellation_accepted:
        attempt = aggregate.runtime.manifest.attempt
        aggregate = _apply_runtime_worker_batch(
            aggregate,
            RuntimeWorkerBatchReceipt(
                settlements=tuple(
                    mint_work_unit_settlement(
                        attempt=attempt,
                        work_unit_key=work_unit_key,
                        outcome=WorkUnitTerminalOutcome.CANCELLED,
                        failure_detail=WorkUnitFailureDetail(
                            category=WorkUnitFailureCategory.CANCELLATION,
                            stage="runtime-owner-pre-dispatch",
                            source_reference=(
                                f"runtime-owner:{attempt.processing_attempt_id}"
                            ),
                            message=(
                                "attempt cancellation accepted before worker dispatch"
                            ),
                        ),
                    )
                    for work_unit_key in aggregate.runtime.manifest.work_unit_keys
                ),
                resource_settlements=tuple(
                    RuntimeResourceSettlementReceipt(
                        attempt=attempt,
                        resource_key=record.reservation.resource_key,
                        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
                        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
                    )
                    for record in aggregate.state.resources
                ),
            ),
        )
    elif not aggregate.runtime.manifest_preflight_failed:
        aggregate = _apply_runtime_worker_batch(
            aggregate,
            worker_batch_provider(aggregate),
        )
    return _finalize_runtime_attempt(
        aggregate,
        telemetry_provider=telemetry_provider,
        persister=persister,
    )


def accept_runtime_finalization_receipt(
    expected: RuntimeFinalizationReceipt,
    submitted: RuntimeFinalizationReceipt,
    *,
    existing: RuntimeFinalizationReceipt | None = None,
) -> RuntimeFinalizationReceipt:
    """Accept only the owner-minted receipt; identical replay is idempotent."""

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


def _derive_runtime_observability_ledger(
    aggregate: RuntimeAttemptAggregate,
    coordinator: CoordinatorTerminalRecord,
    *,
    telemetry: RuntimeTelemetrySamples,
) -> RuntimeObservabilityLedger:
    """Project owner facts and narrow telemetry into observability v1."""

    if not isinstance(aggregate, RuntimeAttemptAggregate):
        raise RuntimeFinalizationContractError(
            "aggregate must be a RuntimeAttemptAggregate"
        )
    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    if coordinator.state != aggregate.state:
        raise RuntimeFinalizationContractError(
            "coordinator must bind the exact runtime aggregate state"
        )
    acquisition_batch = aggregate.acquisitions
    if not isinstance(telemetry, RuntimeTelemetrySamples):
        raise RuntimeFinalizationContractError(
            "telemetry must be RuntimeTelemetrySamples"
        )
    state = coordinator.state
    attempt = state.runtime.manifest.attempt
    shapes = _runtime_observation_shapes(coordinator)
    offsets = telemetry.event_monotonic_offsets_ns
    if len(offsets) != len(shapes):
        raise RuntimeFinalizationContractError(
            "telemetry offsets must match the owner-derived event count"
        )
    if not offsets or offsets[0] != 0 or offsets != tuple(sorted(offsets)):
        raise RuntimeFinalizationContractError(
            "telemetry offsets must start at zero and be monotonic"
        )
    events = tuple(
        RuntimeObservationEvent(
            attempt=attempt,
            sequence=index,
            monotonic_offset_ns=offset,
            kind=kind,
            subject=subject,
            subject_key=subject_key,
        )
        for index, ((kind, subject, subject_key), offset) in enumerate(
            zip(shapes, offsets, strict=True),
            start=1,
        )
    )
    worker_keys = _started_worker_keys(state)
    supplied_worker_keys = tuple(
        sample.worker_key for sample in telemetry.worker_peak_rss
    )
    if set(supplied_worker_keys) != set(worker_keys):
        raise RuntimeFinalizationContractError(
            "worker RSS samples must exactly match owner-derived worker keys"
        )
    worker_samples = {sample.worker_key: sample for sample in telemetry.worker_peak_rss}
    acquisition_metrics = derive_runtime_acquisition_metrics(
        tuple(item.acquisition for item in acquisition_batch)
    )
    acquisition_counts = {
        name: getattr(acquisition_metrics, name)
        for name in (
            "logical_xic_request_count",
            "xic_batch_count",
            "adapter_chromatogram_call_count",
            "trace_point_count",
        )
    }
    count_values = {
        "raw_open_count": sum(
            record.reservation.kind is RuntimeResourceKind.ACQUISITION_SESSION
            and record.open_outcome is RuntimeResourceOpenOutcome.OPENED
            for record in state.resources
        ),
        **acquisition_counts,
        "artifact_read_count": telemetry.artifact_io.read_count,
        "artifact_read_bytes": telemetry.artifact_io.read_bytes,
        "artifact_write_count": telemetry.artifact_io.write_count,
        "artifact_write_bytes": telemetry.artifact_io.write_bytes,
    }
    if state.runtime.manifest_preflight_failed and any(count_values.values()):
        raise RuntimeFinalizationContractError(
            "manifest-preflight failure cannot report runtime work or I/O"
        )
    metrics: list[RuntimeMetricRecord] = []
    for name in (
        "raw_open_count",
        "logical_xic_request_count",
        "xic_batch_count",
        "adapter_chromatogram_call_count",
        "trace_point_count",
        "artifact_read_count",
        "artifact_read_bytes",
        "artifact_write_count",
        "artifact_write_bytes",
    ):
        metrics.append(
            _measured_metric(
                name=name,
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                unit="bytes" if name.endswith("_bytes") else "count",
                aggregation="sum",
                value=count_values[name],
            )
        )
    if state.runtime.manifest_preflight_failed:
        metrics.extend(
            _preflight_terminal_metrics(
                attempt=attempt,
                events=events,
            )
        )
        return _mint_runtime_observability_ledger(
            attempt=attempt,
            work_unit_keys=(),
            events=events,
            metrics=tuple(metrics),
        )
    metrics.extend(
        (
            _measured_metric(
                name="coordinator_work_seconds",
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                unit="seconds",
                aggregation="last",
                value=(
                    _event_offset(events, "terminal_proposal")
                    - _event_offset(events, "attempt_started")
                )
                / 1_000_000_000,
            ),
            _measured_metric(
                name="attempt_wall_seconds",
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                unit="seconds",
                aggregation="last",
                value=(
                    _event_offset(events, "terminal_outcome")
                    - _event_offset(events, "attempt_queued")
                )
                / 1_000_000_000,
            ),
        )
    )
    metrics.extend(
        _measured_metric(
            name="stage_or_unit_elapsed_seconds",
            scope=RuntimeMetricScope.WORK_UNIT,
            scope_key=work_unit_key,
            unit="seconds",
            aggregation="sum",
            value=(
                _event_offset(events, "work_unit_settled", work_unit_key)
                - _event_offset(events, "work_unit_started", work_unit_key)
            )
            / 1_000_000_000,
        )
        for work_unit_key in state.runtime.manifest.work_unit_keys
    )
    if worker_keys:
        metrics.extend(
            _rss_metric(
                name="worker_peak_rss_bytes",
                scope=RuntimeMetricScope.WORKER,
                scope_key=worker_key,
                availability=worker_samples[worker_key].availability,
                value=worker_samples[worker_key].value,
                reason=worker_samples[worker_key].reason,
            )
            for worker_key in worker_keys
        )
    else:
        metrics.append(
            _rss_metric(
                name="worker_peak_rss_bytes",
                scope=RuntimeMetricScope.ATTEMPT,
                scope_key=attempt.processing_attempt_id,
                availability=RuntimeMetricAvailability.UNSUPPORTED,
                value=None,
                reason="not_applicable_serial",
            )
        )
    metrics.append(
        _rss_metric(
            name="process_tree_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt.processing_attempt_id,
            availability=telemetry.process_tree_peak_rss.availability,
            value=telemetry.process_tree_peak_rss.value,
            reason=telemetry.process_tree_peak_rss.reason,
        )
    )
    return _mint_runtime_observability_ledger(
        attempt=attempt,
        work_unit_keys=state.runtime.manifest.work_unit_keys,
        events=events,
        metrics=tuple(metrics),
    )


def runtime_observation_event_count(
    coordinator: CoordinatorTerminalRecord,
) -> int:
    """Return the owner-derived event count expected from a telemetry provider."""

    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    return len(_runtime_observation_shapes(coordinator))


def runtime_started_worker_keys(
    coordinator: CoordinatorTerminalRecord,
) -> tuple[str, ...]:
    """Return worker identities proven started by finalized process resources."""

    if not isinstance(coordinator, CoordinatorTerminalRecord):
        raise RuntimeFinalizationContractError(
            "coordinator must be a CoordinatorTerminalRecord"
        )
    return _started_worker_keys(coordinator.state)


def _runtime_observation_shapes(
    coordinator: CoordinatorTerminalRecord,
) -> tuple[tuple[str, RuntimeObservationSubject, str], ...]:
    state = coordinator.state
    shapes: list[tuple[str, RuntimeObservationSubject, str]] = []

    def append_lifecycle_phase(
        kinds: frozenset[RuntimeLifecycleEventKind],
        subject: RuntimeObservationSubject,
    ) -> None:
        for lifecycle_event in state.events:
            if lifecycle_event.kind not in kinds:
                continue
            if subject is RuntimeObservationSubject.ATTEMPT:
                subject_key = "attempt"
            else:
                lifecycle_subject_key = lifecycle_event.subject_key
                if lifecycle_subject_key is None:
                    raise RuntimeFinalizationContractError(
                        f"{subject.value} event is missing its owner correlation"
                    )
                subject_key = lifecycle_subject_key
            shapes.append((lifecycle_event.kind.value, subject, subject_key))

    for event in state.runtime.history.events:
        shapes.append((event.kind.value, RuntimeObservationSubject.ATTEMPT, "attempt"))
        if event.sequence == 1 and not state.runtime.manifest_preflight_failed:
            shapes.append(
                ("manifest_closed", RuntimeObservationSubject.ATTEMPT, "manifest")
            )
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.RESOURCE_RESERVED}),
        RuntimeObservationSubject.RESOURCE,
    )
    append_lifecycle_phase(
        frozenset(
            {
                RuntimeLifecycleEventKind.RESOURCE_OPENED,
                RuntimeLifecycleEventKind.RESOURCE_OPEN_FAILED,
                RuntimeLifecycleEventKind.RESOURCE_NOT_OPENED,
                RuntimeLifecycleEventKind.RESOURCE_CONDITION_NOT_ACTIVATED,
                RuntimeLifecycleEventKind.RESOURCE_OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
            }
        ),
        RuntimeObservationSubject.RESOURCE,
    )
    for worker_key in _started_worker_keys(state):
        shapes.append(("worker_started", RuntimeObservationSubject.WORKER, worker_key))
    for work_unit_key in state.runtime.manifest.work_unit_keys:
        shapes.extend(
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
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.ARTIFACT_REGISTERED}),
        RuntimeObservationSubject.ARTIFACT,
    )
    append_lifecycle_phase(
        frozenset(
            {
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_SUCCEEDED,
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_FAILED,
                RuntimeLifecycleEventKind.RESOURCE_CLEANUP_UNKNOWN_AFTER_WORKER_LOSS,
            }
        ),
        RuntimeObservationSubject.RESOURCE,
    )
    append_lifecycle_phase(
        frozenset({RuntimeLifecycleEventKind.TERMINAL_PROPOSAL}),
        RuntimeObservationSubject.ATTEMPT,
    )
    shapes.append(
        (
            SupervisorFinalizationEventKind.TERMINAL_ACKNOWLEDGED.value,
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    )
    for artifact in state.artifacts:
        kind = (
            SupervisorFinalizationEventKind.ARTIFACT_OFFERED_TO_OWNER
            if artifact.proposed_disposition
            is RuntimeArtifactDisposition.OFFERED_TO_OWNER
            else SupervisorFinalizationEventKind.ARTIFACT_QUARANTINED_DIAGNOSTIC
        )
        shapes.append(
            (
                kind.value,
                RuntimeObservationSubject.ARTIFACT,
                artifact.artifact.evidence_artifact_id,
            )
        )
    shapes.append(
        (
            SupervisorFinalizationEventKind.TERMINAL_OUTCOME.value,
            RuntimeObservationSubject.ATTEMPT,
            "attempt",
        )
    )
    return tuple(shapes)


def _started_worker_keys(state: AttemptFinalizationState) -> tuple[str, ...]:
    started_outcomes = {
        RuntimeResourceOpenOutcome.OPENED,
        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS,
    }
    return tuple(
        dict.fromkeys(
            record.reservation.worker_key
            for record in state.resources
            if record.reservation.kind is RuntimeResourceKind.PROCESS_WORKER
            and record.open_outcome in started_outcomes
        )
    )


def _preflight_terminal_metrics(
    *,
    attempt: ProcessingAttemptIdentity,
    events: tuple[RuntimeObservationEvent, ...],
) -> tuple[RuntimeMetricRecord, ...]:
    attempt_id = attempt.processing_attempt_id
    unavailable_reason = "not_started_preflight_failure"
    return (
        _unsupported_metric(
            name="coordinator_work_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="last",
            reason=unavailable_reason,
        ),
        _measured_metric(
            name="attempt_wall_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="last",
            value=(
                _event_offset(events, "terminal_outcome")
                - _event_offset(events, "attempt_queued")
            )
            / 1_000_000_000,
        ),
        _unsupported_metric(
            name="stage_or_unit_elapsed_seconds",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="seconds",
            aggregation="sum",
            reason=unavailable_reason,
        ),
        _unsupported_metric(
            name="worker_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="bytes",
            aggregation="max",
            reason=unavailable_reason,
        ),
        _unsupported_metric(
            name="process_tree_peak_rss_bytes",
            scope=RuntimeMetricScope.ATTEMPT,
            scope_key=attempt_id,
            unit="bytes",
            aggregation="max",
            reason=unavailable_reason,
        ),
    )


def _event_offset(
    events: tuple[RuntimeObservationEvent, ...],
    kind: str,
    subject_key: str = "attempt",
) -> int:
    matches = tuple(
        event.monotonic_offset_ns
        for event in events
        if event.kind == kind and event.subject_key == subject_key
    )
    if len(matches) != 1:
        raise RuntimeFinalizationContractError(
            "owner-derived elapsed metric lacks one correlated event"
        )
    return matches[0]


def _measured_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    unit: str,
    aggregation: str,
    value: int | float,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit=unit,
        aggregation=aggregation,
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=RuntimeMetricAvailability.MEASURED,
        value=value,
    )


def _rss_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    availability: RuntimeMetricAvailability,
    value: int | None,
    reason: str | None,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit="bytes",
        aggregation="max",
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=availability,
        value=value,
        reason=reason,
    )


def _unsupported_metric(
    *,
    name: str,
    scope: RuntimeMetricScope,
    scope_key: str,
    unit: str,
    aggregation: str,
    reason: str,
) -> RuntimeMetricRecord:
    return RuntimeMetricRecord(
        name=name,
        scope=scope,
        scope_key=scope_key,
        unit=unit,
        aggregation=aggregation,
        measurement_source=_RUNTIME_OWNER_MEASUREMENT_SOURCE,
        availability=RuntimeMetricAvailability.UNSUPPORTED,
        value=None,
        reason=reason,
    )


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


def _validate_optional_count_projection(
    value: int | None,
    unavailable_reason: str,
    *,
    field: str,
) -> None:
    if value is None:
        _required_text(unavailable_reason, field=f"{field}_unavailable_reason")
        return
    _nonnegative_integer(value, field=field)
    if unavailable_reason:
        raise RuntimeFinalizationContractError(
            f"measured {field} cannot carry an unavailable reason"
        )


def _validate_optional_positive_duration(
    value: float | None,
    unavailable_reason: str,
    *,
    field: str,
) -> None:
    if value is None:
        _required_text(unavailable_reason, field=f"{field}_unavailable_reason")
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise RuntimeFinalizationContractError(
            f"{field} must be finite and positive when available"
        )
    if unavailable_reason:
        raise RuntimeFinalizationContractError(
            f"measured {field} cannot carry an unavailable reason"
        )


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
    """Compare acquisition truth without NumPy's ambiguous array equality."""

    if first.work_unit_key != second.work_unit_key:
        return False
    left = first.acquisition
    right = second.acquisition
    if (
        left.capability != right.capability
        or left.logical_xic_request_count != right.logical_xic_request_count
        or left.xic_batch_count != right.xic_batch_count
        or left.adapter_chromatogram_call_count != right.adapter_chromatogram_call_count
        or left.trace_point_count != right.trace_point_count
        or len(left.traces) != len(right.traces)
    ):
        return False
    return all(
        left_trace.coordinate == right_trace.coordinate
        and tuple(float(value) for value in left_trace.trace.rt)
        == tuple(float(value) for value in right_trace.trace.rt)
        and tuple(float(value) for value in left_trace.trace.intensity)
        == tuple(float(value) for value in right_trace.trace.intensity)
        for left_trace, right_trace in zip(left.traces, right.traces, strict=True)
    )


__all__ = [
    "RuntimeAcquisitionMetrics",
    "RuntimeAcquisitionReceipt",
    "RuntimeArtifactIoSample",
    "RuntimeArtifactRegistrationReceipt",
    "RuntimeAttemptAggregate",
    "RuntimeCancellationRequest",
    "RuntimeFinalizationPersister",
    "RuntimeFinalizationReceipt",
    "RuntimePeakFactCostMetrics",
    "RuntimePeakFactIdentityStatus",
    "RuntimePeakFactOperation",
    "RuntimePeakFactWorkReceipt",
    "RuntimeProcessTreePeakRssSample",
    "RuntimeRawOpenMetrics",
    "RuntimeResourceSettlementReceipt",
    "RuntimeTelemetryProvider",
    "RuntimeTelemetrySamples",
    "RuntimeWorkerBatchReceipt",
    "RuntimeWorkerBatchProvider",
    "RuntimeWorkerPeakRssSample",
    "accept_runtime_finalization_receipt",
    "derive_runtime_acquisition_metrics",
    "derive_runtime_peak_fact_cost_metrics",
    "derive_runtime_raw_open_metrics",
    "execute_runtime_attempt",
    "mint_work_unit_settlement",
    "runtime_observation_event_count",
    "runtime_started_worker_keys",
    "start_runtime_attempt",
]
