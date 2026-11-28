"""Complete Targeted acquisition into bound scientific and public result models."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

from xic_extractor.alignment.cell_decision.owner import ProductionCellDecision
from xic_extractor.alignment.completion import (
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowStageFailure,
    complete_analysis,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.projection.target.pre_cell import TargetCellSubject
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.applications.targeted.application import (
    FinalizedTargetProductionResult,
    TargetedApplicationRequest,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.result_models import (
    ExtractionResult,
    FileResult,
    RunOutput,
)
from xic_extractor.extraction.targeted_capture_population import (
    build_targeted_capture_population,
    targeted_capture_candidate_id,
    targeted_signal_processing_profile,
)
from xic_extractor.extraction.targeted_cid_nl_assessment import (
    assess_targeted_cid_nl_samples,
)
from xic_extractor.extraction.targeted_inputs import (
    ResolvedTargetedInputs,
    build_targeted_acquisition_sources,
    resolve_targeted_inputs,
)
from xic_extractor.extraction.targeted_istd_backfill import (
    resolve_targeted_istd_backfill,
)
from xic_extractor.extraction.targeted_peak_evidence import (
    reselect_targeted_peak_evidence,
)
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
    paired_istd_positions,
)
from xic_extractor.output.messages import DiagnosticRecord, build_ms2_diagnostic_records
from xic_extractor.output.targeted_product_model import (
    TargetedProductContext,
    TargetedProductProjection,
)
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    AcquisitionSourceIdentity,
)
from xic_extractor.sample_metadata import SampleMetadata


@dataclass(frozen=True, slots=True)
class TargetedProductionInputSnapshot:
    """Admitted Targeted inputs and their exact acquisition identity bindings."""

    config: ExtractionConfig
    raw_file_states: tuple[tuple[int, int, int, int, int], ...]
    resolved_inputs: ResolvedTargetedInputs
    targets: tuple[Target, ...]
    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]

    def acquisition_sources_by_sample(self) -> dict[str, AcquisitionSourceExpectation]:
        """Project the admitted RAW identities for the acquisition runner."""

        states_by_stem = {
            path.stem: state
            for path, state in zip(
                self.resolved_inputs.raw_paths,
                self.raw_file_states,
                strict=True,
            )
        }
        return {
            stem: AcquisitionSourceExpectation(
                source=source,
                path_identity=states_by_stem[stem],
            )
            for stem, source in self.acquisition_sources.items()
        }


@dataclass(frozen=True, slots=True)
class _PreparedTargetedProductionWorkflow:
    """Owner-local acquisition population for per-Target scientific completion."""

    acquisition_sources: Mapping[str, AcquisitionSourceIdentity]
    rt_frame: SampleRtFrame
    samples: tuple[SampleMetadata, ...]
    targets: tuple[Target, ...]
    results_by_sample: tuple[tuple[SampleMetadata, FileResult], ...]
    raw_stems: frozenset[str]
    acquisition_evidence: RunOutput


@dataclass(frozen=True, slots=True)
class CompletedTargetedProductionWorkflow:
    """Owner-completed science and a publication model consumed without mutation."""

    finalized_results: tuple[FinalizedTargetProductionResult, ...]
    config: ExtractionConfig
    targets: tuple[Target, ...]
    publication_output: RunOutput

    def __len__(self) -> int:
        return len(self.finalized_results)

    def __iter__(self) -> Iterator[FinalizedTargetProductionResult]:
        return iter(self.finalized_results)

    def __getitem__(self, index: int) -> FinalizedTargetProductionResult:
        return self.finalized_results[index]


def execute_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
    *,
    expected_inputs: TargetedProductionInputSnapshot | None = None,
) -> CompletedTargetedProductionWorkflow:
    """Complete the admitted Target population and build its publication model."""

    prepared = _prepare_targeted_production_workflow(
        request, acquisition_evidence, expected_inputs=expected_inputs
    )
    finalized_results = tuple(
        _execute_target(prepared, request, target) for target in prepared.targets
    )
    publication_output = _build_publication_output(
        finalized_results, prepared, config=request.config
    )
    return CompletedTargetedProductionWorkflow(
        finalized_results=finalized_results,
        config=request.config,
        targets=request.targets,
        publication_output=publication_output,
    )


def _build_publication_output(
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    prepared: _PreparedTargetedProductionWorkflow,
    *,
    config: ExtractionConfig,
) -> RunOutput:
    files: dict[SampleMetadata, tuple[FileResult, dict[str, ExtractionResult]]] = {
        sample: (replace(file_result, sample_metadata=sample), {})
        for sample, file_result in prepared.results_by_sample
    }
    cells: dict[tuple[str, str], _RenderCell] = {}
    targets_by_label = {}
    backfill_diagnostics = {}
    for result in finalized_results:
        target = result.completed_projection.row_binding.target
        target_label = target.label
        targets_by_label[target_label] = target
        backfills = {
            backfill.target_cell.sample_key: backfill
            for backfill in result.scientific_core.targeted_backfill_decisions
        }
        for decision in result.completed_projection.decisions:
            sample = cast(TargetCellSubject, decision.subject).canonical_sample
            file_result, rendered = files[sample]
            backfill_decision = backfills.get(sample.sample_name)
            if backfill_decision is not None:
                backfill_diagnostics[(file_result.sample_name, target_label)] = (
                    DiagnosticRecord(
                        file_result.sample_name,
                        target_label,
                        "BACKFILL_ACCEPTED"
                        if backfill_decision.matched_candidate is not None
                        else "BACKFILL_HOLD",
                        "Cross-sample confirmed peak localization: "
                        + "; ".join(backfill_decision.reason_codes)
                        + "; direct CID/NL remains HOLD; "
                        + (
                            "ISTD identity confirmed by Backfill."
                            if target.is_istd
                            and backfill_decision.matched_candidate is not None
                            else "confirmed=False."
                        ),
                    )
                )
            cell = _RenderCell(
                extraction=file_result.results[target_label],
                decision=decision,
                backfill_supported=(
                    backfill_decision is not None
                    and backfill_decision.matched_candidate is not None
                ),
            )
            cells[(file_result.sample_name, target_label)] = cell
            rendered[target_label] = cell.render()
    position_diagnostics = _apply_paired_istd_positions(
        {file_result.sample_name: rendered for file_result, rendered in files.values()},
        cells,
        targets_by_label,
    )
    ordered_files = prepared.rt_frame.injections.ordered_rows(
        files.items(),
        stem=lambda item: item[0].raw_stem,
        within_sample=lambda item: (item[1][0].sample_name,),
    )
    return RunOutput(
        file_results=[
            replace(file_result, results=rendered)
            for _, (file_result, rendered) in ordered_files
        ],
        diagnostics=[
            *(
                record
                for record in prepared.acquisition_evidence.diagnostics
                if record.issue not in {"NL_FAIL", "NO_MS2"}
            ),
            *(
                record
                for _, (file_result, rendered) in ordered_files
                for label in file_result.results
                for record in build_ms2_diagnostic_records(
                    file_result.sample_name,
                    targets_by_label[label],
                    rendered[label],
                    config,
                )
            ),
            *(
                backfill_diagnostics[(file.sample_name, label)]
                for file in prepared.acquisition_evidence.file_results
                for label in file.results
                if (file.sample_name, label) in backfill_diagnostics
            ),
            *position_diagnostics,
        ],
    )


OFF_PAIRED_ISTD_POSITION = "analyte_peak_off_paired_istd_position"


@dataclass(frozen=True)
class _RenderCell:
    """One finalized sample/target decision and the evidence that renders it."""

    extraction: ExtractionResult
    decision: ProductionCellDecision
    backfill_supported: bool

    def render(
        self,
        *,
        paired_istd_identity: bool = False,
        no_write_reason: str = "",
    ) -> ExtractionResult:
        return _finalized_extraction_result(
            self.extraction,
            target=cast(TargetCellSubject, self.decision.subject).target,
            value=self.decision.value,
            no_write_reason=no_write_reason
            or (
                ""
                if self.decision.variant == "numeric"
                else f"finalized_{self.decision.variant}"
            ),
            evidence=self.decision.evidence,
            backfill_supported=self.backfill_supported,
            paired_istd_identity=paired_istd_identity,
        )


def _apply_paired_istd_positions(
    rendered_by_sample: Mapping[str, dict[str, ExtractionResult]],
    cells: Mapping[tuple[str, str], _RenderCell],
    targets_by_label: Mapping[str, Target],
) -> list[DiagnosticRecord]:
    """Re-render analytes against their counted paired ISTD.

    The ISTD's final count, including accepted Backfill, is known only once
    every Target of the batch has rendered. A peak at the paired position takes
    its identity; a peak elsewhere is published as no value even with its own
    neutral-loss support, with its measurement kept as a diagnostic event.
    """
    from xic_extractor.extraction.target_extraction import current_target_reference_rt
    from xic_extractor.peak_detection.physical_peak_evaluation import (
        physical_peak_support_failures_at_reference,
    )

    diagnostics: list[DiagnosticRecord] = []
    for label, target in targets_by_label.items():
        pair = target.istd_pair
        if target.is_istd or not pair or target.neutral_loss_da is None:
            continue
        analyte = {
            sample: results[label]
            for sample, results in rendered_by_sample.items()
            if label in results
        }
        istd = {
            sample: results[pair]
            for sample, results in rendered_by_sample.items()
            if pair in results
        }
        positions = paired_istd_positions(
            analyte,
            istd,
            expected_offset_min=current_target_reference_rt(
                target, reference_rt=0.0, paired_target=targets_by_label[pair]
            ),
        )
        for sample in positions.identified:
            rendered_by_sample[sample][label] = cells[(sample, label)].render(
                paired_istd_identity=True
            )
        for sample, deviation in positions.off_position.items():
            measured = analyte[sample]
            rendered_by_sample[sample][label] = cells[(sample, label)].render(
                no_write_reason=OFF_PAIRED_ISTD_POSITION
            )
            physical = measured.physical_evaluation
            reference_rt = current_target_reference_rt(
                target,
                reference_rt=istd[sample].reported_rt,
                paired_target=targets_by_label[pair],
            )
            if (
                physical is not None
                and physical.retained_prepared_signal is not None
                and reference_rt is not None
            ):
                failures = physical_peak_support_failures_at_reference(
                    physical.retained_prepared_signal,
                    physical.segment_universe,
                    reference_rt,
                )
                if failures:
                    diagnostics.append(
                        DiagnosticRecord(
                            sample,
                            label,
                            "PAIRED_POSITION_SUPPORT_REJECTED",
                            "At paired-ISTD-derived reference "
                            f"RT {reference_rt:.4f} min: " + "; ".join(failures),
                        )
                    )
            diagnostics.append(
                DiagnosticRecord(
                    sample,
                    label,
                    "OFF_ISTD_POSITION",
                    f"Selected peak at RT {measured.reported_rt:.3f} min "
                    f"(area {measured.reported_peak_area:.3g}) is {deviation:+.2f} "
                    f"min from the {pair} position; reported as ND.",
                )
            )
    return diagnostics


def _finalized_extraction_result(
    extraction: ExtractionResult,
    *,
    target: Target,
    value: float | None,
    no_write_reason: str,
    evidence: object,
    backfill_supported: bool = False,
    paired_istd_identity: bool = False,
) -> ExtractionResult:
    captured_physical_evaluation = extraction.physical_evaluation
    reported_evaluation = (
        cast(ObservedSearchSubject, evidence).hypothesis.peak.physical_evaluation
        if not no_write_reason
        else None
    )
    extraction = reselect_targeted_peak_evidence(extraction, reported_evaluation)
    projection = _finalized_product_projection(
        extraction.targeted_product_projection,
        target=target,
        evidence=evidence,
        value=value,
        no_write_reason=no_write_reason,
        backfill_supported=backfill_supported,
        paired_istd_identity=paired_istd_identity,
        captured_physical_evaluation=captured_physical_evaluation,
    )
    if not no_write_reason:
        physical_evaluation = cast(
            ObservedSearchSubject, evidence
        ).hypothesis.peak.physical_evaluation
        rendered = replace(
            extraction,
            peak_result=replace(
                extraction.peak_result,
                selected_candidate=None,
                candidates=(),
                candidate_scores=(),
            ),
            selected_hypothesis=None,
            selection_decision=None,
            measured_hypothesis=cast(ObservedSearchSubject, evidence).hypothesis,
            physical_evaluation=physical_evaluation,
            quality_flags=(
                extraction.quality_flags
                if not backfill_supported
                and _same_physical_peak(
                    extraction.physical_evaluation, physical_evaluation
                )
                else ()
            ),
            targeted_product_projection=projection,
        )
        return rendered
    return replace(
        extraction,
        peak_result=replace(
            extraction.peak_result,
            selected_candidate=None,
            candidates=(),
            candidate_scores=(),
            reason=no_write_reason,
        ),
        selected_hypothesis=None,
        selection_decision=None,
        targeted_product_projection=projection,
    )


def _same_physical_peak(
    captured: PhysicalPeakEvaluation | None, reported: PhysicalPeakEvaluation | None
) -> bool:
    """Question/provenance may change without replacing the selected segment."""
    return (
        captured is not None
        and reported is not None
        and captured.answer.selected_segment is not None
        and captured.prepared == reported.prepared
        and captured.answer.selected_segment == reported.answer.selected_segment
    )


def _projection_with_reported_peak_quality(
    projection: TargetedProductProjection,
    *,
    captured: PhysicalPeakEvaluation | None,
    reported: PhysicalPeakEvaluation | None,
    backfill_supported: bool,
) -> TargetedProductProjection:
    """Keep sample reasons, replacing only the reported peak's quality facts."""
    review_reasons = projection.review_reasons
    conflict_reasons = projection.conflict_reasons
    if backfill_supported or not _same_physical_peak(captured, reported):
        peak_local_review = {"trace_morphology_review"}
        peak_local_conflicts = {
            "trace_morphology_conflict",
            "hard_local_quality_conflict",
            "hard_quality_flag_conflict",
        }
        review_reasons = tuple(r for r in review_reasons if r not in peak_local_review)
        conflict_reasons = tuple(
            r for r in conflict_reasons if r not in peak_local_conflicts
        )
    return replace(
        projection, review_reasons=review_reasons, conflict_reasons=conflict_reasons
    )


