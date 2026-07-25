from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.rt_prior_library import LibraryEntry

from .targeted_inputs import resolve_model_selection_expected_diff_approvals

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

    def execute(self, request: TargetedApplicationRequest) -> RunOutput:
        resolved_approvals = resolve_model_selection_expected_diff_approvals(
            request.config,
            request.model_selection_expected_diff_approvals,
        )
        return self.runner(
            request.config,
            list(request.targets),
            progress_callback=request.progress_callback,
            should_stop=request.should_stop,
            injection_order=request.injection_order,
            rt_prior_library=request.rt_prior_library,
            model_selection_expected_diff_approvals=resolved_approvals,
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
