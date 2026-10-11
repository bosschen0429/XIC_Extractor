"""Public Targeted extraction entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import RunOutput
from xic_extractor.peak_detection.model_selection import (
    ExpectedDiffApprovalRecords,
)

__all__ = ["acquire", "run"]


def run(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
) -> RunOutput:
    from xic_extractor.applications.targeted import application

    return application.build_canonical_targeted_application_service(acquire).execute(
        application.build_targeted_application_request(
            config,
            targets,
            progress_callback=progress_callback,
            should_stop=should_stop,
            injection_order=injection_order,
            model_selection_expected_diff_approvals=(
                model_selection_expected_diff_approvals
            ),
        )
    )


def acquire(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    acquisition_sources_by_sample: Any = None,
    *,
    publish_outputs: Literal[False] = False,
) -> RunOutput:
    from xic_extractor.extraction.pipeline import run_pipeline

    return run_pipeline(
        config,
        targets,
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        model_selection_expected_diff_approvals=model_selection_expected_diff_approvals,  # noqa: E501
        acquisition_sources_by_sample=acquisition_sources_by_sample,
        publish_outputs=publish_outputs,
    )
