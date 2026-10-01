import json
import multiprocessing
import sys
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.styles import APPLICATION_FONT_FAMILY, APPLICATION_FONT_POINT_SIZE
from gui.wheel_guard import WheelGuard


def configure_application(app) -> None:
    app.setApplicationName("XIC Extractor")
    font = QFont(APPLICATION_FONT_FAMILY, APPLICATION_FONT_POINT_SIZE)
    _enable_tabular_figures(font)
    app.setFont(font)


def _enable_tabular_figures(font: QFont) -> None:
    # Tabular (fixed-width) figures so numeric columns — the targets table, the
    # resolver spin grid, the result tiles — align digit-to-digit instead of
    # jittering on a narrow '1' vs a wide '8'. Qt QSS cannot express this, so it
    # is set as an OpenType feature on the application font (inherited by every
    # widget that does not replace its font outright).
    if not hasattr(font, "setFeature") or not hasattr(QFont, "Tag"):
        return
    font.setFeature(QFont.Tag("tnum"), 1)


def install_wheel_guard(app: QApplication) -> WheelGuard:
    guard = getattr(app, "_xic_wheel_guard", None)
    if isinstance(guard, WheelGuard):
        return guard
    guard = WheelGuard(app)
    setattr(app, "_xic_wheel_guard", guard)
    app.installEventFilter(guard)
    return guard


def main() -> None:
    multiprocessing.freeze_support()
    smoke_report = None
    if len(sys.argv) > 1 and sys.argv[1] == "--smoke-test":
        if len(sys.argv) != 3:
            raise SystemExit("usage: XIC_Extractor --smoke-test REPORT.json")
        smoke_report = Path(sys.argv[2]).resolve()
        smoke_report.parent.mkdir(parents=True, exist_ok=True)
        smoke_report.write_text(
            json.dumps({"status": "failed", "stage": "qt_startup"}), encoding="utf-8"
        )
    app = QApplication(sys.argv)
    configure_application(app)
    install_wheel_guard(app)
    window = MainWindow()
    window.show()
    if smoke_report is not None:
        from gui.desktop_smoke import run_desktop_smoke

        QTimer.singleShot(
            0, lambda: app.exit(run_desktop_smoke(app, window, smoke_report))
        )
    exit_code = app.exec()
    if smoke_report is not None:
        report = json.loads(smoke_report.read_text(encoding="utf-8"))
        report["stage"] = "teardown"
        smoke_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        # Dispose smoke widgets while QApplication still owns their Qt lifetime.
        window.close()
        window.deleteLater()
        guard = getattr(app, "_xic_wheel_guard", None)
        if guard is not None:
            app.removeEventFilter(guard)
            guard.deleteLater()
            delattr(app, "_xic_wheel_guard")
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
