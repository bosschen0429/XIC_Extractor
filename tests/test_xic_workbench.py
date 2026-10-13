"""The local HTTP entry preserves review scope, source files and prior results."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
import time
from contextlib import contextmanager

import pytest

from tests.test_xic_inspector_api import _create_mock_db
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


def test_example_downloads_are_fixed_public_resources_not_local_files(tmp_path):
    from xic_extractor.configuration.paths import targets_template_path

    config = tmp_path / "config"
    config.mkdir()
    private = config / "workspace.json"
    private.write_text("private-local-state", encoding="utf-8")
    with _client(ReviewWorkspace(config_dir=config)) as request:
        for name in ("targeted-method.xlsx", "targets.xlsx"):
            status, payload, headers = request(f"/examples/{name}")
            assert status == 200
            assert headers["Content-Disposition"] == f'attachment; filename="{name}"'
            assert headers["Content-Type"] == (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert payload == (targets_template_path().parent / name).read_bytes()
        for path in ("workspace.json", "../config/workspace.json", "targets.csv"):
            assert request(f"/examples/{path}")[0] == 404
        assert (
            request(
                "/examples/targets.xlsx", headers={"Origin": "https://other.example"}
            )[0]
            == 403
        )
    assert private.read_text(encoding="utf-8") == "private-local-state"


@contextmanager
def _client(workspace):
    server = create_server(workspace, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=10)
    connection.request("GET", "/api/session")
    response = connection.getresponse()
    cookie = response.getheader("Set-Cookie").split(";", 1)[0]
    response.read()

    def request(path, values=None, *, headers=None):
        body = json.dumps(values).encode() if values is not None else None
        actual_headers = {"Cookie": cookie, "X-XIC-Client": "workbench"}
        if body is not None:
            actual_headers["Content-Type"] = "application/json"
        actual_headers.update(headers or {})
        connection.request(
            "POST" if body is not None else "GET", path, body, actual_headers
        )
        result = connection.getresponse()
        data = result.read()
        if result.getheader("Content-Type", "").startswith("application/json"):
            data = json.loads(data)
        return result.status, data, dict(result.getheaders())

    try:
        yield request
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        workspace.close()


def test_http_query_detail_export_and_invalid_open_preserve_source(tmp_path):
    db, matrix, _ = _create_mock_db(tmp_path)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (db, matrix)]
    with _client(ReviewWorkspace()) as request:
        status, page, _ = request("/api/open", {"source": str(tmp_path)})
        assert status == 200
        assert (page["run_total"], page["matched_count"], page["selected_count"]) == (
            1,
            1,
            1,
        )
        item = page["items"][0]
        assert item["group_id"] == "G1"
        assert item["observed_sample_ids"] == ["S1"]
        status, empty, _ = request("/api/query", {"limit": 1, "offset": 1})
        assert status == 200
        assert (
            empty["run_total"],
            empty["matched_count"],
            empty["selected_count"],
        ) == (1, 1, 0)
        _, unmatched, _ = request("/api/query", {"mz_min": 501})
        assert (unmatched["run_total"], unmatched["matched_count"]) == (1, 0)
        assert request("/api/query", {"limit": -1})[0] == 400
        assert (
            request("/api/open", {"source": str(tmp_path / "missing.sqlite")})[0] == 400
        )
        _, page, _ = request("/api/query", {"group_id": "G1"})
        assert page["items"][0]["id"] == item["id"]
        _, detail, _ = request("/api/detail", {"item_id": item["id"]})
        status, html, headers = request(f"/reports/{detail['view_id']}.html?download=1")
        assert status == 200
        assert "attachment" in headers["Content-Disposition"]
        embedded = re.search(
            rb'<script id="inspector-data" type="application/json">(.*?)</script>',
            html,
            re.S,
        )
        payload = json.loads(embedded[1])
        assert payload["items"][0]["samples"][0]["quantitative_value"] == 1000.0
        assert b"__WORKBENCH_JS__" not in html
        assert b"<script src=" not in html
        assert (
            request(
                "/api/curves",
                {
                    "item_id": item["id"],
                    "sample_ids": ["S2"],
                    "raw_dir": str(tmp_path),
                    "dll_dir": str(tmp_path),
                },
            )[0]
            == 400
        )
        assert request("/reports/../../pyproject.toml")[0] == 404
    assert [
        hashlib.sha256(path.read_bytes()).hexdigest() for path in (db, matrix)
    ] == before


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://unrelated.example"},
        {"Host": "unrelated.example"},
        {"Cookie": ""},
        {"X-XIC-Client": ""},
    ],
)
@pytest.mark.parametrize("body_delay", [0, 0.05])
def test_http_rejects_cross_origin_or_unidentified_api_calls(
    headers, body_delay, monkeypatch
):
    send = http.client.HTTPConnection.send

    def delayed_body(connection, data):
        if isinstance(data, bytes) and data.startswith(b"{"):
            time.sleep(body_delay)
        return send(connection, data)

    monkeypatch.setattr(http.client.HTTPConnection, "send", delayed_body)
    with _client(ReviewWorkspace()) as request:
        assert request("/api/open", {"source": "."}, headers=headers)[0] == 403


def test_browse_uses_current_report_folder_without_exposing_other_files(tmp_path):
    report = tmp_path / "cid_nl_explanation.sqlite"
    report.touch()
    (tmp_path / "sample.raw").touch()
    (tmp_path / "subfolder").mkdir()
    with _client(ReviewWorkspace()) as request:
        status, listing, _ = request(
            "/api/browse", {"path": str(report), "kind": "report"}
        )
        assert status == 200
        assert listing["path"] == str(tmp_path.resolve())
        assert [
            (entry["name"], entry["directory"]) for entry in listing["entries"]
        ] == [("subfolder", True), ("cid_nl_explanation.sqlite", False)]
        assert request("/sample.raw")[0] == 404


def test_http_cancel_and_failure_leave_prior_report_readable(tmp_path, monkeypatch):
    from xic_workbench import acquisition

    db, _, _ = _create_mock_db(tmp_path)
    entered = threading.Event()

    def blocked_export(*args, is_cancelled, **kwargs):
        entered.set()
        deadline = time.monotonic() + 5
        while not is_cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise acquisition.CurveAcquisitionCancelled()

    monkeypatch.setattr(acquisition, "export_explanation_curves", blocked_export)
    with _client(ReviewWorkspace()) as request:
        _, page, _ = request("/api/open", {"source": str(db)})
        item_id = page["items"][0]["id"]
        _, detail, _ = request("/api/detail", {"item_id": item_id})
        report_url = f"/reports/{detail['view_id']}.html"
        original = request(report_url)[1]
        parameters = {
            "item_id": item_id,
            "sample_ids": ["S1"],
            "raw_dir": str(tmp_path),
            "dll_dir": str(tmp_path),
        }
        status, job, _ = request("/api/curves", parameters)
        assert status == 202
        assert entered.wait(timeout=5)
        assert request("/api/curves", parameters)[0] == 409
        assert request("/api/query", {})[0] == 409
        code, restored, _ = request("/api/review")
        assert code == 200
        assert restored["job"]["id"] == job["id"]
        assert restored["page"]["source"] == str(db.resolve())
        assert restored["detail"] == detail
        request(f"/api/jobs/{restored['job']['id']}/cancel", {})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, result, _ = request(f"/api/jobs/{job['id']}")
            if result["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert result["status"] == "cancelled"
        assert "view_id" not in result
        assert request(report_url)[1] == original

        def failed_export(*args, **kwargs):
            raise FileNotFoundError("selected RAW is missing")

        monkeypatch.setattr(acquisition, "export_explanation_curves", failed_export)
        _, job, _ = request("/api/curves", parameters)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, result, _ = request(f"/api/jobs/{job['id']}")
            if result["status"] == "failed":
                break
            time.sleep(0.01)
        assert result["status"] == "failed"
        assert "selected RAW is missing" in result["message"]
        assert request(report_url)[1] == original

        monkeypatch.setattr(
            acquisition, "export_explanation_curves", lambda *a, **k: {}
        )

        def render_fixture(source, selection, path, **kwargs):
            path.write_text("<html>diagnostic fixture</html>", encoding="utf-8")

        monkeypatch.setattr("xic_workbench.workspace.generate_report", render_fixture)
        _, job, _ = request("/api/curves", parameters)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, restored, _ = request("/api/review")
            if restored["job"]["status"] == "succeeded":
                break
            time.sleep(0.01)
        assert restored["job"]["status"] == "succeeded"
        assert restored["detail"] == detail
        url = f"/reports/{restored['job']['view_id']}.html"
        assert request(url)[1] == b"<html>diagnostic fixture</html>"
        request("/api/query", {})
        _, restored, _ = request("/api/review")
        assert restored["job"] is None
        assert restored["detail"] is None


def test_two_servers_keep_independent_sessions_with_shared_browser_cookies(tmp_path):
    from http.cookies import SimpleCookie

    with (
        _client(ReviewWorkspace(config_dir=tmp_path / "a")) as a,
        _client(ReviewWorkspace(config_dir=tmp_path / "b")) as b,
    ):
        jar = SimpleCookie()
        for request in (a, b, a):
            _, _, headers = request("/api/session")
            jar.load(headers["Set-Cookie"])
            cookie = "; ".join(f"{key}={value.value}" for key, value in jar.items())
            assert a("/api/analysis", headers={"Cookie": cookie})[0] == 200
            if request is b or len(jar) > 1:
                assert b("/api/analysis", headers={"Cookie": cookie})[0] == 200
