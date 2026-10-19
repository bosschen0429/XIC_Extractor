from __future__ import annotations

import json
from collections.abc import Callable
from copy import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import xic_extractor.alignment.cell_observation.observations as cell_owner
import xic_extractor.alignment.cross_sample.peak_groups as group_owner
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_unique_support,
)
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.test_cid_nl_evidence_spine import (
    _completed_receipt,
    _truth_table_fixture,
)
from tests.test_cross_sample_association_lineage import _final_receipt
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    BackfillPlanningPosition,
    BackfillPlanningReason,
    build_backfill_evaluation_coverage,
    mint_backfill_planning_epoch,
    mint_backfill_position_id,
)
from xic_extractor.alignment.backfill.request_plan import (
    BACKFILL_TRACE_RESULT_SEMANTICS,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservationContractError,
    compose_final_group_cell_observation_population,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_completion import _cell_planning_reason
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.cid_nl_predecessor_cells import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    construct_cross_sample_peak_group_hypotheses,
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)


def _outside_group_fixture(
    tmp_path: Path,
    support_centers: tuple[float, ...],
):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"Multi": "study_sample", "Third": "study_sample"}
    )
    assessments = []
    for stem, centers in (("Multi", support_centers), ("Third", (16.0,))):
        peaks = tuple(
            cid_nl_peak(
                fixture,
                stem,
                apex_rt=center,
                rt_left_min=center - 0.1,
                rt_right_min=center + 0.1,
            )
            for center in centers
        )
        evidence = tuple(
            cid_nl_evidence(fixture, stem, scan_number=100 + index, scan_rt_min=center)
            for index, center in enumerate(centers)
        )
        supports = tuple(
            cid_nl_unique_support(
                fixture, stem, peak=peak, evidence=event, candidate_peaks=peaks
            )
            for peak, event in zip(peaks, evidence, strict=True)
        )
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample(stem),
            method_subject=fixture.method_subject,
            detector_invocation_id=f"outside-group:{stem}",
            evaluation_receipt=replace(
                _completed_receipt(scanned_event_count=len(centers)), query_rt_max=16.5
            ),
            candidate_peaks=peaks,
            evidence_refs=evidence,
            individual_support_refs=supports,
        )
        assert assessment.content.method_result == "SUPPORT"
        assessments.append((fixture.sample(stem), assessment))
    owners = tuple(
        owner_from_cid_nl_assessment_support(
            sample=sample, assessment=assessment, hypothesis=hypothesis
        )
        for sample, assessment in assessments
        for hypothesis in compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=fixture.owners, sample=sample, assessment=assessment
        )
    )
    groups = construct_cross_sample_peak_group_hypotheses(
        owners, config=CID_NL_ALIGNMENT_CONFIG, rt_frame=SampleRtFrame()
    )
    assert len(groups) == len(support_centers) + 1
    return fixture, tuple(assessments), groups


