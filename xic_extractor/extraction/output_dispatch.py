from __future__ import annotations

from typing import TYPE_CHECKING

from xic_extractor.config import ExtractionConfig, Target
from xic_extractor.output import csv_writers
from xic_extractor.output.messages import DiagnosticRecord
from xic_extractor.output.method_manifest import (
    MethodManifestContext,
    write_method_manifest,
)
from xic_extractor.output.peak_candidate_boundaries import (
    write_peak_candidate_boundaries_for_file_results,
)
from xic_extractor.output.peak_candidate_boundary_summary import (
    write_peak_candidate_boundary_summary_for_file_results,
)
from xic_extractor.output.peak_candidates import write_peak_candidates_for_file_results
from xic_extractor.output.peak_region_selection_shadow import (
    write_peak_region_selection_shadow_for_file_results,
)
from xic_extractor.output.selected_envelope_diagnostics import (
    write_selected_envelope_diagnostics_for_file_results,
)
from xic_extractor.output.target_pair_rt_auto_reselection import (
    write_target_pair_rt_auto_reselection_for_file_results,
)

if TYPE_CHECKING:
    from xic_extractor.extractor import RunOutput


def write_outputs(
    config: ExtractionConfig,
    targets: list[Target],
    output: RunOutput,
    *,
    method_manifest_context: MethodManifestContext | None = None,
    write_manifest: bool = True,
) -> None:
    if config.target_pair_rt_calibration_path is not None and not (
        config.emit_peak_candidates
    ):
        output.diagnostics.append(
            DiagnosticRecord(
                "RUN",
                "",
                "TARGET_PAIR_RT_AUTO_RESELECTION_SKIPPED",
                "target_pair_rt_calibration_path configured but "
                "emit_peak_candidates=false; shadow TSV not requested",
            )
        )
    if config.emit_peak_candidates:
        write_peak_candidates_for_file_results(
            config.output_csv.with_name("peak_candidates.tsv"),
            output.file_results,
        )
        write_peak_candidate_boundaries_for_file_results(
            config.output_csv.with_name("peak_candidate_boundaries.tsv"),
            output.file_results,
        )
        write_peak_candidate_boundary_summary_for_file_results(
            config.output_csv.with_name("peak_candidate_boundary_summary.tsv"),
            output.file_results,
        )
        write_peak_region_selection_shadow_for_file_results(
            config.output_csv.with_name("peak_region_selection_shadow.tsv"),
            output.file_results,
        )
        write_selected_envelope_diagnostics_for_file_results(
            config.output_csv.with_name("selected_envelope_diagnostics.tsv"),
            output.file_results,
        )
        if config.target_pair_rt_calibration_path is not None:
            write_target_pair_rt_auto_reselection_for_file_results(
                config.output_csv.with_name("target_pair_rt_auto_reselection.tsv"),
                output.file_results,
                targets=targets,
                calibration_path=config.target_pair_rt_calibration_path,
                target_config_hash=config.target_config_hash or None,
            )
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
