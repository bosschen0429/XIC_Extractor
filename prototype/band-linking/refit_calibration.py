"""PROTOTYPE: can rows linked on raw RT stand in for the bootstrap as calibration anchors?

The batch RT calibration is fitted with the product fitter (fit_batch_rt_model)
from the capture's own cohorts, ISTDs and QC list, twice:
  control - the capture's bootstrap anchors; must reproduce the captured map;
  linked  - direct peaks of rows linked on raw RT (step cap 0.4 or 0.6 min).
Then every window is linked again on each map's coordinates and compared.
"""

import sys
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from whole_batch import differ, inputs, labels, rank  # noqa: E402

from xic_extractor.alignment.rt_anchors import (  # noqa: E402
    RtAnchorObservation,
    fit_batch_rt_model,
    places_sample_on_rt_frame,
)

# Issue #786: the injection right after a short Blank or SDOLEK run is under-equilibrated.
AFTER_SHORT_METHOD = {
    "Breast_Cancer_Tissue_pooled_QC2", "Breast_Cancer_Tissue_pooled_QC3", "Breast_Cancer_Tissue_pooled_QC_4",
    "Breast_Cancer_Tissue_pooled_QC5", "Breast_Cancer_Tissue_pooled_QC6", "Breast_Cancer_Tissue_pooled_QC7",
    "TumorBC2312_DNA", "BenignfatBC1116_DNA",
}


def parse(owner):
    f = owner.owner_id.split("|")
    return f[1] == "dda_cid_nl", float(f[2])


def rows_of(peaks, step):
    """Bands holding at least two direct peaks, as member index lists."""
    return [b for b in link_bands(peaks, max_rt_step=step) if sum(peaks[i][2] for i in b) >= 2]


def labels_of(peaks, step=0.4):
    out = [f"single:{i}" for i in range(len(peaks))]
    for r, band in enumerate(rows_of(peaks, step)):
        for i in band:
            out[i] = r
    return out


def coordinate(calibration, owner):
    sample = calibration.samples.get(owner.sample_stem)
    value = sample.calibrated_rt(owner.peak.rt_apex_min) if sample is not None else None
    return owner.peak.rt_apex_min if value is None else value


model_inputs = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}
captured = {id(ws.rt_calibration): ws.rt_calibration for ws in inputs.worksets if ws.rt_calibration}
print(f"batch models in capture: inputs {len(model_inputs)}, calibrations {len(captured)};"
      f" worksets without a calibration: {sum(ws.rt_calibration is None for ws in inputs.worksets)}")
(model,) = model_inputs.values()
(captured_map,) = captured.values()
boot = [a for batch in model.anchor_batches for a in batch]
print(f"bootstrap anchor observations: {len(boot)} ({sum(a.bootstrap_group_id is not None for a in boot)} in agreed groups),"
      f" cohorts {sorted(set(model.sample_cohorts.values()))}, ISTD readings {len(model.measured_istds)}, QC {len(model.qc_samples)}")
subject = {a.channel: (a.precursor_mz, a.neutral_loss_da) for a in boot}

worksets = [ws for ws in inputs.worksets if ws.rt_calibration is not None]
windows = []
for ws in worksets:
    owners_ = sorted(ws.owners, key=lambda o: o.owner_id)
    facts = [parse(o) for o in owners_]
    if sum(direct for direct, _ in facts) < 2:
        continue
    raw = [(rank[o.sample_stem], o.peak.rt_apex_min, direct, mz) for o, (direct, mz) in zip(owners_, facts)]
    windows.append((ws, owners_, raw))
print(f"windows with 2+ direct peaks: {len(windows)}")


def fit(anchors):
    start = time.perf_counter()
    calibration, _ = fit_batch_rt_model(replace(model, anchor_batches=(tuple(anchors),)))
    return calibration, time.perf_counter() - start


def linked_anchors(step):
    anchors = []
    for ws, owners_, raw in windows:
        key = ws.key.split("|")
        mz, loss = subject.get(ws.key, (float(key[1]), float(key[3]) if len(key) > 3 else None))
        for r, band in enumerate(rows_of(raw, step)):
            for i in band:
                owner = owners_[i]
                if raw[i][2] and places_sample_on_rt_frame(owner):
                    anchors.append(RtAnchorObservation(ws.key, mz, loss, owner.sample_stem, owner.owner_id,
                                                       owner.peak.rt_apex_min, f"{ws.key}#{r}"))
    return anchors


