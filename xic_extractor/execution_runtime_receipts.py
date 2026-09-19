"""Immutable receipts, metrics, and sampling contracts for runtime execution."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import final

from xic_extractor.acquisition_contract import (
    RawAcquisitionSessionCapability,
    RawAcquisitionSessionReceipt,
    TraceAcquisitionBatch,
    acquired_trace_content_id,
)
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import WorkUnitSettlement
from xic_extractor.execution_runtime_finalization import (
    CoordinatorTerminalRecord,
    RuntimeFinalizationContractError,
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    SupervisorFinalizationRecord,
    accept_supervisor_finalization,
)
from xic_extractor.execution_runtime_observability import (
    RuntimeMetricAvailability,
    RuntimeObservabilityLedger,
)
from xic_extractor.processing_identity import (
    AcquisitionSourceIdentity,
    ProcessingAttemptIdentity,
)

_RUNTIME_OWNER_MEASUREMENT_SOURCE = "execution_runtime_owner:v1"
_RUNTIME_OWNER_MINT_TOKEN = object()


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


@dataclass(frozen=True, slots=True)
class RuntimeAcquisitionReceipt:
    """Observed acquisition facts correlated to one submitted terminal result."""

    work_unit_key: str
    acquisition: TraceAcquisitionBatch | RuntimeAcquisitionMetrics
    _acquisition_digest: str | None = field(default=None, repr=False, kw_only=True)
    _mint_token: InitVar[object | None] = field(default=None, kw_only=True)

    def __post_init__(self, _mint_token: object | None) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        if type(self.acquisition) not in (
            TraceAcquisitionBatch,
            RuntimeAcquisitionMetrics,
        ):
            raise RuntimeFinalizationContractError(
                "acquisition must be an owner-produced TraceAcquisitionBatch or metrics"
            )
        if isinstance(self.acquisition, RuntimeAcquisitionMetrics):
            if (
                _mint_token is not _RUNTIME_OWNER_MINT_TOKEN
                or not self._acquisition_digest
            ):
                raise RuntimeFinalizationContractError(
                    "compact acquisition receipts must be minted by the runtime owner"
                )
        elif self._acquisition_digest is not None:
            raise RuntimeFinalizationContractError(
                "full acquisition receipts cannot carry a compact digest"
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
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
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
        _acquisition_digest=_runtime_acquisition_digest(receipt.acquisition),
        _mint_token=_RUNTIME_OWNER_MINT_TOKEN,
    )


def _runtime_acquisition_digest(acquisition: TraceAcquisitionBatch) -> str:
    # Batch has no cached content identity. Reuse the acquisition owner's exact
    # trace identity codec and include the remaining replay-comparison facts.
    digest = hashlib.sha256()
    digest.update(
        repr(
            (
                acquisition.capability,
                acquisition.logical_xic_request_count,
                acquisition.xic_batch_count,
                acquisition.adapter_chromatogram_call_count,
                acquisition.trace_point_count,
                len(acquisition.traces),
            )
        ).encode("utf-8")
    )
    for trace in acquisition.traces:
        digest.update(acquired_trace_content_id(trace).encode("ascii"))
    return digest.hexdigest()


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
    ASLS_BASELINE = "asls_baseline"
    GAUSSIAN_SMOOTHING = "gaussian_smoothing"
    SEGMENT_ENUMERATION = "segment_enumeration"
    INTEGRATION = "integration"
    NORMALIZATION = "normalization"
    RAW_DESCRIPTOR_PROJECTION = "raw_descriptor_projection"
    REGION_AUDIT_PROJECTION = "region_audit_projection"


_RUNTIME_PEAK_FACT_OPERATION_ORDER = tuple(RuntimePeakFactOperation)
_RUNTIME_PREPARATION_OPERATIONS = frozenset(
    {
        RuntimePeakFactOperation.TRACE_EVALUATION,
        RuntimePeakFactOperation.ASLS_BASELINE,
        RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
    }
)
_RUNTIME_PEAK_FACT_OPERATION_SEQUENCES = tuple(
    _RUNTIME_PEAK_FACT_OPERATION_ORDER[:count] for count in (0, 1, 2, 3, 4, 7, 8)
)
_LEGACY_PEAK_FACT_OPERATION_SEQUENCE = (
    RuntimePeakFactOperation.TRACE_EVALUATION,
    RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
    RuntimePeakFactOperation.INTEGRATION,
)


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
    prepared_signal_id: str | None = None
    elapsed_seconds: float | None = None

    def __post_init__(self) -> None:
        _required_text(self.work_unit_key, field="work_unit_key")
        _required_text(self.sample_stem, field="sample_stem")
        _require_tuple_of(
            self.operations,
            RuntimePeakFactOperation,
            field="operations",
        )
        if len(self.operations) != len(set(self.operations)):
            raise RuntimeFinalizationContractError(
                "peak-fact operations may each occur at most once per work receipt"
            )
        if self.operations not in (
            *_RUNTIME_PEAK_FACT_OPERATION_SEQUENCES,
            _LEGACY_PEAK_FACT_OPERATION_SEQUENCE,
        ):
            raise RuntimeFinalizationContractError(
                "peak-fact operations must preserve canonical order"
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
            if self.prepared_signal_id is None and self.operations != (
                _LEGACY_PEAK_FACT_OPERATION_SEQUENCE
            ):
                raise RuntimeFinalizationContractError(
                    "canonical peak-fact work requires prepared_signal_id"
                )
            if self.prepared_signal_id is not None:
                _required_prepared_signal_id(self.prepared_signal_id)
        elif any(
            value is not None
            for value in (
                self.owner_request_identity,
                self.group_hypothesis_id,
                self.acquisition_source,
                self.prepared_signal_id,
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
                RuntimePeakFactOperation.ASLS_BASELINE,
                RuntimePeakFactOperation.GAUSSIAN_SMOOTHING,
                RuntimePeakFactOperation.SEGMENT_ENUMERATION,
                RuntimePeakFactOperation.INTEGRATION,
                RuntimePeakFactOperation.NORMALIZATION,
                RuntimePeakFactOperation.RAW_DESCRIPTOR_PROJECTION,
                RuntimePeakFactOperation.REGION_AUDIT_PROJECTION,
            }
            for operation in self.operations
        )
        if trace_evaluations != 1:
            raise RuntimeFinalizationContractError(
                "peak-fact work requires one trace evaluation"
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
    asls_baseline_count: int
    smoothing_count: int
    segment_enumeration_count: int
    integration_count: int
    normalization_count: int
    raw_descriptor_projection_count: int
    region_audit_projection_count: int
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
            "asls_baseline_count",
            "smoothing_count",
            "segment_enumeration_count",
            "integration_count",
            "normalization_count",
            "raw_descriptor_projection_count",
            "region_audit_projection_count",
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
    prepared_operations_by_signal_id: dict[
        str,
        tuple[RuntimePeakFactOperation, ...],
    ] = {}
    trace_evaluation_count = 0
    asls_baseline_count = 0
    smoothing_count = 0
    segment_enumeration_count = 0
    integration_count = 0
    normalization_count = 0
    raw_descriptor_projection_count = 0
    region_audit_projection_count = 0
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
            and receipt.acquisition_source != raw_session_capability.acquisition_source
        ):
            raise RuntimeFinalizationContractError(
                "peak-fact work identity crosses the RAW session capability"
            )
        preparation_operations = tuple(
            operation
            for operation in receipt.operations
            if operation in _RUNTIME_PREPARATION_OPERATIONS
        )
        prior_preparation_operations = (
            prepared_operations_by_signal_id.get(receipt.prepared_signal_id)
            if receipt.prepared_signal_id is not None
            else None
        )
        if (
            prior_preparation_operations is not None
            and prior_preparation_operations != preparation_operations
        ):
            raise RuntimeFinalizationContractError(
                "one prepared_signal_id carries inconsistent preparation operations"
            )
        prepared_signal_is_new = (
            receipt.prepared_signal_id is None or prior_preparation_operations is None
        )
        if receipt.prepared_signal_id is not None and prepared_signal_is_new:
            prepared_operations_by_signal_id[receipt.prepared_signal_id] = (
                preparation_operations
            )
        if prepared_signal_is_new:
            trace_evaluation_count += receipt.operations.count(
                RuntimePeakFactOperation.TRACE_EVALUATION
            )
        receipt_asls_baseline_count = receipt.operations.count(
            RuntimePeakFactOperation.ASLS_BASELINE
        )
        receipt_smoothing_count = receipt.operations.count(
            RuntimePeakFactOperation.GAUSSIAN_SMOOTHING
        )
        receipt_segment_enumeration_count = receipt.operations.count(
            RuntimePeakFactOperation.SEGMENT_ENUMERATION
        )
        receipt_integration_count = receipt.operations.count(
            RuntimePeakFactOperation.INTEGRATION
        )
        receipt_normalization_count = receipt.operations.count(
            RuntimePeakFactOperation.NORMALIZATION
        )
        receipt_raw_descriptor_projection_count = receipt.operations.count(
            RuntimePeakFactOperation.RAW_DESCRIPTOR_PROJECTION
        )
        receipt_region_audit_projection_count = receipt.operations.count(
            RuntimePeakFactOperation.REGION_AUDIT_PROJECTION
        )
        if prepared_signal_is_new:
            asls_baseline_count += receipt_asls_baseline_count
            smoothing_count += receipt_smoothing_count
        segment_enumeration_count += receipt_segment_enumeration_count
        integration_count += receipt_integration_count
        normalization_count += receipt_normalization_count
        raw_descriptor_projection_count += receipt_raw_descriptor_projection_count
        region_audit_projection_count += receipt_region_audit_projection_count
        sealed_observation_count += (
            receipt.identity_status is RuntimePeakFactIdentityStatus.SEALED
        )
        identity_unavailable_count += (
            receipt.identity_status is RuntimePeakFactIdentityStatus.UNAVAILABLE
        )
        if any(
            (
                receipt_asls_baseline_count,
                receipt_smoothing_count,
                receipt_segment_enumeration_count,
                receipt_integration_count,
                receipt_normalization_count,
                receipt_raw_descriptor_projection_count,
                receipt_region_audit_projection_count,
            )
        ):
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
        asls_baseline_count=asls_baseline_count,
        smoothing_count=smoothing_count,
        segment_enumeration_count=segment_enumeration_count,
        integration_count=integration_count,
        normalization_count=normalization_count,
        raw_descriptor_projection_count=raw_descriptor_projection_count,
        region_audit_projection_count=region_audit_projection_count,
        artifact_round_trip_count=artifact_round_trip_count,
        artifact_round_trip_unavailable_reason=(artifact_round_trip_unavailable_reason),
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
    [object],
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


def _required_prepared_signal_id(value: object) -> str:
    prefix = "prepared-signal.v1:"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise RuntimeFinalizationContractError(
            "prepared_signal_id must use the prepared-signal.v1 identity"
        )
    digest = value[len(prefix) :]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeFinalizationContractError(
            "prepared_signal_id must carry one lowercase SHA-256 digest"
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
    if isinstance(left, RuntimeAcquisitionMetrics) or isinstance(
        right, RuntimeAcquisitionMetrics
    ):
        left_digest = (
            first._acquisition_digest
            if isinstance(left, RuntimeAcquisitionMetrics)
            else _runtime_acquisition_digest(left)
        )
        right_digest = (
            second._acquisition_digest
            if isinstance(right, RuntimeAcquisitionMetrics)
            else _runtime_acquisition_digest(right)
        )
        return left_digest == right_digest
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


__all__ = (
    "RuntimeAcquisitionMetrics",
    "RuntimeAcquisitionReceipt",
    "RuntimeArtifactIoSample",
    "RuntimeArtifactRegistrationReceipt",
    "RuntimeCancellationRequest",
    "RuntimeExecutionPhase",
    "RuntimeExecutionPhaseError",
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
    "RuntimeWorkerBatchProvider",
    "RuntimeWorkerBatchReceipt",
    "RuntimeWorkerPeakRssSample",
    "accept_runtime_finalization_receipt",
    "compact_runtime_acquisition_receipt",
    "derive_runtime_acquisition_metrics",
    "derive_runtime_peak_fact_cost_metrics",
    "derive_runtime_raw_open_metrics",
)
