"""PROTOTYPE: export the sampled windows to one interactive HTML viewer."""

import base64
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

HERE = Path(__file__).parent
order = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
samples = sorted(order, key=order.get)
GRID = 320
KIND = {"random": "一般窗口", "join": "難窗口", "slow": "最慢窗口"}
WATCHED = ("302.1669", "302.1774", "334.0511", "348.2118", "288.0660")

results = pickle.loads((HERE / "results.pkl").read_bytes())
traces = pickle.loads((HERE / "traces.pkl").read_bytes())


def rows_of(labels, direct):
    groups = defaultdict(list)
    for i, x in enumerate(labels):
        groups[x].append(i)
    rows = [m for m in groups.values() if len(m) > 1 and sum(direct[i] for i in m) >= 2]
    return sorted(rows, key=lambda m: (-sum(direct[i] for i in m), -len(m)))


def pair(reference, other, direct):
    """Pair rows of `other` to rows of `reference` by largest direct-peak overlap."""
    mapping = {}
    if reference and other:
        overlap = [[sum(1 for i in set(a) & set(b) if direct[i]) for b in other] for a in reference]
        r_, c_ = linear_sum_assignment([[-v for v in row] for row in overlap])
        for a, b in zip(r_, c_):
            if overlap[a][b] > 0:
                mapping[int(b)] = int(a)
    return mapping


windows = []
for k, r in enumerate(results):
    direct = [p[2] for p in r["peaks"]]
    n = len(direct)
    lo, hi = max(0.0, min(r["raw_rt"]) - 0.4), max(r["raw_rt"]) + 0.4
    grid = np.linspace(lo, hi, GRID)
    image = np.zeros((len(samples), GRID), dtype=np.uint8)
    for i, stem in enumerate(samples):
        x, y = traces[k][stem]
        if x.size and y.max() > 0:
            image[i] = np.round(255 * np.sqrt(np.interp(grid, x, y / y.max(), left=0, right=0)))
    groupings = [rows_of(r["proto"], direct), rows_of(r["nobands"], direct), rows_of(r["current"], direct)]
    # colours follow the prototype's rows; the other groupings borrow the colour of the row they overlap most
    colour = [list(range(len(groupings[0])))]
    for g in (1, 2):
        mapping = pair(groupings[0], groupings[g], direct)
        spare = iter(range(len(groupings[0]), 1000))
        colour.append([mapping[b] if b in mapping else next(spare) for b in range(len(groupings[g]))])
    member = [[-1] * n for _ in range(3)]
    for g, rows in enumerate(groupings):
        for b, row in enumerate(rows):
            for i in row:
                member[g][i] = b
    nb_pair = [pair(groupings[1], groupings[g], direct) for g in range(3)]
    diff = [[0] * n for _ in range(3)]
    for g in (0, 2):
        for i in range(n):
            mine, theirs = member[g][i], member[1][i]
            if (mine < 0) != (theirs < 0) or (mine >= 0 and nb_pair[g].get(mine) != theirs):
                diff[g][i] = 1
    windows.append(dict(
        id=k, mz=round(r["mz"], 4), kind=KIND[r["kind"]], anchored=bool(r.get("anchored")),
        watched=any(f"{r['mz']:.4f}".startswith(w[:7]) for w in WATCHED),
        nd=r["n_direct"], nm=r["n_ms1"], rows=[len(x) for x in groupings],
        moved=[sum(diff[0][i] for i in range(n) if direct[i]), 0, sum(diff[2][i] for i in range(n) if direct[i])],
        lo=round(lo, 4), hi=round(hi, 4), img=base64.b64encode(image.tobytes()).decode("ascii"),
        r=[samples.index(s) for s in r["samples"]], t=[round(v, 3) for v in r["raw_rt"]],
        d=[int(v) for v in direct], z=[round(p[3], 4) for p in r["peaks"]],
        row=member, col=[[colour[g][b] if b >= 0 else -1 for b in member[g]] for g in range(3)], diff=diff,
    ))

data = dict(samples=samples, orders=[order[s] for s in samples], grid=GRID, windows=windows)
template = (HERE / "viewer_template.html").read_text(encoding="utf-8")
out.write_text(template.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))), encoding="utf-8")
print(out, round(out.stat().st_size / 1e6, 2), "MB", len(windows), "windows")
