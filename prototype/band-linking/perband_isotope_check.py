"""PROTOTYPE: 299.15 / 300.16 / 301.16 look like one isotope cluster (spacing ~1.003).
Isotopes co-elute, so per injection the raw apex RT of 300.16's rows should match 299.15's
main row. Which grouping of 300.16 (per-band vs cost search) follows 299.15?"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, labels, owners  # noqa: E402


def window(target):
    ws = max((w for w in inputs.worksets if w.key in owners and abs(float(w.key.split("|")[1]) - target) < 0.01),
             key=lambda w: sum(o.split("|")[1] == "dda_cid_nl" for o in owners[w.key]))
    ids, peaks, _ = coordinates(ws, flatten=False)
    return ws, ids, peaks


def rows(ws, ids, peaks, which):
    if which == "per-band":
        bands, _ = perband(ws, ids, peaks)
        groups = [b for b in bands if sum(peaks[i][2] for i in b) >= 2 and len(b) > 1]
    else:
        g = defaultdict(list)
        for i, o in enumerate(ids):
            x = labels["none"][ws.key][o]
            if not str(x).startswith("single"):
                g[x].append(i)
        groups = [m for m in g.values() if sum(peaks[i][2] for i in m) >= 2]
    out = []
    for m in sorted(groups, key=lambda m: -sum(peaks[i][2] for i in m)):
        out.append({peaks[i][0]: float(ids[i].split("|")[3]) for i in m if peaks[i][2]})
    return out


if __name__ == "__main__":
    ws, ids, peaks = window(299.1548)
    ref = rows(ws, ids, peaks, "per-band")[0]
    print(f"299.15 main row (per-band; cost search agrees 100%): {len(ref)} direct peaks")
    for target in (300.1602, 301.1629):
        ws, ids, peaks = window(target)
        for which in ("per-band", "cost search"):
            for k, row in enumerate(rows(ws, ids, peaks, which)[:3]):
                shared = [r for r in row if r in ref]
                same = sum(1 for r in shared if abs(row[r] - ref[r]) <= 0.05)
                far = sum(1 for r in shared if abs(row[r] - ref[r]) > 0.05)
                print(f"  {target:.2f} {which:11s} row {k + 1}: {len(row):3d} direct; injections shared with 299.15 main row"
                      f" {len(shared):3d}: same RT (<= 3 s) {same:3d}, different {far:3d}")
