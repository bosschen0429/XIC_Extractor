"""Helpers shared by test_xic_inspector_html and other test modules."""

from __future__ import annotations

import json
import re
import shutil
import subprocess


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
    kinds = ["discovery_support", "backfill_support", "unavailable"]
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
