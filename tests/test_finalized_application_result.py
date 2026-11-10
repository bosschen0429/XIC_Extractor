"""No-RAW ordinary-CI contracts for discriminated application results."""

from __future__ import annotations

import io
import pickle
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

from tests.target_pre_cell_projection_fixtures import _finalized_case
from tests.untargeted_matrix_projection_fixtures import _completed_projection
from xic_extractor.alignment.cell_decision.owner import decide_target_cell_population
from xic_extractor.alignment.projection.target.post_cell import (
    TargetPostCellProjection,
    project_target_post_cell,
)
from xic_extractor.alignment.projection.target.pre_cell import project_target_pre_cell
from xic_extractor.alignment.projection.untarget import GroupCells
from xic_extractor.alignment.projection.untarget import matrix as untarget_matrix
from xic_extractor.alignment.result import (
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
)


def _target_completion(
    root: Path,
    *,
    sample_stems: tuple[str, ...] = ("sample-a", "sample-b"),
) -> tuple[object, TargetPostCellProjection]:
    root.mkdir(exist_ok=True)
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        root,
        sample_stems=sample_stems,
        observed_stems=(),
        group_specs=(((), "provisional"),),
    )
    pre_cell = project_target_pre_cell(alignment)
    return alignment, project_target_post_cell(
        pre_cell,
        decide_target_cell_population(pre_cell),
    )


def _untarget_completion(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, tuple[GroupCells, ...]]:
    pre_cell, _decisions, completed = _completed_projection(
        root,
        monkeypatch,
        disposition="established",
    )
    return pre_cell, completed


def test_target_result_binds_exact_owner_graph_and_completion_receipts(
    tmp_path: Path,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    result = FinalizedTargetApplicationResult(
        scientific_core=cast(Any, scientific_core),
        completed_projection=projection,
    )

    assert type(result) is FinalizedTargetApplicationResult
    assert result.variant == "targeted"
    assert len(result.canonical_samples) == 2
    assert all(
        actual is expected
        for actual, expected in zip(
            result.canonical_samples,
            scientific_core.canonical_samples,
            strict=True,
        )
    )
    assert result.scientific_core is scientific_core
    assert result.completed_projection is projection
    assert all(
        actual is expected
        for actual, expected in zip(
            result.completed_projection.decisions,
            projection.decisions,
            strict=True,
        )
    )
    with pytest.raises(FrozenInstanceError):
        result.completed_projection = projection  # type: ignore[misc]


def test_untarget_result_binds_complete_rows_without_matrix_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )

    result = FinalizedUntargetApplicationResult(
        scientific_core=cast(Any, scientific_core),
        group_cells=projection,
    )

    assert type(result) is FinalizedUntargetApplicationResult
    assert result.variant == "untargeted"
    assert result.canonical_samples == scientific_core.canonical_samples
    assert result.scientific_core is scientific_core
    assert result.group_cells is projection


def test_untarget_result_consumes_projection_without_recomputing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )

    def reject_recomputation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "application result reopened the Untarget projection owner"
        )

    monkeypatch.setattr(
        untarget_matrix,
        "project_untarget_matrix",
        reject_recomputation,
    )

    result = FinalizedUntargetApplicationResult(
        scientific_core=cast(Any, scientific_core),
        group_cells=projection,
    )

    assert result.scientific_core is scientific_core
    assert result.group_cells is projection


def _restore_test_mapping_proxy(values):
    return MappingProxyType(values)


def _pickle_completed_owner(value):
    stream = io.BytesIO()
    pickler = pickle.Pickler(stream, protocol=pickle.HIGHEST_PROTOCOL)
    pickler.dispatch_table = {
        MappingProxyType: lambda mapping: (
            _restore_test_mapping_proxy,
            (dict(mapping),),
        )
    }
    pickler.dump(value)
    return pickle.loads(stream.getvalue())


def test_untarget_owner_pickle_rebinds_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, projection = _untarget_completion(tmp_path, monkeypatch)
    restored_core, restored_projection = _pickle_completed_owner((core, projection))
    assert restored_core is not core
    assert restored_projection is not projection
    result = FinalizedUntargetApplicationResult(
        scientific_core=restored_core,
        group_cells=restored_projection,
    )
    assert result.scientific_core is restored_core
    assert result.group_cells is restored_projection
    (restored_group, restored_cells), *_ = restored_projection
    (group, cells), *_ = projection
    assert [d.variant for d in restored_cells] == [d.variant for d in cells]
    assert restored_group == group
    assert restored_group.destination == group.destination
