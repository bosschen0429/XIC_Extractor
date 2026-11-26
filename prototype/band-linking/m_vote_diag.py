"""PROTOTYPE diagnostics for M voting by per-band rows (uses m_vote's cached cost-search rows).

1. Which samples keep the anchors' M under each row source.
2. Injections 3 and 79: for every cost-search row holding that injection, is it in the
   matching per-band row, and how far is it from its 8 injection-order neighbours?
3. Where the 255 moved direct peaks sit (by injection), 348.21 separately.
4. Which drift anchor's coefficients changed most; 348.21's own anchor under both models.
5. Figure: M by injection order for the three row sources.
"""

import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from scipy.optimize import linear_sum_assignment  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband, rows_labels  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES  # noqa: E402
from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_drift import ROW_MIN_SAMPLES  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

HERE = Path(__file__).parent
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def moved_peaks(a, b, d):
    """Direct peaks whose row in ``a`` does not pair with their row in ``b``."""
    ra, rb = defaultdict(set), defaultdict(set)
    for i in d:
        ra[a[i]].add(i)
        rb[b[i]].add(i)
    ka = [k for k in ra if not str(k).startswith("single")]
    kb = [k for k in rb if not str(k).startswith("single")]
    partner = {}
    if ka and kb:
        overlap = [[len(ra[x] & rb[y]) for y in kb] for x in ka]
        r_, c_ = linear_sum_assignment([[-v for v in row] for row in overlap])
        partner = {ka[r]: kb[c] for r, c in zip(r_, c_) if overlap[r][c] > 0}
    out = []
    for i in d:
        sa, sb = str(a[i]).startswith("single"), str(b[i]).startswith("single")
        if sa != sb or (not sa and partner.get(a[i]) != b[i]):
            out.append(i)
    return out


