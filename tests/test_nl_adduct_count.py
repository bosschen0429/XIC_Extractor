from collections import Counter

import numpy as np
import pytest

from xic_extractor.diagnostics.nl_adduct_count import (
    Anchor,
    Candidate,
    RunCount,
    build_units,
    cumulative,
    group_candidates,
    other_sugar_checks,
    overlap,
    rt_map,
    shape_pairs,
    shared_rt_pairs,
    synced,
    union_runs,
)
from xic_extractor.diagnostics.nl_adduct_count_acquisition import read_nucleosides

DR = 116.0474
DC = (Anchor("dC", 228.0979, 8.66), Anchor("dU", 229.0822, None))


def _c(peak_id, mz, rt, events=2, ratio=0.0):
    return Candidate(peak_id, mz, rt, events, ratio)


def _run(candidates, pairs=(), removed=frozenset(), anchors=DC, sample="s"):
    groups = group_candidates(candidates, DR, anchors)
    kept = [g for g in groups if g.category != "nucleoside_elution"]
    units = build_units(kept, pairs)
    return RunCount(sample, tuple(candidates), groups, units, removed, anchors)


def test_groups_join_coeluting_known_differences_and_take_their_category():
    groups = group_candidates(
        (
            _c("m", 300.10, 20.0),
            _c("iso", 301.1034, 20.05),
            _c("insrc", 416.1474, 20.02),
            _c("far", 301.1034, 20.3),
            _c("dc", 228.0979, 8.70),
            _c("dc_other_rt", 228.0979, 11.0),
            _c("du", 229.0822, 30.0),
            _c("near", 350.0, 8.80),
            _c("two", 500.0, 25.0, ratio=0.1),
        ),
        DR,
        DC,
    )
    by_light = {g.members[0].peak_id: g for g in groups}
    assert [c.peak_id for c in by_light["m"].members] == ["m", "iso", "insrc"]
    assert by_light["m"].relations == {"iso": ("13C",), "insrc": ("+loss",)}
    assert by_light["m"].category == "two_sugar"
    assert {k: g.category for k, g in by_light.items() if k != "m"} == {
        "far": "other",
        "dc": "nucleoside",
        "dc_other_rt": "nucleoside_mz",
        "du": "nucleoside_mz",
        "near": "nucleoside_elution",
        "two": "two_sugar",
    }


def test_waterfall_matches_each_candidates_fate():
    run = _run(
        (
            _c("dc", 228.0979, 8.66),
            _c("dc13", 229.1013, 8.66),
            _c("near", 350.0, 8.75),
            _c("x", 400.0, 20.0),
            _c("y", 450.0, 20.03),
            _c("one", 500.0, 30.0, events=1),
            _c("late", 600.0, 45.0),
            _c("rna", 700.0, 22.3),
        ),
        pairs=[(1, 2)],
        removed=frozenset({"700.0000@22.30"}),
    )
    summary = run.summary()
    assert [
        summary[k]
        for k in (
            "detected",
            "derived_known_difference",
            "derived_nucleoside",
            "derived_synchronized_shape",
            "unconfirmed",
            "counted",
            "counted_late",
        )
    ] == [8, 1, 2, 1, 1, 3, 1]
    assert Counter(row["fate"] for row in run.candidate_rows()) == {
        "counted": 3,
        "derived_known_difference": 1,
        "derived_nucleoside_elution": 1,
        "derived_other_sugar_nucleoside": 1,
        "derived_synchronized_shape": 1,
        "unconfirmed": 1,
    }


def test_shape_sync_needs_close_apex_different_mass_and_same_shape():
    groups = group_candidates(
        (
            _c("a", 400.0, 20.0),
            _c("b", 450.0, 20.05),
            _c("c", 400.005, 20.0),
            _c("d", 500.0, 20.2),
        ),
        DR,
        DC,
    )
    assert shape_pairs(list(groups)) == [(0, 2), (1, 2)]
    rt = np.linspace(21.8, 22.8, 40)
    peak = np.exp(-(((rt - 22.3) / 0.05) ** 2))
    shifted = np.exp(-(((rt - 22.45) / 0.05) ** 2))
    assert synced(peak, peak * 3, rt, rt)
    assert not synced(peak, shifted, rt, rt)
    assert not synced(peak, np.ones_like(peak))


