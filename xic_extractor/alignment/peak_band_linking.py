"""Link measured peaks in an injection-ordered RT plane."""

from __future__ import annotations

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
from xic_extractor.mass_accuracy import within_ppm

MAX_INJECTION_GAP = 8
MAX_CALIBRATED_RT_STEP_MIN = 0.4
MAX_BOOTSTRAP_RT_STEP_MIN = 0.6
MAX_BAND_PRECURSOR_SPAN_PPM = 10.0


def link_peak_bands(
    coordinates: Sequence[tuple[SampleLocalMS1Owner, float]],
    *,
    injection_order: Mapping[str, int],
    max_rt_step_min: float = MAX_CALIBRATED_RT_STEP_MIN,
    precursor_ppm: float = MAX_BAND_PRECURSOR_SPAN_PPM,
) -> tuple[tuple[SampleLocalMS1Owner, ...], ...]:
    """Partition one workset, joining the closest admissible links first.

    Distance ties prefer shorter injection gaps, then measured peak order.
    Components carry their occupied injections and their *whole* mass span;
    transitive pairwise bounds alone would allow an out-of-tolerance band.
    Peaks with no known injection order have no links.
    """
    points = sorted(
        coordinates,
        key=lambda item: (
            injection_order.get(item[0].sample_stem, float("inf")),
            item[1],
            item[0].peak.measured_order_key,
            item[0].sample_stem,
        ),
    )
    ranks = [injection_order.get(owner.sample_stem) for owner, _ in points]
    edges: list[tuple[float, int, int, int]] = []
    for i, (_, left_rt) in enumerate(points):
        rank = ranks[i]
        if rank is None:
            break
        for j in range(i + 1, len(points)):
            right_rank = ranks[j]
            if right_rank is None or (gap := right_rank - rank) > MAX_INJECTION_GAP:
                break
            if not gap:
                continue
            step = abs(left_rt - points[j][1])
            if step <= max_rt_step_min or isclose(
                step, max_rt_step_min, rel_tol=0.0, abs_tol=1e-12
            ):
                edges.append((step, gap, i, j))
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
    injection_order: Mapping[str, int],
    drift: RtDriftModel | None,
    has_direct_support: Callable[[SampleLocalMS1Owner], bool],
    precursor_ppm: float = MAX_BAND_PRECURSOR_SPAN_PPM,
    batch_state_lookup: Callable[[Sequence[str], float], Mapping[str, float]]
    | None = None,
) -> tuple[LinkedPeakBand, ...]:
    """Fit each seed before asking whether additional peaks follow its band."""

    def link(points: Sequence[tuple[SampleLocalMS1Owner, float]]):
        return link_peak_bands(
            points, injection_order=injection_order, precursor_ppm=precursor_ppm
        )

    first = link(coordinates)
    if drift is None or not drift.anchors:
        return tuple(LinkedPeakBand(band) for band in first)
    lookup = batch_state_lookup or drift.batch_state_lookup()
    rt_by_owner = {owner.owner_id: rt for owner, rt in coordinates}
    seeds = sorted(
        (
            (-direct, -len(band), median(rt_by_owner[o.owner_id] for o in band), band)
            for band in first
            if len(band) >= MIN_COEFFICIENT_MEMBERS
            and (direct := sum(has_direct_support(o) for o in band)) >= 2
        ),
        key=lambda seed: seed[:3],
    )
    taken: set[str] = set()
    accepted = []
    for _, _, reference_rt, seed in seeds:
        reference_ids = tuple(owner.owner_id for owner in seed)
        reference_set = set(reference_ids)
        if taken & reference_set:
            continue
        shape = lookup(reference_ids, reference_rt)
        coefficients = _own_fit(
            [
                (
                    rt_by_owner[owner.owner_id],
                    shape.get(owner.sample_stem),
                    drift.sample_shift_min.get(owner.sample_stem),
                )
                for owner in seed
            ]
        )
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
            and sum(has_direct_support(o) for o in band) >= 2
        )
        best = max(candidates, key=lambda candidate: candidate[:2], default=None)
        if best is None:
            continue
        band = best[2]
        accepted.append(
            LinkedPeakBand(band, coefficients, (reference_ids, reference_rt))
        )
        taken.update(owner.owner_id for owner in band)
    if not accepted:
        return tuple(LinkedPeakBand(band) for band in first)
    remaining = [
        (owner, rt) for owner, rt in coordinates if owner.owner_id not in taken
    ]
    return (*accepted, *(LinkedPeakBand(band) for band in link(remaining)))
