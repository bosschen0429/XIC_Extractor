"""Complete Targeted choices after all acquisition workers have returned.

This adapter reuses retained signals and CID-NL opportunity/product facts. It
does not reopen RAW, refit baselines, or select values in the output writer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from urllib.parse import quote

from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.anchors import paired_anchor_mismatch_diagnostic
from xic_extractor.extraction.result_assembly import prepare_acquired_observation
from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
from xic_extractor.extraction.scoring_factory import build_scoring_context_factory
from xic_extractor.extraction.targeted_peak_selection import (
    TargetedCandidateAssessment,
    assess_targeted_candidate_population,
)
from xic_extractor.extraction.targeted_product_projection import (
    build_targeted_product_projection,
)
from xic_extractor.output.messages import build_diagnostic_records
from xic_extractor.output.targeted_product_model import TargetedProductContext
from xic_extractor.peak_detection.physical_peak_evaluation import (
    reference_region_envelopes,
)
from xic_extractor.peak_detection.targeted_context import (
    TargetedBatchContext,
    TargetedPopulationContext,
)


def _overlapping_measured_regions(
    left: ExtractionResult, right: ExtractionResult
) -> bool:
    left_facts = (
        left.physical_evaluation.facts if left.physical_evaluation is not None else None
    )
    right_facts = (
        right.physical_evaluation.facts
        if right.physical_evaluation is not None
        else None
    )
    return (
        left_facts is not None
        and right_facts is not None
        and (
            max(left_facts.left_rt_min, right_facts.left_rt_min)
            < min(left_facts.right_rt_min, right_facts.right_rt_min)
        )
    )


def _with_context_reasons(
    result: ExtractionResult,
    target: Target,
    review: Iterable[str],
    conflicts: tuple[str, ...] = (),
) -> ExtractionResult:
    projection = result.targeted_product_projection
    if projection is None:
        return result
    return replace(
        result,
        targeted_product_projection=build_targeted_product_projection(
            TargetedProductContext(
                role="ISTD" if target.is_istd else "Analyte",
                expected_present=target.is_istd,
                target_label=target.label,
                istd_pair=target.istd_pair,
                neutral_loss_required=target.neutral_loss_da is not None,
            ),
            rt=result.reported_rt,
            area=result.reported_peak_area,
            confidence=result.confidence,
            nl_status=result.nl_token or "",
            cid_nl_supported=result.selected_cid_nl_supported,
            support_reasons=projection.support_reasons,
            review_reasons=tuple(dict.fromkeys((*projection.review_reasons, *review))),
            conflict_reasons=tuple(
                dict.fromkeys((*projection.conflict_reasons, *conflicts))
            ),
            not_counted_reasons=projection.not_counted_reasons,
            exclusion_reasons=projection.exclusion_reasons,
            legacy_evidence=projection.legacy_evidence,
        ),
    )


def _reference_interpretation(
    assessment: TargetedCandidateAssessment,
    population: TargetedPopulationContext,
    reference_rts: tuple[float, ...],
) -> TargetedCandidateAssessment:
    prepared = assessment.prepared_result.prepared
    regional = assessment.regional_universe or assessment.universe
    if prepared is None or assessment.target.is_istd:
        return assessment
    target = assessment.target
    # Propose totals in the batch-supported reference frame. The original
    # regions remain alternatives; this proposal does not select an ion band.
    merge_eligible = frozenset(
        region.segment
        for region in population.regions
        if region.independent_body_supported
    )
    interpreted = (
        replace(
            regional,
            segments=reference_region_envelopes(
                prepared,
                regional.segments,
                reference_rts,
                merge_eligible_segments=merge_eligible,
            ),
        )
        if reference_rts
        else regional
    )
    if interpreted.segments == assessment.universe.segments:
        return assessment
    interpreted_assessment = assess_targeted_candidate_population(
        target=target,
        config=assessment.config,
        trace_group=assessment.trace_group,
        prepared_result=assessment.prepared_result,
        universe=interpreted,
        selection_window=assessment.selection_window,
        preferred_rt=assessment.preferred_rt,
        strict_preferred_rt=assessment.strict_preferred_rt,
        method_window=None,
        retained_method_products=assessment.method_products,
        retained_method_receipt=assessment.method_receipt,
        retained_trigger_events=assessment.method_trigger_events,
        regional_universe=regional,
    )
    return replace(
        interpreted_assessment,
        paired_istd_fwhm=assessment.paired_istd_fwhm,
        istd_confidence_note=assessment.istd_confidence_note,
        paired_istd_anchor_rt=assessment.paired_istd_anchor_rt,
    )


def complete_targeted_candidate_batch(
    output: RunOutput, config: ExtractionConfig
) -> RunOutput:
    assessments = {
        (file.sample_name, label): assessment
        for file in output.file_results
        for label, result in file.results.items()
        if (assessment := result.targeted_candidate_assessment) is not None
    }
    if not assessments:
        return output
    regional_batch = TargetedBatchContext(
        tuple(assessment.population_context() for assessment in assessments.values())
    )
    interpretations = {}
    comparison_populations = []
    for key, assessment in assessments.items():
        population = regional_batch.population_by_cell[key]
        interpreted = _reference_interpretation(
            assessment, population, regional_batch.reference_rts(population)
        )
        interpretations[key] = interpreted
        if interpreted is not assessment:
            # Each interpretation keeps its own method relation. Combining
            # their physical intervals into one CID-NL universe would invent
            # overlap and erase valid individual/total support.
            regions = {region.segment: region for region in population.regions}
            regions.update(
                (region.segment, region)
                for region in interpreted.population_context().regions
            )
            population = replace(population, regions=tuple(regions.values()))
        comparison_populations.append(population)
    batch = TargetedBatchContext(tuple(comparison_populations))
    selections = {}
    for file in output.file_results:
        for label, result in file.results.items():
            key = (file.sample_name, label)
            if (assessment := assessments.get(key)) is None:
                continue
            population = batch.population_by_cell[key]
            if assessment.target.is_istd:
                # Consume the established standard anchor, not a new batch
                # choice, when interpreting another assay of the same ion.
                evaluation = result.physical_evaluation
                selections[key] = next(
                    (
                        region
                        for region in population.regions
                        if evaluation is not None
                        and region.segment == evaluation.answer.selected_segment
                    ),
                    None,
                )
            else:
                selections[key] = batch.select(population)
    for key, choice in selections.items():
        if (
            choice is not None
            and choice.segment in interpretations[key].universe.segments
        ):
            assessments[key] = interpretations[key]
    factory = build_scoring_context_factory(config=config)
    files = []
    replaced_keys = {
        key for key, assessment in assessments.items() if not assessment.target.is_istd
    }
    # Peak-local diagnostics refer to the final batch choice, not capture's
    # temporary single-sample answer. Keep file-level and unrelated diagnostics.
    diagnostics = [
        d
        for d in output.diagnostics
        if (d.sample_name, d.target_label) not in replaced_keys
        or d.issue == "ISTD_ANCHOR_MISSING"
    ]
    for file in output.file_results:
        results = dict(file.results)
        for label, captured in file.results.items():
            assessment = assessments.get((file.sample_name, label))
            if assessment is None:
                continue
            if assessment.target.is_istd:
                # Standards already own the sample-local anchor used to
                # transfer analyte references. Do not silently replace that
                # anchor after its dependent priors have been established.
                continue
            trace = assessment.trace_group.primary_trace
            builder = factory(
                target=assessment.target,
                sample_name=file.sample_name,
                rt=trace.rt,
                intensity=trace.intensity,
                prepared_result=assessment.prepared_result,
                istd_rt_in_this_sample=captured.peak_result.paired_istd_anchor_rt,
                paired_istd_fwhm=assessment.paired_istd_fwhm,
                target_reference_rt=assessment.preferred_rt,
                candidate_ms2_evidence_builder=assessment.candidate_ms2_evidence.get,
            )
            population = assessment.population_context()
            selected_region = selections[(file.sample_name, label)]
            peak_result = assessment.score_selected_segment(
                selected_segment=selected_region.segment if selected_region else None,
                competing_region_count=len(
                    batch.competing_regions(
                        batch.population_by_cell[(file.sample_name, label)]
                    )
                ),
                scoring_context_builder=builder,
                istd_confidence_note=assessment.istd_confidence_note,
                paired_istd_anchor_rt=captured.peak_result.paired_istd_anchor_rt,
            )
            result = assessment.complete_result(
                peak_result, scoring_context_builder=builder
            )
            result = replace(
                result,
                context_admissible_candidate_indexes=tuple(
                    index
                    for index, region in enumerate(population.regions)
                    if region.independent_body_supported
                ),
            )
            projection = result.targeted_product_projection
            evaluation = result.physical_evaluation
            review = []
            if result.peak is not None:
                if len(batch.competing_regions(population)) > 1:
                    review.append("targeted_competing_regions")
                selected = (
                    evaluation.answer.selected_segment
                    if evaluation is not None
                    else None
                )
                if selected is not None:
                    if (
                        not selected.left_rt_min
                        <= population.reference_rt_min
                        <= selected.right_rt_min
                    ):
                        review.append("targeted_reference_displacement")
                        if (
                            not batch.retention_intervals(population)
                            and population.reference_istd_rt_min is None
                        ):
                            review.append("targeted_retention_context_unavailable")
                        supported = batch.retention_intervals(population)
                        offsets = (
                            selected.left_rt_min - population.reference_rt_min,
                            selected.right_rt_min - population.reference_rt_min,
                        )
                        if supported and not any(
                            max(offsets[0], left) <= min(offsets[1], right)
                            for left, right in supported
                        ):
                            review.append("targeted_retention_transfer_uncertain")
                    selected_context = next(
                        r for r in population.regions if r.segment == selected
                    )
                    if (
                        round(
                            selected_context.longest_acquired_fraction
                            * (selected.end_index - selected.start_index)
                        )
                        < selected_context.acquired_positive_count
                    ):
                        review.append("targeted_acquired_signal_gaps")
                    if selected_context.observed_component_count > 1:
                        review.append("targeted_unresolved_substructure")
            elif assessment.candidates.candidates:
                if any(
                    region.independent_body_supported
                    and batch.plausible(population, region)
                    and batch.cross_sample_region_conflict(population, region)
                    for region in population.regions
                ):
                    review.append("targeted_cross_sample_region_conflict")
                else:
                    review.append("targeted_no_plausible_measurable_region")
            if projection is not None and review:
                result = _with_context_reasons(
                    result,
                    assessment.target,
                    review,
                    conflicts=tuple(
                        reason
                        for reason in review
                        if reason == "targeted_cross_sample_region_conflict"
                    ),
                )
            source_trace = captured.acquisition_trace
            if source_trace is not None and source_trace.acquisition_source_id:
                result = prepare_acquired_observation(
                    result,
                    acquisition_source_id=source_trace.acquisition_source_id,
                    resolver_mode=config.resolver_mode,
                )
            results[label] = result
            diagnostics.extend(
                build_diagnostic_records(
                    file.sample_name, assessment.target, result, config
                )
            )
            paired_rejection = paired_anchor_mismatch_diagnostic(
                file.sample_name,
                assessment.target,
                result.peak_result,
                reference_rt=captured.peak_result.paired_istd_anchor_rt,
                anchor_rt=assessment.preferred_rt,
                strict_preferred_rt=assessment.strict_preferred_rt,
            )
            if paired_rejection is not None:
                diagnostics.append(paired_rejection)
        for label, result in tuple(results.items()):
            assessment = assessments.get((file.sample_name, label))
            if assessment is None or result.reported_peak_area is None:
                continue
            target = assessment.target
            shared_with = sorted(
                other_label
                for other_label, other in results.items()
                if other_label != label
                and other.reported_peak_area is not None
                and (
                    other_assessment := assessments.get((file.sample_name, other_label))
                )
                is not None
                and abs(target.mz - other_assessment.target.mz)
                <= min(
                    target.mz * target.ppm_tol,
                    other_assessment.target.mz * other_assessment.target.ppm_tol,
                )
                / 1e6
                and _overlapping_measured_regions(result, other)
            )
            if shared_with:
                reason = "targeted_shared_ion_region:" + ",".join(
                    quote(label, safe="") for label in shared_with
                )
                different_reference = any(
                    assessments[(file.sample_name, other)].preferred_rt
                    != assessment.preferred_rt
                    for other in shared_with
                )
                results[label] = _with_context_reasons(
                    result,
                    target,
                    (reason,),
                    ("targeted_shared_ion_region",) if different_reference else (),
                )
        files.append(replace(file, results=results))
    return RunOutput(file_results=files, diagnostics=diagnostics)
