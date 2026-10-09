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
    resolve_peak_trajectories,
)
from xic_extractor.alignment.related_ion_evidence import RelatedIonEvidence
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
    project_elution_context,
)
from xic_extractor.peak_detection.physical_peak_evaluation import (
    prepare_physical_signal,
)


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


@pytest.mark.parametrize("related_rt", [None, 10.6, 11.2])
def test_missing_position_uses_ms1_trajectory_points_not_another_rt_answer(related_rt):
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
        related_ion=(
            RelatedIonEvidence(related_rt, ("partner",))
            if related_rt is not None
            else None
        ),
    )
    # Two supported owners alone have median 10.2. The MS1-only point in s1
    # completes the straight trajectory 10 + 0.2 * injection order.
    assert location.query_rt == pytest.approx(10.6)
    assert location.observed_rt is None
    if related_rt is not None:
        agreement = "consistent" if related_rt == 10.6 else "discordant"
        assert location.basis == f"trajectory_fit;related_ion_{agreement}:partner"


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
    assert dict(result.trajectory_for([owners[2]]).sample_apices).get("a") == 10.6
    assert dict(result.trajectory_for([owners[2]]).sample_apices).get("d") == 11.25
    assert {track.owner_ids for track in result.tracks} == {
        ("a:early", "b:early", "d:early"),
        ("c:late",),
    }


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
