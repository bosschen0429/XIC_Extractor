"""Loopback-only transport for one local XIC review workspace."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import string
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from itertools import islice
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from xic_extractor.configuration.models import ConfigError
from xic_extractor.diagnostics.alignment_explanation import ExplanationError
from xic_extractor.presets import PresetError
from xic_inspector.api import InspectorError, read_evidence_summary, resolve_source
from xic_workbench.analysis_workspace import json_value
from xic_workbench.workspace import ReviewWorkspace, WorkspaceBusy

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/workspace.js": ("workspace.js", "text/javascript; charset=utf-8"),
    "/assets/workspace.css": ("workspace.css", "text/css; charset=utf-8"),
    "/assets/analysis.html": ("analysis.html", "text/html; charset=utf-8"),
    "/assets/analysis.js": ("analysis.js", "text/javascript; charset=utf-8"),
    "/assets/analysis.css": ("analysis.css", "text/css; charset=utf-8"),
}
_BROWSE_SUFFIXES = {
    "folder": (),
    "report": (".sqlite", ".db"),
    "raw": (".raw",),
    "csv": (".csv",),
    "targets": (".csv", ".tsv", ".xlsx", ".xls"),
    "metadata": (".docx", ".xlsx", ".csv", ".tsv"),
    "preset": (".toml",),
    "file": (".csv", ".tsv", ".xlsx", ".json", ".toml", ".txt"),
}
# The workspace sizes the embedded report to its content and publishes the
# visible part of the frame as --host-top/--host-height for dialogs.
_EMBED_STYLE = """<style>
html,body{background:transparent}
body{max-width:none;padding:0}
body>header,.list-panel{display:none}.workbench{display:block}
.detail-panel{border:0;box-shadow:none;border-radius:0;background:transparent}
dialog[open]{position:fixed;inset:auto 0;margin:0 auto;
top:calc(var(--host-top,0px) + var(--host-height,100vh)*.05);
max-height:calc(var(--host-height,100vh)*.9)}
.zoom-container{height:calc(var(--host-height,100vh)*.7)}
</style>"""


def _browse(values: dict) -> dict:
    if set(values) - {"path", "kind"} or values.get("kind") not in _BROWSE_SUFFIXES:
        raise ValueError("請選擇資料夾或結果檔案。")
    value = values.get("path", "")
    if not isinstance(value, str):
        raise ValueError("路徑必須是文字。")
    if not value.strip():
        roots = (
            [Path(f"{drive}:/") for drive in string.ascii_uppercase]
            if os.name == "nt"
            else [Path("/")]
        )
        return {
            "path": "",
            "parent": None,
            "truncated": False,
            "entries": [
                {"name": str(root), "path": str(root), "directory": True}
                for root in roots
                if root.is_dir()
            ],
        }
    folder = Path(value).expanduser().resolve()
    if values["kind"] != "folder" and folder.is_file():
        folder = folder.parent
    if not folder.is_dir():
        raise ValueError("此資料夾不存在或無法讀取。")
    entries = []
    with os.scandir(folder) as children:
        for child in children:
            if child.is_dir() or (
                values["kind"] != "folder"
                and child.name.lower().endswith(_BROWSE_SUFFIXES[values["kind"]])
            ):
                entries.append(
                    {
                        "name": child.name,
                        "path": child.path,
                        "directory": child.is_dir(),
                    }
                )
                if len(entries) > 1000:
                    break
    return {
        "path": str(folder),
        "parent": str(folder.parent) if folder.parent != folder else "",
        "truncated": len(entries) > 1000,
        "entries": sorted(
            islice(entries, 1000),
            key=lambda entry: (not entry["directory"], entry["name"].casefold()),
        ),
    }


def create_server(workspace: ReviewWorkspace, *, port: int = 0) -> ThreadingHTTPServer:
    """Serve only packaged frontend files and reports produced by this session."""
    session = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
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
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "same-origin")
            for key, value in headers.items():
                self.send_header(key.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, *, api=False):
            authority = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != authority:
                raise PermissionError("工作區僅接受本機入口。")
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{authority}":
                raise PermissionError("工作區不接受其他網站的要求。")
            if api:
                cookie = SimpleCookie(self.headers.get("Cookie", ""))
                token = cookie.get("xic_session")
                if (
                    token is None
                    or not secrets.compare_digest(token.value, session)
                    or self.headers.get("X-XIC-Client") != "workbench"
                ):
                    raise PermissionError("請重新載入工作區頁面。")

        def _handle(self, action):
            try:
                action()
            except PermissionError as exc:
                self._send(403, {"error": str(exc)})
            except WorkspaceBusy as exc:
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
                sqlite3.Error,
            ) as exc:
                self._send(400, {"error": str(exc)})
            except Exception:
                logging.getLogger(__name__).exception("Local workspace request failed")
                self._send(
                    500, {"error": "工作區無法完成要求；請查看啟動終端的錯誤訊息。"}
                )

        def do_GET(self):
            self._handle(self._get)

        def _get(self):
            url = urlsplit(self.path)
            self._guard(api=url.path.startswith("/api/") and url.path != "/api/session")
            if url.path in _ASSETS:
                asset, content_type = _ASSETS[url.path]
                data = files("xic_workbench").joinpath("assets", asset).read_bytes()
                self._send(200, data, content_type)
            elif url.path == "/api/session":
                self._send(
                    200,
                    {"defaults": workspace.defaults},
                    Set_Cookie=(
                        f"xic_session={session}; Path=/; HttpOnly; SameSite=Strict"
                    ),
                )
            elif match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})", url.path):
                self._send(200, workspace.job(match[1]))
            elif url.path == "/api/config":
                self._send(200, workspace.analysis.bootstrap())
            elif url.path == "/api/analysis":
                self._send(200, workspace.analysis.state())
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
                elif params.get("embed") == ["1"]:
                    data = data.replace(
                        b"</head>", _EMBED_STYLE.encode() + b"</head>", 1
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
            if path == "/api/open":
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
            elif path == "/api/browse":
                result = _browse(values)
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
                with workspace._lock:
                    workspace._require_idle()
                    if workspace.analysis.jobs.busy:
                        raise WorkspaceBusy("請先停止或等待目前工作完成。")
                self._send(200, {"message": "工作區已結束。"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            elif path.removeprefix("/api/") in {
                "preferences",
                "targeted/save",
                "targeted/method",
                "targets/import",
                "untargeted/save",
                "method",
                "method/save",
                "samples/export",
                "istd",
                "istd/copy",
                "open-local",
            }:
                result = workspace.analysis_action(path.removeprefix("/api/"), values)
            elif match := re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", path):
                result = workspace.cancel(match[1])
            else:
                self._send(404, {"error": "找不到此操作。"})
                return
            self._send(200, result)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