@pytest.mark.parametrize("support_centers", [(8.0,), (8.0, 12.0)])
def test_predecessor_cells_keep_all_supports_with_deterministic_outside_witness(
    tmp_path: Path,
    support_centers: tuple[float, ...],
) -> None:
    fixture, assessments, groups = _outside_group_fixture(tmp_path, support_centers)
    original_contents = None
    for ordered_groups in (groups, tuple(reversed(groups))):
        cells = compose_cid_nl_predecessor_cells(
            processing_owners=fixture.owners,
            groups=ordered_groups,
            sample_assessments=tuple(assessments),
            alignment_config=CID_NL_ALIGNMENT_CONFIG,
            rt_frame=SampleRtFrame(),
        )
        assert len(cells) == len(groups) * len(assessments)
        payloads = [
            json.loads(cell_owner.canonical_cell_observation_bytes(cell.content))
            for cell in cells
        ]
        assert {payload["schema_version"] for payload in payloads} == {
            "cell_observation"
        }
        assert all("outside_group" in payload["content"] for payload in payloads)
        assert len({frozenset(payload["content"]) for payload in payloads}) == 1
        assert any(payload["content"]["outside_group"] is None for payload in payloads)

        assert {
            (
                cell.content.group_hypothesis_id,
                cell.content.canonical_sample_identity_id,
            )
            for cell in cells
        } == {
            (group.group_hypothesis_id, sample.identity.sample_identity_id)
            for group in groups
            for sample, _assessment in assessments
        }
        for sample, assessment in assessments:
            sample_cells = tuple(
                cell
                for cell in cells
                if cell.content.canonical_sample_identity_id
                == sample.identity.sample_identity_id
            )
            assert all(cell.method_assessment is assessment for cell in sample_cells)
            direct = tuple(
                cell for cell in sample_cells if cell.peak_observation is not None
            )
            assert len(direct) == len(assessment.candidate_peaks)
            assert all(
                any(cell.peak_observation is peak for cell in direct)
                for peak in assessment.candidate_peaks
            )
            witness_id = min(cell.content.group_hypothesis_id for cell in direct)
            for cell in sample_cells:
                relation = cell.content.outside_group
                if relation is None:
                    continue
                assert relation.supporting_group_hypothesis_id == witness_id
                assert cell.peak_observation is None and cell.peak_hypothesis is None
                assert cell.content.peak_observation_content_id is None
                assert cell.content.peak_hypothesis_id is None
        contents = {
            (
                cell.content.group_hypothesis_id,
                cell.content.canonical_sample_identity_id,
            ): cell.content
            for cell in cells
        }
        if original_contents is None:
            original_contents = contents
        else:
            assert contents == original_contents
    third_group = next(
        cell.group_context.group
        for cell in cells
        if cell.content.canonical_sample_identity_id
        == fixture.sample("Third").identity.sample_identity_id
        and cell.peak_observation is not None
    )
    with pytest.raises(ValueError, match="one exact sibling SUPPORT context"):
        compose_cid_nl_predecessor_cells(
            processing_owners=fixture.owners,
            groups=(third_group,),
            sample_assessments=tuple(assessments),
            alignment_config=CID_NL_ALIGNMENT_CONFIG,
            rt_frame=SampleRtFrame(),
        )


def _outside_population_handoff(tmp_path: Path):
    fixture, assessments, groups = _outside_group_fixture(tmp_path, (8.0, 12.0))
    cells = compose_cid_nl_predecessor_cells(
        processing_owners=fixture.owners,
        groups=groups,
        sample_assessments=assessments,
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(),
    )
    identities = tuple(
        validate_cross_sample_peak_group_identity(group, require_canonical=True)
        for group in groups
    )
    binding = SimpleNamespace(
        samples=fixture.samples, owners=fixture.owners, request=fixture.owners.request
    )
    coverage = _coverage_for_groups(
        binding, identities, fixture.samples, predecessor_cells=cells
    )
    receipt = _final_receipt(
        _receipt_fixture(binding, fixture.samples),
        predecessors=identities,
        successors=identities,
    )
    return cells, build_cross_sample_association_lineage_handoff(
        coverage=coverage, final_receipt=receipt
    )


def test_cell_handoff_never_reconstructs_completed_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources, handoff = _outside_population_handoff(tmp_path)
    constructions = []
    identity_type = group_owner.CrossSampleGroupIdentity

    def counted(*args, **kwargs):
        constructions.append(kwargs["group_hypothesis_id"])
        return identity_type(*args, **kwargs)

    monkeypatch.setattr(
        group_owner,
        "CrossSampleGroupIdentity",
        counted,
    )
    first = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=sources,
    )
    assert constructions == []
    second = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=sources,
    )
    assert second == first
    assert constructions == []


def test_recovery_evidence_stays_with_its_group_when_peer_has_no_input(
    tmp_path: Path,
) -> None:
    fixture, assessments, all_groups = _outside_group_fixture(tmp_path, (8.0, 12.0))
    initial_sample, initial_assessment = assessments[0]
    recovered_sample = fixture.sample("Third")
    groups = tuple(
        group
        for group in all_groups
        if group.member_subjects[0].canonical_sample_identity_id
        == initial_sample.identity.sample_identity_id
    )
    assert len(groups) == 2
    recovered_group = min(groups, key=lambda group: group.family_center_rt)
    recovered_peak = cid_nl_peak(fixture, "Third", apex_rt=8.0)
    backfill_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=recovered_sample,
        method_subject=fixture.method_subject,
        detector_invocation_id="group-local-recovery",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
        candidate_peaks=(recovered_peak,),
    )
    assert backfill_assessment.content.method_result == "HOLD"
    cells = compose_cid_nl_predecessor_cells(
        processing_owners=fixture.owners,
        groups=groups,
        sample_assessments=((initial_sample, initial_assessment),),
        backfill_assessments_by_group={
            (
                recovered_group.group_hypothesis_id,
                recovered_sample.identity.sample_identity_id,
                fixture.method_subject.method_subject_id,
            ): (recovered_sample, backfill_assessment)
        },
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(),
    )
    assert len(cells) == 3
    recovered_cells = tuple(
        cell
        for cell in cells
        if cell.content.canonical_sample_identity_id
        == recovered_sample.identity.sample_identity_id
    )
    assert len(recovered_cells) == 1
    assert recovered_cells[0].content.group_hypothesis_id == (
        recovered_group.group_hypothesis_id
    )
    assert recovered_cells[0].peak_observation is recovered_peak


