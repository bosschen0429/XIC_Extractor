"""Targeted inputs to ISTD reference recovery and conservative Backfill.

Backfill applies to a Target that is an internal standard or is paired with
one. Its rule belongs to ``alignment.backfill.istd_anchored``; this module reads
the Targeted population for what that rule consumes: the direct CID-NL peers and
the paired internal standard's observation in each sample.
Reference recovery uses retained Targeted peaks and the shared trajectory
localizer; it does not promote an MS1-only observation to local CID-NL support.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median

from xic_extractor.alignment.backfill.istd_anchored import (
    IstdAnchoredBackfill,
    SampleAssessments,
    resolve_istd_anchored_backfill,
)
from xic_extractor.alignment.cell_observation.observations import CidNlCellGroupSupport
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.drift_evidence import IstdReferencePeakSelection
from xic_extractor.alignment.peak_trajectories import PeakTrajectory
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.extraction.result_models import FileResult
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


def resolve_istd_reference_peaks(
    targets: Sequence[Target],
    results_by_sample: Mapping[str, FileResult],
    *,
    alignment_config: AlignmentConfig,
    sample_cohorts: Mapping[str, str],
) -> tuple[dict[tuple[str, str], float], tuple[IstdReferencePeakSelection, ...]]:
    """Recover an observed ISTD peak without manufacturing local NL support.

    Direct Targeted selections freeze the peer evidence. A missing selection,
    or the unique selection contradicting the batch's ISTD order, may use the
    same empirical trajectory localization as Targeted Backfill. The sample
    being repaired never contributes to its own localization. Only one retained
    available physical peak may win; a forecast is never a measured coordinate.
    """
    direct = {}
    supported = {}
    for target in targets:
        for sample, file_result in results_by_sample.items():
            result = file_result.results.get(target.label)
            rt = credible_istd_anchor_rt(result)
            if rt is not None:
                direct[target.label, sample] = rt
                if result is not None and result.selected_cid_nl_supported:
                    supported[target.label, sample] = rt
    cohort_points: dict[tuple[str, str], list[float]] = {}
    for (label, sample), rt in supported.items():
        cohort_points.setdefault((sample_cohorts[sample], label), []).append(rt)
    centers = {key: median(values) for key, values in cohort_points.items()}
    contested = set()
    for sample in results_by_sample:
        cohort = sample_cohorts[sample]
        labels = sorted(
            (label for batch, label in centers if batch == cohort),
            key=lambda label: centers[cohort, label],
        )
        points = [
            (label, direct[label, sample])
            for label in labels
            if (label, sample) in direct
        ]

        def ordered(values):
            return all(a[1] < b[1] for a, b in zip(values, values[1:], strict=False))

        if len(points) >= 4 and not ordered(points):
            alternatives = [
                label
                for index, (label, _) in enumerate(points)
                if ordered(points[:index] + points[index + 1 :])
            ]
            if len(alternatives) == 1:
                contested.add((alternatives[0], sample))
    coordinates = dict(direct)
    selections = {}
    for (label, sample), rt in direct.items():
        selections[label, sample] = IstdReferencePeakSelection(
            label, sample, rt, rt, (label, sample) in supported, "direct_targeted"
        )
    frame = SampleRtFrame(
        sample_universe=frozenset(results_by_sample),
        sample_cohorts=sample_cohorts,
    )
    for target in targets:
        for sample, file_result in results_by_sample.items():
            key = target.label, sample
            if key in direct and key not in contested:
                continue
            peers = tuple(
                (other, rt)
                for (label, other), rt in supported.items()
                if label == target.label
                and other != sample
                and sample_cohorts[other] == sample_cohorts[sample]
                and (label, other) not in contested
            )
            result = file_result.results.get(target.label)
            if len(peers) < 3 or result is None:
                continue
            location = PeakTrajectory((), peers).backfill_locations(
                (sample,),
                reference_samples=frozenset(s for s, _ in peers),
                rt_frame=frame,
                backfill_half_window_min=alignment_config.preferred_rt_sec / 60,
            )[sample]
            if not location.reliable:
                continue
            assert location.apex_tolerance_sec is not None
            matches = [
                (index, evaluation)
                for index, evaluation in enumerate(
                    result.candidate_physical_evaluations
                )
                if evaluation.state == "available"
                and evaluation.integration is not None
                and not evaluation.requires_overlap_review
                and abs(evaluation.integration.rt_apex_min - location.location.query_rt)
                * 60
                <= location.apex_tolerance_sec
            ]
            if len(matches) != 1:
                continue
            index, evaluation = matches[0]
            assert evaluation.integration is not None
            rt = evaluation.integration.rt_apex_min
            nl_observed = (
                result.cid_nl_relations is not None
                and index in result.cid_nl_relations.supported_candidate_indexes
            )
            coordinates[key] = rt
            selections[key] = IstdReferencePeakSelection(
                target.label,
                sample,
                rt,
                direct.get(key),
                nl_observed,
                "cross_sample_physical_peak",
                tuple(sorted(s for s, _ in peers)),
            )
    return coordinates, tuple(selections[key] for key in sorted(selections))


def resolve_targeted_istd_backfill(
    population: TargetedCapturePopulation,
    assessments: SampleAssessments,
    *,
    config: ExtractionConfig,
    alignment_config: AlignmentConfig,
    rt_frame: RtFrame,
) -> IstdAnchoredBackfill:
    """Backfill HOLD samples of an ISTD-related Target from their direct peers."""

    target = population.target.definition.target
    if not (target.is_istd or target.istd_pair) or not assessments:
        return IstdAnchoredBackfill.none()
    assessment_by_sample = {
        sample.sample_name: assessment for sample, assessment in assessments
    }
    sample_by_id = {sample.sample_name: sample for sample in population.samples}
    direct_supports = tuple(
        CidNlCellGroupSupport(
            sample=sample_by_id[item.owner_observation.sample_key],
            assessment=assessment_by_sample[item.owner_observation.sample_key],
            hypothesis=item.owner_hypothesis,
        )
        for item in population.capture_inputs
        if item.owner_hypothesis is not None
        and item.owner_observation is not None
        and item.owner_hypothesis.method == HYPOTHESIS_METHOD_DDA_CID_NL
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


__all__ = ["resolve_istd_reference_peaks", "resolve_targeted_istd_backfill"]