def _finalized_product_projection(
    projection: TargetedProductProjection | None,
    *,
    target: Target,
    evidence: object,
    value: float | None,
    no_write_reason: str,
    backfill_supported: bool = False,
    paired_istd_identity: bool = False,
    captured_physical_evaluation: PhysicalPeakEvaluation | None = None,
) -> TargetedProductProjection | None:
    if projection is None:
        return None
    if no_write_reason:
        return replace(
            projection,
            product_state="not_counted",
            counted_detection=False,
            review_state="review_required",
            projection_reason=no_write_reason,
        )
    evidence = cast(ObservedSearchSubject, evidence)
    physical_evaluation = evidence.hypothesis.peak.physical_evaluation
    projection = _projection_with_reported_peak_quality(
        projection,
        captured=captured_physical_evaluation,
        reported=physical_evaluation,
        backfill_supported=backfill_supported,
    )
    return build_targeted_product_projection(
        TargetedProductContext(
            role="ISTD" if target.is_istd else "Analyte",
            expected_present=target.is_istd,
            target_label=target.label,
            istd_pair=target.istd_pair,
            neutral_loss_required=target.neutral_loss_da is not None,
        ),
        rt=evidence.hypothesis.peak.rt_apex_min,
        area=value,
        cid_nl_supported=(evidence.hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL),
        backfill_supported=backfill_supported,
        paired_istd_identity=paired_istd_identity,
        support_reasons=projection.support_reasons,
        review_reasons=projection.review_reasons,
        conflict_reasons=projection.conflict_reasons,
        not_counted_reasons=projection.not_counted_reasons,
        exclusion_reasons=projection.exclusion_reasons,
    )


