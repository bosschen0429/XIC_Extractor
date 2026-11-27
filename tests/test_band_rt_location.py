"""Backfill coordinates and empirical tolerances from a band's member apices."""

import pytest

from xic_extractor.alignment.band_rt_location import BandRtLocator
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.injection_timeline import InjectionSequence


@pytest.mark.parametrize("offset_scatter", [0.04, 2.0])
def test_paired_istd_localization_uses_only_confirmed_same_batch_offsets(
    offset_scatter,
    monkeypatch,
):
    def unused_trajectory_fit(*args, **kwargs):
        raise AssertionError("paired ISTD localization does not consume a trend fit")

    monkeypatch.setattr(BandRtLocator, "locate", unused_trajectory_fit)
    trajectory = BandRtLocator(
        (
            ("ref1", 10.0),
            ("ref2", 11.0 + offset_scatter),
            ("unconfirmed", 30.0),
            ("other_batch", 40.0),
        ),
    )
    results = trajectory.backfill_locations(
        ("missing", "other_missing"),
        reference_samples=frozenset({"ref1", "ref2", "other_batch"}),
        rt_frame=SampleRtFrame(
            injections=InjectionSequence({"ref1": 1, "ref2": 2, "missing": 3}),
            sample_cohorts=(
                {
                    "ref1": "batch",
                    "ref2": "batch",
                    "missing": "batch",
                    "other_missing": "batch",
                    "unconfirmed": "batch",
                    "other_batch": "different",
                }
            ),
        ),
        backfill_half_window_min=0.5,
        paired_istd_rts={
            "ref1": 8.0,
            "ref2": 9.0,
            "missing": 15.0,
            "other_missing": 18.0,
            "unconfirmed": 10.0,
            "other_batch": 10.0,
        },
    )
    assert list(results) == ["missing", "other_missing"]
    assert results["other_missing"].location.query_rt == pytest.approx(
        20.0 + offset_scatter / 2.0
    )
    result = results["missing"]
    assert result.location.basis == "paired_istd"
    assert result.location.query_rt == pytest.approx(17.0 + offset_scatter / 2.0)
    assert result.reliable is (offset_scatter < 0.5)
    assert result.apex_tolerance_sec == (12.0 if offset_scatter < 0.5 else None)


def test_backfill_localization_does_not_turn_row_scatter_into_unbounded_permission():
    references = (("s0", 10.0), ("s1", 13.0), ("s2", 10.0), ("s3", 13.0))
    orders = {"s0": 0, "s1": 2, "s2": 4, "s3": 6, "missing": 3}
    result = BandRtLocator(references).backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        rt_frame=SampleRtFrame(
            injections=InjectionSequence(orders or {}),
            sample_cohorts=({sample: "batch" for sample in orders}),
        ),
        backfill_half_window_min=1.0,
    )
    assert not result["missing"].reliable
    assert result["missing"].apex_tolerance_sec is None


@pytest.mark.parametrize("pattern_offset", [0.0, 1.0])
def test_forecast_requires_agreement_with_direct_reference_trajectory(pattern_offset):
    references = tuple((f"s{i}", 10.0 + i * 0.1) for i in range(1, 9))
    pattern = tuple(
        (f"s{i}", 10.0 + i * 0.1 + pattern_offset) for i in (10, 11, 13, 14)
    )
    trajectory = BandRtLocator(references + pattern)
    orders = {sample: int(sample[1:]) for sample, _ in references + pattern}
    orders["missing"] = 12
    kwargs = {
        "rt_frame": SampleRtFrame(
            injections=InjectionSequence(orders),
            sample_cohorts=dict.fromkeys(orders, "batch"),
        ),
        "backfill_half_window_min": 1.0,
    }
    prediction = trajectory.locate("missing", **kwargs)
    result = trajectory.backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        **kwargs,
    )["missing"]

    assert result.location == prediction
    assert result.reliable is (pattern_offset == 0.0)
    if pattern_offset == 0.0:
        assert result.location.query_rt == pytest.approx(11.2)


def test_sparse_reference_median_cannot_veto_a_supported_drift_forecast():
    references = (("s1", 10.1), ("s2", 10.2))
    pattern = tuple((f"s{i}", 10.0 + i * 0.1) for i in (10, 11, 13, 14))
    orders = {sample: int(sample[1:]) for sample, _ in references + pattern}
    orders["missing"] = 12
    result = BandRtLocator(references + pattern).backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        rt_frame=SampleRtFrame(
            injections=InjectionSequence(orders or {}),
            sample_cohorts=(dict.fromkeys(orders, "batch")),
        ),
        backfill_half_window_min=1.0,
    )["missing"]
    assert result.reliable
    assert result.location.query_rt == pytest.approx(11.2)


def test_observed_peak_keeps_its_location_without_borrowing_another_batch():
    trajectory = BandRtLocator((("a", 10.0), ("b", 10.1), ("observed", 15.0)))
    result = trajectory.backfill_locations(
        ("missing", "observed"),
        reference_samples=frozenset(("a", "b")),
        rt_frame=SampleRtFrame(
            injections=InjectionSequence({"a": 1, "b": 2, "observed": 3, "missing": 4}),
            sample_cohorts=({"a": "A", "b": "A", "observed": "B", "missing": "B"}),
        ),
        backfill_half_window_min=1.0,
    )
    assert not result["missing"].reliable
    assert result["observed"].location.observed_rt == 15.0
    assert result["observed"].apex_tolerance_sec == 12.0


def test_sparse_calibration_localizes_in_target_coordinates_without_strong_claim():
    from xic_extractor.alignment.sample_rt_calibration import (
        RtCalibrationPolicy,
        build_batch_rt_calibration,
    )

    calibration = build_batch_rt_calibration(
        {
            "early": {"a": 8, "b": 8, "target": 10},
            "late": {"a": 20, "b": 20, "target": 22},
        },
        sample_stems=("a", "b", "target"),
        policy=RtCalibrationPolicy(),
    )
    frame = SampleRtFrame(
        injections=InjectionSequence({"a": 1, "target": 2, "b": 3}),
        sample_cohorts=dict.fromkeys(("a", "b", "target"), "batch"),
    ).with_sample_calibration(calibration)
    prediction = BandRtLocator((("a", 10), ("b", 10))).locate(
        "target",
        rt_frame=frame,
        backfill_half_window_min=1.0,
    )
    assert prediction.query_rt == pytest.approx(12)
    assert prediction.basis == "estimated_trajectory_fit"
    assert prediction.observed_rt is None
    assert not frame.has_support("target")


def test_backfill_frame_scope_does_not_borrow_an_outside_population_reference():
    frame = SampleRtFrame(
        injections=InjectionSequence({"a": 1, "target": 2, "b": 3, "outside": 4}),
        sample_cohorts=dict.fromkeys(("a", "b", "target", "outside"), "batch"),
    ).for_sample_stems(frozenset({"a", "b", "target"}))
    trajectory = BandRtLocator((("a", 10.0), ("b", 12.0), ("outside", 30.0)))
    location = trajectory.locate("target", rt_frame=frame, backfill_half_window_min=1.0)
    assert location.query_rt == 11.0
    assert location.basis == "trajectory_fit"
    assert not location.observed
