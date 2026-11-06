"""No-RAW ordinary-CI contract tests for the cell-decision owner."""

from __future__ import annotations

import pickle
from copy import copy
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.alignment_cell_decision_fixtures import (
    _numeric_peak,
)
from tests.cell_observations_fixtures import (
    _coverage_for_groups,
    _final_receipt,
    _owner_handoff,
    _owner_handoff_for_cells,
    _receipt_fixture,
    _support_assessment,
)
from tests.cid_nl_evidence_spine_fixtures import _completed_receipt
from tests.cid_nl_peak_support_no_raw_fixture import PRECURSOR_MZ
from tests.processing_identity_no_raw_fixture import (
    build_processing_identity_no_raw_fixture,
)
from tests.target_pre_cell_projection_fixtures import _finalized_case
from tests.untargeted_matrix_projection_fixtures import (
    _untarget_projection_inputs,
)
from xic_extractor.alignment.cell_decision import owner as decision_owner
from xic_extractor.alignment.cell_decision.owner import (
    CellDecisionContractError,
    decide_group_cell_population,
    decide_target_cell_population,
)
from xic_extractor.alignment.cell_observation.observations import (
    compose_final_group_cell_observation_population,
    mint_cell_observation_ref,
)
from xic_extractor.alignment.cid_nl_evidence_spine import (
    build_cid_nl_reference_spine,
)
from xic_extractor.alignment.cross_sample.association import (
    build_cross_sample_association_lineage_handoff,
)
from xic_extractor.alignment.cross_sample.peak_groups import (
    mint_cross_sample_group_identity,
)
from xic_extractor.alignment.projection.target.pre_cell import project_target_pre_cell
from xic_extractor.alignment.projection.untarget import (
    project_untarget_matrix,
)
from xic_extractor.configuration.signal_processing_profile import (
    SignalProcessingProfile,
)
from xic_extractor.peak_detection.cid_nl_method_assessment import (
    mint_cid_nl_method_assessment_ref,
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
        "tests.cell_observations_fixtures.cid_nl_peak",
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
    assert reordered == decisions
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


def test_target_fragmented_low_signal_keeps_evidence_without_numeric_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.cell_observations_fixtures.cid_nl_peak",
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
    assert integration.signal_quality.low_signal_to_noise
    assert decision.variant == "not_quantifiable"
    assert decision.value is None
    assert decision.source == ""
    assert decision.selected_integration is integration
    assert "near_detection_limit" in integration.boundary_sources
    restored = pickle.loads(pickle.dumps(decision))
    assert restored.value is None
    assert restored.source == decision.source
    assert restored.subject.terminal_subject.hypothesis.integration == integration
    assert (
        restored.selected_integration
        is restored.subject.terminal_subject.hypothesis.integration
    )


@pytest.mark.parametrize(
    "low_snr,acquired,blocked",
    [
        (True, (1, 0, 0, 1, 0, 0, 1, 0, 0), True),
        (False, (1, 0, 0, 1, 0, 0, 1, 0, 0), False),
        (True, (0, 0, 1, 1, 1, 0, 0, 0, 0), False),
        (True, (1, 1, 0, 1, 1, 0, 1, 1, 0), False),
    ],
)
def test_target_quantification_requires_joint_signal_and_support_failure(
    low_snr, acquired, blocked
):
    from xic_extractor.peak_detection.integration_result import IntegrationResult
    from xic_extractor.peak_detection.signal_quality import SignalQuality

    # A support-policy truth table, not a generated chromatographic oracle.
    # Sparse scans alone and low S/N alone must not suppress a value.
    integration = IntegrationResult(0, 4, 8, 4, 8, 1, 1, 120)
    quality = SignalQuality("available", 1, 2 if low_snr else 20, low_snr)
    integration = replace(integration, signal_quality=quality)
    assert (
        decision_owner._target_has_fragmented_low_signal(integration, acquired)
        is blocked
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
    assert set(answers) == {item.key for item in alignment.cell_observations}
    assert {payload.variant for payload, _ in answers.values()} == {
        "not_quantifiable",
        "not_searched",
    }
    for observation in alignment.cell_observations:
        payload, integration = answers[observation.key]
        if observation.evidence_state == "direct_observation":
            assert payload.variant == "not_quantifiable"
            assert integration is observation.integrations[0]
        else:
            assert payload.variant == "not_searched"
            assert integration is None
    projection = project_untarget_matrix(alignment, answers)
    member = next(
        d for d in projection.decisions if d.subject.member_subject is not None
    )
    integration = member.selected_integration
    original_area = integration.area_raw_counts_seconds
    object.__setattr__(integration, "area_raw_counts_seconds", original_area + 1.0)
    object.__setattr__(integration, "area_raw_counts_seconds", original_area)


def test_group_numeric_uses_real_primary_matrix_area(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "tests.untargeted_matrix_projection_fixtures.cid_nl_peak", _numeric_peak
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
                member.sample_key == observation.sample_key
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
        evaluation_receipt=_completed_receipt(),
    )
    source = next(
        item
        for item in spine.cells
        if item.content.sample_key == sample.identity.sample_key
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
    handoff = _owner_handoff_for_cells(
        fixture,
        handoff.coverage.group_identities[0],
        predecessor_cells,
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


def test_group_reject_decides_rejected_observation(
    tmp_path: Path,
) -> None:
    _fixture, spine, handoff = _owner_handoff(tmp_path)
    cells = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=spine.cells,
    )
    reject = next(
        item for item in cells if item.evidence_state == "rejected_observation"
    )
    assert (
        decision_owner._decide_group(reject, is_member=False)[0].variant
        == "rejected_observation"
    )


def test_group_merge_selects_one_direct_integration_among_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tests.cell_observations_fixtures.cid_nl_peak",
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
    left = left_spine.group.membership
    right = right_spine.group.membership
    merged = mint_cross_sample_group_identity(
        member_subjects=left.member_subjects + right.member_subjects,
    )
    # CID-NL composition gives each spine a cell only for its searched sample.
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
    observations = compose_final_group_cell_observation_population(
        handoff=handoff,
        predecessor_cells=predecessor_cells,
    )

    assert all(
        set(item.evidence_states) == {"direct_observation", "not_searched"}
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
    predecessor = spine.group.membership
    left = mint_cross_sample_group_identity(
        member_subjects=(predecessor.member_subjects[0],),
    )
    right = mint_cross_sample_group_identity(
        member_subjects=(predecessor.member_subjects[1],),
    )
    coverage = _coverage_for_groups(
        fixture,
        (predecessor,),
        samples,
        predecessor_cells=spine.cells,
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
