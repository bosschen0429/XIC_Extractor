"""PROTOTYPE: band linking on every workset; agreement with current rows and timing."""

import json
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

from scipy.optimize import linear_sum_assignment

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import rows  # noqa: E402

from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402
from xic_extractor.diagnostics.stage_snapshot import read_grouping_inputs  # noqa: E402

HERE = Path(__file__).parent
REPLAYS = HERE.parent / "band-threshold-85"
order = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
rank = {s: i for i, s in enumerate(sorted(order, key=order.get))}

owners = defaultdict(dict)
labels = {n: defaultdict(dict) for n in ("none", "21")}
for name in labels:
    with open(REPLAYS / f"replay-{name}.pkl", "rb") as handle:
        for g, row in enumerate(pickle.load(handle)):
            settled = row["disposition"] == "established" and len(row["owners"]) > 1
            for o in row["owners"]:
                owners[row["workset"]][o] = row["sample"][o]
                labels[name][row["workset"]][o] = g if settled else f"single:{o}"

inputs, _ = read_grouping_inputs(REPLAYS / "capture" / "grouping-inputs.json.gz")
base = SampleRtFrame(
    injection_order=inputs.injection_order,
    sample_cohorts=dict.fromkeys(inputs.sample_stems, "snapshot"),
    sample_universe=frozenset(inputs.sample_stems),
)


def differ(d, a, b):
    ra, rb = defaultdict(set), defaultdict(set)
    for i, x, y in zip(d, a, b):
        ra[x].add(i)
        rb[y].add(i)
    ka = [k for k in ra if len(ra[k]) > 1]
    kb = [k for k in rb if len(rb[k]) > 1]
    agree = sum(1 for x, y in zip(a, b) if len(ra[x]) == 1 and len(rb[y]) == 1)
    if ka and kb:
        overlap = [[len(ra[x] & rb[y]) for y in kb] for x in ka]
        r_, c_ = linear_sum_assignment([[-v for v in row] for row in overlap])
        agree += sum(overlap[r][c] for r, c in zip(r_, c_))
    return len(d) - agree


seconds = 0.0
total = moved_cur = moved_none = same_windows = windows = 0
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
    start = time.perf_counter()
    proto = [None] * len(ids)
    for r, members in enumerate(rows(peaks)):
        for i in members:
            proto[i] = r if len(members) > 1 else f"single:{i}"
    seconds += time.perf_counter() - start
    d = [i for i, p in enumerate(peaks) if p[2]]
    if not d:
        continue
    windows += 1
    pro = [proto[i] for i in d]
    cur = [labels["21"][ws.key][ids[i]] for i in d]
    nob = [labels["none"][ws.key][ids[i]] for i in d]
    a, b = differ(d, pro, cur), differ(d, pro, nob)
    total += len(d)
    moved_cur += a
    moved_none += b
    same_windows += a == 0
print(f"worksets with direct peaks: {windows}; identical to current: {same_windows}")
print(f"direct peaks: {total}; placed differently vs current: {moved_cur} ({100 * moved_cur / total:.1f}%); "
      f"vs no band: {moved_none} ({100 * moved_none / total:.1f}%)")
print(f"prototype grouping time, all worksets, one process: {seconds:.1f} s")
