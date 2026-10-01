from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.rt_prior_library import LibraryEntry
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecords
from xic_extractor.processing_identity import AcquisitionSourceExpectation

if TYPE_CHECKING:
    from xic_extractor.extraction.jobs import ScoringInputs
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
    rt_prior_library: dict[tuple[str, str], LibraryEntry],
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
) -> RunOutput:
    from xic_extractor.extraction import result_models
    from xic_extractor.extraction.jobs import ScoringInputs

    istd_targets = tuple(target for target in targets if target.is_istd)
    istd_rts_by_sample = collect_istd_prepass_process(
        config,
        istd_targets,
        raw_paths,
        should_stop=should_stop,
        acquisition_sources_by_sample=acquisition_sources_by_sample,
    )
    scoring_inputs = ScoringInputs(
        injection_order=injection_order,
        istd_rts_by_sample=istd_rts_by_sample,
        rt_prior_library=rt_prior_library,
    )
    raw_results = collect_raw_file_results_process(
        config,
        tuple(targets),
        raw_paths,
        scoring_inputs,
        progress_callback=progress_callback,
        should_stop=should_stop,
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
        acquisition_sources_by_sample=acquisition_sources_by_sample,
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
    scoring_inputs: ScoringInputs,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    runner: Callable[..., list[Any]] | None = None,
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
            raw_index=index,
            raw_path=raw_path,
            config=config,
            targets=targets,
            scoring_inputs=scoring_inputs,
            model_selection_expected_diff_approvals=(
                dict(model_selection_expected_diff_approvals or {})
            ),
            expected_acquisition_source=_expected_source(
                raw_path,
                acquisition_sources_by_sample,
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


def collect_istd_prepass_process(
    config: ExtractionConfig,
    istd_targets: tuple[Target, ...],
    raw_paths: list[Path],
    *,
    should_stop: Callable[[], bool] | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    runner: Callable[..., list[Any]] | None = None,
) -> dict[str, dict[str, float]]:
    from xic_extractor.extraction.jobs import (
        RawFileJob,
        collect_istd_prepass_results,
        run_istd_prepass_jobs,
    )

    if not istd_targets:
        return {}
    if should_stop is not None and should_stop():
        return {}
    jobs = [
        RawFileJob(
            raw_index=index,
            raw_path=raw_path,
            config=config,
            targets=istd_targets,
            expected_acquisition_source=_expected_source(
                raw_path,
                acquisition_sources_by_sample,
            ),
        )
        for index, raw_path in enumerate(raw_paths, start=1)
    ]
    prepass_runner = runner if runner is not None else run_istd_prepass_jobs
    results = prepass_runner(
        jobs,
        max_workers=config.parallel_workers,
        should_stop=should_stop,
    )

    istd_rts_by_sample: dict[str, dict[str, float]] = {}
    ordered_results = collect_istd_prepass_results(results)
    for result in ordered_results:
        for istd_label, anchor_rt in result.anchors.items():
            istd_rts_by_sample.setdefault(istd_label, {})[result.sample_name] = (
                anchor_rt
            )
    return istd_rts_by_sample


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
