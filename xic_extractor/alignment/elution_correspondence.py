"""Resolve local elution patterns without granting MS1 landmarks method support.

Order and a shared, width-bounded RT displacement establish correspondence.
Missing landmarks are allowed. Relative height only breaks ties between equally
complete patterns; it cannot replace a two-peak pattern with one tall peak.
Single-peak links are local continuations, not independent pattern evidence.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from xic_extractor.alignment.config import AlignmentConfig
from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.mass_accuracy import absolute_ppm_error, within_ppm
from xic_extractor.peak_detection.elution_context import (
    ElutionLandmark,
    SampleElutionContext,
)

_NEIGHBOR_SAMPLES = 6
_TIE_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class ElutionTrack:
    owner_ids: tuple[str, ...]
    sample_apices: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class ElutionCorrespondence:
    """Physical correspondence only; unbound or ambiguous owners are omitted."""

    tracks: tuple[ElutionTrack, ...] = ()
    pattern_comparisons: int = 0
    source_contexts: tuple[SampleElutionContext, ...] = ()

    def locations_for(self, owner_ids: Sequence[str]) -> dict[str, float]:
        requested = set(owner_ids)
        tracks = [track for track in self.tracks if requested & set(track.owner_ids)]
        if len(tracks) != 1 or not requested.issubset(tracks[0].owner_ids):
            return {}
        return dict(tracks[0].sample_apices)

    def owner_track_indices(self) -> dict[str, int]:
        return {
            owner_id: index
            for index, track in enumerate(self.tracks)
            for owner_id in track.owner_ids
        }


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


def resolve_elution_correspondence(
    owners: Sequence[SampleLocalMS1Owner],
    contexts: Sequence[SampleElutionContext],
    *,
    config: AlignmentConfig,
    injection_order: Mapping[str, int],
) -> ElutionCorrespondence:
    """Bind existing owners and MS1-only locations to consistent local patterns.

    Acquisition and signal preparation are caller-owned. This finite in-memory
    calculation compares at most six neighboring samples per pattern and never
    creates an owner, selected peak, area, or method assessment.
    """
    if not contexts or not owners:
        return ElutionCorrespondence()
    nodes, patterns, owner_nodes, retained_contexts = _local_patterns(
        owners, contexts, config
    )
    if not patterns:
        return ElutionCorrespondence(source_contexts=retained_contexts)
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
    supported_nodes: set[int] = set()
    for left, right, match, _ in matches:
        proposed = roots.copy()
        for i, j in match.pairs:
            keep, absorbed = sorted((proposed[left.nodes[i]], proposed[right.nodes[j]]))
            if keep != absorbed:
                proposed = [keep if root == absorbed else root for root in proposed]
        if proposed == roots:
            continue
        if not _preserves_order(proposed, patterns):
            continue
        roots = proposed
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
        tracks.append(ElutionTrack(tuple(sorted(owner_ids)), locations))
    return ElutionCorrespondence(
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
    nodes: list[_Node] = []
    nodes_by_sample: dict[str, list[int]] = defaultdict(list)
    patterns: dict[tuple[int, ...], _Pattern] = {}
    owner_nodes = {}
    retained: dict[tuple[str, float, str], SampleElutionContext] = {}

    def add_pattern(
        context: SampleElutionContext, start: int, end: int
    ) -> tuple[int, ...]:
        peaks = context.landmarks
        indices = []
        for peak in peaks[start:end]:
            same = next(
                (
                    index
                    for index in nodes_by_sample[context.sample_stem]
                    if within_ppm(nodes[index].mz, context.mz, config.preferred_ppm)
                    and abs(nodes[index].landmark.apex_rt - peak.apex_rt)
                    <= config.owner_apex_close_sec / 60.0
                ),
                None,
            )
            if same is None:
                same = len(nodes)
                nodes.append(_Node(context.sample_stem, context.mz, peak))
                nodes_by_sample[context.sample_stem].append(same)
            indices.append(same)
        key = tuple(indices)
        patterns[key] = _Pattern(context.sample_stem, context.mz, key)
        return key

    for owner in sorted(
        owners, key=lambda item: (item.sample_stem, item.owner_apex_rt, item.owner_id)
    ):
        available = [
            context
            for context in by_sample[owner.sample_stem]
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
            seed
            for seed in seeds
            if within_ppm(seed.mz, context.mz, config.preferred_ppm)
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
                abs(
                    center
                    - sum(nodes[i].landmark.apex_rt for i in seed.nodes)
                    / len(seed.nodes)
                )
                * 60.0
                <= config.max_rt_sec
                for seed in compatible
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
    for index, pattern in enumerate(patterns):
        center = sum(nodes[i].landmark.apex_rt for i in pattern.nodes) / len(
            pattern.nodes
        )
        candidates = []
        for other_index, other in enumerate(patterns):
            if pattern.sample == other.sample or not within_ppm(
                pattern.mz, other.mz, config.preferred_ppm
            ):
                continue
            other_center = sum(nodes[i].landmark.apex_rt for i in other.nodes) / len(
                other.nodes
            )
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


def _preserves_order(roots: list[int], patterns: tuple[_Pattern, ...]) -> bool:
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
