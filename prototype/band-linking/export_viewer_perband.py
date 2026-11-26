"""PROTOTYPE: viewer and figures for per-band drift.

Groupings: 0 per-band drift, 1 no-band (cost search; the diff reference), 2 calibrated-RT linking.
Windows: the 47 sampled windows plus every window whose per-band joins disagree with the
cost search. Traces for windows outside results.pkl are read from RAW once and cached.
"""

import base64
import json
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.optimize import linear_sum_assignment  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, labels, owners, rank  # noqa: E402

HERE = Path(__file__).parent
OUT = Path(r"C:\Users\user\Desktop\XIC_Extractor\output")
RAW_DIR = Path("C:/Xcalibur/data/20260106_CSMU_NAA_Tissue_R")
DLL_DIR = Path("C:/Users/user/AppData/Local/XIC_Extractor/RawFileReader-8.0.42")
GRID = 320
KIND = {"random": "一般窗口", "join": "難窗口", "slow": "最慢窗口"}
WATCHED = ("348.2114", "302.1766", "334.0504", "277.1251")
samples = sorted(rank, key=rank.get)
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def band_labels(bands, peaks):
    out = [f"single:{i}" for i in range(len(peaks))]
    for r, band in enumerate(bands):
        if len(band) > 1 and sum(peaks[i][2] for i in band) >= 2:
            for i in band:
                out[i] = r
    return out


def rows_of(lab, direct):
    groups = defaultdict(list)
    for i, x in enumerate(lab):
        groups[x].append(i)
    rows = [m for m in groups.values() if len(m) > 1 and sum(direct[i] for i in m) >= 2]
    return sorted(rows, key=lambda m: (-sum(direct[i] for i in m), -len(m)))


def pair(reference, other, direct):
    mapping = {}
    if reference and other:
        overlap = [[sum(1 for i in set(a) & set(b) if direct[i]) for b in other] for a in reference]
        r_, c_ = linear_sum_assignment([[-v for v in row] for row in overlap])
        for a, b in zip(r_, c_):
            if overlap[a][b] > 0:
                mapping[int(b)] = int(a)
    return mapping


def read_traces(requests):
    """requests: key -> (mz, lo, hi); returns key -> stem -> (rt, intensity)."""
    from xic_extractor.raw_reader import open_raw

    traces = defaultdict(dict)
    for stem in samples:
        with open_raw(RAW_DIR / f"{stem}.raw", DLL_DIR) as raw:
            for key, (mz, lo, hi) in requests.items():
                rt, inten = raw.extract_xic(mz, lo, hi, 10.0)
                traces[key][stem] = (np.asarray(rt, float), np.asarray(inten, float))
    return dict(traces)


