"""PROTOTYPE: per-band drift, smallest version (no new thresholds).

1. Link every window on calibrated RT (same linking, 0.4 min step, +-8 injections).
2. Seeds: bands with >= 6 members, >= 2 direct peaks and a nonzero own drift fit
   (master's _own_fit against the band's B shape and M), in a fixed order:
   direct peaks, members, RT.
3. For each seed: flatten the remaining peaks of the window with the seed's own
   coefficients (a peak whose B or M is unknown stays out of this pass), link
   again, and accept only the band holding most of the seed's members.
   Coefficients come only from the already-linked seed, never from a union.
4. Remaining peaks are linked on calibrated RT. Rows hold >= 2 direct peaks.

Pass conditions, written before running:
- 348.21: one main row across the drift; its difference from the reference main
  row is not larger than the complementary version's.
- 302.1774: direct peaks in rows >= calibrated linking's; the ~23 min compound
  is its own row; the main row holds no member near 22.7 or 3.4 min.
- 334.05: two rows; in each co-occurring injection one peak goes to each row.
- 504.19, 297.06, 311.14: unchanged from calibrated linking.
- Whole batch: changes concentrate in windows with their own drift anchor;
  many changes elsewhere are investigated before being called improvements.
"""

import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import base, differ, inputs, labels, owners  # noqa: E402

from xic_extractor.alignment.rt_drift import MIN_COEFFICIENT_MEMBERS, _own_fit  # noqa: E402

_lookups = {}
FITS = Counter()


def drift_of(ws, override=None):
    """The workset's frozen drift model, or ``override`` when given."""
    if override is not None:
        drift = override
    else:
        frame = base.with_warp(ws.rt_warp).with_sample_calibration(ws.rt_calibration, ws.rt_drift)
        drift = frame.drift_model
    if drift is not None and id(drift) not in _lookups:
        _lookups[id(drift)] = drift.batch_state_lookup()
    return drift, _lookups.get(id(drift))


def own_fit(ws, ids, peaks, members, drift, lookup):
    """The band's coefficients and the B shape they were fitted against."""
    FITS["fits"] += 1
    shape = lookup([ids[i] for i in members], float(np.median([peaks[i][1] for i in members])))
    m = drift.sample_shift_min
    pts = [(peaks[i][1], shape.get(owners[ws.key][ids[i]]), m.get(owners[ws.key][ids[i]])) for i in members]
    return _own_fit(pts), shape


def rows_labels(bands, peaks):
    out = [f"single:{i}" for i in range(len(peaks))]
    for r, band in enumerate(bands):
        if len(band) > 1 and sum(peaks[i][2] for i in band) >= 2:
            for i in band:
                out[i] = r
    return out


def perband(ws, ids, peaks, trace=None, drift=None, carry=False):
    """Bands after seeding with each band's own drift fit.

    With ``carry``, a seed member whose offset is unknown stays in the accepted band when
    its injection is not already there: it linked without the drift model.
    """
    drift, lookup = drift_of(ws, drift)
    first = link_bands(peaks)
    if drift is None or not drift.anchors:
        return first, []
    seeds = []
    for band in first:
        direct = sum(peaks[i][2] for i in band)
        if len(band) >= MIN_COEFFICIENT_MEMBERS and direct >= 2:
            coef, shape = own_fit(ws, ids, peaks, band, drift, lookup)
            if coef.nonzero_count:
                seeds.append((-direct, -len(band), float(np.median([peaks[i][1] for i in band])), band, coef, shape))
    seeds.sort(key=lambda s: s[:3])
    taken = set()
    accepted = []
    for *_, band, coef, shape in seeds:
        if taken & set(band):
            continue
        idx, flat, unknown = [], [], []
        members = set(band)
        for i, p in enumerate(peaks):
            if i in taken:
                continue
            s = owners[ws.key][ids[i]]
            offset = coef.offset_min(shape.get(s), drift.sample_shift_min.get(s))
            if offset is None:
                if carry and i in members:
                    unknown.append(i)
                continue
            idx.append(i)
            flat.append((p[0], p[1] - offset, p[2], p[3]))
        relinked = [[idx[k] for k in b] for b in link_bands(flat)]
        if not relinked:
            continue
        best = max(relinked, key=lambda b: (len(set(b) & set(band)), len(b)))
        if carry:
            used = {peaks[i][0] for i in best}
            best = best + [i for i in unknown if peaks[i][0] not in used]
        if len(set(best) & set(band)) == 0 or sum(peaks[i][2] for i in best) < 2:
            continue
        accepted.append(best)
        taken |= set(best)
        if trace is not None:
            trace.append((len(band), len(best), coef))
    rest = [i for i in range(len(peaks)) if i not in taken]
    tail = [[rest[k] for k in b] for b in link_bands([peaks[i] for i in rest])]
    return accepted + tail, accepted


