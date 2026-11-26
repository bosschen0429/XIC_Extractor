"""PROTOTYPE (throwaway): group one ion's peaks by following bands along injection order.

Question: if every sample's peaks of one m/z are laid out as "raw RT x injection
order" (the picture a person reads at a glance), does following each continuous
band produce the rows the eye sees, and where does it differ from the current
grouping?

Pure functions over plain tuples; no RAW, no files, no plotting.

A peak is (rank, rt, direct): rank is the injection-order position (0..N-1), rt is
the raw apex RT in minutes, direct says whether it has its own CID-NL evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

_NO = 1e9


@dataclass(frozen=True)
class Params:
    step_tol: float = 0.12  # min: RT change allowed between neighbouring injections
    gap_tol: float = 0.02  # min added per injection a band went unseen
    max_tol: float = 0.35  # min: never accept a larger jump
    max_gap: int = 12  # injections a band may go unseen and still continue
    slope_points: int = 5  # recent points used to follow the band's drift
    max_slope: float = 0.1  # min per injection


def _tolerance(p: Params, gap: int) -> float:
    return min(p.max_tol, p.step_tol + p.gap_tol * max(0, gap - 1))


def _predict(points: list[tuple[int, float]], rank: int, p: Params) -> tuple[float, int]:
    """Where the band should be at this injection, from its recent drift."""
    last_rank, last_rt = points[-1]
    recent = points[-p.slope_points :]
    slopes = [
        (b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(recent, recent[1:]) if b[0] > a[0]
    ]
    slope = float(np.clip(np.median(slopes), -p.max_slope, p.max_slope)) if slopes else 0.0
    gap = rank - last_rank
    return last_rt + slope * gap, gap


def _assign(costs: np.ndarray) -> list[tuple[int, int]]:
    if costs.size == 0:
        return []
    rows, cols = linear_sum_assignment(costs)
    return [(a, b) for a, b in zip(rows, cols) if costs[a, b] < _NO]


def follow_bands(peaks: list[tuple[int, float, bool]], p: Params = Params()) -> list[list[int]]:
    """Forward pass along injection order: each peak continues the band it fits best."""
    by_rank: dict[int, list[int]] = defaultdict(list)
    for index, (rank, _rt, _direct) in enumerate(peaks):
        by_rank[rank].append(index)
    bands: list[list[int]] = []
    for rank in sorted(by_rank):
        here = by_rank[rank]
        live = [
            b for b, band in enumerate(bands) if rank - peaks[band[-1]][0] <= p.max_gap
        ]
        costs = np.full((len(live), len(here)), _NO)
        for a, b in enumerate(live):
            pred, gap = _predict([(peaks[i][0], peaks[i][1]) for i in bands[b]], rank, p)
            tol = _tolerance(p, gap)
            for c, index in enumerate(here):
                distance = abs(peaks[index][1] - pred)
                if distance <= tol:
                    costs[a, c] = distance
        taken = set()
        for a, c in _assign(costs):
            bands[live[a]].append(here[c])
            taken.add(c)
        for c, index in enumerate(here):
            if c not in taken:
                bands.append([index])
    return bands


def _curve(points: list[tuple[int, float]]) -> tuple[np.ndarray, np.ndarray]:
    """The band's path: a running median over neighbouring injections."""
    ranks = np.array([r for r, _ in points])
    rts = np.array([t for _, t in points])
    smooth = np.array([np.median(rts[max(0, i - 2) : i + 3]) for i in range(len(rts))])
    return ranks, smooth


