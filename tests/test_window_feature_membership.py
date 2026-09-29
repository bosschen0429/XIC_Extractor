"""Real full85 observations that exposed ordinary row fragmentation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
    estimate_batch_rt_warp,
    infer_feature_memberships,
)
from xic_extractor.alignment.ownership_models import IdentityEvent, SampleLocalMS1Owner
from xic_extractor.alignment.untargeted_cid_nl_golden_path import (
    CID_NL_ALIGNMENT_CONFIG,
)

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


@pytest.mark.parametrize("case", sorted(_DATA["cases"]))
def test_full85_clean_band_is_one_assigned_group(case: str) -> None:
    owners, orders = _owners(case)
    old = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        injection_order=orders,
        drift_lookup=None,
        bootstrap=True,
    )
    assert not any(
        group.disposition == "assigned" and len(group.owner_ids) == len(owners)
        for group in old.groups
    ), "the fixture must expose the old split"

    result = infer_feature_memberships(
        tuple(reversed(owners)),
        config=AlignmentConfig(),
        injection_order=orders,
        drift_lookup=None,
        rt_warp=_warp(),
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
    warp = estimate_batch_rt_warp(
        groups,
        owners_by_id={},
        injection_order=orders,
    )
    assert warp.anchor_group_count == 36
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
        injection_order={**orders, "mass-window-outlier": 0},
        drift_lookup=None,
        rt_warp=_warp(),
    )
    assert any(
        group.disposition == "assigned"
        and set(group.owner_ids) == {owner.owner_id for owner in owners}
        for group in result.groups
    )


def test_local_trend_recovers_full85_326_mz_owner_under_warp_uncertainty() -> None:
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
        injection_order=orders,
        drift_lookup=None,
        rt_warp=shifted,
    )
    assert len(result.groups) == 1
    assert result.groups[0].disposition == "assigned"
    assert len(result.groups[0].owner_ids) == 63


def test_equal_band_competition_marks_only_the_bridge_undecided() -> None:
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
        injection_order={"a": 1, "b": 2},
        drift_lookup=None,
    )
    assert len(result.groups) == 3
    assert all(group.disposition == "undecided" for group in result.groups)
    assert any(
        decision.owner_id == bridge.owner_id
        and decision.reason == "alternative_bands_have_near_equal_cost"
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
        injection_order={"first": 1, "middle": 31, "last": 61},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
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
        injection_order={f"s{order:02d}": order for order in range(1, 81)},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
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
        injection_order={f"s{order:02d}": order for order in range(1, 86)},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
    )
    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(owner.owner_id for owner in band) for band in bands
    }
    assert all(group.disposition == "assigned" for group in result.groups)


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
        injection_order={"first": 1, "second": 2, "later": 17},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
    )
    assert any(
        group.disposition == "assigned"
        and {owner.owner_id for owner in local} <= set(group.owner_ids)
        for group in result.groups
    )
    # The two later observations have near-equal costs for the one supported
    # band. Neither gains membership merely because the band now exists.
    for owner in distant:
        assert any(
            group.disposition == "undecided" and group.owner_ids == (owner.owner_id,)
            for group in result.groups
        )


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
        injection_order={f"s{order:02d}": order for order in range(1, 82)},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
    )
    assert any(
        group.disposition == "assigned"
        and set(group.owner_ids) == {owner.owner_id for owner in sparse}
        for group in result.groups
    )


@pytest.mark.parametrize("late_shift_min", (0.0, 0.8))
def test_dense_band_survives_a_long_absence_in_injection_order(
    late_shift_min: float,
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
        injection_order={f"s{order:02d}": order for order in range(1, 42)},
        drift_lookup=None,
        rt_warp=warp,
    )

    assert len(result.groups) == 1
    assert result.groups[0].disposition == "assigned"
    assert set(result.groups[0].owner_ids) == {owner.owner_id for owner in owners}


def test_long_gap_does_not_bridge_distinct_dense_rt_bands() -> None:
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
        injection_order={f"s{order:02d}": order for order in range(1, 42)},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
    )

    assert {frozenset(group.owner_ids) for group in result.groups} == {
        frozenset(owner.owner_id for owner in early),
        frozenset(owner.owner_id for owner in late),
    }
    assert all(group.disposition == "assigned" for group in result.groups)


def test_product_scatter_is_scored_within_the_precursor_search_window() -> None:
    owners = (
        _synthetic_owner("first", "first", 10.0),
        _synthetic_owner("second", "second", 10.0, product_mz=383.973),
    )
    result = infer_feature_memberships(
        owners,
        config=AlignmentConfig(),
        injection_order={"first": 1, "second": 2},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
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
        injection_order={"first": 1, "middle": 2, "last": 3},
        drift_lookup=None,
        rt_warp=BatchRtWarp((), 0),
    )
    assert all(group.disposition == "undecided" for group in result.groups)
