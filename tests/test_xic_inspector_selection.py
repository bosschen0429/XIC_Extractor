"""Tests for XIC Inspector selection engine, oracle invariants, and strata behavior."""

from __future__ import annotations

from xic_inspector.selection.canonical import (
    CanonicalItem,
    Witness,
    build_canonical_ref,
)
from xic_inspector.selection.query import filter_by_query
from xic_inspector.selection.sampler import sample_stratified_sha256_v1
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


def test_stratified_sha256_oracle_seed0_limit1_selects_rest() -> None:
    """Algorithm oracle check: seed=0, limit=1 must select 'rest'."""
    items = [
        _make_item("row", "r_att", "attention", ("s1",), is_attention=True),
        _make_item("candidate", "c_ex", "excluded", ("s1",)),
        _make_item("row", "r_null", "null", ("s1",)),
        _make_item("row", "r_zero", "zero", ("s1",)),
        _make_item("row", "r_rest", "rest", ("s1",)),
    ]

    selected = sample_stratified_sha256_v1(items, limit=1, seed=0)
    assert len(selected) == 1
    assert selected[0].stratum == "rest"
    assert selected[0].identifier == "r_rest"


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
