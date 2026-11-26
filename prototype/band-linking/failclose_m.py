"""PROTOTYPE: fail closed on M where the voting rows disagree.

Votes are the ones master's _row_voted_shift counts (same voters, same neighbours, the final
M as the neighbours' reference). A sample's M is untrusted when it has fewer than 3 votes, or
when its votes spread (1.4826 x MAD) is an outlier among samples: above the median spread plus
3 x 1.4826 x MAD of the spreads. Untrusted M is removed from the model, so a per-band seed
that uses M cannot place that sample's peaks; they fall to the calibrated-RT pass.

Measured twice: with the captured model (cost-search rows) and along chain A.
Read before running: the rule is worth keeping if it flags few injections (3 and 67 among
them), empties few cells, removes chain A's off-band members in 302.1774 and 348.21, and the
target windows still pass.
"""

import pickle
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from a_end_to_end import anchor_observations, peaks_under, shift_rows  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import describe_rows, perband, rows_labels  # noqa: E402
from whole_batch import differ, inputs, labels, owners, rank  # noqa: E402

from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_drift import (  # noqa: E402
    _MIN_COMMON_SAMPLES,
    _MIN_ROW_RESPONSE,
    _MIN_ROW_VOTES,
    _ROW_NEIGHBORS,
    _SYNC_CORRELATION,
    ROW_MIN_SAMPLES,
    _slope,
    _spearman,
)
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

HERE = Path(__file__).parent
ORDER = inputs.injection_order
TARGETS = (348.2114, 302.1766, 334.0504, 504.1913, 297.0579, 311.1413)


def votes(rows, base, final):
    voters = []
    for row in rows:
        members = sorted((s for s in row if s in ORDER), key=lambda s: (ORDER[s], s))
        if len(members) < ROW_MIN_SAMPLES:
            continue
        neighbors = {s: sorted((o for o in members if o != s), key=lambda o: (abs(ORDER[o] - ORDER[s]), ORDER[o], o))[:_ROW_NEIGHBORS]
                     for s in members}
        local = {s: row[s] - float(median(row[n] for n in neighbors[s])) for s in members}
        known = [s for s in members if s in base and all(n in base for n in neighbors[s])]
        if len(known) < _MIN_COMMON_SAMPLES:
            continue
        shift = [base[s] - float(median(base[n] for n in neighbors[s])) for s in known]
        offsets = [local[s] for s in known]
        response = _slope(shift, offsets)
        if response >= _MIN_ROW_RESPONSE and _spearman(shift, offsets) >= _SYNC_CORRELATION:
            voters.append((local, neighbors, response))
    out = defaultdict(list)
    for local, neighbors, response in voters:
        for s in local:
            if all(n in final for n in neighbors[s]):
                out[s].append(local[s] / response + float(median(final[n] for n in neighbors[s])))
    return out, len(voters)


def untrusted(rows, base, drift, name):
    by_sample, n_voters = votes(rows, base, drift.sample_shift_min)
    spread = {}
    for s, v in by_sample.items():
        if len(v) >= _MIN_ROW_VOTES:
            c = median(v)
            spread[s] = 1.4826 * median(abs(x - c) for x in v) * 60
    values = sorted(spread.values())
    centre = median(values)
    mad = 1.4826 * median(abs(x - centre) for x in values)
    limit = centre + 3 * mad
    few = {s for s in drift.sample_shift_min if len(by_sample.get(s, ())) < _MIN_ROW_VOTES}
    wide = {s for s, x in spread.items() if x > limit}
    print(f"[{name}] voting rows {n_voters}; vote spread per sample: median {centre:.1f} s, p90"
          f" {values[int(0.9 * (len(values) - 1))]:.1f} s, outlier limit {limit:.1f} s")
    for s in sorted(few | wide, key=lambda s: ORDER[s]):
        print(f"   untrusted [{rank[s] + 1:2d}] {s[:30]:30s} votes {len(by_sample.get(s, ())):3d},"
              f" spread {spread.get(s, float('nan')):6.1f} s, M {drift.sample_shift_min[s] * 60:+7.1f} s"
              f"{' (too few votes)' if s in few else ''}")
    return replace(drift, sample_shift_min={s: v for s, v in drift.sample_shift_min.items() if s not in few | wide})


