from __future__ import annotations

from copy import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import xic_extractor.alignment.cell_observations as cell_owner
from tests.issue161_no_raw_fixture import (
    issue161_evidence,
    issue161_peak,
    issue161_unique_support,
)
from tests.package2_no_raw_fixture import build_package2_no_raw_fixture
from tests.test_alignment_owner_backfill import _peak_config
from tests.test_alignment_owner_clustering import _owner
from tests.test_cid_nl_evidence_spine import (
    _completed_receipt,
    _truth_table_fixture,
)
from tests.test_cross_sample_association_lineage import _final_receipt, _runtime_feature
from tests.test_owner_backfill_peak_fact_provenance import (
    _CanonicalOwnerBoundSource,
)
from xic_extractor.alignment.backfill import (
    close_observation_repair,
    mint_observation_repair_subject,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    OwnerBackfillEvaluationCoverageContext,
    build_owner_backfill_evaluation_coverage,
    build_owner_backfill_planning_epoch,
    mint_owner_backfill_attempt_terminal,
)
from xic_extractor.alignment.backfill_transaction import OwnerBackfillSampleResult
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    compose_final_group_cell_observation_population,
    mint_cell_observation_ref,
    validate_final_group_cell_observation_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.cross_sample import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    CrossSampleNeighborhoodBudget,
    CrossSampleSuccessorApplication,
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.cross_sample_reconciliation import (
    build_cross_sample_post_mt0_context,
    reconcile_observation_repair,
)
from xic_extractor.alignment.mode_track.contracts import ModeTrackEvidenceProfile
from xic_extractor.alignment.owner_backfill import build_owner_backfill_result
from xic_extractor.alignment.owner_backfill_request_plan import (
    OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
    OwnerBackfillRequestEquivalenceContext,
    build_owner_backfill_request_plan,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.signal_processing_profile import (
    DEFAULT_SIGNAL_PROCESSING_PROFILE,
)


def _owner_handoff(tmp_path: Path):
    fixture, assessments = _truth_table_fixture(tmp_path)
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(
            zip(fixture.samples, assessments, strict=True)
        ),
    )
    assert spine.group is not None
    group = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    feature = _runtime_feature(group)
    sample_order = tuple(sample.lookup_token for sample in fixture.samples)
    context = OwnerBackfillEvaluationCoverageContext(
        processing_owners=fixture.owners,
        input_features=(feature,),
        samples=fixture.samples,
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="full-audit",
        selected_family_ids=frozenset(),
    )
    acquisition_sources = {
        sample.lookup_token: source
        for sample, source in zip(
            fixture.samples,
            fixture.request.acquisition_sources,
            strict=True,
        )
    }
    equivalence = OwnerBackfillRequestEquivalenceContext(
        acquisition_sources=acquisition_sources,
        backend=context.backend,
        window_strategy=context.window_strategy,
        superwindow_span_factor=context.superwindow_span_factor,
        validation_mode=context.validation_mode,
        result_semantics=context.result_semantics,
    )
    request_plan = build_owner_backfill_request_plan(
        (feature,),
        sample_order=context.sample_order,
        raw_sample_stems=context.raw_sample_stems,
        alignment_config=context.alignment_config,
        equivalence_context=equivalence,
    )
    epoch = build_owner_backfill_planning_epoch(
        context,
        selected_features=(feature,),
        request_plan=request_plan,
    )
    terminals = []
    for position in epoch.positions:
        for planned in position.primary_logical_requests:
            terminals.append(
                mint_owner_backfill_attempt_terminal(
                    position_id=position.position_id,
                    logical_request=planned.logical_request,
                    request_identity=planned.request_identity,
                    candidate_phase="primary_fallback",
                    candidate_index=1,
                    trace_evaluated=False,
                    selected_for_output=False,
                    terminal_reason="primary_unassessable",
                    physical_evaluation=None,
                )
            )
    coverage = build_owner_backfill_evaluation_coverage(epoch, tuple(terminals))
    receipt_fixture = SimpleNamespace(
        context=context,
        acquisitions=acquisition_sources,
    )
    receipt = _final_receipt(
        receipt_fixture,
        predecessors=(group,),
        successors=(group,),
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=receipt,
    )
    return fixture, spine, handoff


