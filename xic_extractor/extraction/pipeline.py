from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from xic_extractor.config import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.output_dispatch import write_outputs
from xic_extractor.extraction.paired_area_ratio_projection import (
    apply_paired_area_ratio_projection,
)
from xic_extractor.extraction.targeted_ms1_shape_identity_projection import (
    apply_targeted_ms1_shape_identity_projection,
    load_targeted_ms1_shape_identity_supports,
)
from xic_extractor.peak_detection.model_selection import ExpectedDiffApprovalRecords
from xic_extractor.processing_identity import AcquisitionSourceExpectation
from xic_extractor.raw_reader import RawReaderError, preflight_raw_reader
from xic_extractor.rt_prior_library import LibraryEntry

from .targeted_inputs import resolve_targeted_inputs, resolve_targeted_raw_paths

if TYPE_CHECKING:
    from xic_extractor.extractor import RunOutput


def run_pipeline(
    config: ExtractionConfig,
    targets: list[Target],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    injection_order: dict[str, int] | None = None,
    rt_prior_library: dict[tuple[str, str], LibraryEntry] | None = None,
    model_selection_expected_diff_approvals: ExpectedDiffApprovalRecords | None = None,
    acquisition_sources_by_sample: Mapping[str, AcquisitionSourceExpectation]
    | None = None,
    publish_outputs: bool = True,
) -> RunOutput:
    reader_errors = preflight_raw_reader(config.dll_dir)
    if reader_errors:
        raise RawReaderError(" ".join(reader_errors))

    raw_paths = resolve_targeted_raw_paths(config)
    resolved_inputs = resolve_targeted_inputs(
        config,
        raw_paths=raw_paths,
        injection_order=injection_order,
        rt_prior_library=rt_prior_library,
        model_selection_expected_diff_approvals=(
            model_selection_expected_diff_approvals
        ),
    )
    acquisition_kwargs = (
        {}
        if acquisition_sources_by_sample is None
        else {"acquisition_sources_by_sample": acquisition_sources_by_sample}
    )

    if config.parallel_mode == "process":
        from xic_extractor.extraction.process_backend import run_process

        output = run_process(
            config,
            targets,
            raw_paths=raw_paths,
            progress_callback=progress_callback,
            should_stop=should_stop,
            injection_order=resolved_inputs.injection_order,
            rt_prior_library=resolved_inputs.rt_prior_library,
            model_selection_expected_diff_approvals=(
                resolved_inputs.model_selection_expected_diff_approvals
            ),
            **acquisition_kwargs,
        )
    else:
        from xic_extractor.extraction.serial_backend import run_serial

        output = run_serial(
            config,
            targets,
            raw_paths=raw_paths,
            progress_callback=progress_callback,
            should_stop=should_stop,
            injection_order=resolved_inputs.injection_order,
            rt_prior_library=resolved_inputs.rt_prior_library,
            model_selection_expected_diff_approvals=(
                resolved_inputs.model_selection_expected_diff_approvals
            ),
            **acquisition_kwargs,
        )

    if acquisition_sources_by_sample is not None:
        output = _bind_acquisition_sources(output, acquisition_sources_by_sample)
    output = apply_paired_area_ratio_projection(output, targets=targets)
    if config.targeted_ms1_shape_identity_support_tsv is not None:
        try:
            targeted_ms1_shape_identity_supports = (
                load_targeted_ms1_shape_identity_supports(
                    config.targeted_ms1_shape_identity_support_tsv,
                    activation_policy=(
                        config.targeted_ms1_shape_identity_activation_policy
                    ),
                )
            )
        except (FileNotFoundError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
        output = apply_targeted_ms1_shape_identity_projection(
            output,
            targets=targets,
            supports=targeted_ms1_shape_identity_supports,
        )
    if publish_outputs:
        write_outputs(config, targets, output)
    return output


def _bind_acquisition_sources(
    output: RunOutput,
    sources_by_sample: Mapping[str, AcquisitionSourceExpectation],
) -> RunOutput:
    file_results = []
    seen_samples: set[str] = set()
    for file_result in output.file_results:
        sample_name = file_result.sample_name
        if sample_name in seen_samples:
            raise ConfigError(
                f"Duplicate acquisition output for sample {sample_name!r}."
            )
        seen_samples.add(sample_name)
        expectation = sources_by_sample.get(sample_name)
        if expectation is None:
            raise ConfigError(
                f"Acquisition output sample {sample_name!r} has no processing identity."
            )
        source = expectation.source
        results = {}
        for target_label, result in file_result.results.items():
            candidate = result.candidate_ms2_evidence
            product = None if candidate is None else candidate.best_product_evidence
            if product is not None:
                assert candidate is not None
                if product.acquisition_source_id not in (
                    "",
                    source.acquisition_source_id,
                ):
                    raise ConfigError(
                        "Acquisition output contains MS2 evidence from a different "
                        f"RAW source for {sample_name!r}/{target_label!r}."
                    )
                candidate = replace(
                    candidate,
                    best_product_evidence=replace(
                        product,
                        acquisition_source_id=source.acquisition_source_id,
                    ),
                )
                result = replace(result, candidate_ms2_evidence=candidate)
            results[target_label] = result
        file_results.append(replace(file_result, results=results))
    return replace(output, file_results=file_results)
