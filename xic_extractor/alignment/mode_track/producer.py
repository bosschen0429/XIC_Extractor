"""Finite CID-NL adapter from retained ModeTrack fitting to CrossSample."""

from __future__ import annotations

from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    build_bounded_connected_neighborhood,
    classify_cross_sample_initial_neighborhood_edge,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.identity_coherence.family_context import FamilyContextRef
from xic_extractor.alignment.identity_coherence.mode_track_assignment import (
    ModeTrackAssignment,
    ModeTrackAssignmentBatch,
    ModeTrackModelSet,
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.identity_coherence.mode_track_producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.processing_identity import ProcessingOwnerReferences
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

from .contracts import (
    AcceptedModeTrackRefinement,
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
    ModeTrackRefinementProposal,
    ModeTrackTrajectoryInput,
    RejectedModeTrackRefinement,
    apply_accepted_mode_track_refinement,
    mint_mode_track_trajectory_input,
    propose_mode_track_successor_partition,
)

MODE_TRACK_NEIGHBORHOOD_BUDGET = CrossSampleNeighborhoodBudget(
    max_members=86,
    max_eligible_edges=3655,
)


@dataclass(frozen=True, slots=True)
class ModeTrackRefinementTransaction:
    trajectory_input: ModeTrackTrajectoryInput
    proposal: ModeTrackRefinementProposal
    application: CrossSampleSuccessorApplication | None


@dataclass(frozen=True, slots=True)
class ModeTrackRefinementRun:
    family_context: FamilyContextRef
    model_set: ModeTrackModelSet
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    transactions: tuple[ModeTrackRefinementTransaction, ...]
    final_population: tuple[CrossSampleGroupIdentity, ...]


def bootstrap_mode_track_refinement_from_cid_nl_support(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
) -> ModeTrackRefinementRun:
    family_context, model_set, assignment_batches = (
        bootstrap_mode_tracks_from_cid_nl_support(
            processing_owners=processing_owners,
            sample_assessments=sample_assessments,
            alignment_config=alignment_config,
            trajectory_policy=trajectory_policy,
        )
    )
    groups = family_context.mode_tracks
    predecessors = validate_cross_sample_group_population(
        tuple(
            _required_group_identity(group)
            for group in groups
        )
    )
    owners = tuple(owner for group in groups for owner in group.owners)
    owner_by_member_id = {
        _required_owner_member(owner).member_subject_id: owner for owner in owners
    }
    classified_edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=owners[left_index],
            right_owner=owners[right_index],
            context="complete_link",
            config=alignment_config,
        )
        for left_index in range(len(owners))
        for right_index in range(left_index + 1, len(owners))
    )
    members = tuple(
        member for predecessor in predecessors for member in predecessor.member_subjects
    )
    full_neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=classified_edges,
        budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
    )
    predecessor_by_member_id = {
        member.member_subject_id: predecessor
        for predecessor in predecessors
        for member in predecessor.member_subjects
    }
    injection_order = _injection_order(assignment_batches)
    anchor_owner_ids = {
        anchor.peak_hypothesis_id
        for track in model_set.tracks
        for anchor in track.anchors
    }
    assignment_by_owner_id = _assignment_by_owner_id(assignment_batches)
    if set(assignment_by_owner_id) != {
        owner.owner_id for owner in owners
    }:
        raise ModeTrackRefinementContractError(
            "ModeTrack assignments do not close the owner population"
        )
    evidence_profile = ModeTrackEvidenceProfile(
        trajectory_policy_version=trajectory_policy.trajectory_policy_version,
        band_policy_version=trajectory_policy.band_policy_version,
        prediction_half_width_min=trajectory_policy.prediction_half_width_min,
        max_interpolation_gap=trajectory_policy.max_interpolation_gap,
        max_extrapolation_gap=trajectory_policy.max_extrapolation_gap,
    )

    population = predecessors
    transactions = []
    for component in full_neighborhood.connected_components:
        component_ids = frozenset(component)
        component_predecessors = tuple(
            sorted(
                {predecessor_by_member_id[member_id] for member_id in component_ids},
                key=lambda group: group.group_hypothesis_id,
            )
        )
        if any(
            not {
                member.member_subject_id for member in group.member_subjects
            }.issubset(component_ids)
            for group in component_predecessors
        ):
            raise ModeTrackRefinementContractError(
                "one predecessor spans disconnected eligible components"
            )
        component_owners = tuple(
            owner_by_member_id[member_id] for member_id in sorted(component_ids)
        )
        component_edges = tuple(
            edge
            for edge in classified_edges
            if edge.left_member_subject_id in component_ids
            and edge.right_member_subject_id in component_ids
        )
        trajectory_input = mint_mode_track_trajectory_input(
            predecessors=component_predecessors,
            owners=component_owners,
            classified_edges=component_edges,
            anchor_owner_ids=tuple(
                owner.owner_id
                for owner in component_owners
                if owner.owner_id in anchor_owner_ids
            ),
            injection_order={
                owner.sample_stem: injection_order[owner.sample_stem]
                for owner in component_owners
            },
            evidence_profile=evidence_profile,
            budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
        )
        rejected_reasons = tuple(
            sorted(
                {
                    f"assignment_{assignment_by_owner_id[owner.owner_id].state}"
                    for owner in component_owners
                    if assignment_by_owner_id[owner.owner_id].state
                    != "uniquely_assigned"
                }
            )
        )
        if rejected_reasons:
            proposal: ModeTrackRefinementProposal = RejectedModeTrackRefinement(
                variant="rejected_refinement",
                trajectory_input_id=trajectory_input.trajectory_input_id,
                reason_codes=rejected_reasons,
            )
        else:
            successor_members_by_group: dict[str, list[str]] = {}
            for owner in component_owners:
                assignment = assignment_by_owner_id[owner.owner_id]
                if assignment.group_hypothesis_id is None:
                    raise ModeTrackRefinementContractError(
                        "unique assignment omitted its target group"
                    )
                successor_members_by_group.setdefault(
                    assignment.group_hypothesis_id,
                    [],
                ).append(_required_owner_member(owner).member_subject_id)
            proposal = propose_mode_track_successor_partition(
                trajectory_input=trajectory_input,
                successor_member_subject_ids=tuple(
                    tuple(member_ids)
                    for _, member_ids in sorted(successor_members_by_group.items())
                ),
            )
        application = None
        if type(proposal) is AcceptedModeTrackRefinement:
            application = apply_accepted_mode_track_refinement(
                trajectory_input=trajectory_input,
                proposal=proposal,
                population=population,
            )
            unaffected = tuple(
                group for group in population if group not in application.predecessors
            )
            population = validate_cross_sample_group_population(
                (*unaffected, *application.successors)
            )
        transactions.append(
            ModeTrackRefinementTransaction(
                trajectory_input=trajectory_input,
                proposal=proposal,
                application=application,
            )
        )
    return ModeTrackRefinementRun(
        family_context=family_context,
        model_set=model_set,
        assignment_batches=assignment_batches,
        transactions=tuple(transactions),
        final_population=population,
    )


