"""Immutable request-bound inputs for ModeTrack refinement."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.cross_sample_neighborhood_graph import INITIAL_PAIR_POLICY
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleClassifiedEdge,
    CrossSampleCoupledProposalContext,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleRepairedObservation,
    CrossSampleSuccessorApplication,
    apply_coupled_successor_proposal,
    build_bounded_connected_neighborhood,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_id,
)

from .assignment import (
    ModeTrackAssignmentBatch,
    ModeTrackModelSet,
    ModeTrackSupportAnchor,
)

_TRAJECTORY_INPUT_PREFIX = "xic.mode-track-trajectory-input:"
_TRAJECTORY_INPUT_MINT_TOKEN = object()
_EVIDENCE_MINT_TOKEN = object()
_PROPOSAL_MINT_TOKEN = object()

ModeTrackTrajectoryOwner: TypeAlias = (
    SampleLocalMS1Owner | CrossSampleRepairedObservation
)


class ModeTrackRefinementContractError(ValueError):
    """Raised when ModeTrack input or proposal authority fails closed."""


@dataclass(frozen=True, slots=True)
class ModeTrackEvidenceProfile:
    trajectory_policy_version: str
    band_policy_version: str
    prediction_half_width_min: float
    max_interpolation_gap: int
    max_extrapolation_gap: int

    def __post_init__(self) -> None:
        _required_text(
            self.trajectory_policy_version,
            field="trajectory_policy_version",
        )
        _required_text(self.band_policy_version, field="band_policy_version")
        if (
            not isinstance(self.prediction_half_width_min, float)
            or not math.isfinite(self.prediction_half_width_min)
            or self.prediction_half_width_min <= 0.0
        ):
            raise ModeTrackRefinementContractError(
                "prediction_half_width_min must be a positive canonical float"
            )
        for field_name, value in (
            ("max_interpolation_gap", self.max_interpolation_gap),
            ("max_extrapolation_gap", self.max_extrapolation_gap),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ModeTrackRefinementContractError(
                    f"{field_name} must be a nonnegative integer"
                )


@dataclass(frozen=True, slots=True)
class ModeTrackTrajectoryInput:
    trajectory_input_id: str
    processing_request_id: str
    anchor_ids: tuple[str, ...]
    member_subject_ids: tuple[str, ...]
    member_apexes: tuple[tuple[str, float], ...]
    injection_order: tuple[tuple[str, int], ...]
    evidence_profile: ModeTrackEvidenceProfile
    exclusions: tuple[tuple[str, str, str], ...]
    eligible_member_pairs: tuple[tuple[str, str], ...]
    neighborhood_status: Literal["assessable", "unassessable"]
    _mint_token: InitVar[object] = field(kw_only=True)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _TRAJECTORY_INPUT_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "trajectory input must be minted by the ModeTrack owner"
            )
        if tuple(sorted(set(self.anchor_ids))) != self.anchor_ids:
            raise ModeTrackRefinementContractError(
                "trajectory anchor IDs must be canonical and unique"
            )
        for anchor_id in self.anchor_ids:
            _required_text(anchor_id, field="anchor_id")
        expected = typed_identity_digest(
            _TRAJECTORY_INPUT_PREFIX,
            canonical_identity_basis_bytes(
                _trajectory_input_payload(
                    processing_request_id=self.processing_request_id,
                    anchor_ids=self.anchor_ids,
                    member_subject_ids=self.member_subject_ids,
                    member_apexes=self.member_apexes,
                    injection_order=self.injection_order,
                    evidence_profile=self.evidence_profile,
                    exclusions=self.exclusions,
                    eligible_member_pairs=self.eligible_member_pairs,
                )
            ),
        )
        if self.trajectory_input_id != expected:
            raise ModeTrackRefinementContractError("trajectory input identity mismatch")


@dataclass(frozen=True, slots=True)
class UntrainedModeTrackRefinementEvidence:
    """Closed owner-typed evidence for an unavailable assignment pass."""

    variant: Literal["untrained", "assignment_unavailable"]
    anchors: tuple[ModeTrackSupportAnchor, ...]
    evidence_profile: ModeTrackEvidenceProfile
    reason: Literal["untrained", "assignment_unavailable"]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _EVIDENCE_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "untrained evidence must be minted by the ModeTrack owner"
            )
        if self.variant not in {"untrained", "assignment_unavailable"}:
            raise ModeTrackRefinementContractError(
                "untrained evidence uses a closed assignment-unavailable variant"
            )
        if self.reason not in {"untrained", "assignment_unavailable"}:
            raise ModeTrackRefinementContractError(
                "untrained evidence reason is not closed"
            )
        if self.reason != self.variant:
            raise ModeTrackRefinementContractError(
                "untrained evidence reason must match its closed variant"
            )
        if type(self.anchors) is not tuple or any(
            type(anchor) is not ModeTrackSupportAnchor for anchor in self.anchors
        ):
            raise ModeTrackRefinementContractError(
                "untrained evidence requires owner-typed anchors"
            )
        anchor_ids = tuple(anchor.anchor_id for anchor in self.anchors)
        if anchor_ids != tuple(sorted(set(anchor_ids))):
            raise ModeTrackRefinementContractError(
                "untrained evidence anchors must be canonical-sorted"
            )
        if type(self.evidence_profile) is not ModeTrackEvidenceProfile:
            raise ModeTrackRefinementContractError(
                "untrained evidence requires an owner-typed evidence profile"
            )


@dataclass(frozen=True, slots=True)
class AssignedModeTrackRefinementEvidence:
    """Closed owner-minted assignment evidence consumed by ModeTrack."""

    variant: Literal["assigned"]
    processing_owners: ProcessingOwnerReferences
    model_set: ModeTrackModelSet
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]

    @property
    def anchors(self) -> tuple[ModeTrackSupportAnchor, ...]:
        """Return the canonical anchor projection owned by the model set."""

        return tuple(
            sorted(
                (
                    anchor
                    for track in self.model_set.tracks
                    for anchor in track.anchors
                ),
                key=lambda anchor: anchor.anchor_id,
            )
        )

    @property
    def evidence_profile(self) -> ModeTrackEvidenceProfile:
        """Project the model policy into the trajectory evidence profile."""

        policy = self.model_set.policy
        return ModeTrackEvidenceProfile(
            trajectory_policy_version=policy.trajectory_policy_version,
            band_policy_version=policy.band_policy_version,
            prediction_half_width_min=policy.prediction_half_width_min,
            max_interpolation_gap=policy.max_interpolation_gap,
            max_extrapolation_gap=policy.max_extrapolation_gap,
        )

    def __post_init__(self) -> None:
        if self.variant != "assigned":
            raise ModeTrackRefinementContractError(
                "assigned evidence uses the wrong closed variant"
            )
        if not isinstance(self.processing_owners, ProcessingOwnerReferences):
            raise ModeTrackRefinementContractError(
                "assigned evidence requires owner-validated processing references"
            )
        if type(self.model_set) is not ModeTrackModelSet:
            raise ModeTrackRefinementContractError(
                "assigned evidence requires an owner-minted model set"
            )
        if type(self.assignment_batches) is not tuple or not self.assignment_batches:
            raise ModeTrackRefinementContractError(
                "assigned evidence requires immutable assignment batches"
            )
        if any(
            type(batch) is not ModeTrackAssignmentBatch
            for batch in self.assignment_batches
        ):
            raise ModeTrackRefinementContractError(
                "assigned evidence contains a non-owner assignment batch"
            )
        batch_ids = tuple(
            batch.assignment_batch_id for batch in self.assignment_batches
        )
        if batch_ids != tuple(sorted(set(batch_ids))):
            raise ModeTrackRefinementContractError(
                "assigned evidence batches must be canonical-sorted"
            )


ModeTrackRefinementEvidence: TypeAlias = (
    UntrainedModeTrackRefinementEvidence | AssignedModeTrackRefinementEvidence
)


@dataclass(frozen=True, slots=True)
class AcceptedModeTrackRefinement:
    variant: Literal["accepted_refinement"]
    trajectory_input_id: str
    successor_member_subject_ids: tuple[tuple[str, ...], ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PROPOSAL_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "accepted proposal must be minted by the ModeTrack owner"
            )
        if self.variant != "accepted_refinement":
            raise ModeTrackRefinementContractError(
                "accepted proposal uses the wrong closed variant"
            )
        _required_text(self.trajectory_input_id, field="trajectory_input_id")
        if (
            type(self.successor_member_subject_ids) is not tuple
            or not self.successor_member_subject_ids
            or any(
                type(group) is not tuple or not group
                for group in self.successor_member_subject_ids
            )
        ):
            raise ModeTrackRefinementContractError(
                "accepted proposal requires immutable successor member sets"
            )


@dataclass(frozen=True, slots=True)
class RejectedModeTrackRefinement:
    variant: Literal["rejected_refinement"]
    trajectory_input_id: str
    reason_codes: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PROPOSAL_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "rejected proposal must be minted by the ModeTrack owner"
            )
        if self.variant != "rejected_refinement":
            raise ModeTrackRefinementContractError(
                "rejected proposal uses the wrong closed variant"
            )
        _validate_proposal_common(self.trajectory_input_id, self.reason_codes)


@dataclass(frozen=True, slots=True)
class InsufficientModeTrackTraining:
    variant: Literal["insufficient_training"]
    trajectory_input_id: str
    reason_codes: tuple[str, ...]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PROPOSAL_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "insufficient proposal must be minted by the ModeTrack owner"
            )
        if self.variant != "insufficient_training":
            raise ModeTrackRefinementContractError(
                "insufficient-training proposal uses the wrong closed variant"
            )
        _validate_proposal_common(self.trajectory_input_id, self.reason_codes)


@dataclass(frozen=True, slots=True)
class UnassessableModeTrackRefinement:
    variant: Literal["unassessable"]
    trajectory_input_id: str
    reason: Literal["neighborhood_too_large"]
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _PROPOSAL_MINT_TOKEN:
            raise ModeTrackRefinementContractError(
                "unassessable proposal must be minted by the ModeTrack owner"
            )
        if self.variant != "unassessable" or self.reason != "neighborhood_too_large":
            raise ModeTrackRefinementContractError(
                "unassessable proposal must be neighborhood_too_large"
            )
        _required_text(self.trajectory_input_id, field="trajectory_input_id")


ModeTrackRefinementProposal: TypeAlias = (
    AcceptedModeTrackRefinement
    | RejectedModeTrackRefinement
    | InsufficientModeTrackTraining
    | UnassessableModeTrackRefinement
)


def mint_mode_track_trajectory_input(
    *,
    predecessors: Sequence[CrossSampleGroupIdentity],
    owners: Sequence[ModeTrackTrajectoryOwner],
    classified_edges: Sequence[CrossSampleClassifiedEdge],
    anchors: Sequence[ModeTrackSupportAnchor],
    injection_order: Mapping[str, int],
    evidence_profile: ModeTrackEvidenceProfile,
    budget: CrossSampleNeighborhoodBudget,
    processing_request_id: str | None = None,
) -> ModeTrackTrajectoryInput:
    predecessor_groups = validate_cross_sample_group_population(predecessors)
    request_ids = {group.processing_request_id for group in predecessor_groups}
    if len(request_ids) > 1:
        raise ModeTrackRefinementContractError(
            "trajectory predecessors crossed processing requests"
        )
    explicit_request_id = (
        None
        if processing_request_id is None
        else validate_typed_identity_id(
            processing_request_id,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
    )
    if request_ids:
        resolved_request_id = next(iter(request_ids))
        if (
            explicit_request_id is not None
            and explicit_request_id != resolved_request_id
        ):
            raise ModeTrackRefinementContractError(
                "trajectory request identity crossed its predecessors"
            )
    elif explicit_request_id is None:
        raise ModeTrackRefinementContractError(
            "empty trajectory requires an explicit processing request identity"
        )
    else:
        resolved_request_id = explicit_request_id
    members = tuple(
        member for group in predecessor_groups for member in group.member_subjects
    )
    member_by_id = {member.member_subject_id: member for member in members}
    predecessor_by_member_id = {
        member.member_subject_id: predecessor
        for predecessor in predecessor_groups
        for member in predecessor.member_subjects
    }
    owner_by_id: dict[str, ModeTrackTrajectoryOwner] = {}
    owner_by_member: dict[str, ModeTrackTrajectoryOwner] = {}
    for owner in owners:
        if type(owner) not in {
            SampleLocalMS1Owner,
            CrossSampleRepairedObservation,
        }:
            raise ModeTrackRefinementContractError("trajectory owner must be typed")
        member = owner.cross_sample_member_subject
        if member is None or member.member_subject_id not in member_by_id:
            raise ModeTrackRefinementContractError(
                "trajectory owner is not an exact predecessor member"
            )
        if member_by_id[member.member_subject_id] != member:
            raise ModeTrackRefinementContractError(
                "trajectory owner crossed its canonical member"
            )
        if owner.owner_id in owner_by_id or member.member_subject_id in owner_by_member:
            raise ModeTrackRefinementContractError("trajectory owner is duplicated")
        owner_by_id[owner.owner_id] = owner
        owner_by_member[member.member_subject_id] = owner
    if set(owner_by_member) != set(member_by_id):
        raise ModeTrackRefinementContractError(
            "trajectory owners do not close the predecessor population"
        )

    typed_anchors = tuple(anchors)
    if any(type(anchor) is not ModeTrackSupportAnchor for anchor in typed_anchors):
        raise ModeTrackRefinementContractError(
            "trajectory anchors must be exact owner-validated anchors"
        )
    for anchor in typed_anchors:
        anchor_owner = owner_by_id.get(anchor.peak_hypothesis_id)
        if (
            anchor.processing_request_id != resolved_request_id
            or anchor_owner is None
            or type(anchor_owner) is not SampleLocalMS1Owner
            or anchor.canonical_sample_identity_id
            != _required_owner_member(anchor_owner).canonical_sample_identity_id
            or anchor.group_hypothesis_id
            != predecessor_by_member_id[
                _required_owner_member(anchor_owner).member_subject_id
            ].group_hypothesis_id
        ):
            raise ModeTrackRefinementContractError(
                "trajectory anchor crossed its request or canonical owner"
            )
    canonical_anchor_ids = tuple(sorted(anchor.anchor_id for anchor in typed_anchors))
    if len(set(canonical_anchor_ids)) != len(canonical_anchor_ids) or len(
        {anchor.peak_hypothesis_id for anchor in typed_anchors}
    ) != len(typed_anchors):
        raise ModeTrackRefinementContractError("trajectory anchors are duplicated")
    member_subject_ids = tuple(sorted(member_by_id))
    member_apexes = tuple(
        sorted(
            (
                member_id,
                _finite_float(owner_by_member[member_id].owner_apex_rt),
            )
            for member_id in member_subject_ids
        )
    )
    canonical_injection_order = _canonical_injection_order(
        owners=tuple(owner_by_member.values()),
        injection_order=injection_order,
    )
    edges = tuple(classified_edges)
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=edges,
        budget=budget,
    )
    exclusions = _canonical_exclusions(edges)
    eligible_member_pairs = _canonical_eligible_pairs(edges)
    payload = _trajectory_input_payload(
        processing_request_id=resolved_request_id,
        anchor_ids=canonical_anchor_ids,
        member_subject_ids=member_subject_ids,
        member_apexes=member_apexes,
        injection_order=canonical_injection_order,
        evidence_profile=evidence_profile,
        exclusions=exclusions,
        eligible_member_pairs=eligible_member_pairs,
    )
    trajectory_input_id = typed_identity_digest(
        _TRAJECTORY_INPUT_PREFIX,
        canonical_identity_basis_bytes(payload),
    )
    return ModeTrackTrajectoryInput(
        trajectory_input_id=trajectory_input_id,
        processing_request_id=resolved_request_id,
        anchor_ids=canonical_anchor_ids,
        member_subject_ids=member_subject_ids,
        member_apexes=member_apexes,
        injection_order=canonical_injection_order,
        evidence_profile=evidence_profile,
        exclusions=exclusions,
        eligible_member_pairs=eligible_member_pairs,
        neighborhood_status=neighborhood.status,
        _mint_token=_TRAJECTORY_INPUT_MINT_TOKEN,
    )


def _mint_rejected_mode_track_refinement(
    *,
    trajectory_input_id: str,
    reason_codes: Sequence[str],
) -> RejectedModeTrackRefinement:
    return RejectedModeTrackRefinement(
        variant="rejected_refinement",
        trajectory_input_id=trajectory_input_id,
        reason_codes=tuple(sorted(set(reason_codes))),
        _mint_token=_PROPOSAL_MINT_TOKEN,
    )


def _mint_insufficient_mode_track_training(
    *,
    trajectory_input_id: str,
    reason_codes: Sequence[str],
) -> InsufficientModeTrackTraining:
    return InsufficientModeTrackTraining(
        variant="insufficient_training",
        trajectory_input_id=trajectory_input_id,
        reason_codes=tuple(sorted(set(reason_codes))),
        _mint_token=_PROPOSAL_MINT_TOKEN,
    )


def _mint_unassessable_mode_track_refinement(
    *,
    trajectory_input_id: str,
) -> UnassessableModeTrackRefinement:
    return UnassessableModeTrackRefinement(
        variant="unassessable",
        trajectory_input_id=trajectory_input_id,
        reason="neighborhood_too_large",
        _mint_token=_PROPOSAL_MINT_TOKEN,
    )


def _mint_accepted_mode_track_refinement(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    successor_member_subject_ids: Sequence[Sequence[str]],
) -> AcceptedModeTrackRefinement:
    if not isinstance(trajectory_input, ModeTrackTrajectoryInput):
        raise ModeTrackRefinementContractError("trajectory input must be typed")
    proposed = tuple(tuple(sorted(group)) for group in successor_member_subject_ids)
    if any(len(group) != len(set(group)) for group in proposed):
        raise ModeTrackRefinementContractError(
            "proposal duplicates a member in a group"
        )
    if tuple(sorted(proposed)) != proposed:
        proposed = tuple(sorted(proposed))
    flattened = tuple(member_id for group in proposed for member_id in group)
    if (
        tuple(sorted(flattened)) != trajectory_input.member_subject_ids
        or len(flattened) != len(set(flattened))
    ):
        raise ModeTrackRefinementContractError(
            "proposal is not a complete trajectory-input partition"
        )
    return AcceptedModeTrackRefinement(
        variant="accepted_refinement",
        trajectory_input_id=trajectory_input.trajectory_input_id,
        successor_member_subject_ids=proposed,
        _mint_token=_PROPOSAL_MINT_TOKEN,
    )


def _mint_untrained_mode_track_refinement_evidence(
    *,
    anchors: Sequence[ModeTrackSupportAnchor],
    evidence_profile: ModeTrackEvidenceProfile,
    reason: Literal["untrained", "assignment_unavailable"],
) -> UntrainedModeTrackRefinementEvidence:
    """Mint owner-local evidence for an unavailable assignment pass."""

    if any(type(anchor) is not ModeTrackSupportAnchor for anchor in anchors):
        raise ModeTrackRefinementContractError(
            "untrained evidence requires owner-typed anchors"
        )
    canonical_anchors = tuple(sorted(anchors, key=lambda item: item.anchor_id))
    return UntrainedModeTrackRefinementEvidence(
        variant=reason,
        anchors=canonical_anchors,
        evidence_profile=evidence_profile,
        reason=reason,
        _mint_token=_EVIDENCE_MINT_TOKEN,
    )


def apply_accepted_mode_track_refinement(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    proposal: AcceptedModeTrackRefinement,
    application_context: CrossSampleCoupledProposalContext,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    if (
        not isinstance(trajectory_input, ModeTrackTrajectoryInput)
        or type(proposal) is not AcceptedModeTrackRefinement
    ):
        raise ModeTrackRefinementContractError(
            "ModeTrack application requires exact typed input and proposal"
        )
    if proposal.trajectory_input_id != trajectory_input.trajectory_input_id:
        raise ModeTrackRefinementContractError(
            "ModeTrack proposal is bound to another snapshot"
        )
    if not isinstance(application_context, CrossSampleCoupledProposalContext):
        raise ModeTrackRefinementContractError(
            "ModeTrack application requires CrossSample-owned context"
        )
    context_member_ids = tuple(
        sorted(
            member.member_subject_id
            for group in application_context.predecessors
            for member in group.member_subjects
        )
    )
    if context_member_ids != trajectory_input.member_subject_ids:
        raise ModeTrackRefinementContractError(
            "CrossSample application context crossed the trajectory snapshot"
        )
    context_members = tuple(
        member
        for group in application_context.predecessors
        for member in group.member_subjects
    )
    context_neighborhood = build_bounded_connected_neighborhood(
        member_subjects=context_members,
        edges=application_context.classified_edges,
        budget=application_context.budget,
    )
    if (
        _canonical_exclusions(application_context.classified_edges)
        != trajectory_input.exclusions
        or _canonical_eligible_pairs(application_context.classified_edges)
        != trajectory_input.eligible_member_pairs
        or context_neighborhood.status != trajectory_input.neighborhood_status
    ):
        raise ModeTrackRefinementContractError(
            "CrossSample application context crossed trajectory exclusions or budget"
        )
    member_by_id = {
        member.member_subject_id: member
        for group in application_context.predecessors
        for member in group.member_subjects
    }
    successor_member_sets = tuple(
        tuple(member_by_id[member_id] for member_id in group)
        for group in proposal.successor_member_subject_ids
    )
    return apply_coupled_successor_proposal(
        predecessors=application_context.predecessors,
        successor_member_sets=successor_member_sets,
        edges=application_context.classified_edges,
        budget=application_context.budget,
        population=population,
    )


def _canonical_injection_order(
    *,
    owners: tuple[ModeTrackTrajectoryOwner, ...],
    injection_order: Mapping[str, int],
) -> tuple[tuple[str, int], ...]:
    sample_stems = {owner.sample_stem for owner in owners}
    if set(injection_order) != sample_stems:
        raise ModeTrackRefinementContractError(
            "injection order does not close the trajectory sample population"
        )
    result = []
    for sample_stem, order in injection_order.items():
        if isinstance(order, bool) or not isinstance(order, int) or order < 1:
            raise ModeTrackRefinementContractError(
                "injection order must contain positive integers"
            )
        result.append((sample_stem, order))
    orders = tuple(order for _, order in result)
    if len(set(orders)) != len(orders):
        raise ModeTrackRefinementContractError("injection order is duplicated")
    return tuple(sorted(result))


def _trajectory_input_payload(
    *,
    processing_request_id: str,
    anchor_ids: tuple[str, ...],
    member_subject_ids: tuple[str, ...],
    member_apexes: tuple[tuple[str, float], ...],
    injection_order: tuple[tuple[str, int], ...],
    evidence_profile: ModeTrackEvidenceProfile,
    exclusions: tuple[tuple[str, str, str], ...],
    eligible_member_pairs: tuple[tuple[str, str], ...],
) -> dict[str, object]:
    return {
        "schema_version": "mode_track_trajectory_input_sparse_v1",
        "initial_pair_policy": INITIAL_PAIR_POLICY,
        "unlisted_pairs": "excluded",
        "eligible_member_pairs": [list(pair) for pair in eligible_member_pairs],
        "processing_request_id": processing_request_id,
        "anchors": list(anchor_ids),
        "members": list(member_subject_ids),
        "apexes": [[member_id, apex] for member_id, apex in member_apexes],
        "injection_order": [
            [sample_stem, order] for sample_stem, order in injection_order
        ],
        "evidence_profile": {
            "trajectory_policy_version": (evidence_profile.trajectory_policy_version),
            "band_policy_version": evidence_profile.band_policy_version,
            "prediction_half_width_min": (evidence_profile.prediction_half_width_min),
            "max_interpolation_gap": evidence_profile.max_interpolation_gap,
            "max_extrapolation_gap": evidence_profile.max_extrapolation_gap,
        },
        "exclusions": [list(item) for item in exclusions],
    }


def _canonical_eligible_pairs(
    edges: tuple[CrossSampleClassifiedEdge, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        sorted(
            (min(edge.left_member_subject_id, edge.right_member_subject_id),
             max(edge.left_member_subject_id, edge.right_member_subject_id))
            for edge in edges
            if edge.disposition == "eligible"
        )
    )


def _canonical_exclusions(
    edges: tuple[CrossSampleClassifiedEdge, ...],
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            (
                min(edge.left_member_subject_id, edge.right_member_subject_id),
                max(edge.left_member_subject_id, edge.right_member_subject_id),
                edge.disposition,
            )
            for edge in edges
            if edge.disposition != "eligible"
        )
    )


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModeTrackRefinementContractError(f"{field} must be non-empty text")
    return value


def _validate_proposal_common(
    trajectory_input_id: str,
    reason_codes: tuple[str, ...],
) -> None:
    _required_text(trajectory_input_id, field="trajectory_input_id")
    if (
        type(reason_codes) is not tuple
        or not reason_codes
        or tuple(sorted(set(reason_codes))) != reason_codes
    ):
        raise ModeTrackRefinementContractError(
            "proposal reason codes must be non-empty canonical text"
        )
    for reason_code in reason_codes:
        _required_text(reason_code, field="reason_code")


def _finite_float(value: object) -> float:
    if not isinstance(value, float) or not math.isfinite(value):
        raise ModeTrackRefinementContractError("trajectory apex must be a finite float")
    return value


def _required_owner_member(
    owner: ModeTrackTrajectoryOwner,
) -> CrossSampleGroupMemberSubject:
    member = owner.cross_sample_member_subject
    if member is None:
        raise ModeTrackRefinementContractError(
            "trajectory owner is missing its canonical member"
        )
    return member


__all__ = (
    "AcceptedModeTrackRefinement",
    "InsufficientModeTrackTraining",
    "ModeTrackEvidenceProfile",
    "ModeTrackRefinementContractError",
    "ModeTrackRefinementProposal",
    "ModeTrackTrajectoryInput",
    "RejectedModeTrackRefinement",
    "UnassessableModeTrackRefinement",
    "apply_accepted_mode_track_refinement",
    "mint_mode_track_trajectory_input",
)
