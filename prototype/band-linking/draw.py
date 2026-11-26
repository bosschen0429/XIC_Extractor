"""PROTOTYPE driver: draw each window three ways over the RAW picture and build one HTML file."""

import base64
import html
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
from scipy.optimize import linear_sum_assignment

from xic_extractor.raw_reader import open_raw

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

HERE = Path(__file__).parent
ORDER = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
RAW_DIR = Path("C:/Xcalibur/data/20260106_CSMU_NAA_Tissue_R")
DLL_DIR = Path("C:/Users/user/AppData/Local/XIC_Extractor/RawFileReader-8.0.42")
COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#bcbd22",
          "#e377c2", "#8c564b", "#7f7f7f"]
samples = sorted(ORDER, key=ORDER.get)

with open(HERE / "results.pkl", "rb") as handle:
    results = pickle.load(handle)
for r in results:
    lo = min(r["raw_rt"]) - 0.4
    hi = max(r["raw_rt"]) + 0.4
    r["window"] = (max(0.0, lo), hi)

cache = HERE / "traces.pkl"
if cache.exists():
    traces = pickle.loads(cache.read_bytes())
else:
    traces = defaultdict(dict)
    for stem in samples:
        with open_raw(RAW_DIR / f"{stem}.raw", DLL_DIR) as raw:
            for k, r in enumerate(results):
                rt, inten = raw.extract_xic(r["mz"], r["window"][0], r["window"][1], 10.0)
                traces[k][stem] = (np.asarray(rt, float), np.asarray(inten, float))
    traces = dict(traces)
    cache.write_bytes(pickle.dumps(traces))
print("traces ready")


def partition(labels, direct):
    rows = defaultdict(list)
    for i, x in enumerate(labels):
        rows[x].append(i)
    return [m for m in rows.values() if len(m) > 1 and sum(direct[i] for i in m) >= 2]


def colour_rows(reference, other):
    """Give `other` rows the colour of the reference row they overlap most."""
    colours = {}
    if reference and other:
        overlap = [[len(set(a) & set(b)) for b in other] for a in reference]
        rows_, cols_ = linear_sum_assignment([[-v for v in row] for row in overlap])
        for a, b in zip(rows_, cols_):
            if overlap[a][b] > 0:
                colours[b] = a
    spare = iter(range(len(reference), 10_000))
    return [colours.get(b, next(spare)) for b in range(len(other))]


def figure(k, r):
    direct = [p[2] for p in r["peaks"]]
    ranks = [samples.index(s) for s in r["samples"]]
    rt = r["raw_rt"]
    lo, hi = r["window"]
    grid = np.linspace(lo, hi, 500)
    image = np.zeros((len(samples), grid.size))
    for i, stem in enumerate(samples):
        x, y = traces[k][stem]
        if x.size and y.max() > 0:
            image[i] = np.sqrt(np.interp(grid, x, y / y.max(), left=0, right=0))
    proto = sorted(partition(r["proto"], direct), key=len, reverse=True)
    panels = [
        ("原型：照帶連起來", proto, list(range(len(proto)))),
    ]
    for title, labels in (("PR 現在（漂移帶 21）", r["current"]), ("不啟用漂移帶", r["nobands"])):
        rows = partition(labels, direct)
        panels.append((title, rows, colour_rows(proto, rows)))
    fig, axes = plt.subplots(1, 3, figsize=(16, 6.2), sharey=True)
    for ax, (title, rows, colours) in zip(axes, panels):
        ax.imshow(image, aspect="auto", origin="lower", extent=(lo, hi, 0, len(samples)),
                  cmap="Greys", vmin=0, vmax=1)
        placed = set()
        for row, colour in zip(rows, colours):
            c = COLORS[colour % len(COLORS)]
            xs = [rt[i] for i in row]
            ys = [ranks[i] + 0.5 for i in row]
            sizes = [18 if direct[i] else 6 for i in row]
            ax.scatter(xs, ys, s=sizes, color=c, linewidths=0)
            placed.update(row)
        loose = [i for i in range(len(rt)) if direct[i] and i not in placed]
        if loose:
            ax.scatter([rt[i] for i in loose], [ranks[i] + 0.5 for i in loose], s=20,
                       marker="x", color="#555555", linewidths=1)
        ax.set_xlim(lo, hi)
        n_direct = sum(1 for row in rows for i in row if direct[i])
        ax.set_title(f"{title}：{len(rows)} 列，{n_direct} 支直接峰成列", fontsize=10, loc="left")
        ax.set_xlabel("RT（分鐘，原始）")
    axes[0].set_ylabel("針序（下早上晚）")
    fig.suptitle(
        f"m/z {r['mz']:.4f}　直接峰 {r['n_direct']}、MS1-only {r['n_ms1']}　"
        f"（大點＝直接峰，小點＝補進列的 MS1-only，×＝沒成列的直接峰；同色＝重疊最多的列）",
        fontsize=10.5, x=0.01, ha="left")
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=80)
    plt.close(fig)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