def _owner_handoff(tmp_path: Path):
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(zip(fixture.samples, assessments, strict=True)),
    )
    assert spine.group is not None
    group = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    return fixture, spine, _owner_handoff_for_cells(fixture, group, spine.cells)


def _owner_handoff_for_cells(fixture: Any, group: Any, cells: tuple[Any, ...]):
    coverage = _coverage_for_groups(
        fixture,
        (group,),
        fixture.samples,
        predecessor_cells=cells,
    )
    receipt = _final_receipt(
        SimpleNamespace(context=SimpleNamespace(processing_owners=fixture.owners)),
        predecessors=(group,),
        successors=(group,),
    )
    return build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )


def _support_assessment(
    fixture: Any,
    sample_stem: str,
    *,
    suffix: str,
    apex_rt: float,
    scan_number: int,
    seed_count: int = 1,
):
    peak = cid_nl_peak(
        fixture,
        sample_stem,
        apex_rt=apex_rt,
        rt_left_min=apex_rt - 0.1,
        rt_right_min=apex_rt + 0.1,
    )
    evidence_refs = tuple(
        cid_nl_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=apex_rt,
        )
        for index in range(seed_count)
    )
    support_refs = tuple(
        cid_nl_unique_support(
            fixture,
            sample_stem,
            peak=peak,
            evidence=evidence,
        )
        for evidence in evidence_refs
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:{suffix}",
        evaluation_receipt=_completed_receipt(scanned_event_count=seed_count),
        candidate_peaks=(peak,),
        evidence_refs=evidence_refs,
        individual_support_refs=support_refs,
    )


def _not_planned_coverage(
    owners: Any,
    groups: tuple[Any, ...],
    samples: tuple[Any, ...],
    *,
    planning_reason: Callable[[Any, Any], BackfillPlanningReason],
):
    """Coverage shaped like CID-NL completion: no planned requests or terminals."""
    positions = tuple(
        BackfillPlanningPosition(
            position_id=mint_backfill_position_id(
                processing_request_id=group.processing_request_id,
                group_hypothesis_id=group.group_hypothesis_id,
                sample_identity_id=sample.identity.sample_identity_id,
            ),
            group_identity=group,
            sample=sample,
            planning_reason=planning_reason(group, sample),
        )
        for group in groups
        for sample in samples
    )
    epoch = mint_backfill_planning_epoch(
        processing_owners=owners,
        group_identities=groups,
        samples=samples,
        raw_sample_stems=frozenset(sample.lookup_token for sample in samples),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
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


def _coverage_for_groups(
    fixture: Any,
    groups: tuple[Any, ...],
    samples: tuple[Any, ...],
    *,
    predecessor_cells: tuple[Any, ...],
):
    cells = {
        (
            cell.group_context.group_identity.group_hypothesis_id,
            cell.content.canonical_sample_identity_id,
        ): cell
        for cell in predecessor_cells
    }
    return _not_planned_coverage(
        fixture.owners,
        groups,
        samples,
        planning_reason=lambda group, sample: _cell_planning_reason(
            cells.get((group.group_hypothesis_id, sample.identity.sample_identity_id))
        ),
    )


def _receipt_fixture(fixture: Any, samples: tuple[Any, ...]):
    acquisitions = {
        sample.lookup_token: source
        for sample, source in zip(
            fixture.samples,
            fixture.request.acquisition_sources,
            strict=True,
        )
        if sample in samples
    }
    return SimpleNamespace(
        context=SimpleNamespace(
            samples=samples,
            processing_owners=fixture.owners,
        ),
        acquisitions=acquisitions,
    )


def test_composer_mints_exact_cartesian_owner_population_and_states(
    tmp_path: Path,
) -> None:
    _fixture, spine, handoff = _owner_handoff(tmp_path)

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )

    assert len(cells) == len(handoff.final_population) * len(
        handoff.coverage.epoch.samples
    )
    assert {cell.evidence_state for cell in cells} == {
        "direct_observation",
        "unresolved",
        "rejected_observation",
        "unavailable",
    }
    for cell in cells:
        assert cell.group is handoff.final_population[0]
        assert cell.position_associations
        assert len(cell.position_associations) == len(cell.predecessor_cells)
        assert all(
            any(association is item for item in handoff.position_associations)
            for association in cell.position_associations
        )


