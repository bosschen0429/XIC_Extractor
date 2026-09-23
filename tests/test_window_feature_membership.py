"""Real full85 observations that exposed ordinary row fragmentation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.feature_membership_inference import (
    BatchRtWarp,
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
