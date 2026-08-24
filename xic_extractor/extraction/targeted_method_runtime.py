"""No-RAW scientific orchestration through the existing process runtimes.

Alignment owns candidate-backed cross-sample grouping.  Targeted owns Backfill
and legacy-validated projection.  This module only carries the exact successful
Alignment result between those owners and reuses their existing mechanical
runtime adapters.
"""

from __future__ import annotations

import pickle
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Any

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSamplePeakGroupHypothesis,
)
from xic_extractor.alignment.runtime_adapter import (
    AlignmentRuntimeApplicationResult,
    AlignmentRuntimeProcessResult,
    canonical_alignment_peak_group_binding_bytes,
    execute_alignment_process_runtime,
    mint_alignment_process_result_artifact,
)
from xic_extractor.alignment.targeted_hypothesis_alignment import (
    TargetedHypothesisAlignmentInput,
    align_selected_targeted_hypotheses,
)
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.evidence_artifacts import EvidenceArtifactRef
from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
    WorkUnitFailureCategory,
    WorkUnitFailureDetail,
    WorkUnitTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceCleanupOutcome,
    RuntimeResourceOpenOutcome,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAttemptAggregate,
    RuntimeTelemetryProvider,
    mint_work_unit_settlement,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodRequest,
    canonical_targeted_diagnostic_method_bytes,
    complete_targeted_diagnostic_method,
)
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedProcessResourceReport,
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    execute_targeted_runtime_lifecycle,
    mint_targeted_process_result_ref,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity


class TargetedMethodRuntimeError(ValueError):
    """Raised when the two scientific runtime stages cannot bind exactly."""


