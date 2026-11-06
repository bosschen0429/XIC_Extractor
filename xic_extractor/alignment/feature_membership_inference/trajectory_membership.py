"""Project measured peak trajectories into membership claims and seeds."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from itertools import combinations

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_evidence import (
    owner_uncertainty_reason,
    peak_correspondence_conflict,
)
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import within_ppm

from .feature_evidence import _feature_hard_reason, _FeatureTrack
from .models import FeatureMembershipGroup


def _membership_trajectories(
    owners: Sequence[SampleLocalMS1Owner],
    geometry: PeakTrajectories,
    config: AlignmentConfig,
    *,
    rules: GroupingRules,
) -> PeakTrajectories:
    """Withdraw only pattern membership claims contradicted by direct peaks.

    Two disjoint, co-observed method-approved owners can distinguish envelopes.
    If a track spans members compatible exclusively with opposite sides, that
    track cannot authorize a common Feature. Missing or ambiguous evidence is
    not a contradiction; RT distance and shape similarity do not decide it.

    This projection is private to inference: seeds, same-track cost and derived
    distinctness all consume it. The caller retains the original geometry and
    localization links for the finalized groups.
    """
    if not geometry.tracks:
        return geometry
    supported = {
        owner.owner_id: owner
        for owner in owners
        if rules.trajectory_witness_eligible(owner)
        and not owner_uncertainty_reason(owner, config, rules=rules)
    }
    by_sample_chemistry: dict[
        tuple[str, tuple[str, str]], list[SampleLocalMS1Owner]
    ] = defaultdict(list)
    for owner in supported.values():
        key = owner.sample_stem, rules.chemistry_key(owner)
        by_sample_chemistry[key].append(owner)
    witnesses = []
    for sample_owners in by_sample_chemistry.values():
        for first, second in combinations(sample_owners, 2):
            if not within_ppm(
                max(first.precursor_mz, second.precursor_mz),
                min(first.precursor_mz, second.precursor_mz),
                config.max_ppm,
            ):
                continue
            first_bounds = _selected_peak_bounds(first)
            second_bounds = _selected_peak_bounds(second)
            if first_bounds[1] < second_bounds[0] or second_bounds[1] < first_bounds[0]:
                witnesses.append((first.owner_id, second.owner_id))
    if not witnesses:
        return geometry

    # Prepare shared method envelopes once. Shape has its own membership gate;
    # it cannot disambiguate the two direct peaks for this witness rule.
    anchors = {
        owner_id: _FeatureTrack.from_owner(
            replace(supported[owner_id], peak_shape=None),
            direction="window",
            rules=rules,
        )
        for owner_id in {owner_id for pair in witnesses for owner_id in pair}
    }
    compatibility: dict[tuple[str, str], bool] = {}

    def compatible(member_id: str, anchor_id: str) -> bool:
        key = member_id, anchor_id
        if key not in compatibility:
            compatibility[key] = (
                member_id == anchor_id
                or not _feature_hard_reason(
                    supported[member_id], anchors[anchor_id], config
                )[0]
            )
        return compatibility[key]

    retained = []
    for track in geometry.tracks:
        members = tuple(
            owner_id for owner_id in track.owner_ids if owner_id in supported
        )
        contradicted = False
        if len(members) >= 2:
            for first_id, second_id in witnesses:
                sides = {
                    (compatible(member_id, first_id), compatible(member_id, second_id))
                    for member_id in members
                }
                if (True, False) in sides and (False, True) in sides:
                    contradicted = True
                    break
        if not contradicted:
            retained.append(track)
    if len(retained) == len(geometry.tracks):
        return geometry
    return geometry.membership_subset(retained)


def _selected_peak_bounds(owner: SampleLocalMS1Owner) -> tuple[float, float]:
    integration = owner.selected_integration
    if integration is not None:
        return integration.rt_left_min, integration.rt_right_min
    return owner.owner_peak_start_rt, owner.owner_peak_end_rt


def _trajectory_seeds(
    owners: Sequence[SampleLocalMS1Owner],
    correspondence: PeakTrajectories | None,
    config: AlignmentConfig,
    *,
    rt_frame: RtFrame,
    rules: GroupingRules,
) -> tuple[FeatureMembershipGroup, ...]:
    if correspondence is None:
        return ()
    by_id = {owner.owner_id: owner for owner in owners}
    groups = []
    for context in correspondence.tracks:
        members = [
            by_id[owner_id] for owner_id in context.owner_ids if owner_id in by_id
        ]
        if not members:
            continue
        if (
            len({rules.chemistry_key(member) for member in members}) != 1
            or len({member.sample_stem for member in members}) != len(members)
            or not within_ppm(
                max(member.precursor_mz for member in members),
                min(member.precursor_mz for member in members),
                config.max_ppm,
            )
            or any(
                peak_correspondence_conflict(
                    (member,),
                    members[:index],
                    config,
                    trajectories=correspondence,
                    rt_frame=rt_frame,
                )
                is not None
                for index, member in enumerate(members)
            )
        ):
            continue
        owner_ids = tuple(member.owner_id for member in members)
        assigned = len(members) >= 2 and not any(
            owner_uncertainty_reason(member, config, rules=rules) for member in members
        )
        reason = (
            "ordered_ms1_pattern_links_existing_owners"
            if assigned
            else "pattern_location_does_not_supply_cross_sample_method_support"
        )
        groups.append(
            FeatureMembershipGroup(
                owner_ids=owner_ids,
                disposition="assigned" if assigned else "undecided",
                basis="peak_trajectory",
                reason=reason,
            )
        )
    return tuple(groups)
