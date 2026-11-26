"""Replay a drift-supported CID-NL row alongside ordinary residual groups."""

from __future__ import annotations

from statistics import median

from xic_extractor.alignment.cid_nl_drift_band import BandSlot, assign_drift_band
from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES
from xic_extractor.alignment.cross_sample.peak_groups import (
    CrossSamplePeakGroupHypothesis,
    construct_cross_sample_peak_group_hypotheses,
    hypothesis_from_membership,
)
from xic_extractor.alignment.feature_membership_inference import (
    FeatureMembershipGroup,
    SparseFeatureDecision,
)
from xic_extractor.alignment.grouping_experiment import GroupingInputs
from xic_extractor.alignment.grouping_workflow import reject_shoulder_groups
from xic_extractor.alignment.peak_trajectories import resolve_peak_trajectories
from xic_extractor.alignment.rt_drift import DriftCoefficients
from xic_extractor.alignment.rt_frame import SampleRtFrame
from xic_extractor.mass_accuracy import within_ppm


def regroup_channel(
    inputs: GroupingInputs,
) -> tuple[tuple[BandSlot, ...], tuple[CrossSamplePeakGroupHypothesis, ...]]:
    """Use the frozen RT model; do not refit it with the proposed replacements."""
    if len(inputs.worksets) != 1:
        raise ValueError("supply one complete isolated workset")
    (w,) = inputs.worksets
    cfg, owners = inputs.alignment_config, w.owners
    if w.rt_calibration is None:
        raise ValueError("the replay needs retained sample RT calibration")
    events = [
        o.primary_identity_event for o in owners if o.primary_identity_event is not None
    ]
    if (
        w.known_identity_bindings
        or any(o.cid_nl_composite_assessment is not None for o in owners)
        or len({e.neutral_loss_tag for e in events}) > 1
    ):
        raise ValueError(
            "supply a scalar CID-NL channel without known identity bindings"
        )
    for values, ppm in (
        ([o.peak.mz for o in owners], cfg.precursor_mz_tolerance_ppm),
        ([e.product_mz for e in events], cfg.product_mz_tolerance_ppm),
    ):
        if values:
            center = median(values)
            if any(not within_ppm(v, center, ppm) for v in values):
                raise ValueError("mixed mass window: cannot interpret as one channel")
    anchor = (
        next((a for a in w.rt_drift.anchors if a.channel == w.key), None)
        if w.rt_drift
        else None
    )
    slots = assign_drift_band(
        owners, inputs.sample_stems, w.rt_calibration, w.rt_drift, anchor
    )
    chosen = {s.selected: s for s in slots if s.selected is not None}
    ids = tuple(sorted(chosen))
    by_id = {o.owner_id: o for o in owners}
    fixed: tuple[CrossSamplePeakGroupHypothesis, ...] = ()
    if ids:
        basis = FeatureMembershipGroup(
            ids,
            "assigned",
            "window_level_rt_band",
            "corrected_cid_nl_reference_band",
            rt_drift=anchor.coefficients if anchor else DriftCoefficients(),
        )
        decisions = tuple(
            SparseFeatureDecision(
                "window",
                chosen[k].sample,
                k,
                f"window:{ids[0]}",
                "assigned",
                chosen[k].reason,
                rt_basis="drift_corrected",
            )
            for k in ids
        )
        fixed = (
            hypothesis_from_membership(
                tuple(by_id[k] for k in ids), basis, decisions, CID_NL_GROUPING_RULES
            ),
        )
    universe = frozenset(inputs.sample_stems)
    frame = (
        SampleRtFrame(injection_order=inputs.injection_order, sample_universe=universe)
        .with_warp(w.rt_warp)
        .with_sample_calibration(w.rt_calibration, w.rt_drift)
    )
    trajectories = resolve_peak_trajectories(
        owners,
        w.elution_contexts,
        config=cfg,
        injection_order=inputs.injection_order,
        rt_frame=frame,
    )
    residual = construct_cross_sample_peak_group_hypotheses(
        tuple(o for o in owners if o.owner_id not in chosen),
        accept_competition=True,
        config=cfg,
        sample_universe=universe,
        rt_frame=frame,
        peak_trajectories=trajectories,
        rules=CID_NL_GROUPING_RULES,
    )
    return slots, reject_shoulder_groups((*fixed, *residual), config=cfg)