def test_composer_rejects_missing_or_duplicate_owner_source_cells(
    tmp_path: Path,
) -> None:
    _fixture, spine, handoff = _owner_handoff(tmp_path)

    with pytest.raises(CellObservationContractError, match="incomplete"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=spine.cells[:-1],
        )

    with pytest.raises(CellObservationContractError, match="duplicates"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=spine.cells + (spine.cells[0],),
        )


def test_composer_distinguishes_completed_negative_search(
    tmp_path: Path,
) -> None:
    fixture, spine, handoff = _owner_handoff(tmp_path)
    sample = fixture.sample("s4")
    negative_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:s4:completed-negative",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
    )
    negative_cell = mint_cell_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        group_context=spine.cells[0].group_context,
        method_assessment=negative_assessment,
    )
    predecessor_cells = tuple(
        negative_cell
        if cell.content.canonical_sample_identity_id
        == sample.identity.sample_identity_id
        else cell
        for cell in spine.cells
    )
    handoff = _owner_handoff_for_cells(
        fixture,
        handoff.coverage.epoch.group_identities[0],
        predecessor_cells,
    )

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )

    s4 = next(cell for cell in cells if cell.canonical_sample is sample)
    assert s4.evidence_state == "searched_no_observation"


def test_composer_rejects_same_sample_crossed_source_to_position(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(tmp_path, sample_stems=("s1",))
    sample = fixture.sample("s1")
    first_assessment = _support_assessment(
        fixture,
        "s1",
        suffix="g1",
        apex_rt=8.0,
        scan_number=41,
    )
    second_assessment = _support_assessment(
        fixture,
        "s1",
        suffix="g2",
        apex_rt=8.3,
        scan_number=42,
    )
    first_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((sample, first_assessment),),
    )
    second_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((sample, second_assessment),),
    )
    first_group = validate_cross_sample_peak_group_identity(
        first_spine.group,
        require_canonical=True,
    )
    second_group = validate_cross_sample_peak_group_identity(
        second_spine.group,
        require_canonical=True,
    )
    assert first_group != second_group
    samples = (sample,)
    first_cell = first_spine.cells[0]
    second_cell = second_spine.cells[0]
    coverage = _coverage_for_groups(
        fixture,
        (first_group, second_group),
        samples,
        predecessor_cells=(first_cell, second_cell),
    )
    receipt = _final_receipt(
        _receipt_fixture(fixture, samples),
        predecessors=(first_group, second_group),
        successors=(first_group, second_group),
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    composed = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=(first_cell, second_cell),
    )
    assert len(composed) == 2

    crossed = copy(first_cell)
    object.__setattr__(crossed, "group_context", second_cell.group_context)
    with pytest.raises(
        CellObservationContractError, match="duplicates a coverage position"
    ):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(crossed, second_cell),
        )


