"""No-RAW ordinary-CI contract tests for the cell-decision owner."""

from __future__ import annotations

import math
import pickle
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from tests.cid_nl_peak_support_no_raw_fixture import PRECURSOR_MZ
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.test_cell_observations import (
    _coverage_for_groups,
    _final_receipt,
    _not_evaluated_assessment,
    _owner_handoff,
    _receipt_fixture,
    _source_cell,
    _support_assessment,
)
from tests.test_cid_nl_evidence_spine import _completed_receipt
from tests.test_target_pre_cell_projection import _finalized_case
from tests.test_untargeted_matrix_projection import (
    _untarget_projection_inputs,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    build_backfill_evaluation_coverage,
    mint_backfill_attempt_terminal,
    mint_backfill_selection_subject_id,
)
from xic_extractor.alignment.cell_decision import owner as decision_owner
from xic_extractor.alignment.cell_decision.owner import (
    CellDecisionContractError,
    NoWriteCellDecision,
    NumericCellDecision,
    ProductionCellDecision,
    decide_group_cell_population,
    decide_production_cell,
    decide_target_cell_population,
    validate_production_cell_decision,
)
from xic_extractor.alignment.cell_observation.observations import (
    CellObservationContractError,
    compose_final_group_cell_observation_population,
    mint_cell_observation_ref,
    validate_final_group_cell_observation_population,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.projection.target.pre_cell import project_target_pre_cell
from xic_extractor.alignment.projection.untarget import (
    project_untarget_matrix,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.extraction.targeted_peak_hypotheses import (
    build_targeted_peak_hypotheses,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.observations import (
    mint_peak_observation_ref,
    physical_peak_observation_integration,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    evaluate_physical_peak,
    prepare_physical_signal,
)
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def test_target_population_emits_one_numeric_or_no_write_per_exact_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.test_cell_observations.cid_nl_peak",
        _numeric_peak,
    )
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=((("sample-a", "sample-b"), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)

    basis_calls: list[tuple[object, object, object]] = []
    original_basis = decision_owner._decision_basis

    def count_basis_calls(
        *, subject: object, evidence: object, payload: object
    ) -> bytes:
        basis_calls.append((subject, evidence, payload))
        return original_basis(subject=subject, evidence=evidence, payload=payload)

    with monkeypatch.context() as counted:
        counted.setattr(decision_owner, "_decision_basis", count_basis_calls)
        decisions = decide_target_cell_population(projection)
    assert len(basis_calls) == len(decisions) == len(projection.subjects)
    reordered = decide_target_cell_population(
        projection,
        subjects=tuple(reversed(projection.subjects)),
    )

    assert len(decisions) == len(projection.subjects) == 2
    assert tuple(item.decision_id for item in reordered) == tuple(
        item.decision_id for item in decisions
    )
    assert {item.variant for item in decisions} == {
        "numeric",
        "searched_no_observation",
    }
    numeric = next(item for item in decisions if item.variant == "numeric")
    assert numeric.value is not None and numeric.value > 0
    assert numeric.source
    assert (
        numeric.selected_integration
        is numeric.subject.terminal_subject.hypothesis.integration
    )
    assert (
        next(
            item for item in decisions if item.variant == "searched_no_observation"
        ).selected_integration
        is None
    )
    assert all(
        item.subject is subject
        for item, subject in zip(decisions, projection.subjects, strict=True)
    )
    assert (
        decide_production_cell(
            projection.subjects[0],
            target_projection=projection,
        )
        == decisions[0]
    )
    for item in decisions:
        validate_production_cell_decision(item)


def test_target_censored_observed_area_survives_public_decision_and_pickle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.test_cell_observations.cid_nl_peak",
        _censored_peak,
    )
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=((("sample-a", "sample-b"), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)
    decision = next(
        item
        for item in decide_target_cell_population(projection)
        if item.variant != "searched_no_observation"
    )
    integration = decision.subject.terminal_subject.hypothesis.integration
    expected_area = integration.area_raw_counts_seconds

    assert expected_area > 0
    assert decision.variant == "numeric"
    assert decision.value == expected_area
    assert decision.source == "trapezoid_counts_seconds_on_observed_censored_signal"
    assert decision.selected_integration is integration
    assert "near_detection_limit" in integration.boundary_sources
    restored = pickle.loads(pickle.dumps(decision))
    validate_production_cell_decision(restored)
    assert restored.value == expected_area
    assert restored.source == decision.source
    assert restored.subject.terminal_subject.hypothesis.integration == integration
    assert (
        restored.selected_integration
        is restored.subject.terminal_subject.hypothesis.integration
    )


@pytest.mark.parametrize("variant", ["missing", "duplicate", "substituted"])
def test_target_population_rejects_incomplete_or_competing_subjects(
    tmp_path: Path,
    variant: str,
) -> None:
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)
    if variant == "missing":
        subjects = projection.subjects[:-1]
    elif variant == "duplicate":
        subjects = projection.subjects + (projection.subjects[0],)
    else:
        subjects = (copy(projection.subjects[0]), projection.subjects[1])

    with pytest.raises(CellDecisionContractError, match="duplicate|missing|extra"):
        decide_target_cell_population(projection, subjects=subjects)


def test_group_population_preserves_complete_member_and_nonmember_positions(
    tmp_path, monkeypatch
):
    _, _, _, alignment = _untarget_projection_inputs(tmp_path, monkeypatch)
    answers = decide_group_cell_population(alignment)
    assert set(answers) == {
        item.cell_observation_id for item in alignment.cell_observations
    }
    assert {payload.variant for payload, _ in answers.values()} == {
        "not_quantifiable",
        "unavailable",
    }
    for observation in alignment.cell_observations:
        payload, integration = answers[observation.cell_observation_id]
        if observation.evidence_state == "direct_observation":
            assert payload.variant == "not_quantifiable"
            assert integration is observation.integrations[0]
        else:
            assert payload.variant == "unavailable"
            assert integration is None
    projection = project_untarget_matrix(alignment, answers)
    member = next(
        d for d in projection.decisions if d.subject.member_subject is not None
    )
    integration = member.selected_integration
    original_area = integration.area_raw_counts_seconds
    object.__setattr__(integration, "area_raw_counts_seconds", original_area + 1.0)
    with pytest.raises(CellDecisionContractError, match="basis|substituted"):
        validate_production_cell_decision(member)
    object.__setattr__(integration, "area_raw_counts_seconds", original_area)


def test_group_numeric_uses_real_primary_matrix_area(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.test_untargeted_cid_nl_golden_path.cid_nl_peak", _numeric_peak
    )
    _, _, _, alignment = _untarget_projection_inputs(tmp_path, monkeypatch)
    answers = decide_group_cell_population(alignment)
    numeric = next(
        payload for payload, _ in answers.values() if payload.variant == "numeric"
    )
    assert numeric.value > 0
    assert numeric.source


def test_group_closed_state_table_maps_owner_observations_without_reason_parsing(
    tmp_path,
):
    _, spine, handoff = _owner_handoff(tmp_path)
    observations = compose_final_group_cell_observation_population(
        handoff=handoff, predecessor_cells=spine.cells
    )
    answers = tuple(
        decision_owner._decide_group(
            observation,
            is_member=any(
                member.canonical_sample_identity_id
                == observation.canonical_sample_identity_id
                for member in observation.group.member_subjects
            ),
        )
        for observation in observations
    )
    assert {payload.variant for payload, _ in answers} == {
        "not_quantifiable",
        "unresolved",
        "rejected_observation",
        "unavailable",
    }
    for observation, (payload, integration) in zip(observations, answers, strict=True):
        if payload.variant == "not_quantifiable":
            assert integration is observation.integrations[0]
        else:
            assert integration is None


def test_group_hold_counterexample_distinguishes_observed_and_zero_candidate(
    tmp_path: Path,
) -> None:
    fixture, spine, handoff = _owner_handoff(tmp_path)
    observed_cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    sample = fixture.sample("s4")
    observed = next(item for item in observed_cells if item.canonical_sample is sample)
    assert (
        decision_owner._decide_group(observed, is_member=False)[0].variant
        == "unresolved"
    )

    negative_assessment = mint_cid_nl_method_assessment_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        detector_invocation_id="gaussian15:s4:cell-decision-negative",
        evaluation_receipt=_completed_receipt(scanned_event_count=0),
    )
    source = next(
        item
        for item in spine.cells
        if item.content.canonical_sample_identity_id
        == sample.identity.sample_identity_id
    )
    negative_cell = mint_cell_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        target=fixture.target,
        group_context=source.group_context,
        method_assessment=negative_assessment,
    )
    predecessor_cells = tuple(
        negative_cell if item is source else item for item in spine.cells
    )
    negative_cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )
    negative = next(item for item in negative_cells if item.canonical_sample is sample)
    assert (
        decision_owner._decide_group(negative, is_member=False)[0].variant
        == "searched_no_observation"
    )


