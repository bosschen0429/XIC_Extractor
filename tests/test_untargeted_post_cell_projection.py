"""No-RAW ordinary-CI contract tests for Untarget post-cell projection."""

from __future__ import annotations

from collections import Counter
from copy import copy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from tests.test_alignment_cell_decision import _numeric_peak
from tests.test_alignment_owner_clustering import _owner
from tests.test_untargeted_pre_cell_projection import (
    _untarget_projection_inputs,
)
from xic_extractor.alignment.cell_decision import (
    NoWriteCellDecision,
    decide_group_cell_population,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.owner_clustering import cluster_sample_local_owners
from xic_extractor.alignment.projection.untarget import (
    UntargetPostCellProjection,
    UntargetProjectionContractError,
    project_untarget_post_cell,
    project_untarget_pre_cell,
    validate_untarget_post_cell_projection,
)


def _completed_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    disposition: str,
    numeric: bool = False,
):
    if numeric:
        monkeypatch.setattr(
            "tests.test_untargeted_cid_nl_golden_path.cid_nl_peak",
            _numeric_peak,
        )
    _scope, _captures, _receipt, alignment = _untarget_projection_inputs(
        tmp_path,
        monkeypatch,
        group_mode="merged" if disposition == "established" else "single",
        disposition=disposition,
    )
    pre_cell = project_untarget_pre_cell(alignment)
    decisions = decide_group_cell_population(pre_cell, alignment)
    return pre_cell, decisions, project_untarget_post_cell(pre_cell, decisions)


def test_established_row_preserves_exact_owner_inputs_and_typed_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_cell, decisions, completed = _completed_projection(
        tmp_path,
        monkeypatch,
        disposition="established",
    )

    assert completed.pre_cell_projection is pre_cell
    assert completed.lineage is pre_cell.lineage
    assert completed.decisions == decisions
    assert len(completed.rows) == 1
    row = completed.rows[0]
    assert row.row_key is pre_cell.row_keys[0]
    assert tuple(item.subject for item in row.decisions) == tuple(
        subject for subject in pre_cell.cell_subjects if subject.row_key is row.row_key
    )
    assert all(
        decision is owner_decision
        for decision, owner_decision in zip(
            row.decisions,
            decisions,
            strict=True,
        )
    )
    assert row.route.destination == "matrix"
    assert row.route.reason == "established"
    assert completed.included_rows == (row,)
    assert completed.archive_rows == ()
    assert row.counts.total_cells == len(pre_cell.canonical_samples)
    assert row.counts.numeric_cells == 0
    assert row.counts.no_write_cells == len(pre_cell.canonical_samples)
    expected_counts = Counter(item.variant for item in decisions)
    assert all(
        item.count == expected_counts[item.variant] for item in row.counts.by_variant
    )
    assert sum(item.count for item in row.counts.by_variant) == len(
        pre_cell.canonical_samples
    )
    validate_untarget_post_cell_projection(completed)

    with pytest.raises(FrozenInstanceError):
        completed.rows = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("disposition", "numeric"),
    [
        ("provisional", True),
        ("unresolved", False),
        ("rejected", False),
    ],
)
def test_non_established_rows_remain_explicit_archive_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
    numeric: bool,
) -> None:
    pre_cell, _decisions, completed = _completed_projection(
        tmp_path,
        monkeypatch,
        disposition=disposition,
        numeric=numeric,
    )

    row = completed.rows[0]
    assert row.row_key.group.disposition == disposition
    assert row.route.destination == "archive"
    assert row.route.reason == disposition
    assert completed.included_rows == ()
    assert completed.archive_rows == (row,)
    assert completed.archive_row_keys == pre_cell.row_keys
    if disposition == "provisional":
        assert row.counts.numeric_cells == 1


