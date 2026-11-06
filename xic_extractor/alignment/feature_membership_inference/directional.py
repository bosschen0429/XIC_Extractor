"""Grow indexed feature tracks in one injection direction."""

from __future__ import annotations

from bisect import bisect_left, bisect_right, insort
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.grouping_evidence import (
    owner_uncertainty_reason,
    peak_correspondence_conflict,
)
from xic_extractor.alignment.grouping_rules import GroupingRules
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.peak_trajectories import PeakTrajectories
from xic_extractor.alignment.rt_frame import RtFrame
from xic_extractor.mass_accuracy import mz_window

from .feature_evidence import (
    _compare_owner_to_feature,
    _feature_rt_may_support,
    _FeatureTrack,
)
from .models import DecisionDirection, SparseDecisionState, SparseFeatureDecision
from .sample_assignment import _Candidate, _solve_sample_assignment


class _FeatureIndex:
    """Range index over feature extrema; exact envelopes are checked afterward."""

    def __init__(self) -> None:
        self._axes: dict[tuple[str, str], list[tuple[float, str]]] = defaultdict(list)

    def add(self, track: _FeatureTrack) -> None:
        for key, value in self._entries(track):
            insort(self._axes[key], (value, track.key))

    def extend(
        self, track: _FeatureTrack, owner: SampleLocalMS1Owner, counts: Counter[str]
    ) -> None:
        previous = dict(self._entries(track))
        track.add(owner)
        current = dict(self._entries(track))
        for key in previous.keys() | current.keys():
            if previous.get(key) == current.get(key):
                counts["index_axis_updates_avoided"] += 1
                continue
            counts["index_axis_updates"] += 1
            items = self._axes[key]
            if key in previous:
                item = (previous[key], track.key)
                index = bisect_left(items, item)
                if index < len(items) and items[index] == item:
                    items.pop(index)
            if key in current:
                insort(items, (current[key], track.key))

    def candidates(
        self,
        owner: SampleLocalMS1Owner,
        *,
        rules: GroupingRules,
        config: AlignmentConfig,
        counts: Counter[str],
    ) -> set[str]:
        candidates: set[str] = set()
        for queries in rules.index_query_alternatives(owner, config):
            matches: set[str] | None = None
            for key, value, tolerance in queries:
                axis_matches = self._matches(key, value, tolerance, counts)
                matches = (
                    axis_matches
                    if matches is None
                    else matches.intersection(axis_matches)
                )
                if not matches:
                    break
            candidates.update(matches or ())
        return candidates

    def _matches(
        self,
        key: tuple[str, str],
        value: float,
        tolerance: float,
        counts: Counter[str],
    ) -> set[str]:
        items = self._axes.get(key, ())
        lower, upper = mz_window(value, tolerance)
        first = bisect_left(items, (lower, ""))
        last = bisect_right(items, (upper, chr(0x10FFFF)))
        counts["index_entries_scanned"] += last - first
        return {feature_key for _, feature_key in items[first:last]}

    @staticmethod
    def _entries(track: _FeatureTrack) -> tuple[tuple[tuple[str, str], float], ...]:
        return track.method.index_entries(track.precursor_min)


@dataclass(frozen=True, slots=True)
class _DirectionalResult:
    groups: tuple[tuple[str, ...], ...]
    decisions: tuple[SparseFeatureDecision, ...]
    owner_uncertainty: frozenset[str]