def compare(name, calibration, reference):
    """Calibrated RT of every direct peak under two maps."""
    diffs = defaultdict(list)
    lost = gained = 0
    for _ws, owners_, raw in windows:
        for owner, peak in zip(owners_, raw):
            if not peak[2]:
                continue
            a = calibration.samples.get(owner.sample_stem)
            b = reference.samples.get(owner.sample_stem)
            x = a.calibrated_rt(peak[1]) if a else None
            y = b.calibrated_rt(peak[1]) if b else None
            if (x is None) != (y is None):
                lost += x is None
                gained += y is None
                continue
            if x is None:
                continue
            group = "#786, RT < 12" if owner.sample_stem in AFTER_SHORT_METHOD and peak[1] < 12 else (
                "#786, RT >= 12" if owner.sample_stem in AFTER_SHORT_METHOD else "other injections")
            diffs[group].append(abs(x - y) * 60)
    print(f"  {name}: direct peaks newly without a coordinate {lost}, newly with one {gained}")
    for group in ("other injections", "#786, RT < 12", "#786, RT >= 12"):
        v = sorted(diffs[group])
        if v:
            print(f"    {group:17s} n {len(v):6d}  |diff| s: median {median(v):5.2f}, p95 {v[int(0.95 * (len(v) - 1))]:6.2f},"
                  f" max {v[-1]:6.1f}")


def relink(name, calibration, reference):
    """Link every window on each map's coordinates and count direct peaks placed differently."""
    moved = same = total = moved_none = 0
    for ws, owners_, raw in windows:
        d = [i for i, p in enumerate(raw) if p[2]]
        a = labels_of([(p[0], coordinate(calibration, o), p[2], p[3]) for o, p in zip(owners_, raw)])
        b = labels_of([(p[0], coordinate(reference, o), p[2], p[3]) for o, p in zip(owners_, raw)])
        m = differ(d, [a[i] for i in d], [b[i] for i in d])
        none = [labels["none"][ws.key][o.owner_id] for o in owners_]
        moved_none += differ(d, [a[i] for i in d], [none[i] for i in d])
        moved += m
        same += m == 0
        total += len(d)
    print(f"  {name}: relinked on this map vs on the reference map: identical windows {same}/{len(windows)}"
          f" ({100 * same / len(windows):.0f}%), direct placed differently {moved} ({100 * moved / total:.1f}%);"
          f" vs no-band grouping {100 * moved_none / total:.1f}%")


control, seconds = fit(boot)
print(f"\ncontrol fit from the bootstrap anchors ({seconds:.1f} s):")
compare("control vs captured map", control, captured_map)
print("  knots per #786 injection (control):",
      {s.split("_")[-1] if "QC" in s else s.split("_")[0]: len(control.samples[s].knots) for s in sorted(AFTER_SHORT_METHOD)})
for step in (0.4, 0.6):
    anchors = linked_anchors(step)
    linked, seconds = fit(anchors)
    print(f"\nlinked on raw RT, step cap {step} min: {len(anchors)} anchor observations"
          f" in {len({a.bootstrap_group_id for a in anchors})} rows; fit {seconds:.1f} s")
    print("  knots per #786 injection:",
          {s.split("_")[-1] if "QC" in s else s.split("_")[0]: len(linked.samples[s].knots) if s in linked.samples else 0
           for s in sorted(AFTER_SHORT_METHOD)})
    compare("linked map vs control map", linked, control)
    relink("linked map", linked, control)


# Holdout: refit both anchor sets without the ISTDs, dropping anchor readings that are the
# ISTD peaks themselves, then ask which map puts each ISTD at one RT across injections.
istd = defaultdict(dict)
for label, sample, rt in model.measured_istds:
    istd[label][sample] = rt
print("\nISTD labels:", {label: len(v) for label, v in istd.items()})


def without_istds(anchors):
    readings = defaultdict(list)
    for _label, sample, rt in model.measured_istds:
        readings[sample].append(rt)
    return [a for a in anchors if not any(abs(a.rt_min - rt) < 0.005 for rt in readings[a.sample_stem])]


def fit_holdout(anchors):
    calibration, _ = fit_batch_rt_model(replace(model, anchor_batches=(tuple(without_istds(anchors)),),
                                                measured_istds=()))
    return calibration


