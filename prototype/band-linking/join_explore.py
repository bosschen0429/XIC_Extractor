"""PROTOTYPE exploration: drift-adjusted spread of prototype segments and of their unions."""

import pickle
import sys
from itertools import combinations
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from whole_batch import base, inputs, owners, rank  # noqa: E402

from xic_extractor.alignment.rt_drift import _own_fit  # noqa: E402


def spread(values):
    v = np.asarray(values)
    return float(1.4826 * np.median(np.abs(v - np.median(v)))) if v.size else 0.0


for target in sys.argv[2:]:
    ws = next(w for w in inputs.worksets if w.key.split("|")[1].startswith(target) and w.key in owners)
    frame = base.with_warp(ws.rt_warp).with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    drift = frame.drift_model
    lookup = drift.batch_state_lookup()
    ids = sorted(owners[ws.key])
    peaks = []
    for o in ids:
        f = o.split("|")
        cal = frame.calibrated_rt_min(owners[ws.key][o], float(f[3]))
        peaks.append((rank[owners[ws.key][o]], cal if cal is not None else float(f[3]), f[1] == "dda_cid_nl", float(f[2])))
    segs = [s for s in link_bands(peaks) if len(s) >= 2]
    segs.sort(key=lambda s: -len(s))

    def adjusted(members):
        rt = float(np.median([peaks[i][1] for i in members]))
        b = lookup([ids[i] for i in members], rt)
        pts = [(peaks[i][1], b.get(owners[ws.key][ids[i]]), drift.sample_shift_min.get(owners[ws.key][ids[i]])) for i in members]
        coef = _own_fit(pts)
        out = []
        for y, bb, mm in pts:
            off = coef.offset_min(bb, mm)
            out.append(y - (off or 0.0))
        return out, coef

    print(f"== {target}: {len(segs)} segments with 2+ peaks; anchors in model: {len(drift.anchors)}")
    for k, s in enumerate(segs[:8]):
        adj, coef = adjusted(s)
        ranks_ = sorted(peaks[i][0] for i in s)
        print(f"  seg{k}: n={len(s)} direct={sum(peaks[i][2] for i in s)} ranks {ranks_[0]}-{ranks_[-1]} "
              f"calRT {min(peaks[i][1] for i in s):.2f}-{max(peaks[i][1] for i in s):.2f} "
              f"adj spread {spread(adj) * 60:.1f}s coef=({coef.time:.2f},{coef.sample_shift:.2f})")
    for a, b in combinations(range(min(6, len(segs))), 2):
        sa, sb = segs[a], segs[b]
        shared = {peaks[i][0] for i in sa} & {peaks[i][0] for i in sb}
        if shared:
            continue
        adj, coef = adjusted(sa + sb)
        print(f"  union seg{a}+seg{b}: adj spread {spread(adj) * 60:.1f}s (own {spread(adjusted(sa)[0]) * 60:.1f}s, {spread(adjusted(sb)[0]) * 60:.1f}s)")
