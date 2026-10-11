from __future__ import annotations

from typing import TYPE_CHECKING

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output import csv_writers

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import RunOutput


def write_outputs(
    config: ExtractionConfig,
    targets: list[Target],
    output: RunOutput,
) -> None:
    if not config.keep_intermediate_csv:
        return
    csv_writers.write_all(
        config,
        targets,
        output.file_results,
        output.diagnostics,
    )
