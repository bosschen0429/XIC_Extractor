"""PROTOTYPE: whole batch with drift-flattened coordinates on anchored channels."""

import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from band_tracking import link_bands  # noqa: E402
from flatten_explore import coordinates  # noqa: E402
from whole_batch import differ, inputs, labels, owners  # noqa: E402


def proto_labels(peaks):
    out = [None] * len(peaks)
    for r, band in enumerate(link_bands(peaks)):
        keep = len(band) > 1 and sum(peaks[i][2] for i in band) >= 2
        for i in band:
            out[i] = r if keep else f"single:{i}"
    return out


stats = defaultdict(Counter)
bins = defaultdict(lambda: [0, 0, 0, 0])
seconds = 0.0
for ws in inputs.worksets:
    if ws.key not in owners:
        continue
    ids, peaks, anchor = coordinates(ws, flatten=True)
    d = [i for i, p in enumerate(peaks) if p[2]]
    if len(d) < 2:
        continue
    start = time.perf_counter()
    pro = proto_labels(peaks)
    seconds += time.perf_counter() - start
    kind = "anchored" if anchor else "no anchor"
    for group in (kind, "all"):
        s = stats[group]
        s["windows"] += 1
        s["direct"] += len(d)
        for ref in ("none", "21"):
            moved = differ(d, [pro[i] for i in d], [labels[ref][ws.key][ids[i]] for i in d])
            s[f"identical vs {ref}"] += moved == 0
            s[f"differ vs {ref}"] += moved
    per_injection = Counter(p[0] for p in peaks)
    multi = sum(1 for n in per_injection.values() if n >= 2) / len(per_injection)
    label = "none" if multi == 0 else "<10%" if multi < 0.1 else "10-30%" if multi < 0.3 else ">=30%"
    moved = differ(d, [pro[i] for i in d], [labels["none"][ws.key][ids[i]] for i in d])
    b = bins[label]
    b[0] += 1
    b[1] += moved == 0
    b[2] += len(d)
    b[3] += moved
for group in ("all", "anchored", "no anchor"):
    s = stats[group]
    w, n = s["windows"], s["direct"]
    print(f"[{group}] windows {w}, direct {n}")
    for ref in ("none", "21"):
        print(f"   vs {ref:>4s}: identical {s[f'identical vs {ref}']}/{w} ({100 * s[f'identical vs {ref}'] / w:.0f}%), "
              f"placed differently {s[f'differ vs {ref}']}/{n} ({100 * s[f'differ vs {ref}'] / n:.1f}%)")
print("vs no band, by share of injections with 2+ peaks:")
for label in ("none", "<10%", "10-30%", ">=30%"):
    w, same, n, m = bins[label]
    print(f"   {label:>7s}: windows {w}, identical {same}/{w} ({100 * same / max(1, w):.0f}%), differ {m}/{n} ({100 * m / max(1, n):.1f}%)")
print(f"linking time, all windows with 2+ direct peaks: {seconds:.2f} s")
