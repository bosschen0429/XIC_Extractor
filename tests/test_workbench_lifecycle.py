"""The desktop entry reopens work rather than leaving one server per launch."""

import threading
import time
from contextlib import contextmanager

from tests.workbench_lifecycle_fixtures import (
    _launch,
    _request,
)
from xic_workbench.browser_lifetime import BrowserLifetime
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


def test_repeated_desktop_launch_reuses_the_existing_workspace(tmp_path):
    with _launch(tmp_path / "config") as (first, first_url):
        with _launch(tmp_path / "config") as (second, second_url):
            assert second_url == first_url
            assert second.wait(timeout=5) == 0
            assert first.poll() is None
            assert _request(first_url, "/api/review")[0] == 200


def test_different_explicit_batches_do_not_reuse_the_wrong_workspace(tmp_path):
    with _launch(tmp_path / "config", "--raw-dir", str(tmp_path / "batch-a")) as (_, a):
        with _launch(tmp_path / "config", "--raw-dir", str(tmp_path / "batch-b")) as (
            _,
            b,
        ):
            assert a != b
            assert _request(a, "/api/session")[1]["defaults"]["raw_dir"].endswith(
                "batch-a"
            )
            assert _request(b, "/api/session")[1]["defaults"]["raw_dir"].endswith(
                "batch-b"
            )


def test_reload_grace_and_expired_tabs_allow_reconnection_before_exit():
    clock = [0.0]
    lifetime = BrowserLifetime(grace=20, stale_after=180, clock=lambda: clock[0])
    lifetime.update("old-page")
    lifetime.update("old-page", closed=True)
    clock[0] = 10
    assert not lifetime.should_exit()
    lifetime.update("reloaded-page")
    clock[0] = 200
    assert not lifetime.should_exit()  # Resume after sleep gets a grace period.
    lifetime.update("reloaded-page")
    clock[0] = 201
    assert not lifetime.should_exit()
    lifetime.update("reloaded-page", closed=True)
    clock[0] = 222
    assert lifetime.should_exit()


@contextmanager
def _timed_server(config):
    workspace = ReviewWorkspace(config_dir=config)
    server = create_server(
        workspace, browser_lifetime=BrowserLifetime(grace=0, startup_timeout=None)
    )
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.02), daemon=True
    )
    thread.start()
    try:
        yield workspace, server, thread, f"http://127.0.0.1:{server.server_port}/"
    finally:
        if thread.is_alive():
            server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        workspace.close()


def test_only_the_last_closed_tab_stops_an_idle_workspace(tmp_path):
    with _timed_server(tmp_path) as (workspace, server, thread, url):
        assert _request(url, "/api/window", {"window": "one"})[0] == 200
        assert _request(url, "/api/window", {"window": "two"})[0] == 200
        assert _request(url, "/api/window", {"window": "one", "closed": True})[0] == 200
        server.service_actions()
        assert not workspace.closing
        assert _request(url, "/api/review")[0] == 200
        assert _request(url, "/api/window", {"window": "two", "closed": True})[0] == 200
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_background_work_and_unseen_completion_survive_until_reopened(
    tmp_path, monkeypatch
):
    from xic_workbench import matrix_curation

    started, finish = threading.Event(), threading.Event()
    summary = tmp_path / "summary.json"

    def run_curation(config_dir, values):
        started.set()
        assert finish.wait(10)
        summary.write_text('{"fixture":true}')
        return {"output_dir": str(tmp_path), "summary_path": str(summary)}

    monkeypatch.setattr(matrix_curation, "run_curation", run_curation)
    with _timed_server(tmp_path / "config") as (workspace, server, thread, url):
        try:
            _request(url, "/api/window", {"window": "first"})
            status, _ = _request(
                url,
                "/api/analysis/start",
                {
                    "kind": "curation",
                    "values": {
                        "source_dir": str(tmp_path),
                        "steps": {"finite_fraction": {"min_finite_fraction": 0.5}},
                    },
                },
            )
            assert status == 202 and started.wait(5)
            _request(url, "/api/window", {"window": "first", "closed": True})
            server.service_actions()
            assert not workspace.closing and workspace.analysis.jobs.busy
            finish.set()
            deadline = time.monotonic() + 5
            while workspace.analysis.jobs.busy and time.monotonic() < deadline:
                time.sleep(0.005)
            assert not workspace.analysis.jobs.busy
            server.service_actions()
            assert not workspace.closing
            _request(url, "/api/window", {"window": "reopened"})
            status, result = _request(url, "/api/analysis")
            assert status == 200 and result["job"]["status"] == "succeeded"
            assert result["job"]["result"]["summary_path"] == str(summary)
            _request(url, "/api/window", {"window": "reopened", "closed": True})
            thread.join(timeout=5)
            assert not thread.is_alive()
        finally:
            finish.set()