@dataclass(frozen=True, slots=True)
class TargetedMethodAlignmentJob:
    """One real candidate-set-backed Alignment invocation for a runtime work key."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    resource_key: str
    alignment_inputs: tuple[TargetedHypothesisAlignmentInput, ...]
    target: ResolvedTargetDefinition
    config: AlignmentConfig
    upstream_artifacts: tuple[EvidenceArtifactRef, ...]

    def __post_init__(self) -> None:
        _validate_attempt(self.attempt)
        _required_text(self.work_unit_key, field="work_unit_key")
        _required_text(self.resource_key, field="resource_key")
        if not isinstance(self.alignment_inputs, tuple) or not self.alignment_inputs:
            raise TargetedMethodRuntimeError(
                "alignment_inputs must be a non-empty immutable tuple"
            )
        if any(
            not isinstance(item, TargetedHypothesisAlignmentInput)
            for item in self.alignment_inputs
        ):
            raise TargetedMethodRuntimeError(
                "alignment_inputs must contain TargetedHypothesisAlignmentInput"
            )
        if not isinstance(self.target, ResolvedTargetDefinition):
            raise TargetedMethodRuntimeError(
                "target must be an owner-resolved target definition"
            )
        if not isinstance(self.config, AlignmentConfig):
            raise TargetedMethodRuntimeError("config must be AlignmentConfig")
        _validate_alignment_input_attempt(self.attempt, self.alignment_inputs)
        _validate_alignment_upstreams(
            self.alignment_inputs,
            self.upstream_artifacts,
        )


@dataclass(frozen=True, slots=True)
class TargetedMethodCompletionSpec:
    """Caller mapping from one accepted Alignment work item to Targeted work."""

    alignment_work_unit_key: str
    targeted_work_unit_key: str
    resource_key: str
    request: TargetedDiagnosticMethodRequest

    def __post_init__(self) -> None:
        _required_text(
            self.alignment_work_unit_key,
            field="alignment_work_unit_key",
        )
        _required_text(self.targeted_work_unit_key, field="targeted_work_unit_key")
        _required_text(self.resource_key, field="resource_key")
        if not isinstance(self.request, TargetedDiagnosticMethodRequest):
            raise TargetedMethodRuntimeError(
                "request must be TargetedDiagnosticMethodRequest"
            )


@dataclass(frozen=True, slots=True)
class TargetedMethodCompletionJob:
    """Exact successful Alignment carrier ready for Targeted completion."""

    attempt: ProcessingAttemptIdentity
    work_unit_key: str
    resource_key: str
    request: TargetedDiagnosticMethodRequest
    alignment_work_unit_key: str
    alignment_groups: tuple[CrossSamplePeakGroupHypothesis, ...]
    alignment_result_artifact: EvidenceArtifactRef

    def __post_init__(self) -> None:
        _validate_attempt(self.attempt)
        _required_text(self.work_unit_key, field="work_unit_key")
        _required_text(self.resource_key, field="resource_key")
        _required_text(
            self.alignment_work_unit_key,
            field="alignment_work_unit_key",
        )
        if not isinstance(self.request, TargetedDiagnosticMethodRequest):
            raise TargetedMethodRuntimeError(
                "request must be TargetedDiagnosticMethodRequest"
            )
        if not isinstance(self.alignment_groups, tuple) or any(
            not isinstance(group, CrossSamplePeakGroupHypothesis)
            for group in self.alignment_groups
        ):
            raise TargetedMethodRuntimeError(
                "alignment_groups must be an immutable Alignment-owned tuple"
            )
        if not isinstance(self.alignment_result_artifact, EvidenceArtifactRef):
            raise TargetedMethodRuntimeError(
                "alignment_result_artifact must be an EvidenceArtifactRef"
            )
        request_id, attempt_id = _request_processing_ids(self.request)
        if (
            request_id != self.attempt.processing_request_id
            or attempt_id != self.attempt.processing_attempt_id
            or self.alignment_result_artifact.producer_processing_request_id
            != request_id
            or self.alignment_result_artifact.producer_processing_attempt_id
            != attempt_id
        ):
            raise TargetedMethodRuntimeError(
                "Targeted completion job crosses processing request or attempt"
            )


TargetedMethodCompletionErrorFactory = Callable[
    [TargetedMethodCompletionJob, Exception],
    TargetedRuntimeWorkerBatch,
]


def alignment_upstream_artifacts_for_request(
    request: TargetedDiagnosticMethodRequest,
) -> tuple[EvidenceArtifactRef, ...]:
    """Return the exact candidate-set and selection refs supplied by the caller."""

    if not isinstance(request, TargetedDiagnosticMethodRequest):
        raise TargetedMethodRuntimeError(
            "request must be TargetedDiagnosticMethodRequest"
        )
    artifacts = {
        artifact.evidence_artifact_id: artifact
        for item in request.alignment_inputs
        for artifact in (item.candidate_set.artifact, item.selection.artifact)
    }
    return tuple(artifacts[key] for key in sorted(artifacts))


def execute_targeted_method_alignment_job(
    job: TargetedMethodAlignmentJob,
) -> AlignmentRuntimeProcessResult:
    """Run the existing Alignment owner and return its exact scientific carrier."""

    if not isinstance(job, TargetedMethodAlignmentJob):
        raise TargetedMethodRuntimeError("job must be TargetedMethodAlignmentJob")
    groups = align_selected_targeted_hypotheses(
        job.alignment_inputs,
        target=job.target,
        config=job.config,
    )
    payload = canonical_alignment_peak_group_binding_bytes(groups)
    artifact = mint_alignment_process_result_artifact(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=job.upstream_artifacts,
    )
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.SUCCEEDED,
            owner_result_ref=artifact,
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
        runtime_artifacts=(artifact,),
        cross_sample_peak_groups=groups,
        cross_sample_peak_group_upstream_artifact_ids=(
            artifact.upstream_artifact_ids
        ),
    )


def failed_targeted_method_alignment_job(
    job: TargetedMethodAlignmentJob,
    error: Exception,
) -> AlignmentRuntimeProcessResult:
    """Fail one lost/raised Alignment worker without inventing a result carrier."""

    if not isinstance(job, TargetedMethodAlignmentJob):
        raise TargetedMethodRuntimeError("job must be TargetedMethodAlignmentJob")
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.FAILED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.EXECUTION_ERROR,
                stage="alignment-process-worker",
                source_reference=f"no-raw:{job.work_unit_key}",
                message=f"{type(error).__name__}: {error}",
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=(
            RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
        ),
        cleanup_outcome=(
            RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
        ),
    )


def cancelled_targeted_method_alignment_job(
    job: TargetedMethodAlignmentJob,
) -> AlignmentRuntimeProcessResult:
    """Settle accepted pre-dispatch cancellation without opening a worker."""

    if not isinstance(job, TargetedMethodAlignmentJob):
        raise TargetedMethodRuntimeError("job must be TargetedMethodAlignmentJob")
    return AlignmentRuntimeProcessResult(
        settlement=mint_work_unit_settlement(
            attempt=job.attempt,
            work_unit_key=job.work_unit_key,
            outcome=WorkUnitTerminalOutcome.CANCELLED,
            failure_detail=WorkUnitFailureDetail(
                category=WorkUnitFailureCategory.CANCELLATION,
                stage="alignment-process-worker",
                source_reference=f"no-raw:{job.work_unit_key}",
                message="attempt cancellation accepted before Alignment dispatch",
            ),
        ),
        resource_key=job.resource_key,
        open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
        cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
    )


def execute_targeted_method_alignment_runtime(
    runtime: AttemptRuntime,
    jobs: Iterable[TargetedMethodAlignmentJob],
    *,
    max_workers: int,
    resource_reservations: Sequence[RuntimeResourceReservation],
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    executor_factory: Callable[..., Any] | None = None,
) -> AlignmentRuntimeApplicationResult:
    """Spawn real Alignment jobs through the existing Alignment runtime owner."""

    return execute_alignment_process_runtime(
        runtime,
        jobs,
        worker=execute_targeted_method_alignment_job,
        error_factory=failed_targeted_method_alignment_job,
        cancellation_factory=cancelled_targeted_method_alignment_job,
        max_workers=max_workers,
        resource_reservations=resource_reservations,
        telemetry_provider=telemetry_provider,
        recovery_marker=recovery_marker,
        executor_factory=executor_factory,
    )


def build_targeted_method_completion_jobs(
    alignment: AlignmentRuntimeApplicationResult,
    specs: Sequence[TargetedMethodCompletionSpec],
) -> tuple[TargetedMethodCompletionJob, ...]:
    """Admit only exact successful Alignment carriers into Targeted completion."""

    if not isinstance(alignment, AlignmentRuntimeApplicationResult):
        raise TargetedMethodRuntimeError(
            "alignment must be AlignmentRuntimeApplicationResult"
        )
    if (
        alignment.runtime.integrity_failure is not None
        or alignment.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
    ):
        raise TargetedMethodRuntimeError(
            "only an integrity-clean successful Alignment runtime may continue"
        )
    if isinstance(specs, (str, bytes)):
        raise TargetedMethodRuntimeError(
            "specs must contain TargetedMethodCompletionSpec values"
        )
    try:
        normalized_specs = tuple(specs)
    except TypeError as exc:
        raise TargetedMethodRuntimeError(
            "specs must contain TargetedMethodCompletionSpec values"
        ) from exc
    if any(
        not isinstance(item, TargetedMethodCompletionSpec)
        for item in normalized_specs
    ):
        raise TargetedMethodRuntimeError(
            "specs must contain TargetedMethodCompletionSpec values"
        )
    groups_by_key = dict(alignment.successful_cross_sample_peak_groups)
    artifacts_by_key = {
        settlement.work_unit_key: settlement.owner_result_ref
        for settlement in alignment.runtime.settlements
        if settlement.outcome is WorkUnitTerminalOutcome.SUCCEEDED
        and settlement.owner_result_ref is not None
    }
    attempt = alignment.runtime.manifest.attempt
    jobs: list[TargetedMethodCompletionJob] = []
    for spec in normalized_specs:
        groups = groups_by_key.get(spec.alignment_work_unit_key)
        artifact = artifacts_by_key.get(spec.alignment_work_unit_key)
        if groups is None or artifact is None:
            raise TargetedMethodRuntimeError(
                "Targeted completion requires the exact successful Alignment carrier"
            )
        jobs.append(
            TargetedMethodCompletionJob(
                attempt=attempt,
                work_unit_key=spec.targeted_work_unit_key,
                resource_key=spec.resource_key,
                request=spec.request,
                alignment_work_unit_key=spec.alignment_work_unit_key,
                alignment_groups=groups,
                alignment_result_artifact=artifact,
            )
        )
    return tuple(jobs)


def execute_targeted_method_completion_job(
    job: TargetedMethodCompletionJob,
) -> TargetedRuntimeWorkerBatch:
    """Complete Targeted science from Alignment's exact groups without realignment."""

    if not isinstance(job, TargetedMethodCompletionJob):
        raise TargetedMethodRuntimeError("job must be TargetedMethodCompletionJob")
    result = complete_targeted_diagnostic_method(
        job.request,
        job.alignment_groups,
    )
    payload = canonical_targeted_diagnostic_method_bytes(result)
    reference = mint_targeted_process_result_ref(
        processing_attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        owner_result_payload=payload,
        upstream_artifacts=(job.alignment_result_artifact,),
    )
    envelope = TargetedProcessWorkerResultEnvelope(
        attempt=job.attempt,
        work_unit_key=job.work_unit_key,
        outcome=WorkUnitTerminalOutcome.SUCCEEDED,
        owner_result_ref=reference,
        targeted_method_result=result,
        targeted_method_upstream_artifact_ids=(
            reference.artifact.upstream_artifact_ids
        ),
    )
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(envelope,),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=job.attempt,
                resource_key=job.resource_key,
                open_outcome=RuntimeResourceOpenOutcome.OPENED,
                cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            ),
        ),
        runtime_artifact_refs=(reference,),
    )