def test_group_reject_precedes_fallback_and_evaluated_coverage_terminals(
    tmp_path: Path,
) -> None:
    _fixture, spine, fallback_handoff = _owner_handoff(tmp_path)
    fallback_cells = compose_final_group_cell_observation_population(
        handoff=fallback_handoff,
        predecessor_cells=spine.cells,
    )
    fallback_reject = next(
        item for item in fallback_cells if item.evidence_state == "rejected_observation"
    )
    assert (
        decision_owner._decide_group(fallback_reject, is_member=False)[0].variant
        == "rejected_observation"
    )

    evaluated_handoff = _with_evaluated_coverage_terminal(
        fallback_handoff,
        sample_stem=fallback_reject.canonical_sample.lookup_token,
    )
    evaluated_cells = compose_final_group_cell_observation_population(
        handoff=evaluated_handoff,
        predecessor_cells=spine.cells,
    )
    evaluated_reject = next(
        item
        for item in evaluated_cells
        if item.canonical_sample is fallback_reject.canonical_sample
    )
    assert (
        decision_owner._decide_group(evaluated_reject, is_member=False)[0].variant
        == "rejected_observation"
    )


def test_group_crossed_terminal_and_candidate_combinations_fail_before_decision(
    tmp_path: Path,
) -> None:
    fixture, spine, handoff = _owner_handoff(tmp_path)
    hold_source = next(
        item
        for item in spine.cells
        if item.content.canonical_sample_identity_id
        == fixture.sample("s4").identity.sample_identity_id
    )
    foreign_observation = next(
        item.peak_observation
        for item in spine.cells
        if item.peak_observation is not None
        and item.content.canonical_sample_identity_id
        != hold_source.content.canonical_sample_identity_id
    )
    with pytest.raises(CellObservationContractError, match="candidate universe"):
        mint_cell_observation_ref(
            processing_owners=fixture.owners,
            sample=fixture.sample("s4"),
            target=fixture.target,
            group_context=hold_source.group_context,
            method_assessment=hold_source.method_assessment,
            peak_observation=foreign_observation,
        )

    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    target = next(item for item in cells if item.coverage_terminals)
    foreign_terminal = next(
        terminal
        for item in cells
        if item.canonical_sample is not target.canonical_sample
        for terminal in item.coverage_terminals
    )
    original_terminals = target.coverage_terminals
    object.__setattr__(target, "coverage_terminals", (foreign_terminal,))
    with pytest.raises(CellObservationContractError, match="position|owner-composed"):
        validate_final_group_cell_observation_population(
            handoff=handoff, cell_observations=cells
        )
    object.__setattr__(target, "coverage_terminals", original_terminals)