def _prepare_targeted_production_workflow(
    request: TargetedApplicationRequest,
    acquisition_evidence: RunOutput,
    *,
    expected_inputs: TargetedProductionInputSnapshot | None = None,
) -> _PreparedTargetedProductionWorkflow:
    """Bind acquisition rows to exact RAW, request, sample, and Target owners."""

    current_inputs = (
        snapshot_targeted_production_inputs(request)
        if expected_inputs is None
        else _validate_targeted_production_input_snapshot(request, expected_inputs)
    )
    raw_paths = list(current_inputs.resolved_inputs.raw_paths)
    if len(raw_paths) != len(acquisition_evidence.file_results):
        raise RuntimeError("Targeted acquisition returned partial sample coverage")
    results_by_raw_path = _bind_results_to_raw_paths(
        raw_paths,
        acquisition_evidence.file_results,
    )
    resolved_inputs = current_inputs.resolved_inputs
    metadata_by_stem = {row.raw_stem: row for row in resolved_inputs.sample_metadata}
    samples = tuple(
        replace(
            metadata_by_stem[raw_path.stem],
            sample_name=file_result.sample_name,
            raw_stem=raw_path.stem,
            injection_order=resolved_inputs.injections.required_order(raw_path.stem),
        )
        for raw_path, file_result in results_by_raw_path
    )
    targets = current_inputs.targets
    profile = targeted_signal_processing_profile(request.config)
    physical_evaluations = {}
    for sample, (_, file_result) in zip(
        samples,
        results_by_raw_path,
        strict=True,
    ):
        for target in targets:
            result = file_result.results.get(target.label)
            if result is not None and result.physical_evaluation is not None:
                candidate_id = targeted_capture_candidate_id(target, sample)
                physical_evaluations[candidate_id] = result.physical_evaluation
                for index, evaluation in enumerate(
                    result.candidate_physical_evaluations
                ):
                    physical_evaluations[f"{candidate_id}:universe:{index}"] = (
                        evaluation
                    )
    if any(
        evaluation.prepared.profile != profile
        for evaluation in physical_evaluations.values()
    ):
        raise RuntimeError("Targeted acquisition crossed its configured signal profile")
    return _PreparedTargetedProductionWorkflow(
        acquisition_sources=current_inputs.acquisition_sources,
        rt_frame=SampleRtFrame.from_samples(
            samples, injections=resolved_inputs.injections
        ),
        samples=samples,
        targets=targets,
        results_by_sample=tuple(
            (sample, file_result)
            for sample, (_, file_result) in zip(
                samples,
                results_by_raw_path,
                strict=True,
            )
        ),
        raw_stems=frozenset(path.stem for path in raw_paths),
        acquisition_evidence=acquisition_evidence,
    )


