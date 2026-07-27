from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import AttemptRuntime
from xic_extractor.execution_runtime_finalization import (
    RuntimeResourceReservation,
)
from xic_extractor.execution_runtime_marker import OwnerPathRuntimeMarker
from xic_extractor.execution_runtime_owner import (
    RuntimeAttemptAggregate,
    RuntimeFinalizationReceipt,
    RuntimeTelemetryProvider,
)
from xic_extractor.rt_prior_library import LibraryEntry

from .targeted_inputs import resolve_model_selection_expected_diff_approvals
from .targeted_runtime_adapter import (
    TargetedRuntimeAdapterError,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    execute_targeted_runtime_lifecycle,
)

if TYPE_CHECKING:
    from xic_extractor.extraction.targeted_diagnostic_method import (
        TargetedDiagnosticMethodRequest,
        TargetedDiagnosticMethodResult,
    )
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


class TargetedRuntimeLifecycleRunner(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        aggregate: RuntimeAttemptAggregate,
    ) -> TargetedRuntimeWorkerBatch: ...


class TargetedRuntimeFinalizationPersister(Protocol):
    def __call__(
        self,
        proposed: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt: ...


class TargetedDiagnosticMethodRunner(Protocol):
    def __call__(
        self,
        request: TargetedDiagnosticMethodRequest,
    ) -> TargetedDiagnosticMethodResult: ...


class TargetedDiagnosticMethodRequestFactory(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        authoritative_legacy_output: RunOutput,
    ) -> TargetedDiagnosticMethodRequest | None: ...


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
class TargetedApplicationExecution:
    """One adapter-neutral run; legacy remains active during diagnostic migration."""

    active_output: RunOutput
    diagnostic_method_result: TargetedDiagnosticMethodResult | None = None


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner
    runtime_lifecycle_runner: TargetedRuntimeLifecycleRunner | None = None
    runtime_finalization_persister: TargetedRuntimeFinalizationPersister | None = None
    runtime_recovery_marker: OwnerPathRuntimeMarker | None = None
    diagnostic_method_runner: TargetedDiagnosticMethodRunner | None = None
    diagnostic_method_request_factory: TargetedDiagnosticMethodRequestFactory | None = (
        None
    )

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        """Preserve the existing application API while using the canonical path."""

        return self.execute_application(request).active_output

    def execute_application(
        self,
        request: TargetedApplicationRequest,
    ) -> TargetedApplicationExecution:
        """Run legacy authority and an optional bounded diagnostic successor once."""

        resolved_request = _resolve_application_request(request)
        active_output = self.runner(
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
        diagnostic_runner = self.diagnostic_method_runner
        diagnostic_factory = self.diagnostic_method_request_factory
        if diagnostic_runner is None and diagnostic_factory is None:
            return TargetedApplicationExecution(active_output=active_output)
        if diagnostic_runner is None or diagnostic_factory is None:
            raise TargetedRuntimeAdapterError(
                "diagnostic method runner and request factory must be "
                "configured together"
            )
        diagnostic_request = diagnostic_factory(resolved_request, active_output)
        if diagnostic_request is None:
            return TargetedApplicationExecution(active_output=active_output)
        diagnostic_result = diagnostic_runner(diagnostic_request)
        return TargetedApplicationExecution(
            active_output=active_output,
            diagnostic_method_result=diagnostic_result,
        )

    def execute_runtime_lifecycle(
        self,
        request: TargetedApplicationRequest,
        runtime: AttemptRuntime,
        *,
        resource_reservations: Sequence[RuntimeResourceReservation],
        telemetry_provider: RuntimeTelemetryProvider,
    ) -> TargetedRuntimeLifecycleApplicationResult:
        """Reserve, dispatch, settle, and require one supervisor acknowledgement."""

        if self.runtime_lifecycle_runner is None:
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires runtime_lifecycle_runner "
                "for execute_runtime_lifecycle"
            )
        if (
            self.runtime_finalization_persister is not None
            and self.runtime_recovery_marker is not None
        ):
            raise TargetedRuntimeAdapterError(
                "runtime_finalization_persister and runtime_recovery_marker "
                "are mutually exclusive finalization authorities"
            )
        if (
            self.runtime_finalization_persister is None
            and self.runtime_recovery_marker is None
        ):
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires runtime_finalization_persister "
                "or runtime_recovery_marker for execute_runtime_lifecycle"
            )
        resolved_request = _resolve_application_request(request)
        lifecycle_runner = self.runtime_lifecycle_runner
        if self.runtime_recovery_marker is not None:
            self.runtime_recovery_marker.record_launch(runtime)
        finalization_persister = (
            self.runtime_recovery_marker
            if self.runtime_recovery_marker is not None
            else self.runtime_finalization_persister
        )
        if finalization_persister is None:
            raise TargetedRuntimeAdapterError(
                "Targeted runtime finalizer unexpectedly remained unresolved"
            )
        return execute_targeted_runtime_lifecycle(
            runtime,
            resource_reservations,
            worker_batch_provider=lambda aggregate: lifecycle_runner(
                resolved_request,
                aggregate,
            ),
            telemetry_provider=telemetry_provider,
            finalization_persister=finalization_persister,
        )

    def execute_diagnostic_method(
        self,
        request: TargetedDiagnosticMethodRequest,
    ) -> TargetedDiagnosticMethodResult:
        """Run the bounded no-RAW method through this canonical service owner."""

        if self.diagnostic_method_runner is None:
            raise TargetedRuntimeAdapterError(
                "TargetedApplicationService requires diagnostic_method_runner "
                "for execute_diagnostic_method"
            )
        return self.diagnostic_method_runner(request)


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
