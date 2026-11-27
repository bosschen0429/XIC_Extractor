"""Check band membership on measured coordinates, independently of RAW access."""

from dataclasses import replace

from tests.membership_fixtures import cid_nl_owner
from xic_extractor.alignment.band_grouping import band_shift_rows, group_peak_bands
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.peak_band_linking import (
    MAX_BOOTSTRAP_RT_STEP_MIN,
    competing_band_members,
    link_peak_bands,
    link_with_band_drift,
)
from xic_extractor.alignment.rt_drift import DriftAnchor, RtDriftModel
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    RtCalibrationPolicy,
    SampleRtCalibration,
)
from xic_extractor.injection_timeline import InjectionSequence


def test_competing_peaks_keep_their_parallel_bands_without_using_owner_ids():
    order = {"S1": 1, "S2": 2, "S3": 3}
    first = tuple(cid_nl_owner(sample, "early", apex_rt=10.0) for sample in order)
    second = tuple(cid_nl_owner(sample, "late", apex_rt=10.2) for sample in order)
    owners = first + second
    renamed = tuple(
        replace(
            owner,
            hypothesis=replace(owner.hypothesis, hypothesis_id=f"renamed-{index}"),
        )
        for index, owner in enumerate(reversed(owners))
    )
    expected = {
        frozenset((sample, 10.0) for sample in order),
        frozenset((sample, 10.2) for sample in order),
    }
    for population in (owners, renamed):
        bands = link_peak_bands(
            [(owner, owner.peak.rt_apex_min) for owner in population],
            injections=InjectionSequence(order),
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
        injections=InjectionSequence({"S1": 1, "S2": 2, "S3": 3}),
    ) == ((first, second), (third,))


def test_an_exact_rt_tie_uses_measured_order_not_owner_id_or_arrival_order():
    center = cid_nl_owner("S1", "center", apex_rt=10.0)
    early = cid_nl_owner("S2", "Z", apex_rt=9.75)
    late = cid_nl_owner("S2", "A", apex_rt=10.25)
    order = {"S1": 1, "S2": 2}
    for owners in (
        (late, center, early),
        (
            replace(
                early, hypothesis=replace(early.hypothesis, hypothesis_id=late.owner_id)
            ),
            replace(
                late, hypothesis=replace(late.hypothesis, hypothesis_id=early.owner_id)
            ),
            center,
        ),
    ):
        linked = link_peak_bands(
            [(owner, owner.peak.rt_apex_min) for owner in owners],
            injections=InjectionSequence(order),
        )
        assert tuple(tuple(o.peak.rt_apex_min for o in band) for band in linked) == (
            (10.0, 9.75),
            (10.25,),
        )


def test_competition_names_the_replacement_without_reopening_the_band():
    order = {"S1": 1, "S2": 2, "S3": 3}
    left = cid_nl_owner("S1", "a", apex_rt=10.0)
    selected = cid_nl_owner("S2", "b", apex_rt=10.2)
    right = cid_nl_owner("S3", "c", apex_rt=10.35)
    replacement = cid_nl_owner("S2", "d", apex_rt=10.68)
    outside_mass = cid_nl_owner("S2", "f", apex_rt=10.28, precursor_mz=500.006)
    points = tuple(
        (owner, owner.peak.rt_apex_min)
        for owner in (right, replacement, selected, outside_mass, left)
    )
    band = link_peak_bands(points, injections=InjectionSequence(order))[0]
    assert band == (left, selected, right)
    assert competing_band_members(
        band,
        points,
        injections=InjectionSequence(order),
        has_direct_support=lambda owner: owner.primary_identity_event is not None,
    ) == ((selected, replacement),)


def test_replacement_must_keep_both_sides_of_a_bridge_connected():
    order = {"S1": 1, "S2": 2, "S3": 3}
    left = cid_nl_owner("S1", "a", apex_rt=10.0)
    bridge = cid_nl_owner("S2", "b", apex_rt=10.15)
    right = cid_nl_owner("S3", "c", apex_rt=10.5)
    disconnects_right = cid_nl_owner("S2", "d", apex_rt=9.7)
    points = tuple(
        (owner, owner.peak.rt_apex_min)
        for owner in (right, disconnects_right, left, bridge)
    )
    band = link_peak_bands(points, injections=InjectionSequence(order))[0]
    assert band == (left, bridge, right)
    assert (
        competing_band_members(
            band,
            points,
            injections=InjectionSequence(order),
            has_direct_support=lambda owner: owner.primary_identity_event is not None,
        )
        == ()
    )


def test_a_replacement_cannot_turn_an_established_band_into_one_direct_peak():
    order = {"S1": 1, "S2": 2, "S3": 3}
    first = cid_nl_owner("S1", "a", apex_rt=10.0)
    direct = cid_nl_owner("S2", "b", apex_rt=10.1)
    bridge = replace(cid_nl_owner("S3", "c", apex_rt=10.2), primary_identity_event=None)
    ms1_rival = replace(
        cid_nl_owner("S2", "d", apex_rt=10.38), primary_identity_event=None
    )
    points = tuple(
        (owner, owner.peak.rt_apex_min) for owner in (first, direct, bridge, ms1_rival)
    )
    band = link_peak_bands(points, injections=InjectionSequence(order))[0]
    assert band == (first, direct, bridge)
    assert (
        competing_band_members(
            band,
            points,
            injections=InjectionSequence(order),
            has_direct_support=lambda owner: owner.primary_identity_event is not None,
        )
        == ()
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
        injections=InjectionSequence({f"S{i}": i for i in range(1, 28)}),
    ) == ((first, second, third), (after_gap,), (after_step,))


