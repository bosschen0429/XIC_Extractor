"""Exercise the delivered local HTML application without reading RAW files."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import uuid
from pathlib import Path

from xic_extractor.discovery.blank_qualification import get_default_background_library
from xic_extractor.presets import bind_cid_nl_recipe, load_preset
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


def run_smoke(report_path: Path) -> int:
    report_path = report_path.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "failed",
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "stage": "server_startup",
    }
    workspace = ReviewWorkspace(config_dir=report_path.parent / "smoke-config")
    server = create_server(workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        str(server.server_address[0]), server.server_address[1], timeout=20
    )
    try:
        connection.request("GET", "/api/session")
        response = connection.getresponse()
        session_cookie = response.getheader("Set-Cookie")
        if session_cookie is None:
            raise RuntimeError("Workspace did not establish its local session")
        cookie = session_cookie.split(";", 1)[0]
        response.read()
        headers = {
            "Cookie": cookie,
            "X-XIC-Client": "workbench",
            "Content-Type": "application/json",
        }

        def request(path, values=None):
            connection.request(
                "POST" if values is not None else "GET",
                path,
                json.dumps(values) if values is not None else None,
                headers,
            )
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError(f"{path}: {response.status}: {data.decode('utf-8')}")
            return data

        config = json.loads(request("/api/config"))
        for asset in (
            "/",
            "/assets/analysis.html",
            "/assets/analysis.js",
            "/assets/analysis.css",
            "/assets/workspace.js",
        ):
            if not request(asset):
                raise RuntimeError(f"Empty frontend asset: {asset}")
        from xic_extractor.configuration.paths import targets_template_path
        from xic_extractor.configuration.targeted_method_files import load_target_table

        for name in ("targeted-method.xlsx", "targets.xlsx"):
            if not request(f"/examples/{name}"):
                raise RuntimeError(f"Empty example: {name}")
        config["targets"] = load_target_table(
            targets_template_path().with_name("targets.xlsx")
        )
        if not config["settings"] or not config["targets"]:
            raise RuntimeError("Bundled settings or targets are empty")
        report["server_started"] = True
        report["stage"] = "bundled_resources"
        background = get_default_background_library()
        if background is None or not background.ions:
            raise RuntimeError("Bundled background library is empty")
        report["resources"] = {
            "settings_keys": len(config["settings"]),
            "target_rows": len(config["targets"]),
            "background_ions": len(background.ions),
        }
        report["stage"] = "method_roundtrip"
        method = json.loads(request("/api/method", {"preset": "dna_dr"}))
        destination = report_path.parent / "smoke-method.toml"
        request(
            "/api/method/save",
            {"draft": method["draft"], "path": str(destination)},
        )
        expected = bind_cid_nl_recipe(load_preset("dna_dr"), "full")
        actual = bind_cid_nl_recipe(load_preset(destination), "full")
        if expected.discovery_settings != actual.discovery_settings:
            raise RuntimeError("Saved HTML method changed its bound settings")
        report["stage"] = "targeted_method_roundtrip"
        saved = json.loads(
            request(
                "/api/targeted/method/save",
                {
                    "name": f"smoke-{uuid.uuid4().hex[:8]}",
                    "settings": config["settings"],
                    "targets": config["targets"],
                },
            )
        )
        method_path = next(
            item["path"] for item in saved["methods"] if item["name"] == saved["name"]
        )
        restored = json.loads(
            request(
                "/api/targeted/method/load",
                {"path": method_path, "settings": config["settings"]},
            )
        )
        if (
            restored["settings"] != config["settings"]
            or restored["targets"] != config["targets"]
        ):
            raise RuntimeError("Saved Targeted method changed settings or targets")
        if any(name == "PyQt6" or name.startswith("PyQt6.") for name in sys.modules):
            raise RuntimeError("HTML application imported Qt")
        report.update(
            status="passed",
            stage="complete",
            preset_roundtrip=True,
            preset_path=str(destination),
            targeted_method_roundtrip=True,
        )
    except Exception as exc:
        report["reason"] = str(exc)
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        workspace.close()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0 if report["status"] == "passed" else 1