def targeted_method_worker_loss_batch(
    job: TargetedMethodCompletionJob,
    error: Exception,
) -> TargetedRuntimeWorkerBatch:
    """Fail one lost/raised Targeted worker and conservatively close its resource."""

    if not isinstance(job, TargetedMethodCompletionJob):
        raise TargetedMethodRuntimeError("job must be TargetedMethodCompletionJob")
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(
            TargetedProcessWorkerResultEnvelope(
                attempt=job.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.FAILED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.WORKER_LOSS,
                    stage="targeted-process-worker",
                    source_reference=f"no-raw:{job.work_unit_key}",
                    message=f"{type(error).__name__}: {error}",
                ),
            ),
        ),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=job.attempt,
                resource_key=job.resource_key,
                open_outcome=(
                    RuntimeResourceOpenOutcome.OPEN_STATE_UNKNOWN_AFTER_WORKER_LOSS
                ),
                cleanup_outcome=(
                    RuntimeResourceCleanupOutcome.UNKNOWN_AFTER_WORKER_LOSS
                ),
            ),
        ),
        runtime_artifact_refs=(),
    )


def execute_targeted_method_completion_runtime(
    runtime: AttemptRuntime,
    jobs: Sequence[TargetedMethodCompletionJob],
    *,
    max_workers: int,
    resource_reservations: Sequence[RuntimeResourceReservation],
    telemetry_provider: RuntimeTelemetryProvider,
    recovery_marker: OwnerPathRuntimeMarker,
    error_factory: TargetedMethodCompletionErrorFactory = (
        targeted_method_worker_loss_batch
    ),
    executor_factory: Callable[..., Any] | None = None,
) -> TargetedRuntimeLifecycleApplicationResult:
    """Spawn Targeted completion and close its existing lifecycle atomically."""

    if not isinstance(runtime, AttemptRuntime):
        raise TargetedMethodRuntimeError("runtime must be AttemptRuntime")
    if isinstance(jobs, (str, bytes)):
        raise TargetedMethodRuntimeError(
            "jobs must contain TargetedMethodCompletionJob values"
        )
    try:
        normalized_jobs = tuple(jobs)
    except TypeError as exc:
        raise TargetedMethodRuntimeError(
            "jobs must contain TargetedMethodCompletionJob values"
        ) from exc
    if not normalized_jobs or any(
        not isinstance(job, TargetedMethodCompletionJob) for job in normalized_jobs
    ):
        raise TargetedMethodRuntimeError(
            "jobs must contain TargetedMethodCompletionJob values"
        )
    if any(job.attempt != runtime.manifest.attempt for job in normalized_jobs):
        raise TargetedMethodRuntimeError(
            "Targeted completion jobs must bind the runtime attempt"
        )
    if (
        isinstance(max_workers, bool)
        or not isinstance(max_workers, int)
        or max_workers < 1
    ):
        raise TargetedMethodRuntimeError("max_workers must be a positive integer")
    if not isinstance(recovery_marker, OwnerPathRuntimeMarker):
        raise TargetedMethodRuntimeError(
            "recovery_marker must be OwnerPathRuntimeMarker"
        )
    if not callable(error_factory):
        raise TargetedMethodRuntimeError("error_factory must be callable")
    for job in normalized_jobs:
        _validate_spawn_payload(job)

    recovery_marker.record_launch(runtime)

    def provide_worker_batch(
        _aggregate: RuntimeAttemptAggregate,
    ) -> TargetedRuntimeWorkerBatch:
        if runtime.history.cancellation_accepted:
            observed = tuple(
                _cancelled_completion_batch(job) for job in normalized_jobs
            )
        else:
            observed = _run_spawned_completion_jobs(
                normalized_jobs,
                max_workers=max_workers,
                error_factory=error_factory,
                executor_factory=executor_factory,
            )
        return _merge_targeted_worker_batches(observed)

    return execute_targeted_runtime_lifecycle(
        runtime,
        resource_reservations,
        worker_batch_provider=provide_worker_batch,
        telemetry_provider=telemetry_provider,
        finalization_persister=recovery_marker,
    )


