"""CrossSample closure tests using repository-native processing owners."""

from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import pytest

from tests.test_alignment_owner_clustering import _owner
from tests.test_cell_observations import _repaired_handoff
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import (
    close_observation_repair,
    mint_observation_repair_subject,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupMemberSubject,
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    classify_cross_sample_initial_neighborhood_edge,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostRepairModeTrackInput,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


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


def test_changed_existing_member_handoff_is_component_local(
    tmp_path: Path,
) -> None:
    fixture, _spine, _handoff, _cell, repair, observation = _repaired_handoff(
        tmp_path
    )
    repaired_group = repair.subject.predecessor_group
    assert repair.evidence is not None
    repaired_owner = replace(
        _owner(
            "s1",
            "repair",
            apex_rt=repair.evidence.rt_apex_min + 0.5,
        ),
        cross_sample_member_subject=repaired_group.member_subjects[0],
    )
    physical_id = typed_identity_digest(
        "xic.peak-observation-content.v1:",
        canonical_identity_basis_bytes({"sample_stem": "s2", "suffix": "unaffected"}),
    )
    member_basis = canonical_identity_basis_bytes(
        {
            "schema_version": "group_member_subject_v1",
            "processing_request_id": repaired_group.processing_request_id,
            "canonical_sample_identity_id": "test-sample:s2",
            "subject": {"kind": "physical_observation", "id": physical_id},
            "physical_observation_ids": [physical_id],
        }
    )
    unaffected_member = CrossSampleGroupMemberSubject(
        member_subject_id=typed_identity_digest(
            "xic.group-member-subject.v1:", member_basis
        ),
        processing_request_id=repaired_group.processing_request_id,
        canonical_sample_identity_id="test-sample:s2",
        subject_kind="physical_observation",
        subject_id=physical_id,
        physical_observation_ids=(physical_id,),
        basis_bytes=member_basis,
    )
    unaffected_owner = replace(
        _owner("s2", "unaffected", apex_rt=18.0),
        cross_sample_member_subject=unaffected_member,
    )
    unaffected_group = mint_cross_sample_group_identity(
        processing_request_id=repaired_group.processing_request_id,
        member_subjects=(unaffected_member,),
    )
    owners = tuple(
        sorted(
            (repaired_owner, unaffected_owner),
            key=lambda owner: (
                owner.cross_sample_member_subject.member_subject_id,
                owner.owner_id,
            ),
        )
    )
    population = tuple(
        sorted(
            (repaired_group, unaffected_group),
            key=lambda group: group.group_hypothesis_id,
        )
    )
    edge = classify_cross_sample_initial_neighborhood_edge(
        left_owner=owners[0],
        right_owner=owners[1],
        context="hard_gate",
        config=AlignmentConfig(),
    )
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=population,
        successors=population,
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=repaired_group.processing_request_id,
        processing_owners=fixture.owners,
        population=population,
        application=application,
        owners=owners,
        classified_edges=(edge,),
        anchors=(),
        injection_order={
            owner.sample_stem: index for index, owner in enumerate(owners, 1)
        },
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1", "fixed-band-v1", 0.1, 10, 10
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    subject = mint_observation_repair_subject(
        request_identity=observation.request_identity,
        logical_request=observation.logical_request,
        predecessor_group=repaired_group,
        member_subject=repaired_group.member_subjects[0],
        sample_identity=repair.subject.sample_identity,
        successor_application=application,
    )
    repair = close_observation_repair(
        subject,
        OwnerBackfillSampleResult(
            sample_index=0,
            sample_stem="s1",
            cells=(),
            peak_fact_observations=(observation,),
        ),
    )

    result = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert isinstance(result, CrossSamplePostRepairModeTrackInput)
    assert result.post_repair_population == population
    assert tuple(owner.owner_id for owner in result.post_repair_owners) == (
        repaired_owner.owner_id,
    )
    assert result.post_repair_owners[0].owner_apex_rt == repair.evidence.rt_apex_min
    assert result.post_repair_trajectory.member_subject_ids == (
        repaired_group.member_subjects[0].member_subject_id,
    )


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