def _support_assessment(
    fixture: Any,
    sample_stem: str,
    *,
    suffix: str,
    apex_rt: float,
    scan_number: int,
    seed_count: int = 1,
):
    peak = issue161_peak(
        fixture,
        sample_stem,
        apex_rt=apex_rt,
        rt_left_min=apex_rt - 0.1,
        rt_right_min=apex_rt + 0.1,
    )
    evidence_refs = tuple(
        issue161_evidence(
            fixture,
            sample_stem,
            scan_number=scan_number + index,
            scan_rt_min=apex_rt,
        )
        for index in range(seed_count)
    )
    support_refs = tuple(
        issue161_unique_support(
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


def _not_evaluated_assessment(
    fixture: Any,
    sample_stem: str,
    *,
    suffix: str,
):
    receipt = replace(
        _completed_receipt(scanned_event_count=0),
        search_completed=False,
        reason_code="search_not_evaluated",
    )
    return mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=fixture.sample(sample_stem),
        target=fixture.target,
        detector_invocation_id=f"gaussian15:{sample_stem}:{suffix}",
        evaluation_receipt=receipt,
        technical_state="not_evaluated",
    )


def _coverage_for_groups(
    fixture: Any,
    groups: tuple[Any, ...],
    samples: tuple[Any, ...],
    *,
    planned: bool,
):
    features = tuple(_runtime_feature(group) for group in groups)
    sample_order = tuple(sample.lookup_token for sample in samples)
    all_acquisitions = {
        sample.lookup_token: source
        for sample, source in zip(
            fixture.samples,
            fixture.request.acquisition_sources,
            strict=True,
        )
    }
    acquisition_sources = {
        stem: all_acquisitions[stem] for stem in sample_order
    }
    context = OwnerBackfillEvaluationCoverageContext(
        processing_owners=fixture.owners,
        input_features=features,
        samples=samples,
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=AlignmentConfig(owner_backfill_min_detected_samples=1),
        backend="raw",
        window_strategy="exact",
        superwindow_span_factor=2,
        validation_mode="primary",
        result_semantics=OWNER_BACKFILL_TRACE_RESULT_SEMANTICS,
        scope="full-audit" if planned else "selected-families",
        selected_family_ids=frozenset() if planned else frozenset({"not-this-family"}),
    )
    equivalence = OwnerBackfillRequestEquivalenceContext(
        acquisition_sources=acquisition_sources,
        backend=context.backend,
        window_strategy=context.window_strategy,
        superwindow_span_factor=context.superwindow_span_factor,
        validation_mode=context.validation_mode,
        result_semantics=context.result_semantics,
    )
    request_plan = build_owner_backfill_request_plan(
        features if planned else (),
        sample_order=sample_order,
        raw_sample_stems=frozenset(sample_order),
        alignment_config=context.alignment_config,
        equivalence_context=equivalence,
    )
    epoch = build_owner_backfill_planning_epoch(
        context,
        selected_features=features if planned else (),
        request_plan=request_plan,
    )
    terminals = tuple(
        mint_owner_backfill_attempt_terminal(
            position_id=position.position_id,
            logical_request=planned_request.logical_request,
            request_identity=planned_request.request_identity,
            candidate_phase="primary_fallback",
            candidate_index=1,
            trace_evaluated=False,
            selected_for_output=False,
            terminal_reason="primary_unassessable",
            physical_evaluation=None,
        )
        for position in epoch.positions
        for planned_request in position.primary_logical_requests
    )
    return build_owner_backfill_evaluation_coverage(epoch, terminals)


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


def _source_cell(
    fixture: Any,
    sample: Any,
    group_spine: Any,
    assessment: Any,
):
    return mint_cell_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        group_context=group_spine.cells[0].group_context,
        method_assessment=assessment,
    )


