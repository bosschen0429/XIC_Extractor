from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
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
from xic_extractor.processing_identity import ProcessingRequestIdentity
from xic_extractor.rt_prior_library import LibraryEntry
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

from .targeted_inputs import resolve_model_selection_expected_diff_approvals
from .targeted_runtime_adapter import (
    TargetedRuntimeAdapterError,
    TargetedRuntimeLifecycleApplicationResult,
    TargetedRuntimeWorkerBatch,
    execute_targeted_runtime_lifecycle,
)

if TYPE_CHECKING:
    from xic_extractor.extraction.targeted_diagnostic_delivery import (
        TargetedDiagnosticDeliveryCandidate,
    )
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


@dataclass(frozen=True, slots=True)
class FinalizedTargetMs1BackfillDecision:
    """One Target cell finalized by the shared MS1 selection owner."""

    sample: ResolvedCanonicalSampleIdentity
    variant: Literal["numeric", "no_write"]
    value: float | None
    no_write_reason: str = ""


@dataclass(frozen=True, slots=True)
class FinalizedTargetMs1BackfillResult:
    """A no-NL Target finalized from the constrained MS1 backfill path."""

    target: Target
    processing_request: ProcessingRequestIdentity
    canonical_samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    decisions: tuple[FinalizedTargetMs1BackfillDecision, ...]

    def __post_init__(self) -> None:
        if self.target.neutral_loss_da is not None:
            raise ValueError(
                "MS1 backfill result requires a neutral-loss-disabled Target"
            )
        if len(self.decisions) != len(self.canonical_samples) or any(
            decision.sample is not sample
            for decision, sample in zip(
                self.decisions,
                self.canonical_samples,
                strict=True,
            )
        ):
            raise ValueError("MS1 backfill decisions must cover the sample population")


FinalizedTargetProductionResult: TypeAlias = (
    FinalizedTargetApplicationResult | FinalizedTargetMs1BackfillResult
)


class TargetedRuntimeLifecycleRunner(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        aggregate: RuntimeAttemptAggregate,
    ) -> TargetedRuntimeWorkerBatch: ...


class TargetedFinalizedWorkflowRunner(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        acquisition_evidence: RunOutput,
    ) -> tuple[FinalizedTargetProductionResult, ...]: ...


class TargetedOutputPublisher(Protocol):
    def __call__(
        self,
        request: TargetedApplicationRequest,
        finalized_results: tuple[FinalizedTargetProductionResult, ...],
        acquisition_evidence: RunOutput,
    ) -> TargetedPublication: ...


class TargetedRuntimeFinalizationPersister(Protocol):
    def __call__(
        self,
        proposed: RuntimeFinalizationReceipt,
    ) -> RuntimeFinalizationReceipt: ...


class TargetedDiagnosticMethodRunner(Protocol):
    def __call__(
        self,
        request: TargetedDiagnosticMethodRequest,
        *,
        should_stop: Callable[[], bool] | None = None,
    ) -> TargetedDiagnosticMethodResult | TargetedDiagnosticApplicationOutcome: ...


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
    method_manifest_context: Any = None
    excel_output_path: Any = None
    replace_existing_outputs: bool = False
    output_projection: Literal["finalized", "acquisition_baseline"] = "finalized"


@dataclass(frozen=True)
class TargetedDiagnosticApplicationOutcome:
    """Adapter-neutral diagnostic settlement; legacy always remains active."""

    status: Literal["staged_candidate", "quarantined"]
    method_result: TargetedDiagnosticMethodResult | None
    delivery_candidate: TargetedDiagnosticDeliveryCandidate | None
    quarantine_reason: str = ""


@dataclass(frozen=True)
class TargetedApplicationExecution:
    """One adapter-neutral acquisition with optional finalized publication."""

    active_output: RunOutput
    finalized_results: tuple[FinalizedTargetProductionResult, ...] = ()
    published_paths: tuple[Any, ...] = ()
    diagnostic_method_result: TargetedDiagnosticMethodResult | None = None
    diagnostic_outcome: TargetedDiagnosticApplicationOutcome | None = None


@dataclass(frozen=True, slots=True)
class TargetedPublication:
    """The committed paths and exact output view consumed by their writers."""

    paths: tuple[Any, ...]
    output: RunOutput


