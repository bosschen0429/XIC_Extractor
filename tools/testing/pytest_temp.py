"""Worktree-local pytest temporary files, independent of application fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("basetemp") is not None:
        return

    override = os.environ.get("PYTEST_DEBUG_TEMPROOT")
    root = Path(override) if override else config.rootpath / "tmp_runtime"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise pytest.UsageError(
            f"ENVIRONMENT_BLOCKED: cannot create pytest temporary root {root}: {error}"
        ) from error

    if not override:
        patch = pytest.MonkeyPatch()
        config.add_cleanup(patch.undo)
        patch.setenv("PYTEST_DEBUG_TEMPROOT", str(root))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    # Share pytest's factory with fixtures and xdist, before workers start.
    factory: pytest.TempPathFactory = (
        session.config._tmp_path_factory  # type: ignore[attr-defined]
    )
    try:
        factory.getbasetemp()
    except OSError as error:
        pytest.exit(
            "ENVIRONMENT_BLOCKED: pytest temporary directory initialization failed: "
            f"{error}\nNo test assertions have run. Choose a writable --basetemp "
            "or PYTEST_DEBUG_TEMPROOT and rerun unchanged tests.",
            returncode=pytest.ExitCode.USAGE_ERROR,
        )
