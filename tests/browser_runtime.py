"""Chromium and deadline fixtures independent of Workbench composition."""

import os
import sys
from functools import cache

import pytest
from playwright.sync_api import expect, sync_playwright


@cache
def _windows_network_arguments(channel: str | None) -> tuple[str, ...]:
    """Preserve Playwright's defaults while disabling Windows' faulty port allocator."""
    # https://github.com/microsoft/playwright/issues/43545
    # Repeating --disable-features replaces, rather than extends, the stock list.
    # Read that list once per channel instead of maintaining a copy of it here.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel=channel, args=["--enable-automation"]
        )
        try:
            session = browser.new_browser_cdp_session()
            arguments = session.send("Browser.getBrowserCommandLine")["arguments"]
        finally:
            browser.close()
    prefix = "--disable-features="
    disabled = next(
        (arg[len(prefix) :] for arg in reversed(arguments) if arg.startswith(prefix)),
        "",
    )
    features = dict.fromkeys(
        [*filter(None, disabled.split(",")), "TcpPortRandomizationWin"]
    )
    return (prefix + ",".join(features),)


@pytest.fixture(scope="module")
def browser():
    expect.set_options(timeout=5_000 * DEADLINE_SCALE)
    # CI leaves the channel unset and uses the browser pinned by uv.lock.
    channel = os.environ.get("XIC_PLAYWRIGHT_CHANNEL") or None
    arguments = _windows_network_arguments(channel) if sys.platform == "win32" else ()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, args=arguments)
        yield browser
        browser.close()


DEADLINE_SCALE = int(os.environ.get("XIC_BROWSER_DEADLINE_SCALE", "1"))


ACTION_TIMEOUT_MS = 10_000 * DEADLINE_SCALE
