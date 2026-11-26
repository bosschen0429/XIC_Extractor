"""Look at injections 3 (TumorBC2258) and 67 (BenignfatBC1025): sequence context, ISTD RT
offsets, and how their peaks sit against the bands other injections form.

1. Full injection sequence from RAW headers (all subfolders, deduplicated by creation time):
   what ran before each, the gap and the idle time.
2. ISTD raw RT minus the median of the 3 normal injections on each side (#786 injections are
   not used as neighbours), for injections 1-7 and 63-71.
3. Every per-band row (captured models) that spans >= 60 injections: is injection X a member,
   and how far its peak sits from the band's local position (median raw RT of members within
   +-3 injections). When X is missing, the nearest direct peak of X in that channel.
4. Stacked raw XIC of 302.1774 and 348.21 around injections 3 and 67.
"""

import pickle
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

HERE = Path(__file__).parent
RAW_ROOT = Path(r"C:\Xcalibur\data\20260106_CSMU_NAA_Tissue_R")
DLL = Path(r"C:\Users\user\AppData\Local\XIC_Extractor\RawFileReader-8.0.42")
AFTER_SHORT_METHOD = {
    "Breast_Cancer_Tissue_pooled_QC2", "Breast_Cancer_Tissue_pooled_QC3", "Breast_Cancer_Tissue_pooled_QC_4",
    "Breast_Cancer_Tissue_pooled_QC5", "Breast_Cancer_Tissue_pooled_QC6", "Breast_Cancer_Tissue_pooled_QC7",
    "TumorBC2312_DNA", "BenignfatBC1116_DNA",
}
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
stems = sorted(rank, key=rank.get)
FOCUS = (stems[2], stems[66])


def sequence():
    from xic_extractor.raw_reader import read_raw_injection_header

    seen = {}
    for path in sorted(RAW_ROOT.rglob("*.raw")):
        try:
            h = read_raw_injection_header(path, DLL)
        except Exception as exc:  # noqa: BLE001 - a diagnostic listing, report and continue
            print(f"  unreadable {path.name}: {exc}")
            continue
        seen.setdefault(h.creation_date, (path.stem, h))
    ordered = [seen[k] for k in sorted(seen)]
    print(f"1. sequence: {len(ordered)} distinct injections")
    for target in FOCUS:
        k = next(i for i, (stem, _) in enumerate(ordered) if stem == target)
        for j in range(max(0, k - 3), min(len(ordered), k + 2)):
            stem, h = ordered[j]
            gap = (ordered[j + 1][1].creation_date - h.creation_date).total_seconds() / 60 if j + 1 < len(ordered) else float("nan")
            idle = gap - h.end_time_min
            mark = "  <==" if j == k else ""
            print(f"   {h.creation_date:%m-%d %H:%M}  {stem[:34]:34s} run {h.end_time_min:5.1f} min, idle after {idle:5.1f} min,"
                  f" method {Path(h.instrument_method).name}{mark}")
        print()


def istd_offsets():
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    istd = defaultdict(dict)
    for label, sample, rt in model.measured_istds:
        istd[label][sample] = rt
    labels_ = sorted(istd, key=lambda k: median(istd[k].values()))
    normal = [s for s in stems if s not in AFTER_SHORT_METHOD]
    print("2. ISTD raw RT minus the median of 3 normal injections on each side (s):")
    print("   inj  sample                       " + "  ".join(f"{k[:10]:>10s}" for k in labels_))
    for x in list(range(1, 8)) + list(range(63, 72)):
        s = stems[x - 1]
        before = [n for n in normal if rank[n] < rank[s]][-3:]
        after = [n for n in normal if rank[n] > rank[s]][:3]
        cells = []
        for k in labels_:
            ref = [istd[k][n] for n in before + after if n in istd[k]]
            cells.append(f"{(istd[k][s] - median(ref)) * 60:+10.1f}" if s in istd[k] and ref else f"{'--':>10s}")
        print(f"   {x:3d}  {s[:28]:28s} " + "  ".join(cells))
    print()


def band_positions():
    print("3. long per-band rows (>= 60 injections): injection X against the band's local position")
    stats = {x: defaultdict(list) for x in (2, 3, 4, 66, 67, 68)}
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, peaks, _ = coordinates(ws, flatten=False)
        if sum(p[2] for p in peaks) < 2:
            continue
        raw = [float(o.split("|")[3]) for o in ids]
        for band in perband(ws, ids, peaks)[0]:
            injections = {peaks[i][0] + 1: raw[i] for i in band}
            if len(injections) < 60 or sum(peaks[i][2] for i in band) < 2:
                continue
            for x in stats:
                near = [injections[j] for j in range(x - 3, x + 4) if j != x and j in injections]
                if len(near) < 3:
                    continue
                local = median(near)
                if x in injections:
                    stats[x]["present"].append((injections[x] - local) * 60)
                else:
                    others = [raw[i] for i in range(len(peaks)) if peaks[i][0] + 1 == x and peaks[i][2]]
                    stats[x]["missing"].append(min(((y - local) * 60 for y in others), key=abs) if others else None)
    for x, s in stats.items():
        present = sorted(s["present"])
        missing = s["missing"]
        far = [v for v in missing if v is not None]
        print(f"   injection {x:2d}: present in {len(present)} rows, offset median {median(present):+.1f} s,"
              f" 10-90% {present[len(present) // 10]:+.1f} / {present[9 * len(present) // 10]:+.1f} s;"
              f" missing from {len(missing)} rows, of which {len(far)} have a direct peak elsewhere in the channel"
              + (f" (offset median {median(far):+.1f} s)" if far else ""))
    print()


def stacked_traces(path):
    results = pickle.loads((HERE / "results.pkl").read_bytes())
    traces = pickle.loads((HERE / "traces.pkl").read_bytes())
    picks = [(k, r) for k, r in enumerate(results) if abs(r["mz"] - 348.2118) < 0.002 or abs(r["mz"] - 302.1774) < 0.002]
    fig, axes = plt.subplots(len(picks), 2, figsize=(13, 4.6 * len(picks)), squeeze=False)
    for row_axes, (k, r) in zip(axes, picks):
        for ax, center in zip(row_axes, (3, 67)):
            show = [x for x in range(center - 3, center + 4) if 1 <= x <= len(stems)]
            for level, x in enumerate(show):
                s = stems[x - 1]
                t, y = traces[k][s]
                if y.size and y.max() > 0:
                    ax.plot(t, level + 0.9 * y / y.max(), color="#d62728" if x == center else "#4b5563", lw=1.0)
                for (rnk, _, direct, _), rt, sample in zip(r["peaks"], r["raw_rt"], r["samples"]):
                    if sample == s:
                        ax.scatter(rt, level + 0.02, marker="^", s=30 if direct else 18,
                                   c="#111827" if direct else "none", edgecolors="#111827", zorder=3)
                ax.text(t[0] if len(t) else 0, level + 0.45, f"第 {x} 針{'（' + s[:14] + '）' if x == center else ''}", fontsize=8)
            ax.set_yticks([])
            ax.set_title(f"m/z {r['mz']:.2f}，第 {center} 針（紅）與前後各 3 針", fontsize=10)
            ax.set_xlabel("原始 RT（分）；▲＝取到的直接峰，△＝MS1-only")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(path)


if __name__ == "__main__":
    istd_offsets()
    band_positions()
    stacked_traces(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\13_inj_3_67_traces.png")
    sequence()
