"""Loopback-only transport for one local XIC review workspace."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
import socket
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from xic_extractor.alignment_explanation.storage import (
    ExplanationError,
)
from xic_extractor.configuration.models import ConfigError
from xic_extractor.presets import PresetError
from xic_inspector.api import InspectorError, read_evidence_summary, resolve_source
from xic_workbench.analysis_workspace import json_value
from xic_workbench.browser_lifetime import BrowserLifetime
from xic_workbench.native_picker import PickerBusy, choose_path
from xic_workbench.workspace import ReviewWorkspace, WorkspaceBusy

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
    "/assets/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
    "/assets/analysis.html": ("analysis.html", "text/html; charset=utf-8"),
    "/assets/analysis.js": ("analysis.js", "text/javascript; charset=utf-8"),
    "/assets/analysis_state.js": (
        "analysis_state.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_ui.js": ("analysis_ui.js", "text/javascript; charset=utf-8"),
    "/assets/analysis_setup.js": (
        "analysis_setup.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_targeted.js": (
        "analysis_targeted.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_untargeted.js": (
        "analysis_untargeted.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_samples.js": (
        "analysis_samples.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_jobs.js": ("analysis_jobs.js", "text/javascript; charset=utf-8"),
    "/assets/analysis_results.js": (
        "analysis_results.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_curation.js": (
        "analysis_curation.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis_reader.js": (
        "analysis_reader.js",
        "text/javascript; charset=utf-8",
    ),
    "/assets/analysis.css": ("analysis.css", "text/css; charset=utf-8"),
}
_EXAMPLES = {"/examples/targeted-method.xlsx", "/examples/targets.xlsx"}
_INSPECTOR_ASSETS = {
    "/assets/inspector.js": ("inspector.js", "text/javascript; charset=utf-8"),
    "/assets/inspector.css": ("inspector.css", "text/css; charset=utf-8"),
}


class _WorkbenchHTTPServer(ThreadingHTTPServer):
    # Browsers open parallel connections for the page's assets. The Python 3.12
    # default backlog of five can reject that burst before the accept loop runs.
    request_queue_size = socket.SOMAXCONN

    browser_lifetime: BrowserLifetime | None = None
    workspace: ReviewWorkspace

    def service_actions(self) -> None:
        if (
            self.browser_lifetime is not None
            and self.browser_lifetime.should_exit()
            and self.workspace.request_shutdown(preserve_unseen_completion=True)
        ):
            threading.Thread(target=self.shutdown, daemon=True).start()


class _ResponseDisconnected(Exception):
    """The peer disconnected while this handler was writing its response."""


def create_server(
    workspace: ReviewWorkspace,
    *,
    port: int = 0,
    desktop_key: str | None = None,
    browser_lifetime: BrowserLifetime | None = None,
) -> ThreadingHTTPServer:
    """Serve only packaged frontend files and reports produced by this session."""
    session = secrets.token_urlsafe(32)
    cookie_name = f"xic_session_{secrets.token_hex(8)}"

    class Handler(BaseHTTPRequestHandler):
        server: ThreadingHTTPServer
        # Every response has a Content-Length. Reuse browser connections instead
        # of allocating a new Windows socket for every asset and status poll.
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            logging.getLogger(__name__).info(format, *args)

        def _send(
            self,
            status,
            data,
            content_type="application/json; charset=utf-8",
            **headers,
        ):
            if isinstance(data, dict):
                data = json.dumps(
                    json_value(data), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
            # Rejections may leave a request body unread. GET bodies and chunked
            # transfer are not consumed by this transport either; never interpret
            # their remaining bytes as a new request on a persistent connection.
            if (
                status >= 400
                or self.headers.get("Transfer-Encoding")
                or (self.command == "GET" and self.headers.get("Content-Length"))
            ):
                self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "same-origin")
            if self.close_connection:
                self.send_header("Connection", "close")
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            try:
                self.end_headers()
                self.wfile.write(data)
            except (
                BrokenPipeError,
                ConnectionResetError,
                ConnectionAbortedError,
            ) as exc:
                # Unwind the action: an undelivered job completion must not be
                # acknowledged, and an error response must not be sent twice.
                raise _ResponseDisconnected() from exc

        def _guard(self, *, api=False):
            authority = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != authority:
                raise PermissionError("工作區僅接受本機入口。")
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{authority}":
                raise PermissionError("工作區不接受其他網站的要求。")
            if api:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookie.get(cookie_name)
                if (
                    token is None
                    or not secrets.compare_digest(token.value, session)
                    or self.headers.get("X-XIC-Client") != "workbench"
                ):
                    raise PermissionError("請重新載入工作區頁面。")

        def _handle(self, action):
            try:
                try:
                    action()
                except _ResponseDisconnected:
                    raise
                except PermissionError as exc:
                    self._send(403, {"error": str(exc)})
                except (WorkspaceBusy, PickerBusy) as exc:
                    self._send(409, {"error": str(exc)})
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    InspectorError,
                    ExplanationError,
                    ConfigError,
                    PresetError,
                ) as exc:
                    payload = {"error": str(exc)}
                    if exc.__cause__ is not None:
                        payload["detail"] = str(exc.__cause__)
                    self._send(400, payload)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Local workspace request failed"
                    )
                    self._send(
                        500, {"error": "工作區無法完成要求；請查看啟動終端的錯誤訊息。"}
                    )
            except _ResponseDisconnected:
                self.close_connection = True

        def do_GET(self):
            self._handle(self._get)

        def _get(self):
            url = urlsplit(self.path)
            self._guard(api=url.path.startswith("/api/") and url.path != "/api/session")
            if url.path in _ASSETS:
                asset, content_type = _ASSETS[url.path]
                data = files("xic_workbench").joinpath("assets", asset).read_bytes()
                self._send(200, data, content_type)
            elif url.path in _INSPECTOR_ASSETS:
                asset, content_type = _INSPECTOR_ASSETS[url.path]
                data = (
                    files("xic_inspector.rendering")
                    .joinpath("assets", asset)
                    .read_bytes()
                )
                self._send(200, data, content_type)
            elif url.path in _EXAMPLES:
                # Fixed public resources only; no arbitrary filesystem access.
                name = url.path.rsplit("/", 1)[-1]
                resource = files("xic_extractor.configuration").joinpath("data", name)
                self._send(
                    200,
                    resource.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    Content_Disposition=f'attachment; filename="{name}"',
                )
            elif url.path == "/api/session":
                self._send(
                    200,
                    {
                        "defaults": workspace.defaults,
                        "desktop_key": None if workspace.closing else desktop_key,
                    },
                    Set_Cookie=(
                        f"{cookie_name}={session}; Path=/; HttpOnly; SameSite=Strict"
                    ),
                )
            elif match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})", url.path):
                self._send(200, workspace.job(match[1]))
            elif match := re.fullmatch(r"/api/views/([a-f0-9]{32})", url.path):
                self._send(200, workspace.view_payload(match[1]))
            elif url.path == "/api/review":
                self._send(200, workspace.state())
            elif url.path == "/api/config":
                self._send(200, workspace.analysis.bootstrap())
            elif url.path == "/api/analysis":
                state = workspace.analysis.state()
                self._send(200, state)
                job = state.get("job")
                if job and job["status"] not in {"running", "cancelling"}:
                    workspace.analysis.jobs.acknowledge_completion(job["job_id"])
            elif match := re.fullmatch(r"/files/([a-f0-9]{32})", url.path):
                path = workspace.analysis.artifact_path(match[1])
                data = path.read_bytes()
                content_type = (
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                )
                headers = {}
                if path.suffix.lower() != ".html" or parse_qs(url.query).get(
                    "download"
                ) == ["1"]:
                    from urllib.parse import quote

                    headers["Content_Disposition"] = (
                        f"attachment; filename*=UTF-8''{quote(path.name)}"
                    )
                self._send(200, data, content_type, **headers)
            elif match := re.fullmatch(r"/reports/([a-f0-9]{32})\.html", url.path):
                data = workspace.report_path(match[1]).read_bytes()
                params = parse_qs(url.query)
                headers = {}
                if params.get("download") == ["1"]:
                    headers["Content_Disposition"] = (
                        f'attachment; filename="xic-review-{match[1][:8]}.html"'
                    )
                self._send(200, data, "text/html; charset=utf-8", **headers)
            else:
                self._send(404, {"error": "找不到此頁面。"})

        def do_POST(self):
            self._handle(self._post)

        def _post(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("要求大小超過限制。")
            # Consume the bounded body before rejecting headers: closing while
            # the client is still sending can reset the connection on Windows.
            body = self.rfile.read(length)
            self._guard(api=True)
            if self.headers.get_content_type() != "application/json":
                raise ValueError("請以 JSON 傳送要求。")
            values = json.loads(body)
            if not isinstance(values, dict):
                raise ValueError("要求必須是 JSON 物件。")
            path = urlsplit(self.path).path
            if path == "/api/window":
                window, closed = values.get("window"), values.get("closed", False)
                if (
                    set(values) - {"window", "closed"}
                    or not isinstance(window, str)
                    or not 1 <= len(window) <= 80
                    or not isinstance(closed, bool)
                ):
                    raise ValueError("請提供瀏覽分頁識別。")
                if browser_lifetime is not None:
                    browser_lifetime.update(window, closed=closed)
                self._send(200, {"ok": True})
                return
            elif path == "/api/open":
                if set(values) != {"source"}:
                    raise ValueError("請提供結果位置。")
                result = workspace.open(values["source"])
            elif path == "/api/query":
                result = workspace.query(values)
            elif path == "/api/detail":
                if set(values) != {"item_id"}:
                    raise ValueError("請選擇一個檢閱項目。")
                result = workspace.detail(values["item_id"])
            elif path == "/api/curves":
                self._send(202, workspace.start_curves(values))
                return
            elif path == "/api/pick":
                kind, current = values.get("kind"), values.get("path", "")
                if (
                    set(values) - {"path", "kind"}
                    or not isinstance(kind, str)
                    or not isinstance(current, str)
                ):
                    raise ValueError("請選擇資料夾或檔案類型。")
                result = {"path": choose_path(kind, current)}
            elif path == "/api/samples/preview":
                from xic_workbench.samples import read_batch_samples

                result = read_batch_samples(
                    workspace.analysis.config_dir,
                    Path(values["raw_dir"]),
                    dll_dir=None,
                    sequence_source=Path(values["sequence_source"])
                    if values.get("sequence_source")
                    else None,
                    read_headers=False,
                )
            elif path == "/api/matrix-curation/preview":
                from xic_workbench.matrix_curation import preview_curation

                # Read-only computation runs on this HTTP request thread, without
                # holding the mutable workspace lock or blocking status/queries.
                result = preview_curation(workspace.analysis.config_dir, values)
            elif path == "/api/analysis/start":
                if not isinstance(values.get("values"), dict):
                    raise ValueError("請提供分析設定。")
                self._send(
                    202, workspace.start_analysis(values["kind"], values["values"])
                )
                return
            elif path == "/api/analysis/cancel":
                result = workspace.analysis.jobs.cancel(values["job_id"])
            elif path == "/api/evidence-summary":
                result = read_evidence_summary(resolve_source(Path(values["source"])))
            elif path == "/api/shutdown":
                if not workspace.request_shutdown():
                    raise WorkspaceBusy("請先停止或等待目前工作完成。")
                self.close_connection = True
                self._send(200, {"message": "工作區已結束。"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif path.removeprefix("/api/") in {
                "preferences",
                "paths/validate",
                "targeted/save",
                "targeted/method",
                "targeted/method/save",
                "targeted/method/load",
                "targeted/method/validate",
                "targets/import",
                "targets/upload",
                "targets/export",
                "untargeted/save",
                "method",
                "method/save",
                "samples/save",
                "samples/annotations",
                "samples/exclude",
                "samples/export",
                "matrix-curation/source",
                "istd",
                "istd/upload",
                "istd/copy",
                "open-local",
                "thermo-reader/install",
                "thermo-reader/use-installed",
                "thermo-reader/status",
            }:
                result = workspace.analysis_action(path.removeprefix("/api/"), values)
            elif match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", path):
                result = workspace.cancel(match[1])
            else:
                self._send(404, {"error": "找不到此操作。"})
                return
            self._send(200, result)

    server = _WorkbenchHTTPServer(("127.0.0.1", port), Handler)
    server.workspace = workspace
    server.browser_lifetime = browser_lifetime
    server.daemon_threads = True
    return server
