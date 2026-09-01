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
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
    ObservationRepairEvidence,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNotRunAuthorization,
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
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleNeighborhoodEdge,
    CrossSampleRepairAdmissionApplication,
    CrossSampleRepairAdmissionEdge,
    CrossSampleRepairedObservation,
    CrossSampleSuccessorApplication,
    _apply_repaired_nonmember_admission,
    _expected_cross_sample_lineage,
    _mint_cross_sample_repaired_observation,
    classify_cross_sample_repair_admission_edge,
    mint_cross_sample_group_identity,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
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
)
from xic_extractor.alignment.owner_backfill import (
    OwnerBackfillPeakFactObservation,
    validate_owner_backfill_peak_fact_observation,
)
from xic_extractor.alignment.owner_backfill_peak_fact_provenance import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    ProcessingOwnerReferences,
    canonical_identity_basis_bytes,
    typed_identity_digest,
    validate_typed_identity_basis,
    validate_typed_identity_id,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.mode_track.refinement import (
        ModeTrackFinalAttemptResult,
        ModeTrackRefinementRun,
    )

_FINAL_RECEIPT_PREFIX = "xic.cross-sample-final-receipt.v1:"
_NONMEMBER_REPAIR_RESULT_PREFIX = "xic.cross-sample-nonmember-repair.v1:"
_POST_REPAIR_MODE_TRACK_INPUT_PREFIX = (
    "xic.cross-sample-post-repair-mode-track-input.v1:"
)
_POST_REPAIR_POPULATION_PREFIX = "xic.cross-sample-post-repair-population.v1:"
_POST_REPAIR_APPLICATION_PREFIX = "xic.cross-sample-post-repair-application.v1:"
_EXISTING_MEMBER_REPAIR_RESULT_PREFIX = (
    "xic.cross-sample-existing-member-repair.v1:"
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
CrossSamplePostRepairDisposition: TypeAlias = Literal[
    "existing_member_reconciled",
    "nonmember_admitted",
]
InjectionOrderInput: TypeAlias = Mapping[str, int] | Sequence[tuple[str, int]]


class CrossSampleReconciliationContractError(CrossSampleContractError):
    """Raised when the CrossSample repair closure cannot close exactly."""


@dataclass(frozen=True, slots=True)
class CrossSamplePostMT0Context:
    """Exact post-MT0 CrossSample state needed for one repair reconciliation."""

    processing_request_id: str
    processing_owners: ProcessingOwnerReferences
    population: tuple[CrossSampleGroupIdentity, ...]
    application: CrossSampleSuccessorApplication
    owners: tuple[SampleLocalMS1Owner, ...]
    classified_edges: tuple[CrossSampleNeighborhoodEdge, ...]
    anchors: tuple[ModeTrackSupportAnchor, ...]
    injection_order: tuple[tuple[str, int], ...]
    evidence_profile: ModeTrackEvidenceProfile
    budget: CrossSampleNeighborhoodBudget
    trajectory_input: ModeTrackTrajectoryInput
    fit_once_source_id: str | None = None

    def __post_init__(self) -> None:
        _validate_post_mt0_context(self)


@dataclass(frozen=True, slots=True)
class CrossSamplePostRepairModeTrackInput:
    """Replay-bound CrossSample carrier for the separate ModeTrack owner."""

    post_repair_input_id: str
    processing_request_id: str
    processing_attempt_id: str
    processing_owners: ProcessingOwnerReferences
    repair_terminal: ObservationRepairTerminalResult | None = field(
        compare=False,
        repr=False,
    )
    fit_once_source_id: str | None
    repair_receipt_id: str
    repair_epoch: Literal[1]
    repair_disposition: CrossSamplePostRepairDisposition
    cross_sample_repair_result_id: str
    cross_sample_repair_application_id: str
    repaired_member_subject_id: str
    repaired_observation: CrossSampleRepairedObservation | None
    pre_repair_trajectory_input_id: str
    post_repair_population_id: str
    post_repair_population: tuple[CrossSampleGroupIdentity, ...]
    post_repair_owners: tuple[SampleLocalMS1Owner, ...]
    post_repair_trajectory: ModeTrackTrajectoryInput
    trajectory_changed: bool
    basis_bytes: bytes
    _mint_token: InitVar[object] = field(kw_only=True, repr=False)

    def __post_init__(self, _mint_token: object) -> None:
        if _mint_token is not _POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input must be minted by CrossSample"
            )
        _validate_post_repair_mode_track_input(self)

def _validate_post_repair_mode_track_input(
    input_value: CrossSamplePostRepairModeTrackInput,
) -> None:
    if type(input_value.processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input requires exact processing owners"
        )
    if input_value.repair_terminal is not None:
        terminal = input_value.repair_terminal
        if (
            type(terminal) is not ObservationRepairTerminalResult
            or type(terminal.authorization) is not ObservationRepairRepairAuthorization
            or type(terminal.receipt) is not ObservationRepairReceipt
            or terminal.receipt.receipt_id != input_value.repair_receipt_id
            or terminal.authorization.processing_owners
            is not input_value.processing_owners
        ):
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input crossed its exact repair terminal"
            )
    try:
        request_id = _request_id(input_value.processing_request_id)
        attempt_id = input_value.processing_owners.attempt.processing_attempt_id
    except (AttributeError, TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input processing owners are invalid"
        ) from exc
    if (
        input_value.processing_owners.request.processing_request_id != request_id
        or input_value.processing_owners.attempt.processing_request_id != request_id
        or input_value.processing_attempt_id != attempt_id
    ):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input crossed request or attempt"
        )
    if input_value.fit_once_source_id is not None and (
        type(input_value.fit_once_source_id) is not str
        or not input_value.fit_once_source_id.startswith(
            "mode-track-fit-once-source:"
        )
    ):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input fit-once source is not exact"
        )
    if type(input_value.repair_epoch) is not int or input_value.repair_epoch != 1:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input repair epoch must be exactly one"
        )
    if (
        not isinstance(input_value.repair_disposition, str)
        or input_value.repair_disposition
        not in {
        "existing_member_reconciled",
        "nonmember_admitted",
        }
    ):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input disposition is not closed"
        )
    for field_name in (
        "repair_receipt_id",
        "cross_sample_repair_result_id",
        "cross_sample_repair_application_id",
        "repaired_member_subject_id",
    ):
        if not isinstance(getattr(input_value, field_name), str) or not getattr(
            input_value, field_name
        ).strip():
            raise CrossSampleReconciliationContractError(
                f"post-repair ModeTrack input {field_name} is required"
            )
    if type(input_value.post_repair_population) is not tuple:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input population must be immutable"
        )
    population = tuple(
        sorted(
            input_value.post_repair_population,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    if population != input_value.post_repair_population:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input population is not canonical"
        )
    try:
        validate_cross_sample_group_population(population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    if any(group.processing_request_id != request_id for group in population):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input population crossed request"
        )
    try:
        population_id = _post_repair_population_id(population)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input population identity is invalid"
        ) from exc
    if input_value.post_repair_population_id != population_id:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input crossed population identity"
        )
    if type(input_value.post_repair_owners) is not tuple:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input owners must be immutable"
        )
    owners = tuple(
        sorted(input_value.post_repair_owners, key=_owner_sort_key)
    )
    if owners != input_value.post_repair_owners or any(
        type(owner) is not SampleLocalMS1Owner for owner in owners
    ):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input owners are not canonical"
        )
    population_member_ids = {
        member.member_subject_id
        for group in population
        for member in group.member_subjects
    }
    owner_member_ids = {_owner_member_id(owner) for owner in owners}
    if input_value.repair_disposition == "existing_member_reconciled":
        if input_value.repaired_observation is not None:
            raise CrossSampleReconciliationContractError(
                "existing-member handoff cannot carry a nonmember observation"
            )
    elif input_value.repaired_observation is None:
        raise CrossSampleReconciliationContractError(
            "admitted nonmember handoff requires its repaired observation"
        )
    if input_value.repaired_observation is not None:
        if type(input_value.repaired_observation) is not CrossSampleRepairedObservation:
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input repaired observation is not exact"
            )
        if (
            input_value.repaired_observation.member_subject.member_subject_id
            != input_value.repaired_member_subject_id
            or input_value.repaired_observation.repair_receipt_id
            != input_value.repair_receipt_id
        ):
            raise CrossSampleReconciliationContractError(
                "post-repair ModeTrack input crossed repaired observation"
            )
        owner_member_ids.add(input_value.repaired_member_subject_id)
    if input_value.repaired_member_subject_id not in population_member_ids:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input repaired member is absent from population"
        )
    if not isinstance(input_value.trajectory_changed, bool):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input trajectory_changed must be boolean"
        )
    trajectory = input_value.post_repair_trajectory
    if type(trajectory) is not ModeTrackTrajectoryInput:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack trajectory must be owner-minted"
        )
    trajectory_member_ids = set(trajectory.member_subject_ids)
    if not trajectory_member_ids.issubset(population_member_ids):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack trajectory escaped the complete population"
        )
    if owner_member_ids != trajectory_member_ids:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input crossed trajectory neighborhood owners"
        )
    if (
        trajectory.processing_request_id != request_id
        or input_value.trajectory_changed
        != (
            input_value.pre_repair_trajectory_input_id
            != trajectory.trajectory_input_id
        )
        or not input_value.trajectory_changed
    ):
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input crossed trajectory state"
        )
    _trajectory_id(input_value.pre_repair_trajectory_input_id)
    if input_value.repair_disposition == "existing_member_reconciled":
        expected_result_id = _existing_member_repair_result_id(
            processing_owners=input_value.processing_owners,
            repair_receipt_id=input_value.repair_receipt_id,
            repair_epoch=input_value.repair_epoch,
            cross_sample_repair_application_id=(
                input_value.cross_sample_repair_application_id
            ),
            repaired_member_subject_id=input_value.repaired_member_subject_id,
            pre_repair_trajectory_input_id=(
                input_value.pre_repair_trajectory_input_id
            ),
            post_repair_trajectory_input_id=trajectory.trajectory_input_id,
            post_repair_population_id=input_value.post_repair_population_id,
            post_repair_owner_ids=tuple(owner.owner_id for owner in owners),
        )
        if input_value.cross_sample_repair_result_id != expected_result_id:
            raise CrossSampleReconciliationContractError(
                "existing-member handoff result identity is not deterministic"
            )
    expected_basis = _post_repair_mode_track_input_basis(
        processing_request_id=input_value.processing_request_id,
        processing_attempt_id=input_value.processing_attempt_id,
        processing_owners=input_value.processing_owners,
        fit_once_source_id=input_value.fit_once_source_id,
        repair_receipt_id=input_value.repair_receipt_id,
        repair_epoch=input_value.repair_epoch,
        repair_disposition=input_value.repair_disposition,
        cross_sample_repair_result_id=input_value.cross_sample_repair_result_id,
        cross_sample_repair_application_id=(
            input_value.cross_sample_repair_application_id
        ),
        repaired_member_subject_id=input_value.repaired_member_subject_id,
        repaired_observation=input_value.repaired_observation,
        pre_repair_trajectory_input_id=(
            input_value.pre_repair_trajectory_input_id
        ),
        post_repair_population_id=input_value.post_repair_population_id,
        post_repair_population=input_value.post_repair_population,
        post_repair_owners=input_value.post_repair_owners,
        post_repair_trajectory=input_value.post_repair_trajectory,
        trajectory_changed=input_value.trajectory_changed,
    )
    if input_value.basis_bytes != expected_basis:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input basis bytes are not exact"
        )
    try:
        validate_typed_identity_basis(
            input_value.post_repair_input_id,
            prefix=_POST_REPAIR_MODE_TRACK_INPUT_PREFIX,
            basis_bytes=expected_basis,
            field="post-repair ModeTrack input_id",
        )
    except ValueError as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc


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
    processing_owners: ProcessingOwnerReferences,
    population: Sequence[CrossSampleGroupIdentity],
    application: CrossSampleSuccessorApplication,
    owners: Sequence[SampleLocalMS1Owner],
    classified_edges: Sequence[CrossSampleNeighborhoodEdge],
    anchors: Sequence[ModeTrackSupportAnchor],
    injection_order: InjectionOrderInput,
    evidence_profile: ModeTrackEvidenceProfile,
    budget: CrossSampleNeighborhoodBudget,
    fit_once_source_id: str | None = None,
) -> CrossSamplePostMT0Context:
    """Build and seal the trajectory snapshot for a post-MT0 state."""

    request_id = _request_id(processing_request_id)
    if type(processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "post-MT0 context requires exact processing owners"
        )
    if (
        processing_owners.request.processing_request_id != request_id
        or processing_owners.attempt.processing_request_id != request_id
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 context crossed processing request or attempt"
        )
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
        processing_owners=processing_owners,
        population=population_tuple,
        application=application,
        owners=owners_tuple,
        classified_edges=edges_tuple,
        anchors=anchors_tuple,
        injection_order=injection_tuple,
        evidence_profile=evidence_profile,
        budget=budget,
        trajectory_input=trajectory_input,
        fit_once_source_id=fit_once_source_id,
    )


