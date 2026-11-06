from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_mass_recalibration import PreparedRawMassRecalibration

if TYPE_CHECKING:
    from xic_extractor.extraction.result_models import (
        RawFileExtractionResult,
        RunOutput,
    )


def run_process(
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

    raw_results = collect_raw_file_results_process(
        config,
        tuple(targets),
        raw_paths,
        defer_analyte_selection=defer_analyte_selection,
        progress_callback=progress_callback,
        should_stop=should_stop,
        acquisition_sources_by_sample=acquisition_sources_by_sample,
        mass_recalibration_by_sample=mass_recalibration_by_sample,
    )

    file_results = [result.file_result for result in raw_results]
    diagnostics: list[DiagnosticRecord] = []
    for result in raw_results:
        diagnostics.extend(result.diagnostics)

    return result_models.RunOutput(file_results=file_results, diagnostics=diagnostics)


def collect_raw_file_results_process(
    config: ExtractionConfig,
    targets: tuple[Target, ...],
    raw_paths: list[Path],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    runner: Callable[..., list[Any]] | None = None,
    mass_recalibration_by_sample: Mapping[str, PreparedRawMassRecalibration]
    | None = None,
    defer_analyte_selection: bool = False,
) -> list[RawFileExtractionResult]:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        collect_ordered_results,
        run_raw_file_jobs,
    )

    if should_stop is not None and should_stop():
        return []
    jobs = [
        RawFileJob(
            defer_analyte_selection=defer_analyte_selection,
            raw_index=index,
            raw_path=raw_path,
            config=config,
            targets=targets,
            expected_acquisition_source=_expected_source(
                raw_path,
                acquisition_sources_by_sample,
            ),
            prepared_mass_recalibration=(
                None
                if not mass_recalibration_by_sample
                else mass_recalibration_by_sample[raw_path.stem]
            ),
        )
        for index, raw_path in enumerate(raw_paths, start=1)
    ]
    raw_runner = runner if runner is not None else run_raw_file_jobs
    results = raw_runner(
        jobs,
        max_workers=config.parallel_workers,
        should_stop=should_stop,
        progress_callback=progress_callback,
        total=len(raw_paths),
    )
    return collect_ordered_results(results)


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