def test_other_sugar_check_skips_same_sugar_nucleosides_and_unseen_anchors():
    run = _run((_c("dc", 228.0979, 8.66), _c("m6a", 282.1197, 22.3)))
    rna = (Anchor("m6A", 282.1197, 22.21), Anchor("C", 244.0928, None))
    assert [
        (u.unit_id, a.label) for u, _, _, a in other_sugar_checks(run.units, rna)
    ] == [("282.1197@22.30", "m6A")]


def test_controls_union_cumulative_and_shared_compounds_after_rt_mapping():
    anchors = lambda shift: (  # noqa: E731
        Anchor("dC", 228.0979, 8.66 + shift),
        Anchor("dA", 252.1091, 19.6 + shift),
    )
    controls = [
        _run((_c("a", 400.0, 14.4), _c("u", mz, 30.2, events=1)), anchors=anchors(0.2))
        for mz in (500.0, 510.0)
    ]
    consensus = union_runs(controls)
    assert sorted((c.runs, c.unconfirmed) for c in consensus) == [
        ((0,), True),
        ((0, 1), False),
        ((1,), True),
    ]
    assert cumulative(consensus, 2) == {"counted": [1, 1], "unconfirmed": [1, 2]}
    main = _run((_c("a", 400.0, 14.0),), anchors=anchors(0.0))
    assert overlap(main, controls, consensus) == [
        {
            "unit": "400.0000@14.00",
            "kind": "other",
            "control_injections": 2,
            "control_unconfirmed": False,
        }
    ]


def test_compounds_extend_rt_pairs_only_past_nucleosides_and_one_to_one():
    nucleosides = lambda shift: (  # noqa: E731
        Anchor("dC", 228.0979, 8.66 + shift),
        Anchor("dA", 252.1091, 19.6 + shift),
    )
    control = _run(
        (
            _c("inside", 300.0, 15.0),
            _c("late", 400.0, 30.0),
            _c("twin1", 450.0, 32.0),
            _c("twin2", 450.0, 32.5),
            _c("x", 500.0, 35.0),
        ),
        anchors=nucleosides(0.4),
    )
    main = _run(
        (
            _c("inside", 300.0, 14.0),
            _c("late", 400.0, 30.0),
            _c("twin", 450.0, 32.2),
            _c("x", 500.0, 35.1),
        ),
        anchors=nucleosides(0.0),
    )
    consensus = union_runs([control])
    assert shared_rt_pairs(main, [control], consensus) == [
        (8.66, 9.06),
        (19.6, 20.0),
        (30.0, 30.0),
        (35.1, 35.0),
    ]
    assert {row["unit"] for row in overlap(main, [control], consensus)} == {
        "400.0000@30.00",
        "450.0000@32.20",
        "500.0000@35.10",
    }


def test_rt_map_interpolates_and_shifts_past_the_ends():
    mapped = rt_map([(20.0, 20.5), (8.0, 9.0)])
    assert [mapped(rt) for rt in (4.0, 8.0, 14.0, 30.0)] == [5.0, 9.0, 14.75, 30.5]
    assert rt_map([])(12.0) == 12.0


def test_nucleoside_table_pairs_windows_with_tags_and_locates_bad_rows(tmp_path):
    path = tmp_path / "nucleosides.tsv"
    header = "label\tmz\tneutral_loss_tag\trt_min\trt_max\n"
    path.write_text(header + "dC\t228.09788\tDNA_dR\t8.05\t9.55\n", encoding="utf-8")
    ((window, tag),) = read_nucleosides(path)
    assert (window.label, window.mz, window.rt_low_min, tag) == (
        "dC",
        228.09788,
        8.05,
        "DNA_dR",
    )
    for row, message in (
        ("dC\t228.09788\t\t8.05\t9.55", ":2：缺少 neutral_loss_tag"),
        ("dC\t228.09788\tDNA_dR\t9.55\t8.05", ":2：rt_min 與 rt_max 需成對"),
        ("dC\tx\tDNA_dR\t8.05\t9.55", ":2：mz 必須是有限數字"),
    ):
        path.write_text(header + row + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            read_nucleosides(path)
