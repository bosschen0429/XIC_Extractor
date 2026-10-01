from __future__ import annotations

from typing import TYPE_CHECKING

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.output import csv_writers
from xic_extractor.output.method_manifest import (
    MethodManifestContext,
    write_method_manifest,
)

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import RunOutput


def write_outputs(
    config: ExtractionConfig,
    targets: list[Target],
    output: RunOutput,
    *,
    method_manifest_context: MethodManifestContext | None = None,
    write_manifest: bool = True,
) -> None:
    if not config.keep_intermediate_csv:
        if write_manifest:
            write_method_manifest(config, targets, context=method_manifest_context)
        return
    csv_writers.write_all(
        config,
        targets,
        output.file_results,
        output.diagnostics,
        emit_score_breakdown=config.emit_score_breakdown,
    )
    if write_manifest:
        write_method_manifest(config, targets, context=method_manifest_context)
