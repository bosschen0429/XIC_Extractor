"""PROTOTYPE: chain A's off-band members in 302.1774 and 348.21 - are they driven by drift
anchors that mix compounds (channel-unique peaks), the representation A is meant to delete?

Read before running: if removing the off-band anchor observations brings injections 3 and 67
back near the captured M and the off-band members leave the main rows, the mixed anchors
drive it (A is unfinished, not failed). If nothing changes, the cause stays unchecked.
"""

import sys
from dataclasses import replace
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from a_end_to_end import anchor_observations, peaks_under, shift_rows  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
TARGETS = ((302.1766, 8.0), (348.2114, 9.0))

if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    (captured,) = {id(ws.rt_drift): ws.rt_drift for ws in inputs.worksets if ws.rt_drift}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    subject = {a.channel: (a.precursor_mz, a.neutral_loss_da) for batch in model.anchor_batches for a in batch}
    base_frame = SampleRtFrame(injection_order=inputs.injection_order, sample_universe=frozenset(inputs.sample_stems))
    stems = sorted(rank, key=rank.get)
    inj3, inj67 = stems[2], stems[66]

    def chain(observations):
        model_a = replace(model, anchor_batches=(tuple(observations),))
        calibration_a, preliminary_a = fit_batch_rt_model(model_a)
        rows = shift_rows(worksets, lambda ws: base_frame.with_warp(ws.rt_warp).with_sample_calibration(
            calibration_a, preliminary_a), preliminary_a)
        return calibration_a, preliminary_a, fit_batch_rt_model(model_a, shift_rows=rows)[1]

    def report(name, calibration_a, preliminary_a, drift_a):
        print(f"\n[{name}] M at injection 3 / 67: preliminary {preliminary_a.sample_shift_min.get(inj3, float('nan')) * 60:+.1f}"
              f" / {preliminary_a.sample_shift_min.get(inj67, float('nan')) * 60:+.1f} s, voted"
              f" {drift_a.sample_shift_min.get(inj3, float('nan')) * 60:+.1f} / {drift_a.sample_shift_min.get(inj67, float('nan')) * 60:+.1f} s;"
              f" captured {captured.sample_shift_min[inj3] * 60:+.1f} / {captured.sample_shift_min[inj67] * 60:+.1f} s")
        for target, below in TARGETS:
            ws = next(w for w in inputs.worksets if abs(float(w.key.split("|")[1]) - target) < 0.001)
            ids, captured_peaks, _ = coordinates(ws, flatten=False)
            peaks_a = peaks_under(calibration_a, ws, ids, captured_peaks)
            trace = []
            bands, _ = perband(ws, ids, peaks_a, trace=trace, drift=drift_a)
            main = max(bands, key=lambda b: sum(peaks_a[i][2] for i in b))
            off = [f"{peaks_a[i][1]:.2f} min [{rank[owners[ws.key][ids[i]]] + 1}]" for i in main if peaks_a[i][1] < below]
            print(f"  {target:.2f}: main row {sum(peaks_a[i][2] for i in main)} direct; members below {below} min: {off or 'none'};"
                  f" seeds {[(n, m, round(c.time, 2), round(c.sample_shift, 2)) for n, m, c in trace]}")

    observations = anchor_observations(worksets, subject)
    full = chain(observations)
    report("chain A", *full)

    removed = set()
    for target, below in TARGETS:
        ws = next(w for w in inputs.worksets if abs(float(w.key.split("|")[1]) - target) < 0.001)
        for model_name, drift in (("preliminary", full[1]), ("voted", full[2])):
            anchor = next((a for a in drift.anchors if a.channel == ws.key), None)
            if anchor is None:
                print(f"  {target:.2f}: no drift anchor in the {model_name} model")
                continue
            for o in observations:
                if o.owner_id in anchor.owner_ids:
                    fitted = full[0].samples.get(o.sample_stem)
                    y = fitted.calibrated_rt(o.rt_min) if fitted else None
                    if y is not None and y < below:
                        removed.add(o.owner_id)
                        print(f"  {target:.2f} {model_name} anchor holds an off-band peak: calibrated {y:.2f} min,"
                              f" injection {rank[o.sample_stem] + 1}")
    print(f"removing {len(removed)} anchor observations")
    trimmed = chain([o for o in observations if o.owner_id not in removed])
    report("chain A without those observations", *trimmed)

    fig, ax = plt.subplots(figsize=(11, 4.2))
    for name, drift, style in (("擷取（成本搜尋的列投票）", captured, dict(color="#1f77b4", marker="s", s=18)),
                               ("整段 A", full[2], dict(color="#d62728", marker="^", s=20)),
                               ("整段 A，拿掉混入的錨點峰", trimmed[2], dict(color="#1f9d55", marker="o", s=14))):
        xs = sorted(drift.sample_shift_min, key=rank.get)
        ax.scatter([rank[s] + 1 for s in xs], [drift.sample_shift_min[s] * 60 for s in xs], label=name, zorder=2, **style)
    for s in (inj3, inj67):
        ax.axvline(rank[s] + 1, color="#cccccc", lw=0.8, zorder=0)
        ax.annotate(f"第 {rank[s] + 1} 針", (rank[s] + 1, captured.sample_shift_min[s] * 60), textcoords="offset points",
                    xytext=(6, -12), fontsize=9)
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xlabel("針序（1 起算）")
    ax.set_ylabel("樣本位移 M（秒）")
    ax.set_title("整段 A 的 M 與擷取的 M", fontsize=11)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\12_chain_a_m.png", dpi=130)
    print("figure written")
