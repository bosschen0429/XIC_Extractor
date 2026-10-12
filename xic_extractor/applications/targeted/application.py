from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeAlias

from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.execution._finalization import RuntimeResourceReservation
from xic_extractor.execution._lifecycle import AttemptRuntime
from xic_extractor.execution.attempt import (
    RuntimeAttemptAggregate,
    RuntimeFinalizationReceipt,
    RuntimeTelemetryProvider,
)
from xic_extractor.execution.recovery import OwnerPathRuntimeMarker
from xic_extractor.extraction.targeted_runtime_adapter import (
    TargetedRuntimeAdapterError,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    execute_targeted_runtime_lifecycle,
)

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import RunOutput
    from xic_extractor.processing_identity import (
        AcquisitionSourceExpectation,
    )


class TargetedOutputBoundaryError(RuntimeError):
    """Raised before a complete finalized output set can be published."""


class TargetedRunner(Protocol):
    def __call__(
        self,
        config: ExtractionConfig,
        targets: list[Target],
        progress_callback: Callable[[int, int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        injection_order: dict[str, int] | None = None,
        acquisition_sources_by_sample: (
            Mapping[str, AcquisitionSourceExpectation] | None
        ) = None,
    ) -> RunOutput: ...


FinalizedTargetProductionResult: TypeAlias = FinalizedTargetApplicationResult


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


@dataclass(frozen=True)
class TargetedApplicationRequest:
    """Headless application input; callbacks are lifecycle adapters, not science."""

    config: ExtractionConfig
    targets: tuple[Target, ...]
    progress_callback: Callable[[int, int, str], None] | None = None
    should_stop: Callable[[], bool] | None = None
    injection_order: dict[str, int] | None = None

    excel_output_path: Path | None = None
    replace_existing_outputs: bool = False


@dataclass(frozen=True)
class TargetedApplicationExecution:
    """One adapter-neutral acquisition with complete finalized publication."""

    active_output: RunOutput
    finalized_results: tuple[FinalizedTargetProductionResult, ...] = ()
    published_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetedPublication:
    """The committed paths and exact output view consumed by their writers."""

    paths: tuple[Path, ...]
    output: RunOutput


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner
    runtime_lifecycle_runner: TargetedRuntimeLifecycleRunner | None = None
    runtime_finalization_persister: TargetedRuntimeFinalizationPersister | None = None
    runtime_recovery_marker: OwnerPathRuntimeMarker | None = None

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        """Preserve the existing application API while using the canonical path."""

        return self.execute_application(request).active_output

    def execute_application(
        self,
        request: TargetedApplicationRequest,
    ) -> TargetedApplicationExecution:
        """Acquire once and publish only a complete finalized Target population."""

        resolved_request = _isolate_application_request(
            _resolve_application_request(request)
        )
        from xic_extractor.applications.targeted.production_workflow import (
            execute_targeted_production_workflow,
            snapshot_targeted_production_inputs,
        )
        from xic_extractor.processing_identity import FingerprintCancelledError

        try:
            input_snapshot = snapshot_targeted_production_inputs(resolved_request)
        except FingerprintCancelledError:
            from xic_extractor.extraction.result_models import RunOutput

            return TargetedApplicationExecution(
                active_output=RunOutput(file_results=[], diagnostics=[])
            )
        runner_request = _isolate_application_request(resolved_request)
        active_output = self.runner(
            runner_request.config,
            list(runner_request.targets),
            progress_callback=runner_request.progress_callback,
            should_stop=runner_request.should_stop,
            injection_order=runner_request.injection_order,
            acquisition_sources_by_sample=input_snapshot.acquisition_sources_by_sample(),
        )
        if resolved_request.should_stop is not None and resolved_request.should_stop():
            return TargetedApplicationExecution(active_output=active_output)
        if not active_output.file_results:
            raise ConfigError("No RAW files were acquired for Targeted extraction.")
        completed_workflow = execute_targeted_production_workflow(
            resolved_request,
            active_output,
            expected_inputs=input_snapshot,
        )
        finalized_results = completed_workflow.finalized_results
        if resolved_request.should_stop is not None and resolved_request.should_stop():
            return TargetedApplicationExecution(
                active_output=active_output,
                finalized_results=finalized_results,
            )
        from xic_extractor.applications.targeted.output_boundary import (
            publish_targeted_outputs,
        )

        publication = publish_targeted_outputs(
            resolved_request,
            completed_workflow,
            active_output,
        )
        return TargetedApplicationExecution(
            active_output=publication.output,
            finalized_results=finalized_results,
            published_paths=publication.paths,
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


def build_targeted_application_request(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    excel_output_path: Path | None = None,
    replace_existing_outputs: bool = False,
) -> TargetedApplicationRequest:
    return TargetedApplicationRequest(
        config=config,
        targets=tuple(targets),
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        excel_output_path=excel_output_path,
        replace_existing_outputs=replace_existing_outputs,
    )


def _resolve_application_request(
    request: TargetedApplicationRequest,
) -> TargetedApplicationRequest:
    if not isinstance(request, TargetedApplicationRequest):
        raise TypeError("request must be a TargetedApplicationRequest")
    return request


def _isolate_application_request(
    request: TargetedApplicationRequest,
) -> TargetedApplicationRequest:
    """Copy mutable data inputs while retaining lifecycle callback adapters."""

    return replace(
        request,
        config=deepcopy(request.config),
        targets=deepcopy(request.targets),
        injection_order=deepcopy(request.injection_order),
    )


def build_canonical_targeted_application_service(
    runner: TargetedRunner,
) -> TargetedApplicationService:
    """Build the formal Targeted application for Python, CLI, and GUI."""

    return TargetedApplicationService(runner)
