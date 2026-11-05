import hashlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP_ROOT = Path(__file__).resolve().parent.parent / "tmp_runtime"
_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
_CONFIG_SNAPSHOT = pytest.StashKey[dict[str, str]]()


def _config_snapshot() -> dict[str, str]:
    """Include file names so creation and removal also fail the guard."""
    return {
        str(path.relative_to(_CONFIG_DIR)): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in _CONFIG_DIR.rglob("*")
        if path.is_file()
    }


def pytest_sessionstart(session: pytest.Session) -> None:
    session.stash[_CONFIG_SNAPSHOT] = _config_snapshot()


def pytest_sessionfinish(session: pytest.Session) -> None:
    before = session.stash[_CONFIG_SNAPSHOT]
    after = _config_snapshot()
    changed = sorted(
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    )
    if changed:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep(
                "!", "Tests modified repository config: " + ", ".join(changed)
            )


@pytest.fixture(autouse=True)
def isolated_config_dir(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Share one private config between Workbench and CLI within each test."""
    from xic_extractor.configuration import execution_profile
    from xic_workbench import config_io, untargeted_config

    directory = tmp_path_factory.mktemp("isolated-runtime") / "config"
    directory.mkdir(parents=True)
    monkeypatch.setattr(config_io, "CONFIG_DIR", directory)
    monkeypatch.setattr(untargeted_config, "CONFIG_DIR", directory)
    # Patch the runtime-root lookup used by the JSON execution-profile reader.
    # Workbench and CLI now share this test's application settings, not repo state.
    monkeypatch.setattr(execution_profile, "runtime_root", lambda: directory.parent)
    return directory


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


@pytest.fixture(autouse=True)
def _isolate_raw_reader_assembly_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Isolate fake DLL loads without importing RAW support for unrelated tests."""
    raw_reader = sys.modules.get("xic_extractor.raw_reader")

    if raw_reader is not None:
        monkeypatch.setattr(raw_reader, "_ASSEMBLIES_LOADED", False)
    yield
    if raw_reader is None:
        # A test can import the reader lazily and then load fake assemblies.
        # Its initial cache was empty; restore that state for the next test.
        imported_reader = sys.modules.get("xic_extractor.raw_reader")
        if imported_reader is not None:
            vars(imported_reader)["_ASSEMBLIES_LOADED"] = False