KIND = {"random": "隨機抽的一般窗口", "join": "漂移帶有合併的難窗口", "slow": "最慢的窗口"}
WATCHED = ("302.1669", "302.1774", "334.0511", "348.2118", "288.0660")
items = []
for k, r in enumerate(results):
    items.append(dict(
        id=k, mz=f"{r['mz']:.4f}", kind=KIND[r["kind"]], n_direct=r["n_direct"], n_ms1=r["n_ms1"],
        rows=f"{r['rows_proto']} / {r['rows_current']} / {r['rows_none']}",
        moved=r["moved"], moved_none=r["moved_none"],
        share=round(100 * r["moved"] / max(1, r["n_direct"])),
        watched=any(f"{r['mz']:.4f}".startswith(w[:7]) for w in WATCHED),
        img=figure(k, r),
    ))
    print("drawn", k + 1, "/", len(results))

summary = []
for kind in ("random", "join", "slow"):
    sel = [r for r in results if r["kind"] == kind]
    n = sum(r["n_direct"] for r in sel)
    m = sum(r["moved"] for r in sel)
    summary.append(f"<tr><td>{KIND[kind]}</td><td>{len(sel)}</td><td>{n}</td>"
                   f"<td>{m}（{100 * m / max(1, n):.1f}%）</td></tr>")