def test_injection_gaps_count_this_batchs_injections_not_sequence_numbers():
    """Sequence numbers skipped by injections outside the batch do not widen a gap."""
    order = {"S1": 1, "S2": 2, "S3": 20, "S4": 21}
    owners = tuple(cid_nl_owner(sample, "band", apex_rt=10.0) for sample in order)
    (band,) = group_peak_bands(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injections=InjectionSequence(order)),
    )
    assert band.owners == owners


def test_between_nearly_equal_rt_steps_the_nearer_injection_links_first():
    """Each injection apart adds 0.005 min, so the adjacent injection wins here."""
    earlier = cid_nl_owner("S1", "a", apex_rt=10.0)
    member = cid_nl_owner("S3", "b", apex_rt=10.001)
    adjacent = cid_nl_owner("S4", "c", apex_rt=10.011)
    three_back = cid_nl_owner("S4", "d", apex_rt=9.9905)
    assert link_peak_bands(
        [
            (owner, owner.peak.rt_apex_min)
            for owner in (three_back, adjacent, member, earlier)
        ],
        injections=InjectionSequence({f"S{i}": i for i in range(1, 5)}),
    ) == ((earlier, member, adjacent), (three_back,))


def test_bootstrap_wider_rt_step_crosses_a_whole_injection_jump():
    first = cid_nl_owner("S1", "before", apex_rt=9.0)
    shifted = cid_nl_owner("S2", "shifted", apex_rt=8.5)
    order = {"S1": 1, "S2": 2}
    coordinates = [(first, 9.0), (shifted, 8.5)]
    assert link_peak_bands(coordinates, injections=InjectionSequence(order)) == (
        (first,),
        (shifted,),
    )
    assert link_peak_bands(
        coordinates,
        injections=InjectionSequence(order),
        max_rt_step_min=MAX_BOOTSTRAP_RT_STEP_MIN,
    ) == ((first, shifted),)


def test_ms1_can_bridge_a_band_but_cannot_establish_a_row():
    first = cid_nl_owner("S1", "a", apex_rt=10.0)
    middle = replace(cid_nl_owner("S2", "b", apex_rt=10.3), primary_identity_event=None)
    last = cid_nl_owner("S3", "c", apex_rt=10.6)
    frame = SampleRtFrame(injections=InjectionSequence({"S1": 1, "S2": 2, "S3": 3}))
    (band,) = group_peak_bands(
        (first, middle, last), config=AlignmentConfig(), rt_frame=frame
    )
    assert band.owners == (first, middle, last)
    assert band.destination == "matrix"
    (one_direct,) = group_peak_bands(
        (first, middle, replace(last, primary_identity_event=None)),
        config=AlignmentConfig(),
        rt_frame=frame,
    )
    assert one_direct.destination == "archive"


def test_shift_vote_counts_only_direct_peaks_and_requires_twenty_injections():
    order = {f"S{i:02}": i for i in range(1, 21)}
    owners = tuple(cid_nl_owner(sample, "direct", apex_rt=10.1) for sample in order)
    frame = SampleRtFrame(injections=InjectionSequence(order))
    assert band_shift_rows(owners, config=AlignmentConfig(), rt_frame=frame) == (
        dict.fromkeys(order, 10.1),
    )
    assert (
        band_shift_rows(
            (*owners[:-1], replace(owners[-1], primary_identity_event=None)),
            config=AlignmentConfig(),
            rt_frame=frame,
        )
        == ()
    )


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
        injections=InjectionSequence(order),
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
        injections=InjectionSequence(order),
        drift=drift,
        has_direct_support=lambda owner: owner.primary_identity_event is not None,
    )
    assert linked_without_support[0].members == without_support[:6]
    assert linked_without_support[0].coefficients.nonzero_count == 0


def test_final_rows_unite_seeds_that_one_fit_explains_but_vote_rows_do_not():
    """Ten injections before a batch-state step and ten after: neither piece
    spans any batch state to fit, so each seed alone leaves its own row. Their
    union's fit (0.6 min per unit) flattens the step into one row."""
    order = {f"s{i:02}": i for i in range(1, 21)}
    state = {stem: 0.0 if position <= 10 else 1.0 for stem, position in order.items()}
    owners = tuple(
        cid_nl_owner(stem, "x", apex_rt=10.0 + 0.6 * state[stem]) for stem in order
    )
    frame = SampleRtFrame(injections=InjectionSequence(order)).with_sample_calibration(
        BatchRtCalibration(
            policy=RtCalibrationPolicy(),
            knots=(),
            samples={
                stem: SampleRtCalibration(
                    stem, "calibrated", ((2.0, 2.0), (40.0, 40.0))
                )
                for stem in order
            },
        ),
        RtDriftModel(anchors=(DriftAnchor("anchor", 10.3, 0, state),)),
    )
    (row,) = group_peak_bands(owners, config=AlignmentConfig(), rt_frame=frame)
    assert row.owners == owners
    assert band_shift_rows(owners, config=AlignmentConfig(), rt_frame=frame) == ()