def test_group_merge_selects_one_direct_integration_among_terminal_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.test_cell_observations.cid_nl_peak",
        _numeric_peak,
    )
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2"),
    )
    samples = (fixture.sample("s1"), fixture.sample("s2"))
    assessments = tuple(
        _support_assessment(
            fixture,
            sample.lookup_token,
            suffix="cell-decision-mixed-merge",
            apex_rt=8.0 + index * 0.3,
            scan_number=111 + index,
        )
        for index, sample in enumerate(samples)
    )
    left_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((samples[0], assessments[0]),),
    )
    right_spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=((samples[1], assessments[1]),),
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
        planned=True,
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
    not_evaluated = tuple(
        _not_evaluated_assessment(
            fixture,
            sample.lookup_token,
            suffix="cell-decision-mixed-merge-terminal",
        )
        for sample in samples
    )
    predecessor_cells = (
        left_spine.cells[0],
        _source_cell(fixture, samples[1], left_spine, not_evaluated[1]),
        _source_cell(fixture, samples[0], right_spine, not_evaluated[0]),
        right_spine.cells[0],
    )
    observations = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )

    assert all(
        set(item.evidence_states) == {"direct_observation", "unavailable"}
        and item.evidence_state == "mixed"
        and len(item.integrations) == 1
        for item in observations
    )
    decisions = tuple(
        decision_owner._decide_group(item, is_member=True) for item in observations
    )
    for observation, (payload, integration) in zip(
        observations, decisions, strict=True
    ):
        assert payload.variant == "numeric" and payload.value > 0
        assert integration is observation.integrations[0]