def istd_spread(name, calibration):
    print(f"  {name}")
    for label, by_sample in sorted(istd.items(), key=lambda kv: median(kv[1].values())):
        values, hit = {}, {}
        for sample, rt in by_sample.items():
            fitted = calibration.samples.get(sample) if calibration is not None else None
            value = rt if calibration is None else (fitted.calibrated_rt(rt) if fitted else None)
            if value is not None:
                values[sample] = value * 60
        if not any(s not in AFTER_SHORT_METHOD for s in values):
            print(f"    {label:24s} covered {len(values):2d}/{len(by_sample)}")
            continue
        center = median(v for s, v in values.items() if s not in AFTER_SHORT_METHOD)
        normal = sorted(abs(v - center) for s, v in values.items() if s not in AFTER_SHORT_METHOD)
        shifted = [v - center for s, v in values.items() if s in AFTER_SHORT_METHOD]
        print(f"    {label:24s} RT {center / 60:5.1f}  covered {len(values):2d}/{len(by_sample)}"
              f"  other injections |dev| median {median(normal):5.1f} s, p95 {normal[int(0.95 * (len(normal) - 1))]:5.1f} s"
              f"  #786 injections mean dev {sum(shifted) / max(1, len(shifted)):+6.1f} s")


istd_spread("raw RT (no map)", None)
istd_spread("bootstrap anchors, ISTDs held out", fit_holdout(boot))
for step in (0.4, 0.6):
    istd_spread(f"rows linked on raw RT (cap {step}), ISTDs held out", fit_holdout(linked_anchors(step)))


# Variant: anchors only from windows where no injection holds two peaks (the unambiguous bands).
def clean_anchors(step):
    clean = {ws.key for ws, _owners, raw in windows if max(Counter(p[0] for p in raw).values()) < 2}
    return [a for a in linked_anchors(step) if a.channel in clean], len(clean)


from collections import Counter  # noqa: E402

anchors, n_clean = clean_anchors(0.6)
clean_map, _ = fit(anchors)
print(f"\nclean windows only ({n_clean} of {len(windows)} windows), cap 0.6: {len(anchors)} anchor observations"
      f" in {len({a.bootstrap_group_id for a in anchors})} rows")
compare("clean-window map vs control map", clean_map, control)
relink("clean-window map", clean_map, control)
istd_spread("clean-window rows (cap 0.6), ISTDs held out", fit_holdout(anchors))

# Where do the all-rows (cap 0.6) map and the control map disagree most?
linked06, _ = fit(linked_anchors(0.6))
worst = []
for ws, owners_, raw in windows:
    for owner, peak in zip(owners_, raw):
        a, b = linked06.samples.get(owner.sample_stem), control.samples.get(owner.sample_stem)
        x = a.calibrated_rt(peak[1]) if a else None
        y = b.calibrated_rt(peak[1]) if b else None
        if peak[2] and x is not None and y is not None:
            worst.append((abs(x - y) * 60, owner.sample_stem, peak[1], y, x))
worst.sort(reverse=True)
print("\nlargest map disagreements (all rows, cap 0.6) - |diff| s, sample, raw RT, control, linked:")
seen = set()
for item in worst:
    key = (item[1], round(item[2], 1))
    if key in seen:
        continue
    seen.add(key)
    print(f"  {item[0]:6.1f}  {item[1]:32s} raw {item[2]:6.2f}  control {item[3]:6.2f}  linked {item[4]:6.2f}")
    if len(seen) == 8:
        break
big = [w for w in worst if w[0] > 10]
print(f"direct peaks with |diff| > 10 s: {len(big)}; samples {len({w[1] for w in big})};"
      f" by raw RT: {sorted(Counter(int(w[2] // 5) * 5 for w in big).items())}")


# Which map is right at 26-29 min for TumorBC2257-2259? Read the nearest ISTDs directly.
holdout_boot, holdout_linked = fit_holdout(boot), fit_holdout(linked_anchors(0.6))
by_rank = sorted(rank, key=rank.get)
focus = [s for s in by_rank if rank["TumorBC2257_DNA"] - 3 <= rank[s] <= rank["TumorBC2259_DNA"] + 3]
print("\nISTD raw RT and calibrated RT near TumorBC2257-2259 (min); columns: raw | control | linked(cap 0.6) |"
      " holdout bootstrap | holdout linked")
for label in ("d4-N6-2HE-dA", "d3-N6-medA", "d3-dG-C8-MeIQx"):
    center = median(rt for s, rt in istd[label].items() if s not in AFTER_SHORT_METHOD)
    print(f"  {label} (batch median raw {center:.2f})")
    for s in focus:
        rt = istd[label].get(s)
        if rt is None:
            continue
        cells = []
        for m in (control, linked06, holdout_boot, holdout_linked):
            fitted = m.samples.get(s)
            value = fitted.calibrated_rt(rt) if fitted else None
            cells.append("  --  " if value is None else f"{value:6.2f}")
        print(f"    {rank[s] + 1:3d} {s:24s} {rt:6.2f} | " + " | ".join(cells))


