import os
import shutil
import uuid
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TMP_ROOT = Path(__file__).resolve().parent.parent / "tmp_runtime"


@pytest.fixture(autouse=True)
def _clear_outer_git_repository_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep hook-provided Git repository bindings out of test subprocesses."""

    for key in tuple(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)


class _QtBot:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._widgets = []

    def addWidget(self, widget) -> None:
        self._widgets.append(widget)

    def mouseClick(self, widget, button: Qt.MouseButton) -> None:
        QTest.mouseClick(widget, button)
        self._app.processEvents()

    def keyClicks(self, widget, text: str) -> None:
        QTest.keyClicks(widget, text)
        self._app.processEvents()

    def wait(self, ms: int) -> None:
        QTest.qWait(ms)
        self._app.processEvents()

    def close(self) -> None:
        for widget in reversed(self._widgets):
            widget.close()
            widget.deleteLater()
        self._app.processEvents()


@pytest.fixture()
def tmp_path() -> Path:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture()
def qtbot(qapp: QApplication):
    bot = _QtBot(qapp)
    try:
        yield bot
    finally:
        bot.close()
