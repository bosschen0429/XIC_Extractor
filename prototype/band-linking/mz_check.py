"""PROTOTYPE check: do the 'one band split' windows actually span more than the m/z tolerance?"""

import pickle
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
REPLAYS = HERE.parent / "band-threshold-85"


def mz(o):
    return float(o.split("|")[2])


def span_ppm(ids):
    v = [mz(o) for o in ids]
    return (max(v) - min(v)) / min(v) * 1e6 if len(v) > 1 else 0.0


for name in ("21", "none"):
    rows = pickle.load(open(REPLAYS / f"replay-{name}.pkl", "rb"))
    for target in ("504.19", "297.05", "311.14"):
        sel = [r for r in rows if r["workset"].split("|")[1].startswith(target) and len(r["direct"]) > 1]
        alld = [o for r in rows if r["workset"].split("|")[1].startswith(target) for o in r["direct"]]
        print(f"{name:>4s} {target}: all direct span {span_ppm(alld):.1f} ppm |",
              [(len(r["direct"]), round(min(map(mz, r["direct"])), 4), round(max(map(mz, r["direct"])), 4),
                round(span_ppm(r["direct"]), 1), r["reason"][:22]) for r in sorted(sel, key=lambda r: -len(r["direct"]))])