def compare(name, peaks_of, drift, closed):
    stats = Counter()
    emptied = Counter()
    rows_hit = set()
    shown = {}
    for ws in inputs.worksets:
        if ws.key not in owners:
            continue
        ids, captured_peaks, _ = coordinates(ws, flatten=False)
        d = [i for i, p in enumerate(captured_peaks) if p[2]]
        if len(d) < 2:
            continue
        peaks = peaks_of(ws, ids, captured_peaks)
        a = rows_labels(perband(ws, ids, peaks, drift=drift, carry=True)[0], peaks)
        b = rows_labels(perband(ws, ids, peaks, drift=closed, carry=True)[0], peaks)
        none = [labels["none"][ws.key][o] for o in ids]
        moved = differ(d, [a[i] for i in d], [b[i] for i in d])
        stats["windows"] += 1
        stats["direct"] += len(d)
        stats["changed"] += moved > 0
        stats["moved"] += moved
        stats["in rows before"] += sum(not str(a[i]).startswith("single") for i in d)
        stats["in rows after"] += sum(not str(b[i]).startswith("single") for i in d)
        stats["vs cost before"] += differ(d, [a[i] for i in d], [none[i] for i in d])
        stats["vs cost after"] += differ(d, [b[i] for i in d], [none[i] for i in d])
        for i in d:
            if not str(a[i]).startswith("single") and str(b[i]).startswith("single"):
                emptied[peaks[i][0] + 1] += 1
                rows_hit.add((ws.key, a[i]))
        mz = float(ws.key.split("|")[1])
        for t in TARGETS:
            if abs(mz - t) < 0.001:
                shown[t] = (describe_rows(peaks, perband(ws, ids, peaks, drift=drift, carry=True)[0]),
                            describe_rows(peaks, perband(ws, ids, peaks, drift=closed, carry=True)[0]))
    n = stats["direct"]
    print(f"  [{name}] windows changed {stats['changed']}/{stats['windows']}, direct moved {stats['moved']}"
          f" ({100 * stats['moved'] / n:.2f}%); direct in rows {stats['in rows before']} -> {stats['in rows after']};"
          f" emptied {sum(emptied.values())} in {len(rows_hit)} rows; by injection {emptied.most_common(8)}")
    print(f"  [{name}] vs cost search: {100 * stats['vs cost before'] / n:.2f}% -> {100 * stats['vs cost after'] / n:.2f}%")
    for t, (before, after) in shown.items():
        print(f"    {t:.2f} before: {before}")
        print(f"    {t:.2f} after:  {after}")


if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    (captured,) = {id(ws.rt_drift): ws.rt_drift for ws in inputs.worksets if ws.rt_drift}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    base_frame = SampleRtFrame(injection_order=ORDER, sample_universe=frozenset(inputs.sample_stems))

    _, preliminary = fit_batch_rt_model(model)
    control_rows, _ = pickle.loads((HERE / "m_vote_control_rows.pkl").read_bytes())
    closed = untrusted(control_rows, preliminary.sample_shift_min, captured, "captured model, cost-search rows")
    compare("captured model", lambda ws, ids, p: p, captured, closed)

    subject = {a.channel: (a.precursor_mz, a.neutral_loss_da) for batch in model.anchor_batches for a in batch}
    model_a = replace(model, anchor_batches=(tuple(anchor_observations(worksets, subject)),))
    calibration_a, preliminary_a = fit_batch_rt_model(model_a)
    rows_a = shift_rows(worksets, lambda ws: base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration_a, preliminary_a),
                        preliminary_a)
    _, drift_a = fit_batch_rt_model(model_a, shift_rows=rows_a)
    print()
    closed_a = untrusted(rows_a, preliminary_a.sample_shift_min, drift_a, "chain A, per-band rows")
    compare("chain A", lambda ws, ids, p: peaks_under(calibration_a, ws, ids, p), drift_a, closed_a)
