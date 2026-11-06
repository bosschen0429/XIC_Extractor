"""The local HTTP entry preserves review scope, source files and prior results."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from tests.inspector_fixtures import (
    create_inspector_report,
    create_nearest_review_batch,
    create_review_batch,
)
from tests.workbench_http import workbench_client
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


def test_workbench_startup_skips_heavy_branch_imports(tmp_path):
    """These packages dominate cold start; the first page load does not need them."""
    script = (
        "import sys; from pathlib import Path; import xic_workbench.cli; "
        "from xic_workbench.workspace import ReviewWorkspace; "
        f"workspace = ReviewWorkspace(config_dir=Path({str(tmp_path)!r})); "
        "workspace.analysis.bootstrap(); workspace.analysis.state(); "
        "workspace.state(); workspace.close(); "
        "loaded = {'scipy', 'openpyxl'} & set(sys.modules); "
        "assert not loaded, loaded"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_http_accepts_page_asset_burst_before_dispatch(tmp_path):
    """A page's parallel asset connections can arrive before the accept loop runs."""
    workspace = ReviewWorkspace(config_dir=tmp_path)
    server = create_server(workspace)
    connections = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        # Chromium opens up to six HTTP/1 connections to a page's origin. Hold
        # dispatch until the burst has arrived, rather than racing the scheduler.
        for _ in range(6):
            connections.append(
                socket.create_connection(server.server_address, timeout=1)
            )
        thread.start()
        for connection in connections:
            connection.sendall(
                f"GET /assets/analysis.js HTTP/1.0\r\n"
                f"Host: 127.0.0.1:{server.server_port}\r\n\r\n".encode()
            )
            response = http.client.HTTPResponse(connection)
            response.begin()
            assert response.status == 200
            assert b"use strict" in response.read()
    finally:
        for connection in connections:
            connection.close()
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        server.server_close()
        workspace.close()


def test_nearest_review_query_exposes_owner_ranking_and_preserves_exact(tmp_path):
    report, matrix = create_nearest_review_batch(tmp_path)
    before = [path.read_bytes() for path in (report, matrix)]
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    try:
        workspace.open(str(report))
        exact = workspace.query({"mz_min": 123.4, "mz_max": 123.4})
        assert [item["group_id"] for item in exact["items"]] == ["multi-witness"]
        assert "nearest_mz" not in exact
        assert "mz_distance" not in exact["items"][0]

        page = workspace.query({"nearest_mz": 123.4, "limit": 25})
        assert page["matched_count"] == 32
        assert page["selected_count"] == 25
        assert page["nearest_mz"] == 123.4
        assert page["missing_mz_count"] == 1
        assert page["rt_min"] is None and page["rt_max"] is None
        assert [item["group_id"] for item in page["items"][:3]] == [
            "near-a",
            "near-b",
            "near-c",
        ]
        assert [item["mz_distance"] for item in page["items"][:3]] == pytest.approx(
            [0.002, 0.02, 0.0567]
        )
        tail = workspace.query({"nearest_mz": 123.4, "offset": 25, "limit": 25})
        all_ids = [item["group_id"] for item in page["items"] + tail["items"]]
        assert all_ids == ["near-a", "near-b", "near-c"] + [
            f"far-{i:02}" for i in range(28)
        ] + ["multi-witness"]

        scoped = workspace.query(
            {"nearest_mz": 123.4, "review_scope": "unresolved", "rt_min": 9}
        )
        assert [item["group_id"] for item in scoped["items"]] == ["near-c"]
        assert scoped["rt_min"] == 9
        assert workspace.state()["page"]["nearest_mz"] == 123.4
        restored_exact = workspace.query(
            {"review_scope": "matrix", "mz_min": 123.4, "mz_max": 123.4}
        )
        assert restored_exact["items"] == exact["items"]
        assert "nearest_mz" not in restored_exact
    finally:
        workspace.close()
    assert [path.read_bytes() for path in (report, matrix)] == before


