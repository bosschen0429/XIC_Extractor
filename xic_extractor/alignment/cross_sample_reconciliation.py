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
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, cast

from xic_extractor.alignment.backfill.observation_repair import (
    ObservationRepairCandidate,
    ObservationRepairEvidence,
    ObservationRepairMultipleEligibleAuthorization,
    ObservationRepairNotRunAuthorization,
    ObservationRepairReceipt,
    ObservationRepairRepairAuthorization,
    ObservationRepairTerminalResult,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample_neighborhood_graph import (
    iter_cross_sample_initial_owner_pairs,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleClassifiedEdge,
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodEdge,
    CrossSampleRepairAdmissionApplication,
    CrossSampleSuccessorApplication,
    _apply_repaired_nonmember_admission,
    _expected_cross_sample_lineage,
    classify_cross_sample_initial_neighborhood_edge,
    mint_cross_sample_group_identity,
    validate_cross_sample_group_population,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation_models import (
    _FINAL_RECEIPT_MINT_TOKEN,
    _NONMEMBER_REPAIR_RESULT_MINT_TOKEN,
    _NONMEMBER_REPAIR_RESULT_PREFIX,
    _POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN,
    CrossSampleFinalReceipt,
    CrossSampleNonmemberRepairResult,
    CrossSamplePostMT0Context,
    CrossSamplePostRepairDisposition,
    CrossSamplePostRepairModeTrackInput,
    CrossSamplePostRepairReconciliation,
    CrossSampleProposalOutcome,
    CrossSampleReconciledState,
    CrossSampleReconciliationContractError,
    CrossSampleRepairDisposition,
    InjectionOrderInput,
    _classify_candidate_edge,
    _edge_sort_key,
    _final_population_after_application,
    _mint_context_trajectory,
    _mint_existing_member_post_repair_mode_track_input,
    _mint_final_receipt,
    _mint_nonmember_repair_result,
    _mint_post_repair_mode_track_input,
    _mint_repaired_candidate_member,
    _nonmember_repair_result_basis,
    _owner_member_id,
    _project_repaired_observation,
    _reduce_repair_admission,
    _validate_post_mt0_context,
    _validate_post_repair_mode_track_input,
    _validate_repair_receipt,
    build_cross_sample_post_mt0_context,
)
from xic_extractor.alignment.drift_evidence import KnownIstdReference
from xic_extractor.alignment.edge_scoring import DriftLookupProtocol
from xic_extractor.alignment.mode_track.assignment import (
    ModeTrackCandidateObservation,
    ModeTrackTrajectoryPolicy,
)
from xic_extractor.alignment.mode_track.contracts import (
    AcceptedModeTrackRefinement,
    InsufficientModeTrackTraining,
    ModeTrackEvidenceProfile,
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
from xic_extractor.alignment.owner_backfill_peak_fact_projection import (
    selected_integration_authority_projection,
)
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.peak_detection.capture import SampleLocalCapture
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    CidNlMethodAssessmentRef,
)
from xic_extractor.peak_detection.hypotheses import PeakHypothesis
from xic_extractor.peak_detection.search_scope import (
    TargetSearchScope,
    UntargetedSearchScope,
)
from xic_extractor.processing_identity import (
    ProcessingOwnerReferences,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import ResolvedCanonicalSampleIdentity

if TYPE_CHECKING:
    from xic_extractor.alignment.mode_track.family_context import FamilyContextRef
    from xic_extractor.alignment.mode_track.refinement import (
        ModeTrackFinalAttemptResult,
        ModeTrackRefinementRun,
    )






def apply_mode_track_refinement_run_for_workflow(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    run: ModeTrackRefinementRun,
) -> CrossSamplePostMT0Context:
    """Atomically apply one complete owner-produced MT0 run."""

    from xic_extractor.alignment.mode_track.refinement import (
        ModeTrackRefinementTransaction,
    )

    predecessors = _validate_mode_track_run_for_workflow(
        processing_owners=processing_owners,
        captures=captures,
        run=run,
    )
    transaction_tuple = run.transactions
    if any(
        type(transaction) is not ModeTrackRefinementTransaction
        for transaction in transaction_tuple
    ) or bool(transaction_tuple) != bool(predecessors):
        raise CrossSampleReconciliationContractError(
            "ModeTrack run requires complete typed component transactions"
        )
    component_predecessor_ids = tuple(
        tuple(
            group.group_hypothesis_id
            for group in transaction.application_context.predecessors
        )
        for transaction in transaction_tuple
    )
    transaction_predecessor_ids = tuple(
        group_id for component in component_predecessor_ids for group_id in component
    )
    # Bootstrap orders whole components by their first canonical predecessor;
    # group identities in different components may interleave.
    if (
        any(not component for component in component_predecessor_ids)
        or component_predecessor_ids != tuple(sorted(component_predecessor_ids))
        or tuple(sorted(transaction_predecessor_ids))
        != tuple(group.group_hypothesis_id for group in predecessors)
        or len(transaction_predecessor_ids) != len(set(transaction_predecessor_ids))
    ):
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
            or transaction.trajectory_input.member_subject_ids != component_member_ids
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
    return _build_mode_track_workflow_context(
        processing_owners=processing_owners,
        run=run,
        predecessors=predecessors,
        population=population,
    )


def build_initial_cid_nl_cross_sample_context(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    sample_assessments: tuple[
        tuple[ResolvedCanonicalSampleIdentity, CidNlMethodAssessmentRef], ...
    ],
    alignment_config: AlignmentConfig,
    trajectory_policy: ModeTrackTrajectoryPolicy,
    drift_lookup: DriftLookupProtocol | None = None,
    known_istd_reference: KnownIstdReference | None = None,
    retained_hypotheses_by_assessment: Mapping[
        tuple[str, str], tuple[PeakHypothesis, ...]
    ]
    | None = None,
) -> tuple[
    FamilyContextRef,
    CrossSamplePostMT0Context,
    tuple[CrossSampleNeighborhoodEdge, ...],
    tuple[ModeTrackCandidateObservation, ...],
]:
    """Close initial CID-NL groups without fitting or assigning ModeTracks."""

    from xic_extractor.alignment.mode_track.producer import (
        bootstrap_cid_nl_initial_population,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        MODE_TRACK_NEIGHBORHOOD_BUDGET,
    )

    if type(processing_owners) is not ProcessingOwnerReferences:
        raise CrossSampleReconciliationContractError(
            "initial CID-NL context requires exact processing owners"
        )
    if type(trajectory_policy) is not ModeTrackTrajectoryPolicy:
        raise CrossSampleReconciliationContractError(
            "initial CID-NL context requires an exact trajectory policy"
        )
    batch_samples = processing_owners.sample_resolutions
    family_context, anchors, sample_candidates, drift_lookup = (
        bootstrap_cid_nl_initial_population(
            processing_owners=processing_owners,
            sample_assessments=sample_assessments,
            alignment_config=alignment_config,
            sample_universe=frozenset(
                sample.metadata.raw_stem for sample in batch_samples
            ),
            injection_order={
                sample.metadata.raw_stem: sample.metadata.injection_order
                for sample in batch_samples
                if sample.metadata.injection_order is not None
            },
            drift_lookup=drift_lookup,
            retain_observation_only_candidates=True,
            known_istd_reference=known_istd_reference,
            retained_hypotheses_by_assessment=retained_hypotheses_by_assessment,
        )
    )
    groups = family_context.mode_tracks
    owners = tuple(owner for group in groups for owner in group.owners)
    source_by_id = {
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for _, candidates in sample_candidates
        for candidate in candidates
    }
    _validate_capture_owner_hypotheses(
        captures=captures,
        owners=owners,
        source_by_id=source_by_id,
    )
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    if any(identity is None for identity in identities):
        raise CrossSampleReconciliationContractError(
            "initial CID-NL group lost its canonical identity"
        )
    predecessors = validate_cross_sample_group_population(
        tuple(identity for identity in identities if identity is not None)
    )
    continuity_group_by_owner = {
        owner.owner_id: group.group_hypothesis_id
        for group in groups
        if group.evidence.startswith(
            (
                "owner_full_batch_continuity;",
                "owner_injection_trajectory;",
                "owner_bounded_continuity;",
            )
        )
        for owner in group.owners
    }
    classified_edges = tuple(
        classify_cross_sample_initial_neighborhood_edge(
            left_owner=left,
            right_owner=right,
            context=(
                "full_batch_continuity"
                if left.owner_id in continuity_group_by_owner
                and continuity_group_by_owner[left.owner_id]
                == continuity_group_by_owner.get(right.owner_id)
                else "complete_link"
            ),
            config=alignment_config,
            drift_lookup=drift_lookup,
        )
        for left, right in iter_cross_sample_initial_owner_pairs(
            owners, config=alignment_config,
        )
    )
    injection_order: dict[str, int] = {}
    for sample, candidates in sample_candidates:
        for candidate in candidates:
            prior = injection_order.setdefault(
                sample.metadata.raw_stem, candidate.injection_order
            )
            if prior != candidate.injection_order:
                raise CrossSampleReconciliationContractError(
                    "initial CID-NL candidates crossed injection order"
                )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=predecessors,
        successors=predecessors,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=processing_owners.request.processing_request_id,
        processing_owners=processing_owners,
        population=predecessors,
        application=application,
        owners=owners,
        classified_edges=classified_edges,
        anchors=anchors,
        injection_order={
            owner.sample_stem: injection_order[owner.sample_stem] for owner in owners
        },
        evidence_profile=ModeTrackEvidenceProfile(
            trajectory_policy_version=trajectory_policy.trajectory_policy_version,
            band_policy_version=trajectory_policy.band_policy_version,
            prediction_half_width_min=trajectory_policy.prediction_half_width_min,
            max_interpolation_gap=trajectory_policy.max_interpolation_gap,
            max_extrapolation_gap=trajectory_policy.max_extrapolation_gap,
        ),
        budget=MODE_TRACK_NEIGHBORHOOD_BUDGET,
    )
    context = replace(context, drift_lookup=drift_lookup)
    retained_candidates = tuple(
        sorted(
            (
                candidate
                for _, candidates in sample_candidates
                for candidate in candidates
            ),
            key=lambda candidate: candidate.candidate_observation_id,
        )
    )
    return family_context, context, classified_edges, retained_candidates


def _validate_mode_track_run_for_workflow(
    *,
    processing_owners: ProcessingOwnerReferences,
    captures: Sequence[SampleLocalCapture],
    run: ModeTrackRefinementRun,
) -> tuple[CrossSampleGroupIdentity, ...]:
    from xic_extractor.alignment.mode_track.assignment import (
        ModeTrackContractError,
        validate_mode_track_fit_once_source_for_consumption,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        _REFINEMENT_RUN_MINT_TOKEN,
        ModeTrackRefinementContractError,
        ModeTrackRefinementRun,
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
        or run.fit_once_source.initial_assignment_batches != run.assignment_batches
    ):
        raise CrossSampleReconciliationContractError(
            "ModeTrack run crossed its exact fit-once source"
        )
    source_by_id = {
        candidate.peak_hypothesis_id: candidate.peak_hypothesis
        for sample_source in run.fit_once_source.sample_sources
        for candidate in sample_source.candidates
    }
    _validate_capture_owner_hypotheses(
        captures=captures,
        owners=run.owners,
        source_by_id=source_by_id,
    )
    try:
        predecessors = validate_cross_sample_group_population(run.predecessors)
    except (TypeError, ValueError) as exc:
        raise CrossSampleReconciliationContractError(str(exc)) from exc
    family_predecessors = tuple(
        validate_cross_sample_peak_group_identity(track, require_canonical=True)
        for track in run.family_context.mode_tracks
    )
    if (
        any(group is None for group in family_predecessors)
        or tuple(
            sorted(
                (group for group in family_predecessors if group is not None),
                key=lambda group: group.group_hypothesis_id,
            )
        )
        != predecessors
    ):
        raise CrossSampleReconciliationContractError(
            "ModeTrack run predecessor population crossed Family context"
        )
    return predecessors


def _validate_capture_owner_hypotheses(
    *,
    captures: Sequence[SampleLocalCapture],
    owners: Sequence[SampleLocalMS1Owner],
    source_by_id: Mapping[str, PeakHypothesis],
) -> None:
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
    if candidate_ids != declared_candidate_ids or len(candidate_ids) != len(
        set(candidate_ids)
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
    owner_ids = tuple(sorted(owner.owner_id for owner in owners))
    if not set(selected_ids).issubset(source_by_id) or len(owner_ids) != len(
        set(owner_ids)
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 captures cross the exact retained candidate population"
        )
    if any(
        capture.selected_hypothesis.integration
        is not source_by_id[capture.selected_hypothesis.hypothesis_id].integration
        for capture in capture_tuple
        if capture.selected_hypothesis is not None
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
        for owner in owners
    ):
        raise CrossSampleReconciliationContractError(
            "workflow MT0 source crossed exact owner physical integration"
        )


def _build_mode_track_workflow_context(
    *,
    processing_owners: ProcessingOwnerReferences,
    run: ModeTrackRefinementRun,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    population: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSamplePostMT0Context:
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
            member.member_subject_id for member in application.successor.member_subjects
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
            member.member_subject_id == member_id for member in group.member_subjects
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
        post_trajectory.trajectory_input_id != pre_trajectory.trajectory_input_id
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


def close_cross_sample_without_observation_repair(
    context: CrossSamplePostMT0Context,
) -> CrossSampleFinalReceipt:
    """Close a post-MT0 population when the application requests no repair."""

    _validate_post_mt0_context(context)
    final_application = _mint_unchanged_successor_application(context)
    state = CrossSampleReconciledState(
        processing_request_id=context.processing_request_id,
        population=context.population,
        repair_receipt_id=None,
        repair_epoch=0,
        repair_outcome="not_run",
        member_subject_ids=tuple(
            sorted(
                member.member_subject_id
                for group in context.population
                for member in group.member_subjects
            )
        ),
        trajectory_input_id=context.trajectory_input.trajectory_input_id,
        trajectory_changed=False,
        repair_authorization_id=None,
    )
    return _mint_final_receipt(
        processing_request_id=context.processing_request_id,
        post_mt0_application=context.application,
        repair_terminal=None,
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
        or binding.cross_sample_post_repair_input_id != post_repair.post_repair_input_id
        or binding.cross_sample_repair_result_id
        != post_repair.cross_sample_repair_result_id
        or binding.post_repair_population_id != post_repair.post_repair_population_id
        or binding.pre_trajectory_input_id != post_repair.pre_repair_trajectory_input_id
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
    if post_trajectory.trajectory_input_id != pre_trajectory.trajectory_input_id:
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
    component_member_ids = set(post_repair.post_repair_trajectory.member_subject_ids)
    component_predecessors = tuple(
        group
        for group in predecessors
        if {member.member_subject_id for member in group.member_subjects}
        & component_member_ids
    )
    if any(
        not {member.member_subject_id for member in group.member_subjects}
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
    if tuple(sorted(proposed_ids)) != tuple(sorted(member_by_id)) or len(
        proposed_ids
    ) != len(set(proposed_ids)):
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
        if group.group_hypothesis_id == candidate.predecessor_group.group_hypothesis_id
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



















































__all__ = (
    "CrossSampleFinalReceipt",
    "CrossSampleNonmemberRepairResult",
    "CrossSamplePostMT0Context",
    "CrossSamplePostRepairDisposition",
    "CrossSamplePostRepairModeTrackInput",
    "CrossSamplePostRepairReconciliation",
    "CrossSampleProposalOutcome",
    "CrossSampleReconciledState",
    "CrossSampleReconciliationContractError",
    "CrossSampleRepairDisposition",
    "InjectionOrderInput",
    "_FINAL_RECEIPT_MINT_TOKEN",
    "_NONMEMBER_REPAIR_RESULT_MINT_TOKEN",
    "_POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN",
    "apply_mode_track_refinement_run_for_workflow",
    "build_cross_sample_post_mt0_context",
    "close_cross_sample_without_observation_repair",
    "consume_mode_track_final_attempt",
    "consume_observation_repair_terminal",
    "reconcile_nonmember_observation_repair",
    "reconcile_observation_repair",
)
