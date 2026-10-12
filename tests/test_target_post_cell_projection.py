"""No-RAW ordinary-CI contract tests for Target post-cell projection."""

from __future__ import annotations

from copy import copy
from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_alignment_cell_decision import _numeric_peak
from tests.test_target_pre_cell_projection import _finalized_case
from xic_extractor.alignment.cell_decision.owner import (
    NoWriteCellDecision,
    decide_target_cell_population,
)
from xic_extractor.alignment.projection.target import (
    TargetPostCellProjection,
    TargetProjectionContractError,
    project_target_post_cell,
    project_target_pre_cell,
    validate_target_post_cell_projection,
)


def test_target_post_cell_projection_closes_fixed_row_counts_and_review_routing(
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
    assert completed.include_row is True
    assert completed.inclusion_reason == "fixed_target_row"
    assert completed.accepted_cell_count == 1
    assert completed.no_write_count == 1
    assert completed.decision_count == 2
    assert completed.review_queue_count == 1

    review_ref = completed.review_queue_references[0]
    no_write = next(item for item in decisions if item.variant != "numeric")
    assert review_ref.subject is no_write.subject
    assert review_ref.decision is no_write
    assert review_ref.row_binding is pre_cell.row_binding
    assert review_ref.reasons == ("searched_no_observation",)
    validate_target_post_cell_projection(completed)


def test_disjoint_internal_groups_bind_one_fixed_target_row(
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
    assert completed.review_queue_count == 0


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "duplicate",
        "crossed",
        "subject_substituted",
        "decision_tampered",
        "partial",
    ],
)
def test_target_post_cell_rejects_incomplete_or_substituted_decisions(
    tmp_path: Path,
    variant: str,
) -> None:
    owner_root = tmp_path / "owner"
    owner_root.mkdir()
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        owner_root,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    pre_cell = project_target_pre_cell(alignment)
    decisions = decide_target_cell_population(pre_cell)

    other_root = tmp_path / "other"
    other_root.mkdir()
    _other_fixture, _other_scope, other_alignment, _other_hypotheses = _finalized_case(
        other_root,
        sample_stems=("sample-a", "sample-b"),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    other_pre_cell = project_target_pre_cell(other_alignment)
    other_decisions = decide_target_cell_population(other_pre_cell)

    if variant == "missing":
        supplied: tuple[Any, ...] = decisions[:-1]
    elif variant == "duplicate":
        supplied = decisions + (decisions[0],)
    elif variant == "crossed":
        supplied = (other_decisions[0], decisions[1])
    elif variant == "subject_substituted":
        substituted = copy(decisions[0])
        object.__setattr__(substituted, "subject", copy(decisions[0].subject))
        supplied = (substituted, decisions[1])
    elif variant == "decision_tampered":
        substituted = copy(decisions[0])
        object.__setattr__(
            substituted,
            "payload",
            NoWriteCellDecision("unavailable"),
        )
        supplied = (substituted, decisions[1])
    else:
        supplied = (decisions[0], cast(Any, object()))

    with pytest.raises(
        TargetProjectionContractError,
        match="decision|population|subject|basis",
    ):
        project_target_post_cell(pre_cell, cast(Any, supplied))


def test_target_post_cell_requires_immutable_decision_population(
    tmp_path: Path,
) -> None:
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a",),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    pre_cell = project_target_pre_cell(alignment)
    decisions = decide_target_cell_population(pre_cell)

    with pytest.raises(TargetProjectionContractError, match="immutable tuple"):
        project_target_post_cell(pre_cell, cast(Any, list(decisions)))


def test_target_post_cell_validation_rejects_derived_count_substitution(
    tmp_path: Path,
) -> None:
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        tmp_path,
        sample_stems=("sample-a",),
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    pre_cell = project_target_pre_cell(alignment)
    completed = project_target_post_cell(
        pre_cell,
        decide_target_cell_population(pre_cell),
    )

    object.__setattr__(completed, "accepted_cell_count", 1)

    with pytest.raises(TargetProjectionContractError, match="counts"):
        validate_target_post_cell_projection(completed)
