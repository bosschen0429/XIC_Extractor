"""No-RAW contract tests for the CrossSample association/lineage handoff."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import xic_extractor.alignment.cross_sample_peak_groups as cross_sample_owner
import xic_extractor.peak_detection.integration as integration_runtime
import xic_extractor.peak_detection.ms1_shape_identity as smoothing_runtime
from tests.alignment_pipeline_helpers import _owner
from tests.test_alignment_owner_backfill import _feature
from tests.test_backfill_evaluation_coverage import _coverage_fixture
from xic_extractor.alignment.backfill import (
    acquisition as owner_backfill_acquisition_runtime,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillEvaluationCoverageContext,
    build_backfill_evaluation_coverage,
    build_backfill_planning_epoch,
)
from xic_extractor.alignment.backfill.request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
    BackfillRequestEquivalenceContext,
    build_backfill_request_plan,
)
from xic_extractor.alignment.cross_sample import (
    CrossSampleAssociationContractError,
    CrossSamplePredecessorAssociation,
    build_cross_sample_association_lineage_handoff,
    validate_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingContractError,
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    mint_cross_sample_group_identity,
)
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.processing_identity import (
    canonical_identity_basis_bytes,
    typed_identity_digest,
)


def test_unchanged_identity_closes_complete_cartesian_population(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a", "sample-b"))
    group = _group(fixture, ("sample-a",))
    coverage = _coverage(fixture, (group,))
    receipt = _final_receipt(fixture, predecessors=(group,), successors=(group,))

    first = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    second = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )

    assert first == second
    assert first.coverage is coverage
    assert first.final_receipt is receipt
    assert first.predecessor_associations[0].successors == (group,)
    assert len(first.position_associations) == 2
    assert {item.position.position_id for item in first.position_associations} == {
        item.position_id for item in coverage.epoch.positions
    }
    assert all(
        item.final_successor is group and item.resolution == "unchanged_identity"
        for item in first.position_associations
    )
    assert first.membership_authority == "none"
    assert first.value_authority == "none"
    assert first.matrix_authority == "none"
    assert first.writer_authority == "none"
    validate_cross_sample_association_lineage_handoff(first)


def test_alignment_outputs_carries_exact_owner_coverage(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a",))
    group = _group(fixture, ("sample-a",))
    coverage = _coverage(fixture, (group,))

    outputs = AlignmentRunOutputs(
        owner_backfill_evaluation_coverage=coverage,
    )

    assert outputs.owner_backfill_evaluation_coverage is coverage


def test_pure_association_adds_no_xic_smoothing_or_integration_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a",))
    group = _group(fixture, ("sample-a",))
    coverage = _coverage(fixture, (group,))
    receipt = _final_receipt(fixture, predecessors=(group,), successors=(group,))
    calls: list[str] = []

    def forbidden(name: str):
        def fail(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(
                f"association called forbidden scientific work: {name}"
            )

        return fail

    monkeypatch.setattr(
        owner_backfill_acquisition_runtime,
        "_extract_unique_many",
        forbidden("extract_xic"),
    )
    monkeypatch.setattr(
        smoothing_runtime,
        "gaussian_smooth_values",
        forbidden("smoothing"),
    )
    monkeypatch.setattr(
        integration_runtime,
        "integrate_area_counts_seconds",
        forbidden("integration"),
    )

    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )

    assert handoff.coverage is coverage
    assert calls == []


def test_merge_retains_each_predecessor_and_position(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a", "sample-b"))
    left = _group(fixture, ("sample-a",))
    right = _group(fixture, ("sample-b",))
    merged = _group(fixture, ("sample-a", "sample-b"))
    coverage = _coverage(fixture, (left, right))
    receipt = _final_receipt(
        fixture,
        predecessors=(left, right),
        successors=(merged,),
    )

    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )

    assert (
        tuple(item.predecessor for item in handoff.predecessor_associations)
        == coverage.epoch.group_identities
    )
    assert all(
        item.successors == (merged,) for item in handoff.predecessor_associations
    )
    assert len(handoff.position_associations) == 4
    assert all(
        item.final_successor == merged and item.resolution == "unique_successor"
        for item in handoff.position_associations
    )


def test_split_maps_only_the_exact_same_sample_member(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a", "sample-b"))
    predecessor = _group(fixture, ("sample-a", "sample-b"))
    left = _group(fixture, ("sample-a",))
    right = _group(fixture, ("sample-b",))
    coverage = _coverage(fixture, (predecessor,))
    receipt = _final_receipt(
        fixture,
        predecessors=(predecessor,),
        successors=(left, right),
    )

    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )

    assert handoff.predecessor_associations[0].successors == tuple(
        sorted((left, right), key=lambda group: group.group_hypothesis_id)
    )
    successor_by_sample = {
        item.position.sample.lookup_token: item.final_successor
        for item in handoff.position_associations
    }
    assert successor_by_sample == {"sample-a": left, "sample-b": right}
    assert all(
        item.resolution == "exact_sample_member_successor"
        for item in handoff.position_associations
    )


def test_split_nonmember_position_rejects_without_a_handoff(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b", "sample-c"),
    )
    predecessor = _group(fixture, ("sample-a", "sample-b"))
    left = _group(fixture, ("sample-a",))
    right = _group(fixture, ("sample-b",))

    with pytest.raises(
        CrossSampleAssociationContractError,
        match="ambiguous split position",
    ):
        build_cross_sample_association_lineage_handoff(
            coverage=_coverage(fixture, (predecessor,)),
            final_receipt=_final_receipt(
                fixture,
                predecessors=(predecessor,),
                successors=(left, right),
            ),
        )


def test_rejected_disposition_is_retained_without_decision_authority(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a",))
    predecessor = _group(fixture, ("sample-a",))
    rejected = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=predecessor.member_subjects,
        disposition="rejected",
    )
    coverage = _coverage(fixture, (predecessor,))
    receipt = _final_receipt(
        fixture,
        predecessors=(predecessor,),
        successors=(rejected,),
    )

    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )

    assert handoff.position_associations[0].final_successor is rejected
    assert handoff.position_associations[0].final_successor.disposition == "rejected"
    assert handoff.activation_authority == "none"


def test_crossed_request_and_zero_successor_reject(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "one", sample_stems=("sample-a",))
    other = _fixture(tmp_path / "two", sample_stems=("sample-x",))
    group = _group(fixture, ("sample-a",))
    other_group = _group(other, ("sample-x",))
    coverage = _coverage(fixture, (group,))

    with pytest.raises(
        CrossSampleAssociationContractError,
        match="cross processing request",
    ):
        build_cross_sample_association_lineage_handoff(
            coverage=coverage,
            final_receipt=_final_receipt(
                other,
                predecessors=(other_group,),
                successors=(other_group,),
            ),
        )

    valid_receipt = _final_receipt(
        fixture,
        predecessors=(group,),
        successors=(group,),
    )
    unavailable = CrossSamplePartitionTransition(
        status="unassessable",
        predecessors=(group,),
        successors=(),
        lineage=(),
        reason="eligible_neighborhood_exceeds_budget",
    )
    with pytest.raises(
        CrossSampleGroupingContractError,
        match="partition crossed",
    ):
        replace(valid_receipt, partition_transition=unavailable)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "reordered"])
def test_handoff_validator_rejects_edited_population(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a", "sample-b"))
    group = _group(fixture, ("sample-a",))
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=_coverage(fixture, (group,)),
        final_receipt=_final_receipt(
            fixture,
            predecessors=(group,),
            successors=(group,),
        ),
    )
    forged = copy(handoff)
    positions = handoff.position_associations
    if mutation == "missing":
        changed = positions[:-1]
    elif mutation == "duplicate":
        changed = (*positions, positions[0])
    elif mutation == "extra":
        changed = (positions[0], *positions)
    else:
        changed = tuple(reversed(positions))
    object.__setattr__(forged, "position_associations", changed)

    with pytest.raises(
        CrossSampleAssociationContractError,
        match=(
            "position associations are missing, duplicate, extra, partial, or reordered"
        ),
    ):
        validate_cross_sample_association_lineage_handoff(forged)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "partial"])
def test_handoff_validator_rejects_edited_predecessor_population(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
    )
    predecessor = _group(fixture, ("sample-a", "sample-b"))
    left = _group(fixture, ("sample-a",))
    right = _group(fixture, ("sample-b",))
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=_coverage(fixture, (predecessor,)),
        final_receipt=_final_receipt(
            fixture,
            predecessors=(predecessor,),
            successors=(left, right),
        ),
    )
    associations = handoff.predecessor_associations
    changed: tuple[CrossSamplePredecessorAssociation, ...]
    if mutation == "missing":
        changed = ()
    elif mutation == "duplicate":
        changed = (*associations, associations[0])
    elif mutation == "extra":
        changed = (
            *associations,
            CrossSamplePredecessorAssociation(
                predecessor=left,
                successors=(left,),
            ),
        )
    else:
        changed = (
            replace(associations[0], successors=(associations[0].successors[0],)),
        )
    forged = copy(handoff)
    object.__setattr__(forged, "predecessor_associations", changed)

    with pytest.raises(
        CrossSampleAssociationContractError,
        match=(
            "predecessor associations are missing, duplicate, extra, partial, "
            "or reordered"
        ),
    ):
        validate_cross_sample_association_lineage_handoff(forged)


def test_owner_order_changes_identity_while_each_output_remains_canonical(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a", "sample-b"))
    left = _group(fixture, ("sample-a",))
    right = _group(fixture, ("sample-b",))
    merged = _group(fixture, ("sample-a", "sample-b"))
    receipt = _final_receipt(
        fixture,
        predecessors=(left, right),
        successors=(merged,),
    )
    forward_coverage = _coverage(fixture, (left, right))
    reverse_coverage = _coverage(fixture, (right, left))

    forward = build_cross_sample_association_lineage_handoff(
        coverage=forward_coverage,
        final_receipt=receipt,
    )
    reverse = build_cross_sample_association_lineage_handoff(
        coverage=reverse_coverage,
        final_receipt=receipt,
    )

    assert forward_coverage.coverage_id != reverse_coverage.coverage_id
    assert forward.handoff_id != reverse.handoff_id
    assert tuple(item.predecessor for item in forward.predecessor_associations) == (
        left,
        right,
    )
    assert tuple(item.predecessor for item in reverse.predecessor_associations) == (
        right,
        left,
    )
    validate_cross_sample_association_lineage_handoff(forward)
    validate_cross_sample_association_lineage_handoff(reverse)


class _Fixture:
    def __init__(self, base: Any) -> None:
        self.context = base.context
        self.acquisitions = base.acquisitions
        self.samples = {sample.lookup_token: sample for sample in self.context.samples}
        self.members: dict[str, CrossSampleGroupMemberSubject] = {}


def _fixture(tmp_path: Path, *, sample_stems: tuple[str, ...]) -> _Fixture:
    base = _coverage_fixture(tmp_path, sample_order=sample_stems)
    fixture = _Fixture(base)
    request_id = fixture.context.processing_owners.request.processing_request_id
    for sample_stem, sample in fixture.samples.items():
        original = _owner(sample_stem, f"association-{sample_stem}")
        member = original.cross_sample_member_subject
        assert member is not None
        basis = canonical_identity_basis_bytes(
            {
                "schema_version": "group_member_subject",
                "processing_request_id": request_id,
                "canonical_sample_identity_id": sample.identity.sample_identity_id,
                "subject": {
                    "kind": member.subject_kind,
                    "id": member.subject_id,
                },
                "physical_observation_ids": list(member.physical_observation_ids),
            }
        )
        fixture.members[sample_stem] = CrossSampleGroupMemberSubject(
            member_subject_id=typed_identity_digest("xic.group-member-subject:", basis),
            processing_request_id=request_id,
            canonical_sample_identity_id=sample.identity.sample_identity_id,
            subject_kind=member.subject_kind,
            subject_id=member.subject_id,
            physical_observation_ids=member.physical_observation_ids,
            basis_bytes=basis,
        )
    return fixture


def _group(
    fixture: _Fixture,
    sample_stems: tuple[str, ...],
) -> CrossSampleGroupIdentity:
    return mint_cross_sample_group_identity(
        processing_request_id=(
            fixture.context.processing_owners.request.processing_request_id
        ),
        member_subjects=tuple(fixture.members[sample] for sample in sample_stems),
    )


def _runtime_feature(group: CrossSampleGroupIdentity):
    owner = replace(
        _owner("sample-owner", group.group_hypothesis_id[-8:]),
        cross_sample_member_subject=group.member_subjects[0],
    )
    return replace(
        _feature(),
        owners=(owner,),
        group_hypothesis_id=group.group_hypothesis_id,
        processing_request_id=group.processing_request_id,
        member_subjects=group.member_subjects,
        identity_basis_bytes=group.basis_bytes,
        disposition=group.disposition,
    )


def _coverage(
    fixture: _Fixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> BackfillEvaluationCoverageBatch:
    features = tuple(_runtime_feature(group) for group in groups)
    context = BackfillEvaluationCoverageContext(
        processing_owners=fixture.context.processing_owners,
        input_features=features,
        samples=fixture.context.samples,
        sample_order=fixture.context.sample_order,
        raw_sample_stems=fixture.context.raw_sample_stems,
        alignment_config=fixture.context.alignment_config,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="selected-families",
        selected_family_ids=frozenset({"not-this-family"}),
    )
    equivalence = BackfillRequestEquivalenceContext(
        acquisition_sources=fixture.acquisitions,
        backend=context.backend,
        window_strategy=context.window_strategy,
        superwindow_span_factor=context.superwindow_span_factor,
        validation_mode=context.validation_mode,
        result_semantics=context.result_semantics,
    )
    request_plan = build_backfill_request_plan(
        (),
        sample_order=context.sample_order,
        raw_sample_stems=context.raw_sample_stems,
        alignment_config=context.alignment_config,
        equivalence_context=equivalence,
    )
    epoch = build_backfill_planning_epoch(
        context,
        selected_features=(),
        request_plan=request_plan,
    )
    return build_backfill_evaluation_coverage(epoch, ())


def _final_receipt(
    fixture: _Fixture,
    *,
    predecessors: tuple[CrossSampleGroupIdentity, ...],
    successors: tuple[CrossSampleGroupIdentity, ...],
) -> CrossSampleGroupingResult:
    canonical_predecessors = tuple(
        sorted(predecessors, key=lambda group: group.group_hypothesis_id)
    )
    canonical_successors = tuple(
        sorted(successors, key=lambda group: group.group_hypothesis_id)
    )
    unchanged = {group.group_hypothesis_id for group in canonical_predecessors} == {
        group.group_hypothesis_id for group in canonical_successors
    }
    application = CrossSamplePartitionTransition(
        status="assessable",
        predecessors=canonical_predecessors,
        successors=canonical_successors,
        lineage=(
            ()
            if unchanged
            else cross_sample_owner._expected_cross_sample_lineage(
                predecessors=canonical_predecessors,
                successors=canonical_successors,
            )
        ),
        reason=(
            "unchanged_membership_no_lineage" if unchanged else "membership_changed"
        ),
    )
    return CrossSampleGroupingResult(
        processing_owners=fixture.context.processing_owners,
        partition_transition=application,
    )
