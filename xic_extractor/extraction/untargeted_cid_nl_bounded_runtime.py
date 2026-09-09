"""Complete CID-NL subjects in finite partitions under one runtime attempt."""

from __future__ import annotations

import gc
import importlib
import json
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, fields
from pathlib import Path
from tempfile import mkdtemp
from threading import Event, Lock
from typing import cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.result.application_result import (
    FinalizedUntargetBatchApplicationResult,
    UntargetBatchApplicationResultRequest,
    UntargetedCandidateExclusion,
    finalize_alignment_result,
)
from xic_extractor.alignment.workflow import CompletedUntargetBatchWorkflowResult
from xic_extractor.evidence_artifacts import (
    _mint_artifact_semantic_contract_ref,
    _mint_evidence_artifact_ref,
)
from xic_extractor.execution_runtime import (
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceKind,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAcquisitionReceipt,
    RuntimeArtifactRegistrationReceipt,
    RuntimeResourceSettlementReceipt,
    RuntimeTelemetryProvider,
    RuntimeWorkerBatchReceipt,
    compact_runtime_acquisition_receipt,
    execute_runtime_attempt,
    mint_work_unit_settlement,
    start_runtime_attempt,
)
from xic_extractor.extraction.untargeted_cid_nl_production_batch import (
    UntargetedCidNlPartitionComposition,
    UntargetedCidNlProductionPlan,
    compose_untargeted_cid_nl_partition,
    materialize_untargeted_cid_nl_partition,
)
from xic_extractor.extraction.untargeted_cid_nl_real_data_runtime import (
    UntargetedCidNlRuntimePartitionResult,
    execute_untargeted_cid_nl_runtime_partition,
)
from xic_extractor.extraction.untargeted_cid_nl_result_store import PartitionResultStore
from xic_extractor.immutable_sequence import ImmutableSequence
from xic_extractor.peak_detection.cid_nl_method_subject import (
    CID_NL_PROVIDER_ID,
    UntargetedCidNlMethodSubjectBatch,
)
from xic_extractor.peak_detection.search_scope import (
    SEARCH_SCOPE_SCHEMA_VERSION,
    CaptureCandidateBinding,
    CaptureMethodConstraints,
    EvidenceProviderBinding,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import resolve_request_acquisition_source
from xic_extractor.raw_reader import _ensure_assemblies_loaded

_PARTITION_CONTRACT = _mint_artifact_semantic_contract_ref(
    owner_namespace="xic_extractor.untargeted_cid_nl_bounded_runtime",
    semantic_role="closed_scientific_partition",
    schema_version="cid_nl_scientific_partition_v1",
    validation_status="diagnostic_only",
    media_type="application/json",
    serialization_format="canonical-json-utf8-lf-v1",
)


@dataclass(slots=True)
class _PartitionExecution:
    leaf: UntargetedCidNlRuntimePartitionResult | None = None
    payload: UntargetedCidNlPartitionComposition | None = None
    error: Exception | None = None
    entered_runtime: bool = False


def execute_bounded_untargeted_cid_nl(
    plan: UntargetedCidNlProductionPlan,
    *,
    output_dir: Path,
    alignment_config: AlignmentConfig,
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    progress_callback: Callable[[Mapping[str, object]], None] | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    max_partition_jobs: int = 50_000,
    raw_workers: int = 1,
    should_cancel: Callable[[], bool] | None = None,
) -> CompletedUntargetBatchWorkflowResult:
    """Spill complete subject results, then close the exact global population.

    A partition contains every sample for its subjects, preserving grouping and
    recovery decisions. Operational work units describe partition/sample phases;
    scientific per-subject receipts live in the owner-controlled result store.
    """
    if type(plan) is not UntargetedCidNlProductionPlan:
        raise TypeError("bounded execution requires a production plan")
    if type(max_partition_jobs) is not int or max_partition_jobs < 1:
        raise ValueError("partition job budget must be a positive integer")
    if type(raw_workers) is not int or raw_workers < 1:
        raise ValueError("raw_workers must be a positive integer")
    context = plan.context
    owners = context.processing_owners
    workers = min(
        raw_workers,
        max(1, max_partition_jobs // len(context.samples)),
        len(plan.subject_batch.subjects),
    )
    subjects_per_partition = max(
        1, max_partition_jobs // (len(context.samples) * workers)
    )
    partitions = tuple(
        plan.subject_batch.subjects[start : start + subjects_per_partition]
        for start in range(0, len(plan.subject_batch.subjects), subjects_per_partition)
    )
    partition_keys = tuple(f"partition-{index:05d}" for index in range(len(partitions)))
    phase_keys = tuple(
        f"{partition}:{sample.identity.sample_identity_id}:{phase}"
        for partition in partition_keys
        for sample in context.samples
        for phase in ("initial", "recovery")
    )
    reservations = tuple(
        RuntimeResourceReservation(
            attempt=owners.attempt,
            kind=RuntimeResourceKind.ACQUISITION_SESSION,
            resource_key=f"acquisition-session:{partition}:{sample.identity.sample_identity_id}:{phase}",
            worker_key=f"inline-acquisition:{partition}:{sample.identity.sample_identity_id}:{phase}",
            source_reference=resolve_request_acquisition_source(
                processing_owners=owners, sample=sample
            ).acquisition_source_id,
            compatibility_token="untargeted-cid-nl-real-data-runtime:v1",
            owner_conditional=True,
        )
        for partition in partition_keys
        for sample in context.samples
        for phase in ("initial", "recovery")
    )
    runtime = start_runtime_attempt(
        attempt=owners.attempt,
        work_unit_keys=phase_keys,
        owner_completeness_attestation=(
            f"{owners.request.processing_request_id}:cid-nl-partitioned-v1"
        ),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    store = PartitionResultStore(
        Path(mkdtemp(prefix=".cid_nl_partitions-", dir=output_dir)),
        shared_objects=_shared_objects(plan),
    )
    memberships_by_subject: dict[str, list[tuple[str, str]]] = {}
    for membership in plan.subject_batch.candidate_memberships:
        memberships_by_subject.setdefault(membership[1], []).append(membership)
    failure: Exception | None = None
    application_result = None

    def progress(stage: str, completed: int) -> None:
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "partition_progress",
                    "stage": stage,
                    "completed": completed,
                    "total": len(partitions),
                    "partition_job_budget": max_partition_jobs,
                }
            )

    def provide_worker_batch(_aggregate: object) -> RuntimeWorkerBatchReceipt:
        nonlocal failure, application_result
        settlements = []
        resources: dict[str, RuntimeResourceSettlementReceipt] = {}
        artifacts = []
        acquisitions: list[RuntimeAcquisitionReceipt] = []
        bindings: list[CaptureCandidateBinding] = []
        exclusion_positions: list[tuple[str, int, int]] = []
        unknown_partitions: set[str] = set()
        stopped = Event()
        pending_progress: dict[tuple[str, str], Mapping[str, object]] = {}
        progress_lock = Lock()
        futures: dict[int, Future[_PartitionExecution]] = {}
        executor = None
        partition_failure: Exception | None = None

        def observe_cancellation() -> bool:
            if should_cancel is not None and should_cancel():
                stopped.set()
            return stopped.is_set()

        def enqueue_progress(event: Mapping[str, object]) -> None:
            # A finite latest-event map keeps worker callbacks away from the
            # coordinator-owned timing recorder and its shared output file.
            with progress_lock:
                pending_progress[(str(event["partition_key"]), str(event["stage"]))] = (
                    event
                )

        def flush_progress() -> None:
            with progress_lock:
                events = tuple(pending_progress.values())
                pending_progress.clear()
            if progress_callback is not None:
                for event in events:
                    progress_callback(event)

        def execute_partition(index: int) -> _PartitionExecution:
            nonlocal partition_failure
            result = _PartitionExecution()
            try:
                if stopped.is_set():
                    raise InterruptedError("CID-NL partition cancelled before launch")
                subjects = partitions[index]
                subject_batch = UntargetedCidNlMethodSubjectBatch(
                    subjects=subjects,
                    candidate_memberships=tuple(
                        sorted(
                            membership
                            for subject in subjects
                            for membership in memberships_by_subject[
                                subject.method_subject_id
                            ]
                        )
                    ),
                )
                batch = materialize_untargeted_cid_nl_partition(plan, subject_batch)
                result.entered_runtime = True
                result.leaf = execute_untargeted_cid_nl_runtime_partition(
                    batch.jobs,
                    partition_key=partition_keys[index],
                    alignment_config=alignment_config,
                    progress_callback=(
                        enqueue_progress if workers > 1 else progress_callback
                    ),
                    should_cancel=(
                        stopped.is_set if workers > 1 else observe_cancellation
                    ),
                )
                if not result.leaf.succeeded:
                    failed = next(
                        receipt
                        for receipt in result.leaf.worker_batch.settlements
                        if receipt.outcome is not WorkUnitTerminalOutcome.SUCCEEDED
                    )
                    raise RuntimeError(
                        f"CID-NL partition failed: {failed.failure_detail}"
                    )
                if stopped.is_set():
                    raise InterruptedError(
                        "CID-NL partition cancelled before composition"
                    )
                result.payload = compose_untargeted_cid_nl_partition(
                    batch,
                    result.leaf.sample_results,
                    alignment_config=alignment_config,
                    known_istd_reference=known_istd_reference,
                )
            except Exception as exc:
                result.error = exc
                with progress_lock:
                    if partition_failure is None:
                        partition_failure = exc
                stopped.set()
            return result

        def collect_receipts(index: int, result: _PartitionExecution) -> None:
            if result.leaf is not None:
                resources.update(
                    (receipt.resource_key, receipt)
                    for receipt in result.leaf.worker_batch.resource_settlements
                )
                acquisitions.extend(
                    compact_runtime_acquisition_receipt(receipt)
                    for receipt in result.leaf.acquisitions
                )
            elif result.entered_runtime:
                unknown_partitions.add(partition_keys[index])

        try:
            if observe_cancellation():
                raise InterruptedError("CID-NL batch cancelled before launch")
            if workers > 1:
                # CLR/DLL initialization is process-global; perform it before
                # any worker opens its own thread-local RAW handle.
                _ensure_assemblies_loaded(
                    plan.sample_job_templates[0].dll_dir, importlib.import_module
                )
                executor = ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="cid-nl-partition"
                )
                for index in range(min(workers, len(partitions))):
                    if observe_cancellation():
                        break
                    futures[index] = executor.submit(execute_partition, index)
            for index in range(len(partitions)):
                active_partition = partition_keys[index]
                progress("partition_start", index)
                if executor is None:
                    if observe_cancellation():
                        raise InterruptedError("CID-NL batch cancelled")
                    result = execute_partition(index)
                else:
                    if index not in futures:
                        raise InterruptedError("CID-NL batch cancelled")
                    future = futures[index]
                    while True:
                        observe_cancellation()
                        flush_progress()
                        try:
                            result = future.result(timeout=0.1)
                            break
                        except TimeoutError:
                            continue
                    del futures[index], future
                collect_receipts(index, result)
                if executor is not None:
                    flush_progress()
                if result.error is not None:
                    raise partition_failure or result.error
                if observe_cancellation():
                    raise partition_failure or InterruptedError(
                        "CID-NL batch cancelled before spool"
                    )
                payload = result.payload
                assert payload is not None
                progress("partition_compose", index)
                sealed = store.append(active_partition, payload)
                bindings.extend(payload.capture_bindings)
                exclusion_positions.extend(
                    (exclusion.candidate_id, index, offset)
                    for offset, exclusion in enumerate(payload.excluded_candidates)
                )
                for sample in context.samples:
                    for phase in ("initial", "recovery"):
                        key = (
                            f"{active_partition}:"
                            f"{sample.identity.sample_identity_id}:{phase}"
                        )
                        artifact = _mint_evidence_artifact_ref(
                            producer_processing_request_id=owners.request.processing_request_id,
                            producer_processing_attempt_id=owners.attempt.processing_attempt_id,
                            semantic_contract=_PARTITION_CONTRACT,
                            payload_bytes=(
                                json.dumps(
                                    {
                                        "work_unit_key": key,
                                        "partition_sha256": sealed.sha256,
                                        "partition_bytes": sealed.size_bytes,
                                    },
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            ).encode("utf-8"),
                            upstream_artifacts=(),
                        )
                        artifacts.append(
                            RuntimeArtifactRegistrationReceipt(key, artifact)
                        )
                        settlements.append(
                            mint_work_unit_settlement(
                                attempt=owners.attempt,
                                work_unit_key=key,
                                outcome=WorkUnitTerminalOutcome.SUCCEEDED,
                                owner_result_ref=artifact,
                            )
                        )
                del payload, result
                gc.collect()
                progress("partition_released", index + 1)
                # Admit a replacement only after the previous future, leaf
                # graph and payload have been discarded by the single writer.
                next_index = index + workers
                if (
                    executor is not None
                    and next_index < len(partitions)
                    and not observe_cancellation()
                ):
                    futures[next_index] = executor.submit(execute_partition, next_index)
            if observe_cancellation():
                raise InterruptedError("CID-NL batch cancelled before closure")
            store.seal()
            progress("global_closure", len(partitions))
            scope = UntargetedSearchScope(
                schema_version=SEARCH_SCOPE_SCHEMA_VERSION,
                request=owners.request,
                samples=context.samples,
                method_constraints=CaptureMethodConstraints(
                    boundary_policy="resolver_interval",
                    peak_config=context.peak_config,
                    signal_processing_profile=context.signal_processing_profile,
                ),
                evidence_providers=tuple(
                    EvidenceProviderBinding(
                        CID_NL_PROVIDER_ID, subject.method_subject_id
                    )
                    for subject in plan.subject_batch.subjects
                ),
                declared_candidate_ids=tuple(
                    candidate_id
                    for candidate_id, _ in plan.subject_batch.candidate_memberships
                ),
                capture_bindings=tuple(
                    sorted(bindings, key=lambda binding: binding.candidate_id)
                ),
            )
            application_result = finalize_alignment_result(
                UntargetBatchApplicationResultRequest(
                    scope=scope,
                    processing_request=owners.request,
                    processing_attempt=owners.attempt,
                    children=store.sequence("children"),
                    excluded_candidates=_ordered_exclusions(store, exclusion_positions),
                )
            )
            if observe_cancellation():
                raise InterruptedError("CID-NL batch cancelled during closure")
        except Exception as exc:
            stopped.set()
            failure = exc
            # Every admitted leaf owns and closes its handles. Drain all of
            # them before finalization, retaining their actual cleanup truth.
            for index, future in futures.items():
                collect_receipts(index, future.result())
            futures.clear()
            completed_keys = {receipt.work_unit_key for receipt in settlements}
            # A closure failure invalidates the batch even if all parts finished.
            if len(completed_keys) == len(phase_keys):
                completed_keys.remove(settlements.pop().work_unit_key)
            for key in phase_keys:
                if key not in completed_keys:
                    settlements.append(
                        mint_work_unit_settlement(
                            attempt=owners.attempt,
                            work_unit_key=key,
                            outcome=WorkUnitTerminalOutcome.FAILED,
                            failure_detail=WorkUnitFailureDetail(
                                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                                stage="cid-nl-partition-completion",
                                source_reference=key,
                                message=(str(exc).strip() or type(exc).__name__)[:2000],
                            ),
                        )
                    )
        finally:
            if executor is not None:
                executor.shutdown(wait=True)
        for reservation in reservations:
            if reservation.resource_key not in resources:
                unknown = any(
                    reservation.resource_key.startswith(
                        f"acquisition-session:{partition}:"
                    )
                    for partition in unknown_partitions
                )
                resources[reservation.resource_key] = RuntimeResourceSettlementReceipt(
                    attempt=owners.attempt,
                    resource_key=reservation.resource_key,
                    open_outcome=(
                        RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                        if unknown
                        else RuntimeResourceOpenOutcome.CONDITION_NOT_ACTIVATED
                    ),
                    cleanup_outcome=(
                        RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                        if unknown
                        else RuntimeResourceCleanupOutcome.SUCCEEDED
                    ),
                )
        return RuntimeWorkerBatchReceipt(
            settlements=tuple(settlements),
            resource_settlements=tuple(resources.values()),
            artifact_registrations=tuple(artifacts),
            acquisitions=tuple(acquisitions),
        )

    recovery_marker.record_launch(runtime)
    finalization = execute_runtime_attempt(
        runtime,
        reservations,
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=telemetry_provider,
        persister=recovery_marker,
    )
    if failure is not None:
        raise RuntimeError(
            f"CID-NL bounded execution failed before batch publication: {failure}"
        ) from failure
    if (
        finalization.supervisor.terminal_outcome is not AttemptTerminalOutcome.SUCCEEDED
        or application_result is None
    ):
        raise RuntimeError("CID-NL bounded runtime did not complete atomically")
    progress("batch_completed", len(partitions))
    return CompletedUntargetBatchWorkflowResult(
        runtime_receipt=finalization,
        application_result=cast(
            FinalizedUntargetBatchApplicationResult, application_result
        ),
        method_assessments=store.sequence("method_assessments"),
        initial_groups=store.sequence("initial_groups"),
        initial_edges=store.sequence("initial_edges"),
    )


def _shared_objects(plan: UntargetedCidNlProductionPlan) -> tuple[object, ...]:
    context = plan.context
    owners = context.processing_owners
    values = [
        context,
        owners,
        context.samples,
        *context.samples,
        plan.subject_batch,
        *plan.subject_batch.subjects,
        context.settings,
        context.peak_config,
        context.signal_processing_profile,
    ]
    values.extend(getattr(owners, field.name) for field in fields(owners))
    for sample in context.samples:
        values.extend(getattr(sample, field.name) for field in fields(sample))
    return tuple({id(value): value for value in values if value is not None}.values())


def _ordered_exclusions(
    store: PartitionResultStore, positions: list[tuple[str, int, int]]
) -> ImmutableSequence:
    """Restore canonical candidate order with finite result windows."""
    ordered = tuple(sorted(positions))
    windows = tuple(
        ordered[start : start + 2048] for start in range(0, len(ordered), 2048)
    )

    def load_window(index: int) -> tuple:
        window = windows[index]
        by_partition: dict[int, list[tuple[int, int]]] = {}
        for position, (_, partition, offset) in enumerate(window):
            by_partition.setdefault(partition, []).append((position, offset))
        values: dict[int, UntargetedCandidateExclusion] = {}
        for partition, selected in by_partition.items():
            exclusions = store.read_selected(
                partition,
                "excluded_candidates",
                tuple(offset for _, offset in selected),
            )
            for (position, _), exclusion in zip(selected, exclusions, strict=True):
                values[position] = exclusion
        return tuple(values[position] for position in range(len(window)))

    return ImmutableSequence(tuple(map(len, windows)), load_window)
