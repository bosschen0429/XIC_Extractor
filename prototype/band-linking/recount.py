"""PROTOTYPE recount with honest denominators: windows with 2+ direct peaks, with and
without the m/z tolerance in linking, against both the no-band and the band-21 rows."""

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from whole_batch import base, differ, inputs, labels, owners, rank  # noqa: E402


def span_ppm(values):
    return (max(values) - min(values)) / min(values) * 1e6 if len(values) > 1 else 0.0


def proto_labels(peaks, tolerance):
    out = [None] * len(peaks)
    for r, band in enumerate(link_bands(peaks, mz_tolerance_ppm=tolerance)):
        keep = sum(peaks[i][2] for i in band) >= 2
        for i in band:
            out[i] = r if keep and len(band) > 1 else f"single:{i}"
    return out


stats = defaultdict(Counter)
split_dir = Counter()
split_peaks = Counter()
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    frame = base.with_warp(ws.rt_warp)
    if ws.rt_calibration is not None:
        frame = frame.with_sample_calibration(ws.rt_calibration, ws.rt_drift)
    ids = sorted(owners[ws.key])
    peaks = []
    for o in ids:
        f = o.split("|")
        raw = float(f[3])
        cal = frame.calibrated_rt_min(owners[ws.key][o], raw)
        peaks.append((rank[owners[ws.key][o]], raw if cal is None else cal, f[1] == "dda_cid_nl", float(f[2])))
    d = [i for i, p in enumerate(peaks) if p[2]]
    if len(d) < 2:
        continue
    for variant, tol in (("no m/z limit", None), ("10 ppm limit", 10.0)):
        pro = proto_labels(peaks, tol)
        rows = defaultdict(list)
        for i, x in enumerate(pro):
            rows[x].append(i)
        over = sum(1 for m in rows.values() if len(m) > 1 and span_ppm([peaks[i][3] for i in m]) > 10.0 + 1e-9)
        s = stats[variant]
        s["windows"] += 1
        s["direct"] += len(d)
        s["rows over 10 ppm"] += over
        for ref in ("none", "21"):
            ref_lab = [labels[ref][ws.key][ids[i]] for i in d]
            moved = differ(d, [pro[i] for i in d], ref_lab)
            s[f"identical vs {ref}"] += moved == 0
            s[f"differ vs {ref}"] += moved
        if tol is not None and max(Counter(p[0] for p in peaks).values()) == 1:
            ref_lab = [labels["none"][ws.key][ids[i]] for i in d]
            p_lab = [pro[i] for i in d]
            n_ref = len({x for x in ref_lab if not str(x).startswith("single")})
            n_pro = len({x for x in p_lab if not str(x).startswith("single")})
            if differ(d, p_lab, ref_lab):
                kind = "no-band splits more" if n_ref > n_pro else "prototype splits more" if n_pro > n_ref else "same row count"
                split_dir[kind] += 1
                if kind == "no-band splits more":
                    main = Counter(x for x in ref_lab if not str(x).startswith("single")).most_common(1)
                    split_peaks["outside main row"] += len(d) - (main[0][1] if main else 0)
for variant, s in stats.items():
    w, n = s["windows"], s["direct"]
    print(f"[{variant}] windows with 2+ direct: {w}; prototype rows over 10 ppm: {s['rows over 10 ppm']}")
    for ref in ("none", "21"):
        print(f"   vs {ref:>4s}: identical windows {s[f'identical vs {ref}']}/{w} ({100 * s[f'identical vs {ref}'] / w:.0f}%), "
              f"direct placed differently {s[f'differ vs {ref}']}/{n} ({100 * s[f'differ vs {ref}'] / n:.1f}%)")
print("no co-occurrence windows that disagree with no-band (10 ppm limit):", dict(split_dir), dict(split_peaks))
