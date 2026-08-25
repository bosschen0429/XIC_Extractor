"""Immutable request-bound inputs for ModeTrack refinement."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass, field
from typing import Literal, TypeAlias

from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleCoupledProposalContext,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSampleSuccessorApplication,
    apply_coupled_successor_proposal,
    build_bounded_connected_neighborhood,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)

from .assignment import ModeTrackSupportAnchor

_TRAJECTORY_INPUT_PREFIX = "xic.mode-track-trajectory-input.v1:"
_TRAJECTORY_INPUT_MINT_TOKEN = object()


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
                )
            ),
        )
        if self.trajectory_input_id != expected:
            raise ModeTrackRefinementContractError(
                "trajectory input identity mismatch"
            )


@dataclass(frozen=True, slots=True)
class AcceptedModeTrackRefinement:
    variant: Literal["accepted_refinement"]
    trajectory_input_id: str
    successor_member_subject_ids: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
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

    def __post_init__(self) -> None:
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
    owners: Sequence[SampleLocalMS1Owner],
    classified_edges: Sequence[CrossSampleNeighborhoodEdge],
    anchors: Sequence[ModeTrackSupportAnchor],
    injection_order: Mapping[str, int],
    evidence_profile: ModeTrackEvidenceProfile,
    budget: CrossSampleNeighborhoodBudget,
) -> ModeTrackTrajectoryInput:
    predecessor_groups = validate_cross_sample_group_population(predecessors)
    request_ids = {group.processing_request_id for group in predecessor_groups}
    if len(request_ids) != 1:
        raise ModeTrackRefinementContractError(
            "trajectory predecessors crossed processing requests"
        )
    processing_request_id = next(iter(request_ids))
    members = tuple(
        member for group in predecessor_groups for member in group.member_subjects
    )
    member_by_id = {member.member_subject_id: member for member in members}
    owner_by_id: dict[str, SampleLocalMS1Owner] = {}
    owner_by_member: dict[str, SampleLocalMS1Owner] = {}
    for owner in owners:
        if not isinstance(owner, SampleLocalMS1Owner):
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
            anchor.processing_request_id != processing_request_id
            or anchor_owner is None
            or anchor.canonical_sample_identity_id
            != _required_owner_member(anchor_owner).canonical_sample_identity_id
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
    _validate_complete_classification(
        member_subject_ids=member_subject_ids,
        edges=edges,
    )
    neighborhood = build_bounded_connected_neighborhood(
        member_subjects=members,
        edges=edges,
        budget=budget,
    )
    exclusions = _canonical_exclusions(edges)
    payload = _trajectory_input_payload(
        processing_request_id=processing_request_id,
        anchor_ids=canonical_anchor_ids,
        member_subject_ids=member_subject_ids,
        member_apexes=member_apexes,
        injection_order=canonical_injection_order,
        evidence_profile=evidence_profile,
        exclusions=exclusions,
    )
    trajectory_input_id = typed_identity_digest(
        _TRAJECTORY_INPUT_PREFIX,
        canonical_identity_basis_bytes(payload),
    )
    return ModeTrackTrajectoryInput(
        trajectory_input_id=trajectory_input_id,
        processing_request_id=processing_request_id,
        anchor_ids=canonical_anchor_ids,
        member_subject_ids=member_subject_ids,
        member_apexes=member_apexes,
        injection_order=canonical_injection_order,
        evidence_profile=evidence_profile,
        exclusions=exclusions,
        neighborhood_status=neighborhood.status,
        _mint_token=_TRAJECTORY_INPUT_MINT_TOKEN,
    )


def propose_mode_track_successor_partition(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    successor_member_subject_ids: Sequence[Sequence[str]],
) -> ModeTrackRefinementProposal:
    if not isinstance(trajectory_input, ModeTrackTrajectoryInput):
        raise ModeTrackRefinementContractError("trajectory input must be typed")
    if trajectory_input.neighborhood_status == "unassessable":
        return UnassessableModeTrackRefinement(
            variant="unassessable",
            trajectory_input_id=trajectory_input.trajectory_input_id,
            reason="neighborhood_too_large",
        )
    if not trajectory_input.anchor_ids:
        return InsufficientModeTrackTraining(
            variant="insufficient_training",
            trajectory_input_id=trajectory_input.trajectory_input_id,
            reason_codes=("no_direct_support_anchor",),
        )
    return mint_accepted_mode_track_refinement(
        trajectory_input=trajectory_input,
        successor_member_subject_ids=successor_member_subject_ids,
    )


def mint_accepted_mode_track_refinement(
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
    )


def apply_accepted_mode_track_refinement(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    proposal: AcceptedModeTrackRefinement,
    application_context: CrossSampleCoupledProposalContext,
    population: Sequence[CrossSampleGroupIdentity],
) -> CrossSampleSuccessorApplication:
    if not isinstance(trajectory_input, ModeTrackTrajectoryInput) or type(
        proposal
    ) is not AcceptedModeTrackRefinement:
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
    owners: tuple[SampleLocalMS1Owner, ...],
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
) -> dict[str, object]:
    return {
        "schema_version": "mode_track_trajectory_input_v1",
        "processing_request_id": processing_request_id,
        "anchors": list(anchor_ids),
        "members": list(member_subject_ids),
        "apexes": [[member_id, apex] for member_id, apex in member_apexes],
        "injection_order": [
            [sample_stem, order] for sample_stem, order in injection_order
        ],
        "evidence_profile": {
            "trajectory_policy_version": (
                evidence_profile.trajectory_policy_version
            ),
            "band_policy_version": evidence_profile.band_policy_version,
            "prediction_half_width_min": (
                evidence_profile.prediction_half_width_min
            ),
            "max_interpolation_gap": evidence_profile.max_interpolation_gap,
            "max_extrapolation_gap": evidence_profile.max_extrapolation_gap,
        },
        "exclusions": [list(item) for item in exclusions],
    }


def _validate_complete_classification(
    *,
    member_subject_ids: tuple[str, ...],
    edges: tuple[CrossSampleNeighborhoodEdge, ...],
) -> None:
    expected_pairs = {
        (left_id, right_id)
        for left_index, left_id in enumerate(member_subject_ids)
        for right_id in member_subject_ids[left_index + 1 :]
    }
    observed_pairs = {
        tuple(
            sorted(
                (edge.left_member_subject_id, edge.right_member_subject_id)
            )
        )
        for edge in edges
    }
    if len(observed_pairs) != len(edges) or observed_pairs != expected_pairs:
        raise ModeTrackRefinementContractError(
            "trajectory requires one exact classification for every member pair"
        )


def _canonical_exclusions(
    edges: tuple[CrossSampleNeighborhoodEdge, ...],
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
    owner: SampleLocalMS1Owner,
) -> CrossSampleGroupMemberSubject:
    member = owner.cross_sample_member_subject
    if member is None:
        raise ModeTrackRefinementContractError(
            "trajectory owner is missing its canonical member"
        )
    return member
