"""Finite CID-NL adapter from retained ModeTrack fitting to CrossSample."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import TypeAlias

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_neighborhood_graph import (
    CrossSampleInitialGroupingReceipt,
    mint_cross_sample_initial_grouping_receipt,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleCoupledProposalContext,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSampleRepairedObservation,
    build_bounded_connected_neighborhood,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackAssignment,
    ModeTrackAssignmentBatch,
    ModeTrackContractError,
    ModeTrackFitOnceSource,
    ModeTrackModelSet,
    ModeTrackRepairAssignmentObservation,
    ModeTrackSupportAnchor,
    ModeTrackTrajectoryPolicy,
    validate_mode_track_assignment_batch_for_consumption,
)
from xic_extractor.alignment.mode_track.assignment_scope import ModeTrackAssignmentScope
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
    bootstrap_mode_tracks_from_ms1_hypotheses,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

from .contracts import (
    AssignedModeTrackRefinementEvidence,
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
    ModeTrackRefinementEvidence,
    ModeTrackRefinementProposal,
    ModeTrackTrajectoryInput,
    UntrainedModeTrackRefinementEvidence,
    _mint_accepted_mode_track_refinement,
    _mint_insufficient_mode_track_training,
    _mint_rejected_mode_track_refinement,
    _mint_unassessable_mode_track_refinement,
    mint_mode_track_trajectory_input,
)

_REFINEMENT_RUN_MINT_TOKEN = object()
ModeTrackRefinementOwner: TypeAlias = (
    SampleLocalMS1Owner | CrossSampleRepairedObservation
)


def _propose_mode_track_refinement(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    owners: Sequence[ModeTrackRefinementOwner],
    evidence: ModeTrackRefinementEvidence,
) -> ModeTrackRefinementProposal:
    """Derive one complete proposal only from owner-minted evidence."""

    if type(trajectory_input) is not ModeTrackTrajectoryInput:
        raise ModeTrackRefinementContractError(
            "ModeTrack refinement requires an exact trajectory input"
        )
    owner_by_member_id = _validate_current_owner_population(
        trajectory_input=trajectory_input,
        owners=owners,
    )
    if type(evidence) is UntrainedModeTrackRefinementEvidence:
        _validate_untrained_evidence(
            trajectory_input=trajectory_input,
            owner_by_member_id=owner_by_member_id,
            evidence=evidence,
        )
        if trajectory_input.neighborhood_status == "unassessable":
            return _mint_unassessable_mode_track_refinement(
                trajectory_input_id=trajectory_input.trajectory_input_id,
            )
        return _mint_insufficient_mode_track_training(
            trajectory_input_id=trajectory_input.trajectory_input_id,
            reason_codes=(evidence.reason,),
        )
    if type(evidence) is not AssignedModeTrackRefinementEvidence:
        raise ModeTrackRefinementContractError(
            "ModeTrack refinement requires a closed owner evidence variant"
        )
    _validate_assigned_evidence(
        trajectory_input=trajectory_input,
        owner_by_member_id=owner_by_member_id,
        evidence=evidence,
    )
    if trajectory_input.neighborhood_status == "unassessable":
        return _mint_unassessable_mode_track_refinement(
            trajectory_input_id=trajectory_input.trajectory_input_id,
        )
    assignment_by_member_id = _assignments_for_current_members(
        owner_by_member_id=owner_by_member_id,
        assignment_batches=evidence.assignment_batches,
    )
    rejected_reasons = tuple(
        sorted(
            {
                f"assignment_{assignment.state}"
                for assignment in assignment_by_member_id.values()
                if assignment.state != "uniquely_assigned"
            }
        )
    )
    if rejected_reasons:
        return _mint_rejected_mode_track_refinement(
            trajectory_input_id=trajectory_input.trajectory_input_id,
            reason_codes=rejected_reasons,
        )
    successor_members_by_group: dict[str, list[str]] = {}
    model_group_ids = {track.group_hypothesis_id for track in evidence.model_set.tracks}
    for member_id, assignment in assignment_by_member_id.items():
        group_id = assignment.group_hypothesis_id
        if group_id is None:
            raise ModeTrackRefinementContractError(
                "unique assignment omitted its target group"
            )
        if group_id not in model_group_ids:
            raise ModeTrackRefinementContractError(
                "assignment target group is outside the owner model set"
            )
        successor_members_by_group.setdefault(group_id, []).append(member_id)
    _validate_owner_derived_partition(
        trajectory_input=trajectory_input,
        owner_by_member_id=owner_by_member_id,
        successor_members_by_group=successor_members_by_group,
    )
    return _mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=tuple(
            tuple(member_ids)
            for _, member_ids in sorted(successor_members_by_group.items())
        ),
    )


MODE_TRACK_NEIGHBORHOOD_BUDGET = CrossSampleNeighborhoodBudget(
    max_members=86,
    max_eligible_edges=3655,
)


@dataclass(frozen=True, slots=True)
class ModeTrackRefinementTransaction:
    trajectory_input: ModeTrackTrajectoryInput
    proposal: ModeTrackRefinementProposal
    application_context: CrossSampleCoupledProposalContext


@dataclass(frozen=True, slots=True)
class ModeTrackRefinementRun:
    assignment_scope: ModeTrackAssignmentScope
    model_set: ModeTrackModelSet
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    fit_once_source: ModeTrackFitOnceSource
    transactions: tuple[ModeTrackRefinementTransaction, ...]
    predecessors: tuple[CrossSampleGroupIdentity, ...]
    owners: tuple[SampleLocalMS1Owner, ...]
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    initial_grouping_receipt: CrossSampleInitialGroupingReceipt
    anchors: tuple[ModeTrackSupportAnchor, ...]
    injection_order: tuple[tuple[str, int], ...]
    evidence_profile: ModeTrackEvidenceProfile
    budget: CrossSampleNeighborhoodBudget
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _REFINEMENT_RUN_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "ModeTrack refinement run must be minted by ModeTrack"
            )


def bootstrap_mode_track_refinement_from_cid_nl_support(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
    retain_observation_only_candidates: bool = False,
) -> ModeTrackRefinementRun:
    """Bootstrap groups, diagnostics, and initial component refinements."""

    bootstrap_result = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=processing_owners,
        sample_assessments=sample_assessments,
        alignment_config=alignment_config,
        trajectory_policy=trajectory_policy,
        drift_lookup=drift_lookup,
        retain_observation_only_candidates=retain_observation_only_candidates,
    )
    return _bootstrap_mode_track_refinement(
        processing_owners=processing_owners,
        bootstrap_result=bootstrap_result,
        alignment_config=alignment_config,
        trajectory_policy=trajectory_policy,
        drift_lookup=drift_lookup,
    )


def bootstrap_mode_track_refinement_from_ms1_hypotheses(
    *,
    processing_owners: ProcessingOwnerReferences,
    samples: tuple[ResolvedCanonicalSampleIdentity, ...],
    sample_hypotheses: tuple[
        tuple[ResolvedCanonicalSampleIdentity, PeakHypothesis], ...
    ],
    scope_mz_interval: tuple[float, float],
    scope_rt_interval: tuple[float, float],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
) -> ModeTrackRefinementRun:
    bootstrap_result = bootstrap_mode_tracks_from_ms1_hypotheses(
        processing_owners=processing_owners,
        samples=samples,
        sample_hypotheses=sample_hypotheses,
        scope_mz_interval=scope_mz_interval,
        scope_rt_interval=scope_rt_interval,
        alignment_config=alignment_config,
        trajectory_policy=trajectory_policy,
        drift_lookup=drift_lookup,
    )
    return _bootstrap_mode_track_refinement(
        processing_owners=processing_owners,
        bootstrap_result=bootstrap_result,
        alignment_config=alignment_config,
        trajectory_policy=trajectory_policy,
        drift_lookup=drift_lookup,
    )


def _bootstrap_mode_track_refinement(
    *,
    processing_owners: ProcessingOwnerReferences,
    bootstrap_result: tuple[
        ModeTrackAssignmentScope,
        ModeTrackModelSet,
        tuple[ModeTrackAssignmentBatch, ...],
        ModeTrackFitOnceSource,
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None,
) -> ModeTrackRefinementRun:
    assignment_scope, model_set, assignment_batches, fit_once_source = bootstrap_result
    groups = assignment_scope.groups
    predecessors = validate_cross_sample_group_population(
        tuple(_required_group_identity(group) for group in groups)
    )
    owners = tuple(owner for group in groups for owner in group.owners)
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...] = ()
    grouping_receipt = mint_cross_sample_initial_grouping_receipt(
        processing_request_id=processing_owners.request.processing_request_id,
        groups=predecessors,
        indivisible_member_sets=tuple(
            tuple(member.member_subject_id for member in group.member_subjects)
            for group in groups
            if group.inference_basis is not None
            and group.inference_basis.known_identity_label
        ),
    )
    injection_order = _injection_order(assignment_batches)
    owner_injection_order = {
        owner.sample_stem: injection_order[owner.sample_stem] for owner in owners
    }
    anchor_by_owner_id = {
        anchor.peak_hypothesis_id: anchor
        for track in model_set.tracks
        for anchor in track.anchors
    }
    evidence_profile = ModeTrackEvidenceProfile(
        trajectory_policy_version=trajectory_policy.trajectory_policy_version,
        band_policy_version=trajectory_policy.band_policy_version,
        prediction_half_width_min=trajectory_policy.prediction_half_width_min,
        max_interpolation_gap=trajectory_policy.max_interpolation_gap,
        max_extrapolation_gap=trajectory_policy.max_extrapolation_gap,
    )

    transactions = []
    owner_by_member_id = {
        _required_owner_member(owner).member_subject_id: owner for owner in owners
    }
    members = tuple(
        member for predecessor in predecessors for member in predecessor.member_subjects
    )
    full_neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=classified_edges,
        budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
        grouping_receipt=grouping_receipt,
    )
    predecessor_by_member_id = {
        member.member_subject_id: predecessor
        for predecessor in predecessors
        for member in predecessor.member_subjects
    }
    assigned_evidence = AssignedModeTrackRefinementEvidence(
        variant="assigned",
        processing_owners=processing_owners,
        model_set=model_set,
        assignment_batches=assignment_batches,
    )
    proposal: ModeTrackRefinementProposal
    for component in full_neighborhood.connected_components:
        component_ids = frozenset(component)
        component_predecessors = tuple(
            sorted(
                {predecessor_by_member_id[member_id] for member_id in component_ids},
                key=lambda group: group.group_hypothesis_id,
            )
        )
        if any(
            not {member.member_subject_id for member in group.member_subjects}.issubset(
                component_ids
            )
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
        component_receipt = mint_cross_sample_initial_grouping_receipt(
            processing_request_id=processing_owners.request.processing_request_id,
            groups=component_predecessors,
            evaluated_edges=component_edges,
            indivisible_member_sets=tuple(
                members
                for members in grouping_receipt.indivisible_member_sets
                if set(members).issubset(component_ids)
            ),
        )
        trajectory_input = mint_mode_track_trajectory_input(
            predecessors=component_predecessors,
            owners=component_owners,
            classified_edges=component_edges,
            anchors=tuple(
                anchor_by_owner_id[owner.owner_id]
                for owner in component_owners
                if owner.owner_id in anchor_by_owner_id
            ),
            injection_order={
                owner.sample_stem: injection_order[owner.sample_stem]
                for owner in component_owners
            },
            evidence_profile=evidence_profile,
            budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
            initial_grouping_receipt=component_receipt,
        )
        if trajectory_input.neighborhood_status == "unassessable":
            proposal = _mint_unassessable_mode_track_refinement(
                trajectory_input_id=trajectory_input.trajectory_input_id,
            )
        elif not trajectory_input.anchor_ids:
            proposal = _mint_insufficient_mode_track_training(
                trajectory_input_id=trajectory_input.trajectory_input_id,
                reason_codes=("no_direct_support_anchor",),
            )
        else:
            proposal = _propose_mode_track_refinement(
                trajectory_input=trajectory_input,
                owners=component_owners,
                evidence=assigned_evidence,
            )
        transactions.append(
            ModeTrackRefinementTransaction(
                trajectory_input=trajectory_input,
                proposal=proposal,
                application_context=CrossSampleCoupledProposalContext(
                    predecessors=component_predecessors,
                    classified_edges=component_edges,
                    budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
                    grouping_receipt=component_receipt,
                ),
            )
        )
    transactions.sort(
        key=lambda transaction: (
            transaction.application_context.predecessors[0].group_hypothesis_id
        )
    )
    return ModeTrackRefinementRun(
        assignment_scope=assignment_scope,
        model_set=model_set,
        assignment_batches=assignment_batches,
        fit_once_source=fit_once_source,
        transactions=tuple(transactions),
        predecessors=predecessors,
        owners=owners,
        classified_edges=classified_edges,
        initial_grouping_receipt=grouping_receipt,
        anchors=tuple(
            sorted(anchor_by_owner_id.values(), key=lambda item: item.anchor_id)
        ),
        injection_order=tuple(sorted(owner_injection_order.items())),
        evidence_profile=evidence_profile,
        budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
        _mint_token=_REFINEMENT_RUN_MINT_TOKEN,
    )


def _validate_current_owner_population(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    owners: Sequence[ModeTrackRefinementOwner],
) -> dict[str, ModeTrackRefinementOwner]:
    owner_tuple = tuple(owners)
    if not owner_tuple or any(
        type(owner) not in {SampleLocalMS1Owner, CrossSampleRepairedObservation}
        for owner in owner_tuple
    ):
        raise ModeTrackRefinementContractError(
            "ModeTrack owners must be an exact non-empty owner population"
        )
    snapshot_apexes = dict(trajectory_input.member_apexes)
    if tuple(snapshot_apexes) != tuple(
        member_id for member_id, _ in trajectory_input.member_apexes
    ):
        raise ModeTrackRefinementContractError(
            "trajectory snapshot member apexes are duplicated"
        )
    owner_by_member_id: dict[str, ModeTrackRefinementOwner] = {}
    physical_to_member: dict[str, str] = {}
    for owner in owner_tuple:
        member = _required_owner_member(owner)
        member_id = member.member_subject_id
        if member_id in owner_by_member_id:
            raise ModeTrackRefinementContractError(
                "ModeTrack owner population duplicates a member"
            )
        if member_id not in snapshot_apexes:
            raise ModeTrackRefinementContractError(
                "ModeTrack owner population crossed the trajectory snapshot"
            )
        if member.processing_request_id != trajectory_input.processing_request_id:
            raise ModeTrackRefinementContractError(
                "ModeTrack owner crossed the trajectory processing request"
            )
        if owner.owner_apex_rt != snapshot_apexes[member_id]:
            raise ModeTrackRefinementContractError(
                "ModeTrack owner apex is stale for the trajectory snapshot"
            )
        for physical_id in member.physical_observation_ids:
            prior = physical_to_member.setdefault(physical_id, member_id)
            if prior != member_id:
                raise ModeTrackRefinementContractError(
                    "ModeTrack owner population duplicates a physical observation"
                )
        owner_by_member_id[member_id] = owner
    if tuple(sorted(owner_by_member_id)) != trajectory_input.member_subject_ids:
        raise ModeTrackRefinementContractError(
            "ModeTrack owner population does not close the trajectory snapshot"
        )
    return owner_by_member_id


def _validate_untrained_evidence(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    owner_by_member_id: dict[str, ModeTrackRefinementOwner],
    evidence: UntrainedModeTrackRefinementEvidence,
) -> None:
    if evidence.evidence_profile != trajectory_input.evidence_profile:
        raise ModeTrackRefinementContractError(
            "untrained evidence profile crossed the trajectory snapshot"
        )
    if tuple(anchor.anchor_id for anchor in evidence.anchors) != (
        trajectory_input.anchor_ids
    ):
        raise ModeTrackRefinementContractError(
            "untrained evidence anchors do not close the trajectory snapshot"
        )
    physical_to_member = {
        physical_id: member_id
        for member_id, owner in owner_by_member_id.items()
        for physical_id in _required_owner_member(owner).physical_observation_ids
    }
    for anchor in evidence.anchors:
        if anchor.processing_request_id != trajectory_input.processing_request_id:
            raise ModeTrackRefinementContractError(
                "untrained evidence anchor crossed the processing request"
            )
        member_id = physical_to_member.get(anchor.peak_observation_content_id)
        if member_id is None:
            raise ModeTrackRefinementContractError(
                "untrained evidence anchor crossed the current owner population"
            )
        if anchor.apex_rt_min != owner_by_member_id[member_id].owner_apex_rt:
            raise ModeTrackRefinementContractError(
                "untrained evidence anchor apex is stale"
            )


def _validate_assigned_evidence(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    owner_by_member_id: dict[str, ModeTrackRefinementOwner],
    evidence: AssignedModeTrackRefinementEvidence,
) -> None:
    processing_owners = evidence.processing_owners
    model_set = evidence.model_set
    if (
        processing_owners.request.processing_request_id
        != trajectory_input.processing_request_id
        or model_set.processing_request_id != trajectory_input.processing_request_id
    ):
        raise ModeTrackRefinementContractError(
            "assigned evidence crossed the trajectory processing request"
        )
    if evidence.evidence_profile != trajectory_input.evidence_profile:
        raise ModeTrackRefinementContractError(
            "assigned evidence policy crossed the trajectory snapshot"
        )
    for batch in evidence.assignment_batches:
        if batch.processing_request_id != trajectory_input.processing_request_id:
            raise ModeTrackRefinementContractError(
                "assignment batch crossed the trajectory processing request"
            )
        try:
            validate_mode_track_assignment_batch_for_consumption(
                processing_owners=processing_owners,
                model_set=model_set,
                assignment_batch=batch,
            )
        except (ModeTrackContractError, TypeError, ValueError) as exc:
            raise ModeTrackRefinementContractError(
                f"assignment batch failed owner revalidation: {exc}"
            ) from exc
    owner_sample_ids = {
        _required_owner_member(owner).canonical_sample_identity_id
        for owner in owner_by_member_id.values()
    }
    ordered_batch_sample_ids = tuple(
        batch.canonical_sample_identity_id for batch in evidence.assignment_batches
    )
    if len(ordered_batch_sample_ids) != len(set(ordered_batch_sample_ids)):
        raise ModeTrackRefinementContractError(
            "assignment batches duplicate a current owner sample"
        )
    if not owner_sample_ids <= set(ordered_batch_sample_ids):
        raise ModeTrackRefinementContractError(
            "assignment batches do not close the current owner samples"
        )


def _assignments_for_current_members(
    *,
    owner_by_member_id: dict[str, ModeTrackRefinementOwner],
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...],
) -> dict[str, ModeTrackAssignment]:
    physical_to_member = {
        physical_id: member_id
        for member_id, owner in owner_by_member_id.items()
        for physical_id in _required_owner_member(owner).physical_observation_ids
    }
    assignment_by_member_id: dict[str, ModeTrackAssignment] = {}
    for batch in assignment_batches:
        for assignment in batch.assignments:
            candidate = assignment.candidate_observation
            if candidate is None:
                continue
            member_id: str | None
            if type(candidate) is ModeTrackRepairAssignmentObservation:
                member_id = candidate.repaired_member_subject_id
            else:
                member_id = physical_to_member.get(
                    candidate.peak_observation_content_id
                )
            if member_id is None:
                continue
            if member_id in assignment_by_member_id:
                raise ModeTrackRefinementContractError(
                    "assignment evidence duplicates a current owner"
                )
            if (
                type(candidate) is ModeTrackRepairAssignmentObservation
                and candidate.apex_rt_min != owner_by_member_id[member_id].owner_apex_rt
            ):
                raise ModeTrackRefinementContractError(
                    "repair assignment apex does not match the current owner apex"
                )
            assignment_by_member_id[member_id] = assignment
    if set(assignment_by_member_id) != set(owner_by_member_id):
        raise ModeTrackRefinementContractError(
            "assignment evidence does not close the current owner population"
        )
    return assignment_by_member_id


def _validate_owner_derived_partition(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    owner_by_member_id: dict[str, ModeTrackRefinementOwner],
    successor_members_by_group: dict[str, list[str]],
) -> None:
    flattened_member_ids = {
        member_id
        for members in successor_members_by_group.values()
        for member_id in members
    }
    if flattened_member_ids != set(owner_by_member_id):
        raise ModeTrackRefinementContractError(
            "owner-derived proposal is not a complete trajectory partition"
        )
    member_to_successor = {
        member_id: group_id
        for group_id, member_ids in successor_members_by_group.items()
        for member_id in member_ids
    }
    if any(
        len({member_to_successor[member] for member in members}) != 1
        for members in trajectory_input.indivisible_member_sets
    ):
        raise ModeTrackRefinementContractError(
            "owner-derived proposal splits a confirmed identity"
        )
    for left, right, disposition in trajectory_input.exclusions:
        if (
            disposition == "excluded"
            and member_to_successor[left] == member_to_successor[right]
        ):
            raise ModeTrackRefinementContractError(
                "owner-derived proposal crosses an explicitly excluded member edge"
            )
    for member_ids in successor_members_by_group.values():
        samples: set[str] = set()
        for member_id in member_ids:
            sample_id = _required_owner_member(
                owner_by_member_id[member_id]
            ).canonical_sample_identity_id
            if sample_id in samples:
                raise ModeTrackRefinementContractError(
                    "owner-derived proposal duplicates a sample in one group"
                )
            samples.add(sample_id)


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
    owner: ModeTrackRefinementOwner,
) -> CrossSampleGroupMemberSubject:
    member = owner.cross_sample_member_subject
    if member is None:
        raise ModeTrackRefinementContractError(
            "ModeTrack owner is missing its canonical member"
        )
    return member