def describe_rows(peaks, bands, limit=6):
    rows = sorted((b for b in bands if len(b) > 1 and sum(peaks[i][2] for i in b) >= 2),
                  key=lambda b: -sum(peaks[i][2] for i in b))
    return ", ".join(
        f"[{len(b)} n, {sum(peaks[i][2] for i in b)} d, inj {min(peaks[i][0] for i in b) + 1}-{max(peaks[i][0] for i in b) + 1},"
        f" RT {min(peaks[i][1] for i in b):.1f}-{max(peaks[i][1] for i in b):.1f}]" for b in rows[:limit])


def label_rows(lab, peaks):
    groups = defaultdict(list)
    for i, x in enumerate(lab):
        if not str(x).startswith("single"):
            groups[x].append(i)
    return [g for g in groups.values() if sum(peaks[i][2] for i in g) >= 2]


def main_row_gap(a_rows, b_rows, peaks):
    """Direct peaks in the larger main row that the other main row does not share."""
    def main(rows):
        return set(max(rows, key=lambda r: sum(peaks[i][2] for i in r))) if rows else set()
    a, b = main(a_rows), main(b_rows)
    return sum(peaks[i][2] for i in a ^ b)


if __name__ == "__main__":
    targets = (348.2114, 302.1766, 334.0504, 504.19, 297.06, 311.14)
    print("== target windows (rows: members, direct, injections, calibrated RT)")
    for target in targets:
        ws = max((w for w in inputs.worksets if w.key in owners and abs(float(w.key.split("|")[1]) - target) < 0.01),
                 key=lambda w: sum(o.split("|")[1] == "dda_cid_nl" for o in owners[w.key]))
        ids, peaks, _ = coordinates(ws, flatten=False)
        _, flat_peaks, anchor = coordinates(ws, flatten=True)
        trace = []
        start = time.perf_counter()
        bands, accepted = perband(ws, ids, peaks, trace)
        seconds = time.perf_counter() - start
        cal = link_bands(peaks)
        comp = link_bands(flat_peaks)
        none_rows = label_rows([labels["none"][ws.key][o] for o in ids], peaks)
        d = [i for i, p in enumerate(peaks) if p[2]]
        print(f"\n{ws.key.split('|')[1][:9]} ({'own anchor' if anchor else 'no own anchor'}), direct {len(d)}, {seconds * 1000:.0f} ms,"
              f" seeds used {[(n, m, round(c.time, 2), round(c.sample_shift, 2)) for n, m, c in trace]}")
        for name, b in (("calibrated", cal), ("complementary", comp), ("per-band", bands)):
            lab = rows_labels(b, peaks)
            in_rows = sum(1 for i in d if not str(lab[i]).startswith("single"))
            gap = main_row_gap(label_rows(lab, peaks), none_rows, peaks)
            moved = differ(d, [lab[i] for i in d], [labels["none"][ws.key][ids[i]] for i in d])
            print(f"  {name:13s} direct in rows {in_rows:3d}; vs no-band: placed differently {moved:3d},"
                  f" main-row gap {gap:3d};  rows {describe_rows(peaks, b)}")
        print(f"  {'no-band':13s} rows {describe_rows(peaks, none_rows)}")
        per_injection = Counter(peaks[i][0] for i in d)
        shared = [r for r, n in per_injection.items() if n >= 2]
        rows2 = sorted((b for b in bands if sum(peaks[i][2] for i in b) >= 2), key=lambda b: -sum(peaks[i][2] for i in b))[:2]
        if len(rows2) == 2 and shared:
            both = sum(1 for r in shared if any(peaks[i][0] == r and peaks[i][2] for i in rows2[0])
                       and any(peaks[i][0] == r and peaks[i][2] for i in rows2[1]))
            print(f"  per-band: injections with 2+ direct peaks {len(shared)}, of which both top rows hold one: {both}")

    print("\n== whole batch")
    stats = defaultdict(Counter)
    joined = []
    seconds = 0.0
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, peaks, anchor = coordinates(ws, flatten=False)
        d = [i for i, p in enumerate(peaks) if p[2]]
        if len(d) < 2:
            continue
        own_anchor = anchor is not None
        _, _, anchor = coordinates(ws, flatten=True)
        group = "own anchor" if anchor is not None else "no own anchor"
        start = time.perf_counter()
        bands, accepted = perband(ws, ids, peaks)
        seconds += time.perf_counter() - start
        cal_bands = link_bands(peaks)
        pb, cl = rows_labels(bands, peaks), rows_labels(cal_bands, peaks)
        none = [labels["none"][ws.key][o] for o in ids]
        s = stats[group]
        s["windows"] += 1
        s["direct"] += len(d)
        s["seeded bands"] += len(accepted)
        moved_cal = differ(d, [pb[i] for i in d], [cl[i] for i in d])
        s["changed vs calibrated"] += moved_cal > 0
        s["moved vs calibrated"] += moved_cal
        s["moved vs no-band (per-band)"] += differ(d, [pb[i] for i in d], [none[i] for i in d])
        s["moved vs no-band (calibrated)"] += differ(d, [cl[i] for i in d], [none[i] for i in d])
        s["identical vs no-band (per-band)"] += differ(d, [pb[i] for i in d], [none[i] for i in d]) == 0
        s["identical vs no-band (calibrated)"] += differ(d, [cl[i] for i in d], [none[i] for i in d]) == 0
        cal_of = {i: k for k, b in enumerate(cal_bands) for i in b}
        for row in accepted:
            parts = defaultdict(list)
            for i in row:
                if peaks[i][2]:
                    parts[cal_of[i]].append(i)
            pieces = [p for p in parts.values() if len(p) >= 2]
            if len(pieces) >= 2:
                pieces.sort(key=lambda p: -len(p))
                a, b = pieces[0], pieces[1]
                overlap = {peaks[i][0] for i in a} & {peaks[i][0] for i in b}
                joined.append((ws.key.split("|")[1][:9], group, len(pieces), len(a), len(b), len(overlap),
                               abs(np.median([peaks[i][1] for i in a]) - np.median([peaks[i][1] for i in b]))))
    for group in ("own anchor", "no own anchor"):
        s = stats[group]
        w, n = s["windows"], s["direct"]
        print(f"[{group}] windows {w}, direct {n}, seeded bands {s['seeded bands']},"
              f" windows changed vs calibrated {s['changed vs calibrated']}, direct moved {s['moved vs calibrated']}"
              f" ({100 * s['moved vs calibrated'] / n:.1f}%)")
        print(f"   vs no-band: calibrated identical {s['identical vs no-band (calibrated)']}/{w}, differ"
              f" {100 * s['moved vs no-band (calibrated)'] / n:.1f}%; per-band identical"
              f" {s['identical vs no-band (per-band)']}/{w}, differ {100 * s['moved vs no-band (per-band)'] / n:.1f}%")
    print(f"per-band linking time, all windows: {seconds:.2f} s; drift fits {FITS['fits']}")
    print(f"rows that joined 2+ calibrated pieces (direct >= 2 each): {len(joined)}")
    for item in sorted(joined, key=lambda x: -x[6])[:25]:
        print(f"  {item[0]} [{item[1]}] pieces {item[2]}, sizes {item[3]}+{item[4]}, shared injections {item[5]},"
              f" RT gap {item[6]:.2f} min")