def test_review_overview_does_not_retain_decoded_cells_for_the_population(
    tmp_path, monkeypatch
):
    from xic_extractor.alignment_explanation import reader as alignment_explanation
    from xic_inspector.sources.untargeted_overview import review_overview

    sample_count = 32
    report, _ = create_review_batch(tmp_path, sample_count=sample_count)
    summary = alignment_explanation.read_explanation_metadata(report).summary

    def forbidden(*args, **kwargs):
        raise AssertionError("overview decoded cold scientific details")

    monkeypatch.setattr(alignment_explanation, "decode_row_evidence", forbidden)
    overview, rows = review_overview(report, summary)

    assert overview["matrix_count"] == 3
    assert overview["complete_count"] == 1
    assert overview["unresolved_count"] == 1
    assert overview["archive_count"] == 1
    assert [sample["numeric_count"] for sample in overview["samples"]] == [3] + [1] * 31
    assert rows["z-priority"]["unresolved_count"] == 31


def test_review_overview_separates_matrix_priority_and_internal_evidence(tmp_path):
    report, matrix = create_review_batch(tmp_path)
    before = [p.read_bytes() for p in (report, matrix)]
    workspace = ReviewWorkspace(config_dir=tmp_path / "config")
    try:
        page = workspace.open(str(report))
        overview = page["overview"]
        assert (
            overview["matrix_count"],
            overview["unresolved_count"],
            overview["complete_count"],
            overview["archive_count"],
            overview["candidate_count"],
        ) == (3, 1, 1, 1, 1)
        assert [s["numeric_count"] for s in overview["samples"]] == [3, 1]
        assert [i["group_id"] for i in page["items"]] == [
            "z-priority",
            "a-complete",
            "b-missing",
        ]
        page = workspace.query({"limit": 1, "offset": 1})
        assert page["items"][0]["group_id"] == "a-complete"
        for scope, expected in (
            ("matrix", ["z-priority", "a-complete", "b-missing"]),
            ("unresolved", ["z-priority"]),
            ("archive", ["archive"]),
            ("candidates", ["orphan"]),
        ):
            identifiers = []
            for offset in range(len(expected) + 1):
                page = workspace.query(
                    {"review_scope": scope, "offset": offset, "limit": 1}
                )
                assert page["overview"] == overview
                assert page["matched_count"] == len(expected)
                identifiers.extend(
                    item["candidate_id"] if scope == "candidates" else item["group_id"]
                    for item in page["items"]
                )
            assert identifiers == expected
        page = workspace.query({"review_scope": "unresolved", "offset": 0})
        assert page["matched_count"] == 1
        assert page["items"][0]["unresolved_count"] == 1
        assert (
            workspace.query({"review_scope": "matrix", "mz_min": 300, "mz_max": 300})[
                "items"
            ][0]["group_id"]
            == "b-missing"
        )
        assert (
            workspace.query({"review_scope": "archive"})["items"][0]["group_id"]
            == "archive"
        )
        assert (
            workspace.query({"review_scope": "candidates"})["items"][0]["candidate_id"]
            == "orphan"
        )
    finally:
        workspace.close()
    assert [p.read_bytes() for p in (report, matrix)] == before


def test_example_downloads_are_fixed_public_resources_not_local_files(tmp_path):
    from xic_extractor.configuration.paths import targets_template_path

    config = tmp_path / "config"
    config.mkdir()
    private = config / "workspace.json"
    private.write_text("private-local-state", encoding="utf-8")
    with workbench_client(ReviewWorkspace(config_dir=config)) as request:
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