if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    calibration, preliminary = fit_batch_rt_model(model)
    base_frame = SampleRtFrame(injection_order=inputs.injection_order, sample_universe=frozenset(inputs.sample_stems))

    def frame_of(ws):
        return base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration, preliminary)

    control_rows, _ = pickle.loads((HERE / "m_vote_control_rows.pkl").read_bytes())
    pb_rows = []
    for ws in worksets:
        frame = frame_of(ws)
        placed = sorted(((o, e.rt_min) for o in ws.owners if not CID_NL_GROUPING_RULES.method_unobserved(o)
                         and (e := frame.membership_coordinate(o)) is not None), key=lambda item: item[0].owner_id)
        if len(placed) < ROW_MIN_SAMPLES:
            continue
        ids = [o.owner_id for o, _ in placed]
        peaks = [(rank[o.sample_stem], y, True, float(o.owner_id.split("|")[2])) for o, y in placed]
        for band in perband(ws, ids, peaks, drift=preliminary)[0]:
            row = {placed[i][0].sample_stem: placed[i][1] for i in band}
            if len(row) >= ROW_MIN_SAMPLES:
                pb_rows.append(row)
    _, control = fit_batch_rt_model(model, shift_rows=control_rows)
    _, pb_model = fit_batch_rt_model(model, shift_rows=pb_rows)
    base_m = preliminary.sample_shift_min

    print("1. samples that keep the anchors' M:")
    for name, m in (("cost-search rows", control), ("per-band rows", pb_model)):
        kept = sorted((s for s, v in m.sample_shift_min.items() if abs(v - base_m.get(s, v + 1)) <= 1e-12), key=rank.get)
        print(f"   {name}: " + ", ".join(f"[{rank[s] + 1}] {s}" for s in kept))

    print("\n2. injections 3 and 79 in the rows:")
    by_order = sorted(rank, key=rank.get)
    for stem in (by_order[2], by_order[78]):
        holding = [r for r in control_rows if stem in r]
        pb_holding = sum(1 for r in pb_rows if stem in r)
        missing, distances = 0, []
        for row in holding:
            pairs = {(s, v) for s, v in row.items() if s != stem}
            best = max(pb_rows, key=lambda p: len(pairs & set(p.items())), default=None)
            if best is None or len(pairs & set(best.items())) < ROW_MIN_SAMPLES // 2:
                continue
            if stem not in best:
                missing += 1
                members = sorted((s for s in row if s != stem), key=lambda s: abs(rank[s] - rank[stem]))[:8]
                distances.append(abs(row[stem] - median(row[s] for s in members)))
        far = sum(d > 0.4 for d in distances)
        print(f"   [{rank[stem] + 1}] {stem}: in {len(holding)} cost-search rows and {pb_holding} per-band rows;"
              f" cost-search rows whose matching per-band row lacks it: {missing},"
              f" of which more than 0.4 min from its 8 neighbours: {far}"
              + (f" (median distance {median(distances):.2f} min)" if distances else ""))

    print("\n3. direct peaks placed differently by the per-band-rows model vs the captured model:")
    where = Counter()
    target = Counter()
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, peaks, _ = coordinates(ws, flatten=False)
        d = [i for i, p in enumerate(peaks) if p[2]]
        if len(d) < 2:
            continue
        a = rows_labels(perband(ws, ids, peaks, drift=pb_model)[0], peaks)
        b = rows_labels(perband(ws, ids, peaks)[0], peaks)
        for i in moved_peaks(a, b, d):
            where[peaks[i][0] + 1] += 1
            if abs(float(ws.key.split("|")[1]) - 348.2114) < 0.001:
                target[peaks[i][0] + 1] += 1
    total = sum(where.values())
    top = where.most_common(8)
    print(f"   total {total}; injections with the most: {top}; share of injections 3 and 79: {where[3] + where[79]}/{total}")
    print(f"   348.21: {sum(target.values())} moved, by injection {sorted(target.items())}")

    print("\n4. drift anchor coefficients, per-band-rows model vs cost-search-rows model:")
    ca = {x.channel: x for x in pb_model.anchors}
    cb = {x.channel: x for x in control.anchors}
    change = sorted(((abs(ca[c].coefficients.time - cb[c].coefficients.time)
                      + abs(ca[c].coefficients.sample_shift - cb[c].coefficients.sample_shift), c) for c in ca if c in cb),
                    reverse=True)
    for value, c in change[:3]:
        print(f"   {c.split('|')[1][:9]}: (time {cb[c].coefficients.time:+.2f}, shift {cb[c].coefficients.sample_shift:+.2f})"
              f" -> ({ca[c].coefficients.time:+.2f}, {ca[c].coefficients.sample_shift:+.2f}), change {value:.2f}")
    own = next(c for c in ca if abs(float(c.split("|")[1]) - 348.2114) < 0.001)
    print(f"   348.21 own anchor: (time {cb[own].coefficients.time:+.2f}, shift {cb[own].coefficients.sample_shift:+.2f})"
          f" -> ({ca[own].coefficients.time:+.2f}, {ca[own].coefficients.sample_shift:+.2f})")

    fig, ax = plt.subplots(figsize=(11, 4.2))
    series = (("只靠錨點（不投票）", preliminary, dict(color="#9a9a9a", marker="o", s=16)),
              ("成本搜尋的列投票", control, dict(color="#1f77b4", marker="s", s=18)),
              ("每帶漂移的列投票", pb_model, dict(color="#d62728", marker="^", s=20)))
    for name, m, style in series:
        xs = sorted(m.sample_shift_min, key=rank.get)
        ax.scatter([rank[s] + 1 for s in xs], [m.sample_shift_min[s] * 60 for s in xs], label=name, zorder=2, **style)
    for s, v in pb_model.sample_shift_min.items():
        if abs(v - base_m.get(s, v + 1)) <= 1e-12:
            ax.annotate(f"第 {rank[s] + 1} 針", (rank[s] + 1, v * 60), textcoords="offset points", xytext=(6, 6), fontsize=9)
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xlabel("針序（1 起算）")
    ax.set_ylabel("樣本位移 M（秒）")
    ax.set_title("三種列來源投出的 M；有標註的是每帶列票數不足、留在錨點值的針", fontsize=11)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\09_m_vote.png", dpi=130)
    print("\nfigure written")
