"""Independent elution-order oracles, not chemical-identity labels."""

from __future__ import annotations

from dataclasses import replace

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
):
    trajectory = PeakTrajectory(
        (),
        (
            ("ref1", 10.0),
            ("ref2", 11.0 + offset_scatter),
            ("unconfirmed", 30.0),
            ("other_batch", 40.0),
        ),
    )
    result = trajectory.backfill_locations(
        ("missing",),
        reference_samples=frozenset({"ref1", "ref2", "other_batch"}),
        injection_order={"ref1": 1, "ref2": 2, "missing": 3},
        sample_cohorts={
            "ref1": "batch",
            "ref2": "batch",
            "missing": "batch",
            "unconfirmed": "batch",
            "other_batch": "different",
        },
        rt_warp=None,
        backfill_half_window_min=0.5,
        paired_istd_rts={
            "ref1": 8.0,
            "ref2": 9.0,
            "missing": 15.0,
            "unconfirmed": 10.0,
            "other_batch": 10.0,
        },
    )["missing"]
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
        injection_order=orders,
        sample_cohorts=cohorts,
        rt_warp=None,
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
        injection_order=orders,
        sample_cohorts={sample: "batch" for sample in orders},
        rt_warp=None,
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
        "injection_order": orders,
        "sample_cohorts": dict.fromkeys(orders, "batch"),
        "rt_warp": None,
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
        injection_order=orders,
        sample_cohorts=dict.fromkeys(orders, "batch"),
        rt_warp=None,
        backfill_half_window_min=1.0,
    )["missing"]
    assert result.reliable
    assert result.location.query_rt == pytest.approx(11.2)


def test_observed_peak_keeps_its_location_without_borrowing_another_batch():
    trajectory = PeakTrajectory((), (("a", 10.0), ("b", 10.1), ("observed", 15.0)))
    result = trajectory.backfill_locations(
        ("missing", "observed"),
        reference_samples=frozenset(("a", "b")),
        injection_order={"a": 1, "b": 2, "observed": 3, "missing": 4},
        sample_cohorts={"a": "A", "b": "A", "observed": "B", "missing": "B"},
        rt_warp=None,
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
        identity_conflict=False,
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
            injection_order=orders,
            sample_cohorts=dict.fromkeys(orders, "batch"),
            rt_warp=None,
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
        injection_order=orders,
        sample_cohorts=dict.fromkeys(orders, "batch"),
        rt_warp=None,
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
        injection_order=orders,
        sample_cohorts=dict.fromkeys(orders, "batch"),
        rt_warp=None,
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
            injection_order={"a": 1, "b": 3, "target": 2},
            rt_warp=warp,
            sample_cohorts={"a": "batch", "b": "batch", "target": "batch"},
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
            injection_order={"a": 1, "b": 3, "other": 2},
            rt_warp=warp,
            sample_cohorts={"a": "batch", "b": "batch", "other": "different"},
        )
    )
    assert unsupported.query_rt == 10.0
    assert unsupported.basis == "trajectory_reference"


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
            injection_order=orders,
            sample_cohorts=cohorts,
            rt_warp=flat,
        )
    )
    uncalibrated = (
        PeakTrajectories()
        .trajectory_for(members)
        .locate(
            backfill_half_window_min=1.0,
            sample_stem="target",
            injection_order=orders,
            sample_cohorts=cohorts,
            rt_warp=None,
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
                injection_order=orders,
                sample_cohorts=dict.fromkeys(orders, "batch"),
                rt_warp=warp,
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
                injection_order=orders,
                sample_cohorts=dict.fromkeys(orders, "batch"),
                rt_warp=warp,
                backfill_half_window_min=10.0,
            )
        )
        assert "support_boundary" not in wide_search.basis
        assert not bounds[0] + offset <= wide_search.query_rt <= bounds[1] + offset