@pytest.mark.parametrize(
    ("failure_type", "successful_writes", "application_failure"),
    (
        pytest.param(ConnectionAbortedError, 0, False, id="headers-aborted"),
        pytest.param(ConnectionAbortedError, 1, False, id="body-aborted"),
        pytest.param(ConnectionResetError, 1, False, id="body-reset"),
        pytest.param(BrokenPipeError, 1, False, id="body-broken-pipe"),
        pytest.param(ConnectionAbortedError, 0, True, id="error-response-aborted"),
    ),
)
def test_disconnected_response_does_not_send_an_error_or_acknowledge_completion(
    tmp_path, monkeypatch, failure_type, successful_writes, application_failure
):
    workspace = ReviewWorkspace(config_dir=tmp_path)
    acknowledgements = []
    writes = []
    server_errors = []

    def state():
        if application_failure:
            raise ValueError("invalid selection")
        return {"job": {"status": "failed", "job_id": "a" * 32}}

    monkeypatch.setattr(workspace.analysis, "state", state)
    monkeypatch.setattr(
        workspace.analysis.jobs, "acknowledge_completion", acknowledgements.append
    )

    def controlled_server(*args, **kwargs):
        server = create_server(*args, **kwargs)
        handler_type = server.RequestHandlerClass
        setup = handler_type.setup

        def setup_writer(handler):
            setup(handler)
            stream = handler.wfile

            class DisconnectingWriter:
                def write(self, data):
                    if handler.path == "/api/analysis":
                        writes.append(data)
                        if len(writes) > successful_writes:
                            raise failure_type("peer disconnected")
                    return stream.write(data)

                def __getattr__(self, name):
                    return getattr(stream, name)

            handler.wfile = DisconnectingWriter()

        monkeypatch.setattr(handler_type, "setup", setup_writer)
        monkeypatch.setattr(
            server, "handle_error", lambda *_: server_errors.append(sys.exception())
        )
        return server

    monkeypatch.setattr("xic_workbench.server.create_server", controlled_server)
    with workbench_client(workspace) as request:
        with pytest.raises(
            (http.client.RemoteDisconnected, http.client.IncompleteRead)
        ):
            request("/api/analysis")

    assert len(writes) == successful_writes + 1
    assert not server_errors
    assert not acknowledgements, (
        "An undelivered completion must remain available to poll"
    )


@pytest.mark.parametrize("failure_type", (OSError, ConnectionAbortedError))
def test_application_io_failure_is_still_reported_to_a_connected_client(
    tmp_path, monkeypatch, failure_type
):
    workspace = ReviewWorkspace(config_dir=tmp_path)

    def unavailable():
        raise failure_type("application input unavailable")

    monkeypatch.setattr(workspace.analysis, "state", unavailable)
    with workbench_client(workspace) as request:
        status, payload, _headers = request("/api/analysis")

    assert status == 400
    assert payload == {"error": "application input unavailable"}


def test_http_reuses_connection_for_assets_and_status(tmp_path, monkeypatch):
    accepted = []
    accept = ThreadingHTTPServer.get_request

    def record_accept(server):
        connection, address = accept(server)
        accepted.append(address)
        return connection, address

    monkeypatch.setattr(ThreadingHTTPServer, "get_request", record_accept)
    with workbench_client(ReviewWorkspace(config_dir=tmp_path)) as request:
        for _ in range(6):
            assert request("/assets/analysis.js")[0] == 200
            status, payload, _ = request("/api/analysis")
            assert status == 200
            assert payload["job"] is None
        assert len(accepted) == 1, "Page reads must reuse the session's TCP connection"


def test_http_closes_rejected_request_before_accepting_another(tmp_path):
    with workbench_client(ReviewWorkspace(config_dir=tmp_path)) as request:
        status, _, headers = request(
            "/api/open", {"source": "."}, headers={"Cookie": ""}
        )
        assert status == 403
        assert headers.get("Connection", "").lower() == "close"
        assert request("/api/analysis")[0] == 200


def test_http_query_detail_export_and_invalid_open_preserve_source(tmp_path):
    db, matrix, _ = create_inspector_report(tmp_path)
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (db, matrix)]
    with workbench_client(ReviewWorkspace()) as request:
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


