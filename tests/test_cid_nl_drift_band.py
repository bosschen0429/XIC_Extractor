from dataclasses import replace

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import _owner
from xic_extractor.alignment.cid_nl_drift_band import assign_drift_band
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_experiment import (
    GroupingInputs,
    GroupingWorkset,
    replay_grouping,
)
from xic_extractor.alignment.rt_drift import (
    DriftAnchor,
    DriftCoefficients,
    RtDriftModel,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration
from xic_extractor.mass_accuracy import within_ppm

BAND = "corrected_cid_nl_reference_band"


def ms1(s, rt):
    return replace(_owner(s, str(rt), apex_rt=rt), primary_identity_event=None)


def replay(owners, drift=None):
    """Each sample's band slot, and the formal grouping of the same workset."""
    samples = tuple(dict.fromkeys(o.sample_stem for o in owners))
    order = dict(zip(samples, range(len(samples)), strict=True))
    calibration = BatchRtCalibration.from_coordinate_knots(
        tuple((s, ((1.0, 1.0), (20.0, 20.0))) for s in samples)
    )
    frame = SampleRtFrame(
        injection_order=order, sample_universe=frozenset(samples)
    ).with_sample_calibration(calibration, drift)
    anchor = drift.anchors[0] if drift else None
    slots = assign_drift_band(owners, samples, frame, anchor)
    groups = replay_grouping(
        GroupingInputs(
            samples,
            order,
            AlignmentConfig(),
            (
                GroupingWorkset(
                    "ch", owners, (), None, rt_calibration=calibration, rt_drift=drift
                ),
            ),
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
    for sample in ("vacant", "unknown", "ambiguous"):
        assert slots[sample].selected is None
    (main,) = [g for g in groups if BAND in g.evidence]
    assert {d.owner_id: d.reason for d in main.inference_decisions} == {
        envelope.owner_id: "ms1_only_envelope",
        **{o.owner_id: "direct_apex" for o in ref},
    }
    assert sorted(k for g in groups for k in g.owner_ids) == sorted(
        o.owner_id for o in owners
    )


def test_ms1_fill_needs_a_recurring_direct_row_and_does_not_train_it():
    ref = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    fill = ms1("fill", 10)
    slots, groups = replay((*ref[:-1], fill))
    assert all(s.selected is None for s in slots.values())
    assert not any(BAND in g.evidence for g in groups)
    slots, groups = replay((*ref, fill))
    (main,) = [g for g in groups if BAND in g.evidence]
    assert set(main.owner_ids) == {fill.owner_id, *(o.owner_id for o in ref)}
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


def test_band_row_keeps_the_full_precursor_tolerance():
    """Both mass extremes sit near the median, but not near each other."""
    tolerance = AlignmentConfig().precursor_mz_tolerance_ppm
    offset = 0.8 * tolerance * 1e-6
    owners = tuple(
        _owner(f"r{i}", "ref", apex_rt=10, precursor_mz=500 * (1 + offset * (-1) ** i))
        for i in range(22)
    )
    _, groups = replay(owners)
    assert not any(BAND in g.evidence for g in groups)
    for group in groups:
        masses = [owner.peak.mz for owner in group.owners]
        assert within_ppm(max(masses), min(masses), tolerance)
    assert sorted(k for g in groups for k in g.owner_ids) == sorted(
        o.owner_id for o in owners
    )
