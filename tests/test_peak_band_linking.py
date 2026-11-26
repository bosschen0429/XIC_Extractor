"""Check band membership on measured coordinates, independently of RAW access."""

from dataclasses import replace

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.peak_band_linking import (
    MAX_BOOTSTRAP_RT_STEP_MIN,
    link_peak_bands,
    link_with_band_drift,
)
from xic_extractor.alignment.rt_drift import DriftAnchor, RtDriftModel


def test_competing_peaks_keep_their_parallel_bands_without_using_owner_ids():
    order = {"S1": 1, "S2": 2, "S3": 3}
    first = tuple(cid_nl_owner(sample, "early", apex_rt=10.0) for sample in order)
    second = tuple(cid_nl_owner(sample, "late", apex_rt=10.2) for sample in order)
    owners = first + second
    renamed = tuple(
        replace(owner, owner_id=f"renamed-{index}")
        for index, owner in enumerate(reversed(owners))
    )
    expected = {
        frozenset((sample, 10.0) for sample in order),
        frozenset((sample, 10.2) for sample in order),
    }
    for population in (owners, renamed):
        bands = link_peak_bands(
            [(owner, owner.peak.rt_apex_min) for owner in population],
            injection_order=order,
        )
        assert {
            frozenset((owner.sample_stem, owner.peak.rt_apex_min) for owner in band)
            for band in bands
        } == expected


def test_local_mass_links_cannot_bridge_an_out_of_tolerance_band():
    """Two adjacent deltas near 6 ppm cannot bridge an end-to-end 12 ppm."""
    first = cid_nl_owner("S1", "low", precursor_mz=500.0, apex_rt=10.0)
    second = cid_nl_owner("S2", "mid", precursor_mz=500.003, apex_rt=10.01)
    third = cid_nl_owner("S3", "high", precursor_mz=500.006, apex_rt=10.05)
    assert link_peak_bands(
        [(owner, owner.peak.rt_apex_min) for owner in (third, second, first)],
        injection_order={"S1": 1, "S2": 2, "S3": 3},
    ) == ((first, second), (third,))


def test_an_exact_rt_tie_uses_measured_order_not_owner_id_or_arrival_order():
    center = cid_nl_owner("S1", "center", apex_rt=10.0)
    early = cid_nl_owner("S2", "Z", apex_rt=9.75)
    late = cid_nl_owner("S2", "A", apex_rt=10.25)
    order = {"S1": 1, "S2": 2}
    for owners in (
        (late, center, early),
        (
            replace(early, owner_id=late.owner_id),
            replace(late, owner_id=early.owner_id),
            center,
        ),
    ):
        linked = link_peak_bands(
            [(owner, owner.peak.rt_apex_min) for owner in owners],
            injection_order=order,
        )
        assert tuple(tuple(o.peak.rt_apex_min for o in band) for band in linked) == (
            (10.0, 9.75),
            (10.25,),
        )


def test_rt_and_injection_limits_apply_to_each_link_not_the_whole_band():
    first = cid_nl_owner("S1", "a", apex_rt=10.0)
    second = cid_nl_owner("S9", "b", apex_rt=10.4)
    third = cid_nl_owner("S17", "c", apex_rt=10.8)
    after_gap = cid_nl_owner("S26", "d", apex_rt=10.8)
    after_step = cid_nl_owner("S27", "e", apex_rt=11.21)
    assert link_peak_bands(
        [
            (owner, owner.peak.rt_apex_min)
            for owner in (first, second, third, after_gap, after_step)
        ],
        injection_order={"S1": 1, "S9": 9, "S17": 17, "S26": 26, "S27": 27},
    ) == ((first, second, third), (after_gap,), (after_step,))


def test_bootstrap_wider_rt_step_crosses_a_whole_injection_jump():
    first = cid_nl_owner("S1", "before", apex_rt=9.0)
    shifted = cid_nl_owner("S2", "shifted", apex_rt=8.5)
    order = {"S1": 1, "S2": 2}
    coordinates = [(first, 9.0), (shifted, 8.5)]
    assert link_peak_bands(coordinates, injection_order=order) == ((first,), (shifted,))
    assert link_peak_bands(
        coordinates, injection_order=order, max_rt_step_min=MAX_BOOTSTRAP_RT_STEP_MIN
    ) == ((first, shifted),)


def test_drift_is_fitted_to_the_seed_and_only_relinks_known_coordinates():
    """Six direct peaks establish a known slope across the batch-state jump."""
    order = {f"S{i}": i for i in range(1, 10)}
    b = {f"S{i}": 0.08 * i for i in range(1, 7)} | {"S7": 1.5, "S8": 1.58}
    expected = tuple(
        cid_nl_owner(sample, "band", apex_rt=10.0 + state)
        for sample, state in b.items()
    )
    drift = RtDriftModel(
        anchors=(
            DriftAnchor(
                "measured",
                10.3,
                0,
                b,
                owner_ids=frozenset(owner.owner_id for owner in expected[:6]),
            ),
        )
    )
    competitor = cid_nl_owner("S7", "other", apex_rt=12.0)
    unknown = cid_nl_owner("S9", "unknown", apex_rt=11.95)
    owners = (*expected, competitor, unknown)
    linked = link_with_band_drift(
        [(owner, owner.peak.rt_apex_min) for owner in owners],
        injection_order=order,
        drift=drift,
        has_direct_support=lambda owner: owner.primary_identity_event is not None,
    )
    assert linked[0].members == expected
    assert abs(linked[0].coefficients.time - 1.0) < 1e-10
    assert {frozenset(band.members) for band in linked[1:]} == {
        frozenset((competitor, unknown)),
    }

    without_support = (
        expected[0],
        *(replace(owner, primary_identity_event=None) for owner in expected[1:6]),
        *owners[6:],
    )
    linked_without_support = link_with_band_drift(
        [(owner, owner.peak.rt_apex_min) for owner in without_support],
        injection_order=order,
        drift=drift,
        has_direct_support=lambda owner: owner.primary_identity_event is not None,
    )
    assert linked_without_support[0].members == without_support[:6]
    assert linked_without_support[0].coefficients.nonzero_count == 0
