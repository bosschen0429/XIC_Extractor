"""Are the early peaks of injections 3 and 67 the same compound as the main band? Compare MS2.

For 302.18 and 348.21: the early direct peak in injections 3 and 67, the main-band peak of
injections 2, 4, 66 and 68, and one other compound in the same channel as a control.
Each spectrum sums the MS2 scans whose precursor is within 0.02 Da of the channel, within
+-0.3 min of the peak apex. Similarity is cosine on square-root intensities after matching
fragments within 0.01 Da, ignoring fragments within 1.5 Da of the precursor.
Read before running: early-vs-main similarity near the main-vs-main similarities and clearly
above the control means the same compound (or an isomer with the same spectrum); near the
control means a different compound.
"""

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

from xic_extractor.raw_reader import open_raw  # noqa: E402

RAW_DIR = Path(r"C:\Xcalibur\data\20260106_CSMU_NAA_Tissue_R")
DLL = Path(r"C:\Users\user\AppData\Local\XIC_Extractor\RawFileReader-8.0.42")
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
stems = sorted(rank, key=rank.get)
CASES = ((302.1766, 8.0, (16.0, 30.0)), (348.2114, 9.0, (14.3, 15.6)))


def picks(target, early_below, control_rt):
    ws = next(w for w in inputs.worksets if abs(float(w.key.split("|")[1]) - target) < 0.001)
    ids, peaks, _ = coordinates(ws, flatten=False)
    raw = [float(o.split("|")[3]) for o in ids]
    bands, _ = perband(ws, ids, peaks)
    main = max(bands, key=lambda b: sum(peaks[i][2] for i in b))
    out = []
    for x in (3, 67):
        s = stems[x - 1]
        early = [i for i, o in enumerate(ids) if owners[ws.key][o] == s and peaks[i][2] and peaks[i][1] < early_below]
        for i in early:
            out.append((f"第 {x} 針 提早峰", s, raw[i]))
    for x in (2, 4, 66, 68):
        s = stems[x - 1]
        member = [i for i in main if owners[ws.key][ids[i]] == s and peaks[i][2]]
        if member:
            out.append((f"第 {x} 針 主帶", s, raw[member[0]]))
    control = [i for i, o in enumerate(ids) if peaks[i][2] and control_rt[0] <= peaks[i][1] <= control_rt[1]]
    if control:
        i = control[len(control) // 2]
        out.append((f"第 {peaks[i][0] + 1} 針 對照（{peaks[i][1]:.1f} 分，另一化合物）", owners[ws.key][ids[i]], raw[i]))
    return out


def spectrum(stem, target, apex):
    with open_raw(RAW_DIR / f"{stem}.raw", DLL) as raw:
        events = raw.read_ms2_window(max(0.0, apex - 0.3), apex + 0.3,
                                     precursor_scope=lambda mz, meta: abs(mz - target) <= 0.02)
    masses, intens, n = [], [], 0
    for e in events:
        if e.scan is None:
            continue
        n += 1
        masses.extend(e.scan.masses.tolist())
        intens.extend((e.scan.intensities / max(e.scan.intensities.max(), 1e-9)).tolist())
    if not masses:
        return np.array([]), np.array([]), 0
    order = np.argsort(masses)
    m, y = np.asarray(masses)[order], np.asarray(intens)[order]
    merged_m, merged_y = [m[0]], [y[0]]
    for a, b in zip(m[1:], y[1:]):
        if a - merged_m[-1] <= 0.01:
            merged_y[-1] += b
        else:
            merged_m.append(a)
            merged_y.append(b)
    m, y = np.asarray(merged_m), np.asarray(merged_y)
    keep = np.abs(m - target) > 1.5
    m, y = m[keep], y[keep]
    return m, (y / y.max() if y.size else y), n


def cosine(a, b):
    (ma, ya), (mb, yb) = a, b
    if not ma.size or not mb.size:
        return float("nan")
    used, dot = set(), 0.0
    for i, m in enumerate(ma):
        j = int(np.argmin(np.abs(mb - m)))
        if abs(mb[j] - m) <= 0.01 and j not in used:
            used.add(j)
            dot += np.sqrt(ya[i]) * np.sqrt(yb[j])
    return float(dot / (np.sqrt(ya.sum()) * np.sqrt(yb.sum())))


if __name__ == "__main__":
    fig, axes = plt.subplots(len(CASES), 2, figsize=(13, 4.2 * len(CASES)), squeeze=False)
    for row_axes, (target, below, control_rt) in zip(axes, CASES):
        chosen = picks(target, below, control_rt)
        spectra = []
        for label, stem, apex in chosen:
            m, y, n = spectrum(stem, target, apex)
            spectra.append((label, apex, m, y, n))
            top = sorted(zip(y, m), reverse=True)[:6]
            print(f"{target:.2f} {label:28s} apex {apex:5.2f} min, {n} MS2 scans; top fragments "
                  + ", ".join(f"{mz:.3f}({v:.2f})" for v, mz in top))
        print(f"{target:.2f} cosine similarity:")
        for i, (li, _, mi, yi, _) in enumerate(spectra):
            print("   " + f"{li:28s} " + " ".join(f"{cosine((mi, yi), (mj, yj)):5.2f}" for _, _, mj, yj, _ in spectra))
        early = [s for s in spectra if "提早" in s[0] and s[2].size]
        main = [s for s in spectra if "主帶" in s[0] and s[2].size]
        for ax, e in zip(row_axes, early[:2]):
            ref = min(main, key=lambda s: abs(int(s[0].split()[1]) - int(e[0].split()[1]))) if main else None
            ax.vlines(e[2], 0, e[3], color="#d62728", lw=1.2, label=f"{e[0]}（{e[1]:.2f} 分）")
            if ref is not None:
                ax.vlines(ref[2], 0, -ref[3], color="#1f77b4", lw=1.2, label=f"{ref[0]}（{ref[1]:.2f} 分）")
                ax.set_title(f"m/z {target:.2f}：cosine {cosine((e[2], e[3]), (ref[2], ref[3])):.2f}", fontsize=10)
            ax.axhline(0, color="black", lw=0.6)
            ax.set_xlabel("碎片 m/z")
            ax.legend(fontsize=8, loc="upper right")
        print()
    fig.suptitle("MS2 鏡像圖：上＝提早出現的峰，下＝鄰針主帶的峰（已去掉前驅物 ±1.5 Da）", fontsize=12)
    fig.tight_layout()
    fig.savefig(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\14_ms2_mirror.png", dpi=120)
    print("figure written")
