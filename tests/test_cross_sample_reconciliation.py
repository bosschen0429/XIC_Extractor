"""No-RAW contract tests for the CrossSample repair reconciliation owner."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample_reconciliation as reconciliation
from tests.test_alignment_owner_clustering import _owner
from tests.test_backfill_observation_repair import (
    _repair_fixture,
    _RepairFixture,
    _subject,
)
from xic_extractor.alignment import AlignmentConfig
from xic_extractor.alignment.backfill import (
    close_observation_repair,
    mint_observation_repair_subject,
)
from xic_extractor.alignment.backfill.observation_repair import ObservationRepairReceipt
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    apply_coupled_successor_proposal,
    classify_cross_sample_initial_neighborhood_edge,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    CrossSampleFinalReceipt,
    CrossSamplePostMT0Context,
    CrossSampleReconciliationContractError,
    build_cross_sample_post_mt0_context,
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track.contracts import (
    AcceptedModeTrackRefinement,
    ModeTrackEvidenceProfile,
    mint_accepted_mode_track_refinement,
)
from xic_extractor.processing_identity import (
    PROCESSING_REQUEST_ID_PREFIX,
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


def test_no_repair_closes_directly_and_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, context, repair = _scenario(tmp_path, empty=True)

    def unexpected_proposal(**_kwargs: object) -> None:
        raise AssertionError("unchanged trajectory must not invoke MT1")

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        unexpected_proposal,
    )

    first = reconcile_observation_repair(context=context, repair_receipt=repair)
    second = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert isinstance(first, CrossSampleFinalReceipt)
    assert first == second
    assert first.repair_receipt.outcome == "no_repair"
    assert first.cs2_state.trajectory_changed is False
    assert first.mode_track_proposal is None
    _assert_unchanged_application(context, first)
    assert first.final_population == context.population


def test_repaired_unchanged_trajectory_closes_directly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path)
    calls: list[object] = []

    def unexpected_proposal(**_kwargs: object) -> None:
        calls.append(object())
        raise AssertionError("unchanged trajectory must not invoke MT1")

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        unexpected_proposal,
    )

    first = reconcile_observation_repair(context=context, repair_receipt=repair)
    second = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert first == second
    assert first.repair_receipt.outcome == "repaired"
    assert first.repair_receipt.evidence is not None
    assert (
        first.repair_receipt.evidence.member_subject_id
        == fixture.member.member_subject_id
    )
    assert first.cs2_state.trajectory_changed is False
    assert first.mode_track_proposal is None
    _assert_unchanged_application(context, first)
    assert first.final_population == context.population
    assert calls == []
    assert first.membership_authority == "none"
    assert first.value_authority == "none"
    assert first.matrix_authority == "none"
    assert first.activation_authority == "none"
    assert first.writer_authority == "none"


def test_membership_changing_post_mt0_closes_over_successors(
    tmp_path: Path,
) -> None:
    fixture = _repair_fixture(tmp_path, multi_member=True)
    owners = (
        replace(
            _owner("sample-b", "repair"),
            cross_sample_member_subject=fixture.member,
        ),
        replace(
            _owner("sample-c", "repair-other"),
            cross_sample_member_subject=fixture.other_member,
        ),
    )
    edge = classify_cross_sample_initial_neighborhood_edge(
        left_owner=owners[0],
        right_owner=owners[1],
        context="hard_gate",
        config=AlignmentConfig(),
    )
    budget = CrossSampleNeighborhoodBudget(86, 3655)
    predecessor_groups = tuple(
        mint_cross_sample_group_identity(
            processing_request_id=member.processing_request_id,
            member_subjects=(member,),
        )
        for member in fixture.group.member_subjects
    )
    post_application = apply_coupled_successor_proposal(
        predecessors=predecessor_groups,
        successor_member_sets=(fixture.group.member_subjects,),
        edges=(edge,),
        budget=budget,
        population=predecessor_groups,
    )
    assert post_application.predecessors != post_application.successors
    assert post_application.successors == (fixture.group,)
    subject = mint_observation_repair_subject(
        request_identity=fixture.observation.request_identity,
        logical_request=fixture.observation.logical_request,
        predecessor_group=fixture.group,
        member_subject=fixture.member,
        sample_identity=fixture.sample_identity,
        successor_application=post_application,
    )
    repair = close_observation_repair(subject, fixture.result)
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.group.processing_request_id,
        population=(fixture.group,),
        application=post_application,
        owners=owners,
        classified_edges=(edge,),
        anchors=(),
        injection_order={"sample-b": 1, "sample-c": 2},
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1",
            "fixed-band-v1",
            0.1,
            10,
            10,
        ),
        budget=budget,
    )

    result = reconcile_observation_repair(context=context, repair_receipt=repair)

    assert result.mode_track_proposal is None
    assert result.final_application.predecessors == (fixture.group,)
    assert result.final_application.successors == (fixture.group,)
    assert result.final_application.lineage == ()
    assert result.final_application.reason == "unchanged_membership_no_lineage"
    assert result.final_population == (fixture.group,)


def test_final_receipt_cannot_be_forged_by_direct_constructor(tmp_path: Path) -> None:
    _, context, repair = _scenario(tmp_path)
    result = reconcile_observation_repair(context=context, repair_receipt=repair)

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="must be minted by CrossSample",
    ):
        CrossSampleFinalReceipt(
            receipt_id=result.receipt_id,
            processing_request_id=result.processing_request_id,
            post_mt0_application=result.post_mt0_application,
            repair_receipt=result.repair_receipt,
            repair_epoch=result.repair_epoch,
            cs2_state=result.cs2_state,
            pre_trajectory_input_id=result.pre_trajectory_input_id,
            post_trajectory_input_id=result.post_trajectory_input_id,
            mode_track_proposal=result.mode_track_proposal,
            final_application=result.final_application,
            final_population=result.final_population,
            basis_bytes=result.basis_bytes,
            _mint_token=object(),
        )


@pytest.mark.parametrize(
    "duplicate_injection_order",
    [
        (("sample-b", 1), ("sample-b", 2)),
        (("sample-b", 1), ("sample-b", 1)),
    ],
)
def test_duplicate_injection_order_sample_keys_fail_closed_before_csf(
    tmp_path: Path,
    duplicate_injection_order: tuple[tuple[str, int], ...],
) -> None:
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="duplicate sample keys",
    ):
        _scenario(tmp_path, injection_order=duplicate_injection_order)


def test_changed_trajectory_closes_with_existing_nonaccepted_outcome(
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path, multi_member=True, changed=True)
    member_ids = tuple(
        sorted(member.member_subject_id for member in fixture.group.member_subjects)
    )

    result = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
        mode_track_successor_member_subject_ids=(member_ids,),
    )

    assert result.cs2_state.trajectory_changed is True
    assert result.post_trajectory_input_id != result.pre_trajectory_input_id
    assert result.mode_track_proposal is not None
    assert result.mode_track_proposal.variant == "insufficient_training"
    assert result.final_application == context.application
    assert result.final_population == context.population


def test_changed_trajectory_applies_one_exact_mt1_proposal_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path, multi_member=True, changed=True)
    member_ids = tuple(
        sorted(member.member_subject_id for member in fixture.group.member_subjects)
    )
    partition = tuple((member_id,) for member_id in member_ids)
    calls: list[tuple[str, tuple[tuple[str, ...], ...]]] = []

    def accepting_proposal(*, trajectory_input, successor_member_subject_ids):
        groups = tuple(tuple(group) for group in successor_member_subject_ids)
        calls.append((trajectory_input.trajectory_input_id, groups))
        return mint_accepted_mode_track_refinement(
            trajectory_input=trajectory_input,
            successor_member_subject_ids=groups,
        )

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        accepting_proposal,
    )

    first = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
        mode_track_successor_member_subject_ids=partition,
    )
    assert calls == [(first.post_trajectory_input_id, partition)]
    calls.clear()

    second = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
        mode_track_successor_member_subject_ids=partition,
    )

    assert calls == [(second.post_trajectory_input_id, partition)]
    assert first == second
    assert isinstance(first.mode_track_proposal, AcceptedModeTrackRefinement)
    assert first.final_application.reason == "membership_changed"
    assert len(first.final_application.successors) == 2
    assert len(first.final_application.lineage) == 2
    assert first.final_application.predecessors == context.application.successors
    assert tuple(
        sorted(
            member.member_subject_id
            for group in first.final_population
            for member in group.member_subjects
        )
    ) == member_ids


def test_wrong_request_and_predecessor_fail_closed(tmp_path: Path) -> None:
    fixture, context, repair = _scenario(tmp_path / "first")
    other = _repair_fixture(tmp_path / "other", member_seed="other")
    other_repair = close_observation_repair(_subject(other), other.result)

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="post-MT0 application",
    ):
        reconcile_observation_repair(context=context, repair_receipt=other_repair)

    wrong_request = typed_identity_digest(
        PROCESSING_REQUEST_ID_PREFIX,
        canonical_identity_basis_bytes(
            {"test": "cross-sample-reconciliation-wrong-request"}
        ),
    )
    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="processing request",
    ):
        build_cross_sample_post_mt0_context(
            processing_request_id=wrong_request,
            population=context.population,
            application=context.application,
            owners=context.owners,
            classified_edges=context.classified_edges,
            anchors=context.anchors,
            injection_order=context.injection_order,
            evidence_profile=context.evidence_profile,
            budget=context.budget,
        )


def test_stale_post_mt0_trajectory_snapshot_fails_closed(tmp_path: Path) -> None:
    _, context, _ = _scenario(tmp_path / "original")
    _, stale_context, _ = _scenario(tmp_path / "stale", changed=True)

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="trajectory snapshot",
    ):
        replace(context, trajectory_input=stale_context.trajectory_input)


def test_partial_successor_partition_is_rejected_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path, multi_member=True, changed=True)
    member_ids = tuple(
        sorted(member.member_subject_id for member in fixture.group.member_subjects)
    )

    def partial_proposal(*, trajectory_input, successor_member_subject_ids):
        complete = mint_accepted_mode_track_refinement(
            trajectory_input=trajectory_input,
            successor_member_subject_ids=tuple(
                tuple(group) for group in successor_member_subject_ids
            ),
        )
        return replace(
            complete,
            successor_member_subject_ids=(complete.successor_member_subject_ids[0],),
        )

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        partial_proposal,
    )

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="MT1 application rejected atomically",
    ):
        reconcile_observation_repair(
            context=context,
            repair_receipt=repair,
            mode_track_successor_member_subject_ids=tuple(
                (member_id,) for member_id in member_ids
            ),
        )


def test_stale_mt1_proposal_is_rejected_before_application(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture, context, repair = _scenario(tmp_path, multi_member=True, changed=True)
    member_ids = tuple(
        sorted(member.member_subject_id for member in fixture.group.member_subjects)
    )
    stale = mint_accepted_mode_track_refinement(
        trajectory_input=context.trajectory_input,
        successor_member_subject_ids=tuple((member_id,) for member_id in member_ids),
    )
    calls: list[object] = []

    def stale_proposal(**_kwargs: object) -> AcceptedModeTrackRefinement:
        calls.append(object())
        return stale

    monkeypatch.setattr(
        reconciliation,
        "propose_mode_track_successor_partition",
        stale_proposal,
    )

    with pytest.raises(
        CrossSampleReconciliationContractError,
        match="MT1 proposal is stale",
    ):
        reconcile_observation_repair(
            context=context,
            repair_receipt=repair,
            mode_track_successor_member_subject_ids=tuple(
                (member_id,) for member_id in member_ids
            ),
        )
    assert len(calls) == 1


def _scenario(
    tmp_path: Path,
    *,
    empty: bool = False,
    multi_member: bool = False,
    changed: bool = False,
    injection_order: object | None = None,
) -> tuple[_RepairFixture, CrossSamplePostMT0Context, ObservationRepairReceipt]:
    fixture = _repair_fixture(
        tmp_path,
        empty=empty,
        multi_member=multi_member,
    )
    owners = [
        replace(
            _owner("sample-b", "repair", apex_rt=9.0 if changed else 8.5),
            cross_sample_member_subject=fixture.member,
        )
    ]
    if fixture.other_member is not None:
        owners.append(
            replace(
                _owner("sample-c", "repair-other"),
                cross_sample_member_subject=fixture.other_member,
            )
        )
    owners_tuple = tuple(owners)
    edges = ()
    if len(owners_tuple) == 2:
        edges = (
            classify_cross_sample_initial_neighborhood_edge(
                left_owner=owners_tuple[0],
                right_owner=owners_tuple[1],
                context="hard_gate",
                config=AlignmentConfig(),
            ),
        )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=fixture.group.processing_request_id,
        population=(fixture.group,),
        application=fixture.application,
        owners=owners_tuple,
        classified_edges=edges,
        anchors=(),
        injection_order=(
            injection_order
            if injection_order is not None
            else {
                "sample-b": 1,
                **({"sample-c": 2} if fixture.other_member is not None else {}),
            }
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
    repair = close_observation_repair(_subject(fixture), fixture.result)
    return fixture, context, repair


def _assert_unchanged_application(
    context: CrossSamplePostMT0Context,
    receipt: CrossSampleFinalReceipt,
) -> None:
    assert receipt.final_application.predecessors == context.application.successors
    assert receipt.final_application.successors == context.application.successors
    assert receipt.final_application.lineage == ()
    assert receipt.final_application.reason == "unchanged_membership_no_lineage"