def test_http_open_explains_unreadable_report_in_plain_language(tmp_path):
    report = tmp_path / "alignment_explanation.sqlite"
    with sqlite3.connect(report) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with workbench_client(ReviewWorkspace()) as request:
        status, body, _ = request("/api/open", {"source": str(report)})
    assert status == 400
    assert body["error"].startswith("無法讀取這份結果")
    assert "重新執行 Alignment" in body["error"]
    assert body["detail"]


def test_interactive_views_keep_original_sample_snapshots_and_export_lazily(
    tmp_path, monkeypatch
):
    from xic_inspector.api import PreparedReport
    from xic_inspector.rendering.html_builder import build_report_html

    report, _ = create_review_batch(tmp_path)
    rendered = []
    to_html = PreparedReport.to_html

    def record_render(prepared):
        rendered.append(prepared.payload)
        return to_html(prepared)

    monkeypatch.setattr(PreparedReport, "to_html", record_render)

    def record_snapshot_render(**data):
        rendered.append(data["payload_data"])
        return build_report_html(**data)

    monkeypatch.setattr(
        "xic_workbench.workspace.build_report_html", record_snapshot_render
    )
    with workbench_client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
        assert request("/api/open", {"source": str(report)})[0] == 200
        views = []
        for sample_id, expected_value in (("S1", 100), ("S2", 200)):
            _, page, _ = request(
                "/api/query", {"group_id": "a-complete", "sample_id": sample_id}
            )
            _, detail, _ = request("/api/detail", {"item_id": page["items"][0]["id"]})
            assert rendered == [], "Interactive review must not render offline HTML"
            status, payload, _ = request(f"/api/views/{detail['view_id']}")
            assert status == 200
            assert payload["samples"] == [{"id": sample_id, "label": sample_id}]
            assert (
                payload["items"][0]["samples"][0]["quantitative_value"]
                == expected_value
            )
            views.append((detail["view_id"], payload))

        other = tmp_path / "other"
        other.mkdir()
        db, _, _ = create_inspector_report(other)
        assert request("/api/open", {"source": str(db)})[0] == 200
        report.unlink()

        def unexpected_prepare(*args, **kwargs):
            raise AssertionError("View reads and exports must not reopen the source")

        monkeypatch.setattr(
            "xic_workbench.workspace.prepare_report", unexpected_prepare
        )
        for index, (view_id, original) in enumerate(views, 1):
            assert request(f"/api/views/{view_id}")[1] == original
            status, html, headers = request(f"/reports/{view_id}.html?download=1")
            assert status == 200
            assert headers["Content-Disposition"] == (
                f'attachment; filename="xic-review-{view_id[:8]}.html"'
            )
            embedded = re.search(
                rb'<script id="inspector-data" type="application/json">(.*?)</script>',
                html,
                re.S,
            )
            assert json.loads(embedded[1]) == original
            assert len(rendered) == index
            assert request(f"/reports/{view_id}.html?download=1")[1] == html
            assert len(rendered) == index


def test_analysis_module_graph_is_served_with_javascript_mime(tmp_path):
    from importlib.resources import files

    with workbench_client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
        status, html, _ = request("/")
        assert status == 200
        assert re.search(rb'<script\s+type="module"\s+src="/assets/analysis.js"', html)
        pending = {"analysis.js"}
        visited = set()
        while pending:
            name = pending.pop()
            visited.add(name)
            status, data, headers = request(f"/assets/{name}")
            assert status == 200, name
            assert headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert data == files("xic_workbench").joinpath("assets", name).read_bytes()
            imports = set(re.findall(rb'from "\./([^"/]+\.js)"', data))
            pending.update(
                item.decode() for item in imports if item.decode() not in visited
            )
        # A misspelled entry/import must not make a smaller graph pass silently.
        shipped_modules = {
            asset.name
            for asset in files("xic_workbench").joinpath("assets").iterdir()
            if asset.name.startswith("analysis") and asset.name.endswith(".js")
        }
        assert visited == shipped_modules
        assert request("/assets/../server.py")[0] == 404


