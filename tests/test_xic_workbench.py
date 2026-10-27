"""The local HTTP entry preserves review scope, source files and prior results."""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import threading
import time
import weakref
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

import pytest

from tests.test_cid_nl_explanation import _summary_fixture
from tests.test_xic_inspector_api import _create_mock_db
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


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


def _review_batch(tmp_path, sample_count=2):
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

    sample_ids = tuple(f"S{i + 1}" for i in range(sample_count))
    matrix = tmp_path / "matrix.tsv"
    matrix.write_text("recorded matrix fixture", encoding="utf-8")
    report = tmp_path / "review.sqlite"
    with ExplanationReportWriter(report, review_only=True) as writer:
        for rid, route, values in (
            ("a-complete", "matrix", tuple(100 * (i + 1) for i in range(sample_count))),
            ("b-missing", "matrix", (300,) + ("unavailable",) * (sample_count - 1)),
            ("z-priority", "matrix", (400,) + ("unresolved",) * (sample_count - 1)),
            ("archive", "archive", (500,) + ("unresolved",) * (sample_count - 1)),
        ):
            keys = []
            for sid, value in zip(sample_ids, values, strict=True):
                if isinstance(value, int):
                    cid = f"{rid}-{sid}"
                    keys.append((cid, sid))
                    writer.add_candidate(
                        cid,
                        sid,
                        {
                            "candidate_id": cid,
                            "sample_key": sid,
                            "precursor_mz": value,
                            "rt_apex_min": 1,
                        },
                    )
            writer.add_record(
                "rows",
                rid,
                {
                    "row_key_id": rid,
                    "group_hypothesis_id": rid,
                    "route": {"destination": route, "reason": "recorded"},
                },
                keys,
            )
            writer.add_record("final_groups", rid, {"group_hypothesis_id": rid}, keys)
            writer.add_row_cells(
                rid,
                rid,
                [
                    {
                        "sample_key": sid,
                        "variant": "numeric" if isinstance(value, int) else value,
                        "value": value if isinstance(value, int) else None,
                    }
                    for sid, value in zip(sample_ids, values, strict=True)
                ],
                {sid: i for i, sid in enumerate(sample_ids)},
            )
        writer.add_candidate(
            "orphan",
            "S1",
            {
                "candidate_id": "orphan",
                "sample_key": "S1",
                "precursor_mz": 600,
                "rt_apex_min": 1,
            },
        )
        writer.finalize(
            _summary_fixture(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [
                        {"sample_key": s, "sample_stem": s} for s in sample_ids
                    ],
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def _nearest_review_batch(tmp_path):
    """Saved matrix rows whose review order differs from numeric proximity."""
    from xic_extractor.diagnostics.alignment_explanation import ExplanationReportWriter

    matrix = tmp_path / "nearest-matrix.tsv"
    matrix.write_text("saved near-search fixture", encoding="utf-8")
    report = tmp_path / "nearest-review.sqlite"
    rows = [(f"far-{i:02}", ((200.0 + i, 1.0),), False) for i in range(28)] + [
        ("near-a", ((123.3980, None),), False),
        ("near-b", ((123.4200, 5.0),), False),
        ("near-c", ((123.4567, 10.0),), True),
        ("missing-mz", ((None, 1.0),), False),
        ("multi-witness", ((350.0, 2.0), (123.4, 2.5)), False),
    ]
    with ExplanationReportWriter(report, review_only=True) as writer:
        for rid, witnesses, unresolved in rows:
            keys = []
            for index, (mz, rt) in enumerate(witnesses):
                cid = f"{rid}-{index}"
                keys.append((cid, "S1"))
                writer.add_candidate(
                    cid,
                    "S1",
                    {
                        "candidate_id": cid,
                        "sample_key": "S1",
                        "precursor_mz": mz,
                        "rt_apex_min": rt,
                    },
                )
            writer.add_record(
                "rows",
                rid,
                {
                    "row_key_id": rid,
                    "group_hypothesis_id": rid,
                    "route": {"destination": "matrix", "reason": "recorded"},
                },
                keys,
            )
            writer.add_record("final_groups", rid, {"group_hypothesis_id": rid}, keys)
            writer.add_row_cells(
                rid,
                rid,
                [
                    {
                        "sample_key": "S1",
                        "variant": "unresolved" if unresolved else "numeric",
                        "value": None if unresolved else 100.0,
                    }
                ],
                {"S1": 0},
            )
        writer.finalize(
            _summary_fixture(
                {
                    "matrix_path": str(matrix),
                    "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                    "samples": [{"sample_key": "S1", "sample_stem": "S1"}],
                    "selection_evidence_capability": "recorded_decisions",
                },
                review_only=writer.review_only,
            )
        )
    return report, matrix


def test_nearest_review_query_exposes_owner_ranking_and_preserves_exact(tmp_path):
    report, matrix = _nearest_review_batch(tmp_path)
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
    from xic_extractor.diagnostics import alignment_explanation
    from xic_inspector.sources.untargeted_overview import review_overview

    sample_count = 32
    report, _ = _review_batch(tmp_path, sample_count=sample_count)
    summary = alignment_explanation.read_explanation_metadata(report).summary
    live_cells = weakref.WeakValueDictionary()
    peak_cells = 0
    unpack = alignment_explanation.unpack_cells

    class ObservedCell(dict):
        pass

    def observe_cells(*args, **kwargs):
        nonlocal peak_cells
        # Run the real decoder; weak references observe retention, not its values.
        cells = {
            key: ObservedCell(value) for key, value in unpack(*args, **kwargs).items()
        }
        live_cells.update(cells)
        peak_cells = max(peak_cells, len(live_cells))
        return cells

    monkeypatch.setattr(alignment_explanation, "unpack_cells", observe_cells)
    overview, rows = review_overview(report, summary)

    assert overview["matrix_count"] == 3
    assert overview["complete_count"] == 1
    assert overview["unresolved_count"] == 1
    assert overview["archive_count"] == 1
    assert [sample["numeric_count"] for sample in overview["samples"]] == [3] + [1] * 31
    assert rows["z-priority"]["unresolved_count"] == 31
    # Only the current and next row may overlap while the iterator advances.
    assert peak_cells <= 2 * sample_count
    assert not live_cells


def test_review_overview_separates_matrix_priority_and_internal_evidence(tmp_path):
    report, matrix = _review_batch(tmp_path)
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


def test_http_reuses_connection_for_assets_and_status(tmp_path, monkeypatch):
    accepted = []
    accept = ThreadingHTTPServer.get_request

    def record_accept(server):
        connection, address = accept(server)
        accepted.append(address)
        return connection, address

    monkeypatch.setattr(ThreadingHTTPServer, "get_request", record_accept)
    with _client(ReviewWorkspace(config_dir=tmp_path)) as request:
        for _ in range(6):
            assert request("/assets/analysis.js")[0] == 200
            status, payload, _ = request("/api/analysis")
            assert status == 200
            assert payload["job"] is None
        assert len(accepted) == 1, "Page reads must reuse the session's TCP connection"


def test_http_closes_rejected_request_before_accepting_another(tmp_path):
    with _client(ReviewWorkspace(config_dir=tmp_path)) as request:
        status, _, headers = request(
            "/api/open", {"source": "."}, headers={"Cookie": ""}
        )
        assert status == 403
        assert headers.get("Connection", "").lower() == "close"
        assert request("/api/analysis")[0] == 200


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


def test_interactive_views_keep_original_sample_snapshots_and_export_lazily(
    tmp_path, monkeypatch
):
    from xic_inspector.api import PreparedReport
    from xic_inspector.rendering.html_builder import build_report_html

    report, _ = _review_batch(tmp_path)
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
    with _client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
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
        db, _, _ = _create_mock_db(other)
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


def test_view_endpoint_guards_unknown_ids_and_serves_only_packaged_assets(tmp_path):
    from importlib.resources import files

    db, _, _ = _create_mock_db(tmp_path)
    with _client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
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


def test_opened_results_download_original_bytes_and_switch_with_source(tmp_path):
    from urllib.parse import quote

    from tests.test_xic_inspector_targeted_workbook import write_rare_detection_workbook

    workbook = write_rare_detection_workbook(tmp_path / "分析結果.xlsx")
    report, matrix = _review_batch(tmp_path)
    with _client(ReviewWorkspace(config_dir=tmp_path / "config")) as request:
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
        assert "selected RAW is missing" in result["message"]
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
