import os
from pathlib import Path

import pytest

_TMP_ROOT = Path(__file__).resolve().parent.parent / "tmp_runtime"


def pytest_configure() -> None:
    if "PYTEST_DEBUG_TEMPROOT" not in os.environ:
        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(_TMP_ROOT)


@pytest.fixture(autouse=True)
def _clear_outer_git_repository_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep hook-provided Git repository bindings out of test subprocesses."""

    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
