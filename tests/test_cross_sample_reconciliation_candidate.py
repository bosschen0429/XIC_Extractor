"""No-RAW contract tests for repaired nonmember CrossSample admission."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample as cross_sample_api
import xic_extractor.alignment.cross_sample_peak_groups as peak_group_owner
import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
import xic_extractor.alignment.mode_track.refinement as refinement_owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample import (
    CrossSampleNonmemberRepairResult,
    reconcile_nonmember_observation_repair,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    build_cross_sample_post_mt0_context,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)


class _CandidateDriftLookup:
    source = "batch_istd_trend"

    def __init__(self, *, candidate_sample: str, candidate_delta_min: float) -> None:
        self._candidate_sample = candidate_sample
        self._candidate_delta_min = candidate_delta_min

    def sample_delta_min(self, sample_stem: str) -> float:
        if sample_stem == self._candidate_sample:
            return self._candidate_delta_min
        return 0.0

    def injection_order(self, _sample_stem: str) -> None:
        return None


def test_membership_neutral_repair_enters_the_public_cross_sample_owner(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)

    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
    )
    repeated = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
    )

    assert isinstance(reconciled, CrossSampleNonmemberRepairResult)
    assert repeated == reconciled
    assert reconciled.repair_receipt is terminal.receipt
    assert reconciled.disposition == "admitted"
    assert reconciled.application is not None
    assert reconciled.mode_track_input is not None
    assert reconciled.mode_track_input.post_repair_trajectory.anchor_ids == ()
    assert (
        reconciled.mode_track_input.processing_request_id
        == context.processing_request_id
    )
    assert (
        reconciled.mode_track_input.processing_attempt_id
        == context.processing_owners.attempt.processing_attempt_id
    )
    assert reconciled.mode_track_input.processing_owners is context.processing_owners
    assert reconciled.mode_track_input.repair_epoch == 1
    assert reconciled.mode_track_input.repair_disposition == "nonmember_admitted"
    assert (
        reconciled.mode_track_input.cross_sample_repair_result_id
        == reconciled.result_id
    )
    assert (
        reconciled.mode_track_input.cross_sample_repair_application_id
        == reconciled.application.application_id
    )
    assert (
        reconciled.mode_track_input.post_repair_population
        == reconciled.final_population
    )
    assert (
        reconciled.mode_track_input.post_repair_owners
        == reconciled.final_owners
    )
    assert reconciled.mode_track_input.trajectory_changed is True
    repaired_observation = reconciled.mode_track_input.repaired_observation
    assert repaired_observation.identity_evidence_authority == "none"
    assert not hasattr(repaired_observation, "primary_identity_event")
    assert reconciled.final_owners == context.owners
    assert reconciled.membership_authority == "cross_sample"
    assert reconciled.value_authority == "none"
    assert reconciled.matrix_authority == "none"
    assert reconciled.activation_authority == "none"
    assert reconciled.writer_authority == "none"
    assert reconciled.application.admitted_member_subject_id in {
        member.member_subject_id
        for group in reconciled.final_population
        for member in group.member_subjects
    }


def test_admitted_repair_runs_one_non_training_final_assignment_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    assessments_by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in spine.assessments
    }
    _, _, _, source = bootstrap_mode_tracks_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=tuple(
            (sample, assessments_by_sample[sample.identity.sample_identity_id])
            for sample in fixture.samples
        ),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )
    executed = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        executed.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    owners = tuple(candidate.logical_request.item.feature.owners)
    group = candidate.predecessor_group
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=CrossSampleSuccessorApplication(
            status="assessable",
            predecessors=(group,),
            successors=(group,),
            lineage=(),
            reason="unchanged_membership_no_lineage",
        ),
        owners=owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=executed[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert reconciled.mode_track_input is not None
    calls = 0
    real_assign = refinement_owner.assign_mode_tracks

    def counted_assign(**kwargs):
        nonlocal calls
        calls += 1
        return real_assign(**kwargs)

    monkeypatch.setattr(refinement_owner, "assign_mode_tracks", counted_assign)
    monkeypatch.setattr(
        refinement_owner,
        "bootstrap_mode_tracks_from_cid_nl_support",
        lambda **_kwargs: pytest.fail("final attempt refit the model"),
    )
    result = refinement_owner.run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=reconciled.mode_track_input,
    )

    assert calls == len(source.sample_sources)
    repaired_assignments = tuple(
        assignment
        for batch in result.assignment_batches
        for assignment in batch.assignments
        if isinstance(
            assignment.candidate_observation,
            refinement_owner.ModeTrackRepairAssignmentObservation,
        )
    )
    assert len(repaired_assignments) == 1
    assert repaired_assignments[0].candidate_observation_class == "non_training"
    assert repaired_assignments[0].direct_support_assessment is None


def test_repair_admission_does_not_require_candidate_local_identity_support(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)

    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=replace(
            alignment_config,
            anchor_min_evidence_score=100,
            anchor_min_seed_events=100,
        ),
    )

    assert reconciled.disposition == "admitted"
    assert reconciled.mode_track_input is not None
    assert (
        reconciled.mode_track_input.repaired_observation.identity_evidence_authority
        == "none"
    )


def test_review_only_candidate_mints_no_membership_or_mode_track_input(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    candidate = terminal.candidate
    assert candidate is not None

    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
        drift_lookup=_CandidateDriftLookup(
            candidate_sample=candidate.request_identity.sample_stem,
            candidate_delta_min=2.0,
        ),
    )

    assert reconciled.disposition == "review_only"
    assert {edge.disposition for edge in reconciled.pairwise_edges} == {"review_only"}
    assert reconciled.application is None
    assert reconciled.mode_track_input is None
    assert reconciled.final_population == context.population
    assert reconciled.final_owners == context.owners
    assert reconciled.post_trajectory_input_id == (
        context.trajectory_input.trajectory_input_id
    )


def test_excluded_candidate_is_closed_without_membership_or_mode_track(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    candidate = terminal.candidate
    assert candidate is not None
    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
        drift_lookup=_CandidateDriftLookup(
            candidate_sample=candidate.request_identity.sample_stem,
            candidate_delta_min=4.0,
        ),
    )

    assert reconciled.disposition == "excluded"
    assert {edge.disposition for edge in reconciled.pairwise_edges} == {"excluded"}
    assert reconciled.application is None
    assert reconciled.mode_track_input is None
    assert reconciled.final_population == context.population


def test_selected_output_flag_cannot_change_cross_sample_admission(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    selected = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
    )
    unselected_observation = copy(repair_result.peak_fact_observations[0])
    object.__setattr__(unselected_observation, "selected_for_output", False)
    unselected_result = replace(
        repair_result,
        peak_fact_observations=(unselected_observation,),
    )

    unselected = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=unselected_result,
        alignment_config=alignment_config,
    )

    assert unselected == selected


@pytest.mark.parametrize("variant", ["missing", "duplicate"])
def test_missing_or_duplicate_live_observation_fails_closed(
    tmp_path: Path,
    variant: str,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    observation = repair_result.peak_fact_observations[0]
    observations = () if variant == "missing" else (observation, observation)
    crossed_result = replace(
        repair_result,
        peak_fact_observations=observations,
    )

    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="one exact live observation",
    ):
        reconcile_nonmember_observation_repair(
            context=context,
            repair_terminal=terminal,
            repair_result=crossed_result,
            alignment_config=alignment_config,
        )


def test_crossed_terminal_and_same_looking_context_fail_closed(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(
        tmp_path / "first",
    )
    _other_context, other_terminal, _other_result, _other_config = _scenario(
        tmp_path / "other",
        hold_sample="s4",
    )
    crossed_receipt_terminal = copy(terminal)
    object.__setattr__(
        crossed_receipt_terminal,
        "receipt",
        other_terminal.receipt,
    )

    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="crossed its exact candidate or receipt",
    ):
        reconcile_nonmember_observation_repair(
            context=context,
            repair_terminal=crossed_receipt_terminal,
            repair_result=repair_result,
            alignment_config=alignment_config,
        )

    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="exact current group",
    ):
        reconcile_nonmember_observation_repair(
            context=context,
            repair_terminal=other_terminal,
            repair_result=repair_result,
            alignment_config=alignment_config,
        )


def test_same_looking_application_replay_fails_before_admission(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    copied_successor = copy(context.application.successors[0])
    replayed_application = replace(
        context.application,
        successors=(copied_successor,),
    )
    replayed_context = replace(context, application=replayed_application)

    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="exact current group application",
    ):
        reconcile_nonmember_observation_repair(
            context=replayed_context,
            repair_terminal=terminal,
            repair_result=repair_result,
            alignment_config=alignment_config,
        )


def test_admission_application_result_and_mode_track_input_fail_closed(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    reconciled = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
    )
    application = reconciled.application
    mode_track_input = reconciled.mode_track_input
    assert application is not None
    assert mode_track_input is not None
    assert {
        "CrossSampleNonmemberRepairResult",
        "CrossSamplePostRepairModeTrackInput",
        "CrossSampleRepairAdmissionApplication",
        "CrossSampleRepairAdmissionEdge",
        "CrossSampleRepairDisposition",
        "CrossSampleRepairedObservation",
        "reconcile_nonmember_observation_repair",
    }.issubset(cross_sample_api.__all__)

    with pytest.raises(
        peak_group_owner.CrossSampleContractError,
        match="minted by CrossSample",
    ):
        replace(application, _mint_token=object())
    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="minted by CrossSample",
    ):
        replace(reconciled, _mint_token=object())
    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="minted by CrossSample",
    ):
        replace(mode_track_input, _mint_token=object())
    with pytest.raises(
        peak_group_owner.CrossSampleContractError,
        match="minted by CrossSample",
    ):
        replace(
            mode_track_input.repaired_observation,
            _mint_token=object(),
        )

    with pytest.raises(
        peak_group_owner.CrossSampleContractError,
        match="lineage",
    ):
        replace(
            application,
            lineage=(),
            _mint_token=(peak_group_owner._CROSS_SAMPLE_REPAIR_APPLICATION_MINT_TOKEN),
        )
    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="application or ModeTrack input",
    ):
        replace(
            reconciled,
            final_population=context.population,
            _mint_token=reconciliation_owner._NONMEMBER_REPAIR_RESULT_MINT_TOKEN,
        )
    with pytest.raises(
        reconciliation_owner.CrossSampleReconciliationContractError,
        match="crossed trajectory",
    ):
        replace(
            mode_track_input,
            post_repair_trajectory=context.trajectory_input,
            _mint_token=(reconciliation_owner._POST_REPAIR_MODE_TRACK_INPUT_MINT_TOKEN),
        )


def test_admission_is_one_complete_pairwise_pass_and_no_mode_track_proposal(
    tmp_path: Path,
) -> None:
    context, terminal, repair_result, alignment_config = _scenario(tmp_path)
    candidate = terminal.candidate
    assert candidate is not None

    assert not hasattr(
        reconciliation_owner,
        "propose_mode_track_successor_partition",
    )

    admitted = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
    )

    review_only = reconcile_nonmember_observation_repair(
        context=context,
        repair_terminal=terminal,
        repair_result=repair_result,
        alignment_config=alignment_config,
        drift_lookup=_CandidateDriftLookup(
            candidate_sample=candidate.request_identity.sample_stem,
            candidate_delta_min=2.0,
        ),
    )

    assert (admitted.disposition, review_only.disposition) == (
        "admitted",
        "review_only",
    )
    assert len(admitted.pairwise_edges) == len(context.owners)
    assert len(review_only.pairwise_edges) == len(context.owners)
    assert admitted.application is not None
    assert admitted.mode_track_input is not None
    assert review_only.application is None
    assert review_only.mode_track_input is None


def _scenario(tmp_path: Path, *, hold_sample: str = "s2"):
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=(hold_sample,),
    )
    executed = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        executed.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    assert terminal.receipt is not None
    assert terminal.receipt.evidence is not None
    assert terminal.receipt.evidence.member_subject_id is None
    owners = tuple(candidate.logical_request.item.feature.owners)
    group = candidate.predecessor_group
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=application,
        owners=owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1",
            "fixed-band-v1",
            0.1,
            10,
            10,
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    return context, terminal, executed[0], coverage.epoch.alignment_config
