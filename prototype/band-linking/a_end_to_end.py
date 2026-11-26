"""PROTOTYPE: candidate A end to end - every stage takes its rows from one linking engine,
with no cost search anywhere.

Chain (each step uses only the previous step's output):
  1. calibration anchors: bands linked on raw RT (step cap 0.6 min); every direct,
     quantifiable owner is an anchor observation, and only members of bands with >= 2
     direct peaks carry a group id (the calibration reads those; the drift model reads all,
     as in production);
  2. batch calibration and preliminary drift model fitted from them (fit_batch_rt_model);
  3. M voted by per-band rows of direct owners linked in that frame (>= 20 samples);
  4. final per-band linking on that calibration with that drift model.

Read before running:
- The single-stage swaps moved 1-3% of direct peaks each. If the whole chain stays near
  that (vs per-band with the captured models) and the target windows hold (348.21 one main
  row, 302.1774 two rows with no 22.7 or 3.4 min member, 334.05 two rows), A holds as a
  whole and the draft can propose replacing every stage.
- If the differences compound or targets break, report which stage drives them; chaining
  alone is then not enough.
Agreement with the cost search is agreement, not correctness; the cost-search grouping was
itself built with the captured models.
"""

import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import describe_rows, perband, rows_labels  # noqa: E402
from whole_batch import differ, inputs, labels, owners, rank  # noqa: E402

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES  # noqa: E402
from xic_extractor.alignment.rt_anchors import (  # noqa: E402
    RtAnchorObservation,
    fit_batch_rt_model,
    places_sample_on_rt_frame,
)
from xic_extractor.alignment.rt_drift import ROW_MIN_SAMPLES  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402


def anchor_observations(worksets, subject, step=0.6):
    out = []
    for ws in worksets:
        members = sorted(ws.owners, key=lambda o: o.owner_id)
        raw = [(rank[o.sample_stem], o.peak.rt_apex_min, o.owner_id.split("|")[1] == "dda_cid_nl",
                float(o.owner_id.split("|")[2])) for o in members]
        group = {}
        if sum(p[2] for p in raw) >= 2:
            for r, band in enumerate(link_bands(raw, max_rt_step=step)):
                if sum(raw[i][2] for i in band) >= 2:
                    for i in band:
                        group[i] = f"{ws.key}#{r}"
        key = ws.key.split("|")
        mz, loss = subject.get(ws.key, (float(key[1]), float(key[3]) if len(key) > 3 else None))
        for i, o in enumerate(members):
            if raw[i][2] and places_sample_on_rt_frame(o):
                out.append(RtAnchorObservation(ws.key, mz, loss, o.sample_stem, o.owner_id, o.peak.rt_apex_min, group.get(i)))
    return out


def shift_rows(worksets, frame_of, drift):
    out = []
    for ws in worksets:
        frame = frame_of(ws)
        placed = sorted(((o, e.rt_min) for o in ws.owners if not CID_NL_GROUPING_RULES.method_unobserved(o)
                         and (e := frame.membership_coordinate(o)) is not None), key=lambda item: item[0].owner_id)
        if len(placed) < ROW_MIN_SAMPLES:
            continue
        ids = [o.owner_id for o, _ in placed]
        peaks = [(rank[o.sample_stem], y, True, float(o.owner_id.split("|")[2])) for o, y in placed]
        for band in perband(ws, ids, peaks, drift=drift)[0]:
            row = {placed[i][0].sample_stem: placed[i][1] for i in band}
            if len(row) >= ROW_MIN_SAMPLES:
                out.append(row)
    return out


def peaks_under(calibration, ws, ids, captured_peaks):
    """Same peaks, calibrated with ``calibration`` (raw RT where it gives none)."""
    out = []
    for o, p in zip(ids, captured_peaks):
        raw = float(o.split("|")[3])
        fitted = calibration.samples.get(owners[ws.key][o])
        y = fitted.calibrated_rt(raw) if fitted is not None else None
        out.append((p[0], raw if y is None else y, p[2], p[3]))
    return out


