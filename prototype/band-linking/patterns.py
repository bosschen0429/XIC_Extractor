"""PROTOTYPE: where do the prototype and the current rows disagree? Bin windows by
how often one injection holds two or more peaks of the ion."""

import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import rows  # noqa: E402
from whole_batch import base, differ, inputs, labels, owners, rank  # noqa: E402


def share(a, b):
    return f"{a}/{b} ({100 * a / max(1, b):.1f}%)"


bins = defaultdict(lambda: [0, 0, 0, 0])  # windows, identical, direct, differ
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    frame = base.with_warp(ws.rt_warp)
    if ws.rt_calibration is not None:
        frame = frame.with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    ids = sorted(owners[ws.key])
    peaks = []
    for o in ids:
        raw = float(o.split("|")[3])
        cal = frame.calibrated_rt_min(owners[ws.key][o], raw)
        peaks.append((rank[owners[ws.key][o]], raw if cal is None else cal, o.split("|")[1] == "dda_cid_nl", float(o.split("|")[2])))
    d = [i for i, p in enumerate(peaks) if p[2]]
    if len(d) < 2:
        continue
    per_injection = Counter(p[0] for p in peaks)
    multi = sum(1 for n in per_injection.values() if n >= 2) / len(per_injection)
    label = "none" if multi == 0 else "<10%" if multi < 0.1 else "10-30%" if multi < 0.3 else ">=30%"
    proto = [None] * len(ids)
    for r, members in enumerate(rows(peaks)):
        for i in members:
            proto[i] = r if len(members) > 1 else f"single:{i}"
    moved = differ(d, [proto[i] for i in d], [labels["none"][ws.key][ids[i]] for i in d])
    b = bins[label]
    b[0] += 1
    b[1] += moved == 0
    b[2] += len(d)
    b[3] += moved
print("share of injections holding 2+ peaks of the ion | windows identical | direct peaks placed differently")
for label in ("none", "<10%", "10-30%", ">=30%"):
    w, same, n, m = bins[label]
    print(f"{label:>7s}: windows {w:4d}, identical {share(same, w)}, differ {share(m, n)}")
