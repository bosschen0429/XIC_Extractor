"""PROTOTYPE: draw windows where the current rows split a one-peak-per-injection band."""

import io
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from xic_extractor.raw_reader import open_raw

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).parent
REPLAYS = HERE.parent / "band-threshold-85"
order = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
samples = sorted(order, key=order.get)
rank = {s: i for i, s in enumerate(samples)}
TARGETS = ("297.0578", "504.1913", "311.1412")
COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd"]
RAW_DIR = Path("C:/Xcalibur/data/20260106_CSMU_NAA_Tissue_R")
DLL_DIR = Path("C:/Users/user/AppData/Local/XIC_Extractor/RawFileReader-8.0.42")

with open(REPLAYS / "replay-21.pkl", "rb") as handle:
    current = pickle.load(handle)
windows = {}
for t in TARGETS:
    groups = [r for r in current if r["workset"].split("|")[1].startswith(t)]
    rows_ = sorted((r for r in groups if r["disposition"] == "established" and len(r["owners"]) > 1),
                   key=lambda r: -len(r["direct"]))
    marks = [(r["sample"][o], float(o.split("|")[3]), k) for k, r in enumerate(rows_) for o in r["direct"]]
    mz = float(np.median([float(o.split("|")[2]) for r in groups for o in r["owners"]]))
    rts = [m[1] for m in marks]
    windows[t] = dict(marks=marks, mz=mz, lo=min(rts) - 0.5, hi=max(rts) + 0.5, sizes=[len(r["direct"]) for r in rows_])

traces = defaultdict(dict)
for stem in samples:
    with open_raw(RAW_DIR / f"{stem}.raw", DLL_DIR) as raw:
        for t, w in windows.items():
            rt, inten = raw.extract_xic(w["mz"], w["lo"], w["hi"], 10.0)
            traces[t][stem] = (np.asarray(rt, float), np.asarray(inten, float))

out.mkdir(parents=True, exist_ok=True)
fig, axes = plt.subplots(1, len(TARGETS), figsize=(16, 6), sharey=True)
for ax, t in zip(axes, TARGETS):
    w = windows[t]
    grid = np.linspace(w["lo"], w["hi"], 400)
    image = np.zeros((len(samples), grid.size))
    for i, stem in enumerate(samples):
        x, y = traces[t][stem]
        if x.size and y.max() > 0:
            image[i] = np.sqrt(np.interp(grid, x, y / y.max(), left=0, right=0))
    ax.imshow(image, aspect="auto", origin="lower", extent=(w["lo"], w["hi"], 0, len(samples)),
              cmap="Greys", vmin=0, vmax=1)
    for k in sorted({m[2] for m in w["marks"]}):
        pts = [(rt, rank[s] + 0.5) for s, rt, kk in w["marks"] if kk == k]
        xs, ys = zip(*pts)
        ax.scatter(xs, ys, s=16, color=COLORS[k % len(COLORS)], label=f"現在的第 {k + 1} 列（{w['sizes'][k]} 支）")
    ax.set_title(f"m/z {w['mz']:.4f}：每針一支直接峰", fontsize=11, loc="left")
    ax.set_xlabel("RT（分鐘，原始）")
    ax.legend(loc="upper right", fontsize=8, framealpha=0.85)
axes[0].set_ylabel("針序（下早上晚）")
fig.suptitle("PR 現在（漂移帶 21）的分組：同一條帶被切成幾列（原型在這三個窗口都排成 1 列）", fontsize=11, x=0.01, ha="left")
fig.tight_layout()
path = out / "current_splits_one_band.png"
fig.savefig(path, dpi=100)
print(path)
