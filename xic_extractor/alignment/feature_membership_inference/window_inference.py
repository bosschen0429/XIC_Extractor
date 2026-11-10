"""Compose whole-window inference and emit group dispositions with owner decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.edge_scoring import RTEvidenceBasis
from xic_extractor.alignment.grouping_evidence import owner_uncertainty_reason
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import mz_tolerance_da
from xic_extractor.peak_detection.ms1_shape_identity import LocalPeakShapeComparisons

from .models import FeatureMembershipGroup, SparseFeatureDecision
from .trajectory_membership import _trajectory_seeds
from .window_bands import (
    _assign_window_singletons,
    _bridge_separated_window_bands,
    _join_window_bands,
    _NearestInjectionMedians,
)
from .window_edges import _ambiguous_window_edges, _window_candidate_edges


def _window_inference(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
    trajectories: PeakTrajectories,
) -> tuple[tuple[FeatureMembershipGroup, ...], tuple[SparseFeatureDecision, ...]]:
    """Find all RT bands in each mass neighbourhood in one finite pass.

    Raw ordered patterns can seed owners without measured RT coordinates.
    Calibrated bands use measured RT and mass, not forced pattern unions
    or zero-distance shortcuts. Every union keeps one physical owner per sample
    and distinct tracks apart.
    A feature's six nearest injections provide its RT trend once it has enough
    members; sparse bands rely on the batch warp with a wider RT allowance.
    After local bands settle, separated dense bands can bridge on whole-band
    evidence without treating missing injections as contrary identity evidence.
    """
    injection_order = rt_frame.injection_order
    if not owners:
        return (), ()
    samples = {owner.sample_stem for owner in owners}
    known_orders = {
        sample: order for sample, order in injection_order.items() if sample in samples
    }
    next_order = max(known_orders.values(), default=0) + 1
    orders = dict(known_orders)
    for sample in sorted(samples - known_orders.keys()):
        orders[sample] = next_order
        next_order += 1
    corrected_rt = {
        owner.owner_id: rt_frame.corrected_rt_min(owner) for owner in owners
    }
    common_rt = {
        owner.owner_id: rt
        for owner in owners
        if (
            rt := rt_frame.calibrated_rt_min(
                owner.sample_stem, owner.peak.integration.rt_apex_min
            )
        )
        is not None
    }
    trajectories = trajectories.common_frame_membership(common_rt)
    trajectory_seeds = _trajectory_seeds(
        tuple(owner for owner in owners if owner.owner_id not in common_rt),
        trajectories,
        config,
        rules=rules,
        rt_frame=rt_frame,
    )
    ordinary_by_id = {owner.owner_id: owner for owner in owners}
    track_indices = trajectories.owner_track_indices()
    weak = {
        owner.owner_id: reason
        for owner in owners
        if (reason := owner_uncertainty_reason(owner, config, rules=rules))
    }
    shape_conflicts: dict[str, set[str]] = defaultdict(set)
    eligible = tuple(owner for owner in owners if owner.owner_id not in weak)
    by_chemistry: dict[tuple[str, str], list[SampleLocalMS1Owner]] = defaultdict(list)
    for owner in eligible:
        by_chemistry[rules.chemistry_key(owner)].append(owner)

    final_components: list[tuple[str, ...]] = [(owner_id,) for owner_id in sorted(weak)]
    for chemistry in sorted(by_chemistry):
        sorted_owners = sorted(
            by_chemistry[chemistry],
            key=lambda owner: (owner.peak.mz, owner.owner_id),
        )
        windows: list[list[SampleLocalMS1Owner]] = []
        for owner in sorted_owners:
            # A fixed window start can cut a coherent band when an unrelated
            # lower-mass owner happens to arrive first. Keep local mass
            # neighbourhoods connected, then enforce tolerance per edge.
            if not windows or owner.peak.mz - windows[-1][-1].peak.mz > mz_tolerance_da(
                windows[-1][-1].peak.mz, config.max_ppm
            ):
                windows.append([])
            windows[-1].append(owner)
        for window in windows:
            shape_comparisons = LocalPeakShapeComparisons(
                owner.peak_shape
                for owner in window
                if owner.peak_shape is not None
                and rt_frame.calibrated_peak_region(owner) is None
            )
            counts["index_queries"] += len(window)
            counts["candidate_features_considered"] += len(window)
            members = {owner.owner_id: [owner] for owner in window}
            seeded_owners: set[str] = set()
            for seed in trajectory_seeds:
                retained = [
                    owner_id for owner_id in seed.owner_ids if owner_id in members
                ]
                if seed.disposition != "assigned" or len(retained) < 2:
                    continue
                seeded_owners.update(retained)
                first, *others = retained
                for owner_id in others:
                    members[first].extend(members.pop(owner_id))

            arcs = _window_candidate_edges(
                window,
                shape_comparisons=shape_comparisons,
                config=config,
                orders=orders,
                corrected_rt=corrected_rt,
                common_rt=common_rt,
                track_indices=track_indices,
                trajectories=trajectories,
                counts=counts,
                shape_conflicts=shape_conflicts,
                rules=rules,
                rt_frame=rt_frame,
            )
            counts["supported_candidate_arcs"] += len(arcs)
            ambiguous_window, ambiguous_arcs = _ambiguous_window_edges(
                arcs, ordinary_by_id, seeded_owners
            )
            for owner_id in ambiguous_window:
                weak[owner_id] = "alternative_bands_have_near_equal_cost"
            counts["ambiguous_assignments"] += len(ambiguous_window)
            _join_window_bands(
                members,
                [
                    arc
                    for arc in arcs
                    if arc.left_id not in ambiguous_window
                    and arc.right_id not in ambiguous_window
                    and (arc.left_id, arc.right_id) not in ambiguous_arcs
                    and (arc.right_id, arc.left_id) not in ambiguous_arcs
                ],
                shape_comparisons=shape_comparisons,
                config=config,
                rules=rules,
                rt_frame=rt_frame,
                trajectories=trajectories,
                orders=orders,
                corrected_rt=corrected_rt,
                common_rt=common_rt,
                shape_conflicts=shape_conflicts,
                counts=counts,
            )
            _assign_window_singletons(
                members,
                weak=weak,
                orders=orders,
                corrected_rt=corrected_rt,
                config=config,
                counts=counts,
                trajectories=trajectories,
                shape_conflicts=shape_conflicts,
                rules=rules,
                rt_frame=rt_frame,
            )
            if rt_frame.has_coordinates and len(known_orders) == len(samples):
                _bridge_separated_window_bands(
                    members,
                    weak=weak,
                    orders=orders,
                    corrected_rt=corrected_rt,
                    config=config,
                    counts=counts,
                    trajectories=trajectories,
                    rules=rules,
                    rt_frame=rt_frame,
                )
            final_components.extend(
                tuple(sorted(owner.owner_id for owner in group))
                for group in members.values()
            )

    groups, decisions = _window_membership_result(
        final_components,
        owner_by_id=ordinary_by_id,
        weak=weak,
        orders=orders,
        corrected_rt=corrected_rt,
        rt_basis="drift_corrected" if rt_frame.has_coordinates else "raw",
        shape_conflicts=shape_conflicts,
    )
    counts["samples_processed"] = len(samples)
    counts["births"] = len(groups)
    return groups, decisions


def _window_membership_result(
    components: Sequence[tuple[str, ...]],
    *,
    owner_by_id: Mapping[str, SampleLocalMS1Owner],
    weak: Mapping[str, str],
    orders: Mapping[str, int],
    corrected_rt: Mapping[str, float],
    rt_basis: RTEvidenceBasis,
    shape_conflicts: Mapping[str, set[str]],
) -> tuple[tuple[FeatureMembershipGroup, ...], tuple[SparseFeatureDecision, ...]]:
    """Complete group dispositions and their matching per-owner decisions."""
    groups = []
    decisions = []
    for owner_ids in sorted(components):
        group_owners = tuple(owner_by_id[owner_id] for owner_id in owner_ids)
        assigned = len(owner_ids) >= 2 and not any(
            owner_id in weak for owner_id in owner_ids
        )
        shape_rejected = (
            len(owner_ids) == 1
            and owner_ids[0] not in weak
            and bool(shape_conflicts.get(owner_ids[0]))
        )
        unassigned_reason = (
            "peak_correspondence_mismatch"
            if shape_rejected
            else weak.get(owner_ids[0], "no_supported_cross_sample_membership")
        )
        groups.append(
            FeatureMembershipGroup(
                owner_ids=owner_ids,
                disposition="assigned" if assigned else "undecided",
                basis="window_level_rt_band"
                if assigned
                else "insufficient_cross_sample_evidence",
                reason=(
                    "whole_window_rt_trend_supports_one_band"
                    if assigned
                    else unassigned_reason
                ),
            )
        )
        feature_key = f"window:{owner_ids[0]}"
        trends = _NearestInjectionMedians.prepare(group_owners, orders, corrected_rt)
        for owner in group_owners:
            witness = min(
                (item for item in group_owners if item.owner_id != owner.owner_id),
                key=lambda item: (
                    abs(orders[item.sample_stem] - orders[owner.sample_stem]),
                    item.owner_id,
                ),
                default=None,
            )
            trend = (
                trends.median(owner, exclude_owner_id=owner.owner_id)
                if witness is not None
                else None
            )
            corrected_delta = (
                abs(corrected_rt[owner.owner_id] - trend) * 60.0
                if trend is not None
                else None
            )
            decisions.append(
                SparseFeatureDecision(
                    direction="window",
                    sample_stem=owner.sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature_key,
                    state="assigned"
                    if assigned
                    else (
                        "proven_incompatible"
                        if shape_rejected
                        else "evaluated_undecided"
                    ),
                    reason=("window_level_rt_trend" if assigned else unassigned_reason),
                    witness_owner_ids=(witness.owner_id,)
                    if witness is not None
                    else tuple(sorted(shape_conflicts[owner.owner_id]))
                    if shape_rejected
                    else (),
                    selected_witness_owner_id=witness.owner_id
                    if witness is not None
                    else None,
                    rt_basis=rt_basis,
                    rt_raw_delta_sec=(
                        abs(
                            owner.peak.integration.rt_apex_min
                            - witness.peak.integration.rt_apex_min
                        )
                        * 60.0
                        if witness is not None
                        else None
                    ),
                    rt_drift_corrected_delta_sec=corrected_delta,
                )
            )
    return tuple(groups), tuple(decisions)
