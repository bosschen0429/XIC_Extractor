"""Helpers shared by test_xic_inspector_html and other test modules."""

from __future__ import annotations

import json
import re
import shutil
import subprocess


def build_peak_comparison_payload() -> dict:
    """Measured peaks with unequal heights, tails and nonlinear RT projections."""
    curves = []
    for sid, raw, aligned, intensity in (
        (
            "A",
            [4, 4.5, 4.8, 5, 5.2, 5.7, 6],
            [9, 9.4, 9.8, 10, 10.3, 10.9, 11.2],
            [10, 15, 60, 100, 65, 20, 10],
        ),
        (
            "B",
            [6, 6.5, 6.8, 7, 7.4, 8, 8.4],
            [9.1, 9.6, 10, 10.2, 10.8, 11.7, 12.1],
            [30, 35, 95, 200, 110, 50, 30],
        ),
    ):
        curves.append(
            {
                "sample_id": sid,
                "raw_rts": raw,
                "aligned_rts": aligned,
                "raw_intensities": intensity,
                "processed_curves": [
                    {
                        "kind": "smoothed",
                        "rts": raw,
                        "aligned_rts": aligned,
                        "intensities": intensity,
                    }
                ],
                "apex_rt": raw[3],
                "bound_left_rt": raw[1],
                "bound_right_rt": raw[5],
                "aligned_apex_rt": aligned[3],
                "aligned_bound_left_rt": aligned[1],
                "aligned_bound_right_rt": aligned[5],
                "rt_alignment_methods": ["loess"],
                "rt_alignment_label": "校正 RT：loess",
            }
        )
    return {
        "summary": {"sample_rt_calibration": {"schema": "sample_rt_calibration"}},
        "samples": [{"id": sid, "label": sid} for sid in ("A", "B")],
        "items": [
            {
                "id": "feature",
                "kind": "group",
                "title": "Peak comparison",
                "samples": [
                    {"sample_id": "A", "quantitative_value": 123},
                    {"sample_id": "B", "quantitative_value": 456},
                ],
                "curves": curves,
                "review_rt_window": [4, 8.4],
            }
        ],
    }


def _evaluate_report_functions(report, payload, expression):
    """Exercise actual plot/evidence helpers before registering DOM interaction.

    The browser lane owns real DOM and lifecycle checks. This minimal surface
    keeps the independent numerical/meaning oracles fast without copying plots.
    """
    script = re.search(r"<script>(.*?)</script>", report, re.S)
    assert script is not None
    prefix = script[1].split(
        "  if (mode === 'offline') $('record-list').addEventListener", 1
    )[0]
    assert prefix != script[1]
    document = (
        "var document={createElement:()=>({dataset:{},"
        "querySelector:()=>({dataset:{strategy:'sample'}})})};"
        "var container={append(){}};"
    )
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the embedded JavaScript"
    metadata = re.search(r"metadata:(\{.*\})\}\);", script[1])
    assert metadata is not None
    js = (
        document
        + "console.log("
        + prefix
        + expression
        + "} return mount(container,"
        + json.dumps(payload, ensure_ascii=False)
        + ",{mode:'offline',metadata:"
        + metadata[1]
        + "});})());"
    )
    result = subprocess.run(
        [node],
        input=js,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


def build_origin_curve_payload() -> dict:
    kinds = ["detected", "backfilled", "unavailable"]
    labels = ["detect", "backfill", "來源未提供"]
    samples = [{"id": f"S{i}", "label": f"Sample {i}"} for i in range(3)]
    rows = [
        {
            "sample_id": f"S{i}",
            "quantitative_value": 100 + i if i < 2 else None,
            "support_origin": {
                "kind": kind,
                "label": labels[i],
                "capture_refs": [f"curve-{i}"],
            },
        }
        for i, kind in enumerate(kinds)
    ]
    curves = [
        {
            "sample_id": f"S{i}",
            "evidence_ref": f"curve-{i}",
            "raw_rts": [1.0, 1.1, 1.2],
            "raw_intensities": [0.0, 10.0 + i, 0.0],
            "apex_rt": 1.1,
            "processed_curves": [
                {"kind": "smoothed", "intensities": [0.0, 8.0 + i, 0.0]}
            ],
        }
        for i in range(3)
    ]
    return {
        "samples": samples,
        "items": [{"id": "group", "kind": "group", "samples": rows, "curves": curves}],
    }
