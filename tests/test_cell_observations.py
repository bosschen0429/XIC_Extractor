from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import xic_extractor.alignment.cross_sample.peak_groups as group_owner
from tests.cell_observations_fixtures import (
    _coverage_for_groups,
    _group_and_predecessor_cells,
    _owner_handoff,
    _owner_handoff_for_cells,
    _receipt_fixture,
    _support_assessment,
)
from tests.cid_nl_assessment_fixtures import (
    _completed_receipt,
)
from tests.cid_nl_peak_support_no_raw_fixture import (
    cid_nl_evidence,
    cid_nl_peak,
    cid_nl_unique_support,
)
from tests.cross_sample_association_lineage_fixtures import _final_receipt
from tests.group_fixtures import group_from_members
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.untargeted_cid_nl_no_raw_fixture import (
    build_untargeted_cid_nl_no_raw_fixture,
)
from xic_extractor.alignment.band_grouping import group_peak_bands
from xic_extractor.alignment.cell_observation.observations import (
    compose_final_group_cell_observation_population,
    mint_cell_observation,
)
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.cid_nl_predecessor_cells import (
    compose_cid_nl_predecessor_cells,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.hypothesis_owner_adapter import (
    compose_cid_nl_peak_hypotheses_from_assessment,
    owner_from_cid_nl_assessment_support,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment,
)


def _outside_group_fixture(
    tmp_path: Path,
    support_centers: tuple[float, ...],
):
    fixture = build_untargeted_cid_nl_no_raw_fixture(
        tmp_path, sample_roles={"Multi": "sample", "Third": "sample"}
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
        assessment = mint_cid_nl_method_assessment(
            sample=fixture.sample(stem),
            acquisition_source=fixture.source(fixture.sample(stem)),
            method_subject=fixture.method_subject,
            detector_invocation_id=f"outside-group:{stem}",
            evaluation_receipt=replace(_completed_receipt(), query_rt_max=16.5),
            candidate_peaks=peaks,
            evidence_refs=evidence,
            individual_support_refs=supports,
        )
        assert assessment.method_result == "SUPPORT"
        assessments.append((fixture.sample(stem), assessment))
    owners = tuple(
        owner_from_cid_nl_assessment_support(
            sample=sample,
            assessment=assessment,
            hypothesis=hypothesis,
        )
        for sample, assessment in assessments
        for hypothesis in compose_cid_nl_peak_hypotheses_from_assessment(
            sample=sample, assessment=assessment
        )
    )
    groups = group_peak_bands(
        owners,
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(),
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
            groups=ordered_groups,
            sample_assessments=tuple(assessments),
            alignment_config=CID_NL_ALIGNMENT_CONFIG,
            rt_frame=SampleRtFrame(),
        )
        assert len(cells) == len(groups) * len(assessments)
        assert any(cell.outside_group is None for cell in cells)

        assert {
            (
                cell.group_hypothesis_id,
                cell.sample_key,
            )
            for cell in cells
        } == {
            (group.group_hypothesis_id, sample.sample_name)
            for group in groups
            for sample, _assessment in assessments
        }
        for sample, assessment in assessments:
            sample_cells = tuple(
                cell for cell in cells if cell.sample_key == sample.sample_name
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
            witness_id = min(cell.group_hypothesis_id for cell in direct)
            for cell in sample_cells:
                relation = cell.outside_group
                if relation is None:
                    continue
                assert relation.supporting_group_hypothesis_id == witness_id
                assert cell.peak_observation is None and cell.peak_hypothesis is None
                assert cell.physical_peak_key is None
        contents = {
            (
                cell.group_hypothesis_id,
                cell.sample_key,
            ): cell
            for cell in cells
        }
        if original_contents is None:
            original_contents = contents
        else:
            assert contents == original_contents


def _outside_population_handoff(tmp_path: Path):
    fixture, assessments, groups = _outside_group_fixture(tmp_path, (8.0, 12.0))
    cells = compose_cid_nl_predecessor_cells(
        groups=groups,
        sample_assessments=assessments,
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(),
    )
    identities = tuple(groups)
    binding = fixture
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
    identity_type = group_owner.CrossSamplePeakGroupHypothesis

    def counted(*args, **kwargs):
        constructions.append(kwargs["group_hypothesis_id"])
        return identity_type(*args, **kwargs)

    monkeypatch.setattr(
        group_owner,
        "CrossSamplePeakGroupHypothesis",
        counted,
    )
    first = compose_final_group_cell_observation_population(
        handoff=handoff,
    )
    assert constructions == []
    second = compose_final_group_cell_observation_population(
        handoff=handoff,
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
        if group.member_subjects[0].sample_key == initial_sample.sample_name
    )
    assert len(groups) == 2
    recovered_group = min(groups, key=lambda group: group.group_center_rt)
    recovered_peak = cid_nl_peak(fixture, "Third", apex_rt=8.0)
    backfill_assessment = mint_cid_nl_method_assessment(
        sample=recovered_sample,
        acquisition_source=fixture.source(recovered_sample),
        method_subject=fixture.method_subject,
        detector_invocation_id="group-local-recovery",
        evaluation_receipt=_completed_receipt(),
        candidate_peaks=(recovered_peak,),
    )
    assert backfill_assessment.method_result == "HOLD"
    cells = compose_cid_nl_predecessor_cells(
        groups=groups,
        sample_assessments=((initial_sample, initial_assessment),),
        backfill_assessments_by_group={
            (
                recovered_group.group_hypothesis_id,
                recovered_sample.sample_name,
                fixture.method_subject.method_subject_id,
            ): (recovered_sample, backfill_assessment)
        },
        alignment_config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(),
    )
    assert len(cells) == 3
    recovered_cells = tuple(
        cell for cell in cells if cell.sample_key == recovered_sample.sample_name
    )
    assert len(recovered_cells) == 1
    assert recovered_cells[0].group_hypothesis_id == (
        recovered_group.group_hypothesis_id
    )
    assert recovered_cells[0].peak_observation is recovered_peak


def test_backfill_attempt_with_same_values_reuses_the_initial_assessment(
    tmp_path: Path,
) -> None:
    fixture, assessments, groups = _outside_group_fixture(tmp_path, (8.0, 12.0))
    sample, initial = assessments[0]
    relinked = replace(
        initial,
        candidate_peaks=tuple(
            replace(
                peak,
                generation_provenance=replace(
                    peak.generation_provenance, proposal_sources=("backfill_relinked",)
                ),
                capture_stage="backfill",
            )
            for peak in initial.candidate_peaks
        ),
        individual_support_refs=tuple(reversed(initial.individual_support_refs)),
    )
    assert relinked is not initial
    assert relinked == initial and hash(relinked) == hash(initial)
    group = next(
        group
        for group in groups
        if group.member_subjects[0].sample_key == sample.sample_name
    )
    common = {
        "groups": groups,
        "sample_assessments": assessments,
        "alignment_config": CID_NL_ALIGNMENT_CONFIG,
        "rt_frame": SampleRtFrame(),
    }

    without_backfill = compose_cid_nl_predecessor_cells(**common)
    with_backfill = compose_cid_nl_predecessor_cells(
        **common,
        backfill_assessments_by_group={
            (
                group.group_hypothesis_id,
                sample.sample_name,
                fixture.method_subject.method_subject_id,
            ): (sample, relinked)
        },
    )

    assert with_backfill == without_backfill
    assert all(
        cell.method_assessment is initial
        for cell in with_backfill
        if cell.sample_key == sample.sample_name
    )


def test_composer_mints_exact_cartesian_owner_population_and_states(
    tmp_path: Path,
) -> None:
    _, _, handoff = _owner_handoff(tmp_path)

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
    )

    assert len(cells) == len(handoff.final_population) * len(handoff.coverage.samples)
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


@pytest.mark.parametrize("backfill_enabled", (True, False))
def test_composer_distinguishes_completed_negative_search(
    tmp_path: Path,
    backfill_enabled: bool,
) -> None:
    fixture, source_cells, handoff = _owner_handoff(tmp_path)
    sample = fixture.sample("s4")
    negative_assessment = mint_cid_nl_method_assessment(
        sample=sample,
        acquisition_source=fixture.source(sample),
        target=fixture.target,
        detector_invocation_id="gaussian15:s4:completed-negative",
        evaluation_receipt=_completed_receipt(),
    )
    negative_cell = mint_cell_observation(
        sample=sample,
        target=fixture.target,
        group_context=source_cells[0].group_context,
        method_assessment=negative_assessment,
    )
    predecessor_cells = tuple(
        negative_cell if cell.sample_key == sample.sample_name else cell
        for cell in source_cells
    )
    handoff = _owner_handoff_for_cells(
        fixture,
        handoff.coverage.groups[0],
        predecessor_cells,
        backfill_enabled=backfill_enabled,
    )

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
    )

    s4 = next(cell for cell in cells if cell.canonical_sample is sample)
    assert s4.evidence_state == (
        "searched_no_observation" if backfill_enabled else "not_searched"
    )
    if not backfill_enabled:
        assert (
            s4.position_associations[0].position.planning_reason == "backfill_disabled"
        )
        assert (
            all(p.integration is None for p in s4.value_peaks)
            and not s4.direct_hypotheses
        )


def test_composer_keeps_same_sample_sources_with_their_groups(
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
    first_group, first_cells = _group_and_predecessor_cells(
        ((sample, first_assessment),),
        known_label=fixture.target.label,
    )
    second_group, second_cells = _group_and_predecessor_cells(
        ((sample, second_assessment),),
        known_label=fixture.target.label,
    )
    assert first_group != second_group
    samples = (sample,)
    first_cell = first_cells[0]
    second_cell = second_cells[0]
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
    )
    assert len(composed) == 2


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
    left, left_cells = _group_and_predecessor_cells(
        ((samples[0], first_assessment),),
        known_label=fixture.target.label,
    )
    right, right_cells = _group_and_predecessor_cells(
        ((samples[1], second_assessment),),
        known_label=fixture.target.label,
    )
    merged = group_from_members(
        member_subjects=left.member_subjects + right.member_subjects,
    )
    predecessor_cells = (
        compose_cid_nl_predecessor_cells(
            groups=(left, right),
            sample_assessments=(
                (samples[0], first_assessment),
                (samples[1], second_assessment),
            ),
            alignment_config=CID_NL_ALIGNMENT_CONFIG,
            rt_frame=SampleRtFrame.from_samples(samples),
        )
        if outside_support
        else left_cells + right_cells
    )
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
    )
    reverse = compose_final_group_cell_observation_population(
        handoff=replace(
            handoff,
            position_associations=tuple(reversed(handoff.position_associations)),
        ),
    )
    assert forward == reverse
    assert len(forward) == 2
    assert all(
        item.group is merged
        and len(item.position_associations) == 2
        and len(item.predecessor_cells) == (2 if outside_support else 1)
        and sorted(item.evidence_states) == ["direct_observation", "not_searched"]
        for item in forward
    )
    expected_positions_by_sample = {
        item.canonical_sample.sample_name: {
            association.position.key
            for association in handoff.position_associations
            if association.position.sample is item.canonical_sample
        }
        for item in forward
    }
    assert all(
        {association.position.key for association in item.position_associations}
        == expected_positions_by_sample[item.canonical_sample.sample_name]
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
    predecessor, predecessor_cells = _group_and_predecessor_cells(
        (
            (samples[0], first_assessment),
            (samples[1], second_assessment),
        ),
        known_label=fixture.target.label,
    )
    members_by_sample = {
        member.sample_key: member for member in predecessor.member_subjects
    }
    left = group_from_members(
        member_subjects=(members_by_sample[samples[0].sample_name],),
    )
    right = group_from_members(
        member_subjects=(members_by_sample[samples[1].sample_name],),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        samples,
        predecessor_cells=predecessor_cells,
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
    )
    assert len(composed) == 4
    by_key = {(item.group, item.canonical_sample): item for item in composed}
    assert by_key[(left, samples[0])].evidence_state == "direct_observation"
    assert by_key[(right, samples[1])].evidence_state == "direct_observation"
    assert by_key[(left, samples[1])].evidence_state == "not_searched"
    assert by_key[(right, samples[0])].evidence_state == "not_searched"
    assert not by_key[(left, samples[1])].position_associations
    assert not by_key[(right, samples[0])].position_associations
