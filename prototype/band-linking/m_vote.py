"""PROTOTYPE: can rows linked on the general map vote the sample shift M in place of
the cost search's direct-evidence rows?

control - master's replay (refit_rt_models): fit without rows, take
          direct_evidence_rt_rows (cost search over direct owners) in that frame,
          refit with them. Must reproduce the captured drift model.
linked  - same frame and coordinate (membership_coordinate), same direct owners;
          rows are bands linked along injection order (0.4 min, +-8 injections)
          holding >= ROW_MIN_SAMPLES samples.
Then per-band drift linking is rerun with the linked model and compared.
"""

import pickle
import sys
import time
from collections import Counter
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from perband_drift import perband, rows_labels  # noqa: E402
from whole_batch import differ, inputs, labels, owners, rank  # noqa: E402

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES  # noqa: E402
from xic_extractor.alignment.grouping_workflow import direct_evidence_rt_rows  # noqa: E402
from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_drift import ROW_MIN_SAMPLES  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

HERE = Path(__file__).parent
AFTER_SHORT_METHOD = {
    "Breast_Cancer_Tissue_pooled_QC2", "Breast_Cancer_Tissue_pooled_QC3", "Breast_Cancer_Tissue_pooled_QC_4",
    "Breast_Cancer_Tissue_pooled_QC5", "Breast_Cancer_Tissue_pooled_QC6", "Breast_Cancer_Tissue_pooled_QC7",
    "TumorBC2312_DNA", "BenignfatBC1116_DNA",
}


def linked_rows(worksets, frame_of):
    rows = []
    for ws in worksets:
        frame = frame_of(ws)
        direct = [o for o in ws.owners if not CID_NL_GROUPING_RULES.method_unobserved(o)]
        placed = []
        for owner in direct:
            estimate = frame.membership_coordinate(owner)
            if estimate is not None:
                placed.append((owner, estimate.rt_min))
        if len(placed) < ROW_MIN_SAMPLES:
            continue
        placed.sort(key=lambda item: item[0].owner_id)
        peaks = [(rank[o.sample_stem], y, True, float(o.owner_id.split("|")[2])) for o, y in placed]
        for band in link_bands(peaks):
            row = {placed[i][0].sample_stem: placed[i][1] for i in band}
            if len(row) >= ROW_MIN_SAMPLES:
                rows.append(row)
    return rows


def compare_m(name, model, reference):
    a, b = model.sample_shift_min, reference.sample_shift_min
    common = sorted(set(a) & set(b), key=lambda s: rank.get(s, -1))
    diffs = sorted(((abs(a[s] - b[s]) * 60, s) for s in common), reverse=True)
    values = [d for d, _ in diffs]
    print(f"  {name}: M known {len(a)} vs {len(b)} samples; |diff| s median {median(values):.2f},"
          f" p95 {values[int(0.05 * (len(values) - 1))]:.2f}, max {values[0]:.2f}; > 1 s: {sum(v > 1 for v in values)}")
    for d, s in diffs[:5]:
        tag = " (#786)" if s in AFTER_SHORT_METHOD else ""
        print(f"     [{rank[s] + 1:2d}] {s[:30]:30s}{tag} {a[s] * 60:+6.1f} s vs {b[s] * 60:+6.1f} s")
    ca = {x.channel: x.coefficients for x in model.anchors}
    cb = {x.channel: x.coefficients for x in reference.anchors}
    shared = set(ca) & set(cb)
    worst = max((abs(ca[c].time - cb[c].time) + abs(ca[c].sample_shift - cb[c].sample_shift) for c in shared), default=0)
    print(f"  {name}: anchors {len(ca)} vs {len(cb)}, shared {len(shared)}, largest coefficient change {worst:.3f}")


