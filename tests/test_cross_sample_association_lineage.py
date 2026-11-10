"""No-RAW contract tests for the CrossSample association/lineage handoff."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.peak_detection.integration as integration_runtime
import xic_extractor.peak_detection.ms1_shape_identity as smoothing_runtime
from tests.alignment_pipeline_helpers import _owner
from tests.cross_sample_association_lineage_fixtures import (
    _CoverageContext,
    _final_receipt,
    _Fixture,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationContractError,
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    mint_cross_sample_group_identity,
)
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.sample_metadata import SampleMetadata


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
    assert first.predecessor_associations[0].successors == (group,)
    assert len(first.position_associations) == 2
    assert {item.position.key for item in first.position_associations} == {
        item.key for item in coverage.positions
    }
    assert all(
        item.final_successor is group and item.resolution == "unchanged_identity"
        for item in first.position_associations
    )


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
        == coverage.group_identities
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
        item.position.sample.raw_stem: item.final_successor
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


def test_rejected_disposition_is_retained(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, sample_stems=("sample-a",))
    predecessor = _group(fixture, ("sample-a",))
    rejected = mint_cross_sample_group_identity(
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


def test_zero_successor_rejects(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "one", sample_stems=("sample-a",))
    other = _fixture(tmp_path / "two", sample_stems=("sample-x",))
    group = _group(fixture, ("sample-a",))
    _group(other, ("sample-x",))
    coverage = _coverage(fixture, (group,))

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
        CrossSampleAssociationContractError,
        match="no final successor",
    ):
        build_cross_sample_association_lineage_handoff(
            coverage=coverage,
            final_receipt=replace(valid_receipt, partition_transition=unavailable),
        )


def test_predecessor_order_preserves_each_exact_association_population(
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

    assert {item.position.key for item in forward.position_associations} == {
        item.position.key for item in reverse.position_associations
    }
    assert tuple(item.predecessor for item in forward.predecessor_associations) == (
        left,
        right,
    )
    assert tuple(item.predecessor for item in reverse.predecessor_associations) == (
        right,
        left,
    )


def _coverage_context(
    tmp_path: Path,
    *,
    sample_order: tuple[str, ...],
) -> _CoverageContext:
    samples = tuple(
        SampleMetadata(
            sample_name=sample,
            raw_stem=sample,
            injection_order=index,
            sample_role="study_sample",
        )
        for index, sample in enumerate(sample_order, start=1)
    )
    return _CoverageContext(
        samples=samples,
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(),
    )


def _fixture(tmp_path: Path, *, sample_stems: tuple[str, ...]) -> _Fixture:
    fixture = _Fixture(_coverage_context(tmp_path, sample_order=sample_stems))
    for sample_stem, sample in fixture.samples.items():
        original = _owner(sample_stem, f"association-{sample_stem}")
        member = original.cross_sample_member_subject
        assert member is not None
        fixture.members[sample_stem] = CrossSampleGroupMemberSubject(
            sample_key=sample.sample_name,
            subject_kind=member.subject_kind,
            subject_id=member.subject_id,
            physical_peaks=member.physical_peaks,
        )
    return fixture


def _group(
    fixture: _Fixture,
    sample_stems: tuple[str, ...],
) -> CrossSampleGroupIdentity:
    return mint_cross_sample_group_identity(
        member_subjects=tuple(fixture.members[sample] for sample in sample_stems),
    )


def _coverage(
    fixture: _Fixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> BackfillEvaluationCoverageBatch:
    context = fixture.context
    positions = tuple(
        BackfillPlanningPosition(
            group_identity=group,
            sample=sample,
            planning_reason="no_discovery_input",
        )
        for group in groups
        for sample in context.samples
    )
    return BackfillEvaluationCoverageBatch(
        group_identities=groups,
        samples=context.samples,
        positions=positions,
    )
