"""PROTOTYPE (throwaway): candidate B - trace ridges on the RT x injection signal plane.

Question: on the windows that already have traces, do ridges traced on the signal (no peak
picking, no B or M) give the per-band rows where those are clean, and do better where point
linking struggles: one compound cut into two peaks, injections with no picked peak, weak
tails such as 348.21's injections 45-85?

Read before running:
- B adds something A cannot if: 348.21's main band is one ridge (without B or M) holding at
  least as many direct peaks as the per-band main row; split pieces fall in one ridge
  envelope; ridges carry signal in injections with no picked peak; one-band windows stay one
  ridge; 334.05 gives two ridges with its 17 co-occurring injections split one each.
- A suffices if ridges give the same rows as per-band linking and 348.21 still breaks.
- This form of B fails if ridges merge 334.05 or follow another compound where theirs is absent.

Method (every value below is unvalidated): each injection's trace is mapped to calibrated RT
with that sample's calibration knots, resampled on one grid and scaled by its row maximum
(square root). Dynamic programming finds the path through all 85 injections that maximizes
summed signal minus LAMBDA x |RT step| (steps <= MAX_STEP). In each injection the path climbs
to the nearest local maximum; its envelope is the contiguous region above ENVELOPE x that
maximum, and is removed before the next ridge. Tracing stops when fewer than MIN_PRESENT
injections carry signal >= PRESENT along the path. A picked peak belongs to the ridge whose
envelope holds its calibrated apex in its injection; a ridge with >= 2 direct peaks is a row.
"""

import json
import pickle
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband, rows_labels  # noqa: E402
from whole_batch import differ, inputs, labels, owners, rank  # noqa: E402

HERE = Path(__file__).parent
LAMBDA, MAX_STEP, PRESENT, MIN_PRESENT, MAX_RIDGES, GRID_STEP, CLIMB = 1.5, 0.4, 0.25, 5, 8, 0.02, 5
ENVELOPE = float(__import__("os").environ.get("RIDGE_ENVELOPE", "0.2"))
samples = sorted(rank, key=rank.get)
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def traced_windows():
    results = pickle.loads((HERE / "results.pkl").read_bytes())
    traces = pickle.loads((HERE / "traces.pkl").read_bytes())
    extra = pickle.loads((HERE / "traces_perband_extra.pkl").read_bytes())
    return [(r["key"], traces[k]) for k, r in enumerate(results)] + list(extra.items())


def calibrated_image(ws, trace, lo, hi):
    grid = np.arange(lo, hi + GRID_STEP, GRID_STEP)
    image = np.zeros((len(samples), grid.size))
    for i, stem in enumerate(samples):
        x, y = trace[stem]
        fitted = ws.rt_calibration.samples.get(stem)
        if fitted is None or len(fitted.knots) < 2 or x.size < 2:
            continue
        kx = np.array([k[0] for k in fitted.knots])
        ky = np.array([k[1] for k in fitted.knots])
        xc = np.interp(x, kx, ky, left=np.nan, right=np.nan)
        ok = ~np.isnan(xc)
        if ok.sum() >= 2:
            image[i] = np.interp(grid, xc[ok], y[ok], left=0.0, right=0.0)
    return grid, image


def best_path(S):
    n, g = S.shape
    k = int(round(MAX_STEP / GRID_STEP))
    shifts = np.arange(-k, k + 1)
    penalty = LAMBDA * np.abs(shifts) * GRID_STEP
    best = S[0].copy()
    back = np.zeros((n, g), dtype=np.int16)
    cand = np.empty((shifts.size, g))
    for i in range(1, n):
        cand.fill(-np.inf)
        for j, s in enumerate(shifts):
            if s >= 0:
                cand[j, s:] = best[: g - s] - penalty[j]
            else:
                cand[j, : g + s] = best[-s:] - penalty[j]
        pick = np.argmax(cand, axis=0)
        best = S[i] + cand[pick, np.arange(g)]
        back[i] = shifts[pick]
    path = np.zeros(n, dtype=int)
    path[-1] = int(np.argmax(best))
    for i in range(n - 1, 0, -1):
        path[i - 1] = path[i] - back[i, path[i]]
    return path


def ridges_of(image):
    """Ridges as per-injection (left, right, centre, strength) in grid indices."""
    n, g = image.shape
    scale = image.max(axis=1, keepdims=True)
    scale[scale <= 0] = 1.0
    remaining = image.copy()
    out = []
    for _ in range(MAX_RIDGES):
        S = np.sqrt(np.clip(remaining / scale, 0, None))
        path = best_path(S)
        env = []
        for i in range(n):
            row = remaining[i]
            lo, hi = max(0, path[i] - CLIMB), min(g, path[i] + CLIMB + 1)
            p = lo + int(np.argmax(row[lo:hi]))
            if row[p] <= 0:
                env.append((p, p, p, 0.0))
                continue
            level = ENVELOPE * row[p]
            left = p
            while left > 0 and row[left - 1] > level:
                left -= 1
            right = p
            while right < g - 1 and row[right + 1] > level:
                right += 1
            env.append((left, right, p, float(np.sqrt(row[p] / scale[i, 0]))))
        if sum(e[3] >= PRESENT for e in env) < MIN_PRESENT:
            break
        for i, (left, right, _, _) in enumerate(env):
            remaining[i, left : right + 1] = 0.0
        out.append(env)
    return out


