"""PROTOTYPE output only: votes per injection behind the M fail-close rule, for the captured
model and chain A. Checks that the votes' median matches each model's M (injections with
>= 3 votes) and draws vote spread by injection order with the outlier limit."""

import pickle
import sys
from dataclasses import replace
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from a_end_to_end import anchor_observations, shift_rows  # noqa: E402
from failclose_m import ORDER, votes  # noqa: E402
from whole_batch import inputs, rank  # noqa: E402

from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

HERE = Path(__file__).parent
plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def summary(name, rows, base, drift, ax):
    by_sample, n_voters = votes(rows, base, drift.sample_shift_min)
    spread, count, gap = {}, {}, []
    for s in drift.sample_shift_min:
        v = by_sample.get(s, [])
        count[s] = len(v)
        if len(v) >= 3:
            c = median(v)
            spread[s] = 1.4826 * median(abs(x - c) for x in v) * 60
            gap.append(abs(c - drift.sample_shift_min[s]) * 60)
    values = sorted(spread.values())
    centre = median(values)
    limit = centre + 3 * 1.4826 * median(abs(x - centre) for x in values)
    flagged = {s for s in drift.sample_shift_min if count[s] < 3 or spread.get(s, 0) > limit}
    stems = sorted(drift.sample_shift_min, key=lambda s: ORDER[s])
    print(f"[{name}] voting rows {n_voters}; |median vote - M| over injections with >= 3 votes: max {max(gap):.2f} s")
    for x in (3, 67):
        s = sorted(rank, key=rank.get)[x - 1]
        print(f"   injection {x}: votes {count[s]}, spread {spread.get(s, float('nan')):.1f} s, limit {limit:.1f} s,"
              f" flagged {s in flagged}")
    xs = [rank[s] + 1 for s in stems if s in spread]
    ys = [spread[s] for s in stems if s in spread]
    ax.scatter(xs, ys, s=16, c=["#d62728" if s in flagged else "#1f77b4" for s in stems if s in spread], zorder=2)
    for s in stems:
        if count[s] < 3:
            ax.scatter(rank[s] + 1, 0, marker="x", c="#d62728", zorder=3)
    for x in (3, 67):
        s = sorted(rank, key=rank.get)[x - 1]
        ax.annotate(f"第 {x} 針（{count[s]} 票）", (x, spread.get(s, 0)), textcoords="offset points", xytext=(5, 6), fontsize=9)
    ax.axhline(limit, color="#9ca3af", lw=0.8, ls="--")
    ax.set_title(f"{name}：投票列 {n_voters} 條；紅＝被標成不可信（× 是票數不足）", fontsize=10)
    ax.set_xlabel("針序（1 起算）")
    ax.set_ylabel("票的分歧（秒）")


if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    (captured,) = {id(ws.rt_drift): ws.rt_drift for ws in inputs.worksets if ws.rt_drift}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    base_frame = SampleRtFrame(injection_order=ORDER, sample_universe=frozenset(inputs.sample_stems))
    _, preliminary = fit_batch_rt_model(model)
    control_rows, _ = pickle.loads((HERE / "m_vote_control_rows.pkl").read_bytes())
    subject = {a.channel: (a.precursor_mz, a.neutral_loss_da) for batch in model.anchor_batches for a in batch}
    model_a = replace(model, anchor_batches=(tuple(anchor_observations(worksets, subject)),))
    calibration_a, preliminary_a = fit_batch_rt_model(model_a)
    rows_a = shift_rows(worksets, lambda ws: base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration_a, preliminary_a),
                        preliminary_a)
    _, drift_a = fit_batch_rt_model(model_a, shift_rows=rows_a)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7.4))
    summary("擷取模型（成本搜尋的列）", control_rows, preliminary.sample_shift_min, captured, axes[0])
    summary("整段 A（逐帶漂移的列）", rows_a, preliminary_a.sample_shift_min, drift_a, axes[1])
    fig.tight_layout()
    fig.savefig(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\15_m_vote_spread.png", dpi=125)
    print("figure written")
