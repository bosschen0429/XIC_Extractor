"""PROTOTYPE: who carries the large whole-injection shifts, and one window drawn raw vs calibrated."""

import sys
from collections import Counter
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(sys.argv.pop(2))  # flatten_explore reads argv[2:] as targets
sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# Issue #786: the injection right after a short Blank or SDOLEK run is under-equilibrated.
AFTER_SHORT_METHOD = {
    "Breast_Cancer_Tissue_pooled_QC2", "Breast_Cancer_Tissue_pooled_QC3", "Breast_Cancer_Tissue_pooled_QC_4",
    "Breast_Cancer_Tissue_pooled_QC5", "Breast_Cancer_Tissue_pooled_QC6", "Breast_Cancer_Tissue_pooled_QC7",
    "TumorBC2312_DNA", "BenignfatBC1116_DNA",
}
assert AFTER_SHORT_METHOD <= set(rank), AFTER_SHORT_METHOD - set(rank)
marked = sorted(rank[s] for s in AFTER_SHORT_METHOD)
print("injections after a short method (0-based):", marked)

large = Counter()
target = None
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    ids, cal_peaks, _ = coordinates(ws, flatten=False)
    d = [i for i, p in enumerate(cal_peaks) if p[2]]
    if len(d) < 2:
        continue
    raw_rt = [float(o.split("|")[3]) for o in ids]
    offset = [raw_rt[i] - cal_peaks[i][1] for i in range(len(ids))]
    for i in d:
        near = [offset[j] for j in range(len(ids))
                if 0 < abs(cal_peaks[j][0] - cal_peaks[i][0]) <= 8 and abs(cal_peaks[j][1] - cal_peaks[i][1]) <= 0.5]
        if near and abs(offset[i] - median(near)) >= 0.4:
            large["after short method" if owners[ws.key][ids[i]] in AFTER_SHORT_METHOD else "other"] += 1
    if ws.key.split("|")[1].startswith("298.07"):
        target = (ws.key, ids, cal_peaks, raw_rt)
print("direct peaks with excess shift >= 0.4 min:", dict(large))

key, ids, cal_peaks, raw_rt = target
raw_peaks = [(p[0], raw_rt[i], p[2], p[3]) for i, p in enumerate(cal_peaks)]
fig, axes = plt.subplots(1, 3, figsize=(15, 4.3), sharey=True)
palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]
panels = (
    (axes[0], raw_peaks, 0.4, "原始 RT，相鄰步距上限 0.4 分"),
    (axes[1], raw_peaks, 0.6, "原始 RT，相鄰步距上限 0.6 分"),
    (axes[2], cal_peaks, 0.4, "批次校正後的 RT，上限 0.4 分"),
)
for ax, peaks, step, title in panels:
    bands = sorted(
        (b for b in link_bands(peaks, max_rt_step=step) if len(b) > 1 and sum(peaks[i][2] for i in b) >= 2),
        key=len, reverse=True,
    )
    colour = {}
    for k, band in enumerate(bands):
        for i in band:
            colour[i] = palette[k % len(palette)]
    for x in marked:
        ax.axvline(x + 1, color="#cccccc", lw=0.8, zorder=0)
    for i, p in enumerate(peaks):
        if not p[2]:
            continue
        hit = owners[key][ids[i]] in AFTER_SHORT_METHOD
        ax.scatter(p[0] + 1, p[1], s=34 if hit else 18, c=colour.get(i, "#bbbbbb"),
                   marker="D" if hit else "o", edgecolors="black" if hit else "none", linewidths=0.6, zorder=2)
    ax.set_title(f"{title}：{len(bands)} 列", fontsize=11)
    ax.set_xlabel("針序（1 起算；灰線＝短方法後的那一針，#786）")
axes[0].set_ylabel("RT（分）")
axes[0].set_ylim(7.8, 9.1)
fig.suptitle("m/z 298.07 的直接峰：菱形是短方法後的那一針，同色是同一列，灰點沒成列", fontsize=12)
fig.tight_layout()
fig.savefig(OUT, dpi=130)
print(OUT)
