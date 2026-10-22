"""Compose stable cross-sample membership before bounded missing-cell work."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
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
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureInferenceOperationCounts,
    supports_existing_feature,
)
from xic_extractor.alignment.grouping_bootstrap import (
    InitialGroupingScope,
    RetainedGroupingCandidate,
    bootstrap_cid_nl_initial_population,
)
from xic_extractor.alignment.grouping_evidence import peak_shape_union_conflict
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_peak_hypotheses_from_assessment_universe,
    owner_from_cid_nl_assessment_observation,
    owner_from_cid_nl_assessment_support,
    owner_from_ms1_observation_hypothesis,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import RtFrame, SampleRtFrame
from xic_extractor.mass_accuracy import within_ppm
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
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
        sample.identity.sample_identity_id: sample
        for sample in processing_owners.sample_resolutions
    }
    assessment_by_id = {
        sample.identity.sample_identity_id: assessment
        for sample, assessment in sample_assessments
    }
    owners: list[SampleLocalMS1Owner] = []
    selected_by_id: dict[str, PeakHypothesis] = {}
    for capture in captures:
        selected = capture.selected_hypothesis
        if selected is None:
            continue
        observation = selected.peak_observation_refs[0]
        sample_id = observation.content.canonical_sample_identity_id
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
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
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
        tuple[str, str], tuple[PeakHypothesis, ...]
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
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    if any(identity is None for identity in identities):
        raise CrossSampleGroupingContractError(
            "initial group lost its canonical identity"
        )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
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
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    if any(identity is None for identity in identities):
        raise CrossSampleGroupingContractError(
            "window group lost its canonical identity"
        )
    population = tuple(
        sorted(
            (identity for identity in identities if identity is not None),
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
        config=config,
        sample_universe=sample_universe,
        rt_frame=rt_frame,
        known_identity_bindings=known_identity_bindings,
        operation_counts_sink=operation_counts_sink,
        peak_trajectories=peak_trajectories,
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
) -> tuple[
    CrossSampleGroupingResult,
    tuple[CrossSamplePeakGroupHypothesis, ...],
    dict[str, str],
    dict[tuple[str, str], tuple[PeakHypothesis, ...]],
    frozenset[tuple[str, str]],
]:
    """Extend only the requested group with uniquely claimed supported evidence.

    Initial owners never move. A candidate claimed by several groups, an
    occupied sample, or an unsupported match remains unassigned evidence.
    """
    by_id = {group.group_hypothesis_id: group for group in groups}
    proposals = []
    claim_backfills = {
        key: pair
        for key, pair in backfills.items()
        if pair[1].content.method_result != "SUPPORT"
    }
    retained: dict[tuple[str, str], tuple[PeakHypothesis, ...]] = {}
    for key, (sample, assessment) in sorted(backfills.items()):
        group_id, sample_id, subject_id = key
        group = by_id[group_id]
        if (
            sample_id != sample.identity.sample_identity_id
            or subject_id != assessment.content.method_subject.method_subject_id
        ):
            raise CrossSampleGroupingContractError(
                "backfill crossed its sample or subject"
            )
        if assessment.content.method_result != "SUPPORT":
            continue
        hypotheses = compose_peak_hypotheses_from_assessment_universe(
            processing_owners=result.processing_owners,
            sample=sample,
            assessment=assessment,
        )
        if len(hypotheses) != 1:
            continue
        hypothesis = hypotheses[0]
        owner = owner_from_cid_nl_assessment_support(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
        physical_key = (
            sample_id,
            hypothesis.peak_observation_refs[0].content.peak_observation_content_id,
        )
        if any(
            member.canonical_sample_identity_id == sample_id
            for member in group.member_subjects
        ):
            continue
        if not supports_existing_feature(
            owner,
            group.owners,
            config=alignment_config,
            rt_frame=result.rt_frame,
            rules=CID_NL_GROUPING_RULES,
        ):
            continue
        proposals.append((group_id, physical_key, owner, hypothesis, assessment))
        claim_backfills[key] = (sample, assessment)
    contested_peaks = contested_backfill_peaks(
        groups=groups,
        backfills=claim_backfills,
        member_observations=member_observations,
    )
    cell_claims: dict[tuple[str, str], int] = defaultdict(int)
    for group_id, physical_key, *_ in proposals:
        cell_claims[(group_id, physical_key[0])] += 1
    eligible = []
    candidates_by_group: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for proposal in proposals:
        group_id, physical_key, owner, *_ = proposal
        if (
            physical_key in contested_peaks
            or cell_claims[(group_id, physical_key[0])] != 1
        ):
            continue
        eligible.append(proposal)
        candidates_by_group[group_id].append(owner)
    # Each proposal already agrees with the original members, but shape
    # compatibility is not transitive. Check simultaneous additions only after
    # claim resolution; an inadmissible proposal cannot veto a valid recovery.
    # Neither endpoint of a conflict wins by sample/owner ordering. Independent
    # compatible additions remain eligible rather than rejecting the whole batch.
    conflicted: set[str] = set()
    for candidates in candidates_by_group.values():
        for index, owner in enumerate(candidates):
            for other in candidates[:index]:
                if conflict := peak_shape_union_conflict(
                    (owner,),
                    (other,),
                    alignment_config,
                ):
                    conflicted.update(conflict)
    additions: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for group_id, physical_key, owner, hypothesis, assessment in eligible:
        if owner.owner_id in conflicted:
            continue
        additions[group_id].append(owner)
        retained[
            (physical_key[0], assessment.artifact.content_identity.artifact_content_id)
        ] = (hypothesis,)
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
                    public_family_id=group.public_family_id,
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
    if not admitted:
        return (
            result,
            tuple(sorted(groups, key=lambda group: group.group_hypothesis_id)),
            successor_ids,
            retained,
            contested_peaks,
        )
    identities = tuple(
        sorted(
            (
                cast(
                    CrossSampleGroupIdentity,
                    validate_cross_sample_peak_group_identity(
                        group, require_canonical=True
                    ),
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
        contested_peaks,
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
            disposition="rejected",
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
) -> frozenset[tuple[str, str]]:
    """Resolve physical claims once, before SUPPORT admission or cell creation.

    Direct members of Matrix rows retain occupancy. Competing Backfills have
    no order-dependent winner, regardless of their method result. An archive
    member writes no value and cannot occupy a peak. The caller supplies retained
    observations, not reconstructed peak-shape summaries or rounded coordinates.
    """
    members_by_sample: dict[str, list[tuple[str, PhysicalPeakView]]] = defaultdict(list)
    occupied = set()
    for group in groups:
        if group.disposition != "established":
            continue
        for member in group.member_subjects:
            sample_id = member.canonical_sample_identity_id
            for observation_id in member.physical_observation_ids:
                occupied.add((sample_id, observation_id))
                observation = member_observations[observation_id]
                members_by_sample[sample_id].append(
                    (observation_id, _observation_physical_view(observation))
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
                    _observation_physical_view(peak),
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
            for other_group, other_id, other_view in entries[:index]:
                if other_group != group_id and (
                    observation_id == other_id
                    or compare_physical_peak_views(view, other_view) == "same"
                ):
                    contested.update((key, (sample_id, other_id)))
    return frozenset(contested)


def _observation_physical_view(peak: PeakObservationRef) -> PhysicalPeakView:
    content, provenance = peak.content, peak.generation_provenance
    integration = content.integration
    return PhysicalPeakView(
        acquisition_source_id=provenance.acquisition_source_id,
        apex_rt_min=integration.rt_apex_min,
        left_rt_min=integration.rt_left_min,
        right_rt_min=integration.rt_right_min,
        xic_mz=content.mz,
        xic_ppm_tol=provenance.query_ppm_tol,
        rt=content.rt,
        raw=content.intensity,
    )


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
