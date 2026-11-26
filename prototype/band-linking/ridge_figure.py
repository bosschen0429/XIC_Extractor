"""PROTOTYPE: draw traced ridges next to per-band rows on the calibrated signal plane."""

import pickle
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

HERE = Path(__file__).parent
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
PALETTE = ["#2f5bea", "#e0464e", "#1f9d55", "#8b5cf6", "#f08c00", "#0ea5b7", "#c2410c", "#db2777"]
PRESENT = 0.25

data = pickle.loads((HERE / sys.argv[3]).read_bytes())
picks = [float(x) for x in sys.argv[1].split(",")]
rows = [next(v for k, v in data.items() if abs(v[0] - t) < 0.005) for t in picks]
fig, axes = plt.subplots(len(rows), 2, figsize=(13, 4.2 * len(rows)), squeeze=False)
for (ax_r, ax_p), (mz, grid, image, ridges, assigned, rl, pb, peaks) in zip(axes, rows):
    scale = image.max(axis=1, keepdims=True)
    scale[scale <= 0] = 1.0
    shown = np.sqrt(image / scale)
    for ax in (ax_r, ax_p):
        ax.imshow(shown, aspect="auto", cmap="Greys", origin="lower",
                  extent=[grid[0], grid[-1], 0.5, shown.shape[0] + 0.5], vmin=0, vmax=1)
    for k, env in enumerate(ridges):
        xs = [grid[e[2]] if e[3] >= PRESENT else np.nan for e in env]
        ax_r.plot(xs, np.arange(1, len(env) + 1), color=PALETTE[k % len(PALETTE)], lw=1.2)
    for i, p in enumerate(peaks):
        if not p[2]:
            continue
        a = rl[i]
        ax_r.scatter(p[1], p[0] + 1, s=12, c=PALETTE[a % len(PALETTE)] if not str(a).startswith("single") else "#9ca3af",
                     edgecolors="black", linewidths=0.3, zorder=3)
        b = pb[i]
        ax_p.scatter(p[1], p[0] + 1, s=12, c=PALETTE[b % len(PALETTE)] if not str(b).startswith("single") else "#9ca3af",
                     edgecolors="black", linewidths=0.3, zorder=3)
    ax_r.set_title(f"m/z {mz:.2f} 脈線（線＝脈線路徑，點＝直接峰的歸屬）", fontsize=10)
    ax_p.set_title(f"m/z {mz:.2f} 逐帶漂移連帶（點）", fontsize=10)
    for ax in (ax_r, ax_p):
        ax.set_xlabel("校正後 RT（分）")
        ax.set_ylabel("針序")
fig.suptitle("同一張校正後的訊號圖：左＝脈線追蹤，右＝逐帶漂移連帶；灰點＝沒成列", fontsize=12)
fig.tight_layout()
fig.savefig(sys.argv[2], dpi=115)
print(sys.argv[2])
