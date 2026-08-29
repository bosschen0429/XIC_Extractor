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
from xic_extractor.alignment.projection.target import post_cell as target_post_cell
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


def test_target_result_consumes_decisions_without_reopening_their_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    def reject_revalidation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("application result reopened the Target decision owner")

    monkeypatch.setattr(
        target_post_cell,
        "validate_production_cell_decision",
        reject_revalidation,
    )

    result = finalize_alignment_result(
        TargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    )

    assert result.scientific_core is scientific_core
    assert result.completed_projection is projection


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


def test_untarget_result_rejects_a_partial_completed_row_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )
    partial_projection = copy(projection)
    object.__setattr__(partial_projection, "rows", ())

    with pytest.raises(ApplicationResultContractError, match="missing|partial"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=partial_projection,
            )
        )


def test_target_result_rejects_substituted_completed_counts(tmp_path: Path) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")
    object.__setattr__(
        projection,
        "counted_detection_count",
        bool(projection.counted_detection_count),
    )

    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=projection,
            )
        )


def test_untarget_result_rejects_a_substituted_completed_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )
    substituted_route = copy(projection.rows[0].route)
    object.__setattr__(substituted_route, "destination", "archive")
    substituted_row = copy(projection.rows[0])
    object.__setattr__(substituted_row, "route", substituted_route)
    substituted_projection = copy(projection)
    object.__setattr__(substituted_projection, "rows", (substituted_row,))

    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=substituted_projection,
            )
        )


def test_target_result_rejects_a_substituted_nested_decision_owner(
    tmp_path: Path,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")
    object.__setattr__(
        projection.decisions[0],
        "basis_bytes",
        b"substituted-owner-basis",
    )

    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=projection,
            )
        )


def test_untarget_result_rejects_a_partial_pre_cell_owner_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )
    object.__setattr__(projection.pre_cell_projection, "cell_subjects", ())

    with pytest.raises(ApplicationResultContractError, match="receipt|partial"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=projection,
            )
        )


@pytest.mark.parametrize("variant", ["targeted", "untargeted"])
def test_result_rejects_a_substituted_completed_projection_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    if variant == "targeted":
        scientific_core, projection = _target_completion(tmp_path / "target")
        request = TargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=copy(projection),
        )
    else:
        scientific_core, projection = _untarget_completion(
            tmp_path / "untarget",
            monkeypatch,
        )
        request = UntargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=copy(projection),
        )

    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(request)


def test_target_result_rejects_a_substituted_scientific_core(tmp_path: Path) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    with pytest.raises(ApplicationResultContractError, match="scientific core"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, copy(scientific_core)),
                completed_projection=projection,
            )
        )


@pytest.mark.parametrize(
    "variant",
    [
        "target_request",
        "target_definition",
        "target_sample",
        "untarget_request",
        "untarget_group",
        "untarget_sample",
    ],
)
def test_result_rejects_equal_nested_owner_clones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    if variant.startswith("target_"):
        scientific_core, projection = _target_completion(tmp_path / variant)
        if variant == "target_request":
            object.__setattr__(
                projection.row_binding,
                "processing_request",
                copy(projection.row_binding.processing_request),
            )
        elif variant == "target_definition":
            object.__setattr__(
                projection.row_binding,
                "target",
                copy(projection.row_binding.target),
            )
        else:
            samples = scientific_core.scope.samples
            object.__setattr__(
                scientific_core.scope,
                "samples",
                (copy(samples[0]), *samples[1:]),
            )
        request = TargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    else:
        scientific_core, projection = _untarget_completion(
            tmp_path / variant,
            monkeypatch,
        )
        if variant == "untarget_request":
            cloned_request = copy(scientific_core.processing_request)
            object.__setattr__(
                scientific_core,
                "processing_request",
                cloned_request,
            )
            object.__setattr__(scientific_core.scope, "request", cloned_request)
        elif variant == "untarget_group":
            row_key = projection.pre_cell_projection.row_keys[0]
            object.__setattr__(row_key, "group", copy(row_key.group))
        else:
            samples = scientific_core.scope.samples
            object.__setattr__(
                scientific_core.scope,
                "samples",
                (copy(samples[0]), *samples[1:]),
            )
        request = UntargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )

    with pytest.raises(ApplicationResultContractError, match="substituted|receipt"):
        finalize_alignment_result(request)


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
