"""Helpers shared by test_workbench_browser and other test modules."""

from __future__ import annotations

import os
import threading
from contextlib import ExitStack

import pytest
from playwright.sync_api import expect, sync_playwright

from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_workbench.config_io import write_settings, write_targets
from xic_workbench.server import create_server
from xic_workbench.workspace import ReviewWorkspace


@pytest.fixture(scope="module")
def browser():
    expect.set_options(timeout=5_000 * DEADLINE_SCALE)
    with sync_playwright() as playwright:
        # CI leaves this unset and uses the browser pinned by uv.lock.
        channel = os.environ.get("XIC_PLAYWRIGHT_CHANNEL")
        browser = playwright.chromium.launch(
            **({"channel": channel} if channel else {})
        )
        yield browser
        browser.close()


# Deadlines are hang watchdogs, not latency requirements. Hosted CI VMs can
# stall for several seconds at a time, so CI scales every browser wait by one
# factor; local runs keep Playwright's defaults.
DEADLINE_SCALE = int(os.environ.get("XIC_BROWSER_DEADLINE_SCALE", "1"))


ACTION_TIMEOUT_MS = 10_000 * DEADLINE_SCALE


@pytest.fixture
def config_dir(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    write_settings(
        {
            **CANONICAL_SETTINGS_DEFAULTS,
            "data_dir": str(tmp_path / "raw"),
            "dll_dir": str(tmp_path / "dll"),
        },
        config_dir=config,
    )
    write_targets(
        [
            {
                "label": "Browser target",
                "mz": "500",
                "expected_rt_min": "1.5",
                "method": "dR",
                "role": "istd",
                "istd_pair": "",
            }
        ],
        config_dir=config,
    )
    return config


@pytest.fixture
def browser_errors():
    return []


@pytest.fixture
def workbench_defaults():
    return {}


@pytest.fixture
def workbench(
    browser,
    config_dir,
    browser_errors,
    browser_diagnostics,
    workbench_defaults,
    monkeypatch,
):
    from xic_extractor import thermo_reader_install

    monkeypatch.setattr(
        thermo_reader_install,
        "default_thermo_reader_dir",
        lambda: config_dir.parent / "managed-reader",
    )
    with ExitStack() as cleanup:
        workspace = ReviewWorkspace(config_dir=config_dir, defaults=workbench_defaults)
        cleanup.callback(workspace.close)
        server = create_server(workspace, port=0)
        cleanup.callback(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cleanup.callback(thread.join, timeout=5)
        cleanup.callback(server.shutdown)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        cleanup.callback(context.close)
        # DOM snapshots carry the failure state; screencast frames add renderer load.
        context.tracing.start(snapshots=True, sources=True)
        cleanup.callback(
            context.tracing.stop,
            path=browser_diagnostics.directory / "browser-trace.zip",
        )
        page = context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT_MS)
        browser_diagnostics.attach(page, browser_errors)
        host, port = server.server_address
        page.goto(f"http://{host}:{port}/")
        expect(page.locator("body")).not_to_have_class("booting")
        expect(page.locator("#t-data_dir")).to_be_visible()
        yield page
    assert not browser_errors, "Browser errors: " + "\n".join(browser_errors)
