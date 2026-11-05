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
from tests.workbench_browser_fixtures import (
    browser, browser_errors, config_dir, workbench, workbench_defaults,
)
pytest_plugins = ("tests.browser_diagnostics",)

def test_transport(workbench):
    workbench.route("**/diagnostic-probe*", lambda route: route.abort("failed"))
    with workbench.expect_event(
        "requestfailed", predicate=lambda request: "/diagnostic-probe" in request.url
    ):
        workbench.evaluate("fetch('/diagnostic-probe?token=private').catch(() => {})")

@pytest.fixture(params=["transport", "http"])
def broken_asset(request, browser, monkeypatch):
    new_context = browser.new_context
    def context(**kwargs):
        result = new_context(**kwargs)
        def fail(route):
            if request.param == "transport":
                route.abort("failed")
            else:
                route.fulfill(status=503, body="asset unavailable")
        result.route("**/assets/analysis.css", fail)
        return result
    monkeypatch.setattr(browser, "new_context", context)

def test_asset(broken_asset, workbench):
    assert workbench.title()

def test_console(workbench):
    workbench.evaluate("console.error('diagnostic console probe')")

def test_exception(workbench):
    with workbench.expect_event("pageerror"):
        workbench.evaluate(
            "setTimeout(() => { throw new Error('diagnostic exception probe'); }, 0)"
        )

def test_success(workbench):
    assert workbench.title()

def test_inspector_exception(review_page):
    with review_page.expect_event("pageerror"):
        review_page.evaluate(
            "setTimeout(() => { throw new Error('inspector exception probe'); }, 0)"
        )

def test_both_contexts(workbench, review_page):
    assert workbench.title()
    with review_page.expect_event("pageerror"):
        review_page.evaluate(
            "setTimeout(() => { throw new Error('offline exception probe'); }, 0)"
        )

def test_inspector_console_is_diagnostic(review_page):
    review_page.evaluate("console.error('inspector console remains diagnostic')")

def test_both_success(workbench, review_page):
    assert workbench.title()
    review_page.set_content("<h1>Offline success</h1>")
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
    assert "10 passed, 7 errors" in result.stdout, result.stdout + result.stderr
    traces = sorted(artifacts.rglob("browser-trace.zip"))
    assert len(traces) == 8, result.stdout + result.stderr
    records = {}
    for trace in traces:
        # A readable Playwright archive needs actual trace and network entries.
        with zipfile.ZipFile(trace) as archive:
            assert archive.testzip() is None
            assert any(name.endswith(".trace") for name in archive.namelist())
            assert any(name.endswith(".network") for name in archive.namelist())
    reports = list(artifacts.rglob("browser-events.json"))
    assert len(reports) == 7
    for path in reports:
        report = json.loads(path.read_text("utf-8"))
        assert report["outcomes"] == {
            "setup": "passed",
            "call": "passed",
            "teardown": "failed",
        }
        records[report["test"].rsplit("::", 1)[-1]] = report["events"]
        if report["test"].endswith("::test_both_contexts"):
            assert (path.parent / "browser-trace.zip").is_file()
            assert (path.parent / "inspector" / "browser-trace.zip").is_file()
    failed = next(e for e in records["test_transport"] if e["kind"] == "requestfailed")
    assert failed["phase"] == "call"
    assert failed["method"] == "GET"
    assert failed["resource_type"] == "fetch"
    assert failed["failure"] == "net::ERR_FAILED"
    assert failed["url"].endswith("/diagnostic-probe")
    assert "private" not in json.dumps(records)
    asset_events = records["test_asset[transport]"]
    assert any(
        e["kind"] == "requestfailed"
        and e["phase"] == "setup"
        and e["resource_type"] == "stylesheet"
        and e["url"].endswith("/assets/analysis.css")
        for e in asset_events
    )
    assert any(
        e["kind"] == "console" and "503" in e["text"]
        for e in records["test_asset[http]"]
    )
    assert any(e["kind"] == "console" for e in records["test_console"])
    assert any(e["kind"] == "pageerror" for e in records["test_exception"])
    for name in ("test_inspector_exception", "test_both_contexts"):
        assert any(
            e["kind"] == "pageerror" and e["context"] == "inspector"
            for e in records[name]
        )
