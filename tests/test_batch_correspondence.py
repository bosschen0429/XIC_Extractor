import pytest

from xic_extractor.alignment.batch_correspondence import (
    BatchFeature,
    BatchIstd,
    choose_reference_batch,
    correspond_batches,
)

ISTD_RT = (9.0, 12.5, 16.5, 23.5, 25.8, 40.6)
SHIFT = 0.3  # old batch elutes 0.3 min later than the reference batch
SIZES = {"old": 12, "new": 83}


def _istds():
    return [
        istd
        for index, rt in enumerate(ISTD_RT)
        for istd in (
            BatchIstd("new", f"I{index}", rt),
            BatchIstd("old", f"I{index}", rt + SHIFT),
        )
    ]


def _anchor_features():
    """Ten unique channels spread across RT, present in both batches."""
    features = []
    for index in range(10):
        rt = 8.0 + index * 3.3
        mz = 300.0 + index * 7.0
        features.append(BatchFeature("new", f"n{index}", "dR", mz, rt, 60))
        features.append(BatchFeature("old", f"o{index}", "dR", mz, rt + SHIFT, 10))
    return features


def _run(features, istds=None, sizes=SIZES):
    return correspond_batches(
        features,
        _istds() if istds is None else istds,
        batch_sizes=sizes,
        reference_batch="new",
        precursor_tolerance_ppm=20.0,
    )


def _state(result, batch, key):
    return next(
        item for item in result.features if item.batch_id == batch and item.key == key
    )


def test_unique_counterparts_pair_and_map_the_batch_offset():
    result = _run(_anchor_features())
    batch_map = result.map_for("old")
    assert batch_map is not None
    assert batch_map.to_reference(20.0 + SHIFT) == pytest.approx(20.0, abs=1e-6)
    for index in range(10):
        decision = _state(result, "old", f"o{index}")
        assert decision.state == "paired"
        assert decision.partners == (("new", f"n{index}"),)
    assert len(result.rows()) == 10


def test_two_counterparts_in_window_stay_unpaired_as_ambiguous():
    features = _anchor_features() + [
        BatchFeature("old", "x", "dR", 500.0, 20.0 + SHIFT, 10),
        BatchFeature("new", "y1", "dR", 500.001, 19.95, 40),
        BatchFeature("new", "y2", "dR", 500.001, 20.08, 40),
    ]
    result = _run(features)
    decision = _state(result, "old", "x")
    assert decision.state == "unpaired_ambiguous"
    assert set(decision.competitors) == {("new", "y1"), ("new", "y2")}
    assert _state(result, "new", "y1").state == "unpaired_ambiguous"
    rows = result.rows()
    assert (("old", "x"),) in rows and (("new", "y1"),) in rows


def test_reverse_competitor_blocks_a_forward_unique_match():
    # One new feature, two old features: forward from each old feature is
    # unique, but the new feature's window holds both.
    features = _anchor_features() + [
        BatchFeature("new", "y", "dR", 500.0, 20.0, 40),
        BatchFeature("old", "x1", "dR", 500.0, 20.0 + SHIFT, 10),
        BatchFeature("old", "x2", "dR", 500.0, 20.1 + SHIFT, 2),
    ]
    result = _run(features)
    assert _state(result, "old", "x1").state == "unpaired_ambiguous"
    assert _state(result, "new", "y").state == "unpaired_ambiguous"


def test_weak_competitor_below_anchor_prevalence_still_competes():
    features = _anchor_features() + [
        BatchFeature("old", "x", "dR", 500.0, 20.0 + SHIFT, 10),
        BatchFeature("new", "y", "dR", 500.0, 20.0, 60),
        BatchFeature("new", "weak", "dR", 500.0, 20.1, 1),
    ]
    result = _run(features)
    assert _state(result, "old", "x").state == "unpaired_ambiguous"
    batch_map = result.map_for("old")
    assert batch_map is not None
    assert any(item.key == "x" for item in batch_map.excluded_anchors)


def test_other_channel_or_far_counterpart_is_not_a_candidate():
    features = _anchor_features() + [
        BatchFeature("old", "x", "dR", 500.0, 20.0 + SHIFT, 10),
        BatchFeature("new", "loss", "G", 500.0, 20.0, 60),
        BatchFeature("new", "mass", "dR", 500.05, 20.0, 60),
        BatchFeature("old", "far", "dR", 600.0, 20.0 + SHIFT, 10),
        BatchFeature("new", "far", "dR", 600.0, 25.0, 60),
    ]
    result = _run(features)
    assert _state(result, "old", "x").state == "unpaired_no_candidate"
    assert _state(result, "old", "far").state == "unpaired_outside_window"


