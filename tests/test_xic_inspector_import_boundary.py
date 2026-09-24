"""Inspector imports stay independent of the RAW I/O adapter."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module", ["xic_inspector.sources.cid_nl", "xic_inspector.api"]
)
def test_inspector_import_does_not_load_raw_reader(module: str) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib, sys; "
                f"importlib.import_module({module!r}); "
                "assert 'xic_extractor.raw_reader' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
