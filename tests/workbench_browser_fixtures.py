"""Helpers shared by test_workbench_browser and other test modules."""

from __future__ import annotations

import threading
from contextlib import ExitStack

import pytest
from playwright.sync_api import expect

from tests.browser_runtime import ACTION_TIMEOUT_MS as ACTION_TIMEOUT_MS
from tests.browser_runtime import DEADLINE_SCALE as DEADLINE_SCALE
from tests.browser_runtime import browser as browser
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS
from xic_workbench.application import create_workspace
from xic_workbench.config_io import write_settings, write_targets
from xic_workbench.server import create_server
from xic_workbench.user_guide import read_user_guide

# Deadlines are hang watchdogs, not latency requirements. Hosted CI VMs can
# stall for several seconds at a time, so CI scales every browser wait by one
# factor; local runs keep Playwright's defaults.


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
        workspace = create_workspace(config_dir=config_dir, defaults=workbench_defaults)
        cleanup.callback(workspace.close)
        server = create_server(workspace, port=0, read_user_guide=read_user_guide)
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