def test_elution_family_votes_once():
    features = _anchor_features() + [
        BatchFeature("new", "iso", "dR", 301.003, 8.02, 60),
        BatchFeature("old", "iso", "dR", 301.003, 8.02 + SHIFT, 10),
    ]
    result = _run(features)
    batch_map = result.map_for("old")
    assert batch_map is not None
    family = [a for a in batch_map.anchors if ("old", "iso") in a.feature_keys]
    assert len(family) == 1
    assert ("old", "o0") in family[0].feature_keys


def test_pairing_ignores_batch_ids_keys_and_input_order():
    base = _run(_anchor_features())
    renamed = [
        BatchFeature(
            {"new": "B", "old": "A"}[item.batch_id],
            "k" + item.key[::-1],
            item.neutral_loss_tag,
            item.precursor_mz,
            item.reference_rt_min,
            item.sample_count,
        )
        for item in reversed(_anchor_features())
    ]
    istds = [
        BatchIstd(
            {"new": "B", "old": "A"}[istd.batch_id], istd.label, istd.reference_rt_min
        )
        for istd in reversed(_istds())
    ]
    other = correspond_batches(
        renamed,
        istds,
        batch_sizes={"B": 83, "A": 12},
        reference_batch="B",
        precursor_tolerance_ppm=20.0,
    )

    def shape(result, rename):
        return sorted(
            tuple(sorted(rename(member) for member in row)) for row in result.rows()
        )

    assert shape(base, lambda m: m[1]) == shape(other, lambda m: m[1][1:][::-1])


def test_without_istds_nothing_pairs():
    result = _run(_anchor_features(), istds=[])
    assert {item.state for item in result.features} == {"unpaired_no_map"}


def test_reference_batch_is_largest_then_latest_acquired():
    assert choose_reference_batch({"a": 12, "b": 83}) == "b"
    times = {"a": 2, "b": 1}
    assert choose_reference_batch({"a": 5, "b": 5}, times.__getitem__) == "a"


def test_istds_bound_to_one_identity_pair_by_identity_not_by_rt():
    # The old batch carries the ISTD and an unbound twin 0.04 min apart; RT
    # alone could not choose between them.
    features = _anchor_features() + [
        BatchFeature("old", "istd", "dR", 261.1273, 9.0 + SHIFT, 10, "hmdC"),
        BatchFeature("old", "twin", "dR", 261.1273, 9.04 + SHIFT, 7),
        BatchFeature("new", "istd", "dR", 261.1273, 9.0, 82, "hmdC"),
    ]

    result = _run(features)

    assert _state(result, "old", "istd").partners == (("new", "istd"),)
    assert _state(result, "new", "istd").partners == (("old", "istd"),)
    # The bound pair is not a candidate for the twin.
    assert _state(result, "old", "twin").state == "unpaired_no_candidate"


def test_search_window_follows_the_map_error_of_neighboring_istds():
    # The last ISTD alone departs from the shift, so only the late map is
    # uncertain: a competitor 0.5 min away is ambiguous late but not early.
    istds = [
        istd
        for index, rt in enumerate(ISTD_RT)
        for istd in (
            BatchIstd("new", f"I{index}", rt),
            BatchIstd("old", f"I{index}", rt + (1.3 if rt == ISTD_RT[-1] else SHIFT)),
        )
    ]
    features = [item for item in _anchor_features() if item.reference_rt_min < 25]
    features += [
        BatchFeature("old", "early", "dR", 500.0, 20.0 + SHIFT, 10),
        BatchFeature("new", "early", "dR", 500.0, 20.0, 60),
        BatchFeature("new", "early_rival", "dR", 500.0, 20.5, 60),
        BatchFeature("old", "late", "dR", 600.0, 38.0 + SHIFT, 10),
        BatchFeature("new", "late", "dR", 600.0, 38.0, 60),
        BatchFeature("new", "late_rival", "dR", 600.0, 38.5, 60),
    ]
    result = _run(features, istds)
    batch_map = result.map_for("old")
    assert batch_map is not None
    assert batch_map.search_half_width_at(20.0) == pytest.approx(0.2)
    assert batch_map.search_half_width_at(38.0) > 1.0
    assert _state(result, "old", "early").partners == (("new", "early"),)
    assert _state(result, "new", "early_rival").state == "unpaired_outside_window"
    assert _state(result, "old", "late").state == "unpaired_ambiguous"