def test_view_endpoint_guards_unknown_ids_and_serves_only_packaged_assets(tmp_path):
    from importlib.resources import files

    db, _, _ = create_inspector_report(tmp_path)
    with workbench_client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
        _, page, _ = request("/api/open", {"source": str(db)})
        _, detail, _ = request("/api/detail", {"item_id": page["items"][0]["id"]})
        url = f"/api/views/{detail['view_id']}"
        for headers in (
            {"Origin": "https://unrelated.example"},
            {"Host": "unrelated.example"},
            {"Cookie": ""},
            {"X-XIC-Client": ""},
        ):
            assert request(url, headers=headers)[0] == 403
        assert request(f"/api/views/{'0' * 32}")[0] == 400
        assert request("/api/views/not-a-view")[0] == 404
        assert request("/api/views/../../pyproject.toml")[0] == 404
        for name, content_type in (
            ("inspector.js", "text/javascript; charset=utf-8"),
            ("delta_mass.js", "text/javascript; charset=utf-8"),
            ("inspector.css", "text/css; charset=utf-8"),
        ):
            status, data, headers = request(f"/assets/{name}")
            assert status == 200
            assert headers["Content-Type"] == content_type
            assert (
                data
                == files("xic_inspector.rendering")
                .joinpath("assets", name)
                .read_bytes()
            )
        assert request("/assets/../api.py")[0] == 404


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
    with workbench_client(ReviewWorkspace()) as request:
        assert request("/api/open", {"source": "."}, headers=headers)[0] == 403


def test_pick_returns_the_native_choice_and_starts_from_the_field(
    tmp_path, monkeypatch
):
    from xic_workbench import native_picker, server

    calls = []

    def choose(kind, current=""):
        calls.append((kind, current))
        return "" if kind == "folder" else str(tmp_path / "chosen.sqlite")

    monkeypatch.setattr(server, "choose_path", choose)
    with workbench_client(ReviewWorkspace()) as request:
        status, body, _ = request(
            "/api/pick", {"kind": "report", "path": str(tmp_path)}
        )
        assert (status, body) == (200, {"path": str(tmp_path / "chosen.sqlite")})
        # Cancelling leaves the field as it was.
        assert request("/api/pick", {"kind": "folder"})[1] == {"path": ""}
        assert request("/api/pick", {"kind": "folder", "extra": 1})[0] == 400

        def busy(kind, current=""):
            raise native_picker.PickerBusy("已有開啟中的選擇視窗。")

        monkeypatch.setattr(server, "choose_path", busy)
        assert request("/api/pick", {"kind": "folder"})[0] == 409
    assert calls == [("report", str(tmp_path)), ("folder", "")]

    with pytest.raises(ValueError):
        native_picker.choose_path("unknown")
    nested = tmp_path / "batch"
    nested.mkdir()
    # A typed path that no longer exists starts at its nearest existing parent.
    assert native_picker._starting_folder(str(nested / "gone" / "x.raw")) == str(nested)
    assert native_picker._starting_folder("  ") == ""


def test_opened_results_download_original_bytes_and_switch_with_source(tmp_path):
    from urllib.parse import quote

    from tests.xic_inspector_targeted_workbook_fixtures import (
        write_rare_detection_workbook,
    )

    workbook = write_rare_detection_workbook(tmp_path / "分析結果.xlsx")
    report, matrix = create_review_batch(tmp_path)
    with workbench_client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
        urls = []
        for source, product in ((workbook, workbook), (report, matrix)):
            status, page, _ = request("/api/open", {"source": str(source)})
            assert status == 200
            artifact = page["result_artifact"]
            urls.append(artifact["url"])
            assert artifact["name"] == product.name
            status, downloaded, headers = request(artifact["url"])
            assert status == 200
            assert downloaded == product.read_bytes()
            assert headers["Content-Disposition"] == (
                f"attachment; filename*=UTF-8''{quote(product.name)}"
            )
            assert request("/api/query", {"limit": 1})[1]["result_artifact"] == artifact
            assert request("/api/review")[1]["page"]["result_artifact"] == artifact
        assert urls[0] != urls[1]
        assert request("/files/" + "0" * 32)[0] == 400
        assert request("/files/../../workspace.json")[0] == 404