# Iterate: link again on the map just fitted (normal 0.4 cap), refit from those rows' raw apices.
def anchors_on(calibration, step=0.4):
    anchors = []
    for ws, owners_, raw in windows:
        key = ws.key.split("|")
        mz, loss = subject.get(ws.key, (float(key[1]), float(key[3]) if len(key) > 3 else None))
        peaks = [(p[0], coordinate(calibration, o), p[2], p[3]) for o, p in zip(owners_, raw)]
        for r, band in enumerate(rows_of(peaks, step)):
            for i in band:
                owner = owners_[i]
                if raw[i][2] and places_sample_on_rt_frame(owner):
                    anchors.append(RtAnchorObservation(ws.key, mz, loss, owner.sample_stem, owner.owner_id,
                                                       owner.peak.rt_apex_min, f"{ws.key}#{r}"))
    return anchors


def medA_line(name, m):
    cells = []
    for s in ("TumorBC2257_DNA", "TumorBC2258_DNA", "TumorBC2259_DNA"):
        fitted = m.samples.get(s)
        value = fitted.calibrated_rt(istd["d3-N6-medA"][s]) if fitted else None
        cells.append("--" if value is None else f"{value:.2f}")
    print(f"  {name}: d3-N6-medA in TumorBC2257/2258/2259 -> {', '.join(cells)} (batch median 25.73)")


print("\niteration with ISTDs in the fit (production-like):")
current = linked06
medA_line("round 1 (raw RT rows, cap 0.6)", current)
for round_ in (2, 3):
    anchors = anchors_on(current)
    nxt, _ = fit(anchors)
    print(f" round {round_}: {len(anchors)} anchor observations")
    compare(f"round {round_} vs round {round_ - 1}", nxt, current)
    compare(f"round {round_} vs control", nxt, control)
    relink(f"round {round_}", nxt, control)
    medA_line(f"round {round_}", nxt)
    current = nxt

print("\niteration with ISTDs held out throughout:")
current = holdout_linked
for round_ in (2, 3):
    current = fit_holdout(anchors_on(current))
    istd_spread(f"round {round_}, ISTDs held out", current)


# Are the worst ISTD deviations of the linked map the same injections?
print("\nworst ISTD deviations (s) under each holdout map, injection order in brackets:")
for label in ("d3-5-medC", "d3-N6-medA", "d4-N6-2HE-dA", "d3-5-hmdC"):
    center_raw = istd[label]
    for name, m in (("bootstrap", holdout_boot), ("linked", holdout_linked)):
        values = {}
        for s, rt in center_raw.items():
            fitted = m.samples.get(s)
            v = fitted.calibrated_rt(rt) if fitted else None
            if v is not None:
                values[s] = v * 60
        center = median(v for s, v in values.items() if s not in AFTER_SHORT_METHOD)
        worst_ = sorted(((abs(v - center), s) for s, v in values.items() if s not in AFTER_SHORT_METHOD), reverse=True)[:5]
        print(f"  {label:14s} {name:9s}: " + ", ".join(f"{s.split('_')[0][-6:]}[{rank[s] + 1}] {d:.0f}" for d, s in worst_))


# Final checks. (a) Is the holdout a holdout? ISTD m/z from issue #786.
ISTD_MZ = {"d3-5-hmdC": 261.13, "d3-5-medC": 245.13, "15N5-8-oxodG": 289.08,
           "d4-N6-2HE-dA": 300.16, "d3-N6-medA": 269.14, "d3-dG-C8-MeIQx": 482.21}
readings_by_sample = defaultdict(list)
for label, sample, rt in model.measured_istds:
    readings_by_sample[sample].append((label, rt))


def istd_presence(name, anchors):
    anchors = [a for a in anchors if a.bootstrap_group_id is not None]
    kept = {id(a) for a in without_istds(anchors)}
    near, near_removed = Counter(), Counter()
    for a in anchors:
        for label, rt in readings_by_sample[a.sample_stem]:
            if abs(a.precursor_mz - ISTD_MZ[label]) <= 0.01 and abs(a.rt_min - rt) <= 0.05:
                near[label] += 1
                near_removed[label] += id(a) not in kept
    print(f"  {name}: removed by the holdout {len(anchors) - len(kept)};"
          + "".join(f" {label} {near_removed[label]}/{near[label]};" for label in ISTD_MZ))


