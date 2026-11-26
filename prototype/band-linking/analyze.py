"""PROTOTYPE driver: run band tracking on 85RAW worksets and compare with current rows."""

import json
import pickle
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import adjusted_rand, rows  # noqa: E402

HERE = Path(__file__).parent
REPLAYS = HERE.parent / "band-threshold-85"
ORDER = Path(sys.argv[1])  # 85-injection-order.json
BAND = "corrected_cid_nl_reference_band"


def load(name):
    with open(REPLAYS / f"replay-{name}.pkl", "rb") as handle:
        return pickle.load(handle)


order = json.loads(ORDER.read_text(encoding="utf-8"))
rank = {s: i for i, s in enumerate(sorted(order, key=order.get))}
replays = {name: load(name) for name in ("none", "21")}

owners = defaultdict(dict)  # workset -> owner -> sample
labels = {name: defaultdict(dict) for name in replays}  # name -> workset -> owner -> label
established = {name: defaultdict(int) for name in replays}
for name, groups in replays.items():
    for g, row in enumerate(groups):
        ws = row["workset"]
        settled = row["disposition"] == "established" and len(row["owners"]) > 1
        if settled:
            established[name][ws] += 1
        for o in row["owners"]:
            owners[ws][o] = row["sample"][o]
            labels[name][ws][o] = g if settled else f"single:{o}"
print("method tokens:", Counter(o.split("|")[1] for ws in owners.values() for o in ws).most_common())

# windows: band joins at threshold 21, the two slow windows, and a random draw
none_rows = defaultdict(list)
for row in replays["none"]:
    if row["disposition"] == "established" and len(row["owners"]) > 1:
        none_rows[row["workset"]].append(set(row["direct"]))
joins = set()
for row in replays["21"]:
    if row["reason"] == BAND:
        band = set(row["direct"])
        if sum(1 for r in none_rows[row["workset"]] if r & band) > 1:
            joins.add(row["workset"])


def direct(o):
    return o.split("|")[1] == "dda_cid_nl"


slow = set()
for prefix in ("245.10", "293.17"):
    keys = [k for k in owners if k.split("|")[1].startswith(prefix)]
    if keys:
        slow.add(max(keys, key=lambda k: len(owners[k])))
pool = sorted(k for k in owners if k not in joins | slow and sum(map(direct, owners[k])) >= 20)
drawn = set(random.Random(1286).sample(pool, min(20, len(pool))))
windows = [(k, "join") for k in sorted(joins)] + [(k, "slow") for k in sorted(slow)] + [
    (k, "random") for k in sorted(drawn)
]

from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402
from xic_extractor.diagnostics.stage_snapshot import read_grouping_inputs  # noqa: E402

inputs, _ = read_grouping_inputs(REPLAYS / "capture" / "grouping-inputs.json.gz")
base = SampleRtFrame(
    injection_order=inputs.injection_order,
    sample_cohorts=dict.fromkeys(inputs.sample_stems, "snapshot"),
    sample_universe=frozenset(inputs.sample_stems),
)
worksets = {w.key: w for w in inputs.worksets}
uncalibrated = 0

results = []
for key, kind in windows:
    ws = worksets[key]
    frame = base.with_warp(ws.rt_warp)
    if ws.rt_calibration is not None:
        frame = frame.with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    drift = frame.drift_model
    anchor = next((a for a in drift.anchors if a.channel == key), None) if drift else None
    ids = sorted(owners[key])
    peaks = []
    raw_rt = []
    for o in ids:
        f = o.split("|")
        raw = float(f[3])
        sample = owners[key][o]
        calibrated = frame.calibrated_rt_min(sample, raw)
        if calibrated is None:
            uncalibrated += 1
            calibrated = raw
        if anchor is not None:
            offset = anchor.coefficients.offset_min(anchor.shape.get(sample), drift.sample_shift_min.get(sample))
            if offset is not None:
                calibrated -= offset
        raw_rt.append(raw)
        peaks.append((rank[sample], calibrated, direct(o), float(f[2])))
    proto = [None] * len(ids)
    for r, members in enumerate(rows(peaks)):
        for i in members:
            proto[i] = r if len(members) > 1 else f"single:{i}"
    d = [i for i, o in enumerate(ids) if direct(o)]
    cur = [labels["21"][key][ids[i]] for i in d]
    nob = [labels["none"][key][ids[i]] for i in d]
    pro = [proto[i] for i in d]

    def mates(lab):
        groups = defaultdict(set)
        for i, x in zip(d, lab):
            groups[x].add(i)
        return {i: frozenset(groups[x]) for i, x in zip(d, lab)}

    def differ(a, b):
        """Direct peaks placed differently after pairing rows by largest overlap."""
        from scipy.optimize import linear_sum_assignment

        ra = defaultdict(set)
        rb = defaultdict(set)
        for i, x, y in zip(d, a, b):
            ra[x].add(i)
            rb[y].add(i)
        ka = [k for k in ra if len(ra[k]) > 1]
        kb = [k for k in rb if len(rb[k]) > 1]
        agree = sum(1 for x, y in zip(a, b) if len(ra[x]) == 1 and len(rb[y]) == 1)
        if ka and kb:
            overlap = [[len(ra[x] & rb[y]) for y in kb] for x in ka]
            rows_, cols_ = linear_sum_assignment([[-v for v in row] for row in overlap])
            agree += sum(overlap[r][c] for r, c in zip(rows_, cols_))
        return len(d) - agree

    moved = differ(pro, cur)
    moved_none = differ(pro, nob)
    proto_rows = sum(1 for x in set(pro) if not str(x).startswith("single"))
    mz = sorted(float(o.split("|")[2]) for o in ids)[len(ids) // 2]
    results.append(
        dict(key=key, kind=kind, mz=mz, ids=ids, samples=[owners[key][o] for o in ids],
             peaks=peaks, raw_rt=raw_rt, proto=proto, current=[labels["21"][key][o] for o in ids],
             nobands=[labels["none"][key][o] for o in ids],
             n_direct=len(d), n_ms1=len(ids) - len(d),
             rows_proto=proto_rows, rows_current=established["21"][key],
             ari_current=adjusted_rand(pro, cur), ari_none=adjusted_rand(pro, nob),
             moved=moved, moved_none=moved_none, rows_none=established["none"][key], anchored=anchor is not None)
    )

with open(HERE / "results.pkl", "wb") as handle:
    pickle.dump(results, handle)
print("peaks without a calibrated RT (raw used):", uncalibrated)
print(f"{'kind':7s} {'m/z':>9s} {'direct':>6s} {'ms1':>5s} {'rows P/C/N':>11s} {'diff cur':>8s} {'diff none':>9s}")
for r in sorted(results, key=lambda r: -r["moved"] / max(1, r["n_direct"])):
    print(f"{r['kind']:7s} {r['mz']:9.4f} {r['n_direct']:6d} {r['n_ms1']:5d} "
          f"{r['rows_proto']:3d}/{r['rows_current']:<3d}/{r['rows_none']:<3d} {r['moved']:8d} {r['moved_none']:9d}")
total = sum(r["n_direct"] for r in results)
for kind in ("random", "join", "slow"):
    sel = [r for r in results if r["kind"] == kind]
    n = sum(r["n_direct"] for r in sel)
    print(kind, "windows", len(sel), "direct", n,
          "placed differently vs current", sum(r["moved"] for r in sel),
          "vs no band", sum(r["moved_none"] for r in sel))