@pytest.mark.parametrize("outside_support", (False, True))
def test_composer_merge_retains_both_predecessor_lanes_deterministically(
    tmp_path: Path,
    outside_support: bool,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2"),
    )
    samples = (fixture.sample("s1"), fixture.sample("s2"))
    first_assessment = _support_assessment(
        fixture,
        "s1",
        suffix="merge",
        apex_rt=8.0,
        scan_number=51,
    )
    second_assessment = _support_assessment(
        fixture,
        "s2",
        suffix="merge",
        apex_rt=8.3,
        scan_number=52,
    )
    left_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((samples[0], first_assessment),),
    )
    right_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((samples[1], second_assessment),),
    )
    left = validate_cross_sample_peak_group_identity(
        left_spine.group,
        require_canonical=True,
    )
    right = validate_cross_sample_peak_group_identity(
        right_spine.group,
        require_canonical=True,
    )
    merged = mint_cross_sample_group_identity(
        processing_request_id=left.processing_request_id,
        member_subjects=left.member_subjects + right.member_subjects,
    )
    if outside_support:
        predecessor_cells = tuple(
            group_spine.cells[0]
            if group_spine is supporting_spine
            else mint_cell_observation_ref(
                processing_owners=fixture.owners,
                sample=sample,
                target=fixture.target,
                group_context=group_spine.cells[0].group_context,
                method_assessment=supporting_spine.cells[0].method_assessment,
                outside_group_context=supporting_spine.cells[0].group_context,
            )
            for group_spine in (left_spine, right_spine)
            for sample, supporting_spine in zip(
                samples, (left_spine, right_spine), strict=True
            )
        )
    else:
        predecessor_cells = (left_spine.cells[0], right_spine.cells[0])
    coverage = _coverage_for_groups(
        fixture,
        (left, right),
        samples,
        predecessor_cells=predecessor_cells,
    )
    receipt = _final_receipt(
        _receipt_fixture(fixture, samples),
        predecessors=(left, right),
        successors=(merged,),
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    forward = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )
    reverse = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=tuple(reversed(predecessor_cells)),
    )
    assert forward == reverse
    assert len(forward) == 2
    assert all(
        item.group is merged
        and len(item.position_associations) == 2
        # A lane without discovery input keeps its position but has no cell.
        and len(item.predecessor_cells) == (2 if outside_support else 1)
        and sorted(item.evidence_states) == ["direct_observation", "not_searched"]
        for item in forward
    )
    expected_positions_by_sample = {
        item.canonical_sample.identity.sample_identity_id: {
            association.position.position_id
            for association in handoff.position_associations
            if association.position.sample is item.canonical_sample
        }
        for item in forward
    }
    assert all(
        {association.position.position_id for association in item.position_associations}
        == expected_positions_by_sample[
            item.canonical_sample.identity.sample_identity_id
        ]
        for item in forward
    )


def test_composer_split_keeps_cartesian_not_searched_remainder(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2"),
    )
    samples = (fixture.sample("s1"), fixture.sample("s2"))
    first_assessment = _support_assessment(
        fixture,
        "s1",
        suffix="split",
        apex_rt=8.0,
        scan_number=61,
        seed_count=2,
    )
    second_assessment = _support_assessment(
        fixture,
        "s2",
        suffix="split",
        apex_rt=8.0,
        scan_number=62,
        seed_count=2,
    )
    predecessor_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=(
            (samples[0], first_assessment),
            (samples[1], second_assessment),
        ),
    )
    predecessor = validate_cross_sample_peak_group_identity(
        predecessor_spine.group,
        require_canonical=True,
    )
    members_by_sample = {
        member.canonical_sample_identity_id: member
        for member in predecessor.member_subjects
    }
    left = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=(members_by_sample[samples[0].identity.sample_identity_id],),
    )
    right = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=(members_by_sample[samples[1].identity.sample_identity_id],),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        samples,
        predecessor_cells=predecessor_spine.cells,
    )
    receipt = _final_receipt(
        _receipt_fixture(fixture, samples),
        predecessors=(predecessor,),
        successors=(left, right),
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    composed = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_spine.cells,
    )
    assert len(composed) == 4
    by_key = {(item.group, item.canonical_sample): item for item in composed}
    assert by_key[(left, samples[0])].evidence_state == "direct_observation"
    assert by_key[(right, samples[1])].evidence_state == "direct_observation"
    assert by_key[(left, samples[1])].evidence_state == "not_searched"
    assert by_key[(right, samples[0])].evidence_state == "not_searched"
    assert not by_key[(left, samples[1])].position_associations
    assert not by_key[(right, samples[0])].position_associations


def test_final_member_rejects_hold_method_lane(tmp_path: Path) -> None:
    fixture, spine, handoff = _owner_handoff(tmp_path)
    sample = fixture.sample("s1")
    hold_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:s1:hold-invalid-final-member",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
    )
    source = next(
        cell
        for cell in spine.cells
        if cell.content.canonical_sample_identity_id
        == sample.identity.sample_identity_id
    )
    invalid_source = mint_cell_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        group_context=source.group_context,
        method_assessment=hold_assessment,
    )
    predecessor_cells = tuple(
        invalid_source if cell is source else cell for cell in spine.cells
    )

    with pytest.raises(CellObservationContractError, match="HOLD or REJECT"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=predecessor_cells,
        )
