from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import AttemptRuntime
from xic_extractor.execution_runtime_finalization import (
    AttemptFinalizationState,
    CoordinatorTerminalRecord,
    RuntimeResourceReservation,
    SupervisorFinalizationRecord,
    accept_supervisor_finalization,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_observability import RuntimeObservabilityLedger
from xic_extractor.rt_prior_library import LibraryEntry

from .targeted_inputs import resolve_model_selection_expected_diff_approvals
from .targeted_runtime_adapter import (
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeApplicationResult,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    finalize_targeted_runtime_worker_batch,
    prepare_targeted_runtime_lifecycle,
    settle_targeted_process_worker_results,
)

if TYPE_CHECKING:
    from xic_extractor.extractor import RunOutput
    from xic_extractor.peak_detection.model_selection import (
        ExpectedDiffApprovalRecord,
    )


class TargetedRunner(Protocol):
    def __call__(
        self,
        config: ExtractionConfig,
        targets: list[Target],
        progress_callback: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        injection_order: dict[str, int] | None = None,
        rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
        model_selection_expected_diff_approvals: (
            Mapping[str, ExpectedDiffApprovalRecord] | None
        ) = None,
    ) -> RunOutput: ...


class TargetedRuntimeRunner(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        runtime: AttemptRuntime,
    ) -> Sequence[TargetedProcessWorkerResultEnvelope]: ...


class TargetedRuntimeLifecycleRunner(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        state: AttemptFinalizationState,
    ) -> TargetedRuntimeWorkerBatch: ...


class TargetedRuntimeSupervisorFinalizer(Protocol):
    def __call__(
        self,
        coordinator: CoordinatorTerminalRecord,
        proposed: SupervisorFinalizationRecord,
    ) -> SupervisorFinalizationRecord: ...


@dataclass(frozen=True)
class TargetedApplicationRequest:
    """Headless application input; callbacks are lifecycle adapters, not science."""

    config: ExtractionConfig
    targets: tuple[Target, ...]
    progress_callback: Callable[[int, int, str], None] | None = None
    should_stop: Callable[[], bool] | None = None
    injection_order: dict[str, int] | None = None
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    ) = None


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner
    runtime_runner: TargetedRuntimeRunner | None = None
    runtime_lifecycle_runner: TargetedRuntimeLifecycleRunner | None = None
    runtime_supervisor_finalizer: TargetedRuntimeSupervisorFinalizer | None = None
    runtime_recovery_marker: OwnerPathRuntimeMarker | None = None

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        resolved_request = _resolve_application_request(request)
        return self.runner(
            resolved_request.config,
            list(resolved_request.targets),
            progress_callback=resolved_request.progress_callback,
            should_stop=resolved_request.should_stop,
            injection_order=resolved_request.injection_order,
            rt_prior_library=resolved_request.rt_prior_library,
            model_selection_expected_diff_approvals=(
                resolved_request.model_selection_expected_diff_approvals
            ),
        )

    def execute_runtime(
        self,
        request: TargetedApplicationRequest,
        runtime: AttemptRuntime,
    ) -> TargetedRuntimeApplicationResult:
        """Run the bounded no-RAW process-result settlement consumer."""

        if self.runtime_runner is None:
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires runtime_runner for execute_runtime"
            )
        resolved_request = _resolve_application_request(request)
        envelopes = self.runtime_runner(resolved_request, runtime)
        return settle_targeted_process_worker_results(runtime, envelopes)

    def execute_runtime_lifecycle(
        self,
        request: TargetedApplicationRequest,
        runtime: AttemptRuntime,
        *,
        resource_reservations: Sequence[RuntimeResourceReservation],
        observability_ledger: RuntimeObservabilityLedger,
    ) -> TargetedRuntimeLifecycleApplicationResult:
        """Reserve, dispatch, settle, and require one supervisor acknowledgement."""

        if self.runtime_lifecycle_runner is None:
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires runtime_lifecycle_runner "
                "for execute_runtime_lifecycle"
            )
        if (
            self.runtime_supervisor_finalizer is None
            and self.runtime_recovery_marker is None
        ):
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires runtime_supervisor_finalizer "
                "or runtime_recovery_marker for execute_runtime_lifecycle"
            )
        resolved_request = _resolve_application_request(request)
        state = prepare_targeted_runtime_lifecycle(runtime, resource_reservations)
        if self.runtime_recovery_marker is not None:
            self.runtime_recovery_marker.record_launch(runtime)
        batch = self.runtime_lifecycle_runner(resolved_request, state)
        supervisor_finalizer = self.runtime_supervisor_finalizer
        if self.runtime_recovery_marker is not None:
            marker = self.runtime_recovery_marker
            delegated_finalizer = supervisor_finalizer

            def durable_finalizer(
                coordinator: CoordinatorTerminalRecord,
                proposed: SupervisorFinalizationRecord,
            ) -> SupervisorFinalizationRecord:
                submitted = (
                    proposed
                    if delegated_finalizer is None
                    else delegated_finalizer(coordinator, proposed)
                )
                accepted = accept_supervisor_finalization(
                    coordinator,
                    submitted,
                    observability_ledger=proposed.observability_ledger,
                )
                return marker(coordinator, accepted)

            supervisor_finalizer = durable_finalizer
        if supervisor_finalizer is None:
            raise TargetedRuntimeAdapterError(
                "Targeted runtime finalizer unexpectedly remained unresolved"
            )
        return finalize_targeted_runtime_worker_batch(
            state,
            batch,
            observability_ledger=observability_ledger,
            supervisor_finalizer=supervisor_finalizer,
        )


def build_targeted_application_request(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: (
        Mapping[str, ExpectedDiffApprovalRecord] | None
    ) = None,
) -> TargetedApplicationRequest:
    return TargetedApplicationRequest(
        config=config,
        targets=tuple(targets),
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        rt_prior_library=rt_prior_library,
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
    )


def _resolve_application_request(
    request: TargetedApplicationRequest,
) -> TargetedApplicationRequest:
    if not isinstance(request, TargetedApplicationRequest):
        raise TypeError("request must be a TargetedApplicationRequest")
    resolved_approvals = resolve_model_selection_expected_diff_approvals(
        request.config,
        request.model_selection_expected_diff_approvals,
    )
    return replace(
        request,
        model_selection_expected_diff_approvals=resolved_approvals,
    )
