from dataclasses import replace

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import _owner
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_experiment import GroupingInputs, GroupingWorkset
from xic_extractor.alignment.rt_drift import (
    DriftAnchor,
    DriftCoefficients,
    RtDriftModel,
)
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.diagnostics.cid_nl_trace_partition import regroup_channel


def ms1(s, rt):
    return replace(_owner(s, str(rt), apex_rt=rt), primary_identity_event=None)


def replay(owners, drift=None):
    samples = tuple(dict.fromkeys(o.sample_stem for o in owners))
    calibration = BatchRtCalibration.from_coordinate_knots(
        tuple((s, ((1.0, 1.0), (20.0, 20.0))) for s in samples)
    )
    w = GroupingWorkset(
        "ch", owners, (), None, rt_calibration=calibration, rt_drift=drift
    )
    slots, groups = regroup_channel(
        GroupingInputs(
            samples,
            dict(zip(samples, range(len(samples)), strict=True)),
            AlignmentConfig(),
            (w,),
            {},
        )
    )
    return {s.sample: s for s in slots}, groups


def test_corrected_band_releases_direct_without_erasing_it_or_confirming_the_fill():
    residuals = [(i - 30) / 5000 for i in range(61)]
    ref = tuple(
        _owner(f"p{i}", "ref", apex_rt=10 + i / 10 + r) for i, r in enumerate(residuals)
    )
    early = _owner("fill", "direct", apex_rt=4)
    envelope, distractor = ms1("fill", 11.96), ms1("fill", 13)
    vacant = _owner("vacant", "direct", apex_rt=6)
    unknown = _owner("unknown", "direct", apex_rt=4)
    tie = (ms1("ambiguous", 11.96), ms1("ambiguous", 12.04))
    owners = (
        *ref,
        early,
        envelope,
        distractor,
        vacant,
        ms1("vacant", 15),
        unknown,
        ms1("unknown", 12),
        *tie,
    )
    shape = {o.sample_stem: 1.0 for o in owners}
    shifts = {o.sample_stem: (i / 10 - 1) for i, o in enumerate(ref)}
    shifts.update(fill=1, vacant=1, ambiguous=1)
    drift = RtDriftModel(
        (DriftAnchor("ch", 10, 0, shape, DriftCoefficients(1, 1)),), shifts
    )
    slots, groups = replay(owners, drift)
    for i, o in enumerate(ref):
        peers = np.r_[np.delete(residuals, i), -8.0, -6.0]
        slot = slots[o.sample_stem]
        assert slot.center == pytest.approx(10 + np.median(peers))
        assert slot.radius == pytest.approx(
            np.quantile(peers, 0.95) - np.quantile(peers, 0.05)
        )
        assert slot.selected == o.owner_id
    assert slots["fill"].selected == envelope.owner_id
    assert slots["fill"].reason == "ms1_only_envelope"
    assert (
        slots["vacant"].selected is None
        and slots["vacant"].reason == "no_compatible_peak"
    )
    assert (
        slots["unknown"].selected is None
        and slots["unknown"].reason == "unknown_direct_coordinate"
    )
    assert (
        slots["ambiguous"].selected is None
        and slots["ambiguous"].reason == "competing_candidates"
    )
    (main,) = [g for g in groups if "corrected_cid_nl_reference_band" in g.evidence]
    assert set(main.owner_ids) == {envelope.owner_id, *(o.owner_id for o in ref)}
    assert sorted(k for g in groups for k in g.owner_ids) == sorted(
        o.owner_id for o in owners
    )


def test_ms1_fill_needs_a_recurring_direct_row_and_does_not_train_it():
    ref = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    fill = ms1("fill", 10)
    slots, _ = replay((*ref[:-1], fill))
    assert all(
        s.selected is None and s.reason == "insufficient_peers" for s in slots.values()
    )
    slots, _ = replay((*ref, fill))
    assert (
        slots["fill"].reason == "ms1_only_apex"
        and slots["fill"].selected == fill.owner_id
    )
    assert (slots["r1"].peers, slots["fill"].peers) == (20, 21)


@pytest.mark.parametrize("direct", [False, True])
def test_peak_envelope_still_supports_the_row_when_nl_is_observed(direct):
    peers = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    peak = _owner("query", "peak", apex_rt=9.96)
    if not direct:
        peak = replace(peak, primary_identity_event=None)
    slots, _ = replay((*peers, peak))
    slot = slots["query"]
    assert (slot.center, slot.radius, slot.selected) == (10, 0, peak.owner_id)
    assert slot.reason == ("direct_envelope" if direct else "ms1_only_envelope")


def test_competing_envelopes_use_nl_only_after_the_apex_options():
    peers = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    direct = _owner("query", "supported", apex_rt=9.96)
    other = _owner("query", "alternative", apex_rt=10.04)
    slots, _ = replay((*peers, direct, other))
    assert slots["query"].reason == "competing_candidates"
    assert slots["query"].selected is None
    slots, _ = replay((*peers, direct, replace(other, primary_identity_event=None)))
    assert (slots["query"].selected, slots["query"].reason) == (
        direct.owner_id,
        "direct_envelope",
    )
    apex = ms1("query", 10)
    slots, _ = replay((*peers, direct, apex))
    assert (slots["query"].selected, slots["query"].reason) == (
        apex.owner_id,
        "ms1_only_apex",
    )
