"""ISTD-anchored conservative Backfill for one fixed Target.

A Target that is an internal standard, or is paired with one, may recover a
HOLD sample from the CID-NL peak group its confirmed peers form. This module
assembles that decision from domain models only: direct SUPPORT peers, the
per-sample method assessments, the sample population, and the paired-ISTD
observations that localize a recovery window. It reads no acquisition result;
the caller mints the paired-ISTD observations and hands them in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from xic_extractor.alignment.backfill.conservative import (
    ConservativeBackfillCellDecision,
    assess_conservative_backfill_group,
    map_conservative_backfill_candidates,
    mint_conservative_backfill_candidate_universe,
    plan_conservative_backfill_targets,
)
from xic_extractor.alignment.cell_observation.observations import (
    CidNlCellGroupContext,
    CidNlCellGroupSupport,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectory
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

SampleAssessments = tuple[
    tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
]


@dataclass(frozen=True, slots=True)
class IstdAnchoredBackfill:
    """Backfill decisions and the recovery observation each mapped sample won."""

    decisions: tuple[ConservativeBackfillCellDecision, ...]
    recovery_observations: Mapping[str, PeakObservationRef]

    @classmethod
    def none(cls) -> IstdAnchoredBackfill:
        return cls(decisions=(), recovery_observations=MappingProxyType({}))


def resolve_istd_anchored_backfill(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
    target: ResolvedTargetDefinition,
    direct_supports: tuple[CidNlCellGroupSupport, ...],
    sample_assessments: SampleAssessments,
    paired_istd_observations: Callable[[], tuple[PeakObservationRef, ...]],
    rt_half_window_min: float,
    alignment_config: AlignmentConfig,
) -> IstdAnchoredBackfill:
    """Decide Backfill for HOLD samples from their direct SUPPORT peers.

    ``paired_istd_observations`` is called only when a Backfill plan exists, so
    a Target without a recoverable group never mints them. Direct observations
    freeze the reference group: a missing sample never votes for its own
    eligibility. A fixed Target with competing confirmed groups cannot acquire
    an identity by choosing one of their recovery windows.
    """

    assessment_by_sample = {
        sample.identity.sample_identity_id: assessment
        for sample, assessment in sample_assessments
    }
    sample_by_id = {
        sample.identity.sample_identity_id: sample for sample in sample_population
    }
    groups = (
        construct_cross_sample_peak_group_hypotheses(
            tuple(
                owner_from_cid_nl_assessment_support(
                    sample=support.sample,
                    assessment=support.assessment,
                    hypothesis=support.hypothesis,
                )
                for support in direct_supports
            ),
            config=alignment_config,
        )
        if direct_supports
        else ()
    )
    plans = (
        plan_conservative_backfill_targets(groups, sample_population=sample_population)
        if len(groups) == 1
        else ()
    )
    paired_observations = tuple(paired_istd_observations()) if plans else ()
    pair_rts = {
        sample_by_id[
            peak.content.canonical_sample_identity_id
        ].metadata.raw_stem: peak.content.integration.rt_apex_min
        for peak in paired_observations
    }
    decisions: list[ConservativeBackfillCellDecision] = []
    for group, missing_ids in plans:
        group_context = CidNlCellGroupContext(
            group=group,
            supports=tuple(
                sorted(
                    direct_supports,
                    key=lambda support: support.sample.identity.sample_identity_id,
                )
            ),
            config=alignment_config,
            processing_attempt=processing_owners.attempt,
        )
        direct_by_sample = {
            support.sample.identity.sample_identity_id: support
            for support in direct_supports
        }
        cells = tuple(
            mint_cell_observation_ref(
                processing_owners=processing_owners,
                sample=sample,
                target=target,
                group_context=group_context,
                method_assessment=assessment,
                peak_observation=(
                    direct_by_sample[
                        sample.identity.sample_identity_id
                    ].hypothesis.peak_observation_refs[0]
                    if sample.identity.sample_identity_id in direct_by_sample
                    else None
                ),
                peak_hypothesis=(
                    direct_by_sample[sample.identity.sample_identity_id].hypothesis
                    if sample.identity.sample_identity_id in direct_by_sample
                    else None
                ),
                unassigned_support=(
                    assessment.content.method_result == "SUPPORT"
                    and sample.identity.sample_identity_id not in direct_by_sample
                ),
            )
            for sample, assessment in sample_assessments
        )
        facts = assess_conservative_backfill_group(
            cells, sample_population=sample_population
        )
        trajectory = PeakTrajectory(
            group.owner_ids,
            tuple(
                (
                    support.sample.metadata.raw_stem,
                    support.hypothesis.integration.rt_apex_min,
                )
                for support in direct_supports
            ),
        )
        locations = trajectory.backfill_locations(
            tuple(
                sample_by_id[sample_id].metadata.raw_stem
                for sample_id in sorted(missing_ids)
            ),
            reference_samples=frozenset(
                support.sample.metadata.raw_stem for support in direct_supports
            ),
            injection_order={
                s.metadata.raw_stem: s.metadata.injection_order
                for s in sample_population
                if s.metadata.injection_order is not None
            },
            sample_cohorts={
                s.metadata.raw_stem: s.metadata.batch_id for s in sample_population
            },
            rt_warp=None,
            backfill_half_window_min=rt_half_window_min,
            paired_istd_rts=pair_rts,
        )
        for sample_id in sorted(missing_ids):
            sample = sample_by_id[sample_id]
            if assessment_by_sample[sample_id].content.method_result != "HOLD":
                continue
            location = locations[sample.metadata.raw_stem]
            if (
                not target.definition.target.is_istd
                and location.location.basis != "paired_istd"
            ):
                continue
            universe = mint_conservative_backfill_candidate_universe(
                processing_owners=processing_owners,
                sample=sample,
                group_facts=(facts,),
                candidate_assessment=assessment_by_sample[sample_id],
                localization=location,
                localization_sources=paired_observations,
            )
            decisions.extend(
                map_conservative_backfill_candidates(
                    (facts,),
                    sample=sample,
                    candidate_universes=(universe,),
                )
            )
    recovery_observations = {
        decision.target_cell.content.canonical_sample_identity_id: (
            decision.matched_candidate.peak_observation
        )
        for decision in decisions
        if decision.matched_candidate is not None
    }
    return IstdAnchoredBackfill(
        decisions=tuple(decisions),
        recovery_observations=MappingProxyType(recovery_observations),
    )


__all__ = [
    "IstdAnchoredBackfill",
    "SampleAssessments",
    "resolve_istd_anchored_backfill",
]