def _run_spawned_completion_jobs(
    jobs: tuple[TargetedMethodCompletionJob, ...],
    *,
    max_workers: int,
    error_factory: TargetedMethodCompletionErrorFactory,
    executor_factory: Callable[..., Any] | None,
) -> tuple[TargetedRuntimeWorkerBatch, ...]:
    context = get_context("spawn")
    factory = executor_factory or ProcessPoolExecutor
    with factory(max_workers=max_workers, mp_context=context) as executor:
        submitted = tuple(
            (job, executor.submit(execute_targeted_method_completion_job, job))
            for job in jobs
        )
        observed: list[TargetedRuntimeWorkerBatch] = []
        for job, future in submitted:
            try:
                result = future.result()
            except Exception as exc:
                result = error_factory(job, exc)
            if not isinstance(result, TargetedRuntimeWorkerBatch):
                raise TargetedMethodRuntimeError(
                    "Targeted completion worker returned an invalid batch"
                )
            observed.append(result)
    return tuple(observed)


def _cancelled_completion_batch(
    job: TargetedMethodCompletionJob,
) -> TargetedRuntimeWorkerBatch:
    return TargetedRuntimeWorkerBatch(
        result_envelopes=(
            TargetedProcessWorkerResultEnvelope(
                attempt=job.attempt,
                work_unit_key=job.work_unit_key,
                outcome=WorkUnitTerminalOutcome.CANCELLED,
                failure_detail=WorkUnitFailureDetail(
                    category=WorkUnitFailureCategory.CANCELLATION,
                    stage="targeted-process-worker",
                    source_reference=f"no-raw:{job.work_unit_key}",
                    message=(
                        "attempt cancellation accepted before Targeted dispatch"
                    ),
                ),
            ),
        ),
        resource_reports=(
            TargetedProcessResourceReport(
                attempt=job.attempt,
                resource_key=job.resource_key,
                open_outcome=RuntimeResourceOpenOutcome.NOT_OPENED,
                cleanup_outcome=RuntimeResourceCleanupOutcome.SUCCEEDED,
            ),
        ),
        runtime_artifact_refs=(),
    )


