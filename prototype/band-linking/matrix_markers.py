"""Do injections 3 and 67 carry a different hydrolysis matrix than their neighbours?

Markers from the 4-enzyme hydrolysis (Tris buffers about 35 mM, sodium acetate, EDTA, HCl):
Tris [M+H]+ 122.0812 and EDTA [M+H]+ 293.0983 over the whole run, MS1 TIC in the first
3 min (salt front) and over the run, and the number of MS2 scans.
Read before running: injections 3 and 67 standing out from their neighbours (outside
0.67x-1.5x of the neighbours' median) supports a different sample matrix; looking like
their neighbours weakens that hypothesis. Low MS2 counts would explain missing direct peaks
without any chromatographic cause.
"""

import json
import sys
from pathlib import Path
from statistics import median

import numpy as np

from xic_extractor.raw_reader import open_raw

RAW_DIR = Path(r"C:\Xcalibur\data\20260106_CSMU_NAA_Tissue_R")
DLL = Path(r"C:\Users\user\AppData\Local\XIC_Extractor\RawFileReader-8.0.42")
MARKERS = {"Tris": 122.0812, "EDTA": 293.0983}
order = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
stems = sorted(order, key=order.get)
WANT = list(range(1, 8)) + list(range(63, 72))


def measure(stem):
    out = {}
    with open_raw(RAW_DIR / f"{stem}.raw", DLL) as raw:
        for name, mz in MARKERS.items():
            rt, inten = raw.extract_xic(mz, 0.0, 60.0, 10.0)
            rt, inten = np.asarray(rt, float), np.asarray(inten, float)
            out[name] = float(np.trapezoid(inten, rt)) if rt.size > 1 else 0.0
            out[f"{name} apex"] = float(rt[int(np.argmax(inten))]) if rt.size else float("nan")
        rf = raw._raw_file
        header = rf.RunHeaderEx
        tic = early = 0.0
        ms2 = 0
        for s in range(int(header.FirstSpectrum), int(header.LastSpectrum) + 1):
            f = rf.GetFilterForScanNumber(s)
            if str(f.MSOrder.ToString()) == "Ms":
                value = float(rf.GetScanStatsForScanNumber(s).TIC)
                tic += value
                if float(rf.RetentionTimeFromScanNumber(s)) < 3.0:
                    early += value
            else:
                ms2 += 1
        out["TIC < 3 min"], out["TIC"], out["MS2 scans"] = early, tic, ms2
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    rows = {x: measure(stems[x - 1]) for x in WANT}
    keys = ["Tris", "EDTA", "TIC < 3 min", "TIC", "MS2 scans"]
    print("ratio to the median of injections within +-3 (excluding the injection itself); apex RT in min")
    print(f"{'inj':>4s} {'sample':30s} " + " ".join(f"{k:>11s}" for k in keys) + "  Tris apex")
    for x in WANT:
        near = [rows[j] for j in range(x - 3, x + 4) if j != x and j in rows]
        cells = []
        for k in keys:
            ref = median(r[k] for r in near) if near else float("nan")
            cells.append(f"{rows[x][k] / ref:11.2f}" if ref else f"{'--':>11s}")
        mark = "  <==" if x in (3, 67) else ""
        print(f"{x:4d} {stems[x - 1][:30]:30s} " + " ".join(cells) + f"  {rows[x]['Tris apex']:6.2f}{mark}")
    print("\nabsolute values:")
    for x in WANT:
        r = rows[x]
        print(f"{x:4d} Tris {r['Tris']:.3g}, EDTA {r['EDTA']:.3g}, TIC<3 {r['TIC < 3 min']:.3g}, TIC {r['TIC']:.3g}, MS2 {r['MS2 scans']}")
