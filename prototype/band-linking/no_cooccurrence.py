"""PROTOTYPE: in windows where no injection holds two peaks of the ion, who splits more?"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import rows  # noqa: E402
from whole_batch import base, differ, inputs, labels, owners, rank  # noqa: E402

direction = Counter()
examples = []
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
        peaks.append((rank[owners[ws.key][o]], raw if cal is None else cal, o.split("|")[1] == "dda_cid_nl"))
    d = [i for i, p in enumerate(peaks) if p[2]]
    if len(d) < 2 or max(Counter(p[0] for p in peaks).values()) > 1:
        continue
    proto = [None] * len(ids)
    for r, members in enumerate(rows(peaks)):
        for i in members:
            proto[i] = r if len(members) > 1 else f"single:{i}"
    cur = [labels["21"][ws.key][ids[i]] for i in d]
    pro = [proto[i] for i in d]
    if differ(d, pro, cur) == 0:
        continue
    n_cur = len({x for x in cur if not str(x).startswith("single")})
    n_pro = len({x for x in pro if not str(x).startswith("single")})
    singles_cur = sum(1 for x in cur if str(x).startswith("single"))
    singles_pro = sum(1 for x in pro if str(x).startswith("single"))
    kind = ("current splits more" if n_cur > n_pro else "prototype splits more" if n_pro > n_cur
            else "same row count")
    direction[kind] += 1
    spread = [peaks[i][1] for i in d]
    examples.append((kind, ws.key.split("|")[1][:9], len(d), n_pro, n_cur, singles_pro, singles_cur,
                     round(max(spread) - min(spread), 2)))
print("windows without co-occurrence that disagree:", dict(direction))
print("(kind, m/z, direct, rows proto, rows current, singles proto, singles current, calibrated RT span)")
for e in sorted(examples, key=lambda e: -e[2])[:15]:
    print("  ", e)
