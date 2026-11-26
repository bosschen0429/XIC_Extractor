"""PROTOTYPE: 348.21 rows under the M voted by per-band rows vs the captured M."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from flatten_explore import coordinates  # noqa: E402
from perband_drift import describe_rows, perband  # noqa: E402
from whole_batch import inputs, owners, rank  # noqa: E402

from xic_extractor.alignment.cid_nl_grouping import CID_NL_GROUPING_RULES  # noqa: E402
from xic_extractor.alignment.rt_anchors import fit_batch_rt_model  # noqa: E402
from xic_extractor.alignment.rt_drift import ROW_MIN_SAMPLES  # noqa: E402
from xic_extractor.alignment.rt_frame import SampleRtFrame  # noqa: E402

if __name__ == "__main__":
    (model,) = {id(ws.rt_model_inputs): ws.rt_model_inputs for ws in inputs.worksets if ws.rt_model_inputs}.values()
    worksets = [ws for ws in inputs.worksets if ws.rt_model_inputs is model]
    calibration, preliminary = fit_batch_rt_model(model)
    base_frame = SampleRtFrame(injection_order=inputs.injection_order, sample_universe=frozenset(inputs.sample_stems))
    pb_rows = []
    for ws in worksets:
        frame = base_frame.with_warp(ws.rt_warp).with_sample_calibration(calibration, preliminary)
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
    _, pb_model = fit_batch_rt_model(model, shift_rows=pb_rows)
    ws = next(w for w in inputs.worksets if abs(float(w.key.split("|")[1]) - 348.2114) < 0.001)
    ids, peaks, _ = coordinates(ws, flatten=False)
    print("348.21 rows, captured M:      ", describe_rows(peaks, perband(ws, ids, peaks)[0]))
    print("348.21 rows, per-band-rows M: ", describe_rows(peaks, perband(ws, ids, peaks, drift=pb_model)[0]))
