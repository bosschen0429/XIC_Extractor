"""CrossSample-owned closure for post-MT0 ObservationRepair handoffs.

This module composes sealed CrossSample applications and the proposal-only
ModeTrack API.  Existing-member repair facts can update one trajectory apex. A
membership-neutral repair is instead admitted only by CrossSample's
repair-specific physical compatibility classification and one owner-sealed
atomic group extension.
Neither path mints a selected value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import InitVar, asdict, dataclass, field, replace
from typing import Literal, TypeAlias

from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
    ObservationRepairEvidence,
    ObservationRepairOutcome,
    ObservationRepairReceipt,
    ObservationRepairRepairAuthorization,
    ObservationRepairSubject,
    ObservationRepairTerminalResult,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleClassifiedEdge,
    CrossSampleContractError,
    CrossSampleCoupledProposalContext,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSampleRepairAdmissionApplication,
    CrossSampleRepairAdmissionEdge,
    CrossSampleRepairedObservation,
    CrossSampleSuccessorApplication,
    _apply_repaired_nonmember_admission,
    _mint_cross_sample_repaired_observation,
    apply_coupled_successor_proposal,
    classify_cross_sample_repair_admission_edge,
    validate_cross_sample_group_population,
)
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.mode_track.assignment import ModeTrackSupportAnchor
from xic_extractor.alignment.mode_track.contracts import (
    AcceptedModeTrackRefinement,
    InsufficientModeTrackTraining,
    ModeTrackEvidenceProfile,
    ModeTrackRefinementProposal,
    ModeTrackTrajectoryInput,
    RejectedModeTrackRefinement,
    UnassessableModeTrackRefinement,
    apply_accepted_mode_track_refinement,
    mint_mode_track_trajectory_input,
    propose_mode_track_successor_partition,
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)

_FINAL_RECEIPT_PREFIX = "xic.cross-sample-final-receipt.v1:"
_NONMEMBER_REPAIR_RESULT_PREFIX = "xic.cross-sample-nonmember-repair.v1:"
_POST_REPAIR_MODE_TRACK_INPUT_PREFIX = (
    "xic.cross-sample-post-repair-mode-track-input.v1:"
)
_GROUP_MEMBER_SUBJECT_PREFIX = "xic.group-member-subject.v1:"
_TRAJECTORY_INPUT_PREFIX = "xic.mode-track-trajectory-input.v1:"
_FINAL_RECEIPT_MINT_TOKEN = object()
_NONMEMBER_REPAIR_RESULT_MINT_TOKEN = object()
_POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN = object()

CrossSampleProposalOutcome: TypeAlias = ModeTrackRefinementProposal
CrossSampleRepairDisposition: TypeAlias = Literal[
    "admitted",
    "review_only",
    "excluded",
]
InjectionOrderInput: TypeAlias = Mapping[str, int] | Sequence[tuple[str, int]]


class CrossSampleReconciliationContractError(CrossSampleContractError):
    """Raised when the CrossSample repair closure cannot close exactly."""


@dataclass(frozen=True, slots=True)
class CrossSamplePostMT0Context:
    """Exact post-MT0 CrossSample state needed for one repair reconciliation."""

    processing_request_id: str
    population: tuple[CrossSampleGroupIdentity, ...]
    application: CrossSampleSuccessorApplication
    owners: tuple[SampleLocalMS1Owner, ...]
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    anchors: tuple[ModeTrackSupportAnchor, ...]
    injection_order: tuple[tuple[str, int], ...]
    evidence_profile: ModeTrackEvidenceProfile
    budget: CrossSampleNeighborhoodBudget
    trajectory_input: ModeTrackTrajectoryInput

    def __post_init__(self) -> None:
        _validate_post_mt0_context(self)


@dataclass(frozen=True, slots=True)
class CrossSamplePostRepairModeTrackInput:
    """Replay-bound CrossSample carrier for the separate ModeTrack owner."""

    input_id: str
    repair_receipt_id: str
    application: CrossSampleRepairAdmissionApplication
    repaired_observation: CrossSampleRepairedObservation
    owners: tuple[SampleLocalMS1Owner, ...]
    trajectory_input: ModeTrackTrajectoryInput
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input must be minted by CrossSample"
            )
        if type(self.application) is not CrossSampleRepairAdmissionApplication:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input requires an exact application"
            )
        if self.repair_receipt_id != self.application.repair_receipt_id:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input crossed repair receipt"
            )
        if type(self.repaired_observation) is not CrossSampleRepairedObservation:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input requires an exact repaired observation"
            )
        if type(self.owners) is not tuple or any(
            type(owner) is not SampleLocalMS1Owner for owner in self.owners
        ):
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input requires exact identity owners"
            )
        canonical_owners = tuple(sorted(self.owners, key=_owner_sort_key))
        if self.owners != canonical_owners:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack identity owners are not canonical"
            )
        repaired_member = self.repaired_observation.member_subject
        if (
            repaired_member.member_subject_id
            != self.application.admitted_member_subject_id
        ):
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input crossed the admitted member"
            )
        if type(self.trajectory_input) is not ModeTrackTrajectoryInput:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack trajectory must be owner-minted"
            )
        successor_member_ids = tuple(
            member.member_subject_id
            for member in self.application.successor.member_subjects
        )
        if (
            tuple(sorted(successor_member_ids))
            != self.trajectory_input.member_subject_ids
            or self.trajectory_input.processing_request_id
            != self.application.successor.processing_request_id
            or self.trajectory_input.anchor_ids
        ):
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack trajectory crossed its successor or anchors"
            )
        basis = _post_repair_mode_track_input_basis(
            repair_receipt_id=self.repair_receipt_id,
            application=self.application,
            repaired_observation=self.repaired_observation,
            owners=self.owners,
            trajectory_input=self.trajectory_input,
        )
        if self.basis_bytes != basis:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input basis bytes are not exact"
            )
        try:
            validate_typed_identity_basis(
                self.input_id,
                prefix=_POST_REPAIR_MODE_TRACK_INPUT_PREFIX,
                basis_bytes=basis,
                field="post-repair ModeTrack input_id",
            )
        except ValueError as exc:
            raise CrossSampleReconciliationContractError(str(exc)) from exc

    @property
    def proposal_authority(self) -> Literal["none"]:
        return "none"


@dataclass(frozen=True, slots=True)
class CrossSampleNonmemberRepairResult:
    """Terminal CrossSample decision for one repaired nonmember candidate."""

    result_id: str
    processing_request_id: str
    repair_terminal: ObservationRepairTerminalResult
    disposition: CrossSampleRepairDisposition
    pairwise_edges: tuple[CrossSampleRepairAdmissionEdge, ...]
    application: CrossSampleRepairAdmissionApplication | None
    final_population: tuple[CrossSampleGroupIdentity, ...]
    final_owners: tuple[SampleLocalMS1Owner, ...]
    pre_trajectory_input_id: str
    post_trajectory_input_id: str
    mode_track_input: CrossSamplePostRepairModeTrackInput | None
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _NONMEMBER_REPAIR_RESULT_MINT_TOKEN:
            raise CrossSampleReconciliationContractError(
                "nonmember repair result must be minted by CrossSample"
            )
        _validate_nonmember_repair_result(self)

    @property
    def repair_receipt(self) -> ObservationRepairReceipt:
        receipt = self.repair_terminal.receipt
        assert receipt is not None
        return receipt

    @property
    def membership_authority(self) -> Literal["cross_sample"]:
        return "cross_sample"

    @property
    def value_authority(self) -> Literal["none"]:
        return "none"

    @property
    def matrix_authority(self) -> Literal["none"]:
        return "none"

    @property
    def activation_authority(self) -> Literal["none"]:
        return "none"

    @property
    def writer_authority(self) -> Literal["none"]:
        return "none"


def build_cross_sample_post_mt0_context(
    *,
    processing_request_id: str,
    population: Sequence[CrossSampleGroupIdentity],
    application: CrossSampleSuccessorApplication,
    owners: Sequence[SampleLocalMS1Owner],
    classified_edges: Sequence[CrossSampleNeighborhoodEdge],
    anchors: Sequence[ModeTrackSupportAnchor],
    injection_order: InjectionOrderInput,
    evidence_profile: ModeTrackEvidenceProfile,
    budget: CrossSampleNeighborhoodBudget,
) -> CrossSamplePostMT0Context:
    """Build and seal the trajectory snapshot for a post-MT0 state."""

    request_id = _request_id(processing_request_id)
    application = _exact_application(application, field="post-MT0 application")
    population_tuple = tuple(
        sorted(population, key=lambda group: group.group_hypothesis_id)
    )
    owners_tuple = tuple(
        sorted(
            owners,
            key=lambda owner: (
                _owner_member_id(owner),
                owner.owner_id,
            ),
        )
    )
    edges_tuple = tuple(sorted(classified_edges, key=_edge_sort_key))
    anchors_tuple = tuple(sorted(anchors, key=lambda anchor: anchor.anchor_id))
    injection_tuple = _canonical_injection_order(injection_order)
    trajectory_input = _mint_context_trajectory(
        request_id=request_id,
        application=application,
        owners=owners_tuple,
        classified_edges=edges_tuple,
        anchors=anchors_tuple,
        injection_order=injection_tuple,
        evidence_profile=evidence_profile,
        budget=budget,
    )
    return CrossSamplePostMT0Context(
        processing_request_id=request_id,
        population=population_tuple,
        application=application,
        owners=owners_tuple,
        classified_edges=edges_tuple,
        anchors=anchors_tuple,
        injection_order=injection_tuple,
        evidence_profile=evidence_profile,
        budget=budget,
        trajectory_input=trajectory_input,
    )


def reconcile_nonmember_observation_repair(
    *,
    context: CrossSamplePostMT0Context,
    repair_terminal: ObservationRepairTerminalResult,
    repair_result: OwnerBackfillSampleResult,
    alignment_config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
) -> CrossSampleNonmemberRepairResult:
    """Classify and atomically close one exact repaired nonmember candidate."""

    _validate_post_mt0_context(context)
    if type(alignment_config) is not AlignmentConfig:
        raise CrossSampleReconciliationContractError(
            "nonmember repair admission requires an exact AlignmentConfig"
        )
    candidate, receipt, evidence = _validate_candidate_terminal(
        repair_terminal,
        context=context,
    )
    _repair_observation_for_receipt(
        repair_result,
        candidate=candidate,
        receipt=receipt,
        evidence=evidence,
    )
    candidate_member = _mint_repaired_candidate_member(
        candidate=candidate,
        evidence=evidence,
    )
    repaired_observation = _project_repaired_observation(
        candidate=candidate,
        receipt=receipt,
        evidence=evidence,
        candidate_member=candidate_member,
    )
    candidate_edges = tuple(
        sorted(
            (
                _classify_candidate_edge(
                    predecessor_owner=owner,
                    repaired_observation=repaired_observation,
                    config=alignment_config,
                    drift_lookup=drift_lookup,
                )
                for owner in context.owners
            ),
            key=_edge_sort_key,
        )
    )
    disposition = _reduce_repair_admission(candidate_edges)
    application: CrossSampleRepairAdmissionApplication | None = None
    final_population = context.population
    final_owners = context.owners
    post_trajectory_input_id = context.trajectory_input.trajectory_input_id
    mode_track_input: CrossSamplePostRepairModeTrackInput | None = None
    if disposition == "admitted":
        try:
            application = _apply_repaired_nonmember_admission(
                predecessor=candidate.predecessor_group,
                candidate_member=candidate_member,
                population=context.population,
                repair_receipt_id=receipt.receipt_id,
                authorization_id=candidate.authorization_id,
                candidate_id=candidate.candidate_id,
            )
        except (TypeError, ValueError) as exc:
            raise CrossSampleReconciliationContractError(str(exc)) from exc
        final_population = _replace_exact_population_group(
            population=context.population,
            predecessor=application.predecessor,
            successor=application.successor,
        )
        final_owners = context.owners
        combined_edges: tuple[CrossSampleClassifiedEdge, ...] = (
            *context.classified_edges,
            *candidate_edges,
        )
        classified_edges = tuple(sorted(combined_edges, key=_edge_sort_key))
        injection_order = _post_repair_injection_order(
            context=context,
            candidate=candidate,
            evidence=evidence,
        )
        try:
            trajectory_input = mint_mode_track_trajectory_input(
                predecessors=(application.successor,),
                owners=(*final_owners, repaired_observation),
                classified_edges=classified_edges,
                # The new group identity invalidates old anchor bindings;
                # repair success cannot remint method-specific anchor evidence.
                anchors=(),
                injection_order=injection_order,
                evidence_profile=context.evidence_profile,
                budget=context.budget,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise CrossSampleReconciliationContractError(
                f"post-repair trajectory could not close: {exc}"
            ) from exc
        mode_track_input = _mint_post_repair_mode_track_input(
            repair_receipt_id=receipt.receipt_id,
            application=application,
            repaired_observation=repaired_observation,
            owners=final_owners,
            trajectory_input=trajectory_input,
        )
        post_trajectory_input_id = trajectory_input.trajectory_input_id
    return _mint_nonmember_repair_result(
        processing_request_id=context.processing_request_id,
        repair_terminal=repair_terminal,
        disposition=disposition,
        pairwise_edges=candidate_edges,
        application=application,
        final_population=final_population,
        final_owners=final_owners,
        pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_input=mode_track_input,
    )


@dataclass(frozen=True, slots=True)
class CrossSampleReconciledState:
    """CS2 state after repair facts, before any optional MT1 application."""

    processing_request_id: str
    population: tuple[CrossSampleGroupIdentity, ...]
    repair_receipt_id: str
    repair_epoch: int
    repair_outcome: ObservationRepairOutcome
    member_subject_ids: tuple[str, ...]
    trajectory_input_id: str
    trajectory_changed: bool

    def __post_init__(self) -> None:
        request_id = _request_id(self.processing_request_id)
        if type(self.population) is not tuple:
            raise CrossSampleReconciliationContractError(
                "CS2 population must be an immutable tuple"
            )
        population = tuple(
            sorted(self.population, key=lambda group: group.group_hypothesis_id)
        )
        if population != self.population:
            raise CrossSampleReconciliationContractError(
                "CS2 population is not canonical-sorted"
            )
        try:
            validate_cross_sample_group_population(population)
        except (TypeError, ValueError) as exc:
            raise CrossSampleReconciliationContractError(str(exc)) from exc
        if any(group.processing_request_id != request_id for group in population):
            raise CrossSampleReconciliationContractError(
                "CS2 population crossed processing request"
            )
        if (
            not isinstance(self.repair_receipt_id, str)
            or not self.repair_receipt_id.strip()
        ):
            raise CrossSampleReconciliationContractError(
                "repair_receipt_id must be non-empty text"
            )
        if type(self.repair_epoch) is not int or self.repair_epoch != 1:
            raise CrossSampleReconciliationContractError(
                "CS2 repair epoch must be exactly 1"
            )
        if self.repair_outcome not in {"no_repair", "repaired"}:
            raise CrossSampleReconciliationContractError(
                "CS2 repair outcome is not closed"
            )
        expected_members = tuple(
            sorted(
                member.member_subject_id
                for group in population
                for member in group.member_subjects
            )
        )
        if self.member_subject_ids != expected_members:
            raise CrossSampleReconciliationContractError(
                "CS2 member closure is not exact"
            )
        _trajectory_id(self.trajectory_input_id)
        if not isinstance(self.trajectory_changed, bool):
            raise CrossSampleReconciliationContractError(
                "CS2 trajectory_changed must be boolean"
            )


@dataclass(frozen=True, slots=True)
class CrossSampleFinalReceipt:
    """Immutable CSF handoff for the downstream scientific result owner."""

    receipt_id: str
    processing_request_id: str
    post_mt0_application: CrossSampleSuccessorApplication
    repair_receipt: ObservationRepairReceipt
    repair_epoch: int
    cs2_state: CrossSampleReconciledState
    pre_trajectory_input_id: str
    post_trajectory_input_id: str
    mode_track_proposal: CrossSampleProposalOutcome | None
    final_application: CrossSampleSuccessorApplication
    final_population: tuple[CrossSampleGroupIdentity, ...]
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _FINAL_RECEIPT_MINT_TOKEN:
            raise CrossSampleReconciliationContractError(
                "final receipt must be minted by CrossSample"
            )
        request_id = _request_id(self.processing_request_id)
        post_application = _exact_application(
            self.post_mt0_application,
            field="post-MT0 application",
        )
        final_application = _exact_application(
            self.final_application,
            field="final application",
        )
        _validate_repair_receipt(self.repair_receipt)
        if (
            type(self.repair_epoch) is not int
            or self.repair_epoch != 1
            or self.repair_epoch != self.repair_receipt.repair_epoch
        ):
            raise CrossSampleReconciliationContractError(
                "final receipt crosses repair epoch"
            )
        if self.repair_receipt.subject.successor_application != post_application:
            raise CrossSampleReconciliationContractError(
                "final receipt crosses post-MT0 application"
            )
        if any(
            group.processing_request_id != request_id
            for application in (post_application, final_application)
            for group in application.predecessors + application.successors
        ):
            raise CrossSampleReconciliationContractError(
                "final receipt application crossed request"
            )
        if self.cs2_state.processing_request_id != request_id:
            raise CrossSampleReconciliationContractError(
                "final receipt crosses CS2 request"
            )
        if self.cs2_state.repair_receipt_id != self.repair_receipt.receipt_id:
            raise CrossSampleReconciliationContractError(
                "final receipt crosses repair receipt"
            )
        pre_trajectory_input_id = _trajectory_id(self.pre_trajectory_input_id)
        post_trajectory_input_id = _trajectory_id(self.post_trajectory_input_id)
        trajectory_changed = pre_trajectory_input_id != post_trajectory_input_id
        if self.cs2_state.trajectory_changed != trajectory_changed:
            raise CrossSampleReconciliationContractError(
                "final receipt trajectory change flag is stale"
            )
        if self.cs2_state.trajectory_input_id != post_trajectory_input_id:
            raise CrossSampleReconciliationContractError(
                "final receipt crosses post trajectory snapshot"
            )
        if not trajectory_changed and self.mode_track_proposal is not None:
            raise CrossSampleReconciliationContractError(
                "unchanged trajectory cannot carry an MT1 proposal"
            )
        _validate_proposal_snapshot(
            self.mode_track_proposal,
            trajectory_input_id=post_trajectory_input_id,
            required=trajectory_changed,
        )
        population = tuple(
            sorted(self.final_population, key=lambda group: group.group_hypothesis_id)
        )
        if population != self.final_population:
            raise CrossSampleReconciliationContractError(
                "final population is not canonical-sorted"
            )
        try:
            validate_cross_sample_group_population(population)
        except (TypeError, ValueError) as exc:
            raise CrossSampleReconciliationContractError(str(exc)) from exc
        if (
            self.mode_track_proposal is None
            or type(self.mode_track_proposal) is not AcceptedModeTrackRefinement
        ):
            expected_successors = post_application.successors
            if (
                final_application.predecessors != expected_successors
                or final_application.successors != expected_successors
                or final_application.lineage
                or final_application.reason != "unchanged_membership_no_lineage"
            ):
                raise CrossSampleReconciliationContractError(
                    "direct closure must bind an unchanged successor application"
                )
            if population != self.cs2_state.population:
                raise CrossSampleReconciliationContractError(
                    "rejected MT1 outcome changed the population"
                )
        else:
            canonical_proposal = tuple(
                sorted(
                    tuple(sorted(group))
                    for group in self.mode_track_proposal.successor_member_subject_ids
                )
            )
            if (
                canonical_proposal
                != self.mode_track_proposal.successor_member_subject_ids
            ):
                raise CrossSampleReconciliationContractError(
                    "accepted MT1 proposal is not canonical"
                )
            proposed_partition = canonical_proposal
            applied_partition = tuple(
                sorted(
                    tuple(
                        sorted(
                            member.member_subject_id for member in group.member_subjects
                        )
                    )
                    for group in final_application.successors
                )
            )
            if applied_partition != proposed_partition:
                raise CrossSampleReconciliationContractError(
                    "final application does not bind accepted MT1 proposal"
                )
            expected_population = _final_population_after_application(
                before=self.cs2_state.population,
                predecessors=post_application.successors,
                successors=final_application.successors,
            )
            if population != expected_population:
                raise CrossSampleReconciliationContractError(
                    "final population is not the complete atomic successor partition"
                )
        if final_application.predecessors != post_application.successors:
            raise CrossSampleReconciliationContractError(
                "final application does not bind post-MT0 successors"
            )
        expected_basis = _final_receipt_basis(
            processing_request_id=self.processing_request_id,
            post_mt0_application=self.post_mt0_application,
            repair_receipt=self.repair_receipt,
            repair_epoch=self.repair_epoch,
            cs2_state=self.cs2_state,
            pre_trajectory_input_id=self.pre_trajectory_input_id,
            post_trajectory_input_id=self.post_trajectory_input_id,
            mode_track_proposal=self.mode_track_proposal,
            final_application=self.final_application,
            final_population=self.final_population,
        )
        if self.basis_bytes != expected_basis:
            raise CrossSampleReconciliationContractError(
                "final receipt basis bytes are not exact"
            )
        expected_id = typed_identity_digest(_FINAL_RECEIPT_PREFIX, expected_basis)
        if self.receipt_id != expected_id:
            raise CrossSampleReconciliationContractError(
                "final receipt id is not bound to its complete content"
            )

    @property
    def repair_receipt_id(self) -> str:
        return self.repair_receipt.receipt_id

    @property
    def evidence_authority(self) -> str:
        return "cross_sample_reconciliation_evidence_only"

    @property
    def membership_authority(self) -> str:
        return "none"

    @property
    def value_authority(self) -> str:
        return "none"

    @property
    def matrix_authority(self) -> str:
        return "none"

    @property
    def activation_authority(self) -> str:
        return "none"

    @property
    def writer_authority(self) -> str:
        return "none"


def reconcile_observation_repair(
    *,
    context: CrossSamplePostMT0Context,
    repair_receipt: ObservationRepairReceipt,
    mode_track_successor_member_subject_ids: Sequence[Sequence[str]] = (),
) -> CrossSampleFinalReceipt:
    """Close one repair handoff through optional, exact-bound MT1.

    ``mode_track_successor_member_subject_ids`` is only consumed when repair
    changes the trajectory identity.  The existing ModeTrack proposal API is
    therefore called zero or one time per closure, while CrossSample remains
    the only application and identity owner.
    """

    _validate_post_mt0_context(context)
    _validate_repair_receipt(repair_receipt)
    if repair_receipt.subject.successor_application != context.application:
        raise CrossSampleReconciliationContractError(
            "repair receipt is not bound to the exact post-MT0 application"
        )
    request_id = context.processing_request_id
    if repair_receipt.subject.predecessor_group.processing_request_id != request_id:
        raise CrossSampleReconciliationContractError(
            "repair receipt crossed processing request"
        )
    repaired_owners = _reconcile_owner_projection(
        owners=context.owners,
        receipt=repair_receipt,
    )
    post_trajectory = _mint_context_trajectory(
        request_id=request_id,
        application=context.application,
        owners=repaired_owners,
        classified_edges=context.classified_edges,
        anchors=context.anchors,
        injection_order=context.injection_order,
        evidence_profile=context.evidence_profile,
        budget=context.budget,
    )
    trajectory_changed = (
        post_trajectory.trajectory_input_id
        != context.trajectory_input.trajectory_input_id
    )
    if not trajectory_changed and mode_track_successor_member_subject_ids:
        raise CrossSampleReconciliationContractError(
            "unchanged trajectory cannot accept an MT1 partition"
        )
    cs2_state = CrossSampleReconciledState(
        processing_request_id=request_id,
        population=context.population,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=repair_receipt.repair_epoch,
        repair_outcome=repair_receipt.outcome,
        member_subject_ids=tuple(
            sorted(
                member.member_subject_id
                for group in context.population
                for member in group.member_subjects
            )
        ),
        trajectory_input_id=post_trajectory.trajectory_input_id,
        trajectory_changed=trajectory_changed,
    )
    proposal: CrossSampleProposalOutcome | None = None
    final_application = context.application
    final_population = context.population
    if trajectory_changed:
        try:
            proposal = propose_mode_track_successor_partition(
                trajectory_input=post_trajectory,
                successor_member_subject_ids=tuple(
                    tuple(group) for group in mode_track_successor_member_subject_ids
                ),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise CrossSampleReconciliationContractError(
                f"MT1 proposal could not close: {exc}"
            ) from exc
        _validate_proposal_snapshot(
            proposal,
            trajectory_input_id=post_trajectory.trajectory_input_id,
            required=True,
        )
        if type(proposal) is AcceptedModeTrackRefinement:
            application_context = CrossSampleCoupledProposalContext(
                predecessors=tuple(
                    sorted(
                        context.application.successors,
                        key=lambda group: group.group_hypothesis_id,
                    )
                ),
                classified_edges=context.classified_edges,
                budget=context.budget,
            )
            try:
                final_application = apply_accepted_mode_track_refinement(
                    trajectory_input=post_trajectory,
                    proposal=proposal,
                    application_context=application_context,
                    population=context.population,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise CrossSampleReconciliationContractError(
                    f"MT1 application rejected atomically: {exc}"
                ) from exc
            final_population = _final_population_after_application(
                before=context.population,
                predecessors=context.application.successors,
                successors=final_application.successors,
            )
        else:
            final_application = _mint_unchanged_successor_application(context)
    else:
        final_application = _mint_unchanged_successor_application(context)
    return _mint_final_receipt(
        processing_request_id=request_id,
        post_mt0_application=context.application,
        repair_receipt=repair_receipt,
        cs2_state=cs2_state,
        pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        post_trajectory_input_id=post_trajectory.trajectory_input_id,
        mode_track_proposal=proposal,
        final_application=final_application,
        final_population=final_population,
    )


def _mint_final_receipt(
    *,
    processing_request_id: str,
    post_mt0_application: CrossSampleSuccessorApplication,
    repair_receipt: ObservationRepairReceipt,
    cs2_state: CrossSampleReconciledState,
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_proposal: CrossSampleProposalOutcome | None,
    final_application: CrossSampleSuccessorApplication,
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleFinalReceipt:
    repair_epoch = repair_receipt.repair_epoch
    basis = _final_receipt_basis(
        processing_request_id=processing_request_id,
        post_mt0_application=post_mt0_application,
        repair_receipt=repair_receipt,
        repair_epoch=repair_epoch,
        cs2_state=cs2_state,
        pre_trajectory_input_id=pre_trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_proposal=mode_track_proposal,
        final_application=final_application,
        final_population=final_population,
    )
    return CrossSampleFinalReceipt(
        receipt_id=typed_identity_digest(_FINAL_RECEIPT_PREFIX, basis),
        processing_request_id=processing_request_id,
        post_mt0_application=post_mt0_application,
        repair_receipt=repair_receipt,
        repair_epoch=repair_epoch,
        cs2_state=cs2_state,
        pre_trajectory_input_id=pre_trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_proposal=mode_track_proposal,
        final_application=final_application,
        final_population=final_population,
        basis_bytes=basis,
        _mint_token=_FINAL_RECEIPT_MINT_TOKEN,
    )


def _validate_post_mt0_context(context: CrossSamplePostMT0Context) -> None:
    if type(context) is not CrossSamplePostMT0Context:
        raise CrossSampleReconciliationContractError(
            "post-MT0 context must be an exact typed CrossSample context"
        )
    request_id = _request_id(context.processing_request_id)
    application = _exact_application(
        context.application,
        field="post-MT0 application",
    )
    if any(
        group.processing_request_id != request_id
        for group in application.predecessors + application.successors
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 application crossed processing request"
        )
    if type(context.population) is not tuple:
        raise CrossSampleReconciliationContractError(
            "post-MT0 population must be an immutable tuple"
        )
    population = tuple(
        sorted(context.population, key=lambda group: group.group_hypothesis_id)
    )
    if population != context.population:
        raise CrossSampleReconciliationContractError(
            "post-MT0 population is not canonical-sorted"
        )
    try:
        validate_cross_sample_group_population(population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    population_ids = {group.group_hypothesis_id for group in population}
    if any(
        group.group_hypothesis_id not in population_ids
        for group in application.successors
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 application successor is absent from the population"
        )
    if any(group.processing_request_id != request_id for group in population):
        raise CrossSampleReconciliationContractError(
            "post-MT0 population crossed processing request"
        )
    members = tuple(
        member for group in application.successors for member in group.member_subjects
    )
    member_ids = tuple(member.member_subject_id for member in members)
    if (
        type(context.owners) is not tuple
        or tuple(
            sorted(
                context.owners,
                key=lambda owner: (_owner_member_id(owner), owner.owner_id),
            )
        )
        != context.owners
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 owners are not canonical-sorted"
        )
    owner_members: dict[str, SampleLocalMS1Owner] = {}
    for owner in context.owners:
        if type(owner) is not SampleLocalMS1Owner:
            raise CrossSampleReconciliationContractError("post-MT0 owner must be typed")
        member_id = _owner_member_id(owner)
        if member_id in owner_members or member_id not in member_ids:
            raise CrossSampleReconciliationContractError(
                "post-MT0 owner is not an exact successor member"
            )
        member = owner.cross_sample_member_subject
        assert member is not None
        if member.processing_request_id != request_id:
            raise CrossSampleReconciliationContractError(
                "post-MT0 owner crossed processing request"
            )
        owner_members[member_id] = owner
    if set(owner_members) != set(member_ids):
        raise CrossSampleReconciliationContractError(
            "post-MT0 owners do not close the successor population"
        )
    if (
        type(context.classified_edges) is not tuple
        or tuple(sorted(context.classified_edges, key=_edge_sort_key))
        != context.classified_edges
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 edge classifications are not canonical-sorted"
        )
    if (
        type(context.anchors) is not tuple
        or tuple(sorted(context.anchors, key=lambda anchor: anchor.anchor_id))
        != context.anchors
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 anchors are not canonical-sorted"
        )
    injection_order = _canonical_injection_order(context.injection_order)
    if injection_order != context.injection_order:
        raise CrossSampleReconciliationContractError(
            "post-MT0 injection order is not canonical-sorted"
        )
    _mint_context_trajectory(
        request_id=request_id,
        application=application,
        owners=context.owners,
        classified_edges=context.classified_edges,
        anchors=context.anchors,
        injection_order=injection_order,
        evidence_profile=context.evidence_profile,
        budget=context.budget,
        expected=context.trajectory_input,
    )


def _mint_context_trajectory(
    *,
    request_id: str,
    application: CrossSampleSuccessorApplication,
    owners: tuple[SampleLocalMS1Owner, ...],
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...],
    anchors: tuple[ModeTrackSupportAnchor, ...],
    injection_order: tuple[tuple[str, int], ...],
    evidence_profile: ModeTrackEvidenceProfile,
    budget: CrossSampleNeighborhoodBudget,
    expected: ModeTrackTrajectoryInput | None = None,
) -> ModeTrackTrajectoryInput:
    canonical_injection_order = _canonical_injection_order(injection_order)
    try:
        trajectory = mint_mode_track_trajectory_input(
            predecessors=application.successors,
            owners=owners,
            classified_edges=classified_edges,
            anchors=anchors,
            injection_order=dict(canonical_injection_order),
            evidence_profile=evidence_profile,
            budget=budget,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise CrossSampleReconciliationContractError(
            f"post-MT0 trajectory context is invalid: {exc}"
        ) from exc
    if trajectory.processing_request_id != request_id:
        raise CrossSampleReconciliationContractError(
            "trajectory input crossed processing request"
        )
    if expected is not None and trajectory != expected:
        raise CrossSampleReconciliationContractError(
            "trajectory snapshot is stale or not exact"
        )
    return trajectory


def _canonical_injection_order(
    injection_order: InjectionOrderInput,
) -> tuple[tuple[str, int], ...]:
    """Normalize the public snapshot without collapsing duplicate samples."""

    try:
        entries = tuple(
            injection_order.items()
            if isinstance(injection_order, Mapping)
            else injection_order
        )
    except (AttributeError, TypeError) as exc:
        raise CrossSampleReconciliationContractError(
            "post-MT0 injection order must be a mapping or sequence of pairs"
        ) from exc

    normalized: list[tuple[str, int]] = []
    for entry in entries:
        if isinstance(entry, (str, bytes)):
            raise CrossSampleReconciliationContractError(
                "post-MT0 injection order must contain pairs"
            )
        try:
            sample_stem, order = entry
        except (TypeError, ValueError) as exc:
            raise CrossSampleReconciliationContractError(
                "post-MT0 injection order must contain pairs"
            ) from exc
        if type(sample_stem) is not str:
            raise CrossSampleReconciliationContractError(
                "post-MT0 injection order sample keys must be strings"
            )
        normalized.append((sample_stem, order))

    sample_stems = tuple(sample_stem for sample_stem, _ in normalized)
    if len(set(sample_stems)) != len(sample_stems):
        raise CrossSampleReconciliationContractError(
            "post-MT0 injection order contains duplicate sample keys"
        )
    try:
        return tuple(sorted(normalized))
    except TypeError as exc:
        raise CrossSampleReconciliationContractError(
            "post-MT0 injection order is not sortable"
        ) from exc


def _reconcile_owner_projection(
    *,
    owners: tuple[SampleLocalMS1Owner, ...],
    receipt: ObservationRepairReceipt,
) -> tuple[SampleLocalMS1Owner, ...]:
    if receipt.outcome == "no_repair":
        return owners
    evidence = receipt.evidence
    if evidence is None:
        raise CrossSampleReconciliationContractError(
            "repaired receipt is missing evidence"
        )
    target_id = evidence.member_subject_id
    matches = [owner for owner in owners if _owner_member_id(owner) == target_id]
    if len(matches) != 1:
        raise CrossSampleReconciliationContractError(
            "repair evidence does not identify one exact owner"
        )
    target = matches[0]
    # Only the trajectory apex is projected.  Area, integration, and value
    # facts remain owned by Backfill and are deliberately not reconstructed.
    projected = replace(target, owner_apex_rt=evidence.rt_apex_min)
    return tuple(projected if owner is target else owner for owner in owners)


def _mint_unchanged_successor_application(
    context: CrossSamplePostMT0Context,
) -> CrossSampleSuccessorApplication:
    """Close a prior transition before deciding whether a new one is needed."""

    successors = tuple(
        sorted(
            context.application.successors,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    member_sets = tuple(group.member_subjects for group in successors)
    try:
        return apply_coupled_successor_proposal(
            predecessors=successors,
            successor_member_sets=member_sets,
            edges=context.classified_edges,
            budget=context.budget,
            population=context.population,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise CrossSampleReconciliationContractError(
            f"unchanged successor application could not close: {exc}"
        ) from exc


def _validate_candidate_terminal(
    terminal: ObservationRepairTerminalResult,
    *,
    context: CrossSamplePostMT0Context,
) -> tuple[
    ObservationRepairCandidate,
    ObservationRepairReceipt,
    ObservationRepairEvidence,
]:
    if (
        type(terminal) is not ObservationRepairTerminalResult
        or type(terminal.authorization) is not ObservationRepairRepairAuthorization
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember admission requires an exact repair authorization terminal"
        )
    authorization = terminal.authorization
    candidate = authorization.candidate
    receipt = terminal.receipt
    if (
        type(candidate) is not ObservationRepairCandidate
        or type(receipt) is not ObservationRepairReceipt
        or receipt.subject is not candidate
        or candidate is not authorization.candidate
    ):
        raise CrossSampleReconciliationContractError(
            "repair terminal crossed its exact candidate or receipt"
        )
    evidence = receipt.evidence
    if (
        receipt.outcome != "repaired"
        or type(evidence) is not ObservationRepairEvidence
        or evidence.member_subject_id is not None
        or receipt.authorization_id != authorization.authorization_id
        or receipt.candidate_id != candidate.candidate_id
        or receipt.repair_epoch != candidate.repair_epoch
        or receipt.repair_epoch != 1
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember admission requires one successful membership-neutral receipt"
        )
    if (
        candidate.processing_owners is not authorization.processing_owners
        or candidate.processing_owners.request.processing_request_id
        != context.processing_request_id
        or candidate.predecessor_group.processing_request_id
        != context.processing_request_id
    ):
        raise CrossSampleReconciliationContractError(
            "repair candidate crossed processing owners or request"
        )
    if (
        len(context.application.successors) != 1
        or context.application.successors[0] is not candidate.predecessor_group
        or not any(group is candidate.predecessor_group for group in context.population)
    ):
        raise CrossSampleReconciliationContractError(
            "repair candidate predecessor is not the exact current group application"
        )
    sample_identity_id = candidate.sample_identity.identity.sample_identity_id
    if any(
        member.canonical_sample_identity_id == sample_identity_id
        for member in candidate.predecessor_group.member_subjects
    ):
        raise CrossSampleReconciliationContractError(
            "repair candidate already has CrossSample membership"
        )
    if (
        candidate.sample_identity.metadata.raw_stem
        != candidate.request_identity.sample_stem
        or candidate.cell.content.canonical_sample_identity_id != sample_identity_id
        or candidate.cell.content.group_hypothesis_id
        != candidate.predecessor_group.group_hypothesis_id
        or candidate.logical_request.group_hypothesis_id
        != candidate.predecessor_group.group_hypothesis_id
    ):
        raise CrossSampleReconciliationContractError(
            "repair candidate crossed sample, cell, request, or predecessor"
        )
    exact_evidence = (
        evidence.logical_request_id,
        evidence.request_identity_id,
        evidence.group_hypothesis_id,
        evidence.sample_stem,
        evidence.acquisition_source_id,
    )
    expected_evidence = (
        candidate.logical_request.logical_request_id,
        candidate.request_identity.request_identity_id,
        candidate.predecessor_group.group_hypothesis_id,
        candidate.request_identity.sample_stem,
        candidate.request_identity.acquisition_source.acquisition_source_id,
    )
    if exact_evidence != expected_evidence:
        raise CrossSampleReconciliationContractError(
            "repair receipt crossed request, group, sample, or acquisition"
        )
    return candidate, receipt, evidence


def _repair_observation_for_receipt(
    result: OwnerBackfillSampleResult,
    *,
    candidate: ObservationRepairCandidate,
    receipt: ObservationRepairReceipt,
    evidence: ObservationRepairEvidence,
) -> OwnerBackfillPeakFactObservation:
    if type(result) is not OwnerBackfillSampleResult:
        raise CrossSampleReconciliationContractError(
            "repair observation must come from an exact Backfill sample result"
        )
    if result.sample_stem != candidate.request_identity.sample_stem:
        raise CrossSampleReconciliationContractError(
            "repair sample result crossed candidate sample"
        )
    matches = tuple(
        observation
        for observation in result.peak_fact_observations
        if observation.work_unit_key == evidence.work_unit_key
    )
    if len(matches) != 1:
        raise CrossSampleReconciliationContractError(
            "repair result does not contain one exact live observation"
        )
    observation = matches[0]
    if type(observation) is not OwnerBackfillPeakFactObservation:
        raise CrossSampleReconciliationContractError(
            "repair live observation must be owner-minted"
        )
    try:
        validate_owner_backfill_peak_fact_observation(observation)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    if (
        observation.logical_request != candidate.logical_request
        or observation.request_identity != candidate.request_identity
        or observation.cell.group_hypothesis_id
        != candidate.predecessor_group.group_hypothesis_id
        or observation.cell.sample_stem != candidate.request_identity.sample_stem
    ):
        raise CrossSampleReconciliationContractError(
            "repair live observation crossed its candidate bindings"
        )
    evaluation = observation.physical_evaluation
    fact_receipt = observation.facts
    physical_facts = None if evaluation is None else evaluation.facts
    if evaluation is None or fact_receipt is None or physical_facts is None:
        raise CrossSampleReconciliationContractError(
            "repair receipt is missing its live physical observation"
        )
    integration_id = selected_integration_authority_projection(
        fact_receipt.integration
    ).selected_integration_authority_content_id
    observed = (
        observation.logical_request.logical_request_id,
        observation.request_identity.request_identity_id,
        observation.cell.group_hypothesis_id,
        observation.cell.sample_stem,
        observation.work_unit_key,
        observation.candidate_index,
        observation.candidate_phase,
        observation.request_identity.acquisition_source.acquisition_source_id,
        evaluation.question.selection_subject_id,
        physical_facts.physical_question_id,
        physical_facts.physical_answer_id,
        physical_facts.physical_segment_id,
        physical_facts.fact_content_id,
        integration_id,
        physical_facts.left_rt_min,
        physical_facts.apex_rt_min,
        physical_facts.right_rt_min,
        physical_facts.height,
        physical_facts.scan_support_count,
        physical_facts.area_counts_seconds,
        physical_facts.area_source,
    )
    expected = (
        evidence.logical_request_id,
        evidence.request_identity_id,
        evidence.group_hypothesis_id,
        evidence.sample_stem,
        evidence.work_unit_key,
        evidence.candidate_index,
        evidence.candidate_phase,
        evidence.acquisition_source_id,
        evidence.selection_subject_id,
        evidence.physical_question_id,
        evidence.physical_answer_id,
        evidence.physical_segment_id,
        evidence.fact_content_id,
        evidence.selected_integration_authority_content_id,
        evidence.rt_left_min,
        evidence.rt_apex_min,
        evidence.rt_right_min,
        evidence.height,
        evidence.scan_support_count,
        evidence.area_counts_seconds,
        evidence.area_source,
    )
    if observed != expected or receipt.evidence is not evidence:
        raise CrossSampleReconciliationContractError(
            "repair receipt crossed its exact live physical evidence"
        )
    return observation


def _mint_repaired_candidate_member(
    *,
    candidate: ObservationRepairCandidate,
    evidence: ObservationRepairEvidence,
) -> CrossSampleGroupMemberSubject:
    request_id = candidate.predecessor_group.processing_request_id
    sample_identity_id = candidate.sample_identity.identity.sample_identity_id
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": request_id,
            "canonical_sample_identity_id": sample_identity_id,
            "subject": {
                "kind": "physical_observation",
                "id": evidence.fact_content_id,
            },
            "physical_observation_ids": [evidence.fact_content_id],
        }
    )
    try:
        return CrossSampleGroupMemberSubject(
            member_subject_id=typed_identity_digest(
                _GROUP_MEMBER_SUBJECT_PREFIX,
                basis,
            ),
            processing_request_id=request_id,
            canonical_sample_identity_id=sample_identity_id,
            subject_kind="physical_observation",
            subject_id=evidence.fact_content_id,
            physical_observation_ids=(evidence.fact_content_id,),
            basis_bytes=basis,
        )
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc


def _project_repaired_observation(
    *,
    candidate: ObservationRepairCandidate,
    receipt: ObservationRepairReceipt,
    evidence: ObservationRepairEvidence,
    candidate_member: CrossSampleGroupMemberSubject,
) -> CrossSampleRepairedObservation:
    feature = candidate.logical_request.item.feature
    return _mint_cross_sample_repaired_observation(
        repair_receipt_id=receipt.receipt_id,
        authorization_id=candidate.authorization_id,
        candidate_id=candidate.candidate_id,
        sample_stem=evidence.sample_stem,
        acquisition_source_id=evidence.acquisition_source_id,
        precursor_mz=feature.family_center_mz,
        rt_left_min=evidence.rt_left_min,
        rt_apex_min=evidence.rt_apex_min,
        rt_right_min=evidence.rt_right_min,
        height=evidence.height,
        scan_support_count=evidence.scan_support_count,
        area_counts_seconds=evidence.area_counts_seconds,
        member_subject=candidate_member,
    )


def _classify_candidate_edge(
    *,
    predecessor_owner: SampleLocalMS1Owner,
    repaired_observation: CrossSampleRepairedObservation,
    config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None,
) -> CrossSampleRepairAdmissionEdge:
    try:
        return classify_cross_sample_repair_admission_edge(
            predecessor_owner=predecessor_owner,
            repaired_observation=repaired_observation,
            config=config,
            drift_lookup=drift_lookup,
        )
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(
            f"repair candidate edge could not close: {exc}"
        ) from exc


def _reduce_repair_admission(
    edges: tuple[CrossSampleRepairAdmissionEdge, ...],
) -> CrossSampleRepairDisposition:
    if not edges:
        raise CrossSampleReconciliationContractError(
            "repair candidate classification is incomplete"
        )
    dispositions = tuple(edge.disposition for edge in edges)
    if any(disposition == "excluded" for disposition in dispositions):
        return "excluded"
    if any(disposition == "review_only" for disposition in dispositions):
        return "review_only"
    if all(disposition == "eligible" for disposition in dispositions):
        return "admitted"
    raise CrossSampleReconciliationContractError(
        "repair candidate classification is not a closed disposition"
    )


def _replace_exact_population_group(
    *,
    population: tuple[CrossSampleGroupIdentity, ...],
    predecessor: CrossSampleGroupIdentity,
    successor: CrossSampleGroupIdentity,
) -> tuple[CrossSampleGroupIdentity, ...]:
    matches = [group for group in population if group is predecessor]
    if len(matches) != 1 or any(
        group.group_hypothesis_id == successor.group_hypothesis_id
        for group in population
        if group is not predecessor
    ):
        raise CrossSampleReconciliationContractError(
            "repair application crossed or duplicated the current population"
        )
    result = tuple(
        sorted(
            (successor if group is predecessor else group for group in population),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    try:
        validate_cross_sample_group_population(result)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    return result


def _post_repair_injection_order(
    *,
    context: CrossSamplePostMT0Context,
    candidate: ObservationRepairCandidate,
    evidence: ObservationRepairEvidence,
) -> dict[str, int]:
    injection_order = dict(context.injection_order)
    bindings = tuple(
        binding
        for binding in candidate.processing_owners.request.acquisition_bindings
        if binding.acquisition_source_id == evidence.acquisition_source_id
    )
    if len(bindings) != 1 or evidence.sample_stem in injection_order:
        raise CrossSampleReconciliationContractError(
            "repair candidate injection binding is missing or duplicated"
        )
    binding = bindings[0]
    if binding.injection_order in set(injection_order.values()):
        raise CrossSampleReconciliationContractError(
            "repair candidate injection order crosses the owner population"
        )
    injection_order[evidence.sample_stem] = binding.injection_order
    return injection_order


def _owner_sort_key(owner: SampleLocalMS1Owner) -> tuple[str, str]:
    return (_owner_member_id(owner), owner.owner_id)


def _post_repair_mode_track_input_basis(
    *,
    repair_receipt_id: str,
    application: CrossSampleRepairAdmissionApplication,
    repaired_observation: CrossSampleRepairedObservation,
    owners: tuple[SampleLocalMS1Owner, ...],
    trajectory_input: ModeTrackTrajectoryInput,
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_post_repair_mode_track_input_v1",
            "repair_receipt_id": repair_receipt_id,
            "application_id": application.application_id,
            "repaired_observation_id": repaired_observation.observation_id,
            "admitted_member_subject_id": (application.admitted_member_subject_id),
            "owner_ids": [owner.owner_id for owner in owners],
            "trajectory_input_id": trajectory_input.trajectory_input_id,
        }
    )


def _mint_post_repair_mode_track_input(
    *,
    repair_receipt_id: str,
    application: CrossSampleRepairAdmissionApplication,
    repaired_observation: CrossSampleRepairedObservation,
    owners: tuple[SampleLocalMS1Owner, ...],
    trajectory_input: ModeTrackTrajectoryInput,
) -> CrossSamplePostRepairModeTrackInput:
    basis = _post_repair_mode_track_input_basis(
        repair_receipt_id=repair_receipt_id,
        application=application,
        repaired_observation=repaired_observation,
        owners=owners,
        trajectory_input=trajectory_input,
    )
    return CrossSamplePostRepairModeTrackInput(
        input_id=typed_identity_digest(
            _POST_REPAIR_MODE_TRACK_INPUT_PREFIX,
            basis,
        ),
        repair_receipt_id=repair_receipt_id,
        application=application,
        repaired_observation=repaired_observation,
        owners=owners,
        trajectory_input=trajectory_input,
        basis_bytes=basis,
        _mint_token=_POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN,
    )


def _repair_edge_basis(edge: CrossSampleRepairAdmissionEdge) -> dict[str, object]:
    return {
        "left_member_subject_id": edge.left_member_subject_id,
        "right_member_subject_id": edge.right_member_subject_id,
        "disposition": edge.disposition,
        "predecessor_owner_id": edge.predecessor_owner.owner_id,
        "repaired_observation_id": edge.repaired_observation.observation_id,
        "rt_raw_delta_sec": edge.rt_raw_delta_sec,
        "rt_drift_corrected_delta_sec": edge.rt_drift_corrected_delta_sec,
        "drift_prior_source": edge.drift_prior_source,
        "injection_order_gap": edge.injection_order_gap,
        "reason": edge.reason,
        "config": asdict(edge.config),
    }


def _nonmember_repair_result_basis(
    *,
    processing_request_id: str,
    repair_terminal: ObservationRepairTerminalResult,
    disposition: CrossSampleRepairDisposition,
    pairwise_edges: tuple[CrossSampleRepairAdmissionEdge, ...],
    application: CrossSampleRepairAdmissionApplication | None,
    final_population: tuple[CrossSampleGroupIdentity, ...],
    final_owners: tuple[SampleLocalMS1Owner, ...],
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_input: CrossSamplePostRepairModeTrackInput | None,
) -> bytes:
    receipt = repair_terminal.receipt
    candidate = repair_terminal.candidate
    if receipt is None or candidate is None:
        raise CrossSampleReconciliationContractError(
            "nonmember repair result lost its exact terminal"
        )
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_nonmember_repair_v1",
            "processing_request_id": processing_request_id,
            "authorization_id": candidate.authorization_id,
            "candidate_id": candidate.candidate_id,
            "repair_receipt_id": receipt.receipt_id,
            "disposition": disposition,
            "pairwise_edges": [_repair_edge_basis(edge) for edge in pairwise_edges],
            "application_id": (
                None if application is None else application.application_id
            ),
            "final_group_hypothesis_ids": [
                group.group_hypothesis_id for group in final_population
            ],
            "final_owner_ids": [owner.owner_id for owner in final_owners],
            "pre_trajectory_input_id": pre_trajectory_input_id,
            "post_trajectory_input_id": post_trajectory_input_id,
            "mode_track_input_id": (
                None if mode_track_input is None else mode_track_input.input_id
            ),
        }
    )


def _mint_nonmember_repair_result(
    *,
    processing_request_id: str,
    repair_terminal: ObservationRepairTerminalResult,
    disposition: CrossSampleRepairDisposition,
    pairwise_edges: tuple[CrossSampleRepairAdmissionEdge, ...],
    application: CrossSampleRepairAdmissionApplication | None,
    final_population: tuple[CrossSampleGroupIdentity, ...],
    final_owners: tuple[SampleLocalMS1Owner, ...],
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_input: CrossSamplePostRepairModeTrackInput | None,
) -> CrossSampleNonmemberRepairResult:
    basis = _nonmember_repair_result_basis(
        processing_request_id=processing_request_id,
        repair_terminal=repair_terminal,
        disposition=disposition,
        pairwise_edges=pairwise_edges,
        application=application,
        final_population=final_population,
        final_owners=final_owners,
        pre_trajectory_input_id=pre_trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_input=mode_track_input,
    )
    return CrossSampleNonmemberRepairResult(
        result_id=typed_identity_digest(_NONMEMBER_REPAIR_RESULT_PREFIX, basis),
        processing_request_id=processing_request_id,
        repair_terminal=repair_terminal,
        disposition=disposition,
        pairwise_edges=pairwise_edges,
        application=application,
        final_population=final_population,
        final_owners=final_owners,
        pre_trajectory_input_id=pre_trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_input=mode_track_input,
        basis_bytes=basis,
        _mint_token=_NONMEMBER_REPAIR_RESULT_MINT_TOKEN,
    )


def _validate_nonmember_repair_result(
    result: CrossSampleNonmemberRepairResult,
) -> None:
    request_id = _request_id(result.processing_request_id)
    terminal = result.repair_terminal
    if (
        type(terminal) is not ObservationRepairTerminalResult
        or type(terminal.authorization) is not ObservationRepairRepairAuthorization
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair result crossed its authorization terminal"
        )
    candidate = terminal.authorization.candidate
    receipt = terminal.receipt
    evidence = None if receipt is None else receipt.evidence
    if (
        type(candidate) is not ObservationRepairCandidate
        or type(receipt) is not ObservationRepairReceipt
        or type(evidence) is not ObservationRepairEvidence
        or receipt.subject is not candidate
        or evidence.member_subject_id is not None
        or candidate.predecessor_group.processing_request_id != request_id
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair result lost its exact repair receipt"
        )
    if result.disposition not in {"admitted", "review_only", "excluded"}:
        raise CrossSampleReconciliationContractError(
            "nonmember repair result disposition is not closed"
        )
    if (
        type(result.pairwise_edges) is not tuple
        or tuple(sorted(result.pairwise_edges, key=_edge_sort_key))
        != result.pairwise_edges
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair pairwise edges are not canonical"
        )
    expected_member = _mint_repaired_candidate_member(
        candidate=candidate,
        evidence=evidence,
    )
    expected_observation = _project_repaired_observation(
        candidate=candidate,
        receipt=receipt,
        evidence=evidence,
        candidate_member=expected_member,
    )
    predecessor_member_ids = {
        member.member_subject_id
        for member in candidate.predecessor_group.member_subjects
    }
    observed_predecessor_ids: set[str] = set()
    observed_pairs: set[tuple[str, str]] = set()
    for edge in result.pairwise_edges:
        if (
            type(edge) is not CrossSampleRepairAdmissionEdge
            or edge.repaired_observation != expected_observation
        ):
            raise CrossSampleReconciliationContractError(
                "nonmember repair edge is not an exact physical-admission edge"
            )
        try:
            edge.__post_init__()
        except (TypeError, ValueError) as exc:
            raise CrossSampleReconciliationContractError(str(exc)) from exc
        edge_ids = {
            edge.left_member_subject_id,
            edge.right_member_subject_id,
        }
        left_id, right_id = sorted(
            (
                edge.left_member_subject_id,
                edge.right_member_subject_id,
            )
        )
        observed_pairs.add((left_id, right_id))
        if expected_member.member_subject_id not in edge_ids:
            raise CrossSampleReconciliationContractError(
                "nonmember repair edge crossed the candidate member"
            )
        observed_predecessor_ids.update(edge_ids - {expected_member.member_subject_id})
    if (
        len(result.pairwise_edges) != len(predecessor_member_ids)
        or len(observed_pairs) != len(result.pairwise_edges)
        or observed_predecessor_ids != predecessor_member_ids
        or (_reduce_repair_admission(result.pairwise_edges) != result.disposition)
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair edge set is incomplete or misreduced"
        )
    if (
        type(result.final_population) is not tuple
        or tuple(
            sorted(
                result.final_population,
                key=lambda group: group.group_hypothesis_id,
            )
        )
        != result.final_population
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair population is not canonical"
        )
    try:
        validate_cross_sample_group_population(result.final_population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    if (
        type(result.final_owners) is not tuple
        or tuple(sorted(result.final_owners, key=_owner_sort_key))
        != result.final_owners
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair owners are not canonical"
        )
    pre_id = _trajectory_id(result.pre_trajectory_input_id)
    post_id = _trajectory_id(result.post_trajectory_input_id)
    if result.disposition == "admitted":
        application = result.application
        mode_input = result.mode_track_input
        if (
            type(application) is not CrossSampleRepairAdmissionApplication
            or type(mode_input) is not CrossSamplePostRepairModeTrackInput
            or application.predecessor is not candidate.predecessor_group
            or application.repair_receipt_id != receipt.receipt_id
            or application.authorization_id != candidate.authorization_id
            or application.candidate_id != candidate.candidate_id
            or application.admitted_member_subject_id
            != expected_member.member_subject_id
            or not any(
                group is application.successor for group in result.final_population
            )
            or any(
                group is application.predecessor for group in result.final_population
            )
            or mode_input.application is not application
            or mode_input.repair_receipt_id != receipt.receipt_id
            or mode_input.repaired_observation.member_subject != expected_member
            or mode_input.trajectory_input.trajectory_input_id != post_id
        ):
            raise CrossSampleReconciliationContractError(
                "admitted repair result crossed application or ModeTrack input"
            )
        if pre_id == post_id:
            raise CrossSampleReconciliationContractError(
                "admitted repair result did not change trajectory identity"
            )
        expected_owner_ids = predecessor_member_ids
    else:
        if (
            result.application is not None
            or result.mode_track_input is not None
            or pre_id != post_id
            or not any(
                group is candidate.predecessor_group
                for group in result.final_population
            )
        ):
            raise CrossSampleReconciliationContractError(
                "non-admitted repair result changed membership or trajectory"
            )
        expected_owner_ids = predecessor_member_ids
    owner_member_ids = {_owner_member_id(owner) for owner in result.final_owners}
    if owner_member_ids != expected_owner_ids or len(owner_member_ids) != len(
        result.final_owners
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember repair owners do not close the resulting group"
        )
    basis = _nonmember_repair_result_basis(
        processing_request_id=request_id,
        repair_terminal=terminal,
        disposition=result.disposition,
        pairwise_edges=result.pairwise_edges,
        application=result.application,
        final_population=result.final_population,
        final_owners=result.final_owners,
        pre_trajectory_input_id=pre_id,
        post_trajectory_input_id=post_id,
        mode_track_input=result.mode_track_input,
    )
    if result.basis_bytes != basis:
        raise CrossSampleReconciliationContractError(
            "nonmember repair result basis bytes are not exact"
        )
    try:
        validate_typed_identity_basis(
            result.result_id,
            prefix=_NONMEMBER_REPAIR_RESULT_PREFIX,
            basis_bytes=basis,
            field="nonmember repair result_id",
        )
    except ValueError as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc


def _validate_repair_receipt(receipt: ObservationRepairReceipt) -> None:
    if type(receipt) is not ObservationRepairReceipt:
        raise CrossSampleReconciliationContractError(
            "repair receipt must be an exact Backfill receipt"
        )
    subject = receipt.subject
    if type(subject) is not ObservationRepairSubject:
        raise CrossSampleReconciliationContractError(
            "repair receipt subject must be owner-minted"
        )
    from xic_extractor.alignment.owner_backfill_request_plan import (
        OwnerBackfillLogicalRequestMember,
        OwnerBackfillRequestIdentity,
    )
    from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

    if type(subject.request_identity) is not OwnerBackfillRequestIdentity:
        raise CrossSampleReconciliationContractError(
            "repair subject request identity is not owner-minted"
        )
    if type(subject.logical_request) is not OwnerBackfillLogicalRequestMember:
        raise CrossSampleReconciliationContractError(
            "repair subject logical request is not owner-minted"
        )
    if type(subject.predecessor_group) is not CrossSampleGroupIdentity:
        raise CrossSampleReconciliationContractError(
            "repair subject predecessor is not CrossSample-owned"
        )
    if type(subject.member_subject) is not CrossSampleGroupMemberSubject:
        raise CrossSampleReconciliationContractError(
            "repair subject member is not CrossSample-owned"
        )
    if type(subject.sample_identity) is not ResolvedCanonicalSampleIdentity:
        raise CrossSampleReconciliationContractError(
            "repair subject sample is not owner-resolved"
        )
    _exact_application(
        subject.successor_application,
        field="repair subject application",
    )
    if type(receipt.evidence) not in {ObservationRepairEvidence, type(None)}:
        raise CrossSampleReconciliationContractError(
            "repair receipt evidence must be an exact Backfill record"
        )
    if receipt.subject_id != subject.subject_id:
        raise CrossSampleReconciliationContractError(
            "repair receipt crosses its exact subject"
        )
    if (
        type(receipt.repair_epoch) is not int
        or receipt.repair_epoch != 1
        or type(subject.repair_epoch) is not int
        or receipt.repair_epoch != subject.repair_epoch
    ):
        raise CrossSampleReconciliationContractError(
            "repair receipt epoch is not exactly one"
        )
    if receipt.sample_stem != subject.request_identity.sample_stem:
        raise CrossSampleReconciliationContractError("repair receipt crosses sample")
    expected_outcome = "repaired" if receipt.evidence is not None else "no_repair"
    if receipt.outcome != expected_outcome:
        raise CrossSampleReconciliationContractError(
            "repair receipt outcome crosses its evidence"
        )
    if (
        subject.logical_request.item.sample_stem,
        subject.logical_request.item.request,
    ) != (
        subject.request_identity.sample_stem,
        subject.request_identity.request,
    ):
        raise CrossSampleReconciliationContractError(
            "repair subject crosses exact request identity"
        )
    if (
        subject.logical_request.group_hypothesis_id
        != subject.predecessor_group.group_hypothesis_id
    ):
        raise CrossSampleReconciliationContractError(
            "repair subject crosses predecessor group identity"
        )
    if subject.member_subject not in subject.predecessor_group.member_subjects:
        raise CrossSampleReconciliationContractError(
            "repair subject member is absent from predecessor group"
        )
    if subject.predecessor_group not in subject.successor_application.successors:
        raise CrossSampleReconciliationContractError(
            "repair subject group is absent from successor application"
        )
    if (
        subject.member_subject.processing_request_id
        != subject.predecessor_group.processing_request_id
    ):
        raise CrossSampleReconciliationContractError(
            "repair subject member crosses processing request"
        )
    if (
        subject.sample_identity.identity.sample_identity_id
        != subject.member_subject.canonical_sample_identity_id
        or subject.sample_identity.metadata.raw_stem
        != subject.request_identity.sample_stem
    ):
        raise CrossSampleReconciliationContractError(
            "repair subject crosses exact sample identity"
        )
    if receipt.evidence is not None:
        evidence = receipt.evidence
        if (
            evidence.logical_request_id != subject.logical_request.logical_request_id
            or evidence.request_identity_id
            != subject.request_identity.request_identity_id
            or evidence.group_hypothesis_id
            != subject.predecessor_group.group_hypothesis_id
            or evidence.member_subject_id != subject.member_subject.member_subject_id
            or evidence.sample_stem != subject.request_identity.sample_stem
            or evidence.acquisition_source_id
            != subject.request_identity.acquisition_source.acquisition_source_id
        ):
            raise CrossSampleReconciliationContractError(
                "repair evidence crosses exact request, predecessor, member, or sample"
            )


def _validate_proposal_snapshot(
    proposal: CrossSampleProposalOutcome | None,
    *,
    trajectory_input_id: str,
    required: bool,
) -> None:
    if proposal is None:
        if required:
            raise CrossSampleReconciliationContractError(
                "changed trajectory has no closed MT1 proposal outcome"
            )
        return
    if type(proposal) not in {
        AcceptedModeTrackRefinement,
        RejectedModeTrackRefinement,
        InsufficientModeTrackTraining,
        UnassessableModeTrackRefinement,
    }:
        raise CrossSampleReconciliationContractError(
            "MT1 proposal is not a closed ModeTrack variant"
        )
    if proposal.trajectory_input_id != trajectory_input_id:
        raise CrossSampleReconciliationContractError(
            "MT1 proposal is stale for the repaired trajectory snapshot"
        )


def _final_population_after_application(
    *,
    before: tuple[CrossSampleGroupIdentity, ...],
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> tuple[CrossSampleGroupIdentity, ...]:
    predecessor_ids = {group.group_hypothesis_id for group in predecessors}
    if not predecessor_ids <= {group.group_hypothesis_id for group in before}:
        raise CrossSampleReconciliationContractError(
            "final application predecessor is absent from CS2 population"
        )
    result = tuple(
        sorted(
            tuple(
                group
                for group in before
                if group.group_hypothesis_id not in predecessor_ids
            )
            + tuple(successors),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    try:
        validate_cross_sample_group_population(result)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    return result


def _exact_application(
    application: CrossSampleSuccessorApplication,
    *,
    field: str,
) -> CrossSampleSuccessorApplication:
    if type(application) is not CrossSampleSuccessorApplication:
        raise CrossSampleReconciliationContractError(
            f"{field} must be an exact CrossSample application"
        )
    return application


def _application_reference(
    application: CrossSampleSuccessorApplication,
) -> dict[str, object]:
    return {
        "status": application.status,
        "reason": application.reason,
        "predecessors": [_group_reference(group) for group in application.predecessors],
        "successors": [_group_reference(group) for group in application.successors],
        "lineage": [
            {
                "lineage_edge_id": edge.lineage_edge_id,
                "basis_hex": edge.basis_bytes.hex(),
            }
            for edge in application.lineage
        ],
    }


def _group_reference(group: CrossSampleGroupIdentity) -> dict[str, object]:
    return {
        "group_hypothesis_id": group.group_hypothesis_id,
        "basis_hex": group.basis_bytes.hex(),
        "disposition": group.disposition,
    }


def _final_receipt_basis(
    *,
    processing_request_id: str,
    post_mt0_application: CrossSampleSuccessorApplication,
    repair_receipt: ObservationRepairReceipt,
    repair_epoch: int,
    cs2_state: CrossSampleReconciledState,
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_proposal: CrossSampleProposalOutcome | None,
    final_application: CrossSampleSuccessorApplication,
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_final_receipt_v1",
            "processing_request_id": processing_request_id,
            "post_mt0_application": _application_reference(post_mt0_application),
            "repair_receipt_id": repair_receipt.receipt_id,
            "repair_epoch": repair_epoch,
            "cs2_state": {
                "processing_request_id": cs2_state.processing_request_id,
                "population": [
                    _group_reference(group) for group in cs2_state.population
                ],
                "repair_receipt_id": cs2_state.repair_receipt_id,
                "repair_epoch": cs2_state.repair_epoch,
                "repair_outcome": cs2_state.repair_outcome,
                "member_subject_ids": list(cs2_state.member_subject_ids),
                "trajectory_input_id": cs2_state.trajectory_input_id,
                "trajectory_changed": cs2_state.trajectory_changed,
            },
            "pre_trajectory_input_id": pre_trajectory_input_id,
            "post_trajectory_input_id": post_trajectory_input_id,
            "mode_track_proposal": _proposal_reference(mode_track_proposal),
            "final_application": _application_reference(final_application),
            "final_population": [_group_reference(group) for group in final_population],
        }
    )


def _proposal_reference(
    proposal: CrossSampleProposalOutcome | None,
) -> dict[str, object] | None:
    if proposal is None:
        return None
    if type(proposal) is AcceptedModeTrackRefinement:
        return {
            "variant": proposal.variant,
            "trajectory_input_id": proposal.trajectory_input_id,
            "successor_member_subject_ids": [
                list(group) for group in proposal.successor_member_subject_ids
            ],
        }
    if type(proposal) is RejectedModeTrackRefinement:
        return {
            "variant": proposal.variant,
            "trajectory_input_id": proposal.trajectory_input_id,
            "reason_codes": list(proposal.reason_codes),
        }
    if type(proposal) is InsufficientModeTrackTraining:
        return {
            "variant": proposal.variant,
            "trajectory_input_id": proposal.trajectory_input_id,
            "reason_codes": list(proposal.reason_codes),
        }
    if type(proposal) is UnassessableModeTrackRefinement:
        return {
            "variant": proposal.variant,
            "trajectory_input_id": proposal.trajectory_input_id,
            "reason": proposal.reason,
        }
    raise CrossSampleReconciliationContractError(
        "cannot serialize an unknown MT1 proposal"
    )


def _owner_member_id(owner: SampleLocalMS1Owner) -> str:
    if type(owner) is not SampleLocalMS1Owner:
        raise CrossSampleReconciliationContractError(
            "owner must be an exact SampleLocalMS1Owner"
        )
    member = owner.cross_sample_member_subject
    if type(member) is not CrossSampleGroupMemberSubject:
        raise CrossSampleReconciliationContractError(
            "owner is missing its exact CrossSample member"
        )
    return member.member_subject_id


def _edge_sort_key(
    edge: CrossSampleClassifiedEdge,
) -> tuple[str, ...]:
    if type(edge) not in {
        CrossSampleNeighborhoodEdge,
        CrossSampleRepairAdmissionEdge,
    }:
        raise CrossSampleReconciliationContractError(
            "edge classification must be an exact CrossSample edge"
        )
    return tuple(sorted((edge.left_member_subject_id, edge.right_member_subject_id)))


def _request_id(value: object) -> str:
    try:
        return validate_typed_identity_id(
            value,
            prefix=PROCESSING_REQUEST_ID_PREFIX,
            field="processing_request_id",
        )
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc


def _trajectory_id(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(_TRAJECTORY_INPUT_PREFIX):
        raise CrossSampleReconciliationContractError(
            "trajectory input identity is not owner-typed"
        )
    return value


__all__ = (
    "CrossSampleFinalReceipt",
    "CrossSamplePostMT0Context",
    "CrossSampleProposalOutcome",
    "CrossSampleReconciledState",
    "CrossSampleReconciliationContractError",
    "build_cross_sample_post_mt0_context",
    "reconcile_observation_repair",
)
