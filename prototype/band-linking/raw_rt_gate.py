"""PROTOTYPE: on raw RT, does a wider RT step bring the shifted injections back without hurting the rest?

Reference: linking on calibrated RT with the 0.4 min step. Buckets use the same
excess shift as raw_rt_check.py.
"""

import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import differ, inputs, owners  # noqa: E402

GATES = (0.4, 0.6, 0.8, 1.0)


def proto_labels(peaks, step=0.4):
    out = [None] * len(peaks)
    for r, band in enumerate(link_bands(peaks, max_rt_step=step)):
        keep = len(band) > 1 and sum(peaks[i][2] for i in band) >= 2
        for i in band:
            out[i] = r if keep else f"single:{i}"
    return out


def in_row(label):
    return not str(label).startswith("single")


def mates(labels_, d, i):
    return {j for j in d if labels_[j] == labels_[i]} if in_row(labels_[i]) else {i}


windows = []
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    ids, cal_peaks, _ = coordinates(ws, flatten=False)
    d = [i for i, p in enumerate(cal_peaks) if p[2]]
    if len(d) < 2:
        continue
    raw_rt = [float(o.split("|")[3]) for o in ids]
    offset = [raw_rt[i] - cal_peaks[i][1] for i in range(len(ids))]
    excess = {}
    for i in d:
        near = [offset[j] for j in range(len(ids))
                if 0 < abs(cal_peaks[j][0] - cal_peaks[i][0]) <= 8 and abs(cal_peaks[j][1] - cal_peaks[i][1]) <= 0.5]
        if near:
            excess[i] = abs(offset[i] - median(near))
    raw_peaks = [(p[0], raw_rt[i], p[2], p[3]) for i, p in enumerate(cal_peaks)]
    windows.append((d, excess, proto_labels(cal_peaks), raw_peaks))

total = sum(len(w[0]) for w in windows)
print(f"windows {len(windows)}, direct {total}; reference = calibrated RT, 0.4 min step")
for gate in GATES:
    s = Counter()
    kept = defaultdict(Counter)
    for d, excess, cal, raw_peaks in windows:
        raw = proto_labels(raw_peaks, gate)
        moved = differ(d, [raw[i] for i in d], [cal[i] for i in d])
        s["differ"] += moved
        s["identical"] += moved == 0
        for i, x in excess.items():
            if not in_row(cal[i]):
                continue
            b = "shift >= 0.4" if x >= 0.4 else "shift < 0.4"
            row_mates = mates(cal, d, i)
            kept[b]["n"] += 1
            kept[b]["kept"] += len(row_mates & mates(raw, d, i)) / len(row_mates) >= 0.5
    print(f"raw RT, step {gate:.1f} min: identical windows {s['identical']}/{len(windows)} ({100 * s['identical'] / len(windows):.0f}%),"
          f" direct placed differently {s['differ']} ({100 * s['differ'] / total:.1f}%);"
          + "".join(f" {b}: kept row mates {kept[b]['kept']}/{kept[b]['n']} ({100 * kept[b]['kept'] / kept[b]['n']:.0f}%);"
                    for b in ("shift < 0.4", "shift >= 0.4")))