def snapshot_targeted_production_inputs(
    request: TargetedApplicationRequest,
) -> TargetedProductionInputSnapshot:
    """Snapshot local RAW revisions and scientific inputs for a production phase."""

    resolved_inputs = resolve_targeted_inputs(
        request.config,
        injection_order=request.injection_order,
    )
    raw_paths = resolved_inputs.raw_paths
    if not raw_paths:
        raise ConfigError("No RAW files were found for Targeted extraction.")
    raw_file_states = _raw_file_states(raw_paths)
    acquisition_sources = build_targeted_acquisition_sources(
        raw_paths=raw_paths,
        injections=resolved_inputs.injections,
        should_cancel=request.should_stop,
    )
    if _raw_file_states(raw_paths) != raw_file_states:
        raise RuntimeError("Targeted production inputs changed during preparation")
    return TargetedProductionInputSnapshot(
        config=deepcopy(request.config),
        raw_file_states=raw_file_states,
        resolved_inputs=resolved_inputs,
        targets=request.targets,
        acquisition_sources=acquisition_sources,
    )


def _validate_targeted_production_input_snapshot(
    request: TargetedApplicationRequest,
    expected: TargetedProductionInputSnapshot,
) -> TargetedProductionInputSnapshot:
    resolved_inputs = resolve_targeted_inputs(
        request.config,
        injection_order=request.injection_order,
    )
    if (
        request.config != expected.config
        or resolved_inputs != expected.resolved_inputs
        or _raw_file_states(resolved_inputs.raw_paths) != expected.raw_file_states
        or request.targets != expected.targets
    ):
        raise RuntimeError("Targeted production inputs changed during acquisition")
    return expected