page = f"""<!doctype html><html lang="zh-Hant"><meta charset="utf-8">
<title>原型：照疊圖找帶的分組，互補版（85 針）</title>
<style>
body{{font-family:"Microsoft JhengHei",system-ui,sans-serif;margin:24px auto;max-width:1240px;color:#222;line-height:1.55}}
h1{{font-size:22px;margin:0 0 6px}} .note{{color:#555;font-size:14px}}
table{{border-collapse:collapse;font-size:13px}} td,th{{border-bottom:1px solid #e3e3e3;padding:4px 10px;text-align:right}}
th{{background:#f6f6f6;position:sticky;top:0}} td:first-child,th:first-child{{text-align:left}}
#list tr{{cursor:pointer}} #list tr:hover{{background:#eef4ff}} #list tr.on{{background:#dbe8ff}}
.wrap{{display:grid;grid-template-columns:420px 1fr;gap:16px;align-items:start}}
.scroll{{max-height:640px;overflow:auto;border:1px solid #e3e3e3}} img{{width:100%;border:1px solid #ddd}}
.tabs button{{border:1px solid #bbb;background:#fff;padding:5px 12px;margin:0 6px 8px 0;border-radius:4px;cursor:pointer}}
.tabs button.on{{background:#1f5fbf;color:#fff;border-color:#1f5fbf}}
</style>
<h1>原型：照疊圖找帶的分組（互補版：漂移模型拉平座標＋連帶）</h1>
<p class="note">要回答的問題：把同一個 m/z 在 85 針的峰攤在「RT × 針序」上，照眼睛的方式找連續的帶，排出來的列跟現在的分組差在哪？
原型規則只有兩條：<b>靠得近的峰先連</b>（校正後 RT 相差 ≤ 0.4 分、針序相差 ≤ 8 針，近的先連）；<b>同一針的兩支峰不能在同一條帶</b>。
帶上有 ≥2 支直接峰才算一列。這是用完即丟的原型，不是正式程式；峰用的是跟現在程式同一批（直接峰＋MS1-only）。</p>
<p class="note"><b>這是互補版</b>：有自己漂移錨點的通道，先用漂移模型（B、M）把座標拉平再連帶；其他通道用批次校正 RT。連帶時守整條帶前驅質量 10 ppm。<b>整批（910 個至少有 2 支直接峰的窗口，23,503 支直接峰）</b>：跟「不啟用漂移帶」的分組有 51% 窗口完全相同、9.0% 的直接峰放法不同；47 個有錨點的窗口差異最大（16.5%），863 個沒有錨點的是 8.4%。差異集中在同一針有多支峰的窗口。連帶本身整批 0.25 秒（單一程序）；現在的分組重播約 500 秒，但它另外做了補位、容差與漂移擬合，兩者不完全對等。<b>仍待確認</b>：差異之處誰對，沒有標準答案，要看圖；原型還沒用到曲線形狀。</p>
<p class="note">下表是抽樣的 47 個窗口（25 個漂移帶有合併的難窗口、2 個最慢的、20 個隨機），點左邊的列看右邊的圖。</p>
<table style="margin:10px 0 18px"><tr><th>窗口類別</th><th>窗口數</th><th>直接峰</th><th>原型跟 PR 現在放法不同</th></tr>{''.join(summary)}</table>
<div class="tabs"><button data-f="all" class="on">全部</button><button data-f="watched">先前看過的 4 個</button>
<button data-f="隨機抽的一般窗口">隨機的一般窗口</button><button data-f="漂移帶有合併的難窗口">難窗口</button><button data-f="最慢的窗口">最慢的窗口</button></div>
<div class="wrap"><div class="scroll"><table id="list"><thead><tr><th>m/z</th><th>直接峰</th><th>列數 原型/現在/無帶</th><th>放法不同</th></tr></thead><tbody></tbody></table></div>
<div><img id="view" alt=""><p class="note" id="caption"></p></div></div>
<script>
const items = {json.dumps([{k: v for k, v in i.items() if k != 'img'} for i in items], ensure_ascii=False)};
const images = {json.dumps({str(i['id']): i['img'] for i in items})};
const body = document.querySelector('#list tbody');
function show(id){{
  document.querySelectorAll('#list tr').forEach(tr => tr.classList.toggle('on', tr.dataset.id == id));
  const it = items.find(x => x.id == id);
  document.getElementById('view').src = 'data:image/png;base64,' + images[id];
  document.getElementById('caption').textContent = `m/z ${{it.mz}}｜${{it.kind}}｜直接峰 ${{it.n_direct}}、MS1-only ${{it.n_ms1}}｜列數 原型/現在/無帶 = ${{it.rows}}｜跟 PR 現在放法不同 ${{it.moved}} 支、跟無帶不同 ${{it.moved_none}} 支`;
}}
function render(filter){{
  body.innerHTML = '';
  items.filter(it => filter === 'all' || (filter === 'watched' ? it.watched : it.kind === filter))
       .sort((a, b) => b.share - a.share)
       .forEach(it => {{
         const tr = document.createElement('tr'); tr.dataset.id = it.id;
         tr.innerHTML = `<td>${{it.mz}}</td><td>${{it.n_direct}}</td><td>${{it.rows}}</td><td>${{it.moved}}（${{it.share}}%）</td>`;
         tr.onclick = () => show(it.id); body.appendChild(tr);
       }});
  const first = body.querySelector('tr'); if (first) show(first.dataset.id);
}}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {{
  document.querySelectorAll('.tabs button').forEach(x => x.classList.toggle('on', x === b)); render(b.dataset.f);
}});
render('all');
</script></html>"""
(HERE / "band-tracking-prototype.html").write_text(page, encoding="utf-8")
print(HERE / "band-tracking-prototype.html")
