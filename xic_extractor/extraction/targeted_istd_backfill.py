"""Targeted inputs to ISTD-anchored conservative Backfill.

Backfill applies to a Target that is an internal standard or is paired with
one. Its rule belongs to ``alignment.istd_anchored_backfill``; this module reads
the Targeted population for what that rule consumes: the direct CID-NL peers and
the paired internal standard's observation in each sample.
"""

from __future__ import annotations

from xic_extractor.alignment.cell_observations import CidNlCellGroupSupport
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.istd_anchored_backfill import (
    IstdAnchoredBackfill,
    SampleAssessments,
    resolve_istd_anchored_backfill,
)
from xic_extractor.configuration.models import ExtractionConfig
from xic_extractor.extraction.target_extraction import credible_istd_anchor_rt
from xic_extractor.extraction.targeted_capture_population import (
    TargetedCapturePopulation,
    bound_acquisition_trace,
    mint_candidate_peak_observation,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
)


def resolve_targeted_istd_backfill(
    population: TargetedCapturePopulation,
    assessments: SampleAssessments,
    *,
    config: ExtractionConfig,
    alignment_config: AlignmentConfig,
) -> IstdAnchoredBackfill:
    """Backfill HOLD samples of an ISTD-related Target from their direct peers."""

    target = population.target.definition.target
    if not (target.is_istd or target.istd_pair) or not assessments:
        return IstdAnchoredBackfill.none()
    assessment_by_sample = {
        sample.identity.sample_identity_id: assessment
        for sample, assessment in assessments
    }
    sample_by_id = {
        sample.identity.sample_identity_id: sample for sample in population.samples
    }
    direct_supports = tuple(
        CidNlCellGroupSupport(
            sample=sample_by_id[
                item.owner_observation.content.canonical_sample_identity_id
            ],
            assessment=assessment_by_sample[
                item.owner_observation.content.canonical_sample_identity_id
            ],
            hypothesis=item.owner_hypothesis,
        )
        for item in population.capture_inputs
        if item.owner_hypothesis is not None
        and item.owner_observation is not None
        and item.owner_hypothesis.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL
    )
    return resolve_istd_anchored_backfill(
        processing_owners=population.processing_owners,
        sample_population=population.samples,
        target=population.target,
        direct_supports=direct_supports,
        sample_assessments=assessments,
        paired_istd_observations=lambda: _paired_istd_observations(population, config),
        rt_half_window_min=config.nl_rt_anchor_half_window_min,
        alignment_config=alignment_config,
    )


def _paired_istd_observations(
    population: TargetedCapturePopulation,
    config: ExtractionConfig,
) -> tuple[PeakObservationRef, ...]:
    observations = []
    for entry in population.entries:
        pair = entry.file_result.results.get(
            population.target.definition.target.istd_pair
        )
        if pair is None or credible_istd_anchor_rt(pair) is None:
            continue
        evaluation = pair.physical_evaluation
        if evaluation is None or evaluation.retained_prepared_signal is None:
            continue
        observations.append(
            mint_candidate_peak_observation(
                population.processing_owners,
                entry.sample,
                bound_acquisition_trace(
                    population.processing_owners, entry.sample, pair
                ),
                evaluation,
                config=config,
                scope=population.scope,
            )
        )
    return tuple(observations)


__all__ = ["resolve_targeted_istd_backfill"]
