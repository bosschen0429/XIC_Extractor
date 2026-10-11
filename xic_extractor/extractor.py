"""Public Targeted extraction entry points."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import RunOutput

__all__ = ["acquire", "run"]


def run(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
) -> RunOutput:
    from xic_extractor.applications.targeted import application

    return application.build_canonical_targeted_application_service(acquire).execute(
        application.build_targeted_application_request(
            config,
            targets,
            progress_callback=progress_callback,
            should_stop=should_stop,
            injection_order=injection_order,
        )
    )


def acquire(
    config: ExtractionConfig,
    targets: list[Target],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    acquisition_sources_by_sample: Any = None,
) -> RunOutput:
    from xic_extractor.extraction.pipeline import run_pipeline

    return run_pipeline(
        config,
        targets,
        progress_callback=progress_callback,
        should_stop=should_stop,
        injection_order=injection_order,
        acquisition_sources_by_sample=acquisition_sources_by_sample,
    )
