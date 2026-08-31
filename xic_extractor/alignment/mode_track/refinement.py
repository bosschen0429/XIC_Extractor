"""Finite CID-NL adapter from retained ModeTrack fitting to CrossSample."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal, TypeAlias, cast

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleCoupledProposalContext,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleRepairedObservation,
    build_bounded_connected_neighborhood,
    classify_cross_sample_initial_neighborhood_edge,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackAssignment,
    ModeTrackAssignmentBatch,
    ModeTrackContractError,
    ModeTrackFitOnceSource,
    ModeTrackModelSet,
    ModeTrackRepairAssignmentObservation,
    ModeTrackTrajectoryPolicy,
    _mint_mode_track_repair_assignment_observation,
    _ModeTrackAssignmentUnavailableError,
    assign_mode_tracks,
    validate_mode_track_assignment_batch_for_consumption,
    validate_mode_track_fit_once_source_for_consumption,
)
from xic_extractor.alignment.mode_track.family_context import FamilyContextRef
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

from .contracts import (
    AcceptedModeTrackRefinement,
    AssignedModeTrackRefinementEvidence,
    InsufficientModeTrackTraining,
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
    ModeTrackRefinementEvidence,
    ModeTrackRefinementProposal,
    ModeTrackTrajectoryInput,
    RejectedModeTrackRefinement,
    UnassessableModeTrackRefinement,
    UntrainedModeTrackRefinementEvidence,
    _mint_accepted_mode_track_refinement,
    _mint_insufficient_mode_track_training,
    _mint_rejected_mode_track_refinement,
    _mint_unassessable_mode_track_refinement,
    _mint_untrained_mode_track_refinement_evidence,
    mint_mode_track_trajectory_input,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.backfill.observation_repair import (
        ObservationRepairReceipt,
    )
    from xic_extractor.alignment.cross_sample_reconciliation import (
        CrossSamplePostRepairModeTrackInput,
    )

_FINAL_ATTEMPT_MINT_TOKEN = object()
ModeTrackRefinementOwner: TypeAlias = (
    SampleLocalMS1Owner | CrossSampleRepairedObservation
)


class ModeTrackFinalAttemptContractError(ValueError):
    """Raised when one post-repair final attempt cannot close exactly."""


@dataclass(frozen=True, slots=True)
class ModeTrackFinalAttemptBinding:
    """Replay binding shared by every closed final-attempt result."""

    final_attempt_id: str
    final_result_id: str
    processing_request_id: str
    processing_attempt_id: str
    fit_once_source_id: str
    family_context_id: str
    model_set_id: str
    repair_receipt_id: str
    repair_epoch: int
    cross_sample_post_repair_input_id: str
    cross_sample_repair_result_id: str
    post_repair_population_id: str
    pre_trajectory_input_id: str
    post_trajectory_input_id: str
    owner_census_digest: str
    member_census_digest: str
    assignment_batch_ids: tuple[str, ...]
    result_discriminant: str
    proposal_id: str
    basis_bytes: bytes = field(repr=False)
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FINAL_ATTEMPT_MINT_TOKEN:
            raise ModeTrackFinalAttemptContractError(
                "final-attempt binding must be minted by ModeTrack"
            )


@dataclass(frozen=True, slots=True)
class AssignedModeTrackFinalAttempt:
    variant: ClassVar[Literal["assigned"]] = "assigned"
    binding: ModeTrackFinalAttemptBinding
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    proposal: AcceptedModeTrackRefinement
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _validate_final_attempt_variant(self, _mint_token, "assigned")


@dataclass(frozen=True, slots=True)
class UntrainedModeTrackFinalAttempt:
    variant: ClassVar[Literal["untrained"]] = "untrained"
    binding: ModeTrackFinalAttemptBinding
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    proposal: InsufficientModeTrackTraining
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _validate_final_attempt_variant(self, _mint_token, "untrained")


@dataclass(frozen=True, slots=True)
class AssignmentUnavailableModeTrackFinalAttempt:
    variant: ClassVar[Literal["assignment_unavailable"]] = "assignment_unavailable"
    binding: ModeTrackFinalAttemptBinding
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    proposal: InsufficientModeTrackTraining
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _validate_final_attempt_variant(
            self,
            _mint_token,
            "assignment_unavailable",
        )


@dataclass(frozen=True, slots=True)
class RejectedModeTrackFinalAttempt:
    variant: ClassVar[Literal["rejected"]] = "rejected"
    binding: ModeTrackFinalAttemptBinding
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    proposal: ModeTrackRefinementProposal
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _validate_final_attempt_variant(self, _mint_token, "rejected")


@dataclass(frozen=True, slots=True)
class UnassessableModeTrackFinalAttempt:
    variant: ClassVar[Literal["unassessable"]] = "unassessable"
    binding: ModeTrackFinalAttemptBinding
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    proposal: UnassessableModeTrackRefinement
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        _validate_final_attempt_variant(self, _mint_token, "unassessable")


ModeTrackFinalAttemptResult: TypeAlias = (
    AssignedModeTrackFinalAttempt
    | UntrainedModeTrackFinalAttempt
    | AssignmentUnavailableModeTrackFinalAttempt
    | RejectedModeTrackFinalAttempt
    | UnassessableModeTrackFinalAttempt
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
    model_group_ids = {
        track.group_hypothesis_id for track in evidence.model_set.tracks
    }
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


def _validate_final_attempt_variant(
    result: object,
    mint_token: object,
    discriminant: str,
) -> None:
    if mint_token is not _FINAL_ATTEMPT_MINT_TOKEN:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt result must be minted by ModeTrack"
        )
    binding = getattr(result, "binding", None)
    if type(binding) is not ModeTrackFinalAttemptBinding:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt result requires an exact binding"
        )
    if binding.result_discriminant != discriminant:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt result crossed its discriminant"
        )
    ModeTrackFinalAttemptBinding.__post_init__(
        binding,
        _FINAL_ATTEMPT_MINT_TOKEN,
    )
    assignment_batches = getattr(result, "assignment_batches", None)
    proposal = getattr(result, "proposal", None)
    if type(assignment_batches) is not tuple:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt batches must be an immutable tuple"
        )
    assignment_batch_ids = tuple(
        batch.assignment_batch_id for batch in assignment_batches
    )
    if assignment_batch_ids != binding.assignment_batch_ids:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt batches crossed their replay binding"
        )
    if type(proposal) not in {
        AcceptedModeTrackRefinement,
        RejectedModeTrackRefinement,
        InsufficientModeTrackTraining,
        UnassessableModeTrackRefinement,
    }:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt proposal must be owner-minted"
        )
    proposal = cast(ModeTrackRefinementProposal, proposal)
    if _proposal_identity(proposal) != binding.proposal_id:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt proposal crossed its replay binding"
        )
    if discriminant == "assigned":
        valid_content = (
            bool(assignment_batches)
            and type(proposal) is AcceptedModeTrackRefinement
        )
    elif discriminant in {"untrained", "assignment_unavailable"}:
        valid_content = (
            not assignment_batches
            and type(proposal) is InsufficientModeTrackTraining
            and proposal.reason_codes == (discriminant,)
        )
    elif discriminant == "rejected":
        valid_content = (
            bool(assignment_batches)
            and type(proposal) is RejectedModeTrackRefinement
        )
    else:
        valid_content = type(proposal) is UnassessableModeTrackRefinement
    if not valid_content:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt variant content crossed its closed meaning"
        )
    expected_result_id = typed_identity_digest(
        "mode-track-final-result.v1:",
        canonical_identity_basis_bytes(
            {
                "schema_version": "mode_track_final_result_v1",
                "final_attempt_id": binding.final_attempt_id,
                "assignment_batch_ids": list(binding.assignment_batch_ids),
                "result_discriminant": discriminant,
                "proposal_id": binding.proposal_id,
            }
        ),
    )
    if binding.final_result_id != expected_result_id:
        raise ModeTrackFinalAttemptContractError(
            "final-attempt result identity mismatches its bound content"
        )


def run_post_repair_mode_track_final_attempt(
    *,
    source: ModeTrackFitOnceSource,
    repair_receipt: ObservationRepairReceipt,
    post_repair: CrossSamplePostRepairModeTrackInput,
) -> ModeTrackFinalAttemptResult:
    """Run one owner-derived attempt for one exact changed post-repair state."""

    from xic_extractor.alignment.backfill.observation_repair import (
        _RECEIPT_MINT_TOKEN,
        ObservationRepairReceipt,
    )
    from xic_extractor.alignment.cross_sample_reconciliation import (
        CrossSamplePostRepairModeTrackInput,
        _validate_post_repair_mode_track_input,
    )

    try:
        validate_mode_track_fit_once_source_for_consumption(source)
    except (ModeTrackContractError, TypeError, ValueError) as exc:
        raise ModeTrackFinalAttemptContractError(str(exc)) from exc
    if type(repair_receipt) is not ObservationRepairReceipt:
        raise ModeTrackFinalAttemptContractError(
            "final attempt requires an exact ObservationRepair receipt"
        )
    if type(post_repair) is not CrossSamplePostRepairModeTrackInput:
        raise ModeTrackFinalAttemptContractError(
            "final attempt requires an exact CrossSample post-repair input"
        )
    try:
        repair_receipt.__post_init__(_RECEIPT_MINT_TOKEN)
        _validate_post_repair_mode_track_input(post_repair)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ModeTrackFinalAttemptContractError(str(exc)) from exc
    if (
        source.processing_request_id != post_repair.processing_request_id
        or source.processing_attempt_id != post_repair.processing_attempt_id
        or source.processing_owners is not post_repair.processing_owners
        or repair_receipt.receipt_id != post_repair.repair_receipt_id
        or repair_receipt.repair_epoch != post_repair.repair_epoch
        or not post_repair.trajectory_changed
    ):
        raise ModeTrackFinalAttemptContractError(
            "final attempt crossed request, attempt, receipt, or trajectory"
        )
    source_policy = source.model_set.policy
    if (
        post_repair.post_repair_trajectory.evidence_profile
        != ModeTrackEvidenceProfile(
            source_policy.trajectory_policy_version,
            source_policy.band_policy_version,
            source_policy.prediction_half_width_min,
            source_policy.max_interpolation_gap,
            source_policy.max_extrapolation_gap,
        )
    ):
        raise ModeTrackFinalAttemptContractError(
            "post-repair trajectory crossed the fit-once model policy"
        )
    trajectory_owners: tuple[
        SampleLocalMS1Owner | CrossSampleRepairedObservation, ...
    ] = post_repair.post_repair_owners
    if post_repair.repaired_observation is not None:
        trajectory_owners = (*trajectory_owners, post_repair.repaired_observation)
    repair_evidence = repair_receipt.evidence
    if repair_evidence is None:
        raise ModeTrackFinalAttemptContractError(
            "changed post-repair input requires exact repaired physical evidence"
        )
    repaired_observation = post_repair.repaired_observation
    repaired_owners = tuple(
        owner
        for owner in post_repair.post_repair_owners
        if _required_owner_member(owner).member_subject_id
        == post_repair.repaired_member_subject_id
    )
    if repaired_observation is None:
        if len(repaired_owners) != 1:
            raise ModeTrackFinalAttemptContractError(
                "post-repair owner census does not contain exactly one repaired member"
            )
        repaired_owner = repaired_owners[0]
        repaired_member = _required_owner_member(repaired_owner)
        if (
            repair_evidence.sample_stem != repaired_owner.sample_stem
            or repair_evidence.rt_apex_min != repaired_owner.owner_apex_rt
            or repair_evidence.member_subject_id
            != post_repair.repaired_member_subject_id
        ):
            raise ModeTrackFinalAttemptContractError(
                "repair evidence crossed the exact repaired owner"
            )
    else:
        if repaired_owners:
            raise ModeTrackFinalAttemptContractError(
                "nonmember repair duplicated an existing owner"
            )
        repaired_member = repaired_observation.member_subject
    repaired_sources = tuple(
        sample_source
        for sample_source in source.sample_sources
        if sample_source.canonical_sample_identity_id
        == repaired_member.canonical_sample_identity_id
    )
    if len(repaired_sources) != 1:
        raise ModeTrackFinalAttemptContractError(
            "fit-once source does not contain exactly one repaired sample"
        )
    repaired_source = repaired_sources[0]
    source_observation_id = repair_evidence.physical_answer_id
    if repaired_observation is not None:
        if (
            repaired_observation.repair_receipt_id != repair_receipt.receipt_id
            or repaired_observation.member_subject.member_subject_id
            != post_repair.repaired_member_subject_id
            or repaired_observation.member_subject.subject_id
            != repair_evidence.fact_content_id
            or repaired_observation.rt_apex_min != repair_evidence.rt_apex_min
        ):
            raise ModeTrackFinalAttemptContractError(
                "CrossSample repaired observation crossed Backfill evidence"
            )
        source_observation_id = repaired_observation.observation_id
    repair_assignment_observation = _mint_mode_track_repair_assignment_observation(
        processing_owners=source.processing_owners,
        sample=repaired_source.sample,
        repaired_member_subject_id=post_repair.repaired_member_subject_id,
        physical_observation_id=repair_evidence.fact_content_id,
        apex_rt_min=repair_evidence.rt_apex_min,
        repair_receipt_id=repair_receipt.receipt_id,
        source_observation_id=source_observation_id,
    )
    if not any(track.anchors for track in source.model_set.tracks):
        evidence = _mint_untrained_evidence_for_final_attempt(
            trajectory_input=post_repair.post_repair_trajectory,
            reason="untrained",
        )
        proposal = _propose_mode_track_refinement(
            trajectory_input=post_repair.post_repair_trajectory,
            owners=trajectory_owners,
            evidence=evidence,
        )
        return _mint_final_attempt_result(
            source=source,
            repair_receipt=repair_receipt,
            post_repair=post_repair,
            assignment_batches=(),
            proposal=proposal,
            discriminant="untrained",
        )

    required_sample_ids = {
        member.canonical_sample_identity_id
        for group in post_repair.post_repair_population
        for member in group.member_subjects
    }
    assignment_sources = tuple(
        sample_source
        for sample_source in source.sample_sources
        if sample_source.canonical_sample_identity_id in required_sample_ids
    )
    if {
        sample_source.canonical_sample_identity_id
        for sample_source in assignment_sources
    } != required_sample_ids:
        raise ModeTrackFinalAttemptContractError(
            "fit-once source does not close the post-repair population samples"
        )

    try:
        assignment_batches = tuple(
            sorted(
                (
                    assign_mode_tracks(
                        processing_owners=source.processing_owners,
                        sample=sample_source.sample,
                        model_set=source.model_set,
                        candidates=tuple(
                            candidate
                            for candidate in sample_source.candidates
                            if (
                                sample_source is not repaired_source
                                or candidate.peak_observation_content_id
                                not in repaired_member.physical_observation_ids
                            )
                        ),
                        repair_observations=(
                            (repair_assignment_observation,)
                            if sample_source is repaired_source
                            else ()
                        ),
                        trajectory_assessment=sample_source.trajectory_assessment,
                    )
                    for sample_source in assignment_sources
                ),
                key=lambda batch: batch.assignment_batch_id,
            )
        )
    except _ModeTrackAssignmentUnavailableError:
        evidence = _mint_untrained_evidence_for_final_attempt(
            trajectory_input=post_repair.post_repair_trajectory,
            reason="assignment_unavailable",
        )
        proposal = _propose_mode_track_refinement(
            trajectory_input=post_repair.post_repair_trajectory,
            owners=trajectory_owners,
            evidence=evidence,
        )
        return _mint_final_attempt_result(
            source=source,
            repair_receipt=repair_receipt,
            post_repair=post_repair,
            assignment_batches=(),
            proposal=proposal,
            discriminant="assignment_unavailable",
        )
    except (ModeTrackContractError, TypeError, ValueError) as exc:
        raise ModeTrackFinalAttemptContractError(
            f"final assignment could not close: {exc}"
        ) from exc
    assigned_evidence = AssignedModeTrackRefinementEvidence(
        variant="assigned",
        processing_owners=source.processing_owners,
        model_set=source.model_set,
        assignment_batches=assignment_batches,
    )
    try:
        proposal = _propose_mode_track_refinement(
            trajectory_input=post_repair.post_repair_trajectory,
            owners=trajectory_owners,
            evidence=assigned_evidence,
        )
    except ModeTrackRefinementContractError as exc:
        raise ModeTrackFinalAttemptContractError(str(exc)) from exc
    discriminant: Literal["assigned", "rejected", "unassessable"]
    if type(proposal) is AcceptedModeTrackRefinement:
        discriminant = "assigned"
    elif type(proposal) is UnassessableModeTrackRefinement:
        discriminant = "unassessable"
    else:
        discriminant = "rejected"
    return _mint_final_attempt_result(
        source=source,
        repair_receipt=repair_receipt,
        post_repair=post_repair,
        assignment_batches=assignment_batches,
        proposal=proposal,
        discriminant=discriminant,
    )


def _mint_untrained_evidence_for_final_attempt(
    *,
    trajectory_input: ModeTrackTrajectoryInput,
    reason: Literal["untrained", "assignment_unavailable"],
) -> UntrainedModeTrackRefinementEvidence:
    return _mint_untrained_mode_track_refinement_evidence(
        anchors=(),
        evidence_profile=trajectory_input.evidence_profile,
        reason=reason,
    )


def _proposal_identity(proposal: ModeTrackRefinementProposal) -> str:
    payload: dict[str, object] = {
        "variant": proposal.variant,
        "trajectory_input_id": proposal.trajectory_input_id,
    }
    if type(proposal) is AcceptedModeTrackRefinement:
        payload["successor_member_subject_ids"] = [
            list(group) for group in proposal.successor_member_subject_ids
        ]
    elif type(proposal) is RejectedModeTrackRefinement:
        payload["reason_codes"] = list(proposal.reason_codes)
    elif type(proposal) is InsufficientModeTrackTraining:
        payload["reason_codes"] = list(proposal.reason_codes)
    elif type(proposal) is UnassessableModeTrackRefinement:
        payload["reason"] = proposal.reason
    else:
        raise ModeTrackFinalAttemptContractError(
            "cannot identify an unknown refinement proposal"
        )
    return typed_identity_digest(
        "mode-track-refinement-proposal.v1:",
        canonical_identity_basis_bytes(payload),
    )


def _mint_final_attempt_result(
    *,
    source: ModeTrackFitOnceSource,
    repair_receipt: ObservationRepairReceipt,
    post_repair: CrossSamplePostRepairModeTrackInput,
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...],
    proposal: ModeTrackRefinementProposal,
    discriminant: Literal[
        "assigned",
        "untrained",
        "assignment_unavailable",
        "rejected",
        "unassessable",
    ],
) -> ModeTrackFinalAttemptResult:
    assignment_batch_ids = tuple(
        batch.assignment_batch_id for batch in assignment_batches
    )
    owner_ids = tuple(owner.owner_id for owner in post_repair.post_repair_owners)
    member_ids = post_repair.post_repair_trajectory.member_subject_ids
    owner_census_digest = typed_identity_digest(
        "mode-track-final-owner-census.v1:",
        canonical_identity_basis_bytes({"owner_ids": list(owner_ids)}),
    )
    member_census_digest = typed_identity_digest(
        "mode-track-final-member-census.v1:",
        canonical_identity_basis_bytes({"member_ids": list(member_ids)}),
    )
    proposal_id = _proposal_identity(proposal)
    attempt_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "mode_track_final_attempt_v1",
            "processing_request_id": source.processing_request_id,
            "processing_attempt_id": source.processing_attempt_id,
            "fit_once_source_id": source.fit_once_source_id,
            "family_context_id": source.family_context_id,
            "model_set_id": source.model_set_id,
            "repair_receipt_id": repair_receipt.receipt_id,
            "repair_epoch": repair_receipt.repair_epoch,
            "cross_sample_post_repair_input_id": post_repair.post_repair_input_id,
            "cross_sample_repair_result_id": post_repair.cross_sample_repair_result_id,
            "post_repair_population_id": post_repair.post_repair_population_id,
            "pre_trajectory_input_id": post_repair.pre_repair_trajectory_input_id,
            "post_trajectory_input_id": (
                post_repair.post_repair_trajectory.trajectory_input_id
            ),
            "owner_census_digest": owner_census_digest,
            "member_census_digest": member_census_digest,
        }
    )
    final_attempt_id = typed_identity_digest(
        "mode-track-final-attempt.v1:",
        attempt_basis,
    )
    result_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "mode_track_final_result_v1",
            "final_attempt_id": final_attempt_id,
            "assignment_batch_ids": list(assignment_batch_ids),
            "result_discriminant": discriminant,
            "proposal_id": proposal_id,
        }
    )
    binding = ModeTrackFinalAttemptBinding(
        final_attempt_id=final_attempt_id,
        final_result_id=typed_identity_digest(
            "mode-track-final-result.v1:",
            result_basis,
        ),
        processing_request_id=source.processing_request_id,
        processing_attempt_id=source.processing_attempt_id,
        fit_once_source_id=source.fit_once_source_id,
        family_context_id=source.family_context_id,
        model_set_id=source.model_set_id,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=repair_receipt.repair_epoch,
        cross_sample_post_repair_input_id=post_repair.post_repair_input_id,
        cross_sample_repair_result_id=post_repair.cross_sample_repair_result_id,
        post_repair_population_id=post_repair.post_repair_population_id,
        pre_trajectory_input_id=post_repair.pre_repair_trajectory_input_id,
        post_trajectory_input_id=(
            post_repair.post_repair_trajectory.trajectory_input_id
        ),
        owner_census_digest=owner_census_digest,
        member_census_digest=member_census_digest,
        assignment_batch_ids=assignment_batch_ids,
        result_discriminant=discriminant,
        proposal_id=proposal_id,
        basis_bytes=result_basis,
        _mint_token=_FINAL_ATTEMPT_MINT_TOKEN,
    )
    result_type = {
        "assigned": AssignedModeTrackFinalAttempt,
        "untrained": UntrainedModeTrackFinalAttempt,
        "assignment_unavailable": AssignmentUnavailableModeTrackFinalAttempt,
        "rejected": RejectedModeTrackFinalAttempt,
        "unassessable": UnassessableModeTrackFinalAttempt,
    }[discriminant]
    return result_type(
        binding=binding,
        assignment_batches=assignment_batches,
        proposal=proposal,
        _mint_token=_FINAL_ATTEMPT_MINT_TOKEN,
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
    family_context: FamilyContextRef
    model_set: ModeTrackModelSet
    assignment_batches: tuple[ModeTrackAssignmentBatch, ...]
    fit_once_source: ModeTrackFitOnceSource
    transactions: tuple[ModeTrackRefinementTransaction, ...]


def bootstrap_mode_track_refinement_from_cid_nl_support(
    *,
    processing_owners: ProcessingOwnerReferences,
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
) -> ModeTrackRefinementRun:
    family_context, model_set, assignment_batches, fit_once_source = (
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
                ),
            )
        )
    return ModeTrackRefinementRun(
        family_context=family_context,
        model_set=model_set,
        assignment_batches=assignment_batches,
        fit_once_source=fit_once_source,
        transactions=tuple(transactions),
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
                and candidate.apex_rt_min
                != owner_by_member_id[member_id].owner_apex_rt
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
    excluded_pairs = {
        frozenset((left, right))
        for left, right, _disposition in trajectory_input.exclusions
    }
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
        for left_index, left in enumerate(member_ids):
            for right in member_ids[left_index + 1 :]:
                if frozenset((left, right)) in excluded_pairs:
                    raise ModeTrackRefinementContractError(
                        "owner-derived proposal crosses a non-eligible member edge"
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
    owner: ModeTrackRefinementOwner,
) -> CrossSampleGroupMemberSubject:
    member = owner.cross_sample_member_subject
    if member is None:
        raise ModeTrackRefinementContractError(
            "ModeTrack owner is missing its canonical member"
        )
    return member