def build():
    results = pickle.loads((HERE / "results.pkl").read_bytes())
    old_traces = pickle.loads((HERE / "traces.pkl").read_bytes())
    joins = json.loads((HERE / "perband_joins.json").read_text(encoding="utf-8"))
    joined = {j["key"] for j in joins}
    disagree = {j["key"] for j in joins if j["agree"] < 0.9}
    worksets = {w.key: w for w in inputs.worksets}
    keys = [(r["key"], KIND[r["kind"]], k) for k, r in enumerate(results)]
    known = {r["key"] for r in results}
    keys += [(key, "每帶新接", None) for key in sorted(disagree - known)]

    prepared = []
    for key, kind, k in keys:
        ws = worksets[key]
        ids, peaks, _ = coordinates(ws, flatten=False)
        _, _, anchor = coordinates(ws, flatten=True)
        raw_rt = [float(o.split("|")[3]) for o in ids]
        bands, _ = perband(ws, ids, peaks)
        lab = [band_labels(bands, peaks), [labels["none"][key][o] for o in ids], band_labels(link_bands(peaks), peaks)]
        mz = float(np.median([p[3] for p in peaks]))
        lo, hi = max(0.0, min(raw_rt) - 0.4), max(raw_rt) + 0.4
        prepared.append(dict(key=key, kind=kind, k=k, ids=ids, peaks=peaks, raw_rt=raw_rt, lab=lab, mz=mz, lo=lo, hi=hi,
                             anchored=anchor is not None, joined=key in joined, disagree=key in disagree))

    cache = HERE / "traces_perband_extra.pkl"
    extra = pickle.loads(cache.read_bytes()) if cache.exists() else {}
    missing = {p["key"]: (p["mz"], p["lo"], p["hi"]) for p in prepared if p["k"] is None and p["key"] not in extra}
    if missing:
        print(f"reading RAW for {len(missing)} windows x {len(samples)} injections ...")
        extra.update(read_traces(missing))
        cache.write_bytes(pickle.dumps(extra))

    windows = []
    for wid, p in enumerate(prepared):
        trace = old_traces[p["k"]] if p["k"] is not None else extra[p["key"]]
        direct = [q[2] for q in p["peaks"]]
        n = len(direct)
        grid = np.linspace(p["lo"], p["hi"], GRID)
        image = np.zeros((len(samples), GRID), dtype=np.uint8)
        for i, stem in enumerate(samples):
            x, y = trace[stem]
            if x.size and y.max() > 0:
                image[i] = np.round(255 * np.sqrt(np.interp(grid, x, y / y.max(), left=0, right=0)))
        groupings = [rows_of(lab, direct) for lab in p["lab"]]
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
        ref_pair = [pair(groupings[1], groupings[g], direct) for g in range(3)]
        diff = [[0] * n for _ in range(3)]
        for g in (0, 2):
            for i in range(n):
                mine, theirs = member[g][i], member[1][i]
                if (mine < 0) != (theirs < 0) or (mine >= 0 and ref_pair[g].get(mine) != theirs):
                    diff[g][i] = 1
        windows.append(dict(
            id=wid, mz=round(p["mz"], 4), kind=p["kind"], anchored=p["anchored"], joined=p["joined"],
            disagree=p["disagree"], watched=any(abs(float(p["key"].split("|")[1]) - float(w)) < 0.001 for w in WATCHED),
            nd=sum(direct), nm=n - sum(direct), rows=[len(x) for x in groupings],
            moved=[sum(diff[0][i] for i in range(n) if direct[i]), 0, sum(diff[2][i] for i in range(n) if direct[i])],
            lo=round(p["lo"], 4), hi=round(p["hi"], 4), img=base64.b64encode(image.tobytes()).decode("ascii"),
            r=[rank[owners[p["key"]][o]] for o in p["ids"]], t=[round(v, 3) for v in p["raw_rt"]],
            d=[int(v) for v in direct], z=[round(q[3], 4) for q in p["peaks"]],
            row=member, col=[[colour[g][b] if b >= 0 else -1 for b in member[g]] for g in range(3)], diff=diff,
            image=image,
        ))
    return windows