def _directional_inference(
    owners: tuple[SampleLocalMS1Owner, ...],
    *,
    rules: GroupingRules,
    direction: DecisionDirection,
    config: AlignmentConfig,
    rt_frame: RtFrame,
    counts: Counter[str],
    seed_by_owner: Mapping[str, int],
    trajectories: PeakTrajectories,
) -> _DirectionalResult:
    """Grow features in one injection direction from ordered-pattern seeds.

    An assigned pattern seed is one union: its first member processed in this
    direction competes like any owner, and later members join that member's
    feature. Unbound owners can extend the feature; owners bound to distinct
    trajectories cannot share one.
    """
    injection_order = rt_frame.injection_order
    counts["directional_passes"] += 1
    by_sample: dict[str, list[SampleLocalMS1Owner]] = defaultdict(list)
    for owner in owners:
        by_sample[owner.sample_stem].append(owner)
    sample_stems = sorted(
        by_sample,
        key=lambda sample: _sample_sort_key(sample, injection_order),
        reverse=direction == "reverse",
    )
    tracks: dict[str, _FeatureTrack] = {}
    index = _FeatureIndex()
    feature_members: dict[str, list[str]] = defaultdict(list)
    seed_feature: dict[int, str] = {}
    owner_feature: dict[str, str] = {}
    decisions: list[SparseFeatureDecision] = []
    uncertain: set[str] = set()

    def place(owner_id: str, feature_key: str) -> None:
        owner_feature[owner_id] = feature_key
        feature_members[feature_key].append(owner_id)
        seed = seed_by_owner.get(owner_id)
        if seed is not None:
            seed_feature.setdefault(seed, feature_key)

    for sample_stem in sample_stems:
        counts["samples_processed"] += 1
        sample_owners = tuple(
            sorted(by_sample[sample_stem], key=lambda item: item.owner_id)
        )
        # Seed members go first, so the sample's other owners see that occupancy.
        seeded_rows = tuple(
            owner
            for owner in sample_owners
            if seed_by_owner.get(owner.owner_id) in seed_feature
        )
        for owner in seeded_rows:
            feature_key = seed_feature[seed_by_owner[owner.owner_id]]
            track = tracks[feature_key]
            if peak_correspondence_conflict(
                (owner,),
                track.members,
                config,
                rt_frame=rt_frame,
            ):
                # Pattern agreement cannot override a contradictory measured
                # region or shape introduced by an intervening member.
                continue
            decisions.append(
                SparseFeatureDecision(
                    direction=direction,
                    sample_stem=sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature_key,
                    state="assigned",
                    reason="ordered_ms1_pattern_links_existing_owners",
                    witness_owner_ids=tuple(
                        witness.owner_id for witness in track.witness_owners
                    ),
                )
            )
            index.extend(track, owner, counts)
            place(owner.owner_id, feature_key)
        supported_rows: list[SampleLocalMS1Owner] = []
        candidates_by_owner: dict[str, tuple[_Candidate, ...]] = {}
        for owner in sample_owners:
            if owner.owner_id in owner_feature:
                continue
            weak_reason = owner_uncertainty_reason(owner, config, rules=rules)
            if weak_reason:
                feature_key = f"{direction}:{owner.owner_id}"
                place(owner.owner_id, feature_key)
                uncertain.add(owner.owner_id)
                counts["births"] += 1
                decisions.append(
                    SparseFeatureDecision(
                        direction=direction,
                        sample_stem=sample_stem,
                        owner_id=owner.owner_id,
                        feature_key=feature_key,
                        state="evaluated_undecided",
                        reason=weak_reason,
                    )
                )
                continue
            supported_rows.append(owner)
            counts["index_queries"] += 1
            feature_keys = index.candidates(
                owner, config=config, counts=counts, rules=rules
            )
            feasible_keys = {
                key
                for key in feature_keys
                if _feature_rt_may_support(owner, tracks[key], config, rt_frame)
                and trajectories.permits_membership(
                    (owner.owner_id, *feature_members[key])
                )
            }
            counts["rt_candidate_features_pruned"] += len(feature_keys) - len(
                feasible_keys
            )
            feature_keys = feasible_keys
            counts["candidate_features_considered"] += len(feature_keys)
            row_candidates = tuple(
                _compare_owner_to_feature(
                    owner,
                    tracks[feature_key],
                    config=config,
                    rt_frame=rt_frame,
                    counts=counts,
                    rules=rules,
                )
                for feature_key in sorted(feature_keys)
            )
            candidates_by_owner[owner.owner_id] = row_candidates

        assignments, ambiguous = _solve_sample_assignment(
            supported_rows, candidates_by_owner, counts
        )
        # A pattern seed carries its own cross-sample support, as in the
        # window graph; competition only decides which feature it joins.
        uncertain.update(
            owner_id for owner_id in ambiguous if owner_id not in seed_by_owner
        )
        for owner in supported_rows:
            row_candidates = candidates_by_owner[owner.owner_id]
            assigned_key = assignments.get(owner.owner_id)
            supported_keys = {
                candidate.feature_key
                for candidate in row_candidates
                if candidate.state == "supported"
            }
            for candidate in row_candidates:
                state: SparseDecisionState
                reason = candidate.reason
                if candidate.state == "proven_incompatible":
                    state = "proven_incompatible"
                elif candidate.state == "evaluated_undecided":
                    state = "evaluated_undecided"
                elif (
                    owner.owner_id in ambiguous
                    and candidate.feature_key == assigned_key
                ):
                    state = "evaluated_undecided"
                    reason = "alternative_assignment_has_equal_objective"
                elif candidate.feature_key == assigned_key:
                    state = "assigned"
                else:
                    state = "supported_not_selected"
                decisions.append(
                    SparseFeatureDecision(
                        direction=direction,
                        sample_stem=sample_stem,
                        owner_id=owner.owner_id,
                        feature_key=candidate.feature_key,
                        state=state,
                        reason=reason,
                        cost=candidate.cost,
                        witness_owner_ids=candidate.witness_owner_ids,
                        selected_witness_owner_id=(candidate.selected_witness_owner_id),
                        rt_basis=candidate.rt_basis,
                        rt_raw_delta_sec=candidate.rt_raw_delta_sec,
                        rt_drift_corrected_delta_sec=(
                            candidate.rt_drift_corrected_delta_sec
                        ),
                    )
                )

            if assigned_key is not None and owner.owner_id not in ambiguous:
                track = tracks[assigned_key]
                index.extend(track, owner, counts)
                place(owner.owner_id, assigned_key)
                continue

            feature = _FeatureTrack.from_owner(owner, direction=direction, rules=rules)
            tracks[feature.key] = feature
            index.add(feature)
            place(owner.owner_id, feature.key)
            counts["births"] += 1
            if owner.owner_id in ambiguous:
                reason = "alternative_assignment_has_equal_objective"
                state = "evaluated_undecided"
            elif supported_keys:
                reason = "joint_competition_selected_unassigned"
                state = "competition_unassigned"
                if owner.owner_id not in seed_by_owner:
                    uncertain.add(owner.owner_id)
            else:
                reason = "no_supported_existing_feature"
                state = "birth"
            decisions.append(
                SparseFeatureDecision(
                    direction=direction,
                    sample_stem=sample_stem,
                    owner_id=owner.owner_id,
                    feature_key=feature.key,
                    state=state,
                    reason=reason,
                )
            )

    grouped: dict[str, list[str]] = defaultdict(list)
    for owner_id, feature_key in owner_feature.items():
        grouped[feature_key].append(owner_id)
    groups = tuple(sorted(tuple(sorted(owner_ids)) for owner_ids in grouped.values()))
    return _DirectionalResult(groups, tuple(decisions), frozenset(uncertain))


def _sample_sort_key(
    sample_stem: str,
    injection_order: Mapping[str, int] | None,
) -> tuple[bool, int, str]:
    if injection_order is None or sample_stem not in injection_order:
        return True, 0, sample_stem
    return False, injection_order[sample_stem], sample_stem
