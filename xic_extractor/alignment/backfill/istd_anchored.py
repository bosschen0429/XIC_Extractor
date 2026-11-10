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
    mint_cell_observation,
)
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.peak_trajectories import PeakTrajectory
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.configuration.target_identity import ResolvedTargetDefinition
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessment,
)
from xic_extractor.peak_detection.observations import PeakObservation
from xic_extractor.sample_metadata import SampleMetadata

SampleAssessments = tuple[tuple[SampleMetadata, CidNlMethodAssessment], ...]


@dataclass(frozen=True, slots=True)
class IstdAnchoredBackfill:
    """Backfill decisions and the recovery observation each mapped sample won."""

    decisions: tuple[ConservativeBackfillCellDecision, ...]
    recovery_observations: Mapping[str, PeakObservation]

    @classmethod
    def none(cls) -> IstdAnchoredBackfill:
        return cls(decisions=(), recovery_observations=MappingProxyType({}))


def resolve_istd_anchored_backfill(
    *,
    sample_population: tuple[SampleMetadata, ...],
    target: ResolvedTargetDefinition,
    direct_supports: tuple[CidNlCellGroupSupport, ...],
    sample_assessments: SampleAssessments,
    paired_istd_observations: Callable[[], tuple[PeakObservation, ...]],
    rt_half_window_min: float,
    alignment_config: AlignmentConfig,
    rt_frame: RtFrame,
) -> IstdAnchoredBackfill:
    """Decide Backfill for HOLD samples from their direct SUPPORT peers.

    ``paired_istd_observations`` is called only when a Backfill plan exists, so
    a Target without a recoverable group never mints them. Direct observations
    freeze the reference group: a missing sample never votes for its own
    eligibility. A fixed Target with competing confirmed groups cannot acquire
    an identity by choosing one of their recovery windows.
    """

    assessment_by_sample = {
        sample.sample_name: assessment for sample, assessment in sample_assessments
    }
    sample_by_id = {sample.sample_name: sample for sample in sample_population}
    owners = tuple(
        owner_from_cid_nl_assessment_support(
            sample=support.sample,
            assessment=support.assessment,
            hypothesis=support.hypothesis,
        )
        for support in direct_supports
    )
    # The fixed Target is the identity: its confirmed peers form one group
    # however far RT drifts over the run. Two bound owners in one sample remain
    # an unresolved conflict rather than a choice between recovery windows.
    target_identity = target.lookup_label
    groups = (
        construct_cross_sample_peak_group_hypotheses(
            owners,
            config=alignment_config,
            rt_frame=rt_frame,
            known_identity_bindings=tuple(
                (owner.owner_id, target_identity) for owner in owners
            ),
            rules=CID_NL_GROUPING_RULES,
        )
        if owners
        else ()
    )
    plans = (
        plan_conservative_backfill_targets(groups, sample_population=sample_population)
        if len(groups) == 1
        else ()
    )
    paired_observations = tuple(paired_istd_observations()) if plans else ()
    pair_rts = {
        sample_by_id[peak.sample_key].raw_stem: peak.integration.rt_apex_min
        for peak in paired_observations
    }
    decisions: list[ConservativeBackfillCellDecision] = []
    for group, missing_ids in plans:
        group_context = CidNlCellGroupContext(
            group=group,
            supports=tuple(
                sorted(
                    direct_supports,
                    key=lambda support: support.sample.sample_name,
                )
            ),
            config=alignment_config,
            rt_frame=rt_frame,
        )
        direct_by_sample = {
            support.sample.sample_name: support for support in direct_supports
        }
        cells = tuple(
            mint_cell_observation(
                sample=sample,
                target=target,
                group_context=group_context,
                method_assessment=assessment,
                peak_observation=(
                    direct_by_sample[
                        sample.sample_name
                    ].hypothesis.peak_observation_refs[0]
                    if sample.sample_name in direct_by_sample
                    else None
                ),
                peak_hypothesis=(
                    direct_by_sample[sample.sample_name].hypothesis
                    if sample.sample_name in direct_by_sample
                    else None
                ),
                unassigned_support=(
                    assessment.method_result == "SUPPORT"
                    and sample.sample_name not in direct_by_sample
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
                    support.sample.raw_stem,
                    support.hypothesis.integration.rt_apex_min,
                )
                for support in direct_supports
            ),
        )
        # Analyte recovery requires its paired ISTD; trajectory-only forecasts
        # are useful for an ISTD itself, not an alternative analyte identity.
        locate_missing = (
            trajectory.backfill_locations
            if target.definition.target.is_istd
            else trajectory.paired_istd_locations
        )
        locations = locate_missing(
            tuple(
                sample_by_id[sample_id].raw_stem for sample_id in sorted(missing_ids)
            ),
            reference_samples=frozenset(
                support.sample.raw_stem for support in direct_supports
            ),
            rt_frame=rt_frame,
            backfill_half_window_min=rt_half_window_min,
            paired_istd_rts=pair_rts,
        )
        for sample_id in sorted(missing_ids):
            sample = sample_by_id[sample_id]
            if assessment_by_sample[sample_id].method_result != "HOLD":
                continue
            location = locations.get(sample.raw_stem)
            if location is None:
                continue
            universe = mint_conservative_backfill_candidate_universe(
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
        decision.target_cell.sample_key: (decision.matched_candidate.peak_observation)
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
