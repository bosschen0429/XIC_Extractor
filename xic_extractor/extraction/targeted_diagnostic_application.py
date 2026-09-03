"""Targeted production composition plus retained diagnostic utilities.

Product adapters use the builder's acquire → typed workflow → publish route.
The separately invoked diagnostic method remains available to explicit callers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from xic_extractor.execution_runtime import (
    AttemptRuntime,
    AttemptTerminalOutcome,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceKind,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeCancellationRequest,
    RuntimeTelemetryProvider,
    start_runtime_attempt,
)
from xic_extractor.extraction.targeted_diagnostic_delivery import (
    settle_targeted_diagnostic_delivery_candidate,
)
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodRequest,
    targeted_attempt_namespace_component,
)
from xic_extractor.extraction.targeted_method_runtime import (
    TargetedMethodAlignmentJob,
    TargetedMethodCompletionSpec,
    TargetedMethodRuntimeError,
    alignment_upstream_artifacts_for_request,
    build_targeted_method_completion_jobs,
    execute_targeted_method_alignment_runtime,
    execute_targeted_method_completion_runtime,
)
from xic_extractor.extraction.targeted_service import (
    TargetedApplicationService,
    TargetedDiagnosticApplicationOutcome,
    TargetedRunner,
)
from xic_extractor.processing_identity import ProcessingAttemptIdentity

_ALIGNMENT_WORK_UNIT_KEY = "package2:alignment-method"
_TARGETED_WORK_UNIT_KEY = "package2:targeted-method"
_RUNTIME_CONTRACT = "targeted-diagnostic-application:v1"


class _RuntimeJob(Protocol):
    @property
    def attempt(self) -> ProcessingAttemptIdentity: ...

    @property
    def resource_key(self) -> str: ...

    @property
    def work_unit_key(self) -> str: ...


def build_canonical_targeted_application_service(
    runner: TargetedRunner,
) -> TargetedApplicationService:
    """Build the single production CLI/GUI service composition."""

    return TargetedApplicationService(runner)


def execute_spawned_targeted_diagnostic_method(
    request: TargetedDiagnosticMethodRequest,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
    should_stop: Callable[[], bool] | None = None,
) -> TargetedDiagnosticApplicationOutcome:
    """Run real Alignment then Targeted process consumers for one request."""

    attempt = request.processing_attempt
    attempt_dir = (
        request.peak_config.output_csv.parent
        / "successor_attempts"
        / targeted_attempt_namespace_component(attempt.processing_attempt_id)
    )
    attempt_dir.mkdir(parents=True, exist_ok=True)
    alignment_job = TargetedMethodAlignmentJob(
        attempt=attempt,
        work_unit_key=_ALIGNMENT_WORK_UNIT_KEY,
        resource_key=f"process:{_ALIGNMENT_WORK_UNIT_KEY}",
        alignment_inputs=request.alignment_inputs,
        target=request.alignment_inputs[0].target,
        config=request.alignment_config,
        upstream_artifacts=alignment_upstream_artifacts_for_request(request),
    )
    alignment = execute_targeted_method_alignment_runtime(
        _runtime(
            attempt,
            (_ALIGNMENT_WORK_UNIT_KEY,),
            cancelled=_stop_requested(should_stop),
        ),
        (alignment_job,),
        max_workers=1,
        resource_reservations=_reservations((alignment_job,)),
        telemetry_provider=telemetry_provider,
        recovery_marker=_marker(attempt_dir, "alignment"),
    )
    if (
        alignment.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
    ):
        return TargetedDiagnosticApplicationOutcome(
            status="quarantined",
            method_result=None,
            delivery_candidate=None,
            quarantine_reason=(
                "Alignment runtime attempt "
                f"{alignment.finalization_record.terminal_outcome.value}; "
                "legacy remains active"
            ),
        )
    jobs = build_targeted_method_completion_jobs(
        alignment,
        (
            TargetedMethodCompletionSpec(
                alignment_work_unit_key=_ALIGNMENT_WORK_UNIT_KEY,
                targeted_work_unit_key=_TARGETED_WORK_UNIT_KEY,
                resource_key=f"process:{_TARGETED_WORK_UNIT_KEY}",
                request=request,
            ),
        ),
    )
    targeted = execute_targeted_method_completion_runtime(
        _runtime(
            attempt,
            (_TARGETED_WORK_UNIT_KEY,),
            cancelled=_stop_requested(should_stop),
        ),
        jobs,
        max_workers=1,
        resource_reservations=_reservations(jobs),
        telemetry_provider=telemetry_provider,
        recovery_marker=_marker(attempt_dir, "targeted"),
    )
    results = targeted.settlement.successful_targeted_method_results
    if (
        targeted.finalization_record.terminal_outcome
        is not AttemptTerminalOutcome.SUCCEEDED
    ):
        return TargetedDiagnosticApplicationOutcome(
            status="quarantined",
            method_result=None,
            delivery_candidate=None,
            quarantine_reason=(
                "Targeted runtime attempt "
                f"{targeted.finalization_record.terminal_outcome.value}; "
                "legacy remains active"
            ),
        )
    if len(results) != 1 or results[0][0] != _TARGETED_WORK_UNIT_KEY:
        raise TargetedMethodRuntimeError(
            "Targeted diagnostic runtime did not produce one canonical result"
        )
    result = results[0][1]
    if _stop_requested(should_stop):
        return TargetedDiagnosticApplicationOutcome(
            status="quarantined",
            method_result=result,
            delivery_candidate=None,
            quarantine_reason=(
                "cancellation accepted after Targeted settlement; "
                "delivery not staged and legacy remains active"
            ),
        )
    delivery = settle_targeted_diagnostic_delivery_candidate(
        result,
        request=request,
        alignment_runtime=alignment,
        targeted_runtime=targeted,
        prior_current_delivery_id=request.prior_current_delivery_id,
    )
    if delivery.status == "quarantined":
        return TargetedDiagnosticApplicationOutcome(
            status="quarantined",
            method_result=result,
            delivery_candidate=delivery,
            quarantine_reason=delivery.quarantine_reason,
        )
    if delivery.status != "staged_candidate":
        raise TargetedMethodRuntimeError(
            "diagnostic delivery settlement returned an unsupported status"
        )
    return TargetedDiagnosticApplicationOutcome(
        status="staged_candidate",
        method_result=result,
        delivery_candidate=delivery,
    )


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
    *,
    cancelled: bool,
) -> AttemptRuntime:
    return start_runtime_attempt(
        attempt=attempt,
        work_unit_keys=work_unit_keys,
        owner_completeness_attestation=_RUNTIME_CONTRACT,
        cancellation_request=(
            RuntimeCancellationRequest(attempt=attempt) if cancelled else None
        ),
    )


def _stop_requested(should_stop: Callable[[], bool] | None) -> bool:
    if should_stop is None:
        return False
    if not callable(should_stop):
        raise TypeError("should_stop must be callable or None")
    return bool(should_stop())


def _reservations(
    jobs: Sequence[_RuntimeJob],
) -> tuple[RuntimeResourceReservation, ...]:
    return tuple(
        RuntimeResourceReservation(
            attempt=job.attempt,
            kind=RuntimeResourceKind.PROCESS_WORKER,
            resource_key=job.resource_key,
            worker_key=f"worker:{index}",
            source_reference=f"no-raw:{job.work_unit_key}:{index}",
            compatibility_token=_RUNTIME_CONTRACT,
        )
        for index, job in enumerate(jobs)
    )


def _marker(attempt_dir: Path, phase: str) -> OwnerPathRuntimeMarker:
    return OwnerPathRuntimeMarker(
        attempt_dir / f"{phase}.runtime.json",
        f"targeted-diagnostic-application:{phase}",
    )


__all__ = [
    "build_canonical_targeted_application_service",
    "execute_spawned_targeted_diagnostic_method",
]