@dataclass(frozen=True)
class TargetedApplicationService:
    runner: TargetedRunner
    finalized_workflow_runner: TargetedFinalizedWorkflowRunner | None = None
    output_publisher: TargetedOutputPublisher | None = None
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
        """Acquire once and publish only a complete finalized Target population."""

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
        if (
            resolved_request.should_stop is not None
            and resolved_request.should_stop()
        ):
            return TargetedApplicationExecution(active_output=active_output)
        finalized_runner = self.finalized_workflow_runner
        publisher = self.output_publisher
        if finalized_runner is not None or publisher is not None:
            if finalized_runner is None or publisher is None:
                raise TargetedRuntimeAdapterError(
                    "finalized workflow runner and output publisher must be "
                    "configured together"
                )
            finalized_results = finalized_runner(resolved_request, active_output)
            _validate_finalized_result_population(resolved_request, finalized_results)
            if (
                resolved_request.should_stop is not None
                and resolved_request.should_stop()
            ):
                return TargetedApplicationExecution(
                    active_output=active_output,
                    finalized_results=finalized_results,
                )
            publication = publisher(
                resolved_request,
                finalized_results,
                active_output,
            )
            return TargetedApplicationExecution(
                active_output=publication.output,
                finalized_results=finalized_results,
                published_paths=publication.paths,
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
        diagnostic_execution = diagnostic_runner(
            diagnostic_request,
            should_stop=resolved_request.should_stop,
        )
        if isinstance(diagnostic_execution, TargetedDiagnosticApplicationOutcome):
            return TargetedApplicationExecution(
                active_output=active_output,
                diagnostic_method_result=diagnostic_execution.method_result,
                diagnostic_outcome=diagnostic_execution,
            )
        return TargetedApplicationExecution(
            active_output=active_output,
            diagnostic_method_result=diagnostic_execution,
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
        observed = self.diagnostic_method_runner(request)
        if isinstance(observed, TargetedDiagnosticApplicationOutcome):
            if observed.method_result is None:
                raise TargetedRuntimeAdapterError(
                    "diagnostic application outcome has no method result"
                )
            return observed.method_result
        return observed


def _validate_finalized_result_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
) -> None:
    if type(results) is not tuple or any(
        type(result)
        not in (FinalizedTargetApplicationResult, FinalizedTargetMs1BackfillResult)
        for result in results
    ):
        raise TargetedRuntimeAdapterError(
            "finalized workflow must return an exact immutable Target result tuple"
        )
    if len(results) != len(request.targets):
        raise TargetedRuntimeAdapterError(
            "finalized workflow did not cover every declared Target"
        )
    request_ids = {
        result.processing_request.processing_request_id for result in results
    }
    sample_populations = {
        tuple(sample.identity.sample_identity_id for sample in result.canonical_samples)
        for result in results
    }
    if len(request_ids) != 1 or len(sample_populations) != 1:
        raise TargetedRuntimeAdapterError(
            "finalized Target results crossed request or sample populations"
        )
    for target, result in zip(request.targets, results, strict=True):
        finalized_target = (
            result.target
            if isinstance(result, FinalizedTargetMs1BackfillResult)
            else result.completed_projection.row_binding.target.definition.target
        )
        if finalized_target != target:
            raise TargetedRuntimeAdapterError(
                "finalized Target result order differs from the declared Target list"
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
    method_manifest_context: Any = None,
    excel_output_path: Any = None,
    replace_existing_outputs: bool = False,
    output_projection: Literal["finalized", "acquisition_baseline"] = "finalized",
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
        method_manifest_context=method_manifest_context,
        excel_output_path=excel_output_path,
        replace_existing_outputs=replace_existing_outputs,
        output_projection=output_projection,
    )


def _resolve_application_request(
    request: TargetedApplicationRequest,
) -> TargetedApplicationRequest:
    if not isinstance(request, TargetedApplicationRequest):
        raise TypeError("request must be a TargetedApplicationRequest")
    if request.output_projection not in {"finalized", "acquisition_baseline"}:
        raise TargetedRuntimeAdapterError(
            "output_projection must be finalized or acquisition_baseline"
        )
    resolved_approvals = resolve_model_selection_expected_diff_approvals(
        request.config,
        request.model_selection_expected_diff_approvals,
    )
    return replace(
        request,
        model_selection_expected_diff_approvals=resolved_approvals,
    )
