"""PROTOTYPE pre-checks for per-band drift.

1. 348.21: can each calibrated-RT segment fit its own drift coefficients
   (master needs >= 6 members and a B range >= 0.25)?
2. 302.1774: is the channel's drift anchor one compound or two, and which shape
   and coefficients does each band get?
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import base, inputs, owners  # noqa: E402

from xic_extractor.alignment.rt_drift import candidate_coefficients, _own_fit  # noqa: E402


def window(prefix):
    ws = next(w for w in inputs.worksets if w.key.split("|")[1].startswith(prefix) and w.key in owners)
    ids, peaks, _ = coordinates(ws, flatten=False)
    frame = base.with_warp(ws.rt_warp).with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    return ws, ids, peaks, frame.drift_model


def describe(ws, ids, peaks, drift, members, lookup):
    rts = [peaks[i][1] for i in members]
    shape = lookup([ids[i] for i in members], float(np.median(rts)))
    m = drift.sample_shift_min
    samples = [owners[ws.key][ids[i]] for i in members]
    b = [shape.get(s) for s in samples]
    known_b = [v for v in b if v is not None]
    pts = [(peaks[i][1], shape.get(s), m.get(s)) for i, s in zip(members, samples)]
    own = _own_fit(pts)
    ranks = sorted(peaks[i][0] + 1 for i in members)
    anchor = next((a for a in drift.anchors if set(a.owner_ids) & {ids[i] for i in members}), None)
    return (f"n {len(members):3d} direct {sum(peaks[i][2] for i in members):3d} injections {ranks[0]:2d}-{ranks[-1]:2d}"
            f" RT {min(rts):5.2f}-{max(rts):5.2f}  B range {max(known_b) - min(known_b) if known_b else 0:.2f}"
            f" (known {len(known_b)})  candidates {len(candidate_coefficients(pts))}"
            f"  own fit (time {own.time:+.2f}, shift {own.sample_shift:+.2f})"
            f"  shape from {'anchor ' + anchor.channel.split('|')[1][:8] if anchor else 'RT-borrowed blend'}")


if __name__ == "__main__":
    for prefix in ("348.21", "302.1766"):
        ws, ids, peaks, drift = window(prefix)
        lookup = drift.batch_state_lookup()
        print(f"\n== {ws.key}: {len(ids)} peaks, {sum(p[2] for p in peaks)} direct")
        own_anchor = [a for a in drift.anchors if a.channel == ws.key]
        for a in own_anchor:
            on = [i for i, o in enumerate(ids) if o in a.owner_ids]
            rts = sorted(round(peaks[i][1], 1) for i in on)
            print(f"  channel anchor: reference RT {a.reference_rt_min:.2f}, family {a.family},"
                  f" coefficients (time {a.coefficients.time:+.2f}, shift {a.coefficients.sample_shift:+.2f}),"
                  f" {len(a.owner_ids)} owners, {len(on)} in this window; their calibrated RTs: {rts}")
        if not own_anchor:
            print("  no channel anchor")
        segments = sorted((b for b in link_bands(peaks) if len(b) >= 2), key=len, reverse=True)
        print(f"  calibrated-RT segments with 2+ peaks: {len(segments)}")
        for k, seg in enumerate(segments[:10]):
            print(f"   seg{k}: " + describe(ws, ids, peaks, drift, seg, lookup))
