"""PROTOTYPE: every row the per-band pass joined from 2+ calibrated pieces, checked against
the no-band (cost search) grouping: does the cost search hold those pieces in one row too?"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, labels, owners  # noqa: E402

HERE = Path(__file__).parent

if __name__ == "__main__":
    joins = []
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, peaks, _ = coordinates(ws, flatten=False)
        if sum(p[2] for p in peaks) < 2:
            continue
        _, _, anchor = coordinates(ws, flatten=True)
        bands, accepted = perband(ws, ids, peaks)
        cal_of = {i: k for k, b in enumerate(link_bands(peaks)) for i in b}
        none = [labels["none"][ws.key][o] for o in ids]
        for row in accepted:
            parts = defaultdict(list)
            for i in row:
                if peaks[i][2]:
                    parts[cal_of[i]].append(i)
            pieces = sorted((p for p in parts.values() if len(p) >= 2), key=len, reverse=True)
            if len(pieces) < 2:
                continue
            direct = [i for i in row if peaks[i][2]]
            together = defaultdict(int)
            for i in direct:
                if not str(none[i]).startswith("single"):
                    together[none[i]] += 1
            best = max(together.values(), default=0)
            joins.append(dict(
                key=ws.key, mz=float(ws.key.split("|")[1]), anchored=anchor is not None,
                pieces=[len(p) for p in pieces], direct=len(direct),
                rt_gap=float(abs(np.median([peaks[i][1] for i in pieces[0]]) - np.median([peaks[i][1] for i in pieces[1]]))),
                agree=best / len(direct),
                span=(min(peaks[i][0] for i in row) + 1, max(peaks[i][0] for i in row) + 1),
                rt=(min(peaks[i][1] for i in row), max(peaks[i][1] for i in row)),
            ))
    joins.sort(key=lambda j: j["agree"])
    print(f"joined rows: {len(joins)}; no-band holds >= 90% of the row's direct peaks in one row: "
          f"{sum(j['agree'] >= 0.9 for j in joins)}")
    for j in joins:
        print(f"  {j['mz']:9.4f} {'anchored' if j['anchored'] else 'borrowed':8s} direct {j['direct']:3d} pieces {j['pieces']}"
              f" RT gap {j['rt_gap']:.2f}  inj {j['span'][0]}-{j['span'][1]} RT {j['rt'][0]:.1f}-{j['rt'][1]:.1f}"
              f"  no-band agreement {100 * j['agree']:.0f}%")
    (HERE / "perband_joins.json").write_text(json.dumps(joins, indent=1), encoding="utf-8")
