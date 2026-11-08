"""Run shipped JavaScript assets with a case-specific Node harness."""

import base64
import json
import shutil
import subprocess
from importlib.resources import files


def run_asset_case(asset, export_name, harness, case):
    node = shutil.which("node")
    assert node is not None, "Node.js is required to test the shipped JavaScript"
    source = files("xic_workbench").joinpath("assets", asset).read_bytes()
    module = "data:text/javascript;base64," + base64.b64encode(source).decode()
    result = subprocess.run(
        [node, "--input-type=module"],
        input=(
            f"import {{{export_name}}} from {json.dumps(module)};\n" + harness + case
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