def patch_template():
    t = (HERE / "viewer_template.html").read_text(encoding="utf-8")
    edits = [
        ("<title>分組原型檢視器</title>", "<title>分組原型檢視器 · 每帶漂移</title>"),
        ("<span>· 照疊圖找帶（互補版）</span>", "<span>· 每帶漂移</span>"),
        ('<div class="stat"><b>51%</b><span>與不啟用漂移帶完全相同</span></div>',
         '<div class="stat"><b>51%</b><span>每帶漂移與成本搜尋完全相同（只校正 50%）</span></div>'),
        ('<div class="stat"><b>9.0%</b><span>直接峰放法不同</span></div>',
         '<div class="stat"><b>8.6%</b><span>直接峰放法不同（只校正 10.1%）</span></div>'),
        ('<div class="stat"><b>0.25 秒</b><span>整批連帶（現行重播約 500 秒）</span></div>',
         '<div class="stat"><b>1.2 秒</b><span>整批每帶漂移連帶（只校正 0.25 秒）</span></div>'),
        ("跟「不啟用漂移帶」分法不同", "跟成本搜尋（不啟用漂移帶）分法不同"),
        ('const GROUPS = ["原型（互補版）", "不啟用漂移帶", "PR 現在（漂移帶 21）"];',
         'const GROUPS = ["每帶漂移", "成本搜尋（不啟用漂移帶）", "只用批次校正連帶"];'),
        ('const SHORT = ["原型", "不啟用漂移帶", "PR 現在"];', 'const SHORT = ["每帶漂移", "成本搜尋", "只校正"];'),
        ('const FILTERS = [["all", "全部"], ["watched", "看過的 4 個"], ["一般窗口", "一般"], ["難窗口", "難窗口"], ["最慢窗口", "最慢"], ["anchored", "有錨點"]];',
         'const FILTERS = [["all", "全部"], ["watched", "目標 4 個"], ["disagree", "接法跟成本搜尋不同"], ["joined", "每帶新接"], ["難窗口", "難窗口"], ["anchored", "有錨點"]];'),
        ('state.filter === "anchored" ? w.anchored : w.kind === state.filter);',
         'state.filter === "anchored" ? w.anchored : state.filter === "disagree" ? w.disagree :\n    state.filter === "joined" ? w.joined : w.kind === state.filter);'),
        ("  if (w.anchored) t += '<span class=\"tag anchor\">有錨點</span>';",
         "  if (w.anchored) t += '<span class=\"tag anchor\">有錨點</span>';\n"
         "  if (w.disagree) t += '<span class=\"tag hard\">接法不同</span>';\n"
         "  else if (w.joined) t += '<span class=\"tag slow\">每帶新接</span>';"),
        ('title="原型與不啟用漂移帶分法不同的直接峰比例"', 'title="每帶漂移與成本搜尋分法不同的直接峰比例"'),
        ("列數：原型 ${w.rows[0]} · 無帶 ${w.rows[1]} · PR ${w.rows[2]}",
         "列數：每帶 ${w.rows[0]} · 成本搜尋 ${w.rows[1]} · 只校正 ${w.rows[2]}"),
        ("原型與不啟用漂移帶分法不同 ${w.moved[0]} 支直接峰（${pct}%）· PR 現在與不啟用漂移帶不同 ${w.moved[2]} 支",
         "每帶漂移與成本搜尋分法不同 ${w.moved[0]} 支直接峰（${pct}%）· 只校正連帶與成本搜尋不同 ${w.moved[2]} 支"),
    ]
    for old, new in edits:
        assert t.count(old) == 1, old
        t = t.replace(old, new)
    return t


def figure(windows, picks, names, path, title):
    shown = [w for key in picks for w in windows if f"{w['mz']:.4f}".startswith(key)][: len(picks)]
    fig, axes = plt.subplots(len(shown), len(names), figsize=(4.6 * len(names), 3.9 * len(shown)), squeeze=False)
    palette = ["#2f5bea", "#e0464e", "#1f9d55", "#8b5cf6", "#f08c00", "#0ea5b7", "#c2410c", "#db2777", "#4d7c0f", "#64748b"]
    for row_axes, w in zip(axes, shown):
        for ax, (g, name) in zip(row_axes, names):
            ax.imshow(w["image"], aspect="auto", cmap="Greys", origin="lower",
                      extent=[w["lo"], w["hi"], 0.5, len(samples) + 0.5], vmin=0, vmax=255)
            for i in range(len(w["t"])):
                b = w["row"][g][i]
                if b < 0 and not w["d"][i]:
                    continue
                c = palette[w["col"][g][i] % len(palette)] if b >= 0 else "#9ca3af"
                ax.scatter(w["t"][i], w["r"][i] + 1, s=16 if w["d"][i] else 7, c=c if w["d"][i] else "none",
                           edgecolors=c, linewidths=0.8, zorder=3)
            ax.set_title(f"m/z {w['mz']:.2f}  {name}：{w['rows'][g]} 列", fontsize=10)
            ax.set_xlabel("原始 RT（分）")
            ax.set_ylabel("針序")
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    windows = build()
    names = [(2, "只用批次校正"), (0, "每帶漂移"), (1, "成本搜尋（不啟用漂移帶）")]
    figure(windows, ("348.2", "302.17"), names, OUT / "issue-figures" / "07_perband_348_302.png",
           "實心點＝直接峰（灰＝沒成列），空心＝成列的 MS1-only；同色＝同一列")
    figure(windows, ("300.16", "406.22", "282.15"), names, OUT / "issue-figures" / "08_perband_disagreements.png",
           "每帶漂移接法跟成本搜尋不同的窗口（同色＝同一列）")
    for w in windows:
        del w["image"]
    data = dict(samples=samples, orders=list(range(1, len(samples) + 1)), grid=GRID, windows=windows)
    html = patch_template().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    target = OUT / "band-tracking-perband.html"
    target.write_text(html, encoding="utf-8")
    print(target, round(target.stat().st_size / 1e6, 2), "MB", len(windows), "windows")