def _merge_targeted_worker_batches(
    batches: tuple[TargetedRuntimeWorkerBatch, ...],
) -> TargetedRuntimeWorkerBatch:
    return TargetedRuntimeWorkerBatch(
        result_envelopes=tuple(
            envelope for batch in batches for envelope in batch.result_envelopes
        ),
        resource_reports=tuple(
            report for batch in batches for report in batch.resource_reports
        ),
        runtime_artifact_refs=tuple(
            reference
            for batch in batches
            for reference in batch.runtime_artifact_refs
        ),
    )


def _validate_alignment_input_attempt(
    attempt: ProcessingAttemptIdentity,
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
) -> None:
    if any(
        item.selection.artifact.producer_processing_request_id
        != attempt.processing_request_id
        or item.selection.artifact.producer_processing_attempt_id
        != attempt.processing_attempt_id
        or item.candidate_set.artifact.producer_processing_request_id
        != attempt.processing_request_id
        or item.candidate_set.artifact.producer_processing_attempt_id
        != attempt.processing_attempt_id
        for item in inputs
    ):
        raise TargetedMethodRuntimeError(
            "Alignment inputs cross the job processing request or attempt"
        )


def _validate_alignment_upstreams(
    inputs: tuple[TargetedHypothesisAlignmentInput, ...],
    upstream_artifacts: tuple[EvidenceArtifactRef, ...],
) -> None:
    if not isinstance(upstream_artifacts, tuple) or any(
        not isinstance(artifact, EvidenceArtifactRef)
        for artifact in upstream_artifacts
    ):
        raise TargetedMethodRuntimeError(
            "upstream_artifacts must be an explicit EvidenceArtifactRef tuple"
        )
    supplied = tuple(artifact.evidence_artifact_id for artifact in upstream_artifacts)
    expected = tuple(
        sorted(
            {
                artifact.evidence_artifact_id
                for item in inputs
                for artifact in (item.candidate_set.artifact, item.selection.artifact)
            }
        )
    )
    if supplied != expected:
        raise TargetedMethodRuntimeError(
            "Alignment upstream artifacts must exactly close candidate sets "
            "and selections"
        )


