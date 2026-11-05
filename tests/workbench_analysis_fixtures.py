"""Helpers shared by test_workbench_analysis and other test modules."""

from __future__ import annotations

import time


def _terminal(jobs):
    deadline = time.monotonic() + 5
    while jobs.busy and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not jobs.busy
    return jobs.state()
