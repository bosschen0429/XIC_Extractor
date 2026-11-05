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