def _required_group_identity(group) -> CrossSampleGroupIdentity:
    identity = validate_cross_sample_peak_group_identity(group, require_canonical=True)
    if identity is None:
        raise ModeTrackRefinementContractError(
            "ModeTrack group is missing canonical CrossSample identity"
        )
    return identity


def _injection_order(
    batches: tuple[ModeTrackAssignmentBatch, ...],
) -> dict[str, int]:
    result: dict[str, int] = {}
    for batch in batches:
        for assignment in batch.assignments:
            sample_stem = assignment.sample.metadata.raw_stem
            prior = result.setdefault(sample_stem, assignment.injection_order)
            if prior != assignment.injection_order:
                raise ModeTrackRefinementContractError(
                    "ModeTrack assignments crossed injection order"
                )
    return result


def _assignment_by_owner_id(
    batches: tuple[ModeTrackAssignmentBatch, ...],
) -> dict[str, ModeTrackAssignment]:
    result: dict[str, ModeTrackAssignment] = {}
    for batch in batches:
        for assignment in batch.assignments:
            owner_id = assignment.peak_hypothesis_id
            if owner_id is None:
                continue
            if owner_id in result:
                raise ModeTrackRefinementContractError(
                    "ModeTrack emitted duplicate owner assignments"
                )
            result[owner_id] = assignment
    return result


def _required_owner_member(
    owner: SampleLocalMS1Owner,
) -> CrossSampleGroupMemberSubject:
    member = owner.cross_sample_member_subject
    if member is None:
        raise ModeTrackRefinementContractError(
            "ModeTrack owner is missing its canonical member"
        )
    return member
