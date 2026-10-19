"""No-RAW contract tests for the CrossSample association/lineage handoff."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample.peak_groups as cross_sample_owner
import xic_extractor.peak_detection.integration as integration_runtime
import xic_extractor.peak_detection.ms1_shape_identity as smoothing_runtime
from tests.alignment_pipeline_helpers import _owner
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillEvaluationCoverageBatch,
    BackfillPlanningPosition,
    build_backfill_evaluation_coverage,
    mint_backfill_planning_epoch,
    mint_backfill_position_id,
)
from xic_extractor.alignment.backfill.request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    CrossSampleAssociationContractError,
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.grouping_result import (
    CrossSampleGroupingResult,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSampleGroupIdentity,
    CrossSampleGroupMemberSubject,
    CrossSamplePartitionTransition,
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.processing_identity import (
    AcquisitionInputBinding,
    ProcessingOwnerReferences,
    build_acquisition_source_identity,
    build_processing_request_identity,
    canonical_identity_basis_bytes,
    compose_processing_owner_references,
    issue_processing_attempt,
    typed_identity_digest,
)
from xic_extractor.sample_metadata import (
    ResolvedCanonicalSampleIdentity,
    SampleMetadata,
    mint_sample_metadata_snapshot,
    resolve_canonical_sample_identity,
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


def test_rejected_disposition_is_retained(
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
        CrossSampleAssociationContractError,
        match="no final successor",
    ):
        build_cross_sample_association_lineage_handoff(
            coverage=coverage,
            final_receipt=replace(valid_receipt, partition_transition=unavailable),
        )


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


@dataclass(frozen=True, slots=True)
class _CoverageContext:
    processing_owners: ProcessingOwnerReferences
    samples: tuple[ResolvedCanonicalSampleIdentity, ...]
    sample_order: tuple[str, ...]
    raw_sample_stems: frozenset[str]
    alignment_config: AlignmentConfig


class _Fixture:
    def __init__(self, context: _CoverageContext) -> None:
        self.context = context
        self.samples = {sample.lookup_token: sample for sample in self.context.samples}
        self.members: dict[str, CrossSampleGroupMemberSubject] = {}


def _coverage_context(
    tmp_path: Path,
    *,
    sample_order: tuple[str, ...],
) -> _CoverageContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    acquisitions = []
    bindings = []
    metadata = []
    for index, sample in enumerate(sample_order, start=1):
        raw_path = tmp_path / f"{sample}.raw"
        raw_path.write_bytes(f"coverage:{sample}".encode())
        acquisition = build_acquisition_source_identity(
            raw_path,
            acquisition_format="thermo_raw",
            observation_namespace="test-backfill-coverage",
        )
        acquisitions.append(acquisition)
        bindings.append(
            AcquisitionInputBinding(
                acquisition_source_id=acquisition.acquisition_source_id,
                adapter_input_key=sample,
                injection_order=index,
            )
        )
        metadata.append(
            SampleMetadata(
                sample_name=sample,
                raw_stem=sample,
                injection_order=index,
                sample_role="study_sample",
            )
        )
    request = build_processing_request_identity(
        acquisition_sources=tuple(acquisitions),
        acquisition_bindings=tuple(bindings),
        scientific_input_schema_version="backfill_coverage_test",
        processing_algorithm_version="backfill_coverage_test",
        processing_contract_version="processing_identity",
        scientific_inputs={"fixture": "backfill-evaluation-coverage"},
    )
    snapshot = mint_sample_metadata_snapshot(
        tuple(metadata),
        identity_namespace="study:backfill-evaluation-coverage",
    )
    resolved = tuple(
        resolve_canonical_sample_identity(snapshot, sample) for sample in sample_order
    )
    return _CoverageContext(
        processing_owners=compose_processing_owner_references(
            request=request,
            attempt=issue_processing_attempt(request),
            sample_snapshot=snapshot,
            sample_resolutions=resolved,
        ),
        samples=resolved,
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
    )


def _fixture(tmp_path: Path, *, sample_stems: tuple[str, ...]) -> _Fixture:
    fixture = _Fixture(_coverage_context(tmp_path, sample_order=sample_stems))
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


def _coverage(
    fixture: _Fixture,
    groups: tuple[CrossSampleGroupIdentity, ...],
) -> BackfillEvaluationCoverageBatch:
    context = fixture.context
    positions = tuple(
        BackfillPlanningPosition(
            position_id=mint_backfill_position_id(
                processing_request_id=group.processing_request_id,
                group_hypothesis_id=group.group_hypothesis_id,
                sample_identity_id=sample.identity.sample_identity_id,
            ),
            group_identity=group,
            sample=sample,
            planning_reason="no_discovery_input",
        )
        for group in groups
        for sample in context.samples
    )
    epoch = mint_backfill_planning_epoch(
        processing_owners=context.processing_owners,
        group_identities=groups,
        samples=context.samples,
        raw_sample_stems=context.raw_sample_stems,
        alignment_config=context.alignment_config,
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="full-audit",
        selected_family_ids=frozenset(),
        positions=positions,
    )
    return build_backfill_evaluation_coverage(epoch)


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
        rt_frame=SampleRtFrame(),
    )