def _repaired_handoff(tmp_path: Path, *, sample_stem: str = "s1"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture = build_package2_no_raw_fixture(
        tmp_path,
        sample_stems=(sample_stem,),
    )
    sample = fixture.sample(sample_stem)
    assessment = _support_assessment(
        fixture,
        sample_stem,
        suffix="repair",
        apex_rt=8.0,
        scan_number=71,
        seed_count=2,
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((sample, assessment),),
    )
    group = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    feature = _runtime_feature(group)
    acquisition = fixture.request.acquisition_sources[0]
    source = _CanonicalOwnerBoundSource(acquisition, empty=False)
    built = build_owner_backfill_result(
        (feature,),
        sample_order=(sample_stem,),
        raw_sources={sample_stem: source},
        expected_acquisition_sources={sample_stem: acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=3),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    assert len(built.peak_fact_observations) == 1
    observation = built.peak_fact_observations[0]
    application = CrossSampleSuccessorApplication(
        status="assessable",
        predecessors=(group,),
        successors=(group,),
        lineage=(),
        reason="unchanged_membership_no_lineage",
    )
    subject = mint_observation_repair_subject(
        request_identity=observation.request_identity,
        logical_request=observation.logical_request,
        predecessor_group=group,
        member_subject=group.member_subjects[0],
        sample_identity=sample,
        successor_application=application,
    )
    result = OwnerBackfillSampleResult(
        sample_index=0,
        sample_stem=sample_stem,
        cells=built.cells,
        peak_fact_observations=(observation,),
    )
    repair = close_observation_repair(subject, result)
    assert repair.outcome == "repaired"
    owner = replace(
        _owner(sample_stem, group.group_hypothesis_id[-8:]),
        cross_sample_member_subject=group.member_subjects[0],
    )
    context = build_cross_sample_post_mt0_context(
        processing_request_id=group.processing_request_id,
        processing_owners=fixture.owners,
        population=(group,),
        application=application,
        owners=(owner,),
        classified_edges=(),
        anchors=(),
        injection_order={sample_stem: 1},
        evidence_profile=ModeTrackEvidenceProfile(
            "anchor-trajectory-v1",
            "fixed-band-v1",
            0.1,
            10,
            10,
        ),
        budget=CrossSampleNeighborhoodBudget(86, 3655),
    )
    final_receipt = reconcile_observation_repair(
        context=context,
        repair_receipt=repair,
    )
    coverage = _coverage_for_groups(
        fixture,
        (group,),
        (sample,),
        planned=True,
    )
    handoff = build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=final_receipt,
    )
    source_cell = _source_cell(
        fixture,
        sample,
        spine,
        _not_evaluated_assessment(
            fixture,
            sample_stem,
            suffix="repair-source",
        ),
    )
    return fixture, spine, handoff, source_cell, repair, observation


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
    assert {
        cell.evidence_state for cell in cells
    } == {
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
        assert all(
            any(terminal is item for item in handoff.coverage.terminals)
            for terminal in cell.coverage_terminals
        )

    validated = validate_final_group_cell_observation_population(
        handoff=handoff,
        cell_observations=tuple(reversed(cells)),
    )
    assert validated == cells


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

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )

    s4 = next(cell for cell in cells if cell.canonical_sample is sample)
    assert s4.evidence_state == "searched_no_observation"


def test_composer_rejects_same_sample_crossed_source_to_position(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(tmp_path, sample_stems=("s1",))
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
    coverage = _coverage_for_groups(
        fixture,
        (first_group, second_group),
        samples,
        planned=False,
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
    not_evaluated = _not_evaluated_assessment(
        fixture,
        "s1",
        suffix="a3",
    )
    first_cell = _source_cell(fixture, sample, first_spine, not_evaluated)
    second_cell = _source_cell(fixture, sample, second_spine, not_evaluated)
    composed = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=(first_cell, second_cell),
    )
    assert len(composed) == 2

    crossed = copy(first_cell)
    object.__setattr__(crossed, "group_context", second_cell.group_context)
    with pytest.raises(CellObservationContractError, match="group identity"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(crossed, second_cell),
        )


def test_composer_merge_retains_both_predecessor_lanes_deterministically(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
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
    coverage = _coverage_for_groups(
        fixture,
        (left, right),
        samples,
        planned=False,
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
    not_evaluated = {
        sample.lookup_token: _not_evaluated_assessment(
            fixture,
            sample.lookup_token,
            suffix="a5",
        )
        for sample in samples
    }
    predecessor_cells = tuple(
        _source_cell(fixture, sample, group_spine, not_evaluated[sample.lookup_token])
        for group_spine in (left_spine, right_spine)
        for sample in samples
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
        and len(item.predecessor_cells) == 2
        and item.evidence_states == ("not_searched", "not_searched")
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
        {
            association.position.position_id
            for association in item.position_associations
        }
        == expected_positions_by_sample[
            item.canonical_sample.identity.sample_identity_id
        ]
        for item in forward
    )


def test_composer_split_keeps_cartesian_not_searched_remainder(
    tmp_path: Path,
) -> None:
    fixture = build_package2_no_raw_fixture(
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
    left = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=(predecessor.member_subjects[0],),
    )
    right = mint_cross_sample_group_identity(
        processing_request_id=predecessor.processing_request_id,
        member_subjects=(predecessor.member_subjects[1],),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        samples,
        planned=True,
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
    by_key = {
        (item.group, item.canonical_sample): item for item in composed
    }
    assert by_key[(left, samples[0])].evidence_state == "direct_observation"
    assert by_key[(right, samples[1])].evidence_state == "direct_observation"
    assert by_key[(left, samples[1])].evidence_state == "not_searched"
    assert by_key[(right, samples[0])].evidence_state == "not_searched"
    assert not by_key[(left, samples[1])].position_associations
    assert not by_key[(right, samples[0])].position_associations


def test_composer_retains_exact_repaired_receipt_observation_and_integration(
    tmp_path: Path,
) -> None:
    _fixture, _spine, handoff, source_cell, repair, observation = (
        _repaired_handoff(tmp_path)
    )

    composed = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=(source_cell,),
        repair_receipts=(repair,),
        repaired_observations=(observation,),
    )

    assert len(composed) == 1
    cell = composed[0]
    assert cell.evidence_state == "repaired_observation"
    assert cell.repair_receipts == (repair,)
    assert cell.repaired_observations == (observation,)
    assert cell.predecessor_cells == (source_cell,)
    assert cell.integrations == (observation.facts.integration,)  # type: ignore[union-attr]


def test_composer_rejects_repair_not_authorized_by_final_receipt(
    tmp_path: Path,
) -> None:
    fixture, spine, handoff = _owner_handoff(tmp_path)
    group = validate_cross_sample_peak_group_identity(
        spine.group,
        require_canonical=True,
    )
    sample = fixture.sample("s1")
    acquisition = fixture.request.acquisition_sources[0]
    built = build_owner_backfill_result(
        (_runtime_feature(group),),
        sample_order=("s1",),
        raw_sources={
            "s1": _CanonicalOwnerBoundSource(acquisition, empty=False),
        },
        expected_acquisition_sources={"s1": acquisition},
        alignment_config=AlignmentConfig(max_rt_sec=60.0),
        peak_config=replace(_peak_config(), resolver_min_scans=3),
        signal_processing_profile=DEFAULT_SIGNAL_PROCESSING_PROFILE,
    )
    observation = built.peak_fact_observations[0]
    subject = mint_observation_repair_subject(
        request_identity=observation.request_identity,
        logical_request=observation.logical_request,
        predecessor_group=group,
        member_subject=next(
            member
            for member in group.member_subjects
            if member.canonical_sample_identity_id
            == sample.identity.sample_identity_id
        ),
        sample_identity=sample,
        successor_application=handoff.final_receipt.post_mt0_application,
    )
    stale_repair = close_observation_repair(
        subject,
        OwnerBackfillSampleResult(
            sample_index=0,
            sample_stem="s1",
            cells=built.cells,
            peak_fact_observations=(observation,),
        ),
    )
    replacement = _source_cell(
        fixture,
        sample,
        spine,
        _not_evaluated_assessment(
            fixture,
            "s1",
            suffix="stale-repair-trigger",
        ),
    )
    predecessors = tuple(
        replacement
        if cell.content.canonical_sample_identity_id
        == sample.identity.sample_identity_id
        else cell
        for cell in spine.cells
    )

    assert handoff.final_receipt.repair_receipt.outcome == "no_repair"
    assert stale_repair.outcome == "repaired"
    with pytest.raises(CellObservationContractError, match="exact final repair"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=predecessors,
            repair_receipts=(stale_repair,),
            repaired_observations=(observation,),
        )


def test_composer_rejects_crossed_repaired_member_hypothesis_and_integration(
    tmp_path: Path,
) -> None:
    _, _, handoff, source_cell, repair, observation = _repaired_handoff(
        tmp_path / "base"
    )
    _, _, crossed_handoff, _, crossed_repair, crossed_observation = (
        _repaired_handoff(tmp_path / "crossed", sample_stem="s2")
    )

    with pytest.raises(
        CellObservationContractError,
        match="crossed its processing request|exact final repair",
    ):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(source_cell,),
            repair_receipts=(crossed_repair,),
            repaired_observations=(crossed_observation,),
        )
    assert crossed_handoff.final_receipt.repair_receipt is crossed_repair

    crossed_hypothesis = copy(observation)
    object.__setattr__(
        crossed_hypothesis,
        "logical_request",
        crossed_observation.logical_request,
    )
    with pytest.raises(
        CellObservationContractError,
        match="not revalidatable|bind exactly one",
    ):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(source_cell,),
            repair_receipts=(repair,),
            repaired_observations=(crossed_hypothesis,),
        )

    facts = observation.facts
    assert facts is not None
    crossed_integration = copy(observation)
    crossed_facts = copy(facts)
    object.__setattr__(crossed_facts, "integration", object())
    object.__setattr__(crossed_integration, "facts", crossed_facts)
    with pytest.raises(CellObservationContractError, match="revalidatable"):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(source_cell,),
            repair_receipts=(repair,),
            repaired_observations=(crossed_integration,),
        )


def test_repaired_final_receipt_cannot_be_omitted_from_applicable_closure() -> None:
    sample: Any = SimpleNamespace(
        identity=SimpleNamespace(sample_identity_id="sample")
    )
    group: Any = object()
    receipt: Any = SimpleNamespace(
        outcome="repaired",
        receipt_id="receipt",
        subject=SimpleNamespace(sample_identity=sample, predecessor_group=group),
    )
    handoff: Any = SimpleNamespace(
        final_receipt=SimpleNamespace(repair_receipt=receipt),
    )
    position: Any = SimpleNamespace(
        position_id="position",
        sample=sample,
        group_identity=group,
    )

    with pytest.raises(CellObservationContractError, match="exact final repair"):
        cell_owner._validate_handoff_repair_closure(
            handoff=handoff,
            positions=(position,),
            repair_receipts=(),
            repaired_observations=(),
            receipt_by_position={},
            observation_by_receipt={},
        )


def test_repaired_final_receipt_rejects_equal_looking_substitution() -> None:
    sample: Any = SimpleNamespace(
        identity=SimpleNamespace(sample_identity_id="sample")
    )
    group: Any = object()
    receipt: Any = SimpleNamespace(
        outcome="repaired",
        receipt_id="receipt",
        subject=SimpleNamespace(sample_identity=sample, predecessor_group=group),
    )
    substitute: Any = SimpleNamespace(
        outcome="repaired",
        receipt_id="receipt",
        subject=SimpleNamespace(sample_identity=sample, predecessor_group=group),
    )
    live_observation: Any = object()
    handoff: Any = SimpleNamespace(
        final_receipt=SimpleNamespace(repair_receipt=receipt),
    )
    position: Any = SimpleNamespace(
        position_id="position",
        sample=sample,
        group_identity=group,
    )

    with pytest.raises(CellObservationContractError, match="exact final repair"):
        cell_owner._validate_handoff_repair_closure(
            handoff=handoff,
            positions=(position,),
            repair_receipts=(substitute,),
            repaired_observations=(live_observation,),
            receipt_by_position={"position": (substitute,)},
            observation_by_receipt={"receipt": (live_observation,)},
        )


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
