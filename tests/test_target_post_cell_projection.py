"""No-RAW ordinary-CI contract tests for Target post-cell projection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.alignment_cell_decision_fixtures import _numeric_peak
from tests.target_pre_cell_projection_fixtures import _finalized_case
from xic_extractor.alignment.cell_decision.owner import (
    decide_target_cell_population,
)
from xic_extractor.alignment.projection.target.post_cell import (
    TargetPostCellProjection,
    project_target_post_cell,
)
from xic_extractor.alignment.projection.target.pre_cell import (
    project_target_pre_cell,
)


def test_target_post_cell_projection_closes_fixed_row_counts_and_review_routing(
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
    pre_cell = project_target_pre_cell(alignment)
    decisions = decide_target_cell_population(pre_cell)

    completed = project_target_post_cell(
        pre_cell,
        tuple(reversed(decisions)),
    )

    assert isinstance(completed, TargetPostCellProjection)
    assert completed.pre_cell_projection is pre_cell
    assert completed.row_binding is pre_cell.row_binding
    assert completed.cell_subjects == pre_cell.cell_subjects
    assert all(
        actual is expected
        for actual, expected in zip(completed.decisions, decisions, strict=True)
    )
    assert completed.accepted_cell_count == 1
    assert completed.no_write_count == 1
    assert completed.decision_count == 2

    review_ref = completed.review_queue_references[0]
    no_write = next(item for item in decisions if item.variant != "numeric")
    assert review_ref.subject is no_write.subject
    assert review_ref.decision is no_write
    assert review_ref.row_binding is pre_cell.row_binding
    assert review_ref.reasons == ("searched_no_observation",)


def test_disjoint_internal_groups_bind_one_fixed_target_row(
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
        observed_stems=("sample-a", "sample-b"),
        group_specs=(
            (("sample-a",), "provisional"),
            (("sample-b",), "provisional"),
        ),
    )
    pre_cell = project_target_pre_cell(alignment)
    decisions = decide_target_cell_population(pre_cell)

    completed = project_target_post_cell(pre_cell, decisions)

    assert pre_cell.row_binding.variant == "bound"
    assert completed.accepted_cell_count == 2
    assert completed.no_write_count == 0
