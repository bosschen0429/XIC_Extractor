from __future__ import annotations

import gc
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.extraction.target_extraction import extract_raw_file_result
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import FileResult, RunOutput


def run_serial(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    raw_paths: list[Path],
    injection_order: dict[str, int],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
    defer_analyte_selection: bool = False,
) -> RunOutput:
    from xic_extractor.extraction import result_models

    scoring_context_factory = build_scoring_context_factory(config=config)

    file_results: list[FileResult] = []
    diagnostics: list[DiagnosticRecord] = []
    total = len(raw_paths)

    for index, raw_path in enumerate(raw_paths, start=1):
        if should_stop is not None and should_stop():
            break

        extraction_kwargs: dict[str, Any] = {}
        if defer_analyte_selection:
            extraction_kwargs["defer_analyte_selection"] = True
        if mass_recalibration_by_sample:
            extraction_kwargs["prepared_mass_recalibration"] = (
                mass_recalibration_by_sample[raw_path.stem]
            )
        expected_source = _expected_source(raw_path, acquisition_sources_by_sample)
        if expected_source is not None:
            extraction_kwargs["expected_acquisition_source"] = expected_source
        raw_result = extract_raw_file_result(
            index,
            config,
            targets,
            raw_path,
            scoring_context_factory=scoring_context_factory,
            **extraction_kwargs,
        )
        file_results.append(raw_result.file_result)
        diagnostics.extend(raw_result.diagnostics)

        if progress_callback is not None:
            progress_callback(index, total, raw_path.name)
        if index % 50 == 0:
            gc.collect()

    return result_models.RunOutput(file_results=file_results, diagnostics=diagnostics)


def _expected_source(
    raw_path: Path,
    sources_by_sample: Mapping[str, AcquisitionSourceExpectation] | None,
) -> AcquisitionSourceExpectation | None:
    if sources_by_sample is None:
        return None
    try:
        return sources_by_sample[raw_path.stem]
    except KeyError as exc:
        raise ConfigError(
            f"RAW input {raw_path.name!r} has no expected acquisition source."
        ) from exc
