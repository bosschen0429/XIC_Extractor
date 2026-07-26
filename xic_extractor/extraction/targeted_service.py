from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.execution_runtime import AttemptRuntime
from xic_extractor.rt_prior_library import LibraryEntry

from .targeted_inputs import resolve_model_selection_expected_diff_approvals
from .targeted_runtime_adapter import (
    TargetedProcessWorkerResultEnvelope,
    TargetedRuntimeAdapterError,
    TargetedRuntimeApplicationResult,
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
                "TargetedApplicationService requires runtime_runner for "
                "execute_runtime"
            )
        resolved_request = _resolve_application_request(request)
        envelopes = self.runtime_runner(resolved_request, runtime)
        return settle_targeted_process_worker_results(runtime, envelopes)


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
