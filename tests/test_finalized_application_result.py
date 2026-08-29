"""No-RAW ordinary-CI contracts for discriminated application results."""

from __future__ import annotations

from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any, cast

import pytest

from tests.test_target_pre_cell_projection import _finalized_case
from tests.test_untargeted_post_cell_projection import _completed_projection
from xic_extractor.alignment.cell_decision import decide_target_cell_population
from xic_extractor.alignment.projection.target import (
    TargetPostCellProjection,
    project_target_post_cell,
    project_target_pre_cell,
)
from xic_extractor.alignment.projection.untarget import UntargetPostCellProjection
from xic_extractor.alignment.projection.untarget import post_cell as untarget_post_cell
from xic_extractor.alignment.result import (
    ApplicationResultContractError,
    FinalizedTargetApplicationResult,
    FinalizedUntargetApplicationResult,
    TargetApplicationResultRequest,
    UntargetApplicationResultRequest,
    finalize_alignment_result,
)


def _target_completion(
    root: Path,
) -> tuple[object, TargetPostCellProjection]:
    root.mkdir(exist_ok=True)
    _fixture, _scope, alignment, _hypotheses = _finalized_case(
        root,
        sample_stems=("sample-a", "sample-b"),
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
) -> tuple[object, UntargetPostCellProjection]:
    pre_cell, _decisions, completed = _completed_projection(
        root,
        monkeypatch,
        disposition="established",
    )
    return pre_cell.finalized_alignment, completed


def test_target_result_binds_exact_owner_graph_and_completion_receipts(
    tmp_path: Path,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    result = finalize_alignment_result(
        TargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    )

    assert type(result) is FinalizedTargetApplicationResult
    assert result.variant == "targeted"
    assert result.processing_request is scientific_core.processing_request
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
    assert result.completion_receipts[0] is scientific_core.final_cross_sample_receipt
    assert result.completion_receipts[1] is projection
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

    result = finalize_alignment_result(
        UntargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    )

    assert type(result) is FinalizedUntargetApplicationResult
    assert result.variant == "untargeted"
    assert result.processing_request is scientific_core.processing_request
    assert result.canonical_samples == scientific_core.canonical_samples
    assert result.scientific_core is scientific_core
    assert result.completed_projection is projection
    assert result.completion_receipts == (
        scientific_core.final_cross_sample_receipt,
        projection,
    )
    assert result.completed_projection.included_rows == projection.included_rows
    assert result.completed_projection.archive_rows == projection.archive_rows


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
        untarget_post_cell,
        "project_untarget_pre_cell",
        reject_recomputation,
    )
    monkeypatch.setattr(
        untarget_post_cell,
        "project_untarget_post_cell",
        reject_recomputation,
    )

    result = finalize_alignment_result(
        UntargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    )

    assert result.scientific_core is scientific_core
    assert result.completed_projection is projection


def test_mixed_target_and_untarget_variants_fail_before_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_core, target_projection = _target_completion(tmp_path / "target")
    untarget_core, untarget_projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )

    with pytest.raises(ApplicationResultContractError, match="Target projection"):
        TargetApplicationResultRequest(
            scientific_core=cast(Any, target_core),
            completed_projection=cast(Any, untarget_projection),
        )
    with pytest.raises(ApplicationResultContractError, match="Untarget projection"):
        UntargetApplicationResultRequest(
            scientific_core=cast(Any, untarget_core),
            completed_projection=cast(Any, target_projection),
        )


def test_crossed_same_variant_core_and_projection_fail_closed(
    tmp_path: Path,
) -> None:
    owner_core, _owner_projection = _target_completion(tmp_path / "owner")
    _other_core, other_projection = _target_completion(tmp_path / "other")

    with pytest.raises(ApplicationResultContractError, match="crossed|substituted"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, owner_core),
                completed_projection=other_projection,
            )
        )


@pytest.mark.parametrize(
    "variant",
    ["missing_sample", "duplicate_sample", "partial_decisions", "duplicate_decisions"],
)
def test_incomplete_duplicate_or_partial_owner_populations_fail_closed(
    tmp_path: Path,
    variant: str,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / variant)
    changed_core = scientific_core
    changed_projection = projection
    if variant == "missing_sample":
        changed_core = copy(scientific_core)
        object.__setattr__(
            changed_core,
            "canonical_samples",
            scientific_core.canonical_samples[:-1],
        )
    elif variant == "duplicate_sample":
        changed_core = copy(scientific_core)
        object.__setattr__(
            changed_core,
            "canonical_samples",
            (
                scientific_core.canonical_samples[0],
                scientific_core.canonical_samples[0],
            ),
        )
    elif variant == "partial_decisions":
        changed_projection = copy(projection)
        object.__setattr__(
            changed_projection,
            "decisions",
            projection.decisions[:-1],
        )
    else:
        changed_projection = copy(projection)
        object.__setattr__(
            changed_projection,
            "decisions",
            projection.decisions + (projection.decisions[0],),
        )

    with pytest.raises(
        ApplicationResultContractError,
        match="sample universe|population|decision|duplicate|partial",
    ):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, changed_core),
                completed_projection=changed_projection,
            )
        )


def test_finalized_application_result_cannot_be_caller_minted(
    tmp_path: Path,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    with pytest.raises(ApplicationResultContractError, match="must be minted"):
        FinalizedTargetApplicationResult(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
            _mint_token=object(),
        )
