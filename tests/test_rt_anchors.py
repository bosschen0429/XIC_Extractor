"""RT anchor observations and the calibration knots and table built from them."""

from __future__ import annotations

import csv
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.membership_fixtures import cid_nl_owner, subject_anchor
from xic_extractor.alignment.cid_nl_initial_grouping import CidNlBatchGrouping
from xic_extractor.alignment.rt_anchors import (
    reaches_ordinary_anchor_quorum,
    rt_anchor_observations,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibrationAccumulator,
)
from xic_extractor.output.alignment.rt_anchor_table import write_rt_anchor_table
from xic_extractor.peak_detection.cid_nl_method_subject import (
    mint_untargeted_cid_nl_method_subject,
)

DR = 116.0474


def _subject(precursor_mz: float):
    return mint_untargeted_cid_nl_method_subject(
        precursor_mz=precursor_mz, precursor_tolerance_ppm=10.0, neutral_loss_da=DR
    )


def _observation(sample, group, rt, *, precursor_mz=500.0):
    return subject_anchor(
        _subject(precursor_mz), sample, f"{group}-{sample}", rt, group
    )


def test_published_map_and_drift_are_measured_on_the_reference_population():
    samples = {f"s{i:02}": i for i in range(12)}
    frame = SampleRtFrame(
        injection_order=samples, sample_cohorts=dict.fromkeys(samples, "batch")
    )
    full, clean = CidNlBatchGrouping(frame), CidNlBatchGrouping(frame)
    for i, base in enumerate((4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0)):
        for pop, moves in ((clean, False), (full, True)):
            pop.add_anchor_observations(
                tuple(
                    _observation(
                        s,
                        f"g{i}",
                        base + ((n - 5.5) * 0.1 if moves else 0.0),
                        precursor_mz=400.0 + i,
                    )
                    for s, n in samples.items()
                )
            )
    movers = tuple(
        subject_anchor(_subject(m), s, f"{s}-{m}", base + (n - 5.5) * 0.1, None)
        for m, base in ((510.0, 10.0), (520.0, 22.0))
        for s, n in samples.items()
    )
    clean.add_anchor_observations(movers)
    full.add_anchor_observations(movers)
    full.freeze_calibration(reference=clean)
    fitted = full.frozen_rt_frame
    for s in ("s00", "s11"):
        at = 10.0 + (samples[s] - 5.5) * 0.1
        assert fitted.calibration_for_snapshot.samples[s].calibrated_rt(
            at
        ) == pytest.approx(at)
    assert {a.channel for a in fitted.drift_model.anchors} == {
        _subject(m).method_subject_id for m in (510.0, 520.0)
    }


def test_every_direct_quantifiable_owner_is_observed_and_only_bands_carry_groups():
    """The drift model reads every direct peak; calibration reads band members."""
    direct = SimpleNamespace(method_subject=_subject(500.0))
    a, b, c, d, unbanded = tuple(
        replace(cid_nl_owner(sample, "x"), assessment=direct)
        for sample in ("a", "b", "c", "d", "e")
    )
    scope = SimpleNamespace(
        groups=(
            SimpleNamespace(
                owners=(
                    a,
                    b,
                    replace(c, quantification_eligible=False),
                    replace(d, primary_identity_event=None),
                ),
                disposition="established",
            ),
            SimpleNamespace(owners=(unbanded,), disposition="provisional"),
            SimpleNamespace(
                owners=(cid_nl_owner("a", "y", apex_rt=12.0),),
                disposition="provisional",
            ),
        )
    )
    observations = rt_anchor_observations(scope)
    assert tuple(o.owner_id for o in observations) == (
        a.owner_id,
        b.owner_id,
        unbanded.owner_id,
    )
    assert observations[0].bootstrap_group_id is not None
    assert observations[0].bootstrap_group_id == observations[1].bootstrap_group_id
    assert observations[2].bootstrap_group_id is None
    assert observations[0].channel == direct.method_subject.method_subject_id


def _knots(observations, cohorts, measured_istds=()):
    accumulator = BatchRtCalibrationAccumulator(
        sample_stems=tuple(cohorts), sample_cohorts=cohorts
    )
    accumulator.add_observations(observations)
    return accumulator.freeze(measured_istds=measured_istds).knots


