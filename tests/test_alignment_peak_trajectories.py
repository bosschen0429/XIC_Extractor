"""Independent elution-order oracles, not chemical-identity labels."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import BatchRtWarp
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    PeakTrajectory,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)


@pytest.mark.parametrize("offset_scatter", [0.04, 2.0])
def test_paired_istd_localization_uses_only_confirmed_same_batch_offsets(
    offset_scatter,
    monkeypatch,
):
    def unused_trajectory_fit(*args, **kwargs):
        raise AssertionError("paired ISTD localization does not consume a trend fit")

    monkeypatch.setattr(PeakTrajectory, "locate", unused_trajectory_fit)
    trajectory = PeakTrajectory(
        (),
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
            injection_order=({"ref1": 1, "ref2": 2, "missing": 3}),
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


@pytest.mark.parametrize("half_window_min", [0.15, 1.0])
@pytest.mark.parametrize("with_ms1_positions", [False, True])
def test_backfill_location_prepares_held_out_residuals_once_per_group(
    monkeypatch,
    half_window_min,
    with_ms1_positions,
):
    references = tuple((f"s{i}", 10.0 + i * 0.1) for i in range(6))
    targets = tuple(f"t{i}" for i in range(20))
    orders = {sample: i for i, (sample, _) in enumerate(references)}
    orders.update({sample: i + 6 for i, sample in enumerate(targets)})
    positions = references
    if with_ms1_positions:
        positions += (("ms1_early", 9.8), ("ms1_late", 12.9))
        orders.update({"ms1_early": -2, "ms1_late": 29})
    cohorts = {sample: "batch" for sample in orders}
    trajectory = PeakTrajectory((), positions)
    calls = []
    original = PeakTrajectory.locate

    def counted(self, sample, **kwargs):
        calls.append(sample)
        return original(self, sample, **kwargs)

    monkeypatch.setattr(PeakTrajectory, "locate", counted)
    result = trajectory.backfill_locations(
        targets,
        reference_samples=frozenset(sample for sample, _ in references),
        rt_frame=SampleRtFrame(
            injection_order=(orders or {}), sample_cohorts=(cohorts or {})
        ),
        backfill_half_window_min=half_window_min,
    )
    model_count = 2 if with_ms1_positions else 1
    assert len(calls) == model_count * (len(references) + len(targets))
    assert result["t0"].location.query_rt == pytest.approx(10.6)
    assert all(
        location.apex_tolerance_sec == min(12.0, half_window_min * 60.0)
        for location in result.values()
    )


def test_backfill_localization_does_not_turn_row_scatter_into_unbounded_permission():
    references = (("s0", 10.0), ("s1", 13.0), ("s2", 10.0), ("s3", 13.0))
    orders = {"s0": 0, "s1": 2, "s2": 4, "s3": 6, "missing": 3}
    result = PeakTrajectory((), references).backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        rt_frame=SampleRtFrame(
            injection_order=(orders or {}),
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
    trajectory = PeakTrajectory((), references + pattern)
    orders = {sample: int(sample[1:]) for sample, _ in references + pattern}
    orders["missing"] = 12
    kwargs = {
        "rt_frame": SampleRtFrame(
            injection_order=orders, sample_cohorts=dict.fromkeys(orders, "batch")
        ),
        "backfill_half_window_min": 1.0,
    }
    prediction = trajectory.locate("missing", **kwargs)
    result = trajectory.backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        **kwargs,
    )["missing"]

    # A conflict withholds permission; it does not invent a replacement peak.
    assert result.location == prediction
    assert result.reliable is (pattern_offset == 0.0)
    if pattern_offset == 0.0:
        assert result.location.query_rt == pytest.approx(11.2)


def test_sparse_reference_median_cannot_veto_a_supported_drift_forecast():
    references = (("s1", 10.1), ("s2", 10.2))
    pattern = tuple((f"s{i}", 10.0 + i * 0.1) for i in (10, 11, 13, 14))
    orders = {sample: int(sample[1:]) for sample, _ in references + pattern}
    orders["missing"] = 12
    result = PeakTrajectory((), references + pattern).backfill_locations(
        ("missing",),
        reference_samples=frozenset(sample for sample, _ in references),
        rt_frame=SampleRtFrame(
            injection_order=(orders or {}),
            sample_cohorts=(dict.fromkeys(orders, "batch")),
        ),
        backfill_half_window_min=1.0,
    )["missing"]
    assert result.reliable
    assert result.location.query_rt == pytest.approx(11.2)


def test_observed_peak_keeps_its_location_without_borrowing_another_batch():
    trajectory = PeakTrajectory((), (("a", 10.0), ("b", 10.1), ("observed", 15.0)))
    result = trajectory.backfill_locations(
        ("missing", "observed"),
        reference_samples=frozenset(("a", "b")),
        rt_frame=SampleRtFrame(
            injection_order=({"a": 1, "b": 2, "observed": 3, "missing": 4}),
            sample_cohorts=({"a": "A", "b": "A", "observed": "B", "missing": "B"}),
        ),
        backfill_half_window_min=1.0,
    )
    assert not result["missing"].reliable
    assert result["observed"].location.observed_rt == 15.0
    assert result["observed"].apex_tolerance_sec == 12.0


def _owner(sample: str, label: str, rt: float) -> SampleLocalMS1Owner:
    return SampleLocalMS1Owner(
        owner_id=f"{sample}:{label}",
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        precursor_mz=500.0,
        owner_apex_rt=rt,
        owner_peak_start_rt=rt - 0.1,
        owner_peak_end_rt=rt + 0.1,
        owner_area=1000.0,
        owner_height=100.0,
        primary_identity_event=None,
        supporting_events=(),
        assignment_reason="same_apex_window",
    )


def _context(
    sample: str, peaks: tuple[tuple[float, float], ...]
) -> SampleElutionContext:
    return SampleElutionContext(
        sample_stem=sample,
        mz=500.0,
        rt_start=8.0,
        rt_end=15.0,
        landmarks=tuple(ElutionLandmark(rt, height, 0.25) for rt, height in peaks),
    )


@pytest.mark.parametrize("right_domain_end", (9.0, 11.0))
def test_pattern_outside_calibration_span_matches_wholly_in_observed_rt(
    right_domain_end,
):
    from xic_extractor.alignment.sample_rt_calibration import BatchRtCalibration

    owners = (
        _owner("a", "early", 10.0),
        _owner("a", "late", 10.7),
        _owner("b", "early", 10.6),
        _owner("b", "late", 11.3),
    )
    contexts = (
        _context("a", ((10.0, 30.0), (10.7, 100.0))),
        _context("b", ((10.6, 30.0), (11.3, 100.0))),
    )
    frame = SampleRtFrame(injection_order={"a": 1, "b": 2}).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            (
                ("a", ((8.0, 8.0), (12.0, 12.0))),
                ("b", ((8.0, 8.5), (right_domain_end, right_domain_end + 0.5))),
            )
        )
    )
    result = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order=frame.injection_order,
        rt_frame=frame,
    )
    assert {frozenset(track.owner_ids) for track in result.tracks} == {
        frozenset(("a:early", "b:early")),
        frozenset(("a:late", "b:late")),
    }
    assert result.trajectory_for(owners[:1]).sample_apices == (("a", 10.0), ("b", 10.6))


@pytest.mark.parametrize("reverse", [False, True])
def test_single_landmark_cannot_join_disconnected_ordered_patterns(reverse):
    patterns = {
        "a": ((10.0, 30.0), (10.3, 100.0)),
        "b": ((10.0, 30.0), (10.3, 100.0)),
        "c": ((10.4, 100.0), (11.2, 30.0)),
        "d": ((10.4, 100.0), (11.2, 30.0)),
        "x": ((10.35, 100.0),),
    }
    owners = [
        _owner(sample, str(index), rt)
        for sample, peaks in patterns.items()
        for index, (rt, _) in enumerate(peaks)
    ]
    contexts = [
        replace(
            _context(sample, peaks),
            landmarks=tuple(ElutionLandmark(rt, height, 0.05) for rt, height in peaks),
        )
        for sample, peaks in patterns.items()
    ]
    result = resolve_peak_trajectories(
        owners[::-1] if reverse else owners,
        contexts[::-1] if reverse else contexts,
        config=AlignmentConfig(),
        injection_order={sample: index for index, sample in enumerate(patterns)},
    )
    assert {frozenset(track.owner_ids) for track in result.tracks} == {
        frozenset(("a:0", "b:0")),
        frozenset(("a:1", "b:1")),
        frozenset(("c:0", "d:0")),
        frozenset(("c:1", "d:1")),
    }
    assert result.trajectory_for([owners[1], owners[3]]).sample_apices == (
        ("a", 10.3),
        ("b", 10.3),
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("tied", [True, False])
def test_equal_pattern_paths_do_not_authorize_observed_backfill_location(reverse, tied):
    owners = [_owner(sample, "late", 10.75) for sample in ("a", "b")]
    contexts = [
        _context("a", ((10.0, 50.0), (10.75, 100.0))),
        _context("b", ((10.0, 50.0), (10.75, 100.0))),
        _context("c", ((10.0, 50.0), (10.5, 100.0), (11.0, 100.0 if tied else 50.0))),
    ]
    contexts = [
        replace(
            context,
            landmarks=tuple(
                replace(peak, width=0.1 if peak.apex_rt == 10.0 else 0.375)
                for peak in context.landmarks
            ),
        )
        for context in contexts
    ]
    if reverse:
        owners.reverse()
        contexts.reverse()
    orders = {"a": 1, "b": 2, "c": 3}
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    location = (
        model.trajectory_for(owners)
        .backfill_locations(
            ("c",),
            reference_samples=frozenset(("a", "b")),
            rt_frame=SampleRtFrame(
                injection_order=(orders or {}),
                sample_cohorts=(dict.fromkeys(orders, "batch")),
            ),
            backfill_half_window_min=1.0,
        )["c"]
        .location
    )
    # Both ordered two-peak paths have equal height error and a best normalized
    # RT residual of 2/3. Neither may become an observed Backfill coordinate;
    # choosing 11.0 would exclude the equally supported 10.5 from its 12s gate.
    assert location.observed_rt == (None if tied else 10.5)


def test_missing_position_uses_ms1_trajectory_points_not_another_rt_answer():
    owners = [_owner("s0", "early", 10.0), _owner("s2", "early", 10.4)]
    contexts = [
        _context(f"s{i}", ((10.0 + 0.2 * i, 30.0), (10.6 + 0.2 * i, 100.0)))
        for i in range(3)
    ]
    orders = {f"s{i}": i for i in range(4)}
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    trajectory = model.trajectory_for(owners)
    location = trajectory.locate(
        "s3",
        rt_frame=SampleRtFrame(
            injection_order=(orders or {}),
            sample_cohorts=(dict.fromkeys(orders, "batch")),
        ),
        backfill_half_window_min=0.5,
    )
    # Two supported owners alone have median 10.2. The MS1-only point in s1
    # completes the straight trajectory 10 + 0.2 * injection order.
    assert location.query_rt == pytest.approx(10.6)
    assert location.observed_rt is None


@pytest.mark.parametrize("reverse", [False, True])
def test_order_survives_height_reversal_and_overlapping_absolute_rts(
    reverse: bool,
) -> None:
    contexts = [
        _context("a", ((10.0, 10.0), (10.6, 100.0))),
        _context("b", ((10.1, 100.0), (10.7, 10.0))),
        _context("c", ((10.6, 40.0), (11.2, 100.0))),
    ]
    owners = [
        _owner("a", "early", 10.0),
        _owner("a", "late", 10.6),
        _owner("b", "early", 10.1),
        _owner("b", "late", 10.7),
        _owner("c", "early", 10.6),
        _owner("c", "late", 11.2),
    ]
    if reverse:
        contexts.reverse()
        owners.reverse()
    result = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "c": 20},
    )
    assert {track.owner_ids for track in result.tracks} == {
        ("a:early", "b:early", "c:early"),
        ("a:late", "b:late", "c:late"),
    }


@pytest.mark.parametrize("second_sample_supported", [False, True])
def test_pattern_calibration_requires_support_for_both_samples(second_sample_supported):
    owners = [_owner("a", "early", 10.0), _owner("b", "early", 11.6)]
    contexts = [
        _context("a", ((10.0, 30.0), (10.6, 100.0))),
        _context("b", ((11.6, 30.0), (12.2, 100.0))),
    ]
    knots = [("a", ((8.0, 0.0), (15.0, 0.0)))]
    if second_sample_supported:
        knots.append(("b", ((8.0, 0.0), (15.0, 0.0))))
    frame = SampleRtFrame(injection_order={"a": 1, "b": 2}).with_warp(
        BatchRtWarp(tuple(knots), 12)
    )
    result = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order=frame.injection_order,
        rt_frame=frame,
    )
    if second_sample_supported:
        # A measured zero correction is incompatible with a 1.6-minute pattern
        # displacement. It cannot authorize an observed cross-sample location.
        assert result.tracks == ()
    else:
        # An absent correction is not measured zero. The uncalibrated doublet
        # still supplies its rigid displacement, without rewriting observations.
        assert [(track.owner_ids, track.sample_apices) for track in result.tracks] == [
            (("a:early", "b:early"), (("a", 10.0), ("b", 11.6)))
        ]


@pytest.mark.parametrize("ms1_only", [False, True])
def test_calibrated_candidates_survive_large_raw_displacement(ms1_only):
    owners = [_owner("a", "early", 10.0)]
    if not ms1_only:
        owners.append(_owner("b", "early", 14.0))
    contexts = [
        _context("a", ((10.0, 30.0), (10.6, 100.0))),
        _context("b", ((14.0, 30.0), (14.6, 100.0))),
    ]
    frame = SampleRtFrame(injection_order={"a": 1, "b": 2}).with_warp(
        BatchRtWarp(
            (
                ("a", ((8.0, 0.0), (16.0, 0.0))),
                ("b", ((8.0, 4.0), (16.0, 4.0))),
            ),
            12,
        )
    )
    raw = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order=frame.injection_order,
    )
    result = resolve_peak_trajectories(
        owners,
        raw.source_contexts,
        config=AlignmentConfig(),
        injection_order=frame.injection_order,
        rt_frame=frame,
    )
    assert result.pattern_comparisons == 1
    assert len(result.tracks) == 1
    assert result.tracks[0].owner_ids == tuple(o.owner_id for o in owners)
    assert result.tracks[0].sample_apices == (("a", 10.0), ("b", 14.0))
    assert result.source_contexts == tuple(contexts)


def test_missing_and_extra_neighbors_provide_locations_not_new_owners() -> None:
    contexts = [
        _context("a", ((10.0, 20.0), (10.7, 100.0), (11.3, 60.0))),
        _context("b", ((10.8, 25.0), (11.2, 100.0), (12.0, 60.0))),
        _context("c", ((10.9, 30.0), (11.3, 100.0), (12.1, 65.0), (12.8, 7.0))),
        _context("d", ((11.4, 100.0), (12.2, 60.0))),
    ]
    owners = [
        _owner("a", "late", 11.3),
        _owner("c", "late", 12.1),
        _owner("d", "central", 11.4),
    ]
    result = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 15, "c": 16, "d": 17},
    )
    assert {track.owner_ids for track in result.tracks} == {
        ("a:late", "c:late"),
        ("d:central",),
    }
    assert dict(result.trajectory_for(owners[:2]).sample_apices).get("b") == 12.0
    assert dict(result.trajectory_for(owners[:2]).sample_apices).get("d") == 12.2
    assert dict(result.trajectory_for(owners[2:]).sample_apices).get("a") == 10.7
    assert (
        dict(result.trajectory_for([owners[0], owners[2]]).sample_apices).get("c")
        is None
    )


def test_single_peak_continuation_cannot_overrule_an_ordered_pair() -> None:
    contexts = [
        _context("a", ((10.0, 10.0), (10.6, 100.0))),
        _context("b", ((10.05, 100.0), (10.65, 60.0))),
        _context("c", ((10.7, 100.0),)),
        _context("d", ((10.65, 50.0), (11.25, 100.0))),
    ]
    owners = [
        _owner("a", "early", 10.0),
        _owner("b", "early", 10.05),
        _owner("c", "late", 10.7),
        _owner("d", "early", 10.65),
    ]
    result = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "c": 3, "d": 30},
    )
    # c's lone peak is nearest to d's early peak, but it cannot join or
    # locate the ordered early track. Nor is its owner bound to any track.
    (early,) = result.tracks
    assert early.owner_ids == ("a:early", "b:early", "d:early")
    assert dict(early.sample_apices) == {"a": 10.0, "b": 10.05, "d": 10.65}
    assert result.trajectory_for([owners[2]]).sample_apices == (("c", 10.7),)


@pytest.mark.parametrize(
    ("isolated_rt", "label", "pattern_rts"),
    [
        (11.8, "late", (10.7, 10.75)),
        (12.4, "late", (10.7, 10.75)),
        (9.0, "early", (10.0, 10.05)),
    ],
)
def test_single_peak_continuation_is_not_an_observed_position(
    isolated_rt: float, label: str, pattern_rts: tuple[float, float]
) -> None:
    # x has one peak with no second landmark to witness which ordered peak it
    # is. Its nearest-RT link may continue membership, but it is not an
    # observation of this trajectory and cannot become the Backfill query.
    contexts = [
        _context("a", ((10.0, 60.0), (10.7, 100.0))),
        _context("b", ((10.05, 60.0), (10.75, 100.0))),
        _context("x", ((isolated_rt, 100.0),)),
    ]
    owners = [
        _owner("a", "early", 10.0),
        _owner("a", "late", 10.7),
        _owner("b", "early", 10.05),
        _owner("b", "late", 10.75),
    ]
    orders = {"a": 1, "b": 2, "x": 3}
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    members = [owner for owner in owners if owner.owner_id.endswith(label)]
    location = model.trajectory_for(members).locate(
        "x",
        rt_frame=SampleRtFrame(
            injection_order=(orders or {}),
            sample_cohorts=(dict.fromkeys(orders, "batch")),
        ),
        backfill_half_window_min=0.5,
    )
    assert location.observed_rt is None
    # Two pattern observations leave no slope to fit; their median is used.
    assert location.query_rt == pytest.approx(sum(pattern_rts) / 2.0)


def test_tracks_never_observed_together_share_their_positions() -> None:
    # Two seven-injection blocks see the same doublet but are never compared.
    # A group that joins their owners keeps the MS1-only positions of both.
    orders = {f"s{order:02d}": order for order in (*range(1, 8), *range(21, 28))}
    contexts = [_context(sample, ((10.0, 100.0), (10.6, 60.0))) for sample in orders]
    owners = [
        _owner(sample, "peak", 10.0)
        for sample in orders
        if sample not in {"s04", "s24"}
    ]
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    assert len(model.tracks) == 2
    assert model.permits_membership(owner.owner_id for owner in owners)
    assert dict(model.trajectory_for(owners).sample_apices) == dict.fromkeys(
        orders, 10.0
    )


def test_repeated_indistinguishable_pattern_does_not_invent_an_ordinal() -> None:
    contexts = [
        _context("a", ((10.0, 100.0), (10.6, 100.0), (11.2, 100.0))),
        _context("b", ((10.1, 100.0), (10.7, 100.0))),
    ]
    result = resolve_peak_trajectories(
        [_owner("a", "unknown", 10.6), _owner("b", "unknown", 10.1)],
        contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2},
    )
    assert result.tracks == ()


def test_isolated_peaks_and_other_mass_have_no_pattern_authority() -> None:
    owners = [_owner("a", "only", 10.0), _owner("b", "only", 10.1)]
    contexts = [_context("a", ((10.0, 100.0),)), _context("b", ((10.1, 100.0),))]
    result = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order={"a": 1, "b": 2}
    )
    assert result.tracks == ()
    unrelated = [replace(context, mz=501.0) for context in contexts]
    assert (
        resolve_peak_trajectories(
            owners, unrelated, config=AlignmentConfig(), injection_order={}
        ).tracks
        == ()
    )


@pytest.mark.parametrize("owner_offset_ppm", [-20.0, 20.0, 20.01])
def test_mass_neighborhoods_keep_boundary_owners_and_ms1_only_positions(
    owner_offset_ppm: float,
) -> None:
    owners = []
    contexts = []
    expected = {}
    for mz in (400.0, 500.0, 600.0):
        for sample, shift in (("a", 0.0), ("b", 0.1), ("c", 0.2)):
            contexts.append(
                replace(
                    _context(sample, ((10.0 + shift, 20.0), (10.6 + shift, 100.0))),
                    mz=mz,
                )
            )
            if sample != "c":
                owners.extend(
                    replace(
                        _owner(sample, f"{mz}:{label}", rt + shift),
                        precursor_mz=mz * (1.0 + owner_offset_ppm * 1e-6),
                    )
                    for label, rt in (("early", 10.0), ("late", 10.6))
                )
        for label, rt in (("early", 10.0), ("late", 10.6)):
            expected[(f"a:{mz}:{label}", f"b:{mz}:{label}")] = (
                ("a", rt),
                ("b", rt + 0.1),
                ("c", rt + 0.2),
            )
    result = resolve_peak_trajectories(
        owners[::-1],
        contexts[::-1],
        config=AlignmentConfig(preferred_ppm=20.0),
        injection_order={"a": 1, "b": 2, "c": 3},
    )
    # Ppm uses each context's reference mass. The inclusive boundary binds;
    # just outside it, no owner can grant pattern authority to the MS1 points.
    assert {track.owner_ids: track.sample_apices for track in result.tracks} == (
        expected if abs(owner_offset_ppm) <= 20.0 else {}
    )


def test_landmarks_exclude_clipped_edge_and_keep_interior_maxima() -> None:
    rt = np.linspace(0.0, 3.0, 601)
    raw = sum(
        height * np.exp(-0.5 * ((rt - apex) / 0.06) ** 2)
        for apex, height in [(0.01, 1000.0), (1.0, 300.0), (1.6, 700.0)]
    )
    prepared = prepare_physical_signal(rt, raw)
    before = prepared.prepared
    context = project_elution_context(prepared, sample_stem="sample", mz=500.0)
    assert context is not None
    assert [landmark.apex_rt for landmark in context.landmarks] == pytest.approx(
        [1.0, 1.6], abs=0.01
    )
    assert all(landmark.width > 0.0 for landmark in context.landmarks)
    assert prepared.prepared == before


@pytest.mark.parametrize("shared_lobe", [True, False])
@pytest.mark.parametrize("reverse", [False, True])
def test_membership_exclusion_requires_distinct_physical_segments(shared_lobe, reverse):
    rt = np.linspace(8.0, 12.0, 401)
    raw = 1000.0 * np.exp(-0.5 * ((rt - 9.5) / (0.3 if shared_lobe else 0.08)) ** 2)
    raw += 500.0 * np.exp(-0.5 * ((rt - 9.85) / 0.08) ** 2)
    prepared = prepare_physical_signal(rt, raw)
    contexts = [
        project_elution_context(prepared, sample_stem=sample, mz=500.0)
        for sample in ("a", "b", "ms1_only")
    ]
    assert all(
        context is not None and len(context.landmarks) == 2 for context in contexts
    )
    owners = [
        _owner("a", "early", contexts[0].landmarks[0].apex_rt),
        _owner("b", "late", contexts[1].landmarks[1].apex_rt),
    ]
    if shared_lobe:
        owners = [
            replace(owner, owner_peak_start_rt=8.58, owner_peak_end_rt=10.45)
            for owner in owners
        ]
    unrelated_signal = sum(
        1000.0 * np.exp(-0.5 * ((rt - peak.apex_rt) / 0.035) ** 2)
        for peak in contexts[0].landmarks
    )
    unrelated = prepare_physical_signal(rt, unrelated_signal)
    # A production partition also carries other ions' contexts on these same
    # native scan coordinates. Their resolved doublets are not this ion's proof.
    contexts.extend(
        project_elution_context(unrelated, sample_stem=sample, mz=700.0)
        for sample in ("a", "b", "ms1_only")
    )
    model = resolve_peak_trajectories(
        owners[::-1] if reverse else owners,
        contexts[::-1] if reverse else contexts,
        config=AlignmentConfig(),
        injection_order={"a": 1, "b": 2, "ms1_only": 3},
    )
    assert len(model.tracks) == 2
    # Correspondence can retain two useful landmarks within one lobe. Only
    # the physical segment owner can make their tracks mutually exclusive.
    assert model.permits_membership(owner.owner_id for owner in owners) is shared_lobe
    if shared_lobe:
        # Allowing membership does not pick an arbitrary shoulder as the
        # missing sample's observed Backfill position. Direct apices survive.
        assert dict(model.trajectory_for(owners).sample_apices) == {
            owner.sample_stem: owner.owner_apex_rt for owner in owners
        }


def test_backfill_rt_uses_trajectory_fit_and_target_sample_coordinates() -> None:
    warp = BatchRtWarp(
        (
            ("a", ((7.0, 0.1), (20.0, 0.1))),
            ("b", ((7.0, -0.1), (20.0, -0.1))),
            ("target", ((7.0, 0.3), (20.0, 0.3))),
        ),
        12,
    )
    members = (_owner("a", "f", rt=10.1), _owner("b", "f", rt=9.9))
    prediction = (
        PeakTrajectories()
        .trajectory_for(members)
        .locate(
            backfill_half_window_min=1.0,
            sample_stem="target",
            rt_frame=SampleRtFrame(
                injection_order=({"a": 1, "b": 3, "target": 2}),
                sample_cohorts=({"a": "batch", "b": "batch", "target": "batch"}),
            ).with_warp(warp),
        )
    )
    assert prediction.query_rt == pytest.approx(10.3)
    assert prediction.basis == "calibrated_trajectory_fit"
    unsupported = (
        PeakTrajectories()
        .trajectory_for(members)
        .locate(
            backfill_half_window_min=1.0,
            sample_stem="other",
            rt_frame=SampleRtFrame(
                injection_order=({"a": 1, "b": 3, "other": 2}),
                sample_cohorts=({"a": "batch", "b": "batch", "other": "different"}),
            ).with_warp(warp),
        )
    )
    assert unsupported.query_rt == 10.0
    assert unsupported.basis == "trajectory_reference"


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
        prior_injections={},
        policy=RtCalibrationPolicy(),
    )
    frame = SampleRtFrame(
        injection_order={"a": 1, "target": 2, "b": 3},
        sample_cohorts=dict.fromkeys(("a", "b", "target"), "batch"),
    ).with_sample_calibration(calibration)
    prediction = PeakTrajectory((), (("a", 10), ("b", 10))).locate(
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
        injection_order={"a": 1, "target": 2, "b": 3, "outside": 4},
        sample_cohorts=dict.fromkeys(("a", "b", "target", "outside"), "batch"),
    ).for_sample_stems(frozenset({"a", "b", "target"}))
    trajectory = PeakTrajectory((), (("a", 10.0), ("b", 12.0), ("outside", 30.0)))
    location = trajectory.locate("target", rt_frame=frame, backfill_half_window_min=1.0)
    # Only a and b belong to the original consumer population: the existing
    # two-reference median is 11, irrespective of the outside peak's RT.
    assert location.query_rt == 11.0
    assert location.basis == "trajectory_fit"
    assert not location.observed


@pytest.mark.parametrize("target_order", [12, 40])
def test_backfill_rt_follows_steep_local_drift_beyond_the_neighbors(
    target_order: int,
) -> None:
    samples = [f"s{order}" for order in range(1, 11)]
    members = tuple(
        _owner(sample, "f", rt=10.0 + 0.1 * order)
        for order, sample in enumerate(samples, 1)
    )
    orders = {sample: order for order, sample in enumerate(samples, 1)} | {
        "target": target_order
    }
    cohorts = dict.fromkeys(orders, "batch")
    flat = BatchRtWarp(
        tuple((sample, ((7.0, 0.0), (20.0, 0.0))) for sample in samples)
        + (("target", ((7.0, 0.2), (20.0, 0.2))),),
        12,
    )
    calibrated = (
        PeakTrajectories()
        .trajectory_for(members)
        .locate(
            backfill_half_window_min=1.0,
            sample_stem="target",
            rt_frame=SampleRtFrame(
                injection_order=(orders or {}), sample_cohorts=(cohorts or {})
            ).with_warp(flat),
        )
    )
    uncalibrated = (
        PeakTrajectories()
        .trajectory_for(members)
        .locate(
            backfill_half_window_min=1.0,
            sample_stem="target",
            rt_frame=SampleRtFrame(
                injection_order=(orders or {}), sample_cohorts=(cohorts or {})
            ),
        )
    )
    assert calibrated.query_rt == pytest.approx(10.2 + 0.1 * target_order)
    assert calibrated.basis == "calibrated_trajectory_fit"
    assert uncalibrated.query_rt == pytest.approx(10.0 + 0.1 * target_order)
    assert uncalibrated.basis == "trajectory_fit"


@pytest.mark.parametrize("calibrated", [False, True])
def test_backfill_noisy_extrapolation_stays_at_supported_boundary(
    calibrated: bool,
) -> None:
    orders = {f"s{i}": i for i in range(10, 22)} | {"early": 0, "late": 31}
    members = tuple(
        _owner(
            f"s{i}",
            "f",
            rt=10.0 + 0.1 * (i - 10) + (-0.6 if i % 2 == 0 else 0.6),
        )
        for i in range(10, 22)
    )
    offsets = dict.fromkeys(orders, 0.0) | {"early": 0.3, "late": -0.2}
    warp = (
        BatchRtWarp(tuple((s, ((7.0, v), (20.0, v))) for s, v in offsets.items()), 12)
        if calibrated
        else None
    )
    for sample, bounds in [("early", (9.4, 10.55)), ("late", (10.55, 11.7))]:
        prediction = (
            PeakTrajectories()
            .trajectory_for(members)
            .locate(
                backfill_half_window_min=1.0,
                sample_stem=sample,
                rt_frame=SampleRtFrame(
                    injection_order=(orders or {}),
                    sample_cohorts=(dict.fromkeys(orders, "batch")),
                ).with_warp(warp),
            )
        )
        offset = offsets[sample] if calibrated else 0.0
        assert bounds[0] + offset <= prediction.query_rt <= bounds[1] + offset
        assert prediction.basis == (
            "calibrated_trajectory_fit_at_support_boundary"
            if calibrated
            else "trajectory_fit_at_support_boundary"
        )
        wide_search = (
            PeakTrajectories()
            .trajectory_for(members)
            .locate(
                sample_stem=sample,
                rt_frame=SampleRtFrame(
                    injection_order=(orders or {}),
                    sample_cohorts=(dict.fromkeys(orders, "batch")),
                ).with_warp(warp),
                backfill_half_window_min=10.0,
            )
        )
        assert "support_boundary" not in wide_search.basis
        assert not bounds[0] + offset <= wide_search.query_rt <= bounds[1] + offset


def _direct_conflict_case(second_owner_rt, *, independent_branch=False):
    # Same-sample early/late evidence from the reduced 659.346 case. Cross-sample
    # shifts of 160-175 s are deliberately valid, not a distinct-peak oracle.
    owners = [
        replace(
            _owner("BC1095", "reference", 32.988294154916666),
            precursor_mz=659.3459114397047,
        ),
        replace(
            _owner("BC1165", "reference", second_owner_rt),
            precursor_mz=659.346488450235,
        ),
    ]
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=mz,
            rt_start=start,
            rt_end=end,
            landmarks=tuple(ElutionLandmark(*peak) for peak in peaks),
        )
        for sample, mz, start, end, peaks in (
            (
                "BC1095",
                659.3459114397047,
                31.5,
                33.5,
                (
                    (32.03510181145, 29381.2, 0.500014),
                    (32.988294154916666, 198375.1, 0.412684),
                ),
            ),
            (
                "BC1165",
                659.346488450235,
                32.5,
                33.6,
                ((33.103192714116666, 199026.6, 0.307684),),
            ),
            (
                "BC1165",
                659.3398,
                34.3,
                36.0,
                (
                    (34.55277475815, 299779.3, 0.264601),
                    (35.09108621948334, 68458.4, 0.240920),
                    (35.79459419948333, 165240.8, 0.274382),
                ),
            ),
            (
                "BC1116",
                659.3397,
                34.7,
                36.0,
                ((34.95264327655, 81293.8, 0.202269), (35.7000614, 201354.1, 0.269027)),
            ),
        )
    ]
    orders = {"BC1095": 99, "BC1116": 105, "BC1165": 109}
    if independent_branch:
        contexts.append(
            SampleElutionContext(
                sample_stem="independent",
                mz=659.3459114397047,
                rt_start=28.5,
                rt_end=30.6,
                landmarks=(
                    ElutionLandmark(32.03510181145 - 2.9, 29381.2, 0.500014),
                    ElutionLandmark(32.988294154916666 - 2.9, 198375.1, 0.412684),
                ),
            )
        )
        orders["independent"] = 98
    return owners, contexts, orders


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("independent_branch", [False, True])
def test_direct_conflict_withdraws_only_dependent_locations(
    reverse, independent_branch
):
    owners, contexts, orders = _direct_conflict_case(
        33.103192714116666, independent_branch=independent_branch
    )
    if reverse:
        owners.reverse()
        contexts.reverse()
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    original_tracks = model.tracks
    assert model.permits_membership(owner.owner_id for owner in owners)
    # The pattern itself still has the late points: localization cannot rewrite
    # matching, membership, or the selected direct observations.
    assert any(
        dict(track.sample_apices).get("BC1165") == 35.79459419948333
        for track in original_tracks
    )
    trajectory = model.trajectory_for(owners)
    expected = {owner.sample_stem: owner.owner_apex_rt for owner in owners}
    if independent_branch:
        expected["independent"] = 32.988294154916666 - 2.9
    assert dict(trajectory.sample_apices) == pytest.approx(expected)
    assert model.tracks == original_tracks
    # Forecasts must consume the filtered points, not just relabel a bad point.
    frame = SampleRtFrame(injection_order=orders)
    locations = trajectory.backfill_locations(
        ("BC1116", "independent"),
        reference_samples=frozenset(owner.sample_stem for owner in owners),
        rt_frame=frame,
        backfill_half_window_min=1.0,
    )
    bad = locations["BC1116"]
    assert not bad.location.observed
    assert not bad.reliable
    assert bad.location.basis == "trajectory_reference"
    assert bad.location.query_rt == pytest.approx(
        float(np.median(list(expected.values())))
    )
    if independent_branch:
        good = locations["independent"]
        assert good.location.observed and good.reliable
        assert good.location.basis == "trajectory_observed"
        assert good.location.query_rt == pytest.approx(expected["independent"])


@pytest.mark.parametrize(
    "second_owner_rt", [35.79459419948333, 35.81459419948333, 36.4]
)
def test_direct_binding_does_not_invent_conflict_from_drift_offset_or_missing_binding(
    second_owner_rt,
):
    owners, contexts, orders = _direct_conflict_case(second_owner_rt)
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    trajectory = model.trajectory_for(owners)
    assert dict(trajectory.sample_apices) == pytest.approx(
        {**{o.sample_stem: o.owner_apex_rt for o in owners}, "BC1116": 35.7000614}
    )
    location = trajectory.backfill_locations(
        ("BC1116",),
        reference_samples=frozenset(owner.sample_stem for owner in owners),
        rt_frame=SampleRtFrame(injection_order=orders),
        backfill_half_window_min=1.0,
    )["BC1116"]
    assert location.location.observed and location.reliable
    assert location.location.query_rt == pytest.approx(35.7000614)


def test_same_selected_segment_does_not_make_two_landmarks_distinct_peaks():
    owners, contexts, orders = _direct_conflict_case(33.103192714116666)
    # Two maxima inside one selected segment do not establish distinct peaks.
    owners[1] = replace(owners[1], owner_peak_end_rt=35.9)
    model = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    assert dict(model.trajectory_for(owners).sample_apices)["BC1116"] == 35.7000614


def test_retained_247_locations_survive_a_separate_contradicted_link():
    data = json.loads(
        (
            Path(__file__).parent / "fixtures/trajectory_localization_247.json"
        ).read_text()
    )
    owners = [
        replace(_owner(row["sample_stem"], "reference", row["owner_apex_rt"]), **row)
        for row in data["owners"]
    ]
    contexts = [
        SampleElutionContext(
            **{key: value for key, value in row.items() if key != "landmarks"},
            landmarks=tuple(ElutionLandmark(**peak) for peak in row["landmarks"]),
        )
        for row in data["contexts"]
    ]
    model = resolve_peak_trajectories(
        owners,
        contexts,
        config=AlignmentConfig(),
        injection_order=data["injection_order"],
    )
    # There is a real same-sample contradiction, not an already-clean track.
    assert any(
        dict(track.sample_apices).get("BenignfatBC1228_DNA") == 13.119102411466667
        for track in model.tracks
    )
    trajectory = model.trajectory_for(owners)
    expected = {
        "BenignfatBC1117_DNA": 12.437577116016666,
        "NormalBC2282_DNA": 12.46524381305,
    }
    assert dict(trajectory.sample_apices) == pytest.approx(
        {**{o.sample_stem: o.owner_apex_rt for o in owners}, **expected}
    )
    locations = trajectory.backfill_locations(
        tuple(expected),
        reference_samples=frozenset(o.sample_stem for o in owners),
        rt_frame=SampleRtFrame(injection_order=data["injection_order"]),
        backfill_half_window_min=1.0,
    )
    for sample, rt in expected.items():
        assert locations[sample].location.observed and locations[sample].reliable
        assert locations[sample].location.query_rt == pytest.approx(rt)


def test_backfill_planning_consumes_filtered_locations_with_sample_attribution(
    tmp_path,
):
    from types import SimpleNamespace

    from tests.cid_nl_peak_support_no_raw_fixture import (
        cid_nl_evidence,
        cid_nl_peak,
        cid_nl_support_policy,
        cid_nl_unique_support,
    )
    from tests.processing_identity_no_raw_fixture import (
        build_processing_identity_no_raw_fixture,
    )
    from xic_extractor.alignment.backfill.cid_nl_planning import (
        plan_grouped_cid_nl_backfills,
    )
    from xic_extractor.alignment.grouping_bootstrap import (
        InitialGroupingScope,
        RetainedGroupingCandidate,
    )
    from xic_extractor.alignment.grouping_workflow import group_cid_nl_owners
    from xic_extractor.alignment.hypothesis_owner_adapter import (
        compose_cid_nl_peak_hypotheses_from_assessment,
        owner_from_cid_nl_assessment_support,
    )
    from xic_extractor.peak_detection.cid_nl_method_assessment import (
        CidNlEvaluationReceipt,
        mint_cid_nl_method_assessment_ref,
    )

    original, contexts, orders = _direct_conflict_case(
        33.103192714116666, independent_branch=True
    )
    fixture = build_processing_identity_no_raw_fixture(
        tmp_path,
        sample_stems=tuple(orders),
        sample_roles={sample: "study_sample" for sample in orders},
    )
    owners, retained = [], []
    for original_owner in original:
        name, rt = original_owner.sample_stem, original_owner.owner_apex_rt
        sample = fixture.sample(name)
        peak = cid_nl_peak(
            fixture, name, apex_rt=rt, rt_left_min=rt - 0.1, rt_right_min=rt + 0.1
        )
        evidence = cid_nl_evidence(fixture, name, scan_number=1, scan_rt_min=rt)
        support = cid_nl_unique_support(fixture, name, peak=peak, evidence=evidence)
        assessment = mint_cid_nl_method_assessment_ref(
            processing_owners=fixture.owners,
            sample=sample,
            target=fixture.target,
            detector_invocation_id=f"direct:{name}",
            evaluation_receipt=CidNlEvaluationReceipt(
                query_rt_min=rt - 0.1,
                query_rt_max=rt + 0.1,
                support_policy=cid_nl_support_policy(),
                acquisition_supported=True,
                search_completed=True,
                reason_code=None,
            ),
            candidate_peaks=(peak,),
            evidence_refs=(evidence,),
            individual_support_refs=(support,),
        )
        (hypothesis,) = compose_cid_nl_peak_hypotheses_from_assessment(
            processing_owners=fixture.owners,
            sample=sample,
            assessment=assessment,
        )
        owners.append(
            owner_from_cid_nl_assessment_support(
                sample=sample,
                assessment=assessment,
                hypothesis=hypothesis,
            )
        )
        retained.append(RetainedGroupingCandidate(sample, hypothesis, assessment))
    # Keep the correspondence geometry while using the canonical no-RAW
    # fixture's m/z and genuine method-support/member identities.
    contexts = [
        replace(context, mz=context.mz - 659.3459114397047 + 500.0)
        for context in contexts
    ]
    config = AlignmentConfig()
    frame = SampleRtFrame(injection_order=orders)
    model = resolve_peak_trajectories(
        owners, contexts, config=config, injection_order=orders
    )
    (group,) = group_cid_nl_owners(
        owners,
        config=config,
        sample_universe=frozenset(orders),
        rt_frame=frame,
        peak_trajectories=model,
    )
    scope = InitialGroupingScope((499.0, 501.0), (28.0, 37.0), (group,), model)
    # The planner reads only this frozen scope and retained support, not the
    # acquisition/capture envelope of a runtime workset.
    workset = SimpleNamespace(grouping_scope=scope, retained_candidates=tuple(retained))
    questions = plan_grouped_cid_nl_backfills(
        (workset,),
        sample_population=fixture.samples,
        alignment_config=config,
        rt_frame=frame,
        half_windows_by_sample={s.identity.sample_key: 1.0 for s in fixture.samples},
    )
    by_id = {q.sample_key: q for q in questions}
    assert set(by_id) == {
        fixture.sample(name).identity.sample_key for name in ("BC1116", "independent")
    }
    bad = by_id[fixture.sample("BC1116").identity.sample_key].localization
    good = by_id[fixture.sample("independent").identity.sample_key].localization
    assert not bad.location.observed and not bad.reliable
    assert bad.location.basis == "trajectory_reference"
    assert bad.location.query_rt == pytest.approx(32.988294154916666)
    assert good.location.observed and good.reliable
    assert good.location.basis == "trajectory_observed"
    assert good.location.query_rt == pytest.approx(32.988294154916666 - 2.9)
    assert all(q.group == group for q in questions)
    assert group.owners == tuple(sorted(owners, key=lambda owner: owner.owner_id))
