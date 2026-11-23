from collections import Counter

import numpy as np
import pytest

from xic_extractor.diagnostics.nl_adduct_count import (
    Anchor,
    Candidate,
    count_candidates,
    cumulative,
    group_candidates,
    relation_label,
    synced,
    union_runs,
)
from xic_extractor.diagnostics.nl_adduct_count_acquisition import read_nucleosides
from xic_extractor.xic_models import XICTrace

DR = 116.0474
DC = (Anchor("dC", 228.0979, 8.66), Anchor("dU", 229.0822, None))


def _c(peak_id, mz, rt, events=2, ratio=0.0):
    return Candidate(peak_id, mz, rt, events, ratio)


def _trace(rt, apex):
    if apex is None:
        return XICTrace(rt, np.zeros_like(rt))
    return XICTrace(rt, np.exp(-(((rt - apex) / 0.05) ** 2)))


def _xic(apex_by_mz):
    """Gaussian peaks at the given apex per m/z; flat traces elsewhere."""

    def read(requests):
        return [
            _trace(np.linspace(centre - 0.5, centre + 0.5, 41), apex_by_mz.get(mz))
            for mz, centre in requests
        ]

    return read


def _run(candidates, apex_by_mz=None, other_sugar=(), anchors=DC, sample="s"):
    return count_candidates(
        sample, candidates, DR, anchors, other_sugar, _xic(apex_by_mz or {})
    )


def _fates(run):
    return {row["peak_id"]: row["fate"] for row in run.candidate_rows()}


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
            _c("near", 350.1, 8.80),
            _c("two", 500.1, 25.0, ratio=0.1),
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


def test_waterfall_counts_each_candidates_fate():
    run = _run(
        (
            _c("dc", 228.0979, 8.66),
            _c("dc13", 229.1013, 8.66),
            _c("near", 350.1, 8.75),
            _c("x", 400.1, 20.0),
            _c("y", 450.1, 20.03),
            _c("one", 500.1, 30.0, events=1),
            _c("late", 600.1, 45.0),
            _c("rna", 700.1, 22.3),
            _c("mg_acetate_cluster", 233.0361, 55.99),
        ),
        {400.1: 20.0, 450.1: 20.0, 700.1: 22.3, 282.1197: 22.3},
        other_sugar=(Anchor("m6A", 282.1197, 22.3),),
    )
    summary = run.summary()
    assert [
        summary[k]
        for k in (
            "detected",
            "derived_known_difference",
            "derived_nucleoside",
            "derived_synchronized_shape",
            "no_nucleobase",
            "unconfirmed",
            "counted",
            "counted_late",
        )
    ] == [9, 1, 2, 1, 1, 1, 3, 1]
    assert summary["removed_other_sugar"] == ["700.1000@22.30"]
    assert Counter(_fates(run).values()) == {
        "counted": 3,
        "derived_known_difference": 1,
        "derived_nucleoside_elution": 1,
        "derived_other_sugar_nucleoside": 1,
        "derived_synchronized_shape": 1,
        "no_nucleobase": 1,
        "unconfirmed": 1,
    }


def test_no_nucleobase_needs_every_ion_of_the_compound_without_a_base():
    run = _run(
        (
            _c("compound", 231.0719, 8.29),
            _c("metal_complex", 307.0281, 8.29),
            _c("mg_acetate_cluster", 233.0361, 55.99),
        ),
        {231.0719: 8.29, 307.0281: 8.29},
    )
    assert _fates(run) == {
        "compound": "counted",
        "metal_complex": "derived_synchronized_shape",
        "mg_acetate_cluster": "no_nucleobase",
    }


def test_derived_ions_name_their_parent_and_known_relation():
    run = _run(
        (
            _c("dc", 228.0979, 8.66),
            _c("dc_meoh", 260.1241, 8.70),
            _c("dc_unknown", 371.2, 8.75),
            _c("compound", 300.1, 20.0),
            _c("mg_acetate", 382.0905, 20.0),
            _c("mg_acetate_13c", 383.0939, 20.04),
        ),
        {300.1: 20.0, 382.0905: 20.0},
    )
    rows = {
        r["peak_id"]: (r["relation"], r["derived_from"]) for r in run.candidate_rows()
    }
    assert rows == {
        "dc": ("", ""),
        "dc_meoh": ("MeOH", "dC"),
        "dc_unknown": ("", "dC"),
        "compound": ("", ""),
        "mg_acetate": ("Mg+OAc", "300.1000@20.00"),
        "mg_acetate_13c": ("13C", "382.0905@20.00"),
    }
    assert run.summary()["derived_relations"] == {
        "adduct": 1,
        "unlabeled": 1,
        "metal": 1,
        "isotope": 1,
    }
    assert relation_label(82.9939, 300.1, DC, DR) == "Mg+OAc;13C"


def test_shape_sync_joins_close_apices_of_different_mass_with_the_same_shape():
    candidates = (
        _c("a", 400.1, 20.0),
        _c("b", 450.1, 20.08),
        _c("c", 400.105, 20.0),
        _c("d", 500.1, 20.2),
    )
    run = _run(candidates, {400.1: 20.0, 450.1: 20.0, 400.105: 20.0, 500.1: 20.0})
    assert [u.members for u in run.units] == [
        [(400.1, 20.0), (400.105, 20.0), (450.1, 20.08)],
        [(500.1, 20.2)],
    ]
    rt = np.linspace(21.8, 22.8, 40)
    peak = _trace(rt, 22.3)
    assert synced(peak, XICTrace(rt, peak.intensity * 3), same_apex=True)
    assert not synced(peak, _trace(rt, 22.45), same_apex=True)
    assert not synced(peak, _trace(rt, None))


def test_other_sugar_removes_only_compounds_that_follow_a_seen_nucleoside():
    run = _run(
        (_c("dc", 228.0979, 8.66), _c("m6a", 282.1197, 22.3)),
        {228.0979: 8.66, 245.0768: 8.66, 282.1197: 22.25},
        other_sugar=(
            Anchor("U", 245.0768, 8.66),
            Anchor("m6A", 282.1197, 22.21),
            Anchor("C", 244.0928, None),
        ),
    )
    assert _fates(run) == {
        "dc": "counted",
        "m6a": "derived_other_sugar_nucleoside",
    }


def test_controls_union_within_measured_rt_and_count_cumulatively():
    controls = [
        _run((_c("a", 400.1, 14.0 + shift), _c("u", mz, 30.2, events=1)))
        for mz, shift in ((500.1, 0.0), (510.1, 0.2))
    ]
    consensus = union_runs(controls)
    assert sorted((c.runs, c.unconfirmed) for c in consensus) == [
        ((0,), True),
        ((0, 1), False),
        ((1,), True),
    ]
    assert cumulative(consensus, 2) == {"counted": [1, 1], "unconfirmed": [1, 2]}
    shifted = _run((_c("a", 400.1, 14.4),))
    assert len(union_runs([controls[0], shifted])) == 3


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
