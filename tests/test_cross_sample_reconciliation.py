"""CrossSample closure tests using repository-native processing owners."""

from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import pytest

from tests.test_alignment_owner_clustering import _owner
from tests.test_cell_observations import _repaired_handoff
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostRepairModeTrackInput,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile


def _scenario(
    tmp_path: Path,
    *,
    changed: bool = False,
    injection_order: object | None = None,
    sample_stem: str = "s1",
):
    fixture, _spine, _handoff, _cell, repair, _observation = _repaired_handoff(
        tmp_path,
        sample_stem=sample_stem,
    )
    group = repair.subject.predecessor_group
    member = group.member_subjects[0]
    assert repair.evidence is not None
    owner = replace(
        _owner(
            sample_stem,
            "repair",
            apex_rt=repair.evidence.rt_apex_min + (0.5 if changed else 0.0),
        ),
        cross_sample_member_subject=member,
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=repair.subject.successor_application,
        owners=(owner,),
        classified_edges=(),
        anchors=(),
        injection_order=(
            {sample_stem: 1} if injection_order is None else injection_order
        ),
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1",
            "fixed-band-v1",
            0.1,
            10,
            10,
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    return fixture, context, repair


def test_reconciliation_accepts_only_owner_context_and_repair_receipt() -> None:
    assert tuple(signature(reconcile_observation_repair).parameters) == (
        "context",
        "repair_receipt",
    )


def test_unchanged_repair_closes_deterministically_without_mode_track(
    tmp_path: Path,
) -> None:
    _fixture, context, repair = _scenario(tmp_path)

    first = reconcile_observation_repair(context=context, repair_receipt=repair)
    repeated = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert isinstance(first, CrossSampleFinalReceipt)
    assert repeated == first
    assert first.cs2_state.trajectory_changed is False
    assert first.mode_track_proposal is None
    assert first.final_population == context.population


def test_changed_existing_member_emits_exact_replay_bound_handoff(
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path, changed=True)

    result = reconcile_observation_repair(context=context, repair_receipt=repair)
    repeated = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert isinstance(result, CrossSamplePostRepairModeTrackInput)
    assert repeated == result
    assert result.processing_request_id == context.processing_request_id
    assert result.processing_attempt_id == fixture.owners.attempt.processing_attempt_id
    assert result.processing_owners is fixture.owners
    assert result.repair_receipt_id == repair.receipt_id
    assert result.repair_epoch == 1
    assert result.repair_disposition == "existing_member_reconciled"
    assert result.repaired_member_subject_id == repair.evidence.member_subject_id
    assert result.pre_repair_trajectory_input_id == (
        context.trajectory_input.trajectory_input_id
    )
    assert result.trajectory_changed is True


def test_final_receipt_cannot_be_resealed_by_a_caller(tmp_path: Path) -> None:
    _fixture, context, repair = _scenario(tmp_path)
    result = reconcile_observation_repair(context=context, repair_receipt=repair)
    assert isinstance(result, CrossSampleFinalReceipt)

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="must be minted by CrossSample",
    ):
        replace(result, _mint_token=object())


def test_mode_track_handoff_cannot_be_resealed_by_a_caller(tmp_path: Path) -> None:
    _fixture, context, repair = _scenario(tmp_path, changed=True)
    result = reconcile_observation_repair(context=context, repair_receipt=repair)
    assert isinstance(result, CrossSamplePostRepairModeTrackInput)

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="must be minted by CrossSample",
    ):
        replace(result, _mint_token=object())


def test_context_rejects_duplicate_injection_order_keys(tmp_path: Path) -> None:
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="duplicate sample keys",
    ):
        _scenario(
            tmp_path,
            injection_order=(("s1", 1), ("s1", 2)),
        )


def test_reconciliation_rejects_crossed_repair_receipt(tmp_path: Path) -> None:
    _fixture, context, _repair = _scenario(tmp_path / "first", sample_stem="s1")
    _other_fixture, _other_context, other_repair = _scenario(
        tmp_path / "second",
        sample_stem="s2",
    )

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="post-MT0 application|processing request",
    ):
        reconcile_observation_repair(
            context=context,
            repair_receipt=other_repair,
        )


def test_context_rejects_a_crossed_trajectory_snapshot(tmp_path: Path) -> None:
    _fixture, context, _repair = _scenario(tmp_path / "first", sample_stem="s1")
    _other_fixture, other_context, _other_repair = _scenario(
        tmp_path / "second",
        sample_stem="s2",
    )

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="trajectory snapshot|processing request",
    ):
        replace(context, trajectory_input=other_context.trajectory_input)


def test_reconciliation_rejects_caller_partition_authority(tmp_path: Path) -> None:
    _fixture, context, repair = _scenario(tmp_path)

    with pytest.raises(TypeError):
        reconcile_observation_repair(
            context=context,
            repair_receipt=repair,
            successor_partition=(),  # type: ignore[call-arg]
        )


def test_changed_handoff_closes_population_owner_and_member_sets(
    tmp_path: Path,
) -> None:
    _fixture, context, repair = _scenario(tmp_path, changed=True)
    result = reconcile_observation_repair(context=context, repair_receipt=repair)
    assert isinstance(result, CrossSamplePostRepairModeTrackInput)

    population_member_ids = {
        member.member_subject_id
        for group in result.post_repair_population
        for member in group.member_subjects
    }
    trajectory_member_ids = set(result.post_repair_trajectory.member_subject_ids)
    owner_member_ids = {
        owner.cross_sample_member_subject.member_subject_id
        for owner in result.post_repair_owners
    }
    assert population_member_ids == trajectory_member_ids == owner_member_ids
    assert result.repaired_member_subject_id in population_member_ids
    assert result.post_repair_population == context.population
