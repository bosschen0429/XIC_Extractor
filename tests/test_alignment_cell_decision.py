"""No-RAW ordinary-CI contract tests for the cell-decision owner."""

from __future__ import annotations

import math
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

import xic_extractor.alignment.projection.untarget.pre_cell as untarget_owner
from tests.issue161_no_raw_fixture import PRECURSOR_MZ
from tests.test_cell_observations import (
    _owner_handoff,
    _repaired_handoff,
)
from tests.test_target_pre_cell_projection import _finalized_case
from tests.test_untargeted_pre_cell_projection import (
    _untarget_projection_inputs,
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
from xic_extractor.alignment.cell_observations import (
    CellObservationContractError,
    FinalGroupCellObservation,
    compose_final_group_cell_observation_population,
)
from xic_extractor.alignment.projection.target import project_target_pre_cell
from xic_extractor.alignment.projection.untarget import (
    GroupCellSubject,
    project_untarget_pre_cell,
)
from xic_extractor.extraction.peak_hypothesis_adapter import (
    build_targeted_peak_hypotheses,
)
from xic_extractor.peak_detection.models import (
    PeakCandidate,
    PeakDetectionResult,
    PeakResult,
)
from xic_extractor.peak_detection.observations import mint_peak_observation_ref
from xic_extractor.peak_detection.traces import Trace
from xic_extractor.processing_identity import resolve_request_acquisition_source


def test_target_population_emits_one_numeric_or_no_write_per_exact_subject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.test_cell_observations.issue161_peak",
        _numeric_peak,
    )
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=("sample-a",),
        group_specs=((("sample-a", "sample-b"), "provisional"),),
    )
    projection = project_target_pre_cell(alignment)

    decisions = decide_target_cell_population(projection)
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
    assert decide_production_cell(
        projection.subjects[0],
        target_projection=projection,
    ) == decisions[0]
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
    assert decide_production_cell(
        projection.cell_subjects[0],
        group_handoff=alignment.cell_observation_handoff,
        group_observations=alignment.cell_observations,
    ) == decisions[0]
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
        "tests.test_untargeted_cid_nl_golden_path.issue161_peak",
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
    object.__setattr__(observed, "terminal_subject", alignment.terminal_subjects[0])

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


def test_repaired_observation_is_numeric_only_for_its_exact_member(
    tmp_path: Path,
) -> None:
    _fixture, _spine, handoff, source_cell, repair, observation = _repaired_handoff(
        tmp_path
    )
    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=(source_cell,),
        repair_receipts=(repair,),
        repaired_observations=(observation,),
    )
    cell = cells[0]
    member = next(
        item
        for item in cell.group.member_subjects
        if item.canonical_sample_identity_id == cell.canonical_sample_identity_id
    )
    row = untarget_owner._mint_untarget_row_key(cell.group)
    exact_subject = GroupCellSubject(row, cell.canonical_sample, member)
    nonmember_subject = GroupCellSubject(row, cell.canonical_sample, None)

    decision = decide_production_cell(
        exact_subject,
        group_handoff=handoff,
        group_observations=cells,
    )

    assert decision.variant == "numeric"
    with pytest.raises(CellDecisionContractError, match="nonmember"):
        decide_production_cell(
            nonmember_subject,
            group_handoff=handoff,
            group_observations=cells,
        )


def test_direct_and_repaired_evidence_is_rejected_before_decision(
    tmp_path: Path,
) -> None:
    _fixture, spine, handoff, _source_cell, repair, observation = _repaired_handoff(
        tmp_path
    )

    with pytest.raises(
        CellObservationContractError,
        match="direct and repaired observation",
    ):
        compose_final_group_cell_observation_population(
            handoff=handoff,
            predecessor_cells=(spine.cells[0],),
            repair_receipts=(repair,),
            repaired_observations=(observation,),
        )

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