def assign(ridges, grid, peaks):
    """Ridge index per peak (or None): the ridge whose envelope holds the calibrated apex."""
    out = [None] * len(peaks)
    for i, p in enumerate(peaks):
        r = p[0]
        for k, env in enumerate(ridges):
            left, right, _, _ = env[r]
            if grid[left] - GRID_STEP / 2 <= p[1] <= grid[right] + GRID_STEP / 2:
                out[i] = k
                break
    return out


def ridge_labels(assigned, peaks):
    direct = Counter(k for k, p in zip(assigned, peaks) if k is not None and p[2])
    return [k if k is not None and direct[k] >= 2 else f"single:{i}" for i, k in enumerate(assigned)]


def summary_rows(lab, peaks):
    rows = defaultdict(list)
    for i, x in enumerate(lab):
        if not str(x).startswith("single"):
            rows[x].append(i)
    rows = sorted(rows.values(), key=lambda m: -sum(peaks[i][2] for i in m))
    return ", ".join(f"[{sum(peaks[i][2] for i in m)} d, inj {min(peaks[i][0] for i in m) + 1}-{max(peaks[i][0] for i in m) + 1},"
                     f" RT {min(peaks[i][1] for i in m):.1f}-{max(peaks[i][1] for i in m):.1f}]" for m in rows[:5])


if __name__ == "__main__":
    worksets = {w.key: w for w in inputs.worksets}
    stats = Counter()
    keep = {}
    seconds = 0.0
    for key, trace in traced_windows():
        ws = worksets[key]
        ids, peaks, _ = coordinates(ws, flatten=False)
        d = [i for i, p in enumerate(peaks) if p[2]]
        lo, hi = min(p[1] for p in peaks) - 0.4, max(p[1] for p in peaks) + 0.4
        grid, image = calibrated_image(ws, trace, lo, hi)
        start = time.perf_counter()
        ridges = ridges_of(image)
        seconds += time.perf_counter() - start
        assigned = assign(ridges, grid, peaks)
        rl = ridge_labels(assigned, peaks)
        pb = rows_labels(perband(ws, ids, peaks)[0], peaks)
        cl = rows_labels(link_bands(peaks), peaks)
        none = [labels["none"][key][o] for o in ids]
        moved = {name: differ(d, [rl[i] for i in d], [ref[i] for i in d]) for name, ref in
                 (("per-band", pb), ("cost search", none), ("calibrated", cl))}
        stats["windows"] += 1
        stats["direct"] += len(d)
        for name, m in moved.items():
            stats[f"differ {name}"] += m
            stats[f"identical {name}"] += m == 0
        stats["differ per-band vs cost search"] += differ(d, [pb[i] for i in d], [none[i] for i in d])
        stats["unassigned direct"] += sum(1 for i in d if assigned[i] is None)
        row_ridges = {k for k in set(assigned) if k is not None and not str(rl[assigned.index(k)]).startswith("single")}
        for k in row_ridges:
            env = ridges[k]
            members = Counter(peaks[i][0] for i, a in enumerate(assigned) if a == k)
            stats["split cells (2+ peaks in one envelope)"] += sum(1 for n in members.values() if n >= 2)
            stats["signal cells with no picked peak"] += sum(
                1 for r, e in enumerate(env) if e[3] >= PRESENT and r not in members)
        mz = float(key.split("|")[1])
        keep[key] = (mz, ids, peaks, grid, image, ridges, assigned, rl, pb, none, moved)
    print(f"windows {stats['windows']}, direct {stats['direct']}; ridge tracing {seconds:.1f} s")
    for name in ("per-band", "cost search", "calibrated"):
        print(f"  ridges vs {name:11s}: identical windows {stats[f'identical {name}']}/{stats['windows']},"
              f" direct placed differently {stats[f'differ {name}']} ({100 * stats[f'differ {name}'] / stats['direct']:.1f}%)")
    print(f"  (per-band vs cost search on the same windows: {stats['differ per-band vs cost search']}"
          f" ({100 * stats['differ per-band vs cost search'] / stats['direct']:.1f}%))")
    print(f"  direct peaks outside every ridge envelope: {stats['unassigned direct']}")
    print(f"  in row ridges: cells holding 2+ picked peaks {stats['split cells (2+ peaks in one envelope)']},"
          f" cells with signal >= {PRESENT} and no picked peak {stats['signal cells with no picked peak']}")

    print("\ntargets (rows: direct peaks, injections, calibrated RT):")
    for target in (348.2114, 302.1766, 334.0504, 504.19, 297.06, 311.14, 277.1251, 300.1602, 406.2242, 282.1507):
        hit = [k for k, v in keep.items() if abs(v[0] - target) < 0.005]
        if not hit:
            print(f"  {target:.2f}: not among the traced windows")
            continue
        mz, ids, peaks, grid, image, ridges, assigned, rl, pb, none, moved = keep[max(hit, key=lambda k: len(keep[k][1]))]
        print(f"  {target:.2f}: vs per-band {moved['per-band']}, vs cost search {moved['cost search']}")
        print(f"     ridges   {summary_rows(rl, peaks)}")
        print(f"     per-band {summary_rows(pb, peaks)}")
    pickle.dump({k: (v[0], v[3], v[4], v[5], v[6], v[7], v[8], v[2]) for k, v in keep.items()},
                open(HERE / f"ridges_result_{ENVELOPE}.pkl", "wb"))
