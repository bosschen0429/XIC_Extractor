"""Internal composition for the bounded no-RAW Targeted diagnostic method.

The product adapters always use this builder.  Legacy remains authoritative;
the successor runs only while an internal owner-backed request provider is
installed for a bounded diagnostic execution.  No public CLI/config switch is
introduced.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from xic_extractor.execution_runtime import (
    AttemptLifecycleEventKind,
    AttemptLifecycleHistory,
    AttemptRuntime,
    append_attempt_lifecycle_event,
    preflight_required_work_manifest,
    start_attempt_runtime,
)
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceKind,
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import RuntimeTelemetryProvider
from xic_extractor.extraction.targeted_diagnostic_method import (
    TargetedDiagnosticMethodRequest,
    TargetedDiagnosticMethodResult,
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
    TargetedApplicationRequest,
    TargetedApplicationService,
    TargetedRunner,
)
from xic_extractor.extractor import RunOutput
from xic_extractor.processing_identity import ProcessingAttemptIdentity

TargetedDiagnosticRequestProvider = Callable[
    [TargetedApplicationRequest, RunOutput],
    TargetedDiagnosticMethodRequest,
]

_REQUEST_PROVIDER: ContextVar[TargetedDiagnosticRequestProvider | None] = ContextVar(
    "xic_targeted_diagnostic_request_provider",
    default=None,
)
_TELEMETRY_PROVIDER: ContextVar[RuntimeTelemetryProvider | None] = ContextVar(
    "xic_targeted_diagnostic_telemetry_provider",
    default=None,
)
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


@contextmanager
def targeted_diagnostic_request_provider(
    provider: TargetedDiagnosticRequestProvider,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
) -> Iterator[None]:
    """Install scoped scientific-input and telemetry providers for one run."""

    if not callable(provider):
        raise TypeError("provider must be callable")
    if not callable(telemetry_provider):
        raise TypeError("telemetry_provider must be callable")
    request_token = _REQUEST_PROVIDER.set(provider)
    telemetry_token = _TELEMETRY_PROVIDER.set(telemetry_provider)
    try:
        yield
    finally:
        _TELEMETRY_PROVIDER.reset(telemetry_token)
        _REQUEST_PROVIDER.reset(request_token)


def build_canonical_targeted_application_service(
    runner: TargetedRunner,
) -> TargetedApplicationService:
    """Build the single production CLI/GUI service composition."""

    return TargetedApplicationService(
        runner,
        diagnostic_method_runner=_execute_scoped_targeted_diagnostic_method,
        diagnostic_method_request_factory=_resolve_scoped_diagnostic_request,
    )


def _execute_scoped_targeted_diagnostic_method(
    request: TargetedDiagnosticMethodRequest,
) -> TargetedDiagnosticMethodResult:
    telemetry_provider = _TELEMETRY_PROVIDER.get()
    if telemetry_provider is None:
        raise TargetedMethodRuntimeError(
            "diagnostic execution requires a scoped runtime telemetry provider"
        )
    return execute_spawned_targeted_diagnostic_method(
        request,
        telemetry_provider=telemetry_provider,
    )


def _resolve_scoped_diagnostic_request(
    request: TargetedApplicationRequest,
    authoritative_legacy_output: RunOutput,
) -> TargetedDiagnosticMethodRequest | None:
    provider = _REQUEST_PROVIDER.get()
    if provider is None:
        return None
    resolved = provider(request, authoritative_legacy_output)
    if not isinstance(resolved, TargetedDiagnosticMethodRequest):
        raise TargetedMethodRuntimeError(
            "diagnostic request provider returned an invalid request"
        )
    if resolved.targets != request.targets:
        raise TargetedMethodRuntimeError(
            "diagnostic request target tuple differs from the application request"
        )
    return replace(
        resolved,
        peak_config=request.config,
        authoritative_legacy_output=authoritative_legacy_output,
    )


def execute_spawned_targeted_diagnostic_method(
    request: TargetedDiagnosticMethodRequest,
    *,
    telemetry_provider: RuntimeTelemetryProvider,
) -> TargetedDiagnosticMethodResult:
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
        _runtime(attempt, (_ALIGNMENT_WORK_UNIT_KEY,)),
        (alignment_job,),
        max_workers=1,
        resource_reservations=_reservations((alignment_job,)),
        telemetry_provider=telemetry_provider,
        recovery_marker=_marker(attempt_dir, "alignment"),
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
        _runtime(attempt, (_TARGETED_WORK_UNIT_KEY,)),
        jobs,
        max_workers=1,
        resource_reservations=_reservations(jobs),
        telemetry_provider=telemetry_provider,
        recovery_marker=_marker(attempt_dir, "targeted"),
    )
    results = targeted.settlement.successful_targeted_method_results
    if len(results) != 1 or results[0][0] != _TARGETED_WORK_UNIT_KEY:
        raise TargetedMethodRuntimeError(
            "Targeted diagnostic runtime did not produce one canonical result"
        )
    return results[0][1]


def _runtime(
    attempt: ProcessingAttemptIdentity,
    work_unit_keys: tuple[str, ...],
) -> AttemptRuntime:
    history = append_attempt_lifecycle_event(
        AttemptLifecycleHistory(attempt=attempt),
        AttemptLifecycleEventKind.ATTEMPT_QUEUED,
    )
    return start_attempt_runtime(
        preflight_required_work_manifest(
            history,
            attempt=attempt,
            work_unit_keys=work_unit_keys,
            owner_completeness_attestation=_RUNTIME_CONTRACT,
        )
    )


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
    "targeted_diagnostic_request_provider",
]