def apply_mode_track_refinement_run_for_workflow(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    run: ModeTrackRefinementRun,
) -> CrossSamplePostMT0Context:
    """Atomically apply one complete owner-produced MT0 run."""

    from xic_extractor.alignment.mode_track.assignment import (
        ModeTrackContractError,
        validate_mode_track_fit_once_source_for_consumption,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        _REFINEMENT_RUN_MINT_TOKEN,
        ModeTrackRefinementContractError,
        ModeTrackRefinementRun,
        ModeTrackRefinementTransaction,
    )

    if type(processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "workflow MT0 requires exact processing owners"
        )
    if type(run) is not ModeTrackRefinementRun:
        raise CrossSampleReconciliationContractError(
            "workflow MT0 requires an exact ModeTrack refinement run"
        )
    try:
        run.__post_init__(_REFINEMENT_RUN_MINT_TOKEN)
    except ModeTrackRefinementContractError as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    try:
        validate_mode_track_fit_once_source_for_consumption(run.fit_once_source)
    except (ModeTrackContractError, TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    if (
        run.fit_once_source.processing_owners is not processing_owners
        or run.fit_once_source.family_context is not run.family_context
        or run.fit_once_source.model_set is not run.model_set
        or run.fit_once_source.initial_assignment_batches
        != run.assignment_batches
    ):
        raise CrossSampleReconciliationContractError(
            "ModeTrack run crossed its exact fit-once source"
        )
    capture_tuple = tuple(captures)
    if not capture_tuple or any(
        type(capture) is not SampleLocalCapture for capture in capture_tuple
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures do not close exact owner results"
        )
    capture_scope = capture_tuple[0].scope
    if type(capture_scope) not in {TargetSearchScope, UntargetedSearchScope} or any(
        capture.scope is not capture_scope for capture in capture_tuple
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures crossed their exact SearchScope"
        )
    candidate_ids = tuple(capture.candidate_id for capture in capture_tuple)
    declared_candidate_ids = capture_scope.declared_candidate_ids
    if (
        candidate_ids != declared_candidate_ids
        or len(candidate_ids) != len(set(candidate_ids))
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures do not preserve the declared candidate order"
        )
    for capture in capture_tuple:
        capture.__post_init__()
    selected_ids = tuple(
        sorted(
            capture.selected_hypothesis.hypothesis_id
            for capture in capture_tuple
            if capture.selected_hypothesis is not None
        )
    )
    owner_ids = tuple(sorted(owner.owner_id for owner in run.owners))
    source_by_id = {
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for sample_source in run.fit_once_source.sample_sources
        for candidate in sample_source.candidates
    }
    if (
        len(selected_ids) != len(set(selected_ids))
        or not set(selected_ids).issubset(source_by_id)
        or len(owner_ids) != len(set(owner_ids))
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures cross the exact retained candidate population"
        )
    captured_by_id = {
        capture.selected_hypothesis.hypothesis_id: capture.selected_hypothesis
        for capture in capture_tuple
        if capture.selected_hypothesis is not None
    }
    if any(
        captured.integration is not source_by_id[hypothesis_id].integration
        for hypothesis_id, captured in captured_by_id.items()
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures crossed exact retained physical integration"
        )
    if not set(owner_ids).issubset(source_by_id) or any(
        owner.selected_integration is not source_by_id[owner.owner_id].integration
        or owner.owner_apex_rt != source_by_id[owner.owner_id].integration.rt_apex_min
        or owner.owner_peak_start_rt
        != source_by_id[owner.owner_id].integration.rt_left_min
        or owner.owner_peak_end_rt
        != source_by_id[owner.owner_id].integration.rt_right_min
        or owner.owner_area
        != source_by_id[owner.owner_id].integration.area_raw_counts_seconds
        or owner.owner_height != source_by_id[owner.owner_id].integration.height_raw
        for owner in run.owners
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 source crossed exact owner physical integration"
        )
    try:
        predecessors = validate_cross_sample_group_population(run.predecessors)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    family_predecessors = tuple(
        validate_cross_sample_peak_group_identity(track, require_canonical=True)
        for track in run.family_context.mode_tracks
    )
    if any(group is None for group in family_predecessors) or tuple(
        sorted(
            (group for group in family_predecessors if group is not None),
            key=lambda group: group.group_hypothesis_id,
        )
    ) != predecessors:
        raise CrossSampleReconciliationContractError(
            "ModeTrack run predecessor population crossed Family context"
        )
    transaction_tuple = run.transactions
    if not transaction_tuple or any(
        type(transaction) is not ModeTrackRefinementTransaction
        for transaction in transaction_tuple
    ):
        raise CrossSampleReconciliationContractError(
            "ModeTrack run requires complete typed component transactions"
        )
    transaction_predecessor_ids = tuple(
        group.group_hypothesis_id
        for transaction in transaction_tuple
        for group in transaction.application_context.predecessors
    )
    if transaction_predecessor_ids != tuple(
        group.group_hypothesis_id for group in predecessors
    ) or len(transaction_predecessor_ids) != len(set(transaction_predecessor_ids)):
        raise CrossSampleReconciliationContractError(
            "ModeTrack transactions do not close canonical predecessor order"
        )
    population = predecessors
    for transaction in transaction_tuple:
        proposal = transaction.proposal
        component_member_ids = tuple(
            sorted(
                member.member_subject_id
                for predecessor in transaction.application_context.predecessors
                for member in predecessor.member_subjects
            )
        )
        expected_component_edges = tuple(
            edge
            for edge in run.classified_edges
            if edge.left_member_subject_id in component_member_ids
            and edge.right_member_subject_id in component_member_ids
        )
        if (
            proposal.trajectory_input_id
            != transaction.trajectory_input.trajectory_input_id
            or transaction.trajectory_input.member_subject_ids
            != component_member_ids
            or transaction.application_context.classified_edges
            != expected_component_edges
            or transaction.application_context.budget != run.budget
        ):
            raise CrossSampleReconciliationContractError(
                "ModeTrack transaction crossed its exact component context"
            )
        if type(proposal) is AcceptedModeTrackRefinement:
            try:
                application = apply_accepted_mode_track_refinement(
                    trajectory_input=transaction.trajectory_input,
                    proposal=proposal,
                    application_context=transaction.application_context,
                    population=population,
                )
            except (TypeError, ValueError, KeyError) as exc:
                raise CrossSampleReconciliationContractError(str(exc)) from exc
            if application.status != "assessable":
                raise CrossSampleReconciliationContractError(
                    "accepted MT0 component became unassessable during application"
                )
            successors = application.successors
        elif type(proposal) in {
            RejectedModeTrackRefinement,
            InsufficientModeTrackTraining,
            UnassessableModeTrackRefinement,
        }:
            successors = transaction.application_context.predecessors
        else:
            raise CrossSampleReconciliationContractError(
                "ModeTrack transaction proposal variant is not closed"
            )
        population = _final_population_after_application(
            before=population,
            predecessors=transaction.application_context.predecessors,
            successors=successors,
        )
    lineage = _expected_cross_sample_lineage(
        predecessors=predecessors,
        successors=population,
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=predecessors,
        successors=population,
        lineage=lineage,
        reason=(
            "unchanged_membership_no_lineage"
            if {group.group_hypothesis_id for group in predecessors}
            == {group.group_hypothesis_id for group in population}
            else "membership_changed"
        ),
    )
    return build_cross_sample_post_mt0_context(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_owners=processing_owners,
        population=population,
        application=application,
        owners=run.owners,
        classified_edges=run.classified_edges,
        anchors=run.anchors,
        injection_order=run.injection_order,
        evidence_profile=run.evidence_profile,
        budget=run.budget,
        fit_once_source_id=run.fit_once_source.fit_once_source_id,
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
    candidate, receipt, evidence, current_predecessor = _validate_candidate_terminal(
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
    current_member_ids = {
        member.member_subject_id for member in current_predecessor.member_subjects
    }
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
                if _owner_member_id(owner) in current_member_ids
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
                predecessor=current_predecessor,
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
        trajectory_member_ids = {
            member.member_subject_id
            for member in application.successor.member_subjects
        }
        classified_edges = tuple(
            sorted(
                (
                    edge
                    for edge in combined_edges
                    if edge.left_member_subject_id in trajectory_member_ids
                    and edge.right_member_subject_id in trajectory_member_ids
                ),
                key=_edge_sort_key,
            )
        )
        trajectory_owners = tuple(
            owner
            for owner in final_owners
            if _owner_member_id(owner) in trajectory_member_ids
        )
        trajectory_owner_ids = {owner.owner_id for owner in trajectory_owners}
        injection_order = _post_repair_injection_order(
            context=context,
            candidate=candidate,
            evidence=evidence,
            trajectory_owners=trajectory_owners,
        )
        try:
            trajectory_input = mint_mode_track_trajectory_input(
                predecessors=(application.successor,),
                owners=(*trajectory_owners, repaired_observation),
                classified_edges=classified_edges,
                # Repair adds no anchor; retained direct-support anchors remain exact.
                anchors=tuple(
                    anchor
                    for anchor in context.anchors
                    if anchor.peak_hypothesis_id in trajectory_owner_ids
                ),
                injection_order=injection_order,
                evidence_profile=context.evidence_profile,
                budget=context.budget,
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise CrossSampleReconciliationContractError(
                f"post-repair trajectory could not close: {exc}"
            ) from exc
        pre_trajectory_input_id = context.trajectory_input.trajectory_input_id
        mode_track_input = _mint_post_repair_mode_track_input(
            processing_owners=context.processing_owners,
            repair_terminal=repair_terminal,
            fit_once_source_id=context.fit_once_source_id,
            repair_receipt_id=receipt.receipt_id,
            repair_epoch=receipt.repair_epoch,
            repair_disposition="nonmember_admitted",
            cross_sample_repair_result_id=typed_identity_digest(
                _NONMEMBER_REPAIR_RESULT_PREFIX,
                _nonmember_repair_result_basis(
                    processing_request_id=context.processing_request_id,
                    repair_terminal=repair_terminal,
                    disposition=disposition,
                    pairwise_edges=candidate_edges,
                    application=application,
                    final_population=final_population,
                    final_owners=final_owners,
                    pre_trajectory_input_id=pre_trajectory_input_id,
                    post_trajectory_input_id=trajectory_input.trajectory_input_id,
                    mode_track_input=None,
                ),
            ),
            cross_sample_repair_application_id=application.application_id,
            repaired_member_subject_id=application.admitted_member_subject_id,
            repaired_observation=repaired_observation,
            pre_repair_trajectory_input_id=pre_trajectory_input_id,
            post_repair_population=final_population,
            post_repair_owners=trajectory_owners,
            post_repair_trajectory=trajectory_input,
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
    repair_receipt_id: str | None
    repair_epoch: int
    repair_outcome: Literal[
        "not_run",
        "multiple_eligible",
        "no_repair",
        "repaired",
    ]
    member_subject_ids: tuple[str, ...]
    trajectory_input_id: str
    trajectory_changed: bool
    repair_authorization_id: str | None = None

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
        if self.repair_receipt_id is not None and (
            not isinstance(self.repair_receipt_id, str)
            or not self.repair_receipt_id.strip()
        ):
            raise CrossSampleReconciliationContractError(
                "repair_receipt_id must be absent or non-empty text"
            )
        if self.repair_authorization_id is not None and (
            not isinstance(self.repair_authorization_id, str)
            or not self.repair_authorization_id.strip()
        ):
            raise CrossSampleReconciliationContractError(
                "repair_authorization_id must be absent or non-empty text"
            )
        if type(self.repair_epoch) is not int or self.repair_epoch not in {0, 1}:
            raise CrossSampleReconciliationContractError(
                "CS2 repair epoch must be zero or one"
            )
        if self.repair_outcome not in {
            "not_run",
            "multiple_eligible",
            "no_repair",
            "repaired",
        }:
            raise CrossSampleReconciliationContractError(
                "CS2 repair outcome is not closed"
            )
        if self.repair_epoch == 0:
            if (
                self.repair_outcome not in {"not_run", "multiple_eligible"}
                or self.repair_authorization_id is None
                or self.repair_receipt_id is not None
                or self.trajectory_changed
            ):
                raise CrossSampleReconciliationContractError(
                    "epoch-zero CS2 state contradicts repair authorization"
                )
        elif (
            self.repair_outcome not in {"no_repair", "repaired"}
            or self.repair_receipt_id is None
        ):
            raise CrossSampleReconciliationContractError(
                "epoch-one CS2 state requires an exact repair receipt"
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
    repair_receipt: ObservationRepairReceipt | None
    repair_epoch: Literal[0, 1]
    cs2_state: CrossSampleReconciledState
    pre_trajectory_input_id: str
    post_trajectory_input_id: str
    mode_track_proposal: CrossSampleProposalOutcome | None
    final_application: CrossSampleSuccessorApplication
    final_population: tuple[CrossSampleGroupIdentity, ...]
    basis_bytes: bytes
    repair_terminal: ObservationRepairTerminalResult | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    mode_track_final_attempt: ModeTrackFinalAttemptResult | None = field(
        default=None,
        compare=False,
        repr=False,
    )
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
        _validate_final_repair_binding(
            terminal=self.repair_terminal,
            receipt=self.repair_receipt,
            repair_epoch=self.repair_epoch,
            state=self.cs2_state,
            post_application=post_application,
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
        if self.cs2_state.repair_receipt_id != (
            None if self.repair_receipt is None else self.repair_receipt.receipt_id
        ):
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
        if not trajectory_changed and self.mode_track_final_attempt is not None:
            raise CrossSampleReconciliationContractError(
                "unchanged trajectory cannot carry an MT1 final attempt"
            )
        if trajectory_changed and (
            self.mode_track_final_attempt is None
            or self.mode_track_final_attempt.proposal is not self.mode_track_proposal
        ):
            raise CrossSampleReconciliationContractError(
                "changed trajectory requires the exact MT1 final attempt"
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
            expected_successors = self.cs2_state.population
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
            proposed_member_ids = {
                member_id for group in proposed_partition for member_id in group
            }
            unaffected_predecessors = tuple(
                group
                for group in self.cs2_state.population
                if not {
                    member.member_subject_id for member in group.member_subjects
                }
                & proposed_member_ids
            )
            if any(
                {
                    member.member_subject_id for member in group.member_subjects
                }
                & proposed_member_ids
                and not {
                    member.member_subject_id for member in group.member_subjects
                }
                <= proposed_member_ids
                for group in self.cs2_state.population
            ):
                raise CrossSampleReconciliationContractError(
                    "accepted MT1 proposal only partially covers a current component"
                )
            if any(
                not any(
                    successor is predecessor
                    for successor in final_application.successors
                )
                for predecessor in unaffected_predecessors
            ):
                raise CrossSampleReconciliationContractError(
                    "accepted MT1 application changed an unaffected component"
                )
            applied_partition = tuple(
                sorted(
                    tuple(
                        sorted(
                            member.member_subject_id for member in group.member_subjects
                        )
                    )
                    for group in final_application.successors
                    if any(
                        member.member_subject_id in proposed_member_ids
                        for member in group.member_subjects
                    )
                )
            )
            if any(
                any(
                    member.member_subject_id in proposed_member_ids
                    for member in group.member_subjects
                )
                and any(
                    member.member_subject_id not in proposed_member_ids
                    for member in group.member_subjects
                )
                for group in final_application.successors
            ):
                raise CrossSampleReconciliationContractError(
                    "accepted MT1 application mixed affected and unaffected components"
                )
            if applied_partition != proposed_partition:
                raise CrossSampleReconciliationContractError(
                    "final application does not bind accepted MT1 proposal"
                )
            expected_population = _final_population_after_application(
                before=self.cs2_state.population,
                predecessors=final_application.predecessors,
                successors=final_application.successors,
            )
            if population != expected_population:
                raise CrossSampleReconciliationContractError(
                    "final population is not the complete atomic successor partition"
                )
        if final_application.predecessors != self.cs2_state.population:
            raise CrossSampleReconciliationContractError(
                "final application does not bind the complete CS2 population"
            )
        expected_basis = _final_receipt_basis(
            processing_request_id=self.processing_request_id,
            post_mt0_application=self.post_mt0_application,
            repair_terminal=self.repair_terminal,
            repair_receipt=self.repair_receipt,
            repair_epoch=self.repair_epoch,
            cs2_state=self.cs2_state,
            pre_trajectory_input_id=self.pre_trajectory_input_id,
            post_trajectory_input_id=self.post_trajectory_input_id,
            mode_track_proposal=self.mode_track_proposal,
            mode_track_final_attempt=self.mode_track_final_attempt,
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
    def repair_receipt_id(self) -> str | None:
        return None if self.repair_receipt is None else self.repair_receipt.receipt_id

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


CrossSamplePostRepairReconciliation: TypeAlias = (
    CrossSampleFinalReceipt | CrossSamplePostRepairModeTrackInput
)


def reconcile_observation_repair(
    *,
    context: CrossSamplePostMT0Context,
    repair_receipt: ObservationRepairReceipt,
) -> CrossSamplePostRepairReconciliation:
    """Close one repair handoff and stage changed trajectories for ModeTrack."""

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
    member_id = repair_receipt.subject.member_subject.member_subject_id
    current_groups = tuple(
        group
        for group in context.population
        if any(
            member.member_subject_id == member_id
            for member in group.member_subjects
        )
    )
    if len(current_groups) != 1:
        raise CrossSampleReconciliationContractError(
            "existing-member repair does not resolve one current component"
        )
    evidence = repair_receipt.evidence
    pre_trajectory, post_trajectory, repaired_owners = (
        _existing_member_component_trajectory_projection(
            context=context,
            current_group=current_groups[0],
            current_member_id=member_id,
            repaired_apex_rt=(None if evidence is None else evidence.rt_apex_min),
        )
    )
    trajectory_changed = (
        post_trajectory.trajectory_input_id
        != pre_trajectory.trajectory_input_id
    )
    if trajectory_changed:
        return _mint_existing_member_post_repair_mode_track_input(
            context=context,
            repair_terminal=None,
            repair_receipt=repair_receipt,
            repaired_owners=repaired_owners,
            pre_trajectory_input_id=pre_trajectory.trajectory_input_id,
            post_trajectory=post_trajectory,
        )
    if repair_receipt.repair_epoch != 1:
        raise CrossSampleReconciliationContractError(
            "existing-member repair receipt must close epoch one"
        )
    repair_epoch = cast(Literal[1], repair_receipt.repair_epoch)
    cs2_state = CrossSampleReconciledState(
        processing_request_id=request_id,
        population=context.population,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=repair_epoch,
        repair_outcome=repair_receipt.outcome,
        member_subject_ids=tuple(
            sorted(
                member.member_subject_id
                for group in context.population
                for member in group.member_subjects
            )
        ),
        trajectory_input_id=context.trajectory_input.trajectory_input_id,
        trajectory_changed=trajectory_changed,
    )
    final_application = _mint_unchanged_successor_application(context)
    return _mint_final_receipt(
        processing_request_id=request_id,
        post_mt0_application=context.application,
        repair_terminal=None,
        repair_receipt=repair_receipt,
        repair_epoch=repair_epoch,
        cs2_state=cs2_state,
        pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        post_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        mode_track_proposal=None,
        mode_track_final_attempt=None,
        final_application=final_application,
        final_population=context.population,
    )


def _mint_final_receipt(
    *,
    processing_request_id: str,
    post_mt0_application: CrossSampleSuccessorApplication,
    repair_terminal: ObservationRepairTerminalResult | None,
    repair_receipt: ObservationRepairReceipt | None,
    repair_epoch: Literal[0, 1],
    cs2_state: CrossSampleReconciledState,
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_proposal: CrossSampleProposalOutcome | None,
    mode_track_final_attempt: ModeTrackFinalAttemptResult | None,
    final_application: CrossSampleSuccessorApplication,
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleFinalReceipt:
    basis = _final_receipt_basis(
        processing_request_id=processing_request_id,
        post_mt0_application=post_mt0_application,
        repair_terminal=repair_terminal,
        repair_receipt=repair_receipt,
        repair_epoch=repair_epoch,
        cs2_state=cs2_state,
        pre_trajectory_input_id=pre_trajectory_input_id,
        post_trajectory_input_id=post_trajectory_input_id,
        mode_track_proposal=mode_track_proposal,
        mode_track_final_attempt=mode_track_final_attempt,
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
        repair_terminal=repair_terminal,
        mode_track_final_attempt=mode_track_final_attempt,
        _mint_token=_FINAL_RECEIPT_MINT_TOKEN,
    )


def consume_observation_repair_terminal(
    *,
    context: CrossSamplePostMT0Context,
    terminal: ObservationRepairTerminalResult,
    repair_result: OwnerBackfillSampleResult | None,
    alignment_config: AlignmentConfig,
    drift_lookup: DriftLookupProtocol | None = None,
) -> CrossSamplePostRepairReconciliation:
    """Consume one exact owner terminal without caller-selected routing."""

    _validate_post_mt0_context(context)
    if type(terminal) is not ObservationRepairTerminalResult:
        raise CrossSampleReconciliationContractError(
            "repair handoff requires an exact ObservationRepair terminal"
        )
    authorization = terminal.authorization
    if authorization.processing_owners is not context.processing_owners:
        raise CrossSampleReconciliationContractError(
            "repair terminal crossed the exact processing owners"
        )
    if type(authorization) in {
        ObservationRepairNotRunAuthorization,
        ObservationRepairMultipleEligibleAuthorization,
    }:
        blocked_outcome: Literal["not_run", "multiple_eligible"] = (
            "not_run"
            if type(authorization) is ObservationRepairNotRunAuthorization
            else "multiple_eligible"
        )
        if terminal.receipt is not None or repair_result is not None:
            raise CrossSampleReconciliationContractError(
                "blocked repair terminal cannot carry repair evidence"
            )
        if type(alignment_config) is not AlignmentConfig:
            raise CrossSampleReconciliationContractError(
                "repair terminal consumer requires an exact AlignmentConfig"
            )
        final_application = _mint_unchanged_successor_application(context)
        state = CrossSampleReconciledState(
            processing_request_id=context.processing_request_id,
            population=context.population,
            repair_receipt_id=None,
            repair_epoch=0,
            repair_outcome=blocked_outcome,
            member_subject_ids=tuple(
                sorted(
                    member.member_subject_id
                    for group in context.population
                    for member in group.member_subjects
                )
            ),
            trajectory_input_id=context.trajectory_input.trajectory_input_id,
            trajectory_changed=False,
            repair_authorization_id=authorization.authorization_id,
        )
        return _mint_final_receipt(
            processing_request_id=context.processing_request_id,
            post_mt0_application=context.application,
            repair_terminal=terminal,
            repair_receipt=None,
            repair_epoch=0,
            cs2_state=state,
            pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
            post_trajectory_input_id=context.trajectory_input.trajectory_input_id,
            mode_track_proposal=None,
            mode_track_final_attempt=None,
            final_application=final_application,
            final_population=context.population,
        )
    if type(authorization) is not ObservationRepairRepairAuthorization:
        raise CrossSampleReconciliationContractError(
            "repair terminal authorization variant is not closed"
        )
    if repair_result is None:
        raise CrossSampleReconciliationContractError(
            "repair terminal requires the exact callback result"
        )
    candidate = authorization.candidate
    sample_id = candidate.sample_identity.identity.sample_identity_id
    member_matches = tuple(
        member
        for group in context.population
        for member in group.member_subjects
        if member.canonical_sample_identity_id == sample_id
    )
    if len(member_matches) > 1:
        raise CrossSampleReconciliationContractError(
            "repair candidate has duplicate current membership"
        )
    if member_matches:
        return _consume_existing_member_candidate_terminal(
            context=context,
            terminal=terminal,
            repair_result=repair_result,
        )
    if terminal.receipt is not None and terminal.receipt.outcome == "no_repair":
        return _consume_nonmember_no_repair_candidate_terminal(
            context=context,
            terminal=terminal,
            repair_result=repair_result,
        )
    result = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
        drift_lookup=drift_lookup,
    )
    if result.mode_track_input is not None:
        return result.mode_track_input
    return _close_nonmember_repair_without_mt1(
        context=context,
        terminal=terminal,
        result=result,
    )


def consume_mode_track_final_attempt(
    *,
    context: CrossSamplePostMT0Context,
    post_repair: CrossSamplePostRepairModeTrackInput,
    terminal: ObservationRepairTerminalResult,
    final_attempt: ModeTrackFinalAttemptResult,
) -> CrossSampleFinalReceipt:
    """Consume one exact MT1 owner result and close CrossSample truth."""

    from xic_extractor.alignment.mode_track.refinement import (
        _FINAL_ATTEMPT_MINT_TOKEN,
        AssignedModeTrackFinalAttempt,
        AssignmentUnavailableModeTrackFinalAttempt,
        ModeTrackFinalAttemptContractError,
        RejectedModeTrackFinalAttempt,
        UnassessableModeTrackFinalAttempt,
        UntrainedModeTrackFinalAttempt,
    )

    _validate_post_mt0_context(context)
    _validate_post_repair_mode_track_input(post_repair)
    if type(terminal) is not ObservationRepairTerminalResult:
        raise CrossSampleReconciliationContractError(
            "MT1 closure requires the exact repair terminal"
        )
    receipt = terminal.receipt
    if (
        type(terminal.authorization) is not ObservationRepairRepairAuthorization
        or type(receipt) is not ObservationRepairReceipt
        or post_repair.repair_terminal is not terminal
        or receipt.receipt_id != post_repair.repair_receipt_id
        or terminal.authorization.processing_owners is not context.processing_owners
        or post_repair.processing_owners is not context.processing_owners
    ):
        raise CrossSampleReconciliationContractError(
            "MT1 closure requires the exact repair terminal object, receipt, and owners"
        )
    result_types = {
        AssignedModeTrackFinalAttempt,
        UntrainedModeTrackFinalAttempt,
        AssignmentUnavailableModeTrackFinalAttempt,
        RejectedModeTrackFinalAttempt,
        UnassessableModeTrackFinalAttempt,
    }
    if type(final_attempt) not in result_types:
        raise CrossSampleReconciliationContractError(
            "MT1 closure requires an exact sealed final attempt"
        )
    try:
        final_attempt.__post_init__(_FINAL_ATTEMPT_MINT_TOKEN)
    except ModeTrackFinalAttemptContractError as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    binding = final_attempt.binding
    if (
        context.fit_once_source_id is None
        or post_repair.fit_once_source_id is None
        or post_repair.fit_once_source_id != context.fit_once_source_id
        or binding.fit_once_source_id != context.fit_once_source_id
        or binding.processing_request_id != context.processing_request_id
        or binding.processing_attempt_id
        != context.processing_owners.attempt.processing_attempt_id
        or binding.repair_receipt_id != receipt.receipt_id
        or binding.cross_sample_post_repair_input_id
        != post_repair.post_repair_input_id
        or binding.cross_sample_repair_result_id
        != post_repair.cross_sample_repair_result_id
        or binding.post_repair_population_id
        != post_repair.post_repair_population_id
        or binding.pre_trajectory_input_id
        != post_repair.pre_repair_trajectory_input_id
        or binding.post_trajectory_input_id
        != post_repair.post_repair_trajectory.trajectory_input_id
    ):
        raise CrossSampleReconciliationContractError(
            "MT1 final attempt crossed its complete CrossSample binding"
        )
    proposal = final_attempt.proposal
    final_application, final_population = _apply_final_attempt_population(
        post_repair=post_repair,
        proposal=proposal,
    )
    state = _repair_state(
        context=context,
        terminal=terminal,
        population=post_repair.post_repair_population,
        trajectory_input_id=post_repair.post_repair_trajectory.trajectory_input_id,
        trajectory_changed=True,
    )
    return _mint_final_receipt(
        processing_request_id=context.processing_request_id,
        post_mt0_application=context.application,
        repair_terminal=terminal,
        repair_receipt=receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=post_repair.pre_repair_trajectory_input_id,
        post_trajectory_input_id=post_repair.post_repair_trajectory.trajectory_input_id,
        mode_track_proposal=proposal,
        mode_track_final_attempt=final_attempt,
        final_application=final_application,
        final_population=final_population,
    )


def _consume_existing_member_candidate_terminal(
    *,
    context: CrossSamplePostMT0Context,
    terminal: ObservationRepairTerminalResult,
    repair_result: OwnerBackfillSampleResult,
) -> CrossSamplePostRepairReconciliation:
    authorization = terminal.authorization
    assert type(authorization) is ObservationRepairRepairAuthorization
    candidate = authorization.candidate
    receipt = terminal.receipt
    if (
        type(receipt) is not ObservationRepairReceipt
        or receipt.subject is not candidate
    ):
        raise CrossSampleReconciliationContractError(
            "existing-member terminal crossed candidate or receipt"
        )
    evidence = receipt.evidence
    sample_id = candidate.sample_identity.identity.sample_identity_id
    matches = tuple(
        (group, member)
        for group in context.population
        for member in group.member_subjects
        if member.canonical_sample_identity_id == sample_id
    )
    if len(matches) != 1:
        raise CrossSampleReconciliationContractError(
            "existing-member terminal does not resolve one current member"
        )
    current_group, current_member = matches[0]
    _validate_candidate_successor_lineage(
        context=context,
        candidate=candidate,
        current_group=current_group,
        current_member=current_member,
    )
    if receipt.outcome == "no_repair":
        _validate_no_repair_callback_result(
            result=repair_result,
            candidate=candidate,
            receipt=receipt,
        )
        state = _repair_state(
            context=context,
            terminal=terminal,
            population=context.population,
            trajectory_input_id=context.trajectory_input.trajectory_input_id,
            trajectory_changed=False,
        )
        return _mint_final_receipt(
            processing_request_id=context.processing_request_id,
            post_mt0_application=context.application,
            repair_terminal=terminal,
            repair_receipt=receipt,
            repair_epoch=1,
            cs2_state=state,
            pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
            post_trajectory_input_id=context.trajectory_input.trajectory_input_id,
            mode_track_proposal=None,
            mode_track_final_attempt=None,
            final_application=_mint_unchanged_successor_application(context),
            final_population=context.population,
        )
    if type(evidence) is not ObservationRepairEvidence:
        raise CrossSampleReconciliationContractError(
            "repaired existing-member terminal is missing physical evidence"
        )
    _repair_observation_for_receipt(
        repair_result,
        candidate=candidate,
        receipt=receipt,
        evidence=evidence,
    )
    pre_trajectory, post_trajectory, repaired_owners = (
        _existing_member_component_trajectory_projection(
            context=context,
            current_group=current_group,
            current_member_id=current_member.member_subject_id,
            repaired_apex_rt=evidence.rt_apex_min,
        )
    )
    if (
        post_trajectory.trajectory_input_id
        != pre_trajectory.trajectory_input_id
    ):
        return _mint_existing_member_post_repair_mode_track_input(
            context=context,
            repair_terminal=terminal,
            repair_receipt=receipt,
            repaired_owners=repaired_owners,
            pre_trajectory_input_id=pre_trajectory.trajectory_input_id,
            post_trajectory=post_trajectory,
            repaired_member_subject_id=current_member.member_subject_id,
        )
    state = _repair_state(
        context=context,
        terminal=terminal,
        population=context.population,
        trajectory_input_id=context.trajectory_input.trajectory_input_id,
        trajectory_changed=False,
    )
    return _mint_final_receipt(
        processing_request_id=context.processing_request_id,
        post_mt0_application=context.application,
        repair_terminal=terminal,
        repair_receipt=receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        post_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        mode_track_proposal=None,
        mode_track_final_attempt=None,
        final_application=_mint_unchanged_successor_application(context),
        final_population=context.population,
    )


def _consume_nonmember_no_repair_candidate_terminal(
    *,
    context: CrossSamplePostMT0Context,
    terminal: ObservationRepairTerminalResult,
    repair_result: OwnerBackfillSampleResult,
) -> CrossSampleFinalReceipt:
    candidate, receipt, _current_predecessor = _validate_nonmember_terminal_binding(
        terminal,
        context=context,
    )
    if receipt.outcome != "no_repair" or receipt.evidence is not None:
        raise CrossSampleReconciliationContractError(
            "nonmember no-repair closure requires the exact owner negative"
        )
    _validate_no_repair_callback_result(
        result=repair_result,
        candidate=candidate,
        receipt=receipt,
    )
    state = _repair_state(
        context=context,
        terminal=terminal,
        population=context.population,
        trajectory_input_id=context.trajectory_input.trajectory_input_id,
        trajectory_changed=False,
    )
    return _mint_final_receipt(
        processing_request_id=context.processing_request_id,
        post_mt0_application=context.application,
        repair_terminal=terminal,
        repair_receipt=receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        post_trajectory_input_id=context.trajectory_input.trajectory_input_id,
        mode_track_proposal=None,
        mode_track_final_attempt=None,
        final_application=_mint_unchanged_successor_application(context),
        final_population=context.population,
    )


def _close_nonmember_repair_without_mt1(
    *,
    context: CrossSamplePostMT0Context,
    terminal: ObservationRepairTerminalResult,
    result: CrossSampleNonmemberRepairResult,
) -> CrossSampleFinalReceipt:
    receipt = terminal.receipt
    assert type(receipt) is ObservationRepairReceipt
    state = _repair_state(
        context=context,
        terminal=terminal,
        population=result.final_population,
        trajectory_input_id=result.post_trajectory_input_id,
        trajectory_changed=False,
    )
    return _mint_final_receipt(
        processing_request_id=context.processing_request_id,
        post_mt0_application=context.application,
        repair_terminal=terminal,
        repair_receipt=receipt,
        repair_epoch=1,
        cs2_state=state,
        pre_trajectory_input_id=result.pre_trajectory_input_id,
        post_trajectory_input_id=result.post_trajectory_input_id,
        mode_track_proposal=None,
        mode_track_final_attempt=None,
        final_application=_mint_unchanged_successor_application(context),
        final_population=result.final_population,
    )


def _repair_state(
    *,
    context: CrossSamplePostMT0Context,
    terminal: ObservationRepairTerminalResult,
    population: tuple[CrossSampleGroupIdentity, ...],
    trajectory_input_id: str,
    trajectory_changed: bool,
) -> CrossSampleReconciledState:
    receipt = terminal.receipt
    if type(receipt) is not ObservationRepairReceipt:
        raise CrossSampleReconciliationContractError(
            "repair state requires an exact repair receipt"
        )
    return CrossSampleReconciledState(
        processing_request_id=context.processing_request_id,
        population=population,
        repair_receipt_id=receipt.receipt_id,
        repair_epoch=1,
        repair_outcome=receipt.outcome,
        member_subject_ids=tuple(
            sorted(
                member.member_subject_id
                for group in population
                for member in group.member_subjects
            )
        ),
        trajectory_input_id=trajectory_input_id,
        trajectory_changed=trajectory_changed,
        repair_authorization_id=terminal.authorization.authorization_id,
    )


def _validate_candidate_successor_lineage(
    *,
    context: CrossSamplePostMT0Context,
    candidate: ObservationRepairCandidate,
    current_group: CrossSampleGroupIdentity,
    current_member: CrossSampleGroupMemberSubject,
) -> None:
    predecessor_id = candidate.predecessor_group.group_hypothesis_id
    if current_group.group_hypothesis_id == predecessor_id:
        return
    matching = tuple(
        edge
        for edge in context.application.lineage
        if edge.predecessor_group_hypothesis_id == predecessor_id
        and edge.successor_group_hypothesis_id == current_group.group_hypothesis_id
        and current_member.member_subject_id in edge.member_subject_ids
    )
    if len(matching) != 1:
        raise CrossSampleReconciliationContractError(
            "repair candidate predecessor has no unique current successor lineage"
        )


def _validate_no_repair_callback_result(
    *,
    result: OwnerBackfillSampleResult,
    candidate: ObservationRepairCandidate,
    receipt: ObservationRepairReceipt,
) -> None:
    terminal = receipt.no_repair_terminal
    if (
        type(result) is not OwnerBackfillSampleResult
        or result.sample_stem != candidate.request_identity.sample_stem
        or terminal is None
    ):
        raise CrossSampleReconciliationContractError(
            "no-repair receipt crossed its exact callback result"
        )
    matches = tuple(
        observation
        for observation in result.peak_fact_observations
        if observation.logical_request == candidate.logical_request
        and observation.request_identity == candidate.request_identity
        and observation.work_unit_key == terminal.work_unit_key
    )
    if len(matches) != 1:
        raise CrossSampleReconciliationContractError(
            "no-repair callback result lost its exact live observation"
        )
    observation = matches[0]
    evaluation = observation.physical_evaluation
    if (
        evaluation is None
        or evaluation.answer.physical_answer_id != terminal.physical_answer_id
    ):
        raise CrossSampleReconciliationContractError(
            "no-repair callback result crossed its physical terminal"
        )


def _apply_final_attempt_population(
    *,
    post_repair: CrossSamplePostRepairModeTrackInput,
    proposal: CrossSampleProposalOutcome,
) -> tuple[
    CrossSampleSuccessorApplication,
    tuple[CrossSampleGroupIdentity, ...],
]:
    predecessors = post_repair.post_repair_population
    if type(proposal) is not AcceptedModeTrackRefinement:
        return (
            CrossSampleSuccessorApplication(
                status="assessable",
                predecessors=predecessors,
                successors=predecessors,
                lineage=(),
                reason="unchanged_membership_no_lineage",
            ),
            predecessors,
        )
    component_member_ids = set(
        post_repair.post_repair_trajectory.member_subject_ids
    )
    component_predecessors = tuple(
        group
        for group in predecessors
        if {
            member.member_subject_id for member in group.member_subjects
        }
        & component_member_ids
    )
    if any(
        not {
            member.member_subject_id for member in group.member_subjects
        }
        <= component_member_ids
        for group in component_predecessors
    ):
        raise CrossSampleReconciliationContractError(
            "MT1 trajectory only partially covers a current component"
        )
    member_by_id = {
        member.member_subject_id: member
        for group in component_predecessors
        for member in group.member_subjects
    }
    proposed_ids = tuple(
        member_id
        for member_ids in proposal.successor_member_subject_ids
        for member_id in member_ids
    )
    if (
        tuple(sorted(proposed_ids)) != tuple(sorted(member_by_id))
        or len(proposed_ids) != len(set(proposed_ids))
    ):
        raise CrossSampleReconciliationContractError(
            "accepted MT1 proposal is not a complete current partition"
        )
    exclusions = {
        frozenset((left, right))
        for left, right, _reason in post_repair.post_repair_trajectory.exclusions
    }
    if any(
        frozenset((left, right)) in exclusions
        for member_ids in proposal.successor_member_subject_ids
        for index, left in enumerate(member_ids)
        for right in member_ids[index + 1 :]
    ):
        raise CrossSampleReconciliationContractError(
            "accepted MT1 proposal reopens an excluded edge"
        )
    component_successors = tuple(
        sorted(
            (
                mint_cross_sample_group_identity(
                    processing_request_id=post_repair.processing_request_id,
                    member_subjects=tuple(
                        member_by_id[member_id] for member_id in member_ids
                    ),
                )
                for member_ids in proposal.successor_member_subject_ids
            ),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    successors = tuple(
        sorted(
            tuple(
                group
                for group in predecessors
                if not any(group is component for component in component_predecessors)
            )
            + component_successors,
            key=lambda group: group.group_hypothesis_id,
        )
    )
    lineage = _expected_cross_sample_lineage(
        predecessors=predecessors,
        successors=successors,
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=predecessors,
        successors=successors,
        lineage=lineage,
        reason=(
            "unchanged_membership_no_lineage"
            if {group.group_hypothesis_id for group in predecessors}
            == {group.group_hypothesis_id for group in successors}
            else "membership_changed"
        ),
    )
    return application, successors


def _validate_final_repair_binding(
    *,
    terminal: ObservationRepairTerminalResult | None,
    receipt: ObservationRepairReceipt | None,
    repair_epoch: int,
    state: CrossSampleReconciledState,
    post_application: CrossSampleSuccessorApplication,
) -> None:
    if type(repair_epoch) is not int or repair_epoch not in {0, 1}:
        raise CrossSampleReconciliationContractError(
            "final receipt repair epoch must be an exact integer"
        )
    if terminal is None:
        if type(receipt) is not ObservationRepairReceipt or repair_epoch != 1:
            raise CrossSampleReconciliationContractError(
                "legacy final receipt requires one exact repair receipt"
            )
        _validate_repair_receipt(receipt)
        if receipt.subject.successor_application != post_application:
            raise CrossSampleReconciliationContractError(
                "legacy final receipt crosses post-MT0 application"
            )
        if state.repair_authorization_id is not None:
            raise CrossSampleReconciliationContractError(
                "legacy final receipt cannot invent repair authorization"
            )
        return
    if type(terminal) is not ObservationRepairTerminalResult:
        raise CrossSampleReconciliationContractError(
            "final receipt repair terminal must be exact"
        )
    authorization = terminal.authorization
    if type(authorization) in {
        ObservationRepairNotRunAuthorization,
        ObservationRepairMultipleEligibleAuthorization,
    }:
        if (
            receipt is not None
            or terminal.receipt is not None
            or repair_epoch != 0
            or state.repair_authorization_id != authorization.authorization_id
            or state.repair_receipt_id is not None
            or state.repair_outcome != terminal.state
        ):
            raise CrossSampleReconciliationContractError(
                "final receipt contradicts blocked repair terminal"
            )
        return
    if type(authorization) is not ObservationRepairRepairAuthorization:
        raise CrossSampleReconciliationContractError(
            "final receipt has an unknown repair authorization"
        )
    if (
        type(receipt) is not ObservationRepairReceipt
        or terminal.receipt is not receipt
        or repair_epoch != 1
        or state.repair_authorization_id != authorization.authorization_id
        or state.repair_receipt_id != receipt.receipt_id
        or state.repair_outcome != receipt.outcome
    ):
        raise CrossSampleReconciliationContractError(
            "final receipt crosses exact repair terminal or receipt"
        )


def _validate_post_mt0_context(context: CrossSamplePostMT0Context) -> None:
    if type(context) is not CrossSamplePostMT0Context:
        raise CrossSampleReconciliationContractError(
            "post-MT0 context must be an exact typed CrossSample context"
        )
    request_id = _request_id(context.processing_request_id)
    if type(context.processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "post-MT0 context requires exact processing owners"
        )
    if (
        context.processing_owners.request.processing_request_id != request_id
        or context.processing_owners.attempt.processing_request_id != request_id
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 context crossed processing request or attempt"
        )
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
    if application.successors != population:
        raise CrossSampleReconciliationContractError(
            "post-MT0 population must exactly equal the application successors"
        )
    if context.fit_once_source_id is not None and (
        type(context.fit_once_source_id) is not str
        or not context.fit_once_source_id.startswith("mode-track-fit-once-source:")
    ):
        raise CrossSampleReconciliationContractError(
            "post-MT0 fit-once source identity is not exact"
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

    population = tuple(
        sorted(context.population, key=lambda group: group.group_hypothesis_id)
    )
    return CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=population,
        successors=population,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )


def _validate_nonmember_terminal_binding(
    terminal: ObservationRepairTerminalResult,
    *,
    context: CrossSamplePostMT0Context,
) -> tuple[
    ObservationRepairCandidate,
    ObservationRepairReceipt,
    CrossSampleGroupIdentity,
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
    if (
        receipt.authorization_id != authorization.authorization_id
        or receipt.candidate_id != candidate.candidate_id
        or receipt.repair_epoch != candidate.repair_epoch
        or receipt.repair_epoch != 1
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember terminal crossed authorization, candidate, or epoch"
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
    current_predecessors = tuple(
        group
        for group in context.population
        if group.group_hypothesis_id
        == candidate.predecessor_group.group_hypothesis_id
    )
    if current_predecessors:
        if (
            len(current_predecessors) != 1
            or current_predecessors[0] is not candidate.predecessor_group
        ):
            raise CrossSampleReconciliationContractError(
                "repair candidate is not bound to the exact current group"
            )
    else:
        successor_ids = {
            edge.successor_group_hypothesis_id
            for edge in context.application.lineage
            if edge.predecessor_group_hypothesis_id
            == candidate.predecessor_group.group_hypothesis_id
        }
        current_predecessors = tuple(
            group
            for group in context.population
            if group.group_hypothesis_id in successor_ids
        )
    if len(current_predecessors) != 1:
        raise CrossSampleReconciliationContractError(
            "repair candidate predecessor has no unique current successor"
        )
    current_predecessor = current_predecessors[0]
    if not any(
        group is current_predecessor for group in context.application.successors
    ):
        raise CrossSampleReconciliationContractError(
            "repair candidate is not bound to the exact current group application"
        )
    sample_identity_id = candidate.sample_identity.identity.sample_identity_id
    if any(
        member.canonical_sample_identity_id == sample_identity_id
        for member in current_predecessor.member_subjects
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
    return candidate, receipt, current_predecessor


def _validate_candidate_terminal(
    terminal: ObservationRepairTerminalResult,
    *,
    context: CrossSamplePostMT0Context,
) -> tuple[
    ObservationRepairCandidate,
    ObservationRepairReceipt,
    ObservationRepairEvidence,
    CrossSampleGroupIdentity,
]:
    candidate, receipt, current_predecessor = _validate_nonmember_terminal_binding(
        terminal,
        context=context,
    )
    evidence = receipt.evidence
    if (
        receipt.outcome != "repaired"
        or type(evidence) is not ObservationRepairEvidence
        or evidence.member_subject_id is not None
    ):
        raise CrossSampleReconciliationContractError(
            "nonmember admission requires one successful membership-neutral receipt"
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
    return candidate, receipt, evidence, current_predecessor


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
    trajectory_owners: tuple[SampleLocalMS1Owner, ...],
) -> dict[str, int]:
    trajectory_sample_stems = {owner.sample_stem for owner in trajectory_owners}
    injection_order = {
        sample_stem: injection_order
        for sample_stem, injection_order in context.injection_order
        if sample_stem in trajectory_sample_stems
    }
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


def _existing_member_component_trajectory_projection(
    *,
    context: CrossSamplePostMT0Context,
    current_group: CrossSampleGroupIdentity,
    current_member_id: str,
    repaired_apex_rt: float | None,
) -> tuple[
    ModeTrackTrajectoryInput,
    ModeTrackTrajectoryInput,
    tuple[SampleLocalMS1Owner, ...],
]:
    component_member_ids = {
        member.member_subject_id for member in current_group.member_subjects
    }
    component_owners = tuple(
        owner
        for owner in context.owners
        if _owner_member_id(owner) in component_member_ids
    )
    if len(component_owners) != len(component_member_ids):
        raise CrossSampleReconciliationContractError(
            "existing-member component does not close its exact owners"
        )
    targets = tuple(
        owner
        for owner in component_owners
        if _owner_member_id(owner) == current_member_id
    )
    if len(targets) != 1:
        raise CrossSampleReconciliationContractError(
            "existing-member component does not resolve one exact owner"
        )
    target = targets[0]
    repaired_owners = tuple(
        replace(target, owner_apex_rt=repaired_apex_rt)
        if owner is target and repaired_apex_rt is not None
        else owner
        for owner in component_owners
    )
    component_edges = tuple(
        edge
        for edge in context.classified_edges
        if edge.left_member_subject_id in component_member_ids
        and edge.right_member_subject_id in component_member_ids
    )
    component_owner_ids = {owner.owner_id for owner in component_owners}
    component_anchors = tuple(
        anchor
        for anchor in context.anchors
        if anchor.peak_hypothesis_id in component_owner_ids
    )
    component_sample_stems = {owner.sample_stem for owner in component_owners}
    component_injection_order = tuple(
        (sample_stem, injection_order)
        for sample_stem, injection_order in context.injection_order
        if sample_stem in component_sample_stems
    )
    component_application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(current_group,),
        successors=(current_group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    pre_trajectory = _mint_context_trajectory(
        request_id=context.processing_request_id,
        application=component_application,
        owners=component_owners,
        classified_edges=component_edges,
        anchors=component_anchors,
        injection_order=component_injection_order,
        evidence_profile=context.evidence_profile,
        budget=context.budget,
    )
    post_trajectory = _mint_context_trajectory(
        request_id=context.processing_request_id,
        application=component_application,
        owners=repaired_owners,
        classified_edges=component_edges,
        anchors=component_anchors,
        injection_order=component_injection_order,
        evidence_profile=context.evidence_profile,
        budget=context.budget,
    )
    return pre_trajectory, post_trajectory, repaired_owners


def _owner_sort_key(owner: SampleLocalMS1Owner) -> tuple[str, str]:
    return (_owner_member_id(owner), owner.owner_id)


def _processing_owners_reference(
    processing_owners: ProcessingOwnerReferences,
) -> dict[str, object]:
    if type(processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "processing owners must be an exact owner reference"
        )
    return {
        "request": {
            "processing_request_id": processing_owners.request.processing_request_id,
            "acquisition_bindings": [
                {
                    "acquisition_source_id": binding.acquisition_source_id,
                    "adapter_input_key": binding.adapter_input_key,
                    "injection_order": binding.injection_order,
                }
                for binding in processing_owners.request.acquisition_bindings
            ],
        },
        "attempt": {
            "processing_attempt_id": processing_owners.attempt.processing_attempt_id,
            "retry_of_attempt_id": (
                None
                if processing_owners.attempt.retry_of is None
                else processing_owners.attempt.retry_of.processing_attempt_id
            ),
        },
        "sample_snapshot_id": processing_owners.sample_snapshot.snapshot_id,
        "sample_resolutions": [
            {
                "lookup_token": resolution.lookup_token,
                "sample_identity_id": resolution.identity.sample_identity_id,
                "raw_stem": resolution.metadata.raw_stem,
                "injection_order": resolution.metadata.injection_order,
            }
            for resolution in processing_owners.sample_resolutions
        ],
    }


def _post_repair_population_id(
    population: tuple[CrossSampleGroupIdentity, ...],
) -> str:
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_post_repair_population_v1",
            "groups": [_group_reference(group) for group in population],
        }
    )
    return typed_identity_digest(_POST_REPAIR_POPULATION_PREFIX, basis)


def _post_repair_application_id(
    application: CrossSampleSuccessorApplication,
) -> str:
    application = _exact_application(
        application,
        field="post-repair CrossSample application",
    )
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_post_repair_application_v1",
            "application": _application_reference(application),
        }
    )
    return typed_identity_digest(_POST_REPAIR_APPLICATION_PREFIX, basis)


def _existing_member_repair_result_id(
    *,
    processing_owners: ProcessingOwnerReferences,
    repair_receipt_id: str,
    repair_epoch: int,
    cross_sample_repair_application_id: str,
    repaired_member_subject_id: str,
    pre_repair_trajectory_input_id: str,
    post_repair_trajectory_input_id: str,
    post_repair_population_id: str,
    post_repair_owner_ids: tuple[str, ...],
) -> str:
    basis = canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_existing_member_repair_v1",
            "processing_owners": _processing_owners_reference(processing_owners),
            "repair_receipt_id": repair_receipt_id,
            "repair_epoch": repair_epoch,
            "cross_sample_repair_application_id": cross_sample_repair_application_id,
            "repaired_member_subject_id": repaired_member_subject_id,
            "pre_repair_trajectory_input_id": pre_repair_trajectory_input_id,
            "post_repair_trajectory_input_id": post_repair_trajectory_input_id,
            "post_repair_population_id": post_repair_population_id,
            "post_repair_owner_ids": list(post_repair_owner_ids),
        }
    )
    return typed_identity_digest(_EXISTING_MEMBER_REPAIR_RESULT_PREFIX, basis)


def _post_repair_mode_track_input_basis(
    *,
    processing_request_id: str,
    processing_attempt_id: str,
    processing_owners: ProcessingOwnerReferences,
    fit_once_source_id: str | None,
    repair_receipt_id: str,
    repair_epoch: int,
    repair_disposition: CrossSamplePostRepairDisposition,
    cross_sample_repair_result_id: str,
    cross_sample_repair_application_id: str,
    repaired_member_subject_id: str,
    repaired_observation: CrossSampleRepairedObservation | None,
    pre_repair_trajectory_input_id: str,
    post_repair_population_id: str,
    post_repair_population: tuple[CrossSampleGroupIdentity, ...],
    post_repair_owners: tuple[SampleLocalMS1Owner, ...],
    post_repair_trajectory: ModeTrackTrajectoryInput,
    trajectory_changed: bool,
) -> bytes:
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_post_repair_mode_track_input_v3",
            "processing_request_id": processing_request_id,
            "processing_attempt_id": processing_attempt_id,
            "processing_owners": _processing_owners_reference(processing_owners),
            "fit_once_source_id": fit_once_source_id,
            "repair_receipt_id": repair_receipt_id,
            "repair_epoch": repair_epoch,
            "repair_disposition": repair_disposition,
            "cross_sample_repair_result_id": cross_sample_repair_result_id,
            "cross_sample_repair_application_id": cross_sample_repair_application_id,
            "repaired_member_subject_id": repaired_member_subject_id,
            "repaired_observation_id": (
                None
                if repaired_observation is None
                else repaired_observation.observation_id
            ),
            "pre_repair_trajectory_input_id": pre_repair_trajectory_input_id,
            "post_repair_population_id": post_repair_population_id,
            "post_repair_population": [
                _group_reference(group) for group in post_repair_population
            ],
            "post_repair_owner_ids": [
                owner.owner_id for owner in post_repair_owners
            ],
            "post_repair_trajectory_id": post_repair_trajectory.trajectory_input_id,
            "trajectory_changed": trajectory_changed,
        }
    )


def _mint_post_repair_mode_track_input(
    *,
    processing_owners: ProcessingOwnerReferences,
    repair_terminal: ObservationRepairTerminalResult | None,
    fit_once_source_id: str | None,
    repair_receipt_id: str,
    repair_epoch: int,
    repair_disposition: CrossSamplePostRepairDisposition,
    cross_sample_repair_result_id: str,
    cross_sample_repair_application_id: str,
    repaired_member_subject_id: str,
    repaired_observation: CrossSampleRepairedObservation | None,
    pre_repair_trajectory_input_id: str,
    post_repair_population: tuple[CrossSampleGroupIdentity, ...],
    post_repair_owners: tuple[SampleLocalMS1Owner, ...],
    post_repair_trajectory: ModeTrackTrajectoryInput,
) -> CrossSamplePostRepairModeTrackInput:
    if type(processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "post-repair ModeTrack input requires exact processing owners"
        )
    request_id = _request_id(processing_owners.request.processing_request_id)
    attempt_id = processing_owners.attempt.processing_attempt_id
    population = tuple(
        sorted(post_repair_population, key=lambda group: group.group_hypothesis_id)
    )
    owners = tuple(sorted(post_repair_owners, key=_owner_sort_key))
    population_id = _post_repair_population_id(population)
    trajectory_changed = pre_repair_trajectory_input_id != (
        post_repair_trajectory.trajectory_input_id
    )
    basis = _post_repair_mode_track_input_basis(
        processing_request_id=request_id,
        processing_attempt_id=attempt_id,
        processing_owners=processing_owners,
        fit_once_source_id=fit_once_source_id,
        repair_receipt_id=repair_receipt_id,
        repair_epoch=repair_epoch,
        repair_disposition=repair_disposition,
        cross_sample_repair_result_id=cross_sample_repair_result_id,
        cross_sample_repair_application_id=cross_sample_repair_application_id,
        repaired_member_subject_id=repaired_member_subject_id,
        repaired_observation=repaired_observation,
        pre_repair_trajectory_input_id=pre_repair_trajectory_input_id,
        post_repair_population_id=population_id,
        post_repair_population=population,
        post_repair_owners=owners,
        post_repair_trajectory=post_repair_trajectory,
        trajectory_changed=trajectory_changed,
    )
    return CrossSamplePostRepairModeTrackInput(
        post_repair_input_id=typed_identity_digest(
            _POST_REPAIR_MODE_TRACK_INPUT_PREFIX,
            basis,
        ),
        processing_request_id=request_id,
        processing_attempt_id=attempt_id,
        processing_owners=processing_owners,
        repair_terminal=repair_terminal,
        fit_once_source_id=fit_once_source_id,
        repair_receipt_id=repair_receipt_id,
        repair_epoch=repair_epoch,  # type: ignore[arg-type]
        repair_disposition=repair_disposition,
        cross_sample_repair_result_id=cross_sample_repair_result_id,
        cross_sample_repair_application_id=cross_sample_repair_application_id,
        repaired_member_subject_id=repaired_member_subject_id,
        repaired_observation=repaired_observation,
        pre_repair_trajectory_input_id=pre_repair_trajectory_input_id,
        post_repair_population_id=population_id,
        post_repair_population=population,
        post_repair_owners=owners,
        post_repair_trajectory=post_repair_trajectory,
        trajectory_changed=trajectory_changed,
        basis_bytes=basis,
        _mint_token=_POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN,
    )


def _mint_existing_member_post_repair_mode_track_input(
    *,
    context: CrossSamplePostMT0Context,
    repair_terminal: ObservationRepairTerminalResult | None,
    repair_receipt: ObservationRepairReceipt,
    repaired_owners: tuple[SampleLocalMS1Owner, ...],
    pre_trajectory_input_id: str,
    post_trajectory: ModeTrackTrajectoryInput,
    repaired_member_subject_id: str | None = None,
) -> CrossSamplePostRepairModeTrackInput:
    evidence = repair_receipt.evidence
    member_subject_id = (
        None if evidence is None else evidence.member_subject_id
    )
    if repaired_member_subject_id is not None:
        member_subject_id = repaired_member_subject_id
    if evidence is None or member_subject_id is None:
        raise CrossSampleReconciliationContractError(
            "changed existing-member repair is missing its exact member evidence"
        )
    application_id = _post_repair_application_id(context.application)
    population = tuple(
        sorted(context.population, key=lambda group: group.group_hypothesis_id)
    )
    owner_ids = tuple(owner.owner_id for owner in repaired_owners)
    result_id = _existing_member_repair_result_id(
        processing_owners=context.processing_owners,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=repair_receipt.repair_epoch,
        cross_sample_repair_application_id=application_id,
        repaired_member_subject_id=member_subject_id,
        pre_repair_trajectory_input_id=pre_trajectory_input_id,
        post_repair_trajectory_input_id=post_trajectory.trajectory_input_id,
        post_repair_population_id=_post_repair_population_id(population),
        post_repair_owner_ids=owner_ids,
    )
    return _mint_post_repair_mode_track_input(
        processing_owners=context.processing_owners,
        repair_terminal=repair_terminal,
        fit_once_source_id=context.fit_once_source_id,
        repair_receipt_id=repair_receipt.receipt_id,
        repair_epoch=repair_receipt.repair_epoch,
        repair_disposition="existing_member_reconciled",
        cross_sample_repair_result_id=result_id,
        cross_sample_repair_application_id=application_id,
        repaired_member_subject_id=member_subject_id,
        repaired_observation=None,
        pre_repair_trajectory_input_id=pre_trajectory_input_id,
        post_repair_population=population,
        post_repair_owners=repaired_owners,
        post_repair_trajectory=post_trajectory,
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
    current_group_member_sets = tuple(
        {
            member.member_subject_id
            for member in group.member_subjects
            if member.member_subject_id != expected_member.member_subject_id
        }
        for group in result.final_population
        if observed_predecessor_ids.issubset(
            {member.member_subject_id for member in group.member_subjects}
        )
    )
    if (
        len(current_group_member_sets) != 1
        or len(result.pairwise_edges) != len(current_group_member_sets[0])
        or len(observed_pairs) != len(result.pairwise_edges)
        or observed_predecessor_ids != current_group_member_sets[0]
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
        ):
            raise CrossSampleReconciliationContractError(
                "admitted repair result crossed application or ModeTrack input"
            )
        repaired_observation = mode_input.repaired_observation
        if type(repaired_observation) is not CrossSampleRepairedObservation:
            raise CrossSampleReconciliationContractError(
                "admitted repair result crossed application or ModeTrack input"
            )
        trajectory_member_ids = {
            member.member_subject_id for member in application.successor.member_subjects
        }
        expected_mode_track_owners = tuple(
            owner
            for owner in result.final_owners
            if _owner_member_id(owner) in trajectory_member_ids
        )
        if (
            application.predecessor.processing_request_id != request_id
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
            or mode_input.processing_owners is not candidate.processing_owners
            or mode_input.processing_request_id != request_id
            or mode_input.processing_attempt_id
            != candidate.processing_owners.attempt.processing_attempt_id
            or mode_input.repair_receipt_id != receipt.receipt_id
            or mode_input.repair_epoch != receipt.repair_epoch
            or mode_input.repair_disposition != "nonmember_admitted"
            or mode_input.cross_sample_repair_result_id != result.result_id
            or mode_input.cross_sample_repair_application_id
            != application.application_id
            or mode_input.repaired_member_subject_id
            != expected_member.member_subject_id
            or repaired_observation.member_subject != expected_member
            or mode_input.pre_repair_trajectory_input_id != pre_id
            or mode_input.post_repair_population != result.final_population
            or mode_input.post_repair_owners != expected_mode_track_owners
            or mode_input.post_repair_trajectory.trajectory_input_id != post_id
            or not mode_input.trajectory_changed
        ):
            raise CrossSampleReconciliationContractError(
                "admitted repair result crossed application or ModeTrack input"
            )
        if pre_id == post_id:
            raise CrossSampleReconciliationContractError(
                "admitted repair result did not change trajectory identity"
            )
        expected_owner_ids = {
            member.member_subject_id
            for group in result.final_population
            for member in group.member_subjects
            if member.member_subject_id != expected_member.member_subject_id
        }
    else:
        if (
            result.application is not None
            or result.mode_track_input is not None
            or pre_id != post_id
        ):
            raise CrossSampleReconciliationContractError(
                "non-admitted repair result changed membership or trajectory"
            )
        expected_owner_ids = {
            member.member_subject_id
            for group in result.final_population
            for member in group.member_subjects
        }
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
    repair_terminal: ObservationRepairTerminalResult | None,
    repair_receipt: ObservationRepairReceipt | None,
    repair_epoch: int,
    cs2_state: CrossSampleReconciledState,
    pre_trajectory_input_id: str,
    post_trajectory_input_id: str,
    mode_track_proposal: CrossSampleProposalOutcome | None,
    mode_track_final_attempt: ModeTrackFinalAttemptResult | None,
    final_application: CrossSampleSuccessorApplication,
    final_population: tuple[CrossSampleGroupIdentity, ...],
) -> bytes:
    if repair_terminal is None and mode_track_final_attempt is None:
        if repair_receipt is None:
            raise CrossSampleReconciliationContractError(
                "legacy final basis requires its exact repair receipt"
            )
        return canonical_identity_basis_bytes(
            {
                "schema_version": "cross_sample_final_receipt_v1",
                "processing_request_id": processing_request_id,
                "post_mt0_application": _application_reference(
                    post_mt0_application
                ),
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
                "mode_track_proposal": _proposal_reference(
                    mode_track_proposal
                ),
                "final_application": _application_reference(final_application),
                "final_population": [
                    _group_reference(group) for group in final_population
                ],
            }
        )
    return canonical_identity_basis_bytes(
        {
            "schema_version": "cross_sample_final_receipt_v2",
            "processing_request_id": processing_request_id,
            "post_mt0_application": _application_reference(post_mt0_application),
            "repair_terminal": _repair_terminal_reference(repair_terminal),
            "repair_receipt_id": (
                None if repair_receipt is None else repair_receipt.receipt_id
            ),
            "repair_epoch": repair_epoch,
            "cs2_state": {
                "processing_request_id": cs2_state.processing_request_id,
                "population": [
                    _group_reference(group) for group in cs2_state.population
                ],
                "repair_receipt_id": cs2_state.repair_receipt_id,
                "repair_authorization_id": cs2_state.repair_authorization_id,
                "repair_epoch": cs2_state.repair_epoch,
                "repair_outcome": cs2_state.repair_outcome,
                "member_subject_ids": list(cs2_state.member_subject_ids),
                "trajectory_input_id": cs2_state.trajectory_input_id,
                "trajectory_changed": cs2_state.trajectory_changed,
            },
            "pre_trajectory_input_id": pre_trajectory_input_id,
            "post_trajectory_input_id": post_trajectory_input_id,
            "mode_track_proposal": _proposal_reference(mode_track_proposal),
            "mode_track_final_attempt": _final_attempt_reference(
                mode_track_final_attempt
            ),
            "final_application": _application_reference(final_application),
            "final_population": [_group_reference(group) for group in final_population],
        }
    )


def _repair_terminal_reference(
    terminal: ObservationRepairTerminalResult | None,
) -> dict[str, object] | None:
    if terminal is None:
        return None
    authorization = terminal.authorization
    return {
        "state": terminal.state,
        "authorization_id": authorization.authorization_id,
        "coverage_id": authorization.coverage_id,
        "population_cell_ids": list(authorization.population_cell_ids),
        "eligible_cell_ids": list(authorization.eligible_cell_ids),
        "receipt_id": None if terminal.receipt is None else terminal.receipt.receipt_id,
    }


def _final_attempt_reference(
    final_attempt: ModeTrackFinalAttemptResult | None,
) -> dict[str, object] | None:
    if final_attempt is None:
        return None
    binding = final_attempt.binding
    return {
        "result_discriminant": binding.result_discriminant,
        "final_result_id": binding.final_result_id,
        "repair_receipt_id": binding.repair_receipt_id,
        "fit_once_source_id": binding.fit_once_source_id,
        "trajectory_input_id": binding.post_trajectory_input_id,
        "assignment_batch_ids": list(binding.assignment_batch_ids),
        "proposal_id": binding.proposal_id,
    }


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
    "CrossSamplePostRepairModeTrackInput",
    "CrossSampleProposalOutcome",
    "CrossSampleReconciledState",
    "CrossSampleReconciliationContractError",
    "apply_mode_track_refinement_run_for_workflow",
    "build_cross_sample_post_mt0_context",
    "consume_mode_track_final_attempt",
    "consume_observation_repair_terminal",
    "reconcile_observation_repair",
)
