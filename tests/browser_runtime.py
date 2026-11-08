"""Chromium and deadline fixtures independent of Workbench composition."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright


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


DEADLINE_SCALE = int(os.environ.get("XIC_BROWSER_DEADLINE_SCALE", "1"))


ACTION_TIMEOUT_MS = 10_000 * DEADLINE_SCALE