def settle(peaks: list[tuple[int, float, bool]], bands: list[list[int]], p: Params = Params()) -> list[list[int]]:
    """Give every injection's peaks to the nearest band path, one peak per band.

    Bands seen in fewer than three injections have no path; their peaks stay
    single unless a path claims them.
    """
    curves = []
    for band in bands:
        if len(band) >= 3:
            curves.append(_curve([(peaks[i][0], peaks[i][1]) for i in band]))
    by_rank: dict[int, list[int]] = defaultdict(list)
    for index, (rank, _rt, _direct) in enumerate(peaks):
        by_rank[rank].append(index)
    settled: list[list[int]] = [[] for _ in curves]
    single: list[list[int]] = []
    for rank, here in by_rank.items():
        positions = []
        for k, (ranks, smooth) in enumerate(curves):
            if ranks[0] - p.max_gap <= rank <= ranks[-1] + p.max_gap:
                inside = ranks[0] <= rank <= ranks[-1]
                gap = 1 if inside else min(abs(rank - ranks[0]), abs(rank - ranks[-1]))
                positions.append((k, float(np.interp(rank, ranks, smooth)), _tolerance(p, gap)))
        costs = np.full((len(positions), len(here)), _NO)
        for a, (_k, position, tol) in enumerate(positions):
            for c, index in enumerate(here):
                distance = abs(peaks[index][1] - position)
                if distance <= tol:
                    costs[a, c] = distance
        taken = set()
        for a, c in _assign(costs):
            settled[positions[a][0]].append(here[c])
            taken.add(c)
        single.extend([index] for c, index in enumerate(here) if c not in taken)
    return [band for band in settled if band] + single


def link_bands(
    peaks: list[tuple],
    max_rank_gap: int = 8,
    max_rt_step: float = 0.4,
    rank_weight: float = 0.005,
    mz_tolerance_ppm: float | None = 10.0,
) -> list[list[int]]:
    """What the eye does: join the closest peaks first, never two of one injection.

    Peaks within ``max_rank_gap`` injections and ``max_rt_step`` minutes are
    candidate links, closest first. Two partial bands join unless they already
    hold peaks from the same injection: a second peak in one injection is a
    second band.
    """
    n = len(peaks)
    order = sorted(range(n), key=lambda i: peaks[i][0])
    edges = []
    for a in range(n):
        i = order[a]
        for b in range(a + 1, n):
            j = order[b]
            gap = peaks[j][0] - peaks[i][0]
            if gap > max_rank_gap:
                break
            if gap == 0:
                continue
            step = abs(peaks[i][1] - peaks[j][1])
            if step <= max_rt_step:
                edges.append((step + rank_weight * gap, i, j))
    edges.sort()
    parent = list(range(n))
    ranks = [{peaks[i][0]} for i in range(n)]
    has_mz = mz_tolerance_ppm is not None and all(len(p) > 3 for p in peaks)
    low = [peaks[i][3] if has_mz else 0.0 for i in range(n)]
    high = list(low)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _weight, i, j in edges:
        a, b = find(i), find(j)
        if a == b or ranks[a] & ranks[b]:
            continue
        if has_mz:
            lo, hi = min(low[a], low[b]), max(high[a], high[b])
            if (hi - lo) / lo * 1e6 > mz_tolerance_ppm:
                continue
        if len(ranks[a]) < len(ranks[b]):
            a, b = b, a
        parent[b] = a
        ranks[a] |= ranks[b]
        if has_mz:
            low[a], high[a] = min(low[a], low[b]), max(high[a], high[b])
    bands: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        bands[find(i)].append(i)
    return list(bands.values())


def rows(peaks: list[tuple[int, float, bool]], p: Params = Params()) -> list[list[int]]:
    """Bands carrying at least two direct peaks are rows; everything else is single."""
    result = []
    for band in link_bands(peaks):
        if sum(peaks[i][2] for i in band) >= 2:
            result.append(band)
        else:
            result.extend([i] for i in band)
    return result


def adjusted_rand(a: list[int], b: list[int]) -> float:
    """Agreement of two labelings of the same items; 1 = identical partitions."""
    from math import comb

    pairs = defaultdict(int)
    for x, y in zip(a, b):
        pairs[(x, y)] += 1
    rows_a = defaultdict(int)
    rows_b = defaultdict(int)
    for (x, y), n in pairs.items():
        rows_a[x] += n
        rows_b[y] += n
    index = sum(comb(n, 2) for n in pairs.values())
    sum_a = sum(comb(n, 2) for n in rows_a.values())
    sum_b = sum(comb(n, 2) for n in rows_b.values())
    total = comb(len(a), 2)
    if total == 0:
        return 1.0
    expected = sum_a * sum_b / total
    maximum = (sum_a + sum_b) / 2
    return 1.0 if maximum == expected else (index - expected) / (maximum - expected)
