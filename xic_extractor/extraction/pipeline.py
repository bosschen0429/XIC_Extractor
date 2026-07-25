from __future__ import annotations

from collections.abc import Callable
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
        )

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
    write_outputs(config, targets, output)
    return output
