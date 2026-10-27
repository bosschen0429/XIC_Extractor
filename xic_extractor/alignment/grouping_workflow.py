"""Compose stable cross-sample membership before bounded missing-cell work."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

from xic_extractor.alignment.backfill.membership_selection import (
    select_backfill_memberships,
)
from xic_extractor.alignment.cid_nl_grouping import (
    CID_NL_GROUPING_RULES,
    cid_nl_backfill_comparison_rules,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingContractError,
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    CrossSamplePeakGroupHypothesis,
    _expected_cross_sample_lineage,
    _hypothesis_from_owner_group,
    _identity_from_owners,
    construct_cross_sample_peak_group_hypotheses,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
)
from xic_extractor.alignment.grouping_bootstrap import (
    InitialGroupingScope,
    RetainedGroupingCandidate,
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.membership_selection import MembershipComparison
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentContent,
    CidNlMethodAssessmentRef,
    is_ms1_only_cid_nl_backfill_assessment,
)
from xic_extractor.peak_detection.elution_context import SampleElutionContext
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.observations import PeakObservationRef
from xic_extractor.peak_detection.peak_hypothesis_identity import (
    HYPOTHESIS_METHOD_DDA_CID_NL,
    HYPOTHESIS_METHOD_MS1_OBSERVATION,
)
from xic_extractor.peak_detection.physical_peak_equivalence import (
    PhysicalPeakView,
    compare_physical_peak_views,
    physical_peak_view_from_observation,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity


def build_targeted_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
) -> CrossSampleGroupingResult:
    """Close Targeted membership from the exact selected capture in each cell."""

    sample_by_id = {
        sample.identity.sample_key: sample
        for sample in processing_owners.sample_resolutions
    }
    assessment_by_id = {
        sample.identity.sample_key: assessment
        for sample, assessment in sample_assessments
    }
    owners: list[SampleLocalMS1Owner] = []
    selected_by_id: dict[str, PeakHypothesis] = {}
    for capture in captures:
        selected = capture.selected_hypothesis
        if selected is None:
            continue
        observation = selected.peak_observation_refs[0]
        sample_id = observation.content.sample_key
        sample = sample_by_id.get(sample_id)
        if sample is None or sample_id in selected_by_id:
            raise CrossSampleGroupingContractError(
                "Targeted selection crossed or duplicated its canonical sample"
            )
        assessment = assessment_by_id.get(sample_id)
        if selected.hypothesis_method == HYPOTHESIS_METHOD_DDA_CID_NL:
            if assessment is None:
                raise CrossSampleGroupingContractError(
                    "Targeted CID-NL selection lacks its method assessment"
                )
            owner = owner_from_cid_nl_assessment_support(
                sample=sample, assessment=assessment, hypothesis=selected
            )
        elif selected.hypothesis_method == HYPOTHESIS_METHOD_MS1_OBSERVATION:
            owner = (
                owner_from_cid_nl_assessment_observation(
                    processing_owners=processing_owners,
                    sample=sample,
                    assessment=assessment,
                    hypothesis=selected,
                )
                if assessment is not None
                else owner_from_ms1_observation_hypothesis(
                    processing_owners=processing_owners,
                    sample=sample,
                    hypothesis=selected,
                )
            )
        else:
            raise CrossSampleGroupingContractError(
                "Targeted selection has an unsupported hypothesis method"
            )
        owners.append(owner)
        selected_by_id[sample_id] = selected

    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        config=alignment_config,
        sample_universe=frozenset(
            sample.metadata.raw_stem for sample in processing_owners.sample_resolutions
        ),
        rt_frame=rt_frame,
        rules=CID_NL_GROUPING_RULES,
    )
    identities = tuple(group.membership for group in groups)
    population = tuple(
        sorted(
            identities,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    if tuple(sorted(owner.owner_id for owner in owners)) != tuple(
        sorted(owner_id for group in groups for owner_id in group.owner_ids)
    ):
        raise CrossSampleGroupingContractError(
            "Targeted grouping lost or invented selected hypothesis identity"
        )
    return CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population,
            successors=population,
        ),
        rt_frame=rt_frame,
    )


def build_initial_cid_nl_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    elution_contexts: Sequence[SampleElutionContext] = (),
    rt_frame: SampleRtFrame,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    retained_hypotheses_by_assessment: Mapping[
        CidNlMethodAssessmentContent, tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    InitialGroupingScope,
    CrossSampleGroupingResult,
    tuple[RetainedGroupingCandidate, ...],
]:
    """Infer stable CID-NL groups without producing unused RT trajectories."""
    batch_samples = processing_owners.sample_resolutions
    grouping_scope, retained_candidates, rt_frame = bootstrap_cid_nl_initial_population(
        processing_owners=processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=alignment_config,
        sample_universe=frozenset(sample.metadata.raw_stem for sample in batch_samples),
        rt_frame=rt_frame,
        retain_observation_only_candidates=True,
        elution_contexts=elution_contexts,
        operation_counts_sink=operation_counts_sink,
        retained_hypotheses_by_assessment=retained_hypotheses_by_assessment,
    )
    groups = grouping_scope.groups
    identities = tuple(group.membership for group in groups)
    population = tuple(
        sorted(
            identities,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    result = CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        rt_frame=rt_frame,
    )
    return grouping_scope, result, retained_candidates


def regroup_cid_nl_cross_sample_groups(
    *,
    processing_owners: ProcessingOwnerReferences,
    grouping_scope: InitialGroupingScope,
    grouping_result: CrossSampleGroupingResult,
    alignment_config: AlignmentConfig,
    rt_frame: SampleRtFrame,
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
) -> tuple[InitialGroupingScope, CrossSampleGroupingResult]:
    """Reconsider every ordinary owner after the batch anchor warp is known."""
    owners = tuple(owner for group in grouping_scope.groups for owner in group.owners)
    trajectories = grouping_scope.peak_trajectories
    if rt_frame.has_coordinates:
        trajectories = resolve_peak_trajectories(
            owners,
            trajectories.source_contexts,
            config=alignment_config,
            injection_order=rt_frame.injection_order,
            rt_frame=rt_frame,
        )
    groups = group_cid_nl_owners(
        owners,
        config=alignment_config,
        sample_universe=frozenset(
            sample.metadata.raw_stem for sample in processing_owners.sample_resolutions
        ),
        rt_frame=rt_frame,
        operation_counts_sink=operation_counts_sink,
        peak_trajectories=trajectories,
    )
    identities = tuple(group.membership for group in groups)
    population = tuple(
        sorted(
            identities,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    scope = replace(grouping_scope, groups=groups, peak_trajectories=trajectories)
    result = CrossSampleGroupingResult(
        processing_owners=processing_owners,
        partition_transition=_partition_transition(
            predecessors=population, successors=population
        ),
        rt_frame=rt_frame,
    )
    return scope, result


def group_cid_nl_owners(
    owners: Sequence[SampleLocalMS1Owner],
    *,
    config: AlignmentConfig,
    sample_universe: frozenset[str],
    rt_frame: RtFrame,
    known_identity_bindings: Sequence[tuple[str, str]] = (),
    operation_counts_sink: list[FeatureInferenceOperationCounts] | None = None,
    peak_trajectories: PeakTrajectories | None = None,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Consume inferred membership and apply the separate shoulder disposition."""
    groups = construct_cross_sample_peak_group_hypotheses(
        tuple(owners),
        accept_competition=True,
        config=config,
        sample_universe=sample_universe,
        rt_frame=rt_frame,
        known_identity_bindings=known_identity_bindings,
        operation_counts_sink=operation_counts_sink,
        peak_trajectories=peak_trajectories or PeakTrajectories(),
        rules=CID_NL_GROUPING_RULES,
    )
    return reject_shoulder_groups(groups, config=config)


