"""Targeted inputs to conservative ISTD-anchored Backfill.

Backfill applies to a Target that is an internal standard or is paired with
one. Its rule belongs to ``alignment.backfill.istd_anchored``; this module reads
the Targeted population for what that rule consumes: the direct CID-NL peers and
the paired internal standard's observation in each sample.
"""

from __future__ import annotations

from xic_extractor.alignment.backfill.istd_anchored import (
    IstdAnchoredBackfill,
    SampleAssessments,
    resolve_istd_anchored_backfill,
)
from xic_extractor.alignment.cell_observation.observations import CidNlCellGroupSupport
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.result.scientific_alignment import ObservedSearchSubject
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.extraction.target_extraction import credible_istd_anchor_rt
from xic_extractor.extraction.targeted_capture_population import (
    TargetedCapturePopulation,
    bound_acquisition_trace,
    mint_candidate_peak_observation,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)


def resolve_targeted_istd_backfill(
    population: TargetedCapturePopulation,
    assessments: SampleAssessments,
    *,
    config: ExtractionConfig,
    alignment_config: AlignmentConfig,
    rt_frame: RtFrame,
) -> IstdAnchoredBackfill:
    """Backfill HOLD samples of an ISTD-related Target from their direct peers."""

    target = population.target
    if not (target.is_istd or target.istd_pair) or not assessments:
        return IstdAnchoredBackfill.none()
    assessment_by_sample = {
        sample.sample_name: assessment for sample, assessment in assessments
    }
    sample_by_id = {sample.sample_name: sample for sample in population.samples}
    direct_supports = tuple(
        CidNlCellGroupSupport(
            sample=sample_by_id[item.sample_key],
            assessment=assessment_by_sample[item.sample_key],
            hypothesis=item.hypothesis,
        )
        for item in population.terminal_subjects
        if isinstance(item, ObservedSearchSubject)
        and item.hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL
    )
    return resolve_istd_anchored_backfill(
        sample_population=population.samples,
        target=population.target,
        direct_supports=direct_supports,
        sample_assessments=assessments,
        paired_istd_observations=lambda: _paired_istd_observations(population, config),
        rt_half_window_min=alignment_config.preferred_rt_sec / 60.0,
        alignment_config=alignment_config,
        rt_frame=rt_frame,
    )


def _paired_istd_observations(
    population: TargetedCapturePopulation,
    config: ExtractionConfig,
) -> tuple[PeakObservation, ...]:
    observations = []
    for entry in population.entries:
        pair = entry.file_result.results.get(population.target.istd_pair)
        if pair is None or credible_istd_anchor_rt(pair) is None:
            continue
        evaluation = pair.physical_evaluation
        if evaluation is None or evaluation.retained_prepared_signal is None:
            continue
        observations.append(
            mint_candidate_peak_observation(
                entry.sample,
                bound_acquisition_trace(
                    population.acquisition_sources[entry.sample.raw_stem],
                    entry.sample,
                    pair,
                ),
                evaluation,
                config=config,
                acquired_trace_provenance=pair.acquisition_observation_provenance,
            )
        )
    return tuple(observations)


__all__ = ["resolve_targeted_istd_backfill"]
