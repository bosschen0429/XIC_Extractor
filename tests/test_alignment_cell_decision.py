"""No-RAW ordinary-CI contract tests for the cell-decision owner."""

from __future__ import annotations

import math
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

import xic_extractor.alignment.projection.untarget.pre_cell as untarget_owner
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
from tests.test_untargeted_pre_cell_projection import (
    _untarget_projection_inputs,
)
from xic_extractor.alignment.backfill.evaluation_coverage import (
    build_backfill_evaluation_coverage,
    mint_backfill_attempt_terminal,
    mint_backfill_selection_subject_id,
)
from xic_extractor.alignment.cell_decision import (
    CellDecisionContractError,
    NoWriteCellDecision,
    NumericCellDecision,
    ProductionCellDecision,
    decide_group_cell_population,
    decide_production_cell,
    decide_target_cell_population,
    validate_production_cell_decision,
)
from xic_extractor.alignment.cell_decision import owner as decision_owner
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    FinalGroupCellObservation,
    compose_final_group_cell_observation_population,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample_peak_groups import (
    mint_cross_sample_group_identity,
    validate_cross_sample_peak_group_identity,
)
from xic_extractor.alignment.projection.target import project_target_pre_cell
from xic_extractor.alignment.projection.untarget import (
    GroupCellSubject,
    project_untarget_pre_cell,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.extraction.peak_hypothesis_adapter import (
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
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
    )
    projection = project_untarget_pre_cell(alignment)

    decisions = decide_group_cell_population(projection, alignment)
    reordered = decide_group_cell_population(
        projection,
        alignment,
        subjects=tuple(reversed(projection.cell_subjects)),
        observations=tuple(reversed(alignment.cell_observations)),
    )

    assert len(decisions) == len(projection.cell_subjects)
    assert tuple(item.decision_id for item in reordered) == tuple(
        item.decision_id for item in decisions
    )
    assert {item.variant for item in decisions} == {
        "not_quantifiable",
        "unavailable",
    }
    by_member = {
        cast(GroupCellSubject, item.subject).member_subject is not None: item
        for item in decisions
    }
    assert by_member[True].variant == "not_quantifiable"
    assert by_member[False].variant == "unavailable"
    assert (
        decide_production_cell(
            projection.cell_subjects[0],
            group_handoff=alignment.cell_observation_handoff,
            group_observations=alignment.cell_observations,
        )
        == decisions[0]
    )
    evidence = cast(FinalGroupCellObservation, by_member[True].evidence)
    integration = evidence.integrations[0]
    original_area = integration.area_raw_counts_seconds
    object.__setattr__(integration, "area_raw_counts_seconds", original_area + 1.0)
    with pytest.raises(CellDecisionContractError, match="basis|substituted"):
        validate_production_cell_decision(by_member[True])
    object.__setattr__(integration, "area_raw_counts_seconds", original_area)


def test_group_numeric_uses_real_primary_matrix_area(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.test_untargeted_cid_nl_golden_path.cid_nl_peak",
        _numeric_peak,
    )
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
    )
    projection = project_untarget_pre_cell(alignment)

    decisions = decide_group_cell_population(projection, alignment)

    numeric = next(
        item
        for item in decisions
        if cast(GroupCellSubject, item.subject).member_subject
    )
    assert numeric.variant == "numeric"
    assert numeric.value is not None and numeric.value > 0
    assert numeric.source


@pytest.mark.parametrize("input_name", ["subjects", "observations"])
@pytest.mark.parametrize("variant", ["missing", "duplicate", "substituted"])
def test_group_population_rejects_missing_duplicate_or_substituted_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_name: str,
    variant: str,
) -> None:
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
    )
    projection = project_untarget_pre_cell(alignment)
    source = (
        projection.cell_subjects
        if input_name == "subjects"
        else alignment.cell_observations
    )
    if variant == "missing":
        changed = source[:-1]
    elif variant == "duplicate":
        changed = source + (source[0],)
    else:
        changed = (copy(source[0]),) + source[1:]
    with pytest.raises(CellDecisionContractError, match="duplicate|missing|extra"):
        if input_name == "subjects":
            decide_group_cell_population(
                projection,
                alignment,
                subjects=cast(Any, changed),
            )
        else:
            decide_group_cell_population(
                projection,
                alignment,
                observations=cast(Any, changed),
            )