def test_group_split_nonmember_maps_exact_not_searched_closure(
    tmp_path: Path,
) -> None:
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=("s1", "s2"),
    )
    samples = (fixture.sample("s1"), fixture.sample("s2"))
    assessments = tuple(
        _support_assessment(
            fixture,
            sample.lookup_token,
            suffix="cell-decision-split",
            apex_rt=8.0,
            scan_number=91 + index,
            seed_count=2,
        )
        for index, sample in enumerate(samples)
    )
    spine = build_cid_nl_reference_spine(
        processing_owners=fixture.owners,
        target=fixture.target,
        sample_assessments=tuple(zip(samples, assessments, strict=True)),
    )
    predecessor = validate_cross_sample_peak_group_identity(
        spine.group,
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
    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    not_searched = next(item for item in cells if item.evidence_state == "not_searched")
    assert (
        decision_owner._decide_group(not_searched, is_member=False)[0].variant
        == "not_searched"
    )


def test_target_population_revalidates_row_terminal_and_integration_axes(
    tmp_path: Path,
) -> None:
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=(((), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)
    observed = next(
        item
        for item in projection.subjects
        if item.terminal_subject.variant == "observed"
    )
    negative = next(
        item
        for item in projection.subjects
        if item.terminal_subject.variant == "searched_no_observation"
    )
    original_terminal_subject = observed.terminal_subject

    original_candidate_id = observed.terminal_subject.candidate_id
    object.__setattr__(
        observed.terminal_subject,
        "candidate_id",
        negative.terminal_subject.candidate_id,
    )
    with pytest.raises(CellDecisionContractError, match="scope binding"):
        decide_target_cell_population(projection)
    object.__setattr__(
        observed.terminal_subject,
        "candidate_id",
        original_candidate_id,
    )

    object.__setattr__(observed, "terminal_subject", negative.terminal_subject)
    with pytest.raises(CellDecisionContractError, match="scope binding"):
        decide_target_cell_population(projection)
    object.__setattr__(observed, "terminal_subject", original_terminal_subject)

    original_variant = projection.row_binding.variant
    wrong_variant = "bound" if original_variant == "unbound" else "unbound"
    object.__setattr__(projection.row_binding, "variant", wrong_variant)
    with pytest.raises(CellDecisionContractError, match="cardinality"):
        decide_target_cell_population(projection)
    object.__setattr__(projection.row_binding, "variant", original_variant)

    integration = cast(
        object,
        observed.terminal_subject.hypothesis.integration,  # type: ignore[union-attr]
    )
    object.__setattr__(integration, "area_raw_counts_seconds", math.nan)
    with pytest.raises(CellDecisionContractError, match="must be finite"):
        decide_target_cell_population(projection)


def test_no_write_payload_rejects_unknown_variant() -> None:
    with pytest.raises(CellDecisionContractError, match="unsupported"):
        NoWriteCellDecision("missing")  # type: ignore[arg-type]


def test_decision_is_immutable_sealed_and_invalidates_payload_substitution(
    tmp_path: Path,
) -> None:
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a",),
        observed_stems=("sample-a",),
        group_specs=((("sample-a",), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)
    decision = decide_target_cell_population(projection)[0]

    with pytest.raises(FrozenInstanceError):
        decision.payload = NoWriteCellDecision("unavailable")  # type: ignore[misc]
    with pytest.raises(CellDecisionContractError, match="must be minted"):
        ProductionCellDecision(
            decision_id=decision.decision_id,
            subject=decision.subject,
            evidence=decision.evidence,
            payload=decision.payload,
            basis_bytes=decision.basis_bytes,
            selected_integration=decision.selected_integration,
            _mint_token=object(),
        )
    assert decision.selected_integration is not None
    changed = copy(decision)
    object.__setattr__(
        changed, "selected_integration", copy(decision.selected_integration)
    )
    with pytest.raises(CellDecisionContractError, match="substituted"):
        validate_production_cell_decision(changed)
    object.__setattr__(
        decision,
        "payload",
        cast(NumericCellDecision, NumericCellDecision(1.0, "forged")),
    )
    with pytest.raises(CellDecisionContractError, match="basis"):
        validate_production_cell_decision(decision)


def _with_evaluated_coverage_terminal(
    handoff: Any,
    *,
    sample_stem: str,
) -> Any:
    original = next(
        item
        for item in handoff.coverage.terminals
        if item.logical_request.item.sample_stem == sample_stem
    )
    selection_subject_id = mint_backfill_selection_subject_id(
        request_identity=original.request_identity,
        group_hypothesis_id=original.logical_request.group_hypothesis_id,
        sample_stem=sample_stem,
        request=original.logical_request.item.request,
        candidate_index=original.candidate_index,
        candidate_phase="primary_query",
    )
    rt = tuple(7.5 + index / 30.0 for index in range(31))
    intensity = tuple(
        500.0 if index == 15 else 50.0 if index in {14, 16} else 0.0
        for index in range(31)
    )
    prepared = prepare_physical_signal(
        rt,
        intensity,
        profile=SignalProcessingProfile(window_points=3),
    )
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=selection_subject_id,
        query_rt_min=8.0,
        request_context_rt_start_min=7.9,
        request_context_rt_end_min=8.1,
    )
    evaluated = mint_backfill_attempt_terminal(
        position_id=original.position_id,
        logical_request=original.logical_request,
        request_identity=original.request_identity,
        candidate_phase="primary_query",
        candidate_index=original.candidate_index,
        trace_evaluated=True,
        selected_for_output=True,
        terminal_reason=None,
        physical_evaluation=evaluation,
    )
    terminals = tuple(
        evaluated if item is original else item for item in handoff.coverage.terminals
    )
    coverage = build_backfill_evaluation_coverage(
        handoff.coverage.epoch,
        terminals,
    )
    return build_cross_sample_association_lineage_handoff(
        coverage=coverage,
        final_receipt=handoff.final_receipt,
    )


def _numeric_peak(
    fixture: Any,
    sample_stem: str,
    *,
    mz: float = PRECURSOR_MZ,
    apex_rt: float = 8.0,
    rt_left_min: float = 7.9,
    rt_right_min: float = 8.1,
    height: float = 500.0,
) -> Any:
    sample = fixture.sample(sample_stem)
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    trace = Trace.from_arrays(
        sample_name=sample_stem,
        mz=mz,
        rt=(
            rt_left_min - 0.1,
            rt_left_min,
            apex_rt,
            rt_right_min,
            rt_right_min + 0.1,
        ),
        intensity=(0.0, height / 10.0, height, height / 10.0, 0.0),
        rt_min=rt_left_min - 0.1,
        rt_max=rt_right_min + 0.1,
        ppm_tol=10.0,
        source="cell_decision_no_raw_fixture",
        acquisition_source_id=source.acquisition_source_id,
    )
    peak = PeakResult(
        rt=apex_rt,
        intensity=height,
        intensity_smoothed=height,
        area=height * 6.6,
        peak_start=rt_left_min,
        peak_end=rt_right_min,
    )
    candidate = PeakCandidate(
        peak=peak,
        selection_apex_rt=apex_rt,
        selection_apex_intensity=height,
        selection_apex_index=2,
        raw_apex_rt=apex_rt,
        raw_apex_intensity=height,
        raw_apex_index=2,
        prominence=height * 0.9,
        proposal_sources=("local_minimum",),
    )
    result = PeakDetectionResult(
        status="OK",
        peak=peak,
        n_points=trace.scan_count,
        max_smoothed=height,
        n_prominent_peaks=1,
        candidates=(candidate,),
    )
    integration = build_targeted_peak_hypotheses(
        sample_name=trace.sample_name,
        target_label="cell-decision-no-raw",
        role="",
        istd_pair="",
        resolver_mode="local_minimum",
        peak_result=result,
        rt=trace.rt,
        intensity=trace.intensity,
        baseline_integration_method="asls",
    )[0].integration
    return mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        trace=trace,
        integration=integration,
        resolver_mode="local_minimum",
        proposal_sources=("local_minimum",),
    )


def _censored_peak(
    fixture: Any,
    sample_stem: str,
    *,
    mz: float = PRECURSOR_MZ,
    apex_rt: float = 8.0,
    **_kwargs: Any,
) -> Any:
    sample = fixture.sample(sample_stem)
    source = resolve_request_acquisition_source(
        processing_owners=fixture.owners,
        sample=sample,
    )
    rt = np.arange(401, dtype=float) / 100.0 + apex_rt - 2.0
    intensity = np.zeros(401)
    positions = np.arange(160, 241)
    positions = positions[(positions % 3 == 0) | (positions == 199)]
    intensity[positions] = 100.0 + 200.0 * np.exp(-(((positions - 200) / 25.0) ** 2))
    profile = SignalProcessingProfile()
    prepared = prepare_physical_signal(rt, intensity, profile=profile)
    assert prepared.prepared is not None
    evaluation = evaluate_physical_peak(
        prepared,
        selection_subject_id=f"censored-cluster-{sample_stem}",
        query_rt_min=apex_rt,
    )
    trace = Trace.from_arrays(
        sample_name=sample_stem,
        mz=mz,
        rt=rt,
        intensity=intensity,
        rt_min=float(rt[0]),
        rt_max=float(rt[-1]),
        ppm_tol=10.0,
        source="cell_decision_no_raw_fixture",
        acquisition_source_id=source.acquisition_source_id,
    )
    return mint_peak_observation_ref(
        processing_owners=fixture.owners,
        sample=sample,
        trace=trace,
        integration=physical_peak_observation_integration(evaluation),
        selection_intensity=prepared.prepared.positive_signal,
        resolver_mode="canonical_physical_signal",
        proposal_sources=("canonical_physical_segment",),
        physical_evaluation=evaluation,
        signal_processing_profile=profile,
    )
