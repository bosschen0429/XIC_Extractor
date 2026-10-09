"""One physical peak trajectory supplies correspondence and search coordinates.

Order and a shared, width-bounded RT displacement establish correspondence.
Missing landmarks are allowed. Relative height only breaks ties between equally
complete patterns; it cannot replace a two-peak pattern with one tall peak.
Single-peak links are local continuations, not independent pattern evidence.

The resulting sample positions are observations of the same trajectory used to
locate missing samples. MS1 landmarks never grant method support, and auxiliary
related-ion evidence never substitutes a different trajectory or search center.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import numpy as np

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import (
    PPM_BOUNDARY_EPSILON,
    absolute_ppm_error,
    mz_tolerance_da,
    within_ppm,
)
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)

if TYPE_CHECKING:
    from xic_extractor.alignment.related_ion_evidence import RelatedIonEvidence

_NEIGHBOR_SAMPLES = 6
_TRAJECTORY_NEIGHBORS = 12
_TIE_EPSILON = 1e-9
_TREND_SCALE_FLOOR_MIN = 0.5 / 60.0
_TREND_TOLERANCE_MIN = 1e-8 / 60.0


class RtCalibration(Protocol):
    """The shared batch coordinate transform, including MS1-only observations."""

    def has_support(self, sample_stem: str) -> bool: ...

    def common_rt_min(self, sample_stem: str, observed_rt: float) -> float: ...

    def offset_min(self, sample_stem: str, rt_min: float) -> float: ...


@dataclass(frozen=True, slots=True)
class PeakLocation:
    query_rt: float
    basis: str
    observed: bool = False

    @property
    def observed_rt(self) -> float | None:
        """A forecast alone does not establish a different observed peak."""
        return self.query_rt if self.observed else None


@dataclass(frozen=True, slots=True)
class PeakTrajectory:
    owner_ids: tuple[str, ...]
    sample_apices: tuple[tuple[str, float], ...]
    _positions: Mapping[str, float] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_positions", dict(self.sample_apices))

    def locate(
        self,
        sample_stem: str,
        *,
        injection_order: Mapping[str, int],
        sample_cohorts: Mapping[str, str],
        rt_warp: RtCalibration | None,
        backfill_half_window_min: float,
        related_ion: RelatedIonEvidence | None = None,
    ) -> PeakLocation:
        """Use an observed point or fit the unobserved part of this trajectory.

        Both method-supported owners and matched MS1-only peaks contribute
        positions. The query stays in the target sample's RT coordinates;
        neither fitting nor related-ion agreement supplies method eligibility.
        """
        if sample_stem in self._positions:
            location = PeakLocation(
                self._positions[sample_stem], "trajectory_observed", True
            )
        else:
            location = self._estimate(
                sample_stem,
                injection_order=injection_order,
                sample_cohorts=sample_cohorts,
                rt_warp=rt_warp,
                backfill_half_window_min=backfill_half_window_min,
            )
        if related_ion is None:
            return location
        return PeakLocation(
            location.query_rt,
            f"{location.basis};{related_ion.describe_at(location.query_rt)}",
            location.observed,
        )

    def _estimate(
        self,
        sample_stem: str,
        *,
        injection_order: Mapping[str, int],
        sample_cohorts: Mapping[str, str],
        rt_warp: RtCalibration | None,
        backfill_half_window_min: float,
    ) -> PeakLocation:
        order = injection_order.get(sample_stem)
        cohort = sample_cohorts.get(sample_stem)
        nearest = sorted(
            (
                (sample, rt)
                for sample, rt in self.sample_apices
                if order is not None
                and cohort is not None
                and sample in injection_order
                and sample_cohorts.get(sample) == cohort
            ),
            key=lambda point: (
                abs(injection_order[point[0]] - (order or 0)),
                injection_order[point[0]],
                point[0],
            ),
        )[:_TRAJECTORY_NEIGHBORS]
        if order is None or not nearest:
            # Without same-cohort temporal support, the trajectory has only a
            # reference position. Do not extrapolate from another batch's order.
            return PeakLocation(
                float(np.median([rt for _, rt in self.sample_apices])),
                "trajectory_reference",
            )
        calibrated = (
            rt_warp is not None
            and rt_warp.has_support(sample_stem)
            and all(rt_warp.has_support(sample) for sample, _ in nearest)
        )
        coordinates = [
            rt_warp.common_rt_min(sample, rt)
            if calibrated and rt_warp is not None
            else rt
            for sample, rt in nearest
        ]
        fitted = _fit_rt_trajectory(
            [injection_order[sample] for sample, _ in nearest],
            coordinates,
            order,
            backfill_half_window_min=backfill_half_window_min,
        )
        if calibrated and rt_warp is not None:
            return PeakLocation(
                max(
                    0.0,
                    fitted.query_rt + rt_warp.offset_min(sample_stem, fitted.query_rt),
                ),
                f"calibrated_{fitted.basis}",
            )
        return PeakLocation(max(0.0, fitted.query_rt), fitted.basis)


@dataclass(frozen=True, slots=True)
class PeakTrajectories:
    """Physical correspondence only; unbound or ambiguous owners are omitted."""

    tracks: tuple[PeakTrajectory, ...] = ()
    pattern_comparisons: int = 0
    source_contexts: tuple[SampleElutionContext, ...] = ()
    _track_by_owner: Mapping[str, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_track_by_owner",
            {
                owner_id: index
                for index, track in enumerate(self.tracks)
                for owner_id in track.owner_ids
            },
        )

    def trajectory_for(self, owners: Sequence[SampleLocalMS1Owner]) -> PeakTrajectory:
        """Bind a finalized group to its direct and compatible context points.

        Unmatched owners can extend a pattern-supported trajectory. Conversely,
        a known-identity group spanning conflicting physical patterns supplies
        only its own observations; a physical pattern cannot override identity.
        """
        requested = {owner.owner_id for owner in owners}
        indices = {
            self._track_by_owner[item]
            for item in requested
            if item in self._track_by_owner
        }
        points = (
            dict(self.tracks[next(iter(indices))].sample_apices)
            if len(indices) == 1
            else {}
        )
        points.update((owner.sample_stem, owner.owner_apex_rt) for owner in owners)
        return PeakTrajectory(tuple(sorted(requested)), tuple(sorted(points.items())))

    def owner_track_indices(self) -> dict[str, int]:
        return dict(self._track_by_owner)

    def permits_membership(self, owner_ids: Iterable[str]) -> bool:
        """Unobserved context may extend a track, but distinct tracks cannot merge."""
        return (
            len(
                {
                    self._track_by_owner[item]
                    for item in owner_ids
                    if item in self._track_by_owner
                }
            )
            <= 1
        )


@dataclass(frozen=True, slots=True)
class _Node:
    sample: str
    mz: float
    landmark: ElutionLandmark


@dataclass(frozen=True, slots=True)
class _Pattern:
    sample: str
    mz: float
    nodes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _Match:
    pairs: tuple[tuple[int, int], ...]
    height_error: float
    residual: float


class _MassIndex:
    """A broad candidate window; the existing directional ppm test decides links."""

    def __init__(self, masses: Sequence[float], ppm: float) -> None:
        self._entries = sorted((mz, index) for index, mz in enumerate(masses))
        self._masses = [mz for mz, _ in self._entries]
        self._ppm = ppm + PPM_BOUNDARY_EPSILON
        self._candidates: dict[float, tuple[int, ...]] = {}

    def candidates(self, mz: float) -> tuple[int, ...]:
        if not self._masses or not math.isfinite(mz) or mz <= 0.0:
            return ()
        cached = self._candidates.get(mz)
        if cached is not None:
            return cached
        # Either operand can be the reference in within_ppm. Using the larger
        # mass bounds both orientations without changing the acceptance rule.
        radius = mz_tolerance_da(max(mz, self._masses[-1]), self._ppm)
        first = bisect_left(self._masses, math.nextafter(mz - radius, -math.inf))
        last = bisect_right(self._masses, math.nextafter(mz + radius, math.inf))
        # Preserve original traversal and tie order, not m/z index order.
        selected = tuple(sorted(index for _, index in self._entries[first:last]))
        self._candidates[mz] = selected
        return selected


@dataclass(frozen=True, slots=True)
class _OrderRegion:
    nodes: tuple[int, ...]
    positions: Mapping[int, int]
    patterns: tuple[_Pattern, ...]


def _comparison_regions(
    patterns: tuple[_Pattern, ...],
    matches: Sequence[tuple[_Pattern, _Pattern, _Match, int]],
) -> dict[int, _OrderRegion]:
    """Partition the order graph by all possible correspondence links.

    A contraction cannot affect a disconnected region. Include all proposed
    links, even ones later rejected, so each region remains closed throughout
    the ordered merge pass.
    """
    parents = {node: node for pattern in patterns for node in pattern.nodes}

    def root(node: int) -> int:
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    def join(left: int, right: int) -> None:
        first, second = root(left), root(right)
        if first != second:
            parents[second] = first

    for pattern in patterns:
        for node in pattern.nodes[1:]:
            join(pattern.nodes[0], node)
    for left, right, _, _ in matches:
        join(left.nodes[0], right.nodes[0])
    nodes_by_region: dict[int, list[int]] = defaultdict(list)
    patterns_by_region: dict[int, list[_Pattern]] = defaultdict(list)
    for node in parents:
        nodes_by_region[root(node)].append(node)
    for pattern in patterns:
        patterns_by_region[root(pattern.nodes[0])].append(pattern)
    regions = {}
    for key, nodes in nodes_by_region.items():
        positions = {node: index for index, node in enumerate(nodes)}
        regions[key] = _OrderRegion(
            tuple(nodes),
            positions,
            tuple(
                _Pattern(
                    pattern.sample,
                    pattern.mz,
                    tuple(positions[n] for n in pattern.nodes),
                )
                for pattern in patterns_by_region[key]
            ),
        )
    return {node: regions[root(node)] for node in parents}


def resolve_peak_trajectories(
    owners: Sequence[SampleLocalMS1Owner],
    contexts: Sequence[SampleElutionContext],
    *,
    config: AlignmentConfig,
    injection_order: Mapping[str, int],
) -> PeakTrajectories:
    """Bind existing owners and MS1-only locations to consistent local patterns.

    Acquisition and signal preparation are caller-owned. This finite in-memory
    calculation compares at most six neighboring samples per pattern and never
    creates an owner, selected peak, area, or method assessment.
    """
    if not contexts or not owners:
        return PeakTrajectories()
    nodes, patterns, owner_nodes, retained_contexts = _local_patterns(
        owners, contexts, config
    )
    if not patterns:
        return PeakTrajectories(source_contexts=retained_contexts)
    matches = []
    comparisons = 0
    for left, right in _neighbor_pairs(patterns, nodes, config, injection_order):
        comparisons += 1
        match = _match_patterns(
            [nodes[index].landmark for index in left.nodes],
            [nodes[index].landmark for index in right.nodes],
            max_shift=config.max_rt_sec / 60.0,
        )
        if match is not None:
            gap = abs(
                injection_order.get(left.sample, 0)
                - injection_order.get(right.sample, 0)
            )
            matches.append((left, right, match, gap))
    matches.sort(
        key=lambda item: (
            -len(item[2].pairs),
            item[3],
            item[2].height_error,
            item[2].residual,
            item[0].sample,
            item[1].sample,
            item[0].nodes,
            item[1].nodes,
        )
    )
    roots = list(range(len(nodes)))
    # A pre-existing cycle or repeated node cannot be repaired by contraction.
    # Check the initial graph once, including regions without candidate links.
    if not _preserves_order(roots, patterns):
        return PeakTrajectories((), comparisons, retained_contexts)
    regions = _comparison_regions(patterns, matches)
    supported_nodes: set[int] = set()
    for left, right, match, _ in matches:
        region = regions[left.nodes[0]]
        proposed = [roots[index] for index in region.nodes]
        changed = False
        for i, j in match.pairs:
            keep, absorbed = sorted(
                (
                    proposed[region.positions[left.nodes[i]]],
                    proposed[region.positions[right.nodes[j]]],
                )
            )
            if keep != absorbed:
                changed = True
                proposed = [keep if root == absorbed else root for root in proposed]
        if not changed:
            continue
        if not _preserves_order(proposed, region.patterns):
            continue
        for index, root in zip(region.nodes, proposed, strict=True):
            roots[index] = root
        if len(match.pairs) >= 2:
            supported_nodes.update(left.nodes[i] for i, _ in match.pairs)
            supported_nodes.update(right.nodes[j] for _, j in match.pairs)
    supported_roots = {roots[index] for index in supported_nodes}
    members: dict[int, list[int]] = defaultdict(list)
    bound: dict[int, list[str]] = defaultdict(list)
    for index, root in enumerate(roots):
        if root in supported_roots:
            members[root].append(index)
    for owner_id, node in owner_nodes.items():
        if roots[node] in supported_roots:
            bound[roots[node]].append(owner_id)
    tracks = []
    for root, owner_ids in bound.items():
        locations = tuple(
            sorted((nodes[i].sample, nodes[i].landmark.apex_rt) for i in members[root])
        )
        # Separate windows can have no order edge between them. A duplicated
        # sample is still not a usable location or membership assertion.
        if len({sample for sample, _ in locations}) != len(locations):
            continue
        tracks.append(PeakTrajectory(tuple(sorted(owner_ids)), locations))
    return PeakTrajectories(
        tuple(sorted(tracks, key=lambda track: track.owner_ids)),
        comparisons,
        retained_contexts,
    )


def _local_patterns(
    owners: Sequence[SampleLocalMS1Owner],
    contexts: Sequence[SampleElutionContext],
    config: AlignmentConfig,
) -> tuple[
    list[_Node],
    tuple[_Pattern, ...],
    dict[str, int],
    tuple[SampleElutionContext, ...],
]:
    by_sample: dict[str, list[SampleElutionContext]] = defaultdict(list)
    for context in contexts:
        if context.landmarks:
            by_sample[context.sample_stem].append(context)
    context_indexes = {
        sample: _MassIndex([context.mz for context in rows], config.preferred_ppm)
        for sample, rows in by_sample.items()
    }
    nodes: list[_Node] = []
    nodes_by_mass: dict[tuple[str, float], list[int]] = defaultdict(list)
    patterns: dict[tuple[int, ...], _Pattern] = {}
    owner_nodes = {}
    retained: dict[tuple[str, float, str], SampleElutionContext] = {}

    def add_pattern(
        context: SampleElutionContext, start: int, end: int
    ) -> tuple[int, ...]:
        peaks = context.landmarks
        indices = []
        nearby_masses = {
            by_sample[context.sample_stem][index].mz
            for index in context_indexes[context.sample_stem].candidates(context.mz)
        }
        for peak in peaks[start:end]:
            same = next(
                (
                    index
                    for index in sorted(
                        index
                        for mz in nearby_masses
                        for index in nodes_by_mass[(context.sample_stem, mz)]
                    )
                    if within_ppm(nodes[index].mz, context.mz, config.preferred_ppm)
                    and abs(nodes[index].landmark.apex_rt - peak.apex_rt)
                    <= config.owner_apex_close_sec / 60.0
                ),
                None,
            )
            if same is None:
                same = len(nodes)
                nodes.append(_Node(context.sample_stem, context.mz, peak))
                nodes_by_mass[(context.sample_stem, context.mz)].append(same)
            indices.append(same)
        key = tuple(indices)
        patterns[key] = _Pattern(context.sample_stem, context.mz, key)
        return key

    for owner in sorted(
        owners, key=lambda item: (item.sample_stem, item.owner_apex_rt, item.owner_id)
    ):
        context_index = context_indexes.get(owner.sample_stem)
        if context_index is None:
            continue
        candidate_contexts = (
            by_sample[owner.sample_stem][index]
            for index in context_index.candidates(owner.precursor_mz)
        )
        available = [
            context
            for context in candidate_contexts
            if within_ppm(owner.precursor_mz, context.mz, config.preferred_ppm)
            and context.rt_start <= owner.owner_apex_rt <= context.rt_end
        ]
        if not available:
            continue
        context = min(
            available,
            key=lambda item: (
                -(item.rt_end - item.rt_start),
                absolute_ppm_error(owner.precursor_mz, item.mz),
                item.trace_content_id,
            ),
        )
        peaks = context.landmarks
        retained[(context.sample_stem, context.mz, context.trace_content_id)] = context
        selected = min(
            range(len(peaks)),
            key=lambda index: abs(peaks[index].apex_rt - owner.owner_apex_rt),
        )
        if abs(peaks[selected].apex_rt - owner.owner_apex_rt) > max(
            config.owner_apex_close_sec / 60.0, peaks[selected].width / 2.0
        ):
            continue
        start, end = selected, selected + 1
        gap = config.preferred_rt_sec / 60.0
        while start and peaks[start].apex_rt - peaks[start - 1].apex_rt <= gap:
            start -= 1
        while end < len(peaks) and peaks[end].apex_rt - peaks[end - 1].apex_rt <= gap:
            end += 1
        indices = add_pattern(context, start, end)
        owner_nodes[owner.owner_id] = indices[selected - start]

    # Context from an MS1-only sample must remain usable as a location, even
    # though that sample contributed no direct owner to this grouping scope.
    seeds = tuple(patterns.values())
    seed_index = _MassIndex([seed.mz for seed in seeds], config.preferred_ppm)
    seed_centers = [
        sum(nodes[i].landmark.apex_rt for i in seed.nodes) / len(seed.nodes)
        for seed in seeds
    ]
    for context in sorted(
        contexts,
        key=lambda item: (
            item.sample_stem,
            -(item.rt_end - item.rt_start),
            item.mz,
            item.trace_content_id,
        ),
    ):
        context_key = (context.sample_stem, context.mz, context.trace_content_id)
        if not context.landmarks or context_key in retained:
            continue
        compatible = [
            index
            for index in seed_index.candidates(context.mz)
            if within_ppm(seeds[index].mz, context.mz, config.preferred_ppm)
        ]
        if not compatible:
            continue
        peaks = context.landmarks
        start = 0
        for end in range(1, len(peaks) + 1):
            if (
                end < len(peaks)
                and peaks[end].apex_rt - peaks[end - 1].apex_rt
                <= config.preferred_rt_sec / 60.0
            ):
                continue
            center = (peaks[start].apex_rt + peaks[end - 1].apex_rt) / 2.0
            if any(
                abs(center - seed_centers[index]) * 60.0 <= config.max_rt_sec
                for index in compatible
            ):
                add_pattern(context, start, end)
                retained[context_key] = context
            start = end
    return (
        nodes,
        tuple(sorted(patterns.values(), key=lambda item: (item.sample, item.nodes))),
        owner_nodes,
        tuple(retained[key] for key in sorted(retained)),
    )


def _neighbor_pairs(
    patterns: tuple[_Pattern, ...],
    nodes: list[_Node],
    config: AlignmentConfig,
    injection_order: Mapping[str, int],
) -> tuple[tuple[_Pattern, _Pattern], ...]:
    chosen: set[tuple[int, int]] = set()
    mass_index = _MassIndex([pattern.mz for pattern in patterns], config.preferred_ppm)
    centers = [
        sum(nodes[i].landmark.apex_rt for i in pattern.nodes) / len(pattern.nodes)
        for pattern in patterns
    ]
    for index, pattern in enumerate(patterns):
        center = centers[index]
        candidates = []
        for other_index in mass_index.candidates(pattern.mz):
            other = patterns[other_index]
            if pattern.sample == other.sample or not within_ppm(
                pattern.mz, other.mz, config.preferred_ppm
            ):
                continue
            other_center = centers[other_index]
            if abs(center - other_center) * 60.0 > config.max_rt_sec:
                continue
            candidates.append(
                (
                    abs(
                        injection_order.get(pattern.sample, 0)
                        - injection_order.get(other.sample, 0)
                    ),
                    abs(center - other_center),
                    other.sample,
                    other_index,
                )
            )
        samples: set[str] = set()
        for _, _, sample, other_index in sorted(candidates):
            if sample not in samples and len(samples) == _NEIGHBOR_SAMPLES:
                continue
            samples.add(sample)
            first, second = sorted((index, other_index))
            chosen.add((first, second))
    return tuple((patterns[i], patterns[j]) for i, j in sorted(chosen))


def _match_patterns(
    left: Sequence[ElutionLandmark],
    right: Sequence[ElutionLandmark],
    *,
    max_shift: float,
) -> _Match | None:
    heights = max(peak.height for peak in left), max(peak.height for peak in right)
    deltas = [[b.apex_rt - a.apex_rt for b in right] for a in left]
    tolerances = [[(a.width + b.width) / 2.0 for b in right] for a in left]
    errors = [
        [
            abs(math.log((a.height / heights[0]) / (b.height / heights[1])))
            for b in right
        ]
        for a in left
    ]
    endpoints = {-max_shift, max_shift}
    shifts = {0.0}
    for i in range(len(left)):
        for j in range(len(right)):
            delta, tolerance = deltas[i][j], tolerances[i][j]
            shifts.add(max(-max_shift, min(max_shift, delta)))
            endpoints.update(
                value
                for value in (delta - tolerance, delta + tolerance)
                if abs(value) <= max_shift
            )
    ordered = sorted(endpoints)
    shifts.update((a + b) / 2.0 for a, b in zip(ordered, ordered[1:]))
    empty: tuple[tuple[int, float, float], tuple[tuple[int, int], ...]] = (
        (0, 0.0, 0.0),
        (),
    )
    alternatives: dict[tuple[tuple[int, int], ...], tuple[int, float, float]] = {}
    for shift in sorted(shifts):
        scores = [[empty for _ in range(len(right) + 1)] for _ in range(len(left) + 1)]
        for i in range(len(left)):
            for j in range(len(right)):
                options = [scores[i][j + 1], scores[i + 1][j]]
                residual = abs(deltas[i][j] - shift) / tolerances[i][j]
                if residual <= 1.0:
                    previous, pairs = scores[i][j]
                    options.append(
                        (
                            (
                                previous[0] + 1,
                                previous[1] - errors[i][j],
                                previous[2] - residual,
                            ),
                            (*pairs, (i, j)),
                        )
                    )
                scores[i + 1][j + 1] = max(options)
        score, pairs = scores[-1][-1]
        if pairs and score > alternatives.get(pairs, (-1, 0.0, 0.0)):
            alternatives[pairs] = score
    if not alternatives:
        return None
    ranked = sorted(
        alternatives.items(), key=lambda item: (item[1], item[0]), reverse=True
    )
    pairs, score = ranked[0]
    if score[0] == 1:
        # With no multi-peak witness, retain only the nearest RT continuation.
        singles = sorted(
            (abs(deltas[i][j]), i, j)
            for i in range(len(left))
            for j in range(len(right))
            if abs(deltas[i][j]) <= max_shift
        )
        if not singles or (
            len(singles) > 1 and abs(singles[0][0] - singles[1][0]) <= _TIE_EPSILON
        ):
            return None
        distance, i, j = singles[0]
        return _Match(((i, j),), 0.0, distance)
    if len(ranked) > 1:
        second = ranked[1][1]
        if (
            score[0] == second[0]
            and abs(score[1] - second[1]) <= _TIE_EPSILON
            and abs(score[2] - second[2]) <= _TIE_EPSILON
        ):
            return None
    return _Match(pairs, -score[1], -score[2])


def _preserves_order(roots: Sequence[int], patterns: tuple[_Pattern, ...]) -> bool:
    successors: dict[int, set[int]] = defaultdict(set)
    indegree: dict[int, int] = defaultdict(int)
    for pattern in patterns:
        sequence = [roots[index] for index in pattern.nodes]
        if len(set(sequence)) != len(sequence):
            return False
        for left, right in zip(sequence, sequence[1:]):
            indegree.setdefault(left, 0)
            if right not in successors[left]:
                successors[left].add(right)
                indegree[right] += 1
    available = [root for root, count in indegree.items() if not count]
    visited = 0
    while available:
        root = available.pop()
        visited += 1
        for following in successors[root]:
            indegree[following] -= 1
            if not indegree[following]:
                available.append(following)
    return visited == len(indegree)


def _fit_rt_trajectory(
    orders: Sequence[int],
    rts: Sequence[float],
    query_order: int,
    *,
    backfill_half_window_min: float,
) -> PeakLocation:
    """Robust kernel-weighted linear trend, so steep drift is not lagged.

    Fewer than three neighbors, or neighbors at one order, leave no slope to fit;
    their median is used. Outside the support, residual scatter amplified by
    fit leverage must fit inside the actual search half-window. Otherwise use
    the fitted value at the nearest supported injection, not an unbounded
    slope or a different group. This risk proxy is not a calibrated confidence
    interval and does not establish peak identity.
    """
    offsets = np.asarray(orders, dtype=float) - query_order
    values = np.asarray(rts, dtype=float)
    spread = float(np.std(offsets))
    if len(values) < 3 or spread < 1e-10:
        return PeakLocation(float(np.median(values)), "trajectory_fit")
    design = np.column_stack([np.ones(len(offsets)), offsets / spread])
    kernel = (
        1.0 / (1.0 + np.abs(offsets) / max(float(np.max(np.abs(offsets))), 1e-10))
    ) ** 2
    weights = kernel
    beta = np.zeros(2)
    for _ in range(80):
        fit_weights = weights
        root = np.sqrt(weights)
        next_beta = np.linalg.lstsq(design * root[:, None], values * root, rcond=None)[
            0
        ]
        residual = values - design @ next_beta
        scale = max(
            _TREND_SCALE_FLOOR_MIN,
            1.4826 * float(np.median(np.abs(residual - np.median(residual)))),
        )
        weights = kernel * np.minimum(
            1.0, 1.345 * scale / np.maximum(np.abs(residual), 1e-12)
        )
        converged = float(np.max(np.abs(next_beta - beta))) < _TREND_TOLERANCE_MIN
        beta = next_beta
        if converged:
            break
    basis = "trajectory_fit"
    query_rt = float(beta[0])
    if min(orders) > query_order or max(orders) < query_order:
        # The query row is [1, 0] in this centered design. Reuse the final fit;
        # no cross-validation or extra trend fits belong in this hot path.
        covariance = np.linalg.pinv(design.T @ (fit_weights[:, None] * design))
        risk = scale * float(np.sqrt(1.0 + max(0.0, covariance[0, 0])))
        if risk > backfill_half_window_min:
            boundary = min(max(query_order, min(orders)), max(orders))
            query_rt += float(beta[1]) * (boundary - query_order) / spread
            basis = "trajectory_fit_at_support_boundary"
    return PeakLocation(query_rt, basis)