def test_moderate_group_disposition_controls_current_untarget_route(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    moderate_group = cluster_sample_local_owners(
        (
            _owner(
                "sample-a", "moderate-a", apex_rt=10.0,
                evidence_score=45, seed_event_count=1,
            ),
            _owner(
                "sample-b", "moderate-b", apex_rt=10.1,
                evidence_score=45, seed_event_count=1,
            ),
        ),
        config=AlignmentConfig(),
    )[0]
    strong_group = cluster_sample_local_owners(
        (
            _owner("sample-a", "strong-a", apex_rt=10.0),
            _owner("sample-b", "strong-b", apex_rt=10.1),
        ),
        config=AlignmentConfig(),
    )[0]

    assert moderate_group.evidence.startswith("owner_feature_inference;assigned;")
    assert len(moderate_group.owners) == 2
    assert moderate_group.disposition == "established"
    assert moderate_group.review_only is False
    assert strong_group.evidence.startswith("owner_feature_inference;assigned;")
    assert len(strong_group.owners) == 2
    assert strong_group.disposition == "established"
    assert strong_group.review_only is False

    _pre_cell, _decisions, moderate_projection = _completed_projection(
        tmp_path / "moderate",
        monkeypatch,
        disposition=moderate_group.disposition,
        numeric=True,
    )
    _pre_cell, _decisions, strong_projection = _completed_projection(
        tmp_path / "strong",
        monkeypatch,
        disposition=strong_group.disposition,
        numeric=True,
    )
    assert moderate_projection.rows[0].route.destination == "matrix"
    assert moderate_projection.archive_rows == ()
    assert strong_projection.rows[0].route.destination == "matrix"
    assert strong_projection.archive_rows == ()


def test_complete_decisions_are_canonicalized_without_caller_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_cell, decisions, expected = _completed_projection(
        tmp_path,
        monkeypatch,
        disposition="established",
    )

    reordered = project_untarget_post_cell(
        pre_cell,
        tuple(reversed(decisions)),
    )

    assert reordered == expected
    assert reordered.decisions == decisions


@pytest.mark.parametrize(
    "variant",
    ["missing", "partial", "duplicate", "extra", "crossed", "substituted"],
)
def test_post_cell_binding_and_explicit_audit_reject_invalid_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    pre_cell, decisions, _completed = _completed_projection(
        tmp_path / "owner",
        monkeypatch,
        disposition="established",
    )
    if variant == "missing":
        changed = ()
    elif variant == "partial":
        changed = decisions[:-1]
    elif variant == "duplicate":
        changed = decisions + (decisions[0],)
    elif variant == "extra":
        changed = decisions + decisions
    elif variant == "crossed":
        _other_pre_cell, other_decisions, _other_completed = _completed_projection(
            tmp_path / "crossed",
            monkeypatch,
            disposition="established",
        )
        changed = (other_decisions[0],) + decisions[1:]
    else:
        forged = copy(decisions[0])
        object.__setattr__(forged, "payload", NoWriteCellDecision("unavailable"))
        changed = (forged,) + decisions[1:]

    with pytest.raises(
        UntargetProjectionContractError,
        match="missing|partial|duplicate|extra|crossed|substituted|basis",
    ):
        if variant == "substituted":
            # Deliberate mutation of a frozen owner is an explicit audit concern.
            row = copy(_completed.rows[0])
            object.__setattr__(row, "decisions", changed)
            audited = copy(_completed)
            object.__setattr__(audited, "rows", (row,))
            validate_untarget_post_cell_projection(audited)
        else:
            project_untarget_post_cell(pre_cell, changed)


def test_post_cell_projection_cannot_be_directly_minted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_cell, _decisions, completed = _completed_projection(
        tmp_path,
        monkeypatch,
        disposition="established",
    )

    with pytest.raises(UntargetProjectionContractError, match="must be minted"):
        UntargetPostCellProjection(
            pre_cell_projection=pre_cell,
            rows=completed.rows,
            _mint_token=object(),
        )
