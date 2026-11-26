"""PROTOTYPE exploration: link in drift-flattened coordinates where the channel has an anchor."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from whole_batch import base, inputs, labels, owners, rank  # noqa: E402


def coordinates(ws, flatten):
    frame = base.with_warp(ws.rt_warp)
    if ws.rt_calibration is not None:
        frame = frame.with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    drift = frame.drift_model
    anchor = next((a for a in drift.anchors if a.channel == ws.key), None) if (drift and flatten) else None
    ids = sorted(owners[ws.key])
    peaks = []
    for o in ids:
        f = o.split("|")
        s = owners[ws.key][o]
        y = frame.calibrated_rt_min(s, float(f[3]))
        y = float(f[3]) if y is None else y
        if anchor is not None:
            off = anchor.coefficients.offset_min(anchor.shape.get(s), drift.sample_shift_min.get(s))
            if off is not None:
                y -= off
        peaks.append((rank[s], y, f[1] == "dda_cid_nl", float(f[2])))
    return ids, peaks, anchor


for target in sys.argv[2:]:
    ws = next(w for w in inputs.worksets if w.key.split("|")[1].startswith(target) and w.key in owners)
    for flatten in (False, True):
        ids, peaks, anchor = coordinates(ws, flatten)
        segs = sorted((s for s in link_bands(peaks) if sum(peaks[i][2] for i in s) >= 2), key=len, reverse=True)
        desc = [(len(s), sum(peaks[i][2] for i in s), min(peaks[i][0] for i in s), max(peaks[i][0] for i in s)) for s in segs[:6]]
        print(f"{target} flatten={flatten} anchor={'yes' if anchor else 'no'} rows(n, direct, rank range): {desc}")
    none_rows = Counter(labels['none'][ws.key][o] for o in ids if o.split('|')[1] == 'dda_cid_nl')
    print(f"   no-band rows (direct counts): {sorted((n for k, n in none_rows.items() if not str(k).startswith('single')), reverse=True)}")
