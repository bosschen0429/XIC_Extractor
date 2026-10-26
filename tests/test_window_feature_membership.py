"""Real full85 observations that exposed ordinary row fragmentation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from tests.alignment_pipeline_helpers import resolved_elution_context
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.cid_nl_inputs import (
    CID_NL_ALIGNMENT_CONFIG,
)
from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
    infer_feature_memberships,
    supports_existing_feature,
)
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import (
    PeakTrajectories,
    PeakTrajectory,
    resolve_peak_trajectories,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.alignment.sample_rt_calibration import (
    BatchRtCalibration,
    BatchRtCalibrationAccumulator,
)
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)
from xic_extractor.peak_detection.hypotheses import IntegrationResult
from xic_extractor.peak_detection.ms1_shape_identity import prepare_local_peak_shape

_FIXTURES = Path(__file__).parent / "fixtures"
_DATA = json.loads((_FIXTURES / "window_feature_regression_cases.json").read_text())
_WARP_DATA = json.loads((_FIXTURES / "window_feature_warp_knots.json").read_text())


def _owners(case: str) -> tuple[tuple[SampleLocalMS1Owner, ...], dict[str, int]]:
    columns = _DATA["columns"]
    rows = [dict(zip(columns, values, strict=True)) for values in _DATA["cases"][case]]
    owners = []
    orders = {}
    for index, row in enumerate(rows):
        sample = row["sample_stem"]
        orders[sample] = row["injection_order"]
        mz = row["precursor_mz"]
        product = row["product_mz"]
        rt = row["apex_rt_min"]
        assert row["candidate_state"] in {
            "ms1_feature_nl_supported",
            "ms1_feature_nl_rescued",
        }
        event = IdentityEvent(
            candidate_id=f"{case}:{sample}:{index}",
            sample_stem=sample,
            raw_file=f"{sample}.raw",
            neutral_loss_tag="DNA_dR",
            precursor_mz=mz,
            product_mz=product,
            observed_neutral_loss_da=mz - product,
            seed_rt=rt,
            evidence_score=row["evidence_score"],
            seed_event_count=row["seed_event_count"],
        )
        owners.append(
            SampleLocalMS1Owner(
                owner_id=f"{case}:{index}",
                sample_stem=sample,
                raw_file=f"{sample}.raw",
                precursor_mz=mz,
                owner_apex_rt=rt,
                owner_peak_start_rt=rt - 0.05,
                owner_peak_end_rt=rt + 0.05,
                owner_area=max(1.0, row["area"]),
                owner_height=100.0,
                primary_identity_event=event,
                supporting_events=(),
                identity_conflict=False,
                assignment_reason="same_apex_window",
            )
        )
    return tuple(owners), orders


def _warp() -> BatchRtWarp:
    return BatchRtWarp(
        tuple(
            (sample, tuple(tuple(knot) for knot in knots))
            for sample, knots in _WARP_DATA["knots_by_sample"]
        ),
        _WARP_DATA["anchor_group_count"],
    )


def _synthetic_owner(
    sample: str,
    owner_id: str,
    rt_min: float,
    *,
    precursor_mz: float = 500.0,
    product_mz: float = 383.9526,
) -> SampleLocalMS1Owner:
    event = IdentityEvent(
        candidate_id=owner_id,
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        neutral_loss_tag="DNA_dR",
        precursor_mz=precursor_mz,
        product_mz=product_mz,
        observed_neutral_loss_da=precursor_mz - product_mz,
        seed_rt=rt_min,
        evidence_score=90,
        seed_event_count=3,
    )
    return SampleLocalMS1Owner(
        owner_id=owner_id,
        sample_stem=sample,
        raw_file=f"{sample}.raw",
        precursor_mz=precursor_mz,
        owner_apex_rt=rt_min,
        owner_peak_start_rt=rt_min - 0.05,
        owner_peak_end_rt=rt_min + 0.05,
        owner_area=1000.0,
        owner_height=100.0,
        primary_identity_event=event,
        supporting_events=(),
        identity_conflict=False,
        assignment_reason="same_apex_window",
    )


@pytest.mark.parametrize("reverse", (False, True))
def test_calibrated_band_outweighs_free_shift_pattern_seed(reverse: bool) -> None:
    owners = tuple(
        replace(
            _synthetic_owner(sample, owner_id, apex),
            owner_peak_start_rt=start,
            owner_peak_end_rt=end,
        )
        for sample, owner_id, apex, start, end in (
            ("a", "observed", 7.75, 7.50, 8.18),
            ("b", "early", 6.115, 5.90, 6.40),
            ("b", "main-b", 7.76, 7.50, 8.18),
            ("c", "main-c", 7.74, 7.50, 8.18),
            ("d", "main-d", 7.75, 7.50, 8.18),
        )
    )
    orders = {"a": 1, "b": 2, "c": 3, "d": 4}
    frame = SampleRtFrame(injection_order=orders).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            (
                ("a", ((7.74, 7.74), (9.5, 9.5))),
                *((sample, ((5.0, 5.0), (9.5, 9.5))) for sample in ("b", "c", "d")),
            )
        )
    )
    # The first landmark's width extends beyond calibration support. The raw
    # two-peak pattern can therefore match after a free shift, despite the
    # measured apex coordinates placing the owners in different bands.
    trajectories = resolve_peak_trajectories(
        owners,
        (
            resolved_elution_context("a", ((7.75, 30.0), (8.45, 100.0))),
            resolved_elution_context("b", ((6.115, 30.0), (6.815, 100.0))),
        ),
        config=AlignmentConfig(),
        injection_order=orders,
        rt_frame=frame,
    )
    assert any(
        {"observed", "early"} <= set(track.owner_ids) for track in trajectories.tracks
    )
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=AlignmentConfig(),
        rules=CID_NL_GROUPING_RULES,
        rt_frame=frame,
        peak_trajectories=trajectories,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("observed", "main-b", "main-c", "main-d")),
        frozenset(("early",)),
    }


@pytest.mark.parametrize("reverse", (False, True))
def test_partial_region_calibration_keeps_supported_common_apices(
    reverse: bool,
) -> None:
    owners = tuple(
        replace(
            _synthetic_owner(sample, owner_id, apex),
            owner_peak_start_rt=apex - 0.15,
            owner_peak_end_rt=apex + 0.15,
        )
        for sample, owner_id, apex in (
            ("a", "a-early", 10.0),
            ("a", "a-late", 10.7),
            ("b", "b-early", 9.3),
            ("b", "b-late", 10.0),
            ("c", "c-main", 10.0),
            ("c", "c-late", 10.7),
        )
    )
    orders = {"a": 1, "b": 2, "c": 3}
    frame = SampleRtFrame(injection_order=orders).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            (
                ("a", ((9.99, 9.99), (12.0, 12.0))),
                ("b", ((8.0, 8.0), (12.0, 12.0))),
                ("c", ((8.0, 8.0), (12.0, 12.0))),
            )
        )
    )
    trajectories = resolve_peak_trajectories(
        owners,
        (
            resolved_elution_context("a", ((10.0, 30.0), (10.7, 100.0))),
            resolved_elution_context("b", ((9.3, 30.0), (10.0, 100.0))),
        ),
        config=AlignmentConfig(),
        injection_order=orders,
        rt_frame=frame,
    )
    assert not trajectories.permits_membership(("a-early", "b-late"))
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=AlignmentConfig(),
        rules=CID_NL_GROUPING_RULES,
        rt_frame=frame,
        peak_trajectories=trajectories,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("a-early", "b-early", "c-main")),
        frozenset(("a-late", "b-late", "c-late")),
    }
    # Three-member groups retain the cost policy rather than a hard RT split.
    # a-early's edge extends beyond its calibrated support; its apex does not.
    # Unknown edges cannot discard measured apex coordinates or import raw edges.
    assert {d.accepted_risk.rt_basis for d in result.sparse_decisions} == {
        "drift_corrected"
    }
    assert "low_rt_support" in next(
        d.accepted_risk.risk_flags
        for d in result.sparse_decisions
        if d.owner_id == "a-early"
    )
    # Membership does not erase the caller's original localization geometry.
    assert not trajectories.permits_membership(("a-early", "b-late"))


@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize("include_supported_owner", (False, True))
def test_unrelated_calibration_preserves_unsupported_raw_pattern(
    reverse: bool, include_supported_owner: bool
) -> None:
    raw_owners = (
        _synthetic_owner("a", "a-raw", 10.0),
        _synthetic_owner("b", "b-raw", 11.0),
    )
    owners = raw_owners
    if include_supported_owner:
        owners += (
            _synthetic_owner(
                "a", "a-supported", 20.0, precursor_mz=600.0, product_mz=483.9526
            ),
        )
    orders = {"a": 1, "b": 2}
    frame = SampleRtFrame(injection_order=orders).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            tuple((sample, ((18.0, 18.0), (22.0, 22.0))) for sample in orders)
        )
    )
    assert all(
        frame.calibrated_rt_min(owner.sample_stem, owner.owner_apex_rt) is None
        for owner in raw_owners
    )
    trajectories = resolve_peak_trajectories(
        owners,
        (
            resolved_elution_context("a", ((10.0, 30.0), (10.7, 100.0))),
            resolved_elution_context("b", ((11.0, 30.0), (11.7, 100.0))),
        ),
        config=AlignmentConfig(),
        injection_order=orders,
        rt_frame=frame,
    )
    assert any(
        set(track.owner_ids) == {"a-raw", "b-raw"} for track in trajectories.tracks
    )
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=AlignmentConfig(),
        rules=CID_NL_GROUPING_RULES,
        rt_frame=frame,
        peak_trajectories=trajectories,
    )
    expected = {frozenset(("a-raw", "b-raw"))}
    if include_supported_owner:
        expected.add(frozenset(("a-supported",)))
    assert {frozenset(group.owner_ids) for group in result.groups} == expected


@pytest.mark.parametrize("reverse", (False, True))
def test_retained_m298_configuration_preserves_sample_occupancy(
    reverse: bool,
) -> None:
    # Retained m298 geometry, rounded in its already-calibrated frame.
    # The high-mass owners form two concentrated populations 43.6 seconds
    # apart; the larger within-population range is only 2.0 seconds.
    rows = (
        ("a", "a-early", 9, 13.29663, 298.139679, 182.092172, 13.08574, 13.83271),
        ("b", "b-early", 10, 13.24687, 298.139889, 182.092161, 13.00025, 13.62336),
        ("qc", "qc-low", 12, 13.22745, 298.139840, 182.091941, 13.01534, 13.76716),
        ("first", "bridge", 1, 13.62648, 298.136047, 182.091889, 13.18043, 13.89318),
        ("qc", "qc-high", 12, 13.30995, 298.151120, 182.107381, 13.05742, 14.13970),
        ("c", "c-early", 7, 13.27739, 298.151052, 182.106604, 13.06649, 13.97713),
        ("r", "r-late", 4, 14.01819, 298.139450, 182.092013, 13.64646, 14.26398),
        ("s", "s-late", 18, 14.00938, 298.140195, 182.092070, 13.64239, 14.21320),
        ("a", "a-late", 9, 14.03857, 298.151042, 182.106752, 13.50191, 14.28526),
        ("b", "b-late", 10, 14.03733, 298.151085, 182.106867, 13.58118, 14.28465),
    )
    owners = tuple(
        replace(
            _synthetic_owner(
                sample, owner_id, apex, precursor_mz=mz, product_mz=product
            ),
            owner_peak_start_rt=start,
            owner_peak_end_rt=end,
        )
        for sample, owner_id, _, apex, mz, product, start, end in rows
    )
    orders = {row[0]: row[2] for row in rows}
    frame = SampleRtFrame(injection_order=orders).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            tuple((sample, ((12.0, 12.0), (15.0, 15.0))) for sample in orders)
        )
    )
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=CID_NL_ALIGNMENT_CONFIG,
        rules=CID_NL_GROUPING_RULES,
        rt_frame=frame,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("a-early", "b-early", "qc-low", "bridge", "r-late", "s-late")),
        frozenset(("qc-high", "c-early")),
        frozenset(("a-late", "b-late")),
    }
    by_id = {owner.owner_id: owner for owner in owners}
    assert all(
        len({by_id[key].sample_stem for key in group.owner_ids}) == len(group.owner_ids)
        for group in result.groups
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_injection_gap_does_not_discount_rt_in_union_priority(reverse: bool) -> None:
    owners = (
        _synthetic_owner(
            "first",
            "late-a",
            21.47482461247,
            precursor_mz=283.1120949147,
            product_mz=167.0638763455,
        ),
        _synthetic_owner(
            "middle",
            "observed",
            21.18422018628,
            precursor_mz=283.1177673340,
            product_mz=167.0639010014,
        ),
        _synthetic_owner(
            "nearby",
            "early-a",
            21.19326981726,
            precursor_mz=283.1156311035,
            product_mz=167.0675008556,
        ),
        _synthetic_owner(
            "last",
            "early-b",
            21.19326981726,
            precursor_mz=283.1156311035,
            product_mz=167.0675008556,
        ),
        _synthetic_owner(
            "last",
            "late-b",
            21.47482461247,
            precursor_mz=283.1120949147,
            product_mz=167.0638763455,
        ),
    )
    # The observed peak is 0.54 s from the early peak, not the late peak
    # 17.44 s away. A larger injection gap must not make that RT error cheaper
    # when supported pairings compete for the first irreversible union.
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(
            injection_order={"first": 3, "middle": 16, "nearby": 17, "last": 25}
        ),
        rules=CID_NL_GROUPING_RULES,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("observed", "early-a", "early-b")),
        frozenset(("late-a", "late-b")),
    }
    assert all(group.disposition == "assigned" for group in result.groups)


@pytest.mark.parametrize("bootstrap", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("pattern_seed", [False, True])
def test_peak_shape_contradiction_is_soft_only_for_final_membership(
    bootstrap: bool,
    reverse: bool,
    pattern_seed: bool,
) -> None:
    offsets = np.linspace(-0.6, 0.6, 121)
    peak = np.exp(-0.5 * (offsets / 0.07) ** 2)
    background = 3.0 - offsets + 0.15 * np.cos(offsets * 35)
    owners = []
    for sample, apex, values in (
        ("clean-a", 10.0, peak),
        ("clean-b", 10.1, peak * 250),
        ("background", 10.2, background),
    ):
        owners.append(
            replace(
                _synthetic_owner(sample, sample, apex),
                peak_shape=prepare_local_peak_shape(
                    offsets + apex,
                    values,
                    apex_rt_min=apex,
                ),
            )
        )
    if reverse:
        owners.reverse()
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        bootstrap=bootstrap,
        rt_frame=SampleRtFrame(
            injection_order={
                "clean-a": 1,
                "clean-b": 2,
                "background": 3,
            }
        ),
        peak_trajectories=PeakTrajectories(
            tracks=(
                PeakTrajectory(
                    tuple(owner.owner_id for owner in owners),
                    tuple((owner.sample_stem, owner.owner_apex_rt) for owner in owners),
                ),
            )
        )
        if pattern_seed
        else None,
        rules=CID_NL_GROUPING_RULES,
    )
    if not bootstrap:
        assert len(result.groups) == 1
        assert any(
            "accepted_contradiction" in decision.accepted_risk.risk_flags
            for decision in result.sparse_decisions
        )
        return
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("clean-a", "clean-b")),
        frozenset(("background",)),
    }


def test_ordered_doublet_correspondence_survives_shape_disagreement():
    offsets = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            _synthetic_owner(sample, f"{sample}:{band}", apex),
            peak_shape=prepare_local_peak_shape(
                offsets + apex,
                3 - offsets
                if sample == "b" and band == "early"
                else np.exp(-0.5 * (offsets / 0.07) ** 2),
                apex_rt_min=apex,
            ),
        )
        for sample, shift in (("a", 0.0), ("b", 0.1))
        for band, apex in (("early", 10 + shift), ("late", 10.7 + shift))
    )
    contexts = tuple(
        resolved_elution_context(sample, ((10 + shift, 30), (10.7 + shift, 100)))
        for sample, shift in (("a", 0.0), ("b", 0.1))
    )
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order={"a": 1, "b": 2}
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rules=CID_NL_GROUPING_RULES,
        rt_frame=SampleRtFrame(injection_order={"a": 1, "b": 2}),
        peak_trajectories=trajectories,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("a:early", "b:early")),
        frozenset(("a:late", "b:late")),
    }


@pytest.mark.parametrize("bootstrap", (False, True))
@pytest.mark.parametrize(
    "right_start, joins", ((10.2, True), (10.25, True), (10.3, False))
)
def test_peak_regions_need_overlap_not_mutual_apex_containment(
    bootstrap: bool, right_start: float, joins: bool
) -> None:
    owners = (
        replace(
            _synthetic_owner("left", "left", 10.0),
            owner_peak_start_rt=9.8,
            owner_peak_end_rt=10.25,
        ),
        replace(
            _synthetic_owner("right", "right", 10.5),
            owner_peak_start_rt=right_start,
            owner_peak_end_rt=10.8,
        ),
    )
    frame = SampleRtFrame(
        injection_order={"left": 0, "right": 1}
    ).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            tuple((owner.sample_stem, ((9.0, 9.0), (11.0, 11.0))) for owner in owners)
        )
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
        bootstrap=bootstrap,
    )
    expected = (
        {frozenset(("left", "right"))}
        if joins or not bootstrap
        else {frozenset(("left",)), frozenset(("right",))}
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == expected
    for candidate, peer in (owners, owners[::-1]):
        assert (
            supports_existing_feature(
                candidate,
                (peer,),
                config=AlignmentConfig(),
                rt_frame=frame,
                rules=CID_NL_GROUPING_RULES,
            )
            is joins
        )


def test_final_mixed_frame_comparison_does_not_relax_backfill_regions():
    offsets = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            _synthetic_owner(sample, sample, apex),
            owner_peak_start_rt=apex - half_width,
            owner_peak_end_rt=apex + half_width,
            peak_shape=prepare_local_peak_shape(
                offsets + apex,
                np.exp(-0.5 * (offsets / width) ** 2),
                apex_rt_min=apex,
            ),
        )
        for sample, apex, width, half_width in (
            ("narrow", 10.0, 0.03, 0.1),
            ("shifted-wide", 9.0, 0.25, 0.6),
            ("late", 10.5, 0.03, 0.1),
            ("unsupported", 10.0, 0.03, 0.1),
        )
    )
    frame = SampleRtFrame(
        injection_order={owner.sample_stem: i for i, owner in enumerate(owners)}
    ).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            (
                ("narrow", ((8.0, 8.0), (12.0, 12.0))),
                ("shifted-wide", ((7.0, 8.0), (11.0, 12.0))),
                ("late", ((8.0, 8.0), (12.0, 12.0))),
            )
        )
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
    )
    assert len(result.groups) == 1
    assert {d.accepted_risk.rt_basis for d in result.sparse_decisions} == {"raw"}
    assert any(
        "accepted_contradiction" in d.accepted_risk.risk_flags
        for d in result.sparse_decisions
    )
    # Without its own calibration support, "unsupported" is compared with
    # "narrow" in the observed frame, where the two apices coincide.
    for candidate, expected in zip(owners[1:], (True, False, True), strict=True):
        assert (
            supports_existing_feature(
                candidate,
                (owners[0],),
                config=AlignmentConfig(),
                rt_frame=frame,
                rules=CID_NL_GROUPING_RULES,
            )
            is expected
        )
    assert [(owner.owner_apex_rt, owner.owner_area) for owner in owners] == [
        (10.0, 1000.0),
        (9.0, 1000.0),
        (10.5, 1000.0),
        (10.0, 1000.0),
    ]


@pytest.mark.parametrize("bootstrap", (False, True))
def test_region_separation_is_soft_for_final_and_strict_for_backfill(bootstrap):
    # Band a spans more than one member's narrow region (a1 and a4 exclude each
    # other's apex); band b lies outside band a's consensus region.
    owners = tuple(
        replace(
            _synthetic_owner(sample, sample, apex),
            owner_peak_start_rt=apex - 0.1,
            owner_peak_end_rt=apex + 0.1,
        )
        for sample, apex in (
            ("a1", 10.0),
            ("b1", 10.4),
            ("a2", 10.04),
            ("a3", 10.08),
            ("b2", 10.44),
            ("a4", 10.12),
        )
    )
    frame = SampleRtFrame(
        injection_order={owner.sample_stem: i for i, owner in enumerate(owners)}
    ).with_sample_calibration(
        BatchRtCalibration.from_coordinate_knots(
            tuple((owner.sample_stem, ((9.0, 9.0), (11.0, 11.0))) for owner in owners)
        )
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
        bootstrap=bootstrap,
    )
    if bootstrap:
        assert {frozenset(group.owner_ids) for group in result.groups} == {
            frozenset(("a1", "a2", "a3", "a4")),
            frozenset(("b1", "b2")),
        }
    else:
        assert len(result.groups) == 1
        assert any(
            "region_disagreement" in decision.accepted_risk.soft_contradictions
            for decision in result.sparse_decisions
        )
    by_id = {owner.owner_id: owner for owner in owners}
    band_a = tuple(by_id[owner_id] for owner_id in ("a1", "a2", "a3"))
    assert supports_existing_feature(
        by_id["a4"],
        band_a,
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
    )
    assert not supports_existing_feature(
        by_id["b1"],
        band_a,
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
    )


@pytest.mark.parametrize("bootstrap", [False, True])
def test_intermediate_shape_does_not_transitively_merge_incompatible_peaks(
    bootstrap: bool,
) -> None:
    offsets = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            _synthetic_owner(name, name, 10.0),
            peak_shape=prepare_local_peak_shape(
                offsets + 10.0,
                np.exp(-0.5 * (offsets / width) ** 2),
                apex_rt_min=10.0,
            ),
        )
        for name, width in (("narrow", 0.03), ("middle", 0.07), ("wide", 0.25))
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        bootstrap=bootstrap,
        rt_frame=SampleRtFrame(
            injection_order={
                "narrow": 1,
                "middle": 2,
                "wide": 3,
            }
        ),
        rules=CID_NL_GROUPING_RULES,
    )
    assert sorted(len(group.owner_ids) for group in result.groups) == (
        [1, 1, 1] if bootstrap else [3]
    )
    if bootstrap:
        assert not any(
            {"narrow", "wide"}.issubset(group.owner_ids) for group in result.groups
        )
    else:
        # Accepting the whole group must retain its endpoint contradiction,
        # even though the intermediate peak agrees with both endpoints.
        assert any(
            "shape_disagreement" in decision.accepted_risk.soft_contradictions
            for decision in result.sparse_decisions
        )


def test_singleton_shape_disagreement_is_retained_after_band_extension() -> None:
    offsets = np.linspace(-0.6, 0.6, 121)
    # "broader" differs only from the one narrow member (0.74 < 0.80) and
    # matches the rest of the band (0.94); the ramp matches none of it.
    gaussian = {
        **{f"seed-{i}": 0.07 for i in range(1, 15)},
        "narrow": 0.03,
        "broader": 0.12,
    }
    orders = {
        **{f"seed-{i}": i for i in range(1, 15)},
        "narrow": 15,
        "broader": 40,
        "ramp": 70,
    }
    owners = tuple(
        replace(
            _synthetic_owner(name, name, 10.0),
            peak_shape=prepare_local_peak_shape(
                offsets + 10,
                np.exp(-0.5 * (offsets / gaussian[name]) ** 2)
                if name in gaussian
                else 3 - offsets,
                apex_rt_min=10,
            ),
        )
        for name in orders
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=orders),
        rules=CID_NL_GROUPING_RULES,
    )
    assert len(result.groups) == 1
    ramp = next(d for d in result.sparse_decisions if d.owner_id == "ramp")
    assert "shape_disagreement" in ramp.accepted_risk.soft_contradictions
    assert set(ramp.accepted_risk.selected_owner_ids) == set(orders)


@pytest.mark.parametrize("with_order", [False, True])
def test_existing_feature_admission_requires_compatible_peak_shape(with_order) -> None:
    offsets = np.linspace(-0.6, 0.6, 121)
    reference = replace(
        _synthetic_owner("reference", "reference", 10),
        peak_shape=prepare_local_peak_shape(
            offsets + 10,
            np.exp(-0.5 * (offsets / 0.07) ** 2),
            apex_rt_min=10,
        ),
    )
    candidate = replace(reference, owner_id="candidate", sample_stem="candidate")
    frame = (
        SampleRtFrame(injection_order={"reference": 1, "candidate": 2})
        if with_order
        else SampleRtFrame()
    )
    assert supports_existing_feature(
        candidate,
        (reference,),
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
    )
    candidate = replace(
        candidate,
        peak_shape=prepare_local_peak_shape(
            offsets + 10,
            3 - offsets,
            apex_rt_min=10,
        ),
    )
    assert not supports_existing_feature(
        candidate,
        (reference,),
        config=AlignmentConfig(),
        rt_frame=frame,
        rules=CID_NL_GROUPING_RULES,
    )


def test_tissue_rt_outlier_has_no_feasible_same_group_alternative() -> None:
    evidence = json.loads((_FIXTURES / "tissue_247_peak_shapes.json").read_text())
    owners = tuple(
        replace(
            _synthetic_owner(
                row["sample"],
                row["owner_id"],
                row["apex_rt"],
                precursor_mz=row["mz"],
                product_mz=row["product_mz"],
            ),
            owner_peak_start_rt=row["bounds"][0],
            owner_peak_end_rt=row["bounds"][1],
            peak_shape=prepare_local_peak_shape(
                row["rt"],
                row["intensity"],
                apex_rt_min=row["apex_rt"],
            ),
        )
        for row in evidence["owners"]
    )
    frame = SampleRtFrame(
        injection_order={
            row["sample"]: row["injection_order"] for row in evidence["owners"]
        }
    ).with_warp(
        BatchRtWarp(
            tuple(
                (
                    row["sample"],
                    ((row["corrected_rt"], row["apex_rt"] - row["corrected_rt"]),),
                )
                for row in evidence["owners"]
            ),
            0,
        )
    )
    result = infer_feature_memberships(
        owners, config=AlignmentConfig(), rt_frame=frame, rules=CID_NL_GROUPING_RULES
    )
    by_id = {owner.owner_id: owner for owner in owners}
    assert {
        frozenset(by_id[owner_id].sample_stem for owner_id in group.owner_ids)
        for group in result.groups
    } == {
        frozenset(
            owner.sample_stem
            for owner in owners
            if owner.sample_stem != "NormalBC2302_DNA"
        ),
        frozenset(("NormalBC2302_DNA",)),
    }
    separate = next(
        decision
        for decision in result.sparse_decisions
        if decision.sample_stem == "NormalBC2302_DNA"
    )
    assert separate.state == "birth"
    assert separate.accepted_risk.alternative_partition is None
    assert separate.accepted_risk.status == "no_feasible_alternative"


@pytest.mark.parametrize("reverse", [False, True])
def test_pattern_and_unobserved_context_samples_extend_one_trajectory(reverse) -> None:
    owners = [
        _synthetic_owner("a", "a:early", 10.0),
        _synthetic_owner("a", "a:late", 10.7),
        _synthetic_owner("b", "b:early", 10.1),
        _synthetic_owner("b", "b:late", 10.8),
        _synthetic_owner("c", "c:early", 10.2),
    ]
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=500.0,
            rt_start=9.0,
            rt_end=12.0,
            landmarks=(
                ElutionLandmark(10.0 + shift, 30.0, 0.25),
                ElutionLandmark(10.7 + shift, 100.0, 0.25),
            ),
        )
        for sample, shift in (("a", 0.0), ("b", 0.1))
    ]
    if reverse:
        owners.reverse()
        contexts.reverse()
    orders = {"a": 1, "b": 2, "c": 3}
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(("a:early", "b:early", "c:early")),
        frozenset(("a:late", "b:late")),
    }
    assert all(group.disposition == "assigned" for group in result.groups)
    # Pattern seeds join the whole-window graph; the band is the decision.
    assert {group.basis for group in result.groups} == {"window_level_rt_band"}


def test_bootstrap_pattern_seed_extends_to_an_unobserved_context_sample() -> None:
    owners = [
        _synthetic_owner("a", "a:early", 10.0),
        _synthetic_owner("a", "a:late", 10.7),
        _synthetic_owner("b", "b:early", 10.1),
        _synthetic_owner("b", "b:late", 10.8),
        _synthetic_owner("c", "c:early", 10.2),
    ]
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=500.0,
            rt_start=9.0,
            rt_end=12.0,
            landmarks=(
                ElutionLandmark(10.0 + shift, 30.0, 0.25),
                ElutionLandmark(10.7 + shift, 100.0, 0.25),
            ),
        )
        for sample, shift in (("a", 0.0), ("b", 0.1))
    ]
    orders = {"a": 1, "b": 2, "c": 3}
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        bootstrap=True,
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    assert {(group.owner_ids, group.disposition) for group in result.groups} == {
        (("a:early", "b:early", "c:early"), "assigned"),
        (("a:late", "b:late"), "assigned"),
    }


def _method_disambiguated_pattern_conflict():
    # The sample a doublet distinguishes two method envelopes. A shifted
    # pattern incorrectly links b's early owner to c's late owner, and its
    # borrowed a position makes that track distinct from the valid late track.
    owners = [
        _synthetic_owner("a", "a:early", 10.0),
        _synthetic_owner("a", "a:late", 11.5, product_mz=383.9726),
        _synthetic_owner("b", "b:early", 10.05),
        _synthetic_owner("c", "c:late", 11.55, product_mz=383.9726),
        _synthetic_owner("d", "d:late", 11.45, product_mz=383.9726),
        _synthetic_owner("u", "u:independent", 20.0),
        _synthetic_owner("v", "v:independent", 22.8),
    ]
    geometry = PeakTrajectories(
        (
            PeakTrajectory(
                ("b:early", "c:late"), (("a", 13.0), ("b", 10.05), ("c", 11.55))
            ),
            PeakTrajectory(("a:late", "d:late"), (("a", 11.5), ("d", 11.45))),
            PeakTrajectory(
                ("u:independent", "v:independent"), (("u", 20.0), ("v", 22.8))
            ),
        )
    )
    return owners, geometry


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("bootstrap", [False, True])
def test_coobserved_method_witness_withdraws_only_contradicted_membership(
    reverse,
    bootstrap,
):
    owners, geometry = _method_disambiguated_pattern_conflict()
    before = geometry.trajectory_for(owners[2:4])
    result = infer_feature_memberships(
        owners[::-1] if reverse else owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order={s: i for i, s in enumerate("abcduv")}),
        peak_trajectories=geometry,
        bootstrap=bootstrap,
        rules=CID_NL_GROUPING_RULES,
    )
    expected = {
        frozenset(("a:early", "b:early")),
        frozenset(("a:late", "c:late", "d:late")),
    }
    expected.update(
        {frozenset(("u:independent", "v:independent"))}
        if bootstrap
        else {frozenset(("u:independent",)), frozenset(("v:independent",))}
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == expected
    # Final selection scores the independent raw pair's actual 168 s gap;
    # it is no longer a mandatory seed with zero RT cost.
    # Membership qualification must not mutate geometry used by localization.
    assert geometry.trajectory_for(owners[2:4]) == before
    assert dict(before.sample_apices)["a"] == 13.0


@pytest.mark.parametrize(
    "control",
    [
        "missing_second_peak",
        "overlapping_peaks",
        "ambiguous_method",
        "provisional_anchor",
        "weak_anchor",
        "provisional_member",
        "weak_member",
    ],
)
def test_incomplete_coobserved_witness_does_not_veto_a_bootstrap_pattern(control):
    owners, geometry = _method_disambiguated_pattern_conflict()
    # Isolate the claimed correspondence; no competing valid late seed.
    geometry = PeakTrajectories((geometry.tracks[0],))
    owners = owners[:4]
    if control == "missing_second_peak":
        owners.pop(1)
    elif control == "overlapping_peaks":
        owners[0] = replace(owners[0], owner_peak_end_rt=12.0)
    elif control == "ambiguous_method":
        owners = [
            replace(
                owner,
                primary_identity_event=replace(
                    owner.primary_identity_event,
                    product_mz=383.9526,
                    observed_neutral_loss_da=116.0474,
                ),
            )
            for owner in owners
        ]
    elif control.startswith("provisional"):
        index = 1 if control.endswith("anchor") else 3
        owners[index] = replace(owners[index], is_provisional=True)
    else:
        index = 1 if control.endswith("anchor") else 3
        owners[index] = replace(
            owners[index],
            primary_identity_event=replace(
                owners[index].primary_identity_event,
                evidence_score=1,
                seed_event_count=0,
            ),
        )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order={s: i for i, s in enumerate("abcd")}),
        peak_trajectories=geometry,
        bootstrap=True,
        rules=CID_NL_GROUPING_RULES,
    )
    if control.endswith("member"):
        # Weak members cannot seed an assigned Feature in the first place.
        # Compare the full result to the same input without the second anchor:
        # neither can authorize the new witness-based withdrawal.
        without_anchor = infer_feature_memberships(
            [o for o in owners if o.owner_id != "a:late"],
            config=AlignmentConfig(),
            rt_frame=SampleRtFrame(
                injection_order={s: i for i, s in enumerate("abcd")}
            ),
            peak_trajectories=geometry,
            bootstrap=True,
            rules=CID_NL_GROUPING_RULES,
        )
        assert next(g for g in result.groups if "c:late" in g.owner_ids) == next(
            g for g in without_anchor.groups if "c:late" in g.owner_ids
        )
    else:
        assert any({"b:early", "c:late"} <= set(g.owner_ids) for g in result.groups)


@pytest.mark.parametrize("overlap", [False, True])
def test_bootstrap_coobserved_witness_uses_selected_integration_segments(overlap):
    owners, geometry = _method_disambiguated_pattern_conflict()
    for index in (0, 1):
        owner = owners[index]
        left = owner.owner_apex_rt - 0.05
        right = 12.0 if overlap and index == 0 else owner.owner_apex_rt + 0.05
        owners[index] = replace(
            owner,
            # The original search bounds deliberately suggest the opposite.
            owner_peak_start_rt=left if overlap else 9.0,
            owner_peak_end_rt=owner.owner_apex_rt + 0.05 if overlap else 12.0,
            selected_integration=IntegrationResult(
                rt_left_min=left,
                rt_apex_min=owner.owner_apex_rt,
                rt_right_min=right,
                raw_apex_rt_min=owner.owner_apex_rt,
                rt_width_min=right - left,
                height_raw=100.0,
                height_smoothed=100.0,
                area_raw_counts_seconds=1000.0,
            ),
        )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order={s: i for i, s in enumerate("abcduv")}),
        peak_trajectories=geometry,
        bootstrap=True,
        rules=CID_NL_GROUPING_RULES,
    )
    containing_late = next(g for g in result.groups if "c:late" in g.owner_ids)
    if overlap:
        assert {"b:early", "c:late"} <= set(containing_late.owner_ids)
    else:
        assert set(containing_late.owner_ids) == {"a:late", "c:late", "d:late"}


def test_single_sample_pattern_does_not_split_a_bootstrap_band() -> None:
    owners = [
        _synthetic_owner(sample, f"{sample}:p", 10.0 + 0.02 * index)
        for index, sample in enumerate("abcdef")
    ]
    orders = {sample: index for index, sample in enumerate("abcdef")} | {"x": 9}
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=500.0,
            rt_start=9.0,
            rt_end=12.0,
            landmarks=(
                ElutionLandmark(10.0 + shift, 100.0, 0.25),
                ElutionLandmark(10.7 + shift, 100.0, 0.25),
            ),
        )
        for sample, shift in (("a", 0.0), ("x", 0.01))
    ]
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    assert any(track.owner_ids == ("a:p",) for track in trajectories.tracks)

    for bootstrap in (False, True):
        result = infer_feature_memberships(
            owners,
            config=AlignmentConfig(),
            rt_frame=SampleRtFrame(injection_order=(orders or {})),
            bootstrap=bootstrap,
            peak_trajectories=trajectories,
            rules=CID_NL_GROUPING_RULES,
        )
        assert [
            (set(group.owner_ids), group.disposition) for group in result.groups
        ] == [({owner.owner_id for owner in owners}, "assigned")], bootstrap


def test_directional_extension_disagreement_keeps_the_pattern_seed() -> None:
    # The forward pass reaches b through a; the reverse pass reaches the seed
    # through c, which is beyond the preferred RT distance from b.
    owners = [
        _synthetic_owner("a", "a:p", 10.0),
        _synthetic_owner("b", "b:p", 10.05),
        _synthetic_owner("c", "c:p", 11.2),
    ]
    orders = {"a": 0, "b": 1, "c": 2}
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=500.0,
            rt_start=9.0,
            rt_end=13.0,
            landmarks=(
                ElutionLandmark(10.0 + shift, 30.0, 0.25),
                ElutionLandmark(10.7 + shift, 100.0, 0.25),
            ),
        )
        for sample, shift in (("a", 0.0), ("c", 1.2))
    ]
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        bootstrap=True,
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    assert {(group.owner_ids, group.disposition) for group in result.groups} == {
        (("a:p", "c:p"), "assigned"),
        (("b:p",), "undecided"),
    }


@pytest.mark.parametrize("seed_samples", (("c", "d"), ("b", "d")))
def test_directional_competition_does_not_demote_an_assigned_pattern_seed(
    seed_samples: tuple[str, str],
) -> None:
    owners = [
        _synthetic_owner("a", "a:left", 10.0),
        _synthetic_owner("a", "a:right", 11.0),
        *(_synthetic_owner(sample, f"{sample}:p", 10.5) for sample in "bcd"),
    ]
    contexts = [
        SampleElutionContext(
            sample_stem=sample,
            mz=500.0,
            rt_start=9.0,
            rt_end=12.0,
            landmarks=(
                ElutionLandmark(10.5, 30.0, 0.25),
                ElutionLandmark(11.2, 100.0, 0.25),
            ),
        )
        for sample in seed_samples
    ]
    orders = {sample: index for index, sample in enumerate("abcd")}
    config = AlignmentConfig(preferred_rt_sec=60.0)
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=config, injection_order=orders
    )
    seed_ids = tuple(f"{sample}:p" for sample in seed_samples)
    assert any(track.owner_ids == seed_ids for track in trajectories.tracks)
    result = infer_feature_memberships(
        owners,
        config=config,
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        bootstrap=True,
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    # b competes equally with a's two peaks before the pattern seed is complete.
    assert any(
        decision.owner_id == "b:p"
        and decision.reason == "alternative_assignment_has_equal_objective"
        for decision in result.sparse_decisions
    )
    assigned_ids = seed_ids if seed_samples == ("c", "d") else ("b:p", "c:p", "d:p")
    assert {(group.owner_ids, group.disposition) for group in result.groups} == {
        (("a:left",), "undecided"),
        (("a:right",), "undecided"),
        (assigned_ids, "assigned"),
        *({(("b:p",), "undecided")} if seed_samples == ("c", "d") else set()),
    }
    if seed_samples == ("c", "d"):
        (bridge,) = (group for group in result.groups if group.owner_ids == ("b:p",))
        assert bridge.reason == "directional_assignment_contains_unresolved_competition"


def test_alternating_direct_peaks_follow_ms1_pattern_not_nearest_absolute_rt() -> None:
    owners = []
    contexts = []
    orders = {}
    for index, (shift, selected) in enumerate(
        ((0.0, 0), (0.04, 1), (0.08, 0), (0.6, 1), (0.64, 0), (0.68, 1))
    ):
        sample = f"sample{index}"
        peaks = (10.0 + shift, 10.6 + shift)
        owners.append(
            _synthetic_owner(sample, f"{sample}:peak{selected}", peaks[selected])
        )
        contexts.append(
            resolved_elution_context(sample, tuple((rt, 1000.0) for rt in peaks))
        )
        orders[sample] = index + 1
    correspondence = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    assert len(correspondence.tracks) == 2
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        peak_trajectories=correspondence,
        rules=CID_NL_GROUPING_RULES,
    )
    assert {group.owner_ids for group in result.groups} == {
        ("sample0:peak0", "sample2:peak0", "sample4:peak0"),
        ("sample1:peak1", "sample3:peak1", "sample5:peak1"),
    }
    assert all(group.disposition == "assigned" for group in result.groups)


@pytest.mark.parametrize(
    "injection_orders",
    (tuple(range(1, 15)), (*range(1, 8), *range(21, 28))),
    ids=("contiguous", "context_gap"),
)
def test_never_compared_pattern_observations_do_not_split_one_band(
    injection_orders: tuple[int, ...],
) -> None:
    # Each block shares the same doublet context, but the 13 injections
    # between the blocks hold no related context. Not having compared two
    # blocks is not evidence that they observed different peaks.
    owners = []
    contexts = []
    orders = {}
    for order in injection_orders:
        sample = f"s{order:02d}"
        orders[sample] = order
        owners.append(_synthetic_owner(sample, f"{sample}:peak", 10.0))
        contexts.append(
            SampleElutionContext(
                sample,
                500.0,
                9.0,
                12.0,
                (ElutionLandmark(10.0, 100.0, 0.25), ElutionLandmark(10.6, 60.0, 0.25)),
            )
        )
    trajectories = resolve_peak_trajectories(
        owners, contexts, config=AlignmentConfig(), injection_order=orders
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    assert [(group.owner_ids, group.disposition) for group in result.groups] == [
        (tuple(sorted(owner.owner_id for owner in owners)), "assigned")
    ]


@pytest.mark.parametrize("bootstrap", (False, True), ids=("window", "bootstrap"))
@pytest.mark.parametrize("isolated_rt", (11.8, 12.4))
def test_single_peak_owner_keeps_ordinary_rt_gate(
    isolated_rt: float, bootstrap: bool
) -> None:
    # x has one landmark, so its nearest-RT link to the late peak is not an
    # ordered-pattern witness. Its owner, over a minute from the late band,
    # must not be seeded into that band or exempted from the RT gate.
    owners = [
        _synthetic_owner("a", "a:early", 10.0),
        _synthetic_owner("a", "a:late", 10.7),
        _synthetic_owner("b", "b:early", 10.05),
        _synthetic_owner("b", "b:late", 10.75),
        _synthetic_owner("x", "x:only", isolated_rt),
    ]
    contexts = [
        SampleElutionContext(
            sample,
            500.0,
            8.0,
            15.0,
            tuple(ElutionLandmark(rt, height, 0.25) for rt, height in peaks),
        )
        for sample, peaks in (
            ("a", ((10.0, 60.0), (10.7, 100.0))),
            ("b", ((10.05, 60.0), (10.75, 100.0))),
            ("x", ((isolated_rt, 100.0),)),
        )
    ]
    orders = {"a": 1, "b": 2, "x": 3}
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        bootstrap=bootstrap,
        peak_trajectories=resolve_peak_trajectories(
            owners, contexts, config=AlignmentConfig(), injection_order=orders
        ),
        rules=CID_NL_GROUPING_RULES,
    )
    (x_group,) = (group for group in result.groups if "x:only" in group.owner_ids)
    if bootstrap or isolated_rt == 12.4:
        assert x_group.owner_ids == ("x:only",)
    else:
        assert set(x_group.owner_ids) == {"a:late", "b:late", "x:only"}
        assert "low_rt_support" in next(
            d.accepted_risk.risk_flags
            for d in result.sparse_decisions
            if d.owner_id == "x:only"
        )
    assert x_group.reason != "ordered_ms1_pattern_links_existing_owners"


@pytest.mark.parametrize("case", sorted(_DATA["cases"]))
def test_full85_bands_preserve_coherent_members_and_separate_rt_outlier(
    case: str,
) -> None:
    owners, orders = _owners(case)
    old = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})),
        bootstrap=True,
        rules=CID_NL_GROUPING_RULES,
    )
    assert not any(
        group.disposition == "assigned" and len(group.owner_ids) == len(owners)
        for group in old.groups
    ), "the fixture must expose the old split"

    result = infer_feature_memberships(
        tuple(reversed(owners)),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=(orders or {})).with_warp(_warp()),
        rules=CID_NL_GROUPING_RULES,
    )
    for group in result.groups:
        stems = [
            owner.sample_stem for owner in owners if owner.owner_id in group.owner_ids
        ]
        assert len(stems) == len(set(stems))
    if case == "special_334069":
        required = {
            owner.owner_id for owner in owners if orders[owner.sample_stem] != 82
        }
        assert len(required) == 70
        assert any(
            group.disposition == "assigned" and required <= set(group.owner_ids)
            for group in result.groups
        )
    elif case == "tier1_03":
        # The frozen common frame places this owner 61.88 seconds before the
        # other 62 owners, whose complete range is only 20.01 seconds.
        assert {frozenset(group.owner_ids) for group in result.groups} == {
            frozenset(("tier1_03:54",)),
            frozenset(
                owner.owner_id for owner in owners if owner.owner_id != "tier1_03:54"
            ),
        }
    else:
        assert len(result.groups) == 1
        assert result.groups[0].disposition == "assigned"
        assert set(result.groups[0].owner_ids) == {owner.owner_id for owner in owners}


def test_measured_warp_has_the_short_method_profile() -> None:
    warp = _warp()
    affected = (
        "Breast_Cancer_Tissue_pooled_QC2",
        "Breast_Cancer_Tissue_pooled_QC3",
        "Breast_Cancer_Tissue_pooled_QC_4",
        "Breast_Cancer_Tissue_pooled_QC5",
        "Breast_Cancer_Tissue_pooled_QC6",
        "Breast_Cancer_Tissue_pooled_QC7",
        "TumorBC2312_DNA",
        "BenignfatBC1116_DNA",
    )
    for sample in affected:
        assert warp.offset_min(sample, 7.0) * 60.0 < -25.0
        assert abs(warp.offset_min(sample, 27.0) * 60.0) < 8.0
    for sample in ("Breast_Cancer_Tissue_pooled_QC1", "BenignfatBC0979_DNA"):
        assert warp.offset_min(sample, 7.0) * 60.0 > -20.0


def test_estimated_warp_recovers_an_injected_early_shift() -> None:
    orders = {f"s{order:02d}": order for order in range(1, 21)}
    groups = []
    for index, rt_min in enumerate(
        (
            *[7.0 + index / 10.0 for index in range(18)],
            *[27.0 + index / 10.0 for index in range(18)],
        )
    ):
        groups.append(
            tuple(
                _synthetic_owner(
                    sample,
                    f"anchor-{index}-{sample}",
                    rt_min - (0.5 if sample == "s10" and rt_min < 10.0 else 0.0),
                )
                for sample in orders
            )
        )
    calibration = BatchRtCalibrationAccumulator(
        sample_stems=tuple(orders), sample_cohorts=dict.fromkeys(orders, "batch")
    )
    calibration.add_groups(groups)
    warp = calibration.freeze()
    assert len(warp.knots) == 36
    assert warp.offset_min("s10", 7.0) * 60.0 < -25.0
    assert abs(warp.offset_min("s10", 27.0) * 60.0) < 8.0
    for sample in ("s01", "s20"):
        assert abs(warp.offset_min(sample, 7.0) * 60.0) < 8.0


def test_nearby_mass_outlier_cannot_cut_a_clean_band() -> None:
    owners, orders = _owners("tier1_06")
    assert owners[0].primary_identity_event is not None
    outlier_event = replace(
        owners[0].primary_identity_event,
        candidate_id="mass-window-outlier",
        sample_stem="mass-window-outlier",
        raw_file="mass-window-outlier.raw",
        precursor_mz=266.097693,
        product_mz=150.050293,
    )
    outlier = replace(
        owners[0],
        owner_id="mass-window-outlier",
        sample_stem="mass-window-outlier",
        raw_file="mass-window-outlier.raw",
        precursor_mz=266.097693,
        owner_apex_rt=30.0,
        primary_identity_event=outlier_event,
    )
    result = infer_feature_memberships(
        (*owners, outlier),
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(
            injection_order=({**orders, "mass-window-outlier": 0})
        ).with_warp(_warp()),
        rules=CID_NL_GROUPING_RULES,
    )
    assert any(
        group.disposition == "assigned"
        and set(group.owner_ids) == {owner.owner_id for owner in owners}
        for group in result.groups
    )


def test_local_trend_does_not_override_full85_rt_population_separation() -> None:
    owners, orders = _owners("tier1_03")
    shifted_sample = "BenignfatBC1025_DNA"
    warp = _warp()
    shifted = BatchRtWarp(
        tuple(
            (
                sample,
                tuple(
                    (rt, offset + (0.1 if sample == shifted_sample else 0.0))
                    for rt, offset in knots
                ),
            )
            for sample, knots in warp.knots_by_sample
        ),
        warp.anchor_group_count,
    )
    result = infer_feature_memberships(
        owners,
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(injection_order=(orders or {})).with_warp(shifted),
        rules=CID_NL_GROUPING_RULES,
    )
    by_id = {owner.owner_id: owner.sample_stem for owner in owners}
    assert {
        frozenset(by_id[key] for key in group.owner_ids) for group in result.groups
    } == {
        frozenset((shifted_sample,)),
        frozenset(
            owner.sample_stem for owner in owners if owner.sample_stem != shifted_sample
        ),
    }


def test_equal_band_competition_selects_and_records_the_alternative() -> None:
    def make(sample: str, name: str, rt: float) -> SampleLocalMS1Owner:
        event = IdentityEvent(
            candidate_id=name,
            sample_stem=sample,
            raw_file=f"{sample}.raw",
            neutral_loss_tag="DNA_dR",
            precursor_mz=500.0,
            product_mz=383.9526,
            observed_neutral_loss_da=116.0474,
            seed_rt=rt,
            evidence_score=90,
            seed_event_count=3,
        )
        return SampleLocalMS1Owner(
            owner_id=name,
            sample_stem=sample,
            raw_file=f"{sample}.raw",
            precursor_mz=500.0,
            owner_apex_rt=rt,
            owner_peak_start_rt=rt - 0.05,
            owner_peak_end_rt=rt + 0.05,
            owner_area=1000.0,
            owner_height=100.0,
            primary_identity_event=event,
            supporting_events=(),
            identity_conflict=False,
            assignment_reason="same_apex_window",
        )

    left = make("a", "left", 10.0)
    right = make("a", "right", 11.0)
    bridge = make("b", "bridge", 10.5)
    result = infer_feature_memberships(
        (left, bridge, right),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=({"a": 1, "b": 2})),
        rules=CID_NL_GROUPING_RULES,
    )
    assert sorted(len(g.owner_ids) for g in result.groups) == [1, 2]
    assert any(
        decision.owner_id == bridge.owner_id
        and decision.state == "assigned"
        and "accepted_competition" in decision.accepted_risk.risk_flags
        and decision.accepted_risk.equivalent_alternatives
        for decision in result.sparse_decisions
    )


def test_sparse_feature_uses_warp_only_across_distant_injections() -> None:
    def make(sample: str, order: int, rt: float) -> SampleLocalMS1Owner:
        event = IdentityEvent(
            candidate_id=sample,
            sample_stem=sample,
            raw_file=f"{sample}.raw",
            neutral_loss_tag="DNA_dR",
            precursor_mz=500.0,
            product_mz=383.9526,
            observed_neutral_loss_da=116.0474,
            seed_rt=rt,
            evidence_score=90,
            seed_event_count=3,
        )
        return SampleLocalMS1Owner(
            owner_id=sample,
            sample_stem=sample,
            raw_file=f"{sample}.raw",
            precursor_mz=500.0,
            owner_apex_rt=rt,
            owner_peak_start_rt=rt - 0.05,
            owner_peak_end_rt=rt + 0.05,
            owner_area=1000.0,
            owner_height=100.0,
            primary_identity_event=event,
            supporting_events=(),
            identity_conflict=False,
            assignment_reason="same_apex_window",
        )

    owners = (make("first", 1, 10.0), make("middle", 31, 10.2), make("last", 61, 10.4))
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({"first": 1, "middle": 31, "last": 61})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert len(result.groups) == 1
    assert result.groups[0].disposition == "assigned"


def test_one_sample_doublet_does_not_split_the_other_seventy_nine() -> None:
    clean = tuple(
        _synthetic_owner(f"s{order:02d}", f"clean-{order}", 10.0)
        for order in range(1, 81)
    )
    doublet = _synthetic_owner("s40", "doublet", 10.05)
    result = infer_feature_memberships(
        (*clean, doublet),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({f"s{order:02d}": order for order in range(1, 81)})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert any(
        group.disposition == "assigned"
        and len(group.owner_ids) >= 79
        and set(group.owner_ids) <= {owner.owner_id for owner in clean}
        for group in result.groups
    )
    assert any(
        group.disposition == "undecided" and "doublet" in group.owner_ids
        for group in result.groups
    )


@pytest.mark.parametrize("reverse", (False, True))
def test_mass_resolved_coeluting_bands_survive_smooth_rt_drift(reverse: bool) -> None:
    # Distinct measured inputs remain distinct; this does not decide whether
    # two method subjects describe the same chemical compound.
    bands = tuple(
        tuple(
            _synthetic_owner(
                f"s{order:02d}",
                f"{label}-{order}",
                18.3 + 1.3 * (order - 1) / 84,
                precursor_mz=mz,
                product_mz=137.065,
            )
            for order in range(1, 86)
        )
        for label, mz in (("lower", 253.106354), ("upper", 253.111792))
    )
    owners = tuple(owner for band in bands for owner in band)
    result = infer_feature_memberships(
        tuple(reversed(owners)) if reverse else owners,
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(
            injection_order=({f"s{order:02d}": order for order in range(1, 86)})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(owner.owner_id for owner in band) for band in bands
    }
    assert all(group.disposition == "assigned" for group in result.groups)


@pytest.mark.parametrize("occupy_early_band", (False, True))
@pytest.mark.parametrize("reverse", (False, True))
def test_singleton_ambiguity_uses_remaining_band_choices(
    occupy_early_band: bool, reverse: bool
) -> None:
    orders = {"probe": 1, "a": 2, "b": 3, "distant": 20}
    owners = tuple(
        _synthetic_owner(sample, f"{band}-{sample}", rt)
        for band, rt in (("early", 10.0), ("late", 10.18))
        for sample in ("a", "b", "distant")
    ) + (_synthetic_owner("probe", "between", 10.09),)
    if occupy_early_band:
        owners += (_synthetic_owner("probe", "early-probe", 10.0),)
    # Pairwise uncertainty at the distant injection makes the probe peaks
    # compete. Once early-probe joins the early band, only the late band can
    # admit between. Without that occupied slot, both choices remain viable.
    result = infer_feature_memberships(
        tuple(reversed(owners)) if reverse else owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=orders),
        rules=CID_NL_GROUPING_RULES,
    )
    early = {f"early-{sample}" for sample in ("a", "b", "distant")}
    late = {f"late-{sample}" for sample in ("a", "b", "distant")}
    if occupy_early_band:
        early.add("early-probe")
        late.add("between")
    else:
        early.add("between")
    expected = {frozenset(early), frozenset(late)}
    assert {frozenset(group.owner_ids) for group in result.groups} == expected
    assert all(
        group.disposition == ("assigned" if len(group.owner_ids) > 1 else "undecided")
        for group in result.groups
    )
    between = next(
        decision
        for decision in result.sparse_decisions
        if decision.owner_id == "between"
    )
    assert between.state == "assigned"
    if not occupy_early_band:
        assert "accepted_competition" in between.accepted_risk.risk_flags


def test_ambiguous_distant_pair_does_not_erase_independent_local_support() -> None:
    local = tuple(
        _synthetic_owner(
            sample, sample, 10.0, precursor_mz=253.106354, product_mz=137.065
        )
        for sample in ("first", "second")
    )
    distant = (
        _synthetic_owner(
            "later",
            "lower",
            10.0,
            precursor_mz=253.106354,
            product_mz=137.06618,
        ),
        _synthetic_owner(
            "later",
            "upper",
            10.0,
            precursor_mz=253.111792,
            product_mz=137.065,
        ),
    )
    result = infer_feature_memberships(
        (*local, *distant),
        config=CID_NL_ALIGNMENT_CONFIG,
        rt_frame=SampleRtFrame(
            injection_order=({"first": 1, "second": 2, "later": 17})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert any(
        group.disposition == "assigned"
        and {owner.owner_id for owner in local} <= set(group.owner_ids)
        for group in result.groups
    )
    # A choice is now made, without admitting both same-sample candidates.
    assert sorted(len(group.owner_ids) for group in result.groups) == [1, 3]
    assert all(
        len(set(group.owner_ids) & {o.owner_id for o in distant}) <= 1
        for group in result.groups
    )


@pytest.mark.parametrize("spacing", (17, 35))
@pytest.mark.parametrize("reverse", (False, True))
def test_distant_samples_form_distinct_bands_in_a_crowded_mass_window(
    spacing: int, reverse: bool
) -> None:
    # Nearby peaks inflate local detection density; none of the samples has
    # a neighbor within 16 injections from which to establish a band first.
    orders = {f"s{i}": 1 + i * spacing for i in range(4)}
    owners = tuple(
        _synthetic_owner(sample, f"{band}-{sample}", rt + i * 0.01)
        for band, rt in (("early", 9.0), ("middle", 10.0), ("late", 11.0))
        for i, sample in enumerate(orders)
    )
    result = infer_feature_memberships(
        tuple(reversed(owners)) if reverse else owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=orders),
        rules=CID_NL_GROUPING_RULES,
    )
    assert {frozenset(g.owner_ids) for g in result.groups} == {
        frozenset(f"{band}-{sample}" for sample in orders)
        for band in ("early", "middle", "late")
    }
    assert all(g.disposition == "assigned" for g in result.groups)


def test_sparse_band_remains_warp_only_beside_an_unrelated_dense_band() -> None:
    sparse_orders = (1, 21, 41, 61, 81)
    sparse = tuple(
        _synthetic_owner(f"s{order:02d}", f"sparse-{order}", 10.0 + order / 1200.0)
        for order in sparse_orders
    )
    dense = tuple(
        _synthetic_owner(f"s{order:02d}", f"dense-{order}", 30.0)
        for order in range(1, 31)
    )
    result = infer_feature_memberships(
        (*sparse, *dense),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({f"s{order:02d}": order for order in range(1, 82)})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert any(
        group.disposition == "assigned"
        and set(group.owner_ids) == {owner.owner_id for owner in sparse}
        for group in result.groups
    )


@pytest.mark.parametrize("late_shift_min", (0.0, 0.8))
@pytest.mark.parametrize("different_shape", (False, True))
def test_dense_band_survives_a_long_absence_in_injection_order(
    late_shift_min: float,
    different_shape: bool,
) -> None:
    orders = (*range(1, 8), *range(35, 42))
    owners = tuple(
        _synthetic_owner(
            f"s{order:02d}",
            f"same-{order}",
            10.0 + order / 6000.0 + (late_shift_min if order >= 35 else 0.0),
        )
        for order in orders
    )
    offsets = np.linspace(-0.6, 0.6, 121)
    owners = tuple(
        replace(
            owner,
            peak_shape=prepare_local_peak_shape(
                offsets + owner.owner_apex_rt,
                3 - offsets
                if different_shape and order >= 35
                else np.exp(-0.5 * (offsets / 0.07) ** 2),
                apex_rt_min=owner.owner_apex_rt,
            ),
        )
        for owner, order in zip(owners, orders, strict=True)
    )
    warp = BatchRtWarp(
        tuple(
            (f"s{order:02d}", ((7.0, late_shift_min), (11.0, late_shift_min)))
            for order in range(35, 42)
        ),
        0,
    )
    result = infer_feature_memberships(
        tuple(reversed(owners)),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({f"s{order:02d}": order for order in range(1, 42)})
        ).with_warp(warp),
        rules=CID_NL_GROUPING_RULES,
    )

    assert all(group.disposition == "assigned" for group in result.groups)
    assert len(result.groups) == 1
    assert set(result.groups[0].owner_ids) == {owner.owner_id for owner in owners}
    if different_shape:
        assert all(
            "shape_disagreement" in d.accepted_risk.soft_contradictions
            for d in result.sparse_decisions
        )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ordered_chain", [False, True])
def test_separated_band_bridge_respects_indirect_peak_order(
    reverse: bool,
    ordered_chain: bool,
) -> None:
    # A and C never co-occur. A < B < C forbids their union; A < B > C
    # does not. Keep A/C less than 20 seconds apart to isolate physical order
    # from the independent common-RT population constraint.
    middle_rt = 10.15 if ordered_chain else 10.5
    owners = tuple(
        _synthetic_owner(f"{band}{i}", f"{band}{i}", rt)
        for band, rt in (("a", 10.0), ("b", middle_rt), ("c", 10.3))
        for i in (1, 2)
    )
    tracks = (
        PeakTrajectory(
            ("a1", "a2"),
            (
                ("a1", 10.0),
                ("a2", 10.0),
                ("left_context", 10.0),
            ),
        ),
        PeakTrajectory(
            ("b1", "b2"),
            (
                ("b1", middle_rt),
                ("b2", middle_rt),
                ("left_context", middle_rt),
                ("right_context", middle_rt),
            ),
        ),
        PeakTrajectory(
            ("c1", "c2"),
            (
                ("c1", 10.3),
                ("c2", 10.3),
                ("right_context", 10.3),
            ),
        ),
    )
    orders = {"a1": 1, "a2": 2, "b1": 40, "b2": 41, "c1": 100, "c2": 101}
    result = infer_feature_memberships(
        tuple(reversed(owners)) if reverse else owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=orders).with_warp(
            BatchRtWarp(tuple((s, ((0.0, 0.0), (20.0, 0.0))) for s in orders), 12)
        ),
        peak_trajectories=PeakTrajectories(
            tuple(reversed(tracks)) if reverse else tracks,
            physical_order=(
                ("a1", "b1"),
                ("b1", "c1") if ordered_chain else ("c1", "b1"),
            ),
        ),
        rules=CID_NL_GROUPING_RULES,
    )
    expected = {frozenset({"b1", "b2"})}
    if ordered_chain:
        expected.update((frozenset({"a1", "a2"}), frozenset({"c1", "c2"})))
    else:
        expected.add(frozenset({"a1", "a2", "c1", "c2"}))
    assert {frozenset(g.owner_ids) for g in result.groups} == expected
    assert all(g.disposition == "assigned" for g in result.groups)


def test_long_gap_and_cross_sample_region_difference_are_costs_not_vetoes() -> None:
    early = tuple(
        _synthetic_owner(f"s{order:02d}", f"early-{order}", 10.0)
        for order in range(1, 8)
    )
    late = tuple(
        _synthetic_owner(f"s{order:02d}", f"late-{order}", 11.25)
        for order in range(35, 42)
    )
    result = infer_feature_memberships(
        (*early, *late),
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({f"s{order:02d}": order for order in range(1, 42)})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )

    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(owner.owner_id for owner in (*early, *late)),
    }
    assert all(group.disposition == "assigned" for group in result.groups)
    assert all(
        "region_disagreement" in d.accepted_risk.soft_contradictions
        for d in result.sparse_decisions
    )


def test_product_scatter_is_scored_within_the_precursor_search_window() -> None:
    owners = (
        _synthetic_owner("first", "first", 10.0),
        _synthetic_owner("second", "second", 10.0, product_mz=383.973),
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(injection_order=({"first": 1, "second": 2})).with_warp(
            BatchRtWarp((), 0)
        ),
        rules=CID_NL_GROUPING_RULES,
    )
    assert len(result.groups) == 1
    assert result.groups[0].disposition == "assigned"
    assert set(result.groups[0].owner_ids) == {"first", "second"}


def test_precursor_lookup_bound_is_independent_of_injection_order() -> None:
    owners = (
        _synthetic_owner("first", "high", 10.0, precursor_mz=500.04),
        _synthetic_owner("middle", "bridge", 30.0, precursor_mz=500.02),
        _synthetic_owner("last", "low", 10.0),
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        rt_frame=SampleRtFrame(
            injection_order=({"first": 1, "middle": 2, "last": 3})
        ).with_warp(BatchRtWarp((), 0)),
        rules=CID_NL_GROUPING_RULES,
    )
    assert all(group.disposition == "undecided" for group in result.groups)
