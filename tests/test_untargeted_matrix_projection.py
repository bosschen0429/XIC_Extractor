"""Completed Feature x Sample projection, output identity and route contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import xic_extractor.alignment.result.scientific_alignment as finalizer_owner
from tests.untargeted_matrix_projection_fixtures import (
    _completed_projection,
    _untarget_projection_inputs,
)
from xic_extractor.alignment.cell_decision.owner import (
    decide_group_cell_population,
)
from xic_extractor.alignment.projection.untarget import (
    UntargetProjectionContractError,
    project_untarget_matrix,
)
from xic_extractor.alignment.result import (
    FinalizedScientificAlignment,
)


def _predecessor_cells(base: FinalizedScientificAlignment):
    predecessor_cells = []
    seen = set()
    for cell in base.cell_observations:
        for predecessor_cell in cell.predecessor_cells:
            if id(predecessor_cell) not in seen:
                seen.add(id(predecessor_cell))
                predecessor_cells.append(predecessor_cell)
    return tuple(predecessor_cells)


@pytest.mark.parametrize("group_mode", ["single", "two", "merged"])
def test_completed_values_project_to_exact_group_sample_positions(
    tmp_path, monkeypatch, group_mode
):
    from tests.alignment_cell_decision_fixtures import _numeric_peak

    monkeypatch.setattr(
        "tests.untargeted_matrix_projection_fixtures.cid_nl_peak", _numeric_peak
    )
    _, _, _, alignment = _untarget_projection_inputs(
        tmp_path, monkeypatch, group_mode=group_mode
    )
    answers = decide_group_cell_population(alignment)
    projection = project_untarget_matrix(alignment, answers)
    assert projection.finalized_alignment is alignment
    assert projection.lineage is alignment.lineage
    assert [row.group for row in projection.rows] == sorted(
        alignment.final_population, key=lambda group: group.group_hypothesis_id
    )
    observations = {
        (item.group_hypothesis_id, item.sample_key): item
        for item in alignment.cell_observations
    }
    assert len(projection.decisions) == len(observations)
    numeric_by_sample = {}
    for row in projection.rows:
        assert (
            tuple(d.subject.canonical_sample for d in row.decisions)
            == alignment.canonical_samples
        )
        for decision in row.decisions:
            subject = decision.subject
            evidence = observations[
                subject.group_hypothesis_id,
                subject.canonical_sample.sample_name,
            ]
            assert decision.evidence is evidence
            assert subject.group is row.group
            payload, integration = answers[evidence.key]
            assert decision.payload is payload
            assert decision.selected_integration is integration
            if subject.member_subject is None:
                assert decision.variant == "not_searched"
                assert integration is None
            else:
                assert decision.variant == "numeric"
                assert integration is evidence.integrations[0]
                numeric_by_sample[subject.canonical_sample.raw_stem] = decision.value
    if group_mode != "single":
        assert numeric_by_sample["sample-c"] == pytest.approx(
            2 * numeric_by_sample["sample-b"]
        )
    with pytest.raises(FrozenInstanceError):
        projection.rows = ()


@pytest.mark.parametrize(
    "disposition", ["established", "provisional", "unresolved", "rejected"]
)
def test_disposition_projects_row_route_and_complete_counts(
    tmp_path, monkeypatch, disposition
):
    alignment, answers, projection = _completed_projection(
        tmp_path, monkeypatch, disposition=disposition, numeric=True
    )
    (row,) = projection.rows
    assert row.route.reason == disposition
    assert row.route.destination == (
        "matrix" if disposition == "established" else "archive"
    )
    assert sum(d.value is not None for d in row.decisions) == (
        2 if disposition == "established" else 1
    )
    reordered = project_untarget_matrix(
        alignment, dict(reversed(tuple(answers.items())))
    )
    assert projection == reordered


def test_projection_is_deterministic_for_reversed_capture_order(tmp_path, monkeypatch):
    _, captures, _, forward = _untarget_projection_inputs(tmp_path, monkeypatch)
    reverse = finalizer_owner.finalize_scientific_alignment(
        scope=forward.scope,
        captures=tuple(reversed(captures)),
        grouping_result=forward.grouping_result,
        cell_observation_handoff=forward.cell_observation_handoff,
        predecessor_cell_observations=_predecessor_cells(forward),
    )
    assert project_untarget_matrix(forward, decide_group_cell_population(forward)) == (
        project_untarget_matrix(reverse, decide_group_cell_population(reverse))
    )


@pytest.mark.parametrize("variant", ["missing", "extra", "crossed"])
def test_projection_requires_answers_for_the_exact_observation_population(
    tmp_path, monkeypatch, variant
):
    alignment, answers, _ = _completed_projection(
        tmp_path / "owner", monkeypatch, disposition="established"
    )
    changed = dict(answers)
    if variant == "missing":
        changed.pop(next(iter(changed)))
    elif variant == "extra":
        changed["other-observation"] = next(iter(changed.values()))
    else:
        group, sample_key = next(iter(changed))
        changed[group, sample_key + "-other"] = changed.pop((group, sample_key))
    with pytest.raises(UntargetProjectionContractError, match="missing|extra|crossed"):
        project_untarget_matrix(alignment, changed)