def test_ordinary_knot_records_cohort_and_its_observed_channel():
    cohorts = {"a": "old", "b": "old", "c": "old", "d": "new", "e": "new", "f": "new"}
    knots = _knots(
        [_observation(s, "G1", 10.0) for s in cohorts]
        + [_observation(s, "G2", 12.0) for s in "abc"],
        cohorts,
    )
    by_cohort = {(k.cohort, k.reference_rt_min): k for k in knots}
    assert set(by_cohort) == {("old", 10.0), ("new", 10.0), ("old", 12.0)}
    for knot in knots:
        assert knot.istd_label is None
        assert knot.channels == ((500.0, DR),)
        assert knot.sample_count == 3


def test_outside_an_agreed_group_an_observation_does_not_calibrate():
    cohorts = dict.fromkeys("abc", "")
    observations = [
        replace(_observation(s, "G1", 10.0), bootstrap_group_id=None) for s in cohorts
    ]
    assert _knots(observations, cohorts) == ()


_COHORTS = {
    **{f"old{i}": "old" for i in range(8)},
    **{f"new{i}": "new" for i in range(3)},
}


@pytest.mark.parametrize(
    "present",
    [
        ("old0", "old1", "old2"),
        ("old0", "old1", "old2", "old3"),
        ("old0", "old1", "new0", "new1"),
        ("new0", "new1", "new2"),
    ],
)
def test_quorum_says_whether_the_widest_agreement_could_calibrate(present):
    frame = SampleRtFrame(sample_cohorts=_COHORTS, sample_universe=frozenset(_COHORTS))
    widest = [_observation(sample, "G1", 10.0) for sample in present]
    assert reaches_ordinary_anchor_quorum(present, frame) is bool(
        _knots(widest, _COHORTS)
    )


def test_multi_channel_and_duplicate_pattern_knots_list_every_channel():
    cohorts = dict.fromkeys("abc", "")
    knots = _knots(
        [
            _observation("a", "G1", 10.0, precursor_mz=500.0),
            _observation("b", "G1", 10.0, precursor_mz=501.0),
            _observation("c", "G1", 10.0, precursor_mz=500.0),
        ]
        + [_observation(s, "G2", 10.0, precursor_mz=502.0) for s in cohorts],
        cohorts,
    )
    assert len(knots) == 1
    assert knots[0].channels == ((500.0, DR), (501.0, DR), (502.0, DR))


def test_istd_knot_carries_its_label_and_no_channel():
    cohorts = dict.fromkeys("abc", "batch")
    (knot,) = _knots(
        (), cohorts, measured_istds=[("d3-5-medC", s, 12.5) for s in cohorts]
    )
    assert (knot.istd_label, knot.cohort, knot.channels) == (
        "d3-5-medC",
        "batch",
        (),
    )


def test_anchor_table_projects_each_frozen_knot(tmp_path):
    cohorts = dict.fromkeys("abc", "")
    accumulator = BatchRtCalibrationAccumulator(
        sample_stems=tuple(cohorts), sample_cohorts=cohorts
    )
    accumulator.add_observations(
        [
            _observation("a", "G1", 10.0, precursor_mz=500.0),
            _observation("b", "G1", 10.123456789, precursor_mz=501.0),
            _observation("c", "G1", 10.5, precursor_mz=500.0),
        ]
    )
    calibration = accumulator.freeze(
        measured_istds=[("d3-5-medC", s, 12.5) for s in cohorts]
    )
    path = tmp_path / "rt_anchors.tsv"
    write_rt_anchor_table(path, calibration)
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    assert [(row["knot_id"], float(row["reference_rt_min"])) for row in rows] == [
        (knot.knot_id, knot.reference_rt_min) for knot in calibration.knots
    ]
    assert rows[0] | {"reference_rt_min": ""} == {
        "knot_id": "K001",
        "cohort": "",
        "kind": "ordinary",
        "istd_label": "",
        "precursor_mz": "500.0;501.0",
        "neutral_loss_da": f"{DR};{DR}",
        "reference_rt_min": "",
        "sample_count": "3",
    }
    assert (rows[1]["kind"], rows[1]["istd_label"], rows[1]["precursor_mz"]) == (
        "istd",
        "d3-5-medC",
        "",
    )

    write_rt_anchor_table(path, None)
    assert path.read_text(encoding="utf-8").splitlines() == [
        "knot_id\tcohort\tkind\tistd_label\tprecursor_mz\tneutral_loss_da"
        "\treference_rt_min\tsample_count"
    ]
