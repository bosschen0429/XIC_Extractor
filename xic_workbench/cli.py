"""Launch the HTML workspace without importing Qt."""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import webbrowser
from pathlib import Path

from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(
        description="Local HTML workspace for XIC analysis and result review"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Batch output folder, Targeted XLSX, or cid_nl_explanation.sqlite",
    )
    parser.add_argument("--raw-dir", default=os.environ.get("XIC_RAW_ROOT", ""))
    parser.add_argument(
        "--dll-dir", default=os.environ.get("THERMO_RAWFILE_READER_DLL_DIR", "")
    )
    parser.add_argument(
        "--port", type=int, default=0, help="Local port; default chooses a free port"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Print the URL without opening a browser"
    )
    parser.add_argument("--config-dir", type=Path, help="Writable settings directory")
    parser.add_argument("--smoke-test", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.smoke_test:
        from xic_workbench.smoke import run_smoke

        return run_smoke(args.smoke_test)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    logging.basicConfig(level=logging.WARNING)
    workspace = ReviewWorkspace(
        defaults={
            "source": args.source or "",
            "raw_dir": args.raw_dir,
            "dll_dir": args.dll_dir,
        },
        config_dir=args.config_dir,
    )
    try:
        server = create_server(workspace, port=args.port)
    except OSError as exc:
        workspace.close()
        parser.exit(1, f"Unable to start the local workspace: {exc}\n")
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"XIC Workbench: {url}", flush=True)
    print("Press Ctrl+C to stop the local workspace.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workspace.close()
    return 0
