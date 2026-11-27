"""Link measured peaks in an injection-ordered RT plane."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import isclose
from statistics import median

from xic_extractor.alignment.ownership_models import SampleLocalMS1Owner
from xic_extractor.alignment.rt_drift import (
    MIN_COEFFICIENT_MEMBERS,
    DriftCoefficients,
    RtDriftModel,
    _own_fit,
)
from xic_extractor.injection_timeline import InjectionSequence
from xic_extractor.mass_accuracy import within_ppm

MAX_INJECTION_GAP = 8
INJECTION_GAP_WEIGHT_MIN = 0.005
MAX_CALIBRATED_RT_STEP_MIN = 0.4
MAX_BOOTSTRAP_RT_STEP_MIN = 0.6
MAX_BAND_PRECURSOR_SPAN_PPM = 10.0


def _within_link_step(step: float, limit: float) -> bool:
    return step <= limit or isclose(step, limit, rel_tol=0.0, abs_tol=1e-12)


def link_peak_bands(
    coordinates: Sequence[tuple[SampleLocalMS1Owner, float]],
    *,
    injections: InjectionSequence,
    max_rt_step_min: float = MAX_CALIBRATED_RT_STEP_MIN,
    precursor_ppm: float = MAX_BAND_PRECURSOR_SPAN_PPM,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    """Partition one workset, joining the closest admissible links first.

    A link's distance is its RT step plus ``INJECTION_GAP_WEIGHT_MIN`` per
    injection apart, so between nearly equal steps the nearer injection links
    first. Distance ties prefer shorter injection gaps, then measured peak order.
    Components carry their occupied injections and their *whole* mass span;
    transitive pairwise bounds alone would allow an out-of-tolerance band.
    Peaks with no known injection position have no links.
    """
    points = injections.ordered_rows(
        coordinates,
        stem=lambda item: item[0].sample_stem,
        within_sample=lambda item: (
            item[1],
            item[0].peak.measured_order_key,
            item[0].sample_stem,
        ),
        by_position=True,
    )
    stems = [owner.sample_stem for owner, _ in points]
    ranks = [injections.position(stem) for stem in stems]
    edges: list[tuple[float, int, int, int]] = []
    for i, (_, left_rt) in enumerate(points):
        rank = ranks[i]
        if rank is None:
            break
        for j in range(i + 1, len(points)):
            gap = injections.position_gap(stems[i], stems[j])
            if gap is None or gap > MAX_INJECTION_GAP:
                break
            if not gap:
                continue
            step = abs(left_rt - points[j][1])
            if _within_link_step(step, max_rt_step_min):
                edges.append((step + INJECTION_GAP_WEIGHT_MIN * gap, gap, i, j))
    edges.sort()

    parent = list(range(len(points)))
    occupied = [{rank} for rank in ranks]
    low = [owner.peak.mz for owner, _ in points]
    high = low.copy()

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for _, _, i, j in edges:
        left, right = root(i), root(j)
        if left == right or occupied[left] & occupied[right]:
            continue
        lo, hi = min(low[left], low[right]), max(high[left], high[right])
        if not within_ppm(hi, lo, precursor_ppm):
            continue
        if len(occupied[left]) < len(occupied[right]):
            left, right = right, left
        parent[right] = left
        occupied[left].update(occupied[right])
        low[left], high[left] = lo, hi

    bands: dict[int, list[SampleLocalMS1Owner]] = {}
    for index, (owner, _) in enumerate(points):
        bands.setdefault(root(index), []).append(owner)
    return tuple(tuple(band) for band in bands.values())


@dataclass(frozen=True, slots=True)
class LinkedPeakBand:
    """Members and the fit that authorized their link, if any."""

    members: tuple[SampleLocalMS1Owner, ...]
    coefficients: DriftCoefficients = DriftCoefficients()
    drift_reference: tuple[tuple[str, ...], float] | None = None


def link_with_band_drift(
    coordinates: Sequence[tuple[SampleLocalMS1Owner, float]],
    *,
    injections: InjectionSequence,
    drift: RtDriftModel | None,
    has_direct_support: Callable[[SampleLocalMS1Owner], bool],
    precursor_ppm: float = MAX_BAND_PRECURSOR_SPAN_PPM,
    batch_state_lookup: Callable[[Sequence[str], float], Mapping[str, float]]
    | None = None,
    unite_seeds: bool = False,
) -> tuple[LinkedPeakBand, ...]:
    """Fit each seed before asking whether additional peaks follow its band.

    With ``unite_seeds``, seeds that one joint fit explains are merged first:
    two seeds join when, flattened by the fit of their union, the union alone
    links into one band. Fits only ever use seed members, never peaks that a
    flattened relink would add, so a band cannot fit away its own jumps.
    """

    def link(points: Sequence[tuple[SampleLocalMS1Owner, float]]):
        return link_peak_bands(
            points,
            injections=injections,
            precursor_ppm=precursor_ppm,
        )

    first = link(coordinates)
    if drift is None or not drift.anchors:
        return tuple(LinkedPeakBand(band) for band in first)
    lookup = batch_state_lookup or drift.batch_state_lookup()
    rt_by_owner = {owner.owner_id: rt for owner, rt in coordinates}

    def direct(band: Sequence[SampleLocalMS1Owner]) -> int:
        return sum(has_direct_support(owner) for owner in band)

    def seed_order(band: Sequence[SampleLocalMS1Owner]) -> tuple[int, int, float]:
        return (
            -direct(band),
            -len(band),
            median(rt_by_owner[owner.owner_id] for owner in band),
        )

    def fit(band: Sequence[SampleLocalMS1Owner]):
        reference = (
            tuple(owner.owner_id for owner in band),
            median(rt_by_owner[owner.owner_id] for owner in band),
        )
        shape = lookup(*reference)
        coefficients = _own_fit(
            [
                (
                    rt_by_owner[owner.owner_id],
                    shape.get(owner.sample_stem),
                    drift.sample_shift_min.get(owner.sample_stem),
                )
                for owner in band
            ]
        )
        return reference, shape, coefficients

    def explained_together(band: Sequence[SampleLocalMS1Owner]) -> bool:
        _, shape, coefficients = fit(band)
        if not coefficients.nonzero_count:
            return False
        offsets = drift.sample_offsets(coefficients, shape)
        if any(owner.sample_stem not in offsets for owner in band):
            return False
        flat = [
            (owner, rt_by_owner[owner.owner_id] - offsets[owner.sample_stem])
            for owner in band
        ]
        return len(link(flat)) == 1

    seeds = sorted(
        (
            band
            for band in first
            if len(band) >= MIN_COEFFICIENT_MEMBERS and direct(band) >= 2
        ),
        key=seed_order,
    )
    if unite_seeds:
        seeds = sorted(
            _united_seeds(seeds, explained_together, precursor_ppm), key=seed_order
        )
    taken: set[str] = set()
    accepted = []
    for seed in seeds:
        reference_set = {owner.owner_id for owner in seed}
        if taken & reference_set:
            continue
        reference, shape, coefficients = fit(seed)
        if not coefficients.nonzero_count:
            continue
        offsets = drift.sample_offsets(coefficients, shape)
        flat = [
            (owner, rt - offsets[owner.sample_stem])
            for owner, rt in coordinates
            if owner.owner_id not in taken and owner.sample_stem in offsets
        ]
        candidates = (
            (overlap, len(band), band)
            for band in link(flat)
            if (overlap := sum(o.owner_id in reference_set for o in band))
            and direct(band) >= 2
        )
        best = max(candidates, key=lambda candidate: candidate[:2], default=None)
        if best is None:
            continue
        band = best[2]
        accepted.append(LinkedPeakBand(band, coefficients, reference))
        taken.update(owner.owner_id for owner in band)
    if not accepted:
        return tuple(LinkedPeakBand(band) for band in first)
    remaining = [
        (owner, rt) for owner, rt in coordinates if owner.owner_id not in taken
    ]
    return (*accepted, *(LinkedPeakBand(band) for band in link(remaining)))


def _united_seeds(
    seeds: Sequence[tuple[SampleLocalMS1Owner, ...]],
    explained_together: Callable[[Sequence[SampleLocalMS1Owner]], bool],
    precursor_ppm: float,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    """Merge seeds pairwise in seed order; each test covers both whole unions."""
    parent = list(range(len(seeds)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    centers = [median(owner.peak.mz for owner in seed) for seed in seeds]
    for i in range(len(seeds)):
        for j in range(i + 1, len(seeds)):
            left, right = root(i), root(j)
            if left == right or not within_ppm(centers[i], centers[j], precursor_ppm):
                continue
            union = [
                owner
                for k, seed in enumerate(seeds)
                if root(k) in (left, right)
                for owner in seed
            ]
            if explained_together(union):
                parent[right] = left
    united: dict[int, list[SampleLocalMS1Owner]] = {}
    for k, seed in enumerate(seeds):
        united.setdefault(root(k), []).extend(seed)
    return tuple(tuple(members) for members in united.values())


def competing_band_members(
    members: Sequence[SampleLocalMS1Owner],
    coordinates: Sequence[tuple[SampleLocalMS1Owner, float]],
    *,
    injections: InjectionSequence,
    has_direct_support: Callable[[SampleLocalMS1Owner], bool],
    precursor_ppm: float = MAX_BAND_PRECURSOR_SPAN_PPM,
) -> tuple[tuple[SampleLocalMS1Owner, SampleLocalMS1Owner], ...]:
    """Give each selected member's admissible rivals without changing membership.

    Coordinates are the same plane that linked this band, including its frozen
    drift adjustment if it used one. Replacing one member must leave both
    direct support and connectivity intact, even if that member was a bridge.
    """
    direct_count = sum(has_direct_support(owner) for owner in members)
    if direct_count < 2:
        return ()
    points_by_sample: dict[str, list[tuple[SampleLocalMS1Owner, float]]] = defaultdict(
        list
    )
    rt_by_owner: dict[str, float] = {}
    for owner, rt in coordinates:
        points_by_sample[owner.sample_stem].append((owner, rt))
        rt_by_owner[owner.owner_id] = rt

    result: list[tuple[SampleLocalMS1Owner, SampleLocalMS1Owner]] = []
    for selected in members:
        points = points_by_sample[selected.sample_stem]
        if len(points) < 2:
            continue
        direct_without_member = direct_count - has_direct_support(selected)
        peers = tuple(
            (owner, rt_by_owner[owner.owner_id])
            for owner in members
            if owner.sample_stem != selected.sample_stem
        )
        low = min(owner.peak.mz for owner, _ in peers)
        high = max(owner.peak.mz for owner, _ in peers)
        neighbor_rts = {
            owner.owner_id: rt
            for owner, rt in peers
            if (gap := injections.position_gap(owner.sample_stem, selected.sample_stem))
            is not None
            and 0 < gap <= MAX_INJECTION_GAP
        }
        components = None
        for rival, rival_rt in sorted(
            points, key=lambda point: (point[1], point[0].peak.measured_order_key)
        ):
            if rival.peak == selected.peak or (
                direct_without_member + has_direct_support(rival) < 2
            ):
                continue
            mz = rival.peak.mz
            if not within_ppm(max(high, mz), min(low, mz), precursor_ppm):
                continue
            neighbors = {
                owner_id
                for owner_id, rt in neighbor_rts.items()
                if _within_link_step(abs(rt - rival_rt), MAX_CALIBRATED_RT_STEP_MIN)
            }
            if not neighbors:
                continue
            if components is None:
                components = link_peak_bands(
                    peers,
                    injections=injections,
                    precursor_ppm=precursor_ppm,
                )
            if all(
                any(owner.owner_id in neighbors for owner in component)
                for component in components
            ):
                result.append((selected, rival))
    return tuple(result)


def band_extension_distance(
    members: Sequence[tuple[SampleLocalMS1Owner, float]],
    candidate: tuple[SampleLocalMS1Owner, float],
    *,
    injections: InjectionSequence,
    precursor_ppm: float,
) -> float | None:
    """Closest admissible link into a band's otherwise empty injection."""
    owner, rt = candidate
    if owner.sample_stem not in injections:
        return None
    mz = owner.peak.mz
    lo = hi = mz
    closest = None
    for peer, peer_rt in members:
        gap = injections.position_gap(owner.sample_stem, peer.sample_stem)
        if gap == 0:
            return None
        lo, hi = min(lo, peer.peak.mz), max(hi, peer.peak.mz)
        if gap is not None and gap <= MAX_INJECTION_GAP:
            step = abs(peer_rt - rt)
            if _within_link_step(step, MAX_CALIBRATED_RT_STEP_MIN) and (
                closest is None or step < closest
            ):
                closest = step
    return closest if within_ppm(hi, lo, precursor_ppm) else None
