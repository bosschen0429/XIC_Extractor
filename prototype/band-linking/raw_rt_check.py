"""PROTOTYPE: does band linking hold on raw RT, before any calibration exists?

Same linking as the calibrated run; only the coordinate changes. The question is
whether injections carrying a whole-injection shift stay in their bands on raw RT,
because a calibration fitted from raw-linked rows can only learn shifts of
injections those rows contain.

"Excess shift" of a peak: its calibration offset (raw - calibrated) minus the
median offset of other injections within 8 positions and 0.5 min (calibrated) in the window.
Slow batch drift cancels out; what remains is the jump raw-RT linking must cross.
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import differ, inputs, labels, owners  # noqa: E402

EDGES = (0.1, 0.2, 0.4)


def proto_labels(peaks):
    out = [None] * len(peaks)
    for r, band in enumerate(link_bands(peaks)):
        keep = len(band) > 1 and sum(peaks[i][2] for i in band) >= 2
        for i in band:
            out[i] = r if keep else f"single:{i}"
    return out


def in_row(label):
    return not str(label).startswith("single")


def bucket(x):
    for edge in EDGES:
        if x < edge:
            return f"< {edge}"
    return f">= {EDGES[-1]}"


def mates(labels_, d, i):
    return {j for j in d if labels_[j] == labels_[i]} if in_row(labels_[i]) else {i}


summary = Counter()
by_bucket = defaultdict(Counter)
by_sample = defaultdict(lambda: {"excess": [], "cal": 0, "raw": 0, "kept": 0})
detail = {}
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    ids, cal_peaks, _ = coordinates(ws, flatten=False)
    d = [i for i, p in enumerate(cal_peaks) if p[2]]
    if len(d) < 2:
        continue
    raw_rt = [float(o.split("|")[3]) for o in ids]
    raw_peaks = [(p[0], raw_rt[i], p[2], p[3]) for i, p in enumerate(cal_peaks)]
    cal = proto_labels(cal_peaks)
    raw = proto_labels(raw_peaks)
    none = [labels["none"][ws.key][o] for o in ids]
    summary["windows"] += 1
    summary["direct"] += len(d)
    for name, a, b in (("raw vs calibrated", raw, cal), ("raw vs no band", raw, none), ("calibrated vs no band", cal, none)):
        moved = differ(d, [a[i] for i in d], [b[i] for i in d])
        summary[f"{name}: differ"] += moved
        summary[f"{name}: identical windows"] += moved == 0
    summary["direct in rows, calibrated"] += sum(in_row(cal[i]) for i in d)
    summary["direct in rows, raw"] += sum(in_row(raw[i]) for i in d)
    offset = [raw_rt[i] - cal_peaks[i][1] for i in range(len(ids))]
    for i in d:
        near = [offset[j] for j in range(len(ids)) if 0 < abs(cal_peaks[j][0] - cal_peaks[i][0]) <= 8 and abs(cal_peaks[j][1] - cal_peaks[i][1]) <= 0.5]
        if not near:
            continue
        excess = abs(offset[i] - median(near))
        sample = owners[ws.key][ids[i]]
        c = by_bucket[bucket(excess)]
        s = by_sample[sample]
        c["direct"] += 1
        s["excess"].append(excess)
        if in_row(cal[i]):
            row_mates = mates(cal, d, i)
            kept = len(row_mates & mates(raw, d, i)) / len(row_mates) >= 0.5
            c["in a row (calibrated)"] += 1
            c["still in a row (raw)"] += in_row(raw[i])
            c["kept most row mates (raw)"] += kept
            s["cal"] += 1
            s["raw"] += in_row(raw[i])
            s["kept"] += kept
    if ws.key.split("|")[1].startswith("298.07"):
        detail[ws.key] = (ids, cal_peaks, raw_rt, cal, raw, offset, d)

print(f"windows with 2+ direct peaks: {summary['windows']}, direct peaks: {summary['direct']}")
for name in ("raw vs calibrated", "raw vs no band", "calibrated vs no band"):
    print(f"  {name:22s}: identical windows {summary[f'{name}: identical windows']}/{summary['windows']}"
          f" ({100 * summary[f'{name}: identical windows'] / summary['windows']:.0f}%),"
          f" direct placed differently {summary[f'{name}: differ']} ({100 * summary[f'{name}: differ'] / summary['direct']:.1f}%)")
print(f"  direct peaks in rows: calibrated {summary['direct in rows, calibrated']}, raw {summary['direct in rows, raw']}")
print("\nby excess shift (min) of direct peaks that sit in a row on calibrated RT:")
for key in [f"< {e}" for e in EDGES] + [f">= {EDGES[-1]}"]:
    c = by_bucket[key]
    n = c["in a row (calibrated)"]
    print(f"  {key:>7s}: direct {c['direct']:6d}, in a row {n:6d}, still in a row on raw {c['still in a row (raw)']:6d}"
          f" ({100 * c['still in a row (raw)'] / max(1, n):.0f}%), kept most row mates {c['kept most row mates (raw)']:6d}"
          f" ({100 * c['kept most row mates (raw)'] / max(1, n):.0f}%)")
print("\nsamples with the largest median excess shift:")
ranked = sorted(by_sample.items(), key=lambda kv: -median(kv[1]["excess"]))
for sample, s in ranked[:12]:
    print(f"  {sample:28s} median excess {median(s['excess']):.3f} min, in rows calibrated {s['cal']:4d},"
          f" raw {s['raw']:4d}, kept mates {s['kept']:4d} ({100 * s['kept'] / max(1, s['cal']):.0f}%)")
for key, (ids, cal_peaks, raw_rt, cal, raw, offset, d) in detail.items():
    shifted = [i for i in d if abs(raw_rt[i] - 8.6) > 0.3 and abs(cal_peaks[i][1] - 8.6) < 0.2]
    print(f"\n{key}: direct {len(d)}; direct peaks far from 8.6 on raw but near it calibrated: {len(shifted)}")
    for i in sorted(shifted, key=lambda i: cal_peaks[i][0]):
        print(f"  rank {cal_peaks[i][0]:3d} {owners[key][ids[i]]:28s} raw {raw_rt[i]:.3f} cal {cal_peaks[i][1]:.3f}"
              f"  row calibrated {in_row(cal[i])}, row raw {in_row(raw[i])},"
              f" raw row size {len(mates(raw, d, i))}")
