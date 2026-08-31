"""Terminal CrossSample bridges used by the shared alignment workflow."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample_reconciliation as reconciliation_owner
from tests.test_backfill_observation_repair_authorization import (
    _authorization_population,
)
from tests.test_backfill_observation_repair_execution import _repair_result
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import execute_observation_repair
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    consume_mode_track_final_attempt,
    consume_observation_repair_terminal,
)
from xic_extractor.alignment.mode_track.assignment import ModeTrackTrajectoryPolicy
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
from xic_extractor.alignment.mode_track.producer import (
    bootstrap_mode_tracks_from_cid_nl_support,
)
from xic_extractor.alignment.mode_track.refinement import (
    run_post_repair_mode_track_final_attempt,
)


def test_nonmember_no_repair_preserves_owner_negative_without_mt1(
    tmp_path: Path,
) -> None:
    fixture, _spine, coverage, cells = _authorization_population(
        tmp_path,
        hold_samples=("s2",),
    )
    executed = []

    def execute(candidate):
        built, result = _repair_result(fixture, coverage, candidate)
        unselected = copy(built.peak_fact_observations[0])
        object.__setattr__(unselected, "selected_for_output", False)
        no_repair_result = replace(result, peak_fact_observations=(unselected,))
        executed.append(no_repair_result)
        return no_repair_result

    terminal = execute_observation_repair(
        processing_owners=fixture.owners,
        coverage=coverage,
        cell_observations=cells,
        execute=execute,
    )
    candidate = terminal.candidate
    assert candidate is not None
    assert terminal.receipt is not None
    assert terminal.receipt.outcome == "no_repair"
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
        owners=tuple(candidate.logical_request.item.feature.owners),
        classified_edges=(),
        anchors=(),
        injection_order={
            owner.sample_stem: index
            for index, owner in enumerate(
                candidate.logical_request.item.feature.owners,
                start=1,
            )
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )

    final = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=executed[0],
        alignment_config=coverage.epoch.alignment_config,
    )

    assert type(final) is CrossSampleFinalReceipt
    assert final.repair_terminal is terminal
    assert final.repair_receipt is terminal.receipt
    assert final.repair_epoch == 1
    assert final.cs2_state.repair_outcome == "no_repair"
    assert final.final_population == context.population
    assert final.mode_track_final_attempt is None


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
        fit_once_source_id=source.fit_once_source_id,
    )
    post_repair = consume_observation_repair_terminal(
        context=context,
        terminal=terminal,
        repair_result=captured[0],
        alignment_config=coverage.epoch.alignment_config,
    )
    assert not isinstance(post_repair, CrossSampleFinalReceipt)
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
        match="repair epoch must be an exact integer",
    ):
        replace(
            final,
            repair_epoch=True,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="exact MT1 final attempt",
    ):
        replace(
            final,
            mode_track_final_attempt=None,
            _mint_token=reconciliation_owner._FINAL_RECEIPT_MINT_TOKEN,
        )