def _request_processing_ids(
    request: TargetedDiagnosticMethodRequest,
) -> tuple[str, str]:
    request_ids = {
        item.selection.artifact.producer_processing_request_id
        for item in request.alignment_inputs
    }
    attempt_ids = {
        item.selection.artifact.producer_processing_attempt_id
        for item in request.alignment_inputs
    }
    if len(request_ids) != 1 or len(attempt_ids) != 1:
        raise TargetedMethodRuntimeError(
            "Targeted method request must bind one processing request and attempt"
        )
    return next(iter(request_ids)), next(iter(attempt_ids))


def _validate_attempt(value: object) -> None:
    if not isinstance(value, ProcessingAttemptIdentity):
        raise TargetedMethodRuntimeError(
            "attempt must be an owner-issued ProcessingAttemptIdentity"
        )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise TargetedMethodRuntimeError(
            f"{field} must be non-empty owner-normalized text"
        )
    return value


def _validate_spawn_payload(job: TargetedMethodCompletionJob) -> None:
    try:
        pickle.dumps(job)
    except Exception as exc:
        raise TargetedMethodRuntimeError(
            f"Targeted completion job is not pickleable: {exc}"
        ) from exc


__all__ = [
    "TargetedMethodAlignmentJob",
    "TargetedMethodCompletionErrorFactory",
    "TargetedMethodCompletionJob",
    "TargetedMethodCompletionSpec",
    "TargetedMethodRuntimeError",
    "alignment_upstream_artifacts_for_request",
    "build_targeted_method_completion_jobs",
    "cancelled_targeted_method_alignment_job",
    "execute_targeted_method_alignment_job",
    "execute_targeted_method_alignment_runtime",
    "execute_targeted_method_completion_job",
    "execute_targeted_method_completion_runtime",
    "failed_targeted_method_alignment_job",
    "targeted_method_worker_loss_batch",
]