def test_group_closed_state_table_maps_owner_observations_without_reason_parsing(
    tmp_path: Path,
) -> None:
    _fixture, _spine, handoff = _owner_handoff(tmp_path)
    observations = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=_spine.cells,
    )
    decisions = []
    for observation in observations:
        row = untarget_owner._mint_untarget_row_key(observation.group)
        member = next(
            (
                item
                for item in observation.group.member_subjects
                if item.canonical_sample_identity_id
                == observation.canonical_sample_identity_id
            ),
            None,
        )
        subject = GroupCellSubject(
            row_key=row,
            canonical_sample=observation.canonical_sample,
            member_subject=member,
        )
        decisions.append(
            decide_production_cell(
                subject,
                group_handoff=handoff,
                group_observations=observations,
            )
        )

    assert {item.variant for item in decisions} == {
        "not_quantifiable",
        "unresolved",
        "rejected_observation",
        "unavailable",
    }

    direct = next(
        item for item in observations if item.evidence_state == "direct_observation"
    )
    nonmember = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(direct.group),
        direct.canonical_sample,
        None,
    )
    with pytest.raises(CellDecisionContractError, match="nonmember"):
        decide_production_cell(
            nonmember,
            group_handoff=handoff,
            group_observations=observations,
        )


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
    observed_subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(observed.group),
        sample,
        None,
    )
    assert (
        decide_production_cell(
            observed_subject,
            group_handoff=handoff,
            group_observations=observed_cells,
        ).variant
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
    negative_subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(negative.group),
        sample,
        None,
    )
    assert (
        decide_production_cell(
            negative_subject,
            group_handoff=handoff,
            group_observations=negative_cells,
        ).variant
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
    fallback_subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(fallback_reject.group),
        fallback_reject.canonical_sample,
        None,
    )
    assert (
        decide_production_cell(
            fallback_subject,
            group_handoff=fallback_handoff,
            group_observations=fallback_cells,
        ).variant
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
    evaluated_subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(evaluated_reject.group),
        evaluated_reject.canonical_sample,
        None,
    )
    assert (
        decide_production_cell(
            evaluated_subject,
            group_handoff=evaluated_handoff,
            group_observations=evaluated_cells,
        ).variant
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
    member = next(
        (
            item
            for item in target.group.member_subjects
            if item.canonical_sample_identity_id == target.canonical_sample_identity_id
        ),
        None,
    )
    subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(target.group),
        target.canonical_sample,
        member,
    )
    with pytest.raises(CellDecisionContractError, match="position|owner-composed"):
        decide_production_cell(
            subject,
            group_handoff=handoff,
            group_observations=cells,
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
        decide_production_cell(
            GroupCellSubject(
                untarget_owner._mint_untarget_row_key(item.group),
                item.canonical_sample,
                next(
                    member
                    for member in item.group.member_subjects
                    if member.canonical_sample_identity_id
                    == item.canonical_sample_identity_id
                ),
            ),
            group_handoff=handoff,
            group_observations=observations,
        )
        for item in observations
    )

    assert all(
        item.variant == "numeric" and item.value is not None and item.value > 0
        for item in decisions
    )


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
    subject = GroupCellSubject(
        untarget_owner._mint_untarget_row_key(not_searched.group),
        not_searched.canonical_sample,
        None,
    )

    assert (
        decide_production_cell(
            subject,
            group_handoff=handoff,
            group_observations=cells,
        ).variant
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


def test_group_population_rejects_crossed_member_and_partial_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
    )
    projection = project_untarget_pre_cell(alignment)
    member_subject = next(
        item for item in projection.cell_subjects if item.member_subject is not None
    )
    nonmember_subject = next(
        item for item in projection.cell_subjects if item.member_subject is None
    )

    object.__setattr__(
        nonmember_subject,
        "member_subject",
        member_subject.member_subject,
    )
    with pytest.raises(CellDecisionContractError, match="projection"):
        decide_group_cell_population(projection, alignment)
    object.__setattr__(nonmember_subject, "member_subject", None)

    object.__setattr__(alignment.cell_observations[0], "evidence_states", ())
    with pytest.raises(CellDecisionContractError, match="exact owner-composed"):
        decide_group_cell_population(projection, alignment)


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
            _mint_token=object(),
        )
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