def test_http_cancel_and_failure_leave_prior_report_readable(tmp_path, monkeypatch):
    from xic_workbench import acquisition

    db, _, _ = create_inspector_report(tmp_path)
    entered = threading.Event()

    def blocked_export(*args, is_cancelled, **kwargs):
        entered.set()
        deadline = time.monotonic() + 5
        while not is_cancelled() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise acquisition.CurveAcquisitionCancelled()

    monkeypatch.setattr(acquisition, "export_explanation_curves", blocked_export)
    with workbench_client(ReviewWorkspace()) as request:
        _, page, _ = request("/api/open", {"source": str(db)})
        item_id = page["items"][0]["id"]
        _, detail, _ = request("/api/detail", {"item_id": item_id})
        report_url = f"/reports/{detail['view_id']}.html"
        view_url = f"/api/views/{detail['view_id']}"
        original_payload = request(view_url)[1]
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
        assert request(view_url)[1] == original_payload

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
        assert result["message"] == "讀取 RAW 時發生錯誤。"
        assert "selected RAW is missing" in result["detail"]
        assert request(report_url)[1] == original
        assert request(view_url)[1] == original_payload

        monkeypatch.setattr(
            acquisition, "export_explanation_curves", lambda *a, **k: {}
        )

        from xic_inspector.api import SelectionRequest, prepare_report

        prepared_fixture = prepare_report(db, SelectionRequest(sample_ids=("S1",)))

        def render_fixture(source, selection, **kwargs):
            return prepared_fixture

        def unexpected_html(prepared):
            raise AssertionError("Interactive curves must not render offline HTML")

        monkeypatch.setattr(type(prepared_fixture), "to_html", unexpected_html)
        monkeypatch.setattr("xic_workbench.workspace.prepare_report", render_fixture)
        _, job, _ = request("/api/curves", parameters)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _, restored, _ = request("/api/review")
            if restored["job"]["status"] == "succeeded":
                break
            time.sleep(0.01)
        assert restored["job"]["status"] == "succeeded"
        assert restored["detail"] == detail
        view_id = restored["job"]["view_id"]
        _, payload, _ = request(f"/api/views/{view_id}")
        assert payload == json.loads(json.dumps(prepared_fixture.payload))
        assert request(f"/reports/{view_id}.html")[0] == 200

        monkeypatch.setattr("xic_workbench.workspace.prepare_report", prepare_report)
        request("/api/query", {})
        _, restored, _ = request("/api/review")
        assert restored["job"] is None
        assert restored["detail"] is None


def test_two_servers_keep_independent_sessions_with_shared_browser_cookies(tmp_path):
    from http.cookies import SimpleCookie

    with (
        workbench_client(ReviewWorkspace(config_dir=tmp_path / "a")) as a,
        workbench_client(ReviewWorkspace(config_dir=tmp_path / "b")) as b,
    ):
        jar = SimpleCookie()
        for request in (a, b, a):
            _, _, headers = request("/api/session")
            jar.load(headers["Set-Cookie"])
            cookie = "; ".join(f"{key}={value.value}" for key, value in jar.items())
            assert a("/api/analysis", headers={"Cookie": cookie})[0] == 200
            if request is b or len(jar) > 1:
                assert b("/api/analysis", headers={"Cookie": cookie})[0] == 200