print("\nISTD peaks among calibrating anchors (removed by holdout / present within 0.01 Da and 0.05 min):")
istd_presence("bootstrap", boot)
istd_presence("linked cap 0.6", linked_anchors(0.6))

# (b) TumorBC2257 knots, 20-32 min: (observed RT, reference - observed in s).
print("\nTumorBC2257 knots, observed 20-32 min (observed, reference - observed s):")
for name, m in (("control", control), ("linked cap 0.6", linked06),
                ("holdout bootstrap", holdout_boot), ("holdout linked", holdout_linked)):
    s = m.samples.get("TumorBC2257_DNA")
    knots = [(round(o, 2), round((r - o) * 60)) for o, r in (s.knots if s else ()) if 20 <= o <= 32]
    print(f"  {name:18s} {knots}")

# (c) The medA channel linked on raw RT (cap 0.6): are injections 1-10 in the ISTD's row?
medA = istd["d3-N6-medA"]
best = None
for ws, owners_, raw in windows:
    hits = sum(1 for o in owners_ if o.sample_stem in medA and abs(o.peak.rt_apex_min - medA[o.sample_stem]) < 0.01)
    if hits and (best is None or hits > best[0]):
        best = (hits, ws, owners_, raw)
if best is None:
    print("\nno window holds the d3-N6-medA peaks")
else:
    hits, ws, owners_, raw = best
    bands = sorted(rows_of(raw, 0.6), key=len, reverse=True)
    row_of = {i: k for k, b in enumerate(bands) for i in b}
    on_istd = [i for i, o in enumerate(owners_) if o.sample_stem in medA and abs(o.peak.rt_apex_min - medA[o.sample_stem]) < 0.01]
    print(f"\nmedA window {ws.key}: {hits} owners sit on the ISTD readings; their rows (cap 0.6, raw RT):"
          f" {dict(Counter(row_of.get(i) for i in on_istd))}; row sizes {[len(b) for b in bands[:4]]}")
    for s in by_rank[:10]:
        here = [(round(o.peak.rt_apex_min, 2), row_of.get(i), "d" if raw[i][2] else "m")
                for i, o in enumerate(owners_) if o.sample_stem == s and 23 <= o.peak.rt_apex_min <= 27]
        print(f"  [{rank[s] + 1:2d}] {s[:26]:26s} ISTD {medA.get(s, float('nan')):.2f}  peaks 23-27 min (RT, row, direct/ms1): {here}")

# (d) Figure: ISTD deviation from the batch centre by injection order.
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft JhengHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
fig, axes = plt.subplots(1, 2, figsize=(13, 4.4))
series = (("原始 RT（不校正）", None, dict(color="#9a9a9a", marker="o", s=14)),
          ("bootstrap 錨點擬合（ISTD 留出）", holdout_boot, dict(color="#1f77b4", marker="s", s=18)),
          ("連帶錨點擬合（ISTD 留出）", holdout_linked, dict(color="#d62728", marker="^", s=20)))
for ax, label in zip(axes, ("d4-N6-2HE-dA", "d3-N6-medA")):
    ax.axvspan(0.5, 10.5, color="#efefef", zorder=0)
    ax.axhline(0, color="black", lw=0.6, zorder=1)
    for name, m, style in series:
        values = {}
        for s, rt in istd[label].items():
            fitted = m.samples.get(s) if m is not None else None
            v = rt if m is None else (fitted.calibrated_rt(rt) if fitted else None)
            if v is not None:
                values[s] = v * 60
        center = median(v for s, v in values.items() if s not in AFTER_SHORT_METHOD)
        order = sorted(values, key=rank.get)
        ax.scatter([rank[s] + 1 for s in order], [values[s] - center for s in order], label=name, zorder=2, **style)
    ax.set_title(f"{label}（約 {median(istd[label].values()):.1f} 分）", fontsize=11)
    ax.set_xlabel("針序（1 起算；灰底＝第 1–10 針）")
axes[0].set_ylabel("偏離批次中心（秒）")
axes[1].legend(fontsize=9, loc="lower right")
fig.suptitle("ISTD 不進擬合時，兩組錨點擬合出的校正把 ISTD 放在哪裡", fontsize=12)
fig.tight_layout()
fig.savefig(r"C:\Users\user\Desktop\XIC_Extractor\output\issue-figures\06_istd_batch_start.png", dpi=130)
print("figure written")
