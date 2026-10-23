"""Exercise the real browser fixture through pytest's complete report lifecycle."""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser_e2e


def test_teardown_errors_keep_browser_evidence_after_tmp_path_cleanup(tmp_path):
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        """
import pytest
from tests.test_workbench_browser import browser, browser_errors, config_dir, workbench
pytest_plugins = ("tests.browser_diagnostics",)

def test_transport(workbench):
    workbench.route("**/diagnostic-probe*", lambda route: route.abort("failed"))
    with workbench.expect_event(
        "requestfailed", predicate=lambda request: "/diagnostic-probe" in request.url
    ):
        workbench.evaluate("fetch('/diagnostic-probe?token=private').catch(() => {})")

def test_console(workbench):
    workbench.evaluate("console.error('diagnostic console probe')")

def test_exception(workbench):
    with workbench.expect_event("pageerror"):
        workbench.evaluate(
            "setTimeout(() => { throw new Error('diagnostic exception probe'); }, 0)"
        )

def test_success(workbench):
    assert workbench.title()
""",
        encoding="utf-8",
    )
    artifacts = tmp_path / "evidence"
    env = {**os.environ, "XIC_BROWSER_ARTIFACT_DIR": str(artifacts)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q", "--tb=short"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "4 passed, 3 errors" in result.stdout, result.stdout + result.stderr
    traces = sorted(artifacts.rglob("browser-trace.zip"))
    assert len(traces) == 3, result.stdout + result.stderr
    records = {}
    for trace in traces:
        # A readable Playwright archive needs actual trace and network entries.
        with zipfile.ZipFile(trace) as archive:
            assert archive.testzip() is None
            assert any(name.endswith(".trace") for name in archive.namelist())
            assert any(name.endswith(".network") for name in archive.namelist())
        report = json.loads(trace.with_name("browser-events.json").read_text("utf-8"))
        assert report["outcomes"] == {
            "setup": "passed",
            "call": "passed",
            "teardown": "failed",
        }
        records[report["test"].rsplit("::", 1)[-1]] = report["events"]
    failed = next(e for e in records["test_transport"] if e["kind"] == "requestfailed")
    assert failed["phase"] == "call"
    assert failed["method"] == "GET"
    assert failed["resource_type"] == "fetch"
    assert failed["failure"] == "net::ERR_FAILED"
    assert failed["url"].endswith("/diagnostic-probe")
    assert "private" not in json.dumps(records)
    assert any(e["kind"] == "console" for e in records["test_console"])
    assert any(e["kind"] == "pageerror" for e in records["test_exception"])