def _raw_file_states(
    raw_paths: tuple[Path, ...],
) -> tuple[tuple[int, int, int, int, int], ...]:
    return tuple(
        (
            state.st_dev,
            state.st_ino,
            state.st_size,
            state.st_mtime_ns,
            state.st_ctime_ns,
        )
        for state in (path.stat() for path in raw_paths)
    )


def _bind_results_to_raw_paths(
    raw_paths: list[Path],
    file_results: list[FileResult],
) -> tuple[tuple[Path, FileResult], ...]:
    results_by_sample: dict[str, FileResult] = {}
    for file_result in file_results:
        if file_result.error:
            raise RuntimeError(
                "Targeted RAW acquisition failed for "
                f"{file_result.sample_name}: {file_result.error}"
            )
        if file_result.sample_name in results_by_sample:
            raise RuntimeError(
                "Targeted acquisition returned duplicate sample identities"
            )
        results_by_sample[file_result.sample_name] = file_result
    raw_stems = {path.stem for path in raw_paths}
    result_names = set(results_by_sample)
    if result_names != raw_stems:
        raise RuntimeError(
            "Targeted acquisition sample identities do not match resolved RAW inputs"
        )
    return tuple((path, results_by_sample[path.stem]) for path in raw_paths)


def _execute_target(
    context: _PreparedTargetedProductionWorkflow,
    request: TargetedApplicationRequest,
    target: Target,
) -> FinalizedTargetProductionResult:
    population = build_targeted_capture_population(
        target=target,
        acquisition_sources=context.acquisition_sources,
        samples=context.samples,
        results_by_sample=context.results_by_sample,
        config=request.config,
    )
    assessments = assess_targeted_cid_nl_samples(population)
    population = population.bind_assessments(assessments)
    alignment_config = AlignmentConfig()
    backfill = resolve_targeted_istd_backfill(
        population,
        assessments,
        config=request.config,
        alignment_config=alignment_config,
        rt_frame=context.rt_frame,
    )
    population = population.with_recovered_peaks(backfill.recovery_observations)
    assessment_tuple = tuple(
        sorted(
            assessments,
            key=lambda item: item[0].sample_name,
        )
    )
    return _complete_retained_target(
        TargetWorkflowRequest(scope=population.scope),
        TargetWorkflowAdapter(
            terminal_subjects=population.terminal_subjects,
            rt_frame=context.rt_frame,
            sample_assessments=assessment_tuple,
            alignment_config=alignment_config,
            targeted_backfill_decisions=backfill.decisions,
        ),
    )


def _complete_retained_target(
    request: TargetWorkflowRequest,
    adapter: TargetWorkflowAdapter,
) -> FinalizedTargetProductionResult:
    """Complete retained acquisition directly into its scientific result."""
    try:
        result = complete_analysis(request, adapter)
    except WorkflowStageFailure as failure:
        raise RuntimeError(
            "Targeted workflow failed before publication: "
            f"stage={failure.stage}; reason={failure.error}"
        ) from failure.error
    return cast(FinalizedTargetProductionResult, result)


__all__ = ["execute_targeted_production_workflow"]
