"""Plan sparse CID-NL group revisits without runtime state or acquisition I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from xic_extractor.alignment.backfill.conservative import (
    plan_conservative_backfill_targets,
)
from xic_extractor.alignment.cell_observation.group_context import (
    select_cid_nl_group_backfill_subject,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import BackfillPeakLocation
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
    is_cid_nl_missing_ms1_rejection,
    is_unsearched_cid_nl_discovery_assessment,
)
from xic_extractor.peak_detection.cid_nl_method_subject import CidNlMethodSubject
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.cid_nl_completion import CidNlWorksetGrouping


@dataclass(frozen=True, slots=True)
class CidNlBackfillQuestion:
    """One eligible sample/group question, already localized by its science owner.

    Sparse execution may not have an initial assessment for this sample/subject
    yet. Runtime obtains that assessment before resolving the question; it does
    not choose a new group, query window or revisit policy.
    """

    sample_identity_id: str
    group: CrossSamplePeakGroupHypothesis
    method_subject: CidNlMethodSubject
    localization: BackfillPeakLocation | None = None
    observed_apex_tolerance_sec: float = 0.0

    def resolve(self, assessment: CidNlMethodAssessmentRef) -> CidNlBackfillDecision:
        return CidNlBackfillDecision(
            question=self,
            assessment=assessment,
            action=decide_cid_nl_backfill_revisit(
                assessment,
                observed_rt=(
                    None
                    if self.localization is None
                    else self.localization.location.observed_rt
                ),
                apex_tolerance_sec=self.observed_apex_tolerance_sec,
            ),
        )


@dataclass(frozen=True, slots=True)
class CidNlBackfillDecision:
    question: CidNlBackfillQuestion
    assessment: CidNlMethodAssessmentRef
    action: Literal["reuse", "search", "skip"]


def decide_cid_nl_backfill_revisit(
    assessment: CidNlMethodAssessmentRef,
    *,
    observed_rt: float | None = None,
    apex_tolerance_sec: float = 0.0,
) -> Literal["reuse", "search", "skip"]:
    """Reuse HOLD evidence unless an observed pattern locates a different peak.

    SUPPORT outside this group belongs to another peak and requires a search.
    A forecast alone does not establish a different observed peak.
    """
    if assessment.content.method_result == "SUPPORT":
        return "search"
    if not (
        assessment.content.method_result == "HOLD"
        or is_unsearched_cid_nl_discovery_assessment(assessment)
        or is_cid_nl_missing_ms1_rejection(assessment)
    ):
        return "skip"
    if not assessment.candidate_peaks:
        return "search"
    if observed_rt is not None and not any(
        abs(peak.content.integration.rt_apex_min - observed_rt) * 60.0
        <= apex_tolerance_sec
        for peak in assessment.candidate_peaks
    ):
        return "search"
    return "reuse"


def plan_grouped_cid_nl_backfills(
    worksets: tuple[CidNlWorksetGrouping, ...],
    *,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
    half_windows_by_sample: Mapping[str, float],
) -> tuple[CidNlBackfillQuestion, ...]:
    """Resolve subject, trajectory and window facts from frozen workset membership."""
    samples = {
        sample.identity.sample_identity_id: sample for sample in sample_population
    }
    rt_frame = rt_frame.for_sample_stems(
        frozenset(sample.metadata.raw_stem for sample in sample_population)
    )
    questions: list[CidNlBackfillQuestion] = []
    for workset in worksets:
        direct = {
            candidate.peak_hypothesis_id: candidate.direct_support_assessment
            for candidate in workset.retained_candidates
            if candidate.direct_support_assessment is not None
        }
        groups = tuple(
            group
            for group in workset.grouping_scope.groups
            if all(owner_id in direct for owner_id in group.owner_ids)
        )
        for group, target_ids in plan_conservative_backfill_targets(
            groups, sample_population=sample_population
        ):
            if not target_ids:
                continue
            subject = select_cid_nl_group_backfill_subject(
                group,
                tuple(
                    {
                        direct[owner_id].content.method_subject
                        for owner_id in group.owner_ids
                    }
                ),
            )
            assert group.group_center_rt is not None
            trajectory = workset.grouping_scope.peak_trajectories.trajectory_for(
                group.owners
            )
            locations = trajectory.backfill_locations(
                tuple(
                    samples[sample_id].metadata.raw_stem
                    for sample_id in sorted(target_ids)
                ),
                reference_samples=frozenset(
                    owner.sample_stem
                    for owner in group.owners
                    if not owner.is_provisional
                ),
                rt_frame=rt_frame,
                backfill_half_window_min=half_windows_by_sample[min(target_ids)],
            )
            questions.extend(
                CidNlBackfillQuestion(
                    sample_identity_id=sample_id,
                    group=group,
                    method_subject=subject,
                    localization=locations[samples[sample_id].metadata.raw_stem],
                    observed_apex_tolerance_sec=alignment_config.owner_apex_close_sec,
                )
                for sample_id in sorted(target_ids)
            )
    return tuple(questions)


def plan_subject_cid_nl_backfills(
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    *,
    method_subject: CidNlMethodSubject,
    processing_owners: ProcessingOwnerReferences,
    sample_population: tuple[ResolvedCanonicalSampleIdentity, ...],
    alignment_config: AlignmentConfig,
) -> tuple[CidNlBackfillQuestion, ...]:
    """Plan the direct subject route without inventing a frozen batch trajectory."""
    support_owners: list[SampleLocalMS1Owner] = []
    for sample, assessment in sample_assessments:
        if assessment.content.method_result != "SUPPORT":
            continue
        hypotheses = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=processing_owners, sample=sample, assessment=assessment
        )
        support_owners.extend(
            owner_from_cid_nl_assessment_support(
                sample=sample, assessment=assessment, hypothesis=hypothesis
            )
            for hypothesis in hypotheses
        )
    if not support_owners:
        return ()
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(support_owners), config=alignment_config, rt_frame=SampleRtFrame()
    )
    return tuple(
        CidNlBackfillQuestion(sample_id, group, method_subject)
        for group, target_ids in plan_conservative_backfill_targets(
            groups, sample_population=sample_population
        )
        for sample_id in sorted(target_ids)
    )
