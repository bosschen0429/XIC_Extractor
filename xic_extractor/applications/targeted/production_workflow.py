"""Complete Targeted acquisition into bound scientific and public result models."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from xic_extractor.alignment.completion import (
    TargetWorkflowAdapter,
    TargetWorkflowRequest,
    WorkflowStageFailure,
    complete_analysis,
    resolve_workflow_processing_owners,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.result.application_result import (
    FinalizedTargetApplicationResult,
)
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.applications.targeted.application import (
    FinalizedTargetProductionResult,
    TargetedApplicationError,
    TargetedApplicationRequest,
    TargetedOutputBoundaryError,
)
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.configuration.target_identity import (
    ResolvedTargetDefinition,
    TargetListSnapshot,
    mint_target_list_snapshot,
    resolve_target_definition,
)
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
    bind_targeted_physical_capture_request,
    build_targeted_processing_request_identity,
    resolve_targeted_inputs,
    resolve_targeted_raw_paths,
)
from xic_extractor.extraction.targeted_istd_backfill import (
    resolve_targeted_istd_backfill,
)
from xic_extractor.extraction.targeted_peak_evidence import (
    reselect_targeted_peak_evidence,
    targeted_fragment_identity_rejection,
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
from xic_extractor.peak_detection.models import PeakResult
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)
from xic_extractor.peak_detection.physical_peak_models import PhysicalPeakEvaluation
from xic_extractor.processing_identity import (
    AcquisitionSourceExpectation,
    ProcessingOwnerReferences,
    ProcessingRequestIdentity,
    compose_processing_owner_references,
    issue_processing_attempt,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
)

_IDENTITY_NAMESPACE = "targeted-production"


@dataclass(frozen=True, slots=True)
class TargetedProductionInputSnapshot:
    """Admitted Targeted inputs and their exact acquisition identity bindings."""

    config: ExtractionConfig
    raw_paths: tuple[Path, ...]
    raw_file_states: tuple[tuple[int, int, int, int, int], ...]
    resolved_inputs: ResolvedTargetedInputs
    target_snapshot: TargetListSnapshot
    processing_request: ProcessingRequestIdentity

    def acquisition_sources_by_sample(self) -> dict[str, AcquisitionSourceExpectation]:
        """Project the admitted RAW identities for the acquisition runner."""

        request = self.processing_request
        sources_by_id = {
            source.acquisition_source_id: source
            for source in request.acquisition_sources
        }
        states_by_stem = {
            path.stem: state
            for path, state in zip(
                self.raw_paths,
                self.raw_file_states,
                strict=True,
            )
        }
        return {
            binding.adapter_input_key: AcquisitionSourceExpectation(
                source=sources_by_id[binding.acquisition_source_id],
                path_identity=states_by_stem[binding.adapter_input_key],
            )
            for binding in request.acquisition_bindings
        }


@dataclass(frozen=True, slots=True)
class _PreparedTargetedProductionWorkflow:
    """Owner-local acquisition population for per-Target scientific completion."""

    owners: ProcessingOwnerReferences
    rt_frame: SampleRtFrame
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    targets: tuple[ResolvedTargetDefinition, ...]
    results_by_sample: tuple[tuple[ResolvedCanonicalSampleIdentity, FileResult], ...]
    raw_stems: frozenset[str]
    acquisition_file_ids: tuple[int, ...]
    acquisition_evidence: RunOutput


@dataclass(frozen=True, slots=True)
class CompletedTargetedProductionWorkflow:
    """Owner-completed science and a publication model consumed without mutation."""

    finalized_results: tuple[FinalizedTargetProductionResult, ...]
    _acquisition_file_ids: tuple[int, ...]
    _publication_output: RunOutput

    def publication_output(
        self,
        request: TargetedApplicationRequest,
        acquisition_evidence: RunOutput,
    ) -> RunOutput:
        """Bind a publication request and return its already-completed result view."""

        acquisition_file_ids = tuple(
            id(file_result) for file_result in acquisition_evidence.file_results
        )
        if acquisition_file_ids != self._acquisition_file_ids:
            raise TargetedOutputBoundaryError(
                "prepared Targeted workflow crossed the acquisition evidence"
            )
        _validate_target_population(request, self.finalized_results)
        return self._publication_output

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
    target_contexts = tuple(
        _context_for_target(prepared, index)
        for index, _target in enumerate(prepared.targets)
    )
    finalized_results = tuple(
        _execute_target(context, request, target)
        for context, target in zip(
            target_contexts,
            prepared.targets,
            strict=True,
        )
    )
    _validate_finalized_result_population(
        request,
        finalized_results,
        expected_processing_request=prepared.owners.request,
        expected_samples=prepared.samples,
    )
    publication_output = _build_publication_output(
        finalized_results, prepared.acquisition_evidence, config=request.config
    )
    return CompletedTargetedProductionWorkflow(
        finalized_results=finalized_results,
        _acquisition_file_ids=prepared.acquisition_file_ids,
        _publication_output=publication_output,
    )


def _validate_finalized_result_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
    *,
    expected_processing_request: ProcessingRequestIdentity,
    expected_samples: tuple[ResolvedCanonicalSampleIdentity, ...],
) -> None:
    if type(results) is not tuple or any(
        type(result) is not FinalizedTargetApplicationResult for result in results
    ):
        raise TargetedApplicationError(
            "finalized workflow must return an exact immutable Target result tuple"
        )
    _validate_target_population(request, results)
    request_ids = {result.processing_request for result in results}
    sample_populations = {
        tuple(sample.identity.sample_key for sample in result.canonical_samples)
        for result in results
    }
    if len(request_ids) != 1 or len(sample_populations) != 1:
        raise TargetedApplicationError(
            "finalized Target results crossed request or sample populations"
        )
    expected_sample_population = tuple(
        sorted(sample.identity.sample_key for sample in expected_samples)
    )
    if any(
        result.processing_request != expected_processing_request
        or tuple(
            sorted(sample.identity.sample_key for sample in result.canonical_samples)
        )
        != expected_sample_population
        for result in results
    ):
        raise TargetedApplicationError(
            "finalized Target results do not belong to the current acquisition"
        )


def _validate_target_population(
    request: TargetedApplicationRequest,
    results: tuple[FinalizedTargetProductionResult, ...],
) -> None:
    if len(results) != len(request.targets):
        raise TargetedApplicationError(
            "finalized workflow did not cover every declared Target"
        )
    for target, result in zip(request.targets, results, strict=True):
        finalized_target = (
            result.completed_projection.row_binding.target.definition.target
        )
        if finalized_target != target:
            raise TargetedApplicationError(
                "finalized Target result order differs from the declared Target list"
            )


def _build_publication_output(
    finalized_results: tuple[FinalizedTargetProductionResult, ...],
    acquisition_evidence: RunOutput,
    *,
    config: ExtractionConfig,
) -> RunOutput:
    expected_samples = finalized_results[0].canonical_samples
    expected_sample_ids = tuple(
        sample.identity.sample_key for sample in expected_samples
    )
    samples_by_raw_stem = {
        sample.metadata.raw_stem: sample for sample in expected_samples
    }
    if len(samples_by_raw_stem) != len(expected_samples):
        raise TargetedOutputBoundaryError(
            "finalized Targeted sample population contains duplicate RAW identities"
        )

    decisions: dict[tuple[str, str], Any] = {}
    target_ids_by_label: dict[str, str] = {}
    targets_by_label: dict[str, Target] = {}
    backfills = {}
    backfill_diagnostics = []
    for result in finalized_results:
        target = result.completed_projection.row_binding.target.definition
        target_label = target.target.label
        target_id = target_label
        if target_label in target_ids_by_label:
            raise TargetedOutputBoundaryError(
                "finalized Targeted result population contains duplicate Targets"
            )
        target_ids_by_label[target_label] = target_id
        targets_by_label[target_label] = target.target
        for backfill in result.scientific_core.targeted_backfill_decisions:
            sample_id = backfill.target_cell.content.sample_key
            backfills[(sample_id, target_id)] = backfill
        for projection_decision in result.completed_projection.decisions:
            subject = cast(Any, projection_decision.subject)
            sample_id = subject.sample.identity.sample_key
            if sample_id not in expected_sample_ids:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision crossed the canonical "
                    "sample population"
                )
            key = (sample_id, target_id)
            if key in decisions:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decision population contains duplicates"
                )
            decisions[key] = projection_decision

    files: list[tuple[int | None, FileResult, dict[str, ExtractionResult]]] = []
    cells: dict[tuple[str, str], _RenderCell] = {}
    consumed: set[tuple[str, str]] = set()
    consumed_sample_ids: set[str] = set()
    for file_result in acquisition_evidence.file_results:
        sample = samples_by_raw_stem.get(file_result.sample_name)
        if sample is None:
            raise TargetedOutputBoundaryError(
                "acquisition evidence does not resolve to a canonical sample identity"
            )
        sample_id = sample.identity.sample_key
        if sample_id in consumed_sample_ids:
            raise TargetedOutputBoundaryError(
                "acquisition evidence contains duplicate canonical samples"
            )
        consumed_sample_ids.add(sample_id)
        rendered: dict[str, ExtractionResult] = {}
        for target_label, extraction in file_result.results.items():
            if extraction.target_label != target_label:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence crossed its declared Target identity"
                )
            resolved_target_id = target_ids_by_label.get(target_label)
            if resolved_target_id is None:
                raise TargetedOutputBoundaryError(
                    "acquisition evidence does not resolve to a finalized "
                    "Target identity"
                )
            key = (sample_id, resolved_target_id)
            render_decision = decisions.get(key)
            if render_decision is None:
                raise TargetedOutputBoundaryError(
                    "finalized Targeted decisions do not cover acquisition evidence"
                )
            consumed.add(key)
            backfill_decision = backfills.get(key)
            if backfill_decision is not None:
                backfill_diagnostics.append(
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
                            if targets_by_label[target_label].is_istd
                            and backfill_decision.matched_candidate is not None
                            else "confirmed=False."
                        ),
                    )
                )
            no_write_reason = (
                ""
                if render_decision.variant == "numeric"
                else "not_quantifiable"
                if render_decision.variant == "not_quantifiable"
                else getattr(
                    render_decision,
                    "no_write_reason",
                    f"finalized_{render_decision.variant}",
                )
            )
            cell = _RenderCell(
                extraction=extraction,
                target=targets_by_label[target_label],
                value=render_decision.value,
                no_write_reason=no_write_reason,
                evidence=getattr(render_decision, "evidence", None),
                backfill_supported=(
                    backfill_decision is not None
                    and backfill_decision.matched_candidate is not None
                ),
            )
            cells[(file_result.sample_name, target_label)] = cell
            rendered[target_label] = cell.render()
        files.append(
            (
                sample.metadata.injection_order,
                replace(file_result, sample_metadata=sample.metadata),
                rendered,
            )
        )
    if consumed != set(decisions):
        raise TargetedOutputBoundaryError(
            "finalized Targeted decisions crossed the acquisition sample population"
        )
    position_diagnostics = _apply_paired_istd_positions(
        {file_result.sample_name: rendered for _, file_result, rendered in files},
        cells,
        targets_by_label,
    )
    # Every published reader follows the run as acquired, not RAW file names.
    files.sort(key=lambda item: (item[0] is None, item[0] or 0, item[1].sample_name))
    return RunOutput(
        file_results=[
            replace(file_result, results=rendered) for _, file_result, rendered in files
        ],
        diagnostics=[
            *(
                record
                for record in acquisition_evidence.diagnostics
                if record.issue not in {"NL_FAIL", "NO_MS2", "ISTD_CONFIDENCE"}
            ),
            *(
                record
                for _, file_result, rendered in files
                for label, result in rendered.items()
                for record in build_ms2_diagnostic_records(
                    file_result.sample_name, targets_by_label[label], result, config
                )
            ),
            *backfill_diagnostics,
            *position_diagnostics,
        ],
    )


# A peak away from the paired-ISTD position is another signal; it is not
# reported as this analyte's quantity.
OFF_PAIRED_ISTD_POSITION = "analyte_peak_off_paired_istd_position"


@dataclass(frozen=True)
class _RenderCell:
    """One finalized sample/target decision and the evidence that renders it."""

    extraction: ExtractionResult
    target: Target
    value: float | None
    no_write_reason: str
    evidence: object
    backfill_supported: bool

    def render(
        self,
        *,
        paired_istd_identity: bool = False,
        no_write_reason: str = "",
    ) -> ExtractionResult:
        return _finalized_extraction_result(
            self.extraction,
            target=self.target,
            value=self.value,
            no_write_reason=no_write_reason or self.no_write_reason,
            evidence=self.evidence,
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
        evidence.hypothesis.peak_observation_refs[0].physical_evaluation
        if no_write_reason in {"", "not_quantifiable"}
        and type(evidence) is ObservedSearchSubject
        else None
    )
    extraction = reselect_targeted_peak_evidence(extraction, reported_evaluation)
    projection = extraction.targeted_product_projection
    if projection is not None and reported_evaluation is not None:
        projection = replace(
            projection,
            conflict_reasons=tuple(
                reason
                for reason in projection.conflict_reasons
                if reason != "hmdc_competing_rna_fragment"
            ),
        )
    # Finalization can replace the capture with a retained Backfill candidate.
    # Consume that candidate's chemistry, never the previous selection's summary.
    fragment_rejection = targeted_fragment_identity_rejection(
        target, extraction.candidate_ms2_evidence
    )
    if fragment_rejection:
        no_write_reason = fragment_rejection
        if (
            projection is not None
            and fragment_rejection == "hmdc_competing_rna_fragment"
        ):
            projection = replace(
                projection,
                conflict_reasons=(
                    fragment_rejection,
                    *projection.conflict_reasons,
                ),
            )
    projection = _finalized_product_projection(
        projection,
        target=target,
        evidence=evidence,
        value=value,
        no_write_reason=no_write_reason,
        backfill_supported=backfill_supported,
        paired_istd_identity=paired_istd_identity,
        captured_physical_evaluation=captured_physical_evaluation,
        nl_status=extraction.nl_token or "",
    )
    if not no_write_reason:
        if (
            value is None
            or type(evidence) is not ObservedSearchSubject
            or _hypothesis_area(evidence.hypothesis) != value
        ):
            raise TargetedOutputBoundaryError(
                "finalized Targeted value does not match its public result model"
            )
        integration = evidence.hypothesis.integration
        physical_evaluation = evidence.hypothesis.peak_observation_refs[
            0
        ].physical_evaluation
        rendered = replace(
            extraction,
            peak_result=replace(
                extraction.peak_result,
                peak=PeakResult(
                    rt=integration.rt_apex_min,
                    intensity=integration.height_raw,
                    intensity_smoothed=integration.height_smoothed,
                    area=value,
                    peak_start=integration.rt_left_min,
                    peak_end=integration.rt_right_min,
                ),
                candidates=(),
                candidate_scores=(),
            ),
            selected_hypothesis=None,
            selection_decision=None,
            physical_evaluation=physical_evaluation,
            quality_flags=(
                extraction.quality_flags
                if not backfill_supported
                and _same_physical_peak(
                    extraction.physical_evaluation, physical_evaluation
                )
                else evidence.hypothesis.evidence.quality_flags
            ),
            targeted_product_projection=projection,
        )
        if rendered.reported_peak_area != value:
            raise TargetedOutputBoundaryError(
                "finalized Targeted hypothesis did not produce its numeric value"
            )
        return rendered
    return replace(
        extraction,
        peak_result=replace(
            extraction.peak_result,
            peak=None,
            candidates=(),
            candidate_scores=(),
            confidence=None,
            reason=no_write_reason,
        ),
        selected_hypothesis=None,
        selection_decision=None,
        targeted_product_projection=projection,
    )


def _hypothesis_area(hypothesis: Any) -> float:
    integration = hypothesis.integration
    if integration.area_ms1_morphology is not None:
        return integration.area_ms1_morphology
    return integration.area_raw_counts_seconds


def _same_physical_peak(
    captured: PhysicalPeakEvaluation | None, reported: PhysicalPeakEvaluation | None
) -> bool:
    """Question/provenance may change without replacing the selected segment."""
    return (
        captured is not None
        and reported is not None
        and captured.facts is not None
        and reported.facts is not None
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
        # These reasons are issued by selected-candidate morphology/quality
        # facts (or its selected shoulder), not by the sample. A new physical
        # answer cannot inherit them from the replaced capture. Unknown and
        # sample-level reasons remain intact; Backfill acceptance is not clean.
        peak_local_review = {"trace_morphology_review", "physical_shoulder_overlap"}
        peak_local_conflicts = {
            "trace_morphology_conflict",
            "hard_local_quality_conflict",
            "hard_quality_flag_conflict",
        }
        review_reasons = tuple(r for r in review_reasons if r not in peak_local_review)
        conflict_reasons = tuple(
            r for r in conflict_reasons if r not in peak_local_conflicts
        )
    if reported is not None and reported.requires_overlap_review:
        review_reasons = tuple(
            dict.fromkeys((*review_reasons, "physical_shoulder_overlap"))
        )
    if backfill_supported:
        # Accepted Backfill uniquely mapped a peak using confirmed peers and
        # the current pair; acquisition's unresolved alternatives are resolved.
        conflict_reasons = tuple(
            r for r in conflict_reasons if r != "paired_istd_multiple_candidates"
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
    nl_status: str,
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
    if type(evidence) is not ObservedSearchSubject:
        raise TargetedOutputBoundaryError(
            "numeric projection lost its finalized evidence"
        )
    physical_evaluation = evidence.hypothesis.peak_observation_refs[
        0
    ].physical_evaluation
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
        rt=evidence.hypothesis.integration.rt_apex_min,
        area=value,
        confidence=projection.legacy_evidence.get("confidence", ""),
        nl_status=nl_status,
        cid_nl_supported=(
            evidence.hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
        ),
        backfill_supported=backfill_supported,
        paired_istd_identity=paired_istd_identity,
        support_reasons=projection.support_reasons,
        review_reasons=projection.review_reasons,
        conflict_reasons=projection.conflict_reasons,
        not_counted_reasons=projection.not_counted_reasons,
        exclusion_reasons=projection.exclusion_reasons,
        legacy_evidence={**projection.legacy_evidence, "nl_status": nl_status},
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
    raw_paths = list(current_inputs.raw_paths)
    if len(raw_paths) != len(acquisition_evidence.file_results):
        raise RuntimeError("Targeted acquisition returned partial sample coverage")
    acquisition_file_ids = tuple(id(item) for item in acquisition_evidence.file_results)
    results_by_raw_path = _bind_results_to_raw_paths(
        raw_paths,
        acquisition_evidence.file_results,
    )
    resolved_inputs = current_inputs.resolved_inputs
    target_snapshot = current_inputs.target_snapshot
    metadata_by_stem = {row.raw_stem: row for row in resolved_inputs.sample_metadata}
    rows = tuple(
        replace(
            metadata_by_stem[raw_path.stem],
            sample_name=file_result.sample_name,
            raw_stem=raw_path.stem,
            injection_order=resolved_inputs.injection_order[raw_path.stem],
        )
        for raw_path, file_result in results_by_raw_path
    )
    sample_snapshot = mint_sample_metadata_snapshot(
        rows,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    samples = tuple(
        resolve_canonical_sample_identity(sample_snapshot, row.raw_stem) for row in rows
    )
    resolved_targets = tuple(
        resolve_target_definition(target_snapshot, target.label)
        for target in request.targets
    )
    profile = targeted_signal_processing_profile(request.config)
    physical_evaluations = {}
    for sample, (_, file_result) in zip(
        samples,
        results_by_raw_path,
        strict=True,
    ):
        for target in resolved_targets:
            result = file_result.results.get(target.definition.target.label)
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
    processing_request = bind_targeted_physical_capture_request(
        current_inputs.processing_request,
        profile=profile,
    )
    attempt = issue_processing_attempt()
    owners = compose_processing_owner_references(
        request=processing_request,
        attempt=attempt,
        sample_snapshot=sample_snapshot,
        sample_resolutions=samples,
        target_snapshot=target_snapshot,
    )
    return _PreparedTargetedProductionWorkflow(
        owners=owners,
        rt_frame=SampleRtFrame.from_samples(samples),
        samples=samples,
        targets=resolved_targets,
        results_by_sample=tuple(
            (sample, file_result)
            for sample, (_, file_result) in zip(
                samples,
                results_by_raw_path,
                strict=True,
            )
        ),
        raw_stems=frozenset(path.stem for path in raw_paths),
        acquisition_file_ids=acquisition_file_ids,
        acquisition_evidence=acquisition_evidence,
    )


def snapshot_targeted_production_inputs(
    request: TargetedApplicationRequest,
) -> TargetedProductionInputSnapshot:
    """Snapshot local RAW revisions and scientific inputs for a production phase."""

    raw_paths = resolve_targeted_raw_paths(request.config)
    if not raw_paths:
        raise ConfigError("No RAW files were found for Targeted extraction.")
    resolved_inputs = resolve_targeted_inputs(
        request.config,
        raw_paths=raw_paths,
        injection_order=request.injection_order,
    )
    target_snapshot = mint_target_list_snapshot(
        request.targets,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    raw_file_states = _raw_file_states(raw_paths)
    processing_request = build_targeted_processing_request_identity(
        request.config,
        target_snapshot=target_snapshot,
        raw_paths=raw_paths,
        resolved_inputs=resolved_inputs,
        should_cancel=request.should_stop,
    )
    if _raw_file_states(raw_paths) != raw_file_states:
        raise RuntimeError("Targeted production inputs changed during preparation")
    return TargetedProductionInputSnapshot(
        config=deepcopy(request.config),
        raw_paths=tuple(raw_paths),
        raw_file_states=raw_file_states,
        resolved_inputs=resolved_inputs,
        target_snapshot=target_snapshot,
        processing_request=processing_request,
    )


def _validate_targeted_production_input_snapshot(
    request: TargetedApplicationRequest,
    expected: TargetedProductionInputSnapshot,
) -> TargetedProductionInputSnapshot:
    raw_paths = resolve_targeted_raw_paths(request.config)
    resolved_inputs = resolve_targeted_inputs(
        request.config,
        raw_paths=raw_paths,
        injection_order=request.injection_order,
    )
    target_snapshot = mint_target_list_snapshot(
        request.targets,
        identity_namespace=_IDENTITY_NAMESPACE,
    )
    if (
        request.config != expected.config
        or tuple(raw_paths) != expected.raw_paths
        or _raw_file_states(raw_paths) != expected.raw_file_states
        or resolved_inputs != expected.resolved_inputs
        or target_snapshot != expected.target_snapshot
    ):
        raise RuntimeError("Targeted production inputs changed during acquisition")
    return expected


def _raw_file_states(
    raw_paths: list[Path],
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


def _context_for_target(
    context: _PreparedTargetedProductionWorkflow,
    target_index: int,
) -> _PreparedTargetedProductionWorkflow:
    """Reuse the initial attempt, then issue one attempt per later Target."""

    if target_index == 0:
        return context
    owners = context.owners
    attempt = issue_processing_attempt()
    return replace(
        context,
        owners=compose_processing_owner_references(
            request=owners.request,
            attempt=attempt,
            sample_snapshot=owners.sample_snapshot,
            sample_resolutions=owners.sample_resolutions,
            target_snapshot=owners.target_snapshot,
        ),
    )


def _execute_target(
    context: _PreparedTargetedProductionWorkflow,
    request: TargetedApplicationRequest,
    target: ResolvedTargetDefinition,
) -> FinalizedTargetProductionResult:
    population = build_targeted_capture_population(
        target=target,
        processing_owners=context.owners,
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
    population = population.rebind_to_recovery(backfill.recovery_observations)
    assessment_tuple = tuple(
        sorted(
            assessments,
            key=lambda item: item[0].identity.sample_key,
        )
    )
    capture_inputs = population.capture_inputs
    return _complete_retained_target(
        TargetWorkflowRequest(
            scope=population.scope,
            processing_request=context.owners.request,
            processing_attempt=context.owners.attempt,
        ),
        TargetWorkflowAdapter(
            capture_inputs=capture_inputs,
            rt_frame=context.rt_frame,
            sample_assessments=assessment_tuple,
            alignment_config=alignment_config,
            predecessor_cell_observations=(),
            completed_capture_handoff=population.captures,
            targeted_backfill_decisions=backfill.decisions,
        ),
    )


def _complete_retained_target(
    request: TargetWorkflowRequest,
    adapter: TargetWorkflowAdapter,
) -> FinalizedTargetProductionResult:
    """Complete retained acquisition directly into its scientific result."""
    owners = resolve_workflow_processing_owners(request, adapter)
    try:
        result = complete_analysis(request, adapter, processing_owners=owners)
    except WorkflowStageFailure as failure:
        raise RuntimeError(
            "Targeted workflow failed before publication: "
            f"stage={failure.stage}; reason={failure.error}"
        ) from failure.error
    if type(result) is not FinalizedTargetProductionResult:
        raise RuntimeError("Targeted completion returned a crossed result")
    return result


__all__ = ["execute_targeted_production_workflow"]
