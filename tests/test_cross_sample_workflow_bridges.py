"""CrossSample-owned bridges used by the shared alignment workflow."""

from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.cell_observations import (
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleReconciliationContractError,
    apply_mode_track_refinement_run_for_workflow,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
    consume_observation_repair_terminal,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import (
    ModeTrackEvidenceProfile,
    ModeTrackRefinementContractError,
)
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.mode_track.refinement import (
    run_post_repair_mode_track_final_attempt,
)


def test_workflow_repair_bridges_accept_no_caller_route_or_proposal() -> None:
    assert tuple(
        signature(apply_mode_track_refinement_run_for_workflow).parameters
    ) == ("processing_owners", "captures", "run")
    assert tuple(signature(consume_observation_repair_terminal).parameters) == (
        "context",
        "terminal",
        "repair_result",
        "alignment_config",
        "drift_lookup",
    )
    assert tuple(signature(consume_mode_track_final_attempt).parameters) == (
        "context",
        "post_repair",
        "terminal",
        "final_attempt",
    )


@pytest.mark.parametrize("hold_samples", [(), ("s2", "s4")])
def test_no_receipt_terminal_closes_epoch_zero_without_repair_evidence(
    tmp_path: Path,
    hold_samples: tuple[str, ...],
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=hold_samples,
    )
    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=lambda _candidate: pytest.fail("blocked repair invoked callback"),
    )
    group = spine.group
    assert group is not None
    identity = validate_cross_sample_peak_group_identity(
        group,
        require_canonical=True,
    )
    population = (identity,)
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=population,
        successors=population,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.request.processing_request_id,
        processing_owners=fixture.owners,
        population=population,
        application=application,
        owners=group.owners,
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(group.owners, start=1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )

    result = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=None,
        alignment_config=coverage.epoch.alignment_config,
    )

    assert isinstance(result, CrossSampleFinalReceipt)
    assert result.repair_terminal is terminal
    assert result.repair_receipt is None
    assert result.repair_epoch == 0
    assert result.cs2_state.repair_authorization_id == (
        terminal.authorization.authorization_id
    )
    assert result.cs2_state.repair_receipt_id is None
    assert result.cs2_state.repair_outcome == terminal.state
    assert result.mode_track_final_attempt is None
    assert result.final_population == context.population
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="legacy final receipt requires",
    ):
        replace(
            result,
            repair_terminal=None,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=result,
    )
    final_cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=cells,
        repair_receipts=(),
        repaired_observations=(),
    )
    assert final_cells
    assert all(cell.repair_receipts == () for cell in final_cells)


def test_existing_member_terminal_and_exact_final_attempt_close_once(
    tmp_path: Path,
) -> None:
    fixture, spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    captured = []

    def execute(candidate):
        _built, result = _repair_result(fixture, coverage, candidate)
        captured.append(result)
        return result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    group = candidate.predecessor_group
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    owners = tuple(candidate.logical_request.item.feature.owners)
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.request.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=application,
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
    post_repair = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=captured[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert not isinstance(post_repair, CrossSampleFinalReceipt)
    assessments_by_sample = {
        assessment.content.canonical_sample_identity_id: assessment
        for assessment in spine.assessments
    }
    _family, _model, _batches, source = bootstrap_mode_tracks_from_cid_nl_support(
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
    assert terminal.receipt is not None
    final_attempt = run_post_repair_mode_track_final_attempt(
        source=source,
        repair_receipt=terminal.receipt,
        post_repair=post_repair,
    )

    final = consume_mode_track_final_attempt(
        context=context,
        post_repair=post_repair,
        terminal=terminal,
        final_attempt=final_attempt,
    )

    assert final.repair_terminal is terminal
    assert final.repair_receipt is terminal.receipt
    assert final.mode_track_final_attempt is final_attempt
    assert final.mode_track_proposal is final_attempt.proposal
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exact MT1 final attempt",
    ):
        replace(
            final,
            mode_track_final_attempt=None,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )


def test_mt0_bridge_rejects_missing_capture_owner_closure(tmp_path: Path) -> None:
    from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
    from tests.test_alignment_multi_hypothesis_support import (
        _assessment_with_observation_only_peak,
    )
    from xic_extractor.alignment.mode_track.refinement import (
        bootstrap_mode_track_refinement_from_cid_nl_support,
    )

    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("early",))
    assessment = _assessment_with_observation_only_peak(
        fixture,
        sample_stem="early",
        direct_apex_rt=7.80,
        observed_apex_rt=8.40,
        scan_number=201,
    )
    run = bootstrap_mode_track_refinement_from_cid_nl_support(
        processing_owners=fixture.owners,
        sample_assessments=((fixture.sample("early"), assessment),),
        alignment_config=AlignmentConfig(anchor_min_seed_events=1),
        trajectory_policy=ModeTrackTrajectoryPolicy(
            trajectory_policy_version="anchor-trajectory-v1",
            band_policy_version="fixed-band-v1",
            prediction_half_width_min=0.1,
            max_interpolation_gap=10,
            max_extrapolation_gap=10,
        ),
    )

    with pytest.raises(
        ModeTrackRefinementContractError,
        match="must be minted by ModeTrack",
    ):
        replace(run, _mint_token=object())

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="captures do not close",
    ):
        apply_mode_track_refinement_run_for_workflow(
            processing_owners=fixture.owners,
            captures=(),
            run=run,
        )
