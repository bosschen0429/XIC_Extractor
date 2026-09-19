"""Tests for XIC Inspector selection engine, oracle invariants, and strata behavior."""

from __future__ import annotations

from dataclasses import replace

from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.query import filter_by_query
from xic_inspector.selection.sampler import sample_cross_sample_sha256
from xic_inspector.selection.strata import classify_scoped_stratum


def _make_item(
    kind: str,
    identifier: str,
    stratum: str,
    sample_ids: tuple[str, ...],
    witnesses: tuple[Witness, ...] = (),
    is_attention: bool = False,
) -> CanonicalItem:
    sample_id = sample_ids[0] if kind == "candidate" else None
    ref, sref = build_canonical_ref(
        kind=kind,  # type: ignore
        request_id="req1",
        attempt_id="att1",
        identifier=identifier,
        sample_id=sample_id,
    )
    return CanonicalItem(
        kind=kind,  # type: ignore
        request_id="req1",
        attempt_id="att1",
        identifier=identifier,
        serialized_ref=sref,
        sample_ids=sample_ids,
        witnesses=witnesses,
        stratum=stratum,
        is_attention=is_attention,
    )


def test_default_sample_keeps_cross_sample_cases_among_many_singletons() -> None:
    """A large candidate population must not hide broad or recovery families."""
    groups = []
    for name, observed, origin, count in (
        ("broad_direct", 8, "discovery_support", 60),
        ("broad_recovery", 7, "recovery_support", 2),
        ("partial_direct", 4, "discovery_support", 60),
        ("partial_reacquired", 3, "reacquired_support", 15),
        ("limited", 1, "discovery_support", 200),
    ):
        for index in range(count):
            groups.append(replace(
                _make_item("row", f"{name}_{index}", "rest", ("s1",)),
                details={"review_summary": {
                    "observed_sample_count": observed,
                    "scope_sample_count": 8,
                    "origin_kinds": [origin],
                }},
            ))
    candidates = [
        _make_item("candidate", f"c_{index}", "excluded", ("s1",))
        for index in range(2000)
    ]
    selected = sample_cross_sample_sha256(groups + candidates, limit=30, seed=0)
    assert len(selected) == 30
    assert all(item.kind == "row" for item in selected)
    assert sum(item.details["review_summary"]["observed_sample_count"] >= 2
               for item in selected) >= 20
    assert any(item.identifier.startswith("broad_recovery") for item in selected)
    assert any(item.identifier.startswith("broad_direct") for item in selected)
    assert any(item.identifier.startswith("partial_reacquired") for item in selected)
    assert any(item.identifier.startswith("limited") for item in selected)
    assert selected == sample_cross_sample_sha256(
        list(reversed(candidates + groups)), limit=30, seed=0
    )
    assert selected == sample_cross_sample_sha256(groups, limit=30, seed=0)


def test_default_sample_uses_candidates_only_after_existing_groups() -> None:
    row = _make_item("row", "row", "rest", ("s1",))
    candidates = [
        _make_item("candidate", f"c_{index}", "excluded", ("s1",))
        for index in range(20)
    ]
    selected = sample_cross_sample_sha256([row, *candidates], limit=4, seed=0)
    assert len(selected) == 4
    assert row in selected
    assert len({item.serialized_ref for item in selected}) == 4


def test_scoped_strata_priority() -> None:
    """Verify scoped strata priority: attention > excluded > null > zero > rest."""
    # NULL + nonzero -> null
    assert classify_scoped_stratum(
        is_attention=False, is_excluded=False, has_null_cell=True, has_zero_cell=False
    ) == "null"
    # Only nonzero -> rest
    assert classify_scoped_stratum(
        is_attention=False, is_excluded=False, has_null_cell=False, has_zero_cell=False
    ) == "rest"
    # 0 + nonzero -> zero
    assert classify_scoped_stratum(
        is_attention=False, is_excluded=False, has_null_cell=False, has_zero_cell=True
    ) == "zero"
    # Scoped attention has highest priority
    assert classify_scoped_stratum(
        is_attention=True, is_excluded=True, has_null_cell=True, has_zero_cell=True
    ) == "attention"


def test_acceptance_a3_witness_chain_query() -> None:
    """Acceptance A3: 無row的G: C1/S1=(500, 1min), C2/S2=(600, 8min)."""
    w1 = Witness(
        canonical_sample_identity_id="S1",
        candidate_id="C1",
        group_hypothesis_id="G",
        precursor_mz=500.0,
        rt_apex_min=1.0,
    )
    w2 = Witness(
        canonical_sample_identity_id="S2",
        candidate_id="C2",
        group_hypothesis_id="G",
        precursor_mz=600.0,
        rt_apex_min=8.0,
    )
    group_item = _make_item("group", "G", "rest", ("S1", "S2"), witnesses=(w1, w2))

    # Query C1 + mz=500 + rt=8 -> 0 (C1 RT is 1min; S2 RT 8min has mz 600)
    sel, matched, missing_rt = filter_by_query(
        [group_item],
        candidate_ids=("C1",),
        mz_min=500.0,
        mz_max=500.0,
        rt_min=8.0,
        rt_max=8.0,
    )
    assert matched == 0
    assert len(sel) == 0

    # Query C1 + mz=500 + rt=1 -> Hits G once with witness C1/S1
    sel, matched, missing_rt = filter_by_query(
        [group_item],
        candidate_ids=("C1",),
        mz_min=500.0,
        mz_max=500.0,
        rt_min=1.0,
        rt_max=1.0,
    )
    assert matched == 1
    assert len(sel) == 1
    assert sel[0].identifier == "G"

    # When restricting sample_ids to ("S1",), S2 cannot testify for rt=8
    sel, matched, missing_rt = filter_by_query(
        [group_item],
        sample_ids=("S1",),
        rt_min=8.0,
        rt_max=8.0,
    )
    assert matched == 0


def test_query_pagination_and_stability() -> None:
    """Verify offset and limit pagination over query results."""
    items = [
        _make_item(
            "row",
            f"row_{i:02d}",
            "rest",
            ("s1",),
            witnesses=(
                Witness(
                    canonical_sample_identity_id="s1",
                    candidate_id=f"c_{i:02d}",
                    precursor_mz=400.0 + i,
                    rt_apex_min=2.0 + i * 0.1,
                ),
            ),
        )
        for i in range(10)
    ]
    p1, m1, _ = filter_by_query(items, mz_min=400.0, mz_max=410.0, offset=0, limit=5)
    p2, m2, _ = filter_by_query(items, mz_min=400.0, mz_max=410.0, offset=5, limit=5)

    assert m1 == 10
    assert m2 == 10
    assert len(p1) == 5
    assert len(p2) == 5
    # Union of p1 and p2 is complete and non-overlapping
    assert set(it.identifier for it in p1).isdisjoint(set(it.identifier for it in p2))
    assert set(it.identifier for it in p1) | set(it.identifier for it in p2) == set(
        it.identifier for it in items
    )