if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    (captured,) = {id(ws.rt_drift): ws.rt_drift for ws in inputs.worksets if ws.rt_drift}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    calibration, preliminary = fit_batch_rt_model(model)
    base_frame = SampleRtFrame(injection_order=inputs.injection_order, sample_universe=frozenset(inputs.sample_stems))

    def frame_of(ws):
        return base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration, preliminary)

    cache = HERE / "m_vote_control_rows.pkl"
    if cache.exists():
        control_rows, control_seconds = pickle.loads(cache.read_bytes())
    else:
        start = time.perf_counter()
        control_rows = [
            row for ws in worksets
            for row in direct_evidence_rt_rows(ws.owners, ws.elution_contexts, config=inputs.alignment_config,
                                               sample_universe=frozenset(inputs.sample_stems), rt_frame=frame_of(ws))
        ]
        control_seconds = time.perf_counter() - start
        cache.write_bytes(pickle.dumps((control_rows, control_seconds)))
    start = time.perf_counter()
    rows = linked_rows(worksets, frame_of)
    linked_seconds = time.perf_counter() - start

    def perband_rows(drift):
        """Direct owners linked with per-band drift seeding on the same coordinate."""
        out = []
        for ws in worksets:
            frame = frame_of(ws)
            placed = sorted(
                ((o, e.rt_min) for o in ws.owners if not CID_NL_GROUPING_RULES.method_unobserved(o)
                 and (e := frame.membership_coordinate(o)) is not None),
                key=lambda item: item[0].owner_id,
            )
            if len(placed) < ROW_MIN_SAMPLES:
                continue
            ids = [o.owner_id for o, _ in placed]
            peaks = [(rank[o.sample_stem], y, True, float(o.owner_id.split("|")[2])) for o, y in placed]
            for band in perband(ws, ids, peaks, drift=drift)[0]:
                row = {placed[i][0].sample_stem: placed[i][1] for i in band}
                if len(row) >= ROW_MIN_SAMPLES:
                    out.append(row)
        return out

    start = time.perf_counter()
    pb_rows = perband_rows(preliminary)
    pb_seconds = time.perf_counter() - start
    print(f"rows: cost search {len(control_rows)} ({control_seconds:.0f} s), linked {len(rows)} ({linked_seconds:.1f} s),"
          f" per-band with the preliminary model {len(pb_rows)} ({pb_seconds:.1f} s)")

    _, control = fit_batch_rt_model(model, shift_rows=control_rows)
    _, linked = fit_batch_rt_model(model, shift_rows=rows)
    _, pb_model = fit_batch_rt_model(model, shift_rows=pb_rows)
    print()
    print("sample shift M:")
    compare_m("control vs captured", control, captured)
    compare_m("linked rows vs control", linked, control)
    compare_m("per-band rows vs control", pb_model, control)
    compare_m("no rows (anchors only) vs control", preliminary, control)
    base_m = preliminary.sample_shift_min
    for name, m in (("cost search", control), ("linked", linked), ("per-band", pb_model)):
        moved = sum(1 for s, v in m.sample_shift_min.items() if abs(v - base_m.get(s, v)) > 1e-12)
        print(f"  samples whose M moved off the anchors' M ({name} rows): {moved}/{len(m.sample_shift_min)}")

    def evaluate(name, drift):
        print()
        print(f"per-band drift linking with the {name} model vs the captured model:")
        for target in (348.2114, 302.1766, 334.0504):
            ws = max((w for w in inputs.worksets if w.key in owners and abs(float(w.key.split("|")[1]) - target) < 0.01),
                     key=lambda w: len(owners[w.key]))
            ids, peaks, _ = coordinates(ws, flatten=False)
            d = [i for i, p in enumerate(peaks) if p[2]]
            a = rows_labels(perband(ws, ids, peaks, drift=drift)[0], peaks)
            b = rows_labels(perband(ws, ids, peaks)[0], peaks)
            none = [labels["none"][ws.key][o] for o in ids]
            print(f"  {target:.2f}: placed differently between the two models {differ(d, [a[i] for i in d], [b[i] for i in d])};"
                  f" vs cost search: {name} model {differ(d, [a[i] for i in d], [none[i] for i in d])},"
                  f" captured model {differ(d, [b[i] for i in d], [none[i] for i in d])}")
        stats = Counter()
        for ws in inputs.worksets:
            if ws.key not in owners:
                continue
            ids, peaks, _ = coordinates(ws, flatten=False)
            d = [i for i, p in enumerate(peaks) if p[2]]
            if len(d) < 2:
                continue
            a = rows_labels(perband(ws, ids, peaks, drift=drift)[0], peaks)
            b = rows_labels(perband(ws, ids, peaks)[0], peaks)
            none = [labels["none"][ws.key][o] for o in ids]
            moved = differ(d, [a[i] for i in d], [b[i] for i in d])
            stats["windows"] += 1
            stats["direct"] += len(d)
            stats["changed"] += moved > 0
            stats["moved"] += moved
            stats["vs cost (model)"] += differ(d, [a[i] for i in d], [none[i] for i in d])
            stats["vs cost (captured)"] += differ(d, [b[i] for i in d], [none[i] for i in d])
        n = stats["direct"]
        print(f"  whole batch: windows changed {stats['changed']}/{stats['windows']}, direct moved {stats['moved']}"
              f" ({100 * stats['moved'] / n:.2f}%); vs cost search {stats['vs cost (model)']} ({100 * stats['vs cost (model)'] / n:.2f}%)"
              f" with the {name} model, {stats['vs cost (captured)']} ({100 * stats['vs cost (captured)'] / n:.2f}%) with the captured model")

    evaluate("linked-rows", linked)
    evaluate("per-band-rows", pb_model)
