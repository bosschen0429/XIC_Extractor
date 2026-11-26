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


def replay(owners, drift=None, bindings=()):
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
                    "ch",
                    owners,
                    bindings,
                    None,
                    rt_calibration=calibration,
                    rt_drift=drift,
                ),
            ),
            {},
        )
    )
    return {s.sample: s for s in slots}, groups


def competing(group):
    return {
        d.owner_id
        for d in group.inference_decisions
        if d.accepted_risk and "accepted_competition" in d.accepted_risk.risk_flags
    }


def test_corrected_band_releases_direct_without_erasing_it_or_confirming_the_fill():
    """Far direct peaks keep their evidence; the shared localization fills slots."""
    residuals = [(i - 30) / 5000 for i in range(61)]
    ref = tuple(
        _owner(f"p{i}", "ref", apex_rt=10 + i / 10 + r) for i, r in enumerate(residuals)
    )
    early = _owner("fill", "direct", apex_rt=4)
    envelope, distractor = ms1("fill", 11.96), ms1("fill", 13)
    vacant = _owner("vacant", "direct", apex_rt=6)
    tie = (ms1("ambiguous", 11.96), ms1("ambiguous", 12.04))
    owners = (
        *ref,
        early,
        envelope,
        distractor,
        vacant,
        ms1("vacant", 15),
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
    for sample in ("fill", "vacant", "ambiguous"):
        assert slots[sample].selected is None
    (main,) = [g for g in groups if BAND in g.evidence]
    reasons = {d.owner_id: d.reason for d in main.inference_decisions}
    assert {k for k, r in reasons.items() if r == "direct_apex"} == {
        o.owner_id for o in ref
    }
    filled = {k for k, r in reasons.items() if r.startswith("localized_fill:")}
    chosen = filled - {envelope.owner_id}
    assert envelope.owner_id in filled
    assert len(chosen) == 1 and chosen <= {o.owner_id for o in tie}
    assert competing(main) == chosen
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
    assert slots["fill"].selected is None
    assert (slots["r1"].peers, slots["fill"].peers) == (20, 21)


def test_ms1_only_peak_fills_an_ordinary_direct_row_it_lacks():
    """Below the band quorum, a direct row is localized the same way."""
    ref = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(3))
    fill = ms1("fill", 10)
    _, groups = replay((*ref, fill))
    assert not any(BAND in g.evidence for g in groups)
    assert {frozenset(g.owner_ids) for g in groups} == {
        frozenset(o.owner_id for o in (*ref, fill))
    }


def test_known_identity_row_takes_no_ms1_fill():
    """A known identity is exact; a peak it never bound cannot join it."""
    ref = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    fill = ms1("fill", 10)
    _, groups = replay((*ref, fill), bindings=tuple((o.owner_id, "ISTD") for o in ref))
    assert {frozenset(g.owner_ids) for g in groups} == {
        frozenset(o.owner_id for o in ref),
        frozenset({fill.owner_id}),
    }


@pytest.mark.parametrize("direct", [False, True])
def test_peak_envelope_still_supports_the_row_when_nl_is_observed(direct):
    peers = tuple(_owner(f"r{i}", "ref", apex_rt=10) for i in range(21))
    peak = _owner("query", "peak", apex_rt=9.96)
    if not direct:
        peak = replace(peak, primary_identity_event=None)
    slots, groups = replay((*peers, peak))
    slot = slots["query"]
    (main,) = [g for g in groups if BAND in g.evidence]
    assert peak.owner_id in main.owner_ids
    assert (slot.center, slot.radius) == (10, 0)
    if direct:
        assert (slot.selected, slot.reason) == (peak.owner_id, "direct_envelope")
    else:
        assert slot.selected is None


def test_band_slot_takes_only_direct_peaks():
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
    slots, groups = replay((*peers, direct, apex))
    assert (slots["query"].selected, slots["query"].reason) == (
        direct.owner_id,
        "direct_envelope",
    )
    assert (apex.owner_id,) in [g.owner_ids for g in groups]


def test_peaks_without_method_evidence_cannot_split_a_direct_row():
    """Two direct peaks 60 s apart share a row on their own. Same-RT MS1-only
    peaks in each other's sample would make two RT bands cheaper, but final
    grouping never scores them."""
    direct = (_owner("s1", "direct", apex_rt=10), _owner("s2", "direct", apex_rt=11))
    unobserved = (ms1("s1", 11), ms1("s2", 10))
    direct_row = frozenset(o.owner_id for o in direct)

    def rows(owners):
        return {frozenset(g.owner_ids) for g in replay(owners)[1]}

    assert rows(direct) == {direct_row}
    population = (*direct, *unobserved)
    for owners in (population, population[::-1]):
        assert rows(owners) == {
            direct_row,
            *(frozenset({o.owner_id}) for o in unobserved),
        }


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