if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    (captured_drift,) = {id(ws.rt_drift): ws.rt_drift for ws in inputs.worksets if ws.rt_drift}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    subject = {a.channel: (a.precursor_mz, a.neutral_loss_da) for batch in model.anchor_batches for a in batch}
    base_frame = SampleRtFrame(injection_order=inputs.injection_order, sample_universe=frozenset(inputs.sample_stems))

    observations = anchor_observations(worksets, subject)
    model_a = replace(model, anchor_batches=(tuple(observations),))
    calibration_a, preliminary_a = fit_batch_rt_model(model_a)
    rows = shift_rows(worksets, lambda ws: base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration_a, preliminary_a),
                      preliminary_a)
    _, drift_a = fit_batch_rt_model(model_a, shift_rows=rows)
    print(f"anchor observations {len(observations)} ({sum(o.bootstrap_group_id is not None for o in observations)} in bands);"
          f" drift anchors {len(drift_a.anchors)} (captured {len(captured_drift.anchors)}); M rows {len(rows)}")
    common = [s for s in drift_a.sample_shift_min if s in captured_drift.sample_shift_min]
    dm = sorted(abs(drift_a.sample_shift_min[s] - captured_drift.sample_shift_min[s]) * 60 for s in common)
    print(f"M vs captured: median {median(dm):.1f} s, p95 {dm[int(0.95 * (len(dm) - 1))]:.1f} s, max {dm[-1]:.1f} s")

    stats = Counter()
    by_window, by_injection = Counter(), Counter()
    targets = {348.2114: None, 302.1766: None, 334.0504: None, 504.1913: None, 297.0579: None, 311.1413: None}
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, captured_peaks, _ = coordinates(ws, flatten=False)
        d = [i for i, p in enumerate(captured_peaks) if p[2]]
        if len(d) < 2:
            continue
        peaks_a = peaks_under(calibration_a, ws, ids, captured_peaks)
        a = rows_labels(perband(ws, ids, peaks_a, drift=drift_a)[0], peaks_a)
        b = rows_labels(perband(ws, ids, captured_peaks)[0], captured_peaks)
        none = [labels["none"][ws.key][o] for o in ids]
        moved = differ(d, [a[i] for i in d], [b[i] for i in d])
        stats["windows"] += 1
        stats["direct"] += len(d)
        stats["changed vs per-band (captured models)"] += moved > 0
        stats["moved vs per-band (captured models)"] += moved
        stats["moved vs cost search (A)"] += differ(d, [a[i] for i in d], [none[i] for i in d])
        stats["moved vs cost search (per-band, captured)"] += differ(d, [b[i] for i in d], [none[i] for i in d])
        stats["identical vs cost search (A)"] += differ(d, [a[i] for i in d], [none[i] for i in d]) == 0
        if moved:
            by_window[f"{float(ws.key.split('|')[1]):.4f}"] += moved
        for t in targets:
            if abs(float(ws.key.split("|")[1]) - t) < 0.001:
                targets[t] = (describe_rows(peaks_a, perband(ws, ids, peaks_a, drift=drift_a)[0]),
                              describe_rows(captured_peaks, perband(ws, ids, captured_peaks)[0]))
    n = stats["direct"]
    print(f"\nwindows {stats['windows']}, direct {n}")
    print(f"  A vs per-band with the captured models: windows changed {stats['changed vs per-band (captured models)']},"
          f" direct moved {stats['moved vs per-band (captured models)']} ({100 * stats['moved vs per-band (captured models)'] / n:.2f}%)")
    print(f"  vs cost search: A {stats['moved vs cost search (A)']} ({100 * stats['moved vs cost search (A)'] / n:.2f}%),"
          f" identical windows {stats['identical vs cost search (A)']}/{stats['windows']};"
          f" per-band with captured models {stats['moved vs cost search (per-band, captured)']}"
          f" ({100 * stats['moved vs cost search (per-band, captured)'] / n:.2f}%)")
    print(f"  windows with the most moved direct peaks: {by_window.most_common(10)}")
    print("\ntargets (A | per-band with captured models):")
    for t, v in targets.items():
        if v:
            print(f"  {t:.2f} A:        {v[0]}")
            print(f"  {t:.2f} captured: {v[1]}")
