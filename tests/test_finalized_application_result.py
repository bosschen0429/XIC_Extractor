"""No-RAW ordinary-CI contracts for discriminated application results."""

from __future__ import annotations

import io
import pickle
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

from tests.test_target_pre_cell_projection import _finalized_case
from tests.test_untargeted_post_cell_projection import _completed_projection
from xic_extractor.alignment.cell_decision.owner import decide_target_cell_population
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
from xic_extractor.alignment.result import (
    scientific_alignment as scientific_alignment_owner,
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
    assert result.completion_receipts[0] is scientific_core.grouping_result
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


def test_result_consumes_scientific_core_without_reopening_its_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    def reject_revalidation(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("application result reopened the #305 scientific owner")

    monkeypatch.setattr(
        scientific_alignment_owner,
        "_validate_finalized_alignment",
        reject_revalidation,
    )

    result = finalize_alignment_result(
        TargetApplicationResultRequest(
            scientific_core=cast(Any, scientific_core),
            completed_projection=projection,
        )
    )

    assert result.scientific_core is scientific_core


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
        scientific_core.grouping_result,
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


def test_untarget_projection_rejects_a_partial_subject_population(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )
    object.__setattr__(projection.pre_cell_projection, "cell_subjects", ())

    with pytest.raises(ValueError, match="population|subject"):
        untarget_post_cell.project_untarget_post_cell(
            projection.pre_cell_projection,
            projection.decisions,
        )


def test_untarget_result_rejects_a_substituted_completed_projection_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / "untarget",
        monkeypatch,
    )
    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=copy(projection),
            )
        )


def test_target_result_rejects_a_substituted_scientific_core(tmp_path: Path) -> None:
    scientific_core, projection = _target_completion(tmp_path / "target")

    with pytest.raises(ApplicationResultContractError, match="scientific core"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, copy(scientific_core)),
                completed_projection=projection,
            )
        )


@pytest.mark.parametrize("variant", ["untarget_request", "untarget_sample"])
def test_untarget_result_rejects_equal_nested_owner_clones(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    scientific_core, projection = _untarget_completion(
        tmp_path / variant,
        monkeypatch,
    )
    if variant == "untarget_request":
        cloned_request = copy(scientific_core.processing_request)
        object.__setattr__(scientific_core, "processing_request", cloned_request)
        object.__setattr__(scientific_core.scope, "request", cloned_request)
    else:
        samples = scientific_core.scope.samples
        object.__setattr__(
            scientific_core.scope,
            "samples",
            (copy(samples[0]), *samples[1:]),
        )
    with pytest.raises(ApplicationResultContractError, match="substituted|receipt"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=cast(Any, scientific_core),
                completed_projection=projection,
            )
        )


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


@pytest.mark.parametrize(
    "other_samples",
    [("sample-a", "sample-b"), ("sample-a", "sample-c"), ("sample-a",)],
)
def test_crossed_same_variant_core_and_projection_fail_closed(
    tmp_path: Path,
    other_samples: tuple[str, ...],
) -> None:
    owner_core, _owner_projection = _target_completion(tmp_path / "owner")
    _other_core, other_projection = _target_completion(
        tmp_path / "other", sample_stems=other_samples
    )

    with pytest.raises(ApplicationResultContractError, match="crossed|substituted"):
        finalize_alignment_result(
            TargetApplicationResultRequest(
                scientific_core=cast(Any, owner_core),
                completed_projection=other_projection,
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


@pytest.mark.parametrize("tamper", ["core", "projection"])
def test_untarget_owner_pickle_rebinds_completion_and_still_rejects_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    core, projection = _untarget_completion(tmp_path, monkeypatch)
    restored_core, restored_projection = _pickle_completed_owner((core, projection))
    assert restored_core is not core
    assert restored_projection is not projection
    assert restored_projection.pre_cell_projection.finalized_alignment is restored_core
    result = finalize_alignment_result(
        UntargetApplicationResultRequest(
            scientific_core=restored_core,
            completed_projection=restored_projection,
        )
    )
    assert result.scientific_core is restored_core
    assert result.completed_projection is restored_projection
    assert (
        restored_core.processing_request.processing_request_id
        == core.processing_request.processing_request_id
    )
    assert restored_projection.rows[0].counts == projection.rows[0].counts
    assert restored_projection.rows[0].route == projection.rows[0].route
    if tamper == "core":
        object.__setattr__(
            restored_core,
            "grouping_result",
            copy(restored_core.grouping_result),
        )
    else:
        object.__setattr__(restored_projection, "rows", ())
    with pytest.raises(ApplicationResultContractError, match="receipt|substituted"):
        finalize_alignment_result(
            UntargetApplicationResultRequest(
                scientific_core=restored_core,
                completed_projection=restored_projection,
            )
        )
    with pytest.raises(ValueError, match="receipt|substituted"):
        _pickle_completed_owner((restored_core, restored_projection))


@pytest.mark.parametrize("owner", ["core", "projection"])
def test_unsealed_completion_cannot_be_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    core, projection = _untarget_completion(tmp_path, monkeypatch)
    value = core if owner == "core" else projection
    object.__delattr__(value, "_completion_seal")
    with pytest.raises(ValueError, match="unsealed"):
        _pickle_completed_owner(value)