def admit_cid_nl_group_backfills(
    *,
    result: CrossSampleGroupingResult,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    alignment_config: AlignmentConfig,
    member_observations: Mapping[str, PeakObservationRef],
    physical_peak_views: Mapping[str, PhysicalPeakView] | None = None,
    peak_trajectories: PeakTrajectories | None = None,
) -> tuple[
    CrossSampleGroupingResult,
    tuple[CrossSamplePeakGroupHypothesis, ...],
    dict[str, str],
    dict[CidNlMethodAssessmentContent, tuple[PeakHypothesis, ...]],
    frozenset[tuple[str, str]],
]:
    """Choose bounded acquired candidates while preserving all original owners."""
    by_id = {group.group_hypothesis_id: group for group in groups}
    proposals = []
    retained: dict[CidNlMethodAssessmentContent, tuple[PeakHypothesis, ...]] = {}
    for key, (sample, assessment) in sorted(backfills.items()):
        group_id, sample_id, subject_id = key
        group = by_id[group_id]
        if (
            sample_id != sample.identity.sample_key
            or subject_id != assessment.content.method_subject.method_subject_id
        ):
            raise CrossSampleGroupingContractError(
                "backfill crossed its sample or subject"
            )
        if (
            assessment.content.method_result != "SUPPORT"
            and not is_ms1_only_cid_nl_backfill_assessment(assessment)
        ):
            continue
        hypotheses = compose_peak_hypotheses_from_assessment_universe(
            processing_owners=result.processing_owners,
            sample=sample,
            assessment=assessment,
        )
        if len(hypotheses) != 1:
            continue
        hypothesis = hypotheses[0]
        owner = (
            owner_from_cid_nl_assessment_support(
                sample=sample, assessment=assessment, hypothesis=hypothesis
            )
            if assessment.content.method_result == "SUPPORT"
            else owner_from_cid_nl_assessment_observation(
                processing_owners=result.processing_owners,
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
        )
        physical_key = (
            sample_id,
            hypothesis.peak_observation_refs[0].content.peak_observation_content_id,
        )
        if any(member.sample_key == sample_id for member in group.member_subjects):
            continue
        proposals.append((group_id, physical_key, owner, hypothesis, assessment))
    if not proposals:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            {g.group_hypothesis_id: g.group_hypothesis_id for g in groups},
            retained,
            frozenset((group, sample) for group, sample, _ in backfills),
        )
    comparison_views = dict(physical_peak_views or {})
    for _, _, _, hypothesis, _ in proposals:
        observation = hypothesis.peak_observation_refs[0]
        observation_id = observation.content.peak_observation_content_id
        if observation_id not in comparison_views:
            comparison_views[observation_id] = physical_peak_view_from_observation(
                observation
            )
    occupied = contested_backfill_peaks(
        groups=groups,
        backfills=backfills,
        member_observations=member_observations,
        physical_peak_views=comparison_views,
        direct_only=True,
    )
    eligible = [p for p in proposals if p[1] not in occupied]
    if not eligible:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            {g.group_hypothesis_id: g.group_hypothesis_id for g in groups},
            retained,
            frozenset((group, sample) for group, sample, _ in backfills),
        )
    views = [comparison_views[p[1][1]] for p in eligible]
    conflicts = frozenset(
        (j, i)
        for i, proposal in enumerate(eligible)
        for j, other in enumerate(eligible[:i])
        if proposal[1][0] == other[1][0]
        and (
            proposal[0] == other[0]
            or proposal[1] == other[1]
            or compare_physical_peak_views(views[i], views[j]) == "same"
        )
    )
    comparison = MembershipComparison(
        tuple(
            {
                owner.owner_id: owner
                for owner in (
                    *(owner for group in groups for owner in group.owners),
                    *(p[2] for p in eligible),
                )
            }.values()
        ),
        rules=cid_nl_backfill_comparison_rules(
            frozenset(
                owner.owner_id
                for _, _, owner, _, assessment in eligible
                if assessment.content.method_result != "SUPPORT"
                and assessment.content.method_subject.composition is not None
            )
        ),
        config=alignment_config,
        rt_frame=result.rt_frame,
        trajectories=peak_trajectories or PeakTrajectories(),
    )
    low_localization = {
        i
        for i, proposal in enumerate(eligible)
        if (evaluation := proposal[3].peak_observation_refs[0].physical_evaluation)
        is not None
        and evaluation.question.selection_policy_version
        == PHYSICAL_PEAK_UNLOCATED_BACKFILL_POLICY
    }
    for owner_id in {eligible[index][2].owner_id for index in low_localization}:
        comparison.rt_weights[owner_id] *= 0.25
    selected, risks = select_backfill_memberships(
        comparison=comparison,
        original_groups={g.group_hypothesis_id: g.owner_ids for g in groups},
        proposals=tuple((p[0], p[2].owner_id) for p in eligible),
        conflicts=conflicts,
    )
    for index in selected & low_localization:
        risks[index] = replace(
            risks[index],
            risk_flags=tuple(
                sorted(
                    (*risks[index].risk_flags, "backfill_low_localization_confidence")
                )
            ),
        )
    selected_cells = {(eligible[i][0], eligible[i][1][0]) for i in selected}
    rejected_cells = frozenset(
        (group, sample)
        for group, sample, _ in backfills
        if (group, sample) not in selected_cells
    )
    additions: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for index in sorted(selected):
        group_id, physical_key, owner, hypothesis, assessment = eligible[index]
        if assessment.content.method_result != "SUPPORT":
            continue
        additions[group_id].append(owner)
        retained[assessment.content] = (hypothesis,)
    completed = []
    successor_ids = {}
    admitted: list[CrossSampleGroupMemberSubject] = []
    for group in groups:
        owners = additions.get(group.group_hypothesis_id, [])
        if not owners:
            successor = group
        else:
            combined = tuple(
                sorted((*group.owners, *owners), key=lambda item: item.owner_id)
            )
            identity = _identity_from_owners(
                combined,
                processing_request_id=result.processing_request_id,
                disposition=group.disposition,
            )
            successor = replace(
                _hypothesis_from_owner_group(
                    combined,
                    group_identity=identity,
                    feature_id=group.feature_id,
                    evidence=group.evidence,
                    identity_conflict=group.identity_conflict,
                    review_only=group.review_only,
                ),
                # The original inference/ISTD binding remains about its original
                # owners; backfill does not label new owners as known standards.
                inference_basis=group.inference_basis,
                inference_decisions=group.inference_decisions,
                # Keep the reference coordinates used to choose the canonical
                # backfill subject. New members cannot redirect another sample's
                # already completed group-bound query to a different subject.
                group_center_mz=group.group_center_mz,
                group_center_rt=group.group_center_rt,
            )
            admitted.extend(
                cast(CrossSampleGroupMemberSubject, owner.cross_sample_member_subject)
                for owner in owners
            )
        completed.append(successor)
        successor_ids[group.group_hypothesis_id] = successor.group_hypothesis_id
    result = replace(
        result,
        backfill_membership_risks=tuple(
            (successor_ids[eligible[i][0]], eligible[i][1][0], risks[i])
            for i in sorted(selected)
        ),
    )
    if not admitted:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            successor_ids,
            retained,
            rejected_cells,
        )
    identities = tuple(
        sorted(
            (
                cast(
                    CrossSampleGroupIdentity,
                    group.membership,
                )
                for group in completed
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    partition = CrossSamplePartitionTransition(
        status="assessable",
        predecessors=result.initial_population,
        successors=identities,
        lineage=_expected_cross_sample_lineage(
            predecessors=result.initial_population, successors=identities
        ),
        reason="membership_changed",
        admitted_members=tuple(
            sorted(admitted, key=lambda member: member.member_subject_id)
        ),
    )
    return (
        replace(result, partition_transition=partition),
        tuple(sorted(completed, key=lambda group: group.group_hypothesis_id)),
        successor_ids,
        retained,
        rejected_cells,
    )


def reject_shoulder_groups(
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    *,
    config: AlignmentConfig,
) -> tuple[CrossSamplePeakGroupHypothesis, ...]:
    """Reject an established group that is the split-off shoulder of another.

    Discovery can cut one chromatographic peak at a valley into a small shoulder
    and the peak. A sample holds one member per group, so the shoulders then form
    their own group, and its Backfill finds the whole peak where no shoulder was
    cut. A group is such a shoulder when more than half of its members share a
    boundary with a same-m/z member of one other established group and every
    member it shares a sample with is such a smaller, adjacent neighbour.
    """
    by_sample = {
        group.group_hypothesis_id: {owner.sample_stem: owner for owner in group.owners}
        for group in groups
        if group.disposition == "established"
    }
    rejected: dict[str, str] = {}
    for group_id, owners in by_sample.items():
        for other_id, others in by_sample.items():
            shared = owners.keys() & others.keys()
            if (
                other_id != group_id
                and 2 * len(shared) > len(owners)
                and all(
                    _is_shoulder_of(owners[sample], others[sample], config=config)
                    for sample in shared
                )
            ):
                rejected[group_id] = other_id
                break
    if not rejected:
        return groups
    return tuple(
        replace(
            group,
            membership=replace(group.membership, disposition="rejected"),
            evidence=(
                f"{group.evidence};shoulder_of:{rejected[group.group_hypothesis_id]}"
            ),
        )
        if group.group_hypothesis_id in rejected
        else group
        for group in groups
    )


def _is_shoulder_of(
    owner: SampleLocalMS1Owner,
    neighbour: SampleLocalMS1Owner,
    *,
    config: AlignmentConfig,
) -> bool:
    return (
        owner.owner_area < neighbour.owner_area
        and (
            owner.owner_peak_end_rt == neighbour.owner_peak_start_rt
            or owner.owner_peak_start_rt == neighbour.owner_peak_end_rt
        )
        and within_ppm(owner.precursor_mz, neighbour.precursor_mz, config.preferred_ppm)
    )


def contested_backfill_peaks(
    *,
    groups: tuple[CrossSamplePeakGroupHypothesis, ...],
    backfills: Mapping[
        tuple[str, str, str],
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef],
    ],
    member_observations: Mapping[str, PeakObservationRef],
    physical_peak_views: Mapping[str, PhysicalPeakView] | None = None,
    direct_only: bool = False,
) -> frozenset[tuple[str, str]]:
    """Resolve physical claims once, before SUPPORT admission or cell creation.

    Direct members of Matrix rows retain occupancy. ``direct_only`` excludes
    competition between Backfills: the bounded assignment owner resolves those
    claims instead of rejecting every claimant. An archive member writes no
    value and cannot occupy a peak. The caller supplies retained observations,
    not reconstructed peak-shape summaries or rounded coordinates.
    """

    def view_for(peak: PeakObservationRef) -> PhysicalPeakView:
        if physical_peak_views is not None:
            view = physical_peak_views.get(peak.content.peak_observation_content_id)
            if view is not None:
                return view
        return physical_peak_view_from_observation(peak)

    members_by_sample: dict[str, list[tuple[str, PhysicalPeakView]]] = defaultdict(list)
    occupied = set()
    for group in groups:
        if group.disposition != "established":
            continue
        for member in group.member_subjects:
            sample_id = member.sample_key
            for observation_id in member.physical_observation_ids:
                occupied.add((sample_id, observation_id))
                observation = member_observations[observation_id]
                members_by_sample[sample_id].append(
                    (observation_id, view_for(observation))
                )
    backfills_by_sample: dict[str, list[tuple[str, str, PhysicalPeakView]]] = (
        defaultdict(list)
    )
    for (group_id, sample_id, _), (_, assessment) in backfills.items():
        for peak in assessment.candidate_peaks:
            backfills_by_sample[sample_id].append(
                (
                    group_id,
                    peak.content.peak_observation_content_id,
                    view_for(peak),
                )
            )
    contested = set()
    for sample_id, entries in backfills_by_sample.items():
        for index, (group_id, observation_id, view) in enumerate(entries):
            key = (sample_id, observation_id)
            if key in occupied or any(
                compare_physical_peak_views(view, member_view) == "same"
                for _, member_view in members_by_sample[sample_id]
            ):
                contested.add(key)
            for other_group, other_id, other_view in (
                () if direct_only else entries[:index]
            ):
                if other_group != group_id and (
                    observation_id == other_id
                    or compare_physical_peak_views(view, other_view) == "same"
                ):
                    contested.update((key, (sample_id, other_id)))
    return frozenset(contested)


def _partition_transition(
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSamplePartitionTransition:
    return CrossSamplePartitionTransition(
        status="assessable",
        predecessors=predecessors,
        successors=successors,
        lineage=_expected_cross_sample_lineage(
            predecessors=predecessors, successors=successors
        ),
        reason=(
            "unchanged_membership_no_lineage"
            if predecessors == successors
            else "membership_changed"
        ),
    )
