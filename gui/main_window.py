import ctypes
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, QSettings, QSize, Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from gui.dialogs import confirm_close_while_busy, show_about
from gui.styles import DARK_PALETTE, LIGHT_PALETTE, build_stylesheet, set_active
from gui.views.targeted_view import TargetedView
from gui.views.untargeted_view import UntargetedView

_APP_VERSION = "0.2"

if getattr(sys, "frozen", False):
    _ROOT = Path(sys.executable).parent  # user-writable: config/, output/
    _BUNDLE = Path(getattr(sys, "_MEIPASS"))  # read-only bundle: scripts/, assets/
else:
    _ROOT = Path(__file__).resolve().parent.parent
    _BUNDLE = _ROOT
_ICON_PATH = _BUNDLE / "assets" / "app_icon.png"
_ICONS_DIR = _BUNDLE / "assets" / "icons"
(_ROOT / "output").mkdir(exist_ok=True)


def _icon(name: str, hex_color: str = "#7a828e") -> QIcon:
    """Load an SVG icon recoloured to hex_color (the icons ship as #7a828e)."""
    path = _ICONS_DIR / name
    if not path.exists():
        return QIcon()
    svg = path.read_text(encoding="utf-8").replace("#7a828e", hex_color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


if getattr(sys, "frozen", False) and sys.platform == "win32":
    _internal = _ROOT / "_internal"
    if _internal.exists():
        ctypes.windll.kernel32.SetFileAttributesW(
            str(_internal), 0x02
        )  # FILE_ATTRIBUTE_HIDDEN

# (nav label, icon file, header title, header subtitle) per workspace view.
_VIEWS = (
    ("Targeted", "targeted.svg", "Targeted Extraction", "依目標清單萃取並整合 XIC"),
    (
        "Untargeted",
        "untargeted.svg",
        "Untargeted Discovery",
        "全譜峰偵測、跨樣本對齊與候選審閱",
    ),
)


def _color_titlebar(hwnd: int, hex_color: str, text_color: str) -> None:
    """Apply a custom caption colour on Windows 11+ (silently ignored elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        colorref = r | (g << 8) | (b << 16)  # COLORREF = 0x00BBGGRR
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        _set = ctypes.windll.dwmapi.DwmSetWindowAttribute
        _set(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(ctypes.c_int(colorref)), 4)
        textref = (
            int(text_color[1:3], 16)
            | (int(text_color[3:5], 16) << 8)
            | (int(text_color[5:7], 16) << 16)
        )
        _set(hwnd, DWMWA_TEXT_COLOR, ctypes.byref(ctypes.c_int(textref)), 4)
    except Exception:
        pass  # Win10 or non-Windows: no-op


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("XIC Extractor")
        self.resize(1180, 880)
        # Keep the expanded method form readable at the supported small size.
        self.setMinimumSize(1040, 640)
        self._settings = QSettings("ChaoHuLab", "XICExtractor")
        self._dark = bool(self._settings.value("window/dark", False, type=bool))

        if _ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(_ICON_PATH)))

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._state_label = QLabel()
        self._state_label.setObjectName("status_state")
        self._status_bar.addPermanentWidget(self._state_label)
        self._view_states = ["就緒", "就緒"]
        self._themed_icons: list[tuple[object, str]] = []
        self._build_menu_bar()

        root = QWidget()
        root.setObjectName("app_root")
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.setCentralWidget(root)

        self._nav_buttons: list[QPushButton] = []
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        row.addWidget(self._build_sidebar())

        right = QWidget()
        right_col = QVBoxLayout(right)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(0)
        self._header_title = QLabel()
        self._header_title.setObjectName("header_title")
        self._header_subtitle = QLabel()
        self._header_subtitle.setObjectName("header_subtitle")
        right_col.addWidget(self._build_header())
        self._stack = QStackedWidget()
        right_col.addWidget(self._stack, 1)
        row.addWidget(right, 1)

        self.targeted_view = TargetedView(_ROOT / "config")
        self.targeted_view.status_message.connect(
            lambda text, timeout: self._show_status(f"Targeted · {text}", timeout)
        )
        self.targeted_view.state_changed.connect(
            lambda text: self._on_view_state(0, text)
        )
        self.untargeted_view = UntargetedView()
        self.untargeted_view.status_message.connect(
            lambda text, timeout: self._show_status(f"Untargeted · {text}", timeout)
        )
        self.untargeted_view.state_changed.connect(
            lambda text: self._on_view_state(1, text)
        )
        self._views = (self.targeted_view, self.untargeted_view)
        self._stack.addWidget(self.targeted_view)
        self._stack.addWidget(self.untargeted_view)

        self._restore_window_state()
        self._apply_theme(self._dark)
        self.targeted_view.load_config()
        self._restore_dll_directory()
        for view in self._views:
            view.emit_state()

    def _restore_dll_directory(self) -> None:
        """Remember the machine environment independently of batch saves."""
        targeted = self.targeted_view._settings._dll_dir_edit
        untargeted = self.untargeted_view._source._fields["dll_dir"]._edit
        fields = (targeted, untargeted)
        remembered = str(self._settings.value("environment/dll_dir", "") or "")
        if not remembered:
            remembered = next(
                (
                    field.text().strip()
                    for field in fields
                    if field.text().strip() and Path(field.text().strip()).is_dir()
                ),
                "",
            )
        for field in fields:
            if remembered:
                field.setText(remembered)
            field.textChanged.connect(self._remember_dll_directory)
        self.targeted_view._settings.environment_section.set_expanded(
            not bool(targeted.text().strip() and Path(targeted.text()).is_dir())
        )
        self.untargeted_view._source._dll_section.set_expanded(
            not bool(untargeted.text().strip() and Path(untargeted.text()).is_dir())
        )
        self._remember_dll_directory(remembered)

    def _remember_dll_directory(self, value: str) -> None:
        value = value.strip()
        if not value or not Path(value).is_dir():
            return
        value = str(Path(value).resolve())
        if value != self._settings.value("environment/dll_dir", ""):
            self._settings.setValue("environment/dll_dir", value)
            self._settings.sync()

    # ── Menu bar & shortcuts ────────────────────────────────────────────────

    def _build_menu_bar(self) -> None:
        bar = QMenuBar(self)
        self.setMenuBar(bar)

        file_menu = self._add_menu(bar, "檔案(&F)")
        self._add_action(
            file_menu,
            "開啟輸出資料夾",
            "Ctrl+O",
            self._open_output_dir,
            icon="folder.svg",
        )
        file_menu.addSeparator()
        self._add_action(file_menu, "結束", "Ctrl+Q", self.close)

        view_menu = self._add_menu(bar, "檢視(&V)")
        for index, (name, icon_file, _, _) in enumerate(_VIEWS):
            self._add_action(
                view_menu,
                name,
                f"Ctrl+{index + 1}",
                lambda _checked=False, i=index: self._select_view(i),
                icon=icon_file,
            )
        view_menu.addSeparator()
        self._dark_action = QAction("深色模式", self)
        self._dark_action.setCheckable(True)
        self._dark_action.setChecked(self._dark)
        self._dark_action.setShortcut(QKeySequence("Ctrl+D"))
        self._dark_action.toggled.connect(self._apply_theme)
        view_menu.addAction(self._dark_action)

        run_menu = self._add_menu(bar, "執行(&R)")
        self._add_action(
            run_menu,
            "執行目前工作區",
            "Ctrl+R",
            lambda: self._current_view().trigger_run(),
            icon="play.svg",
        )
        self._add_action(
            run_menu,
            "停止",
            "Ctrl+.",
            lambda: self._current_view().trigger_stop(),
            icon="stop.svg",
        )

        help_menu = self._add_menu(bar, "說明(&H)")
        self._add_action(
            help_menu,
            "關於 XIC Extractor",
            None,
            lambda: show_about(self, _APP_VERSION),
            icon="info.svg",
        )

    def _add_menu(self, bar: QMenuBar, title: str) -> QMenu:
        menu = bar.addMenu(title)
        assert menu is not None  # addMenu only returns None on failure
        return menu

    def _add_action(
        self,
        menu: QMenu,
        text: str,
        shortcut: str | None,
        slot: object,
        *,
        icon: str | None = None,
    ) -> None:
        action = QAction(text, self)
        if icon is not None:
            self._themed_icons.append((action, icon))
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)  # type: ignore[arg-type]
        menu.addAction(action)

    def _apply_theme(self, dark: bool) -> None:
        self._dark = dark
        palette = DARK_PALETTE if dark else LIGHT_PALETTE
        set_active(palette)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.setStyleSheet(build_stylesheet(palette))
        self._retheme_icons(palette["icon"])
        for view in getattr(self, "_views", ()):
            view.refresh_theme()
        if self.isVisible():
            _color_titlebar(int(self.winId()), palette["bg_card"], palette["text"])
        self._settings.setValue("window/dark", dark)

    def _retheme_icons(self, color: str) -> None:
        for target, name in self._themed_icons:
            target.setIcon(_icon(name, color))  # type: ignore[attr-defined]

    def _current_view(self) -> TargetedView | UntargetedView:
        return self._views[self._stack.currentIndex()]

    def _open_output_dir(self) -> None:
        output_dir = _ROOT / "output"
        output_dir.mkdir(exist_ok=True)
        os.startfile(str(output_dir))  # noqa: S606

    # ── Shell construction ──────────────────────────────────────────────────

    def _build_sidebar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(184)
        col = QVBoxLayout(bar)
        col.setContentsMargins(14, 18, 14, 16)
        col.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(9)
        if _ICON_PATH.exists():
            logo = QLabel()
            logo.setPixmap(
                QPixmap(str(_ICON_PATH)).scaledToHeight(
                    28, Qt.TransformationMode.SmoothTransformation
                )
            )
            brand.addWidget(logo)
        brand_title = QLabel("XIC Extractor")
        brand_title.setObjectName("brand_title")
        brand.addWidget(brand_title)
        brand.addStretch()
        col.addLayout(brand)

        col.addSpacing(12)

        for index, (name, icon_file, _, _) in enumerate(_VIEWS):
            button = QPushButton(f"  {name}")
            button.setObjectName("nav_item")
            button.setIconSize(QSize(18, 18))
            self._themed_icons.append((button, icon_file))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, i=index: self._select_view(i))
            self._nav_group.addButton(button, index)
            self._nav_buttons.append(button)
            col.addWidget(button)

        col.addStretch()
        footer = QLabel("v0.2")
        footer.setObjectName("brand_sub")
        col.addWidget(footer)
        return bar

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("app_header")
        header.setFixedHeight(60)
        col = QVBoxLayout(header)
        col.setContentsMargins(20, 0, 20, 0)
        col.setSpacing(2)
        col.addStretch()
        col.addWidget(self._header_title)
        col.addWidget(self._header_subtitle)
        col.addStretch()
        return header

    # ── Behaviour ───────────────────────────────────────────────────────────

    def _select_view(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        self._nav_buttons[index].setChecked(True)
        _, _, title, subtitle = _VIEWS[index]
        self._header_title.setText(title)
        self._header_subtitle.setText(subtitle)
        self._refresh_state_label()

    def _on_view_state(self, index: int, text: str) -> None:
        self._view_states[index] = text
        if index == self._stack.currentIndex():
            self._refresh_state_label()

    def _refresh_state_label(self) -> None:
        index = self._stack.currentIndex()
        workspace = _VIEWS[index][0]
        self._state_label.setText(f"{workspace} · {self._view_states[index]}")

    def _restore_window_state(self) -> None:
        geometry = self._settings.value("window/geometry")
        if isinstance(geometry, QByteArray) and not geometry.isEmpty():
            self.restoreGeometry(geometry)
        last_view = self._settings.value("window/view", 1)
        try:
            index = int(last_view)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            index = 1
        self._select_view(index if 0 <= index < len(_VIEWS) else 1)

    def _save_window_state(self) -> None:
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.setValue("window/view", self._stack.currentIndex())

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        palette = DARK_PALETTE if self._dark else LIGHT_PALETTE
        _color_titlebar(int(self.winId()), palette["bg_card"], palette["text"])

    def closeEvent(self, event) -> None:  # type: ignore[override]
        busy = any(
            view.is_busy() for view in (self.targeted_view, self.untargeted_view)
        ) or self.untargeted_view.has_active_report()
        if busy:
            if not confirm_close_while_busy(self):
                event.ignore()
                return
            for view in self._views:
                if view.is_busy() or (
                    view is self.untargeted_view and view.has_active_report()
                ):
                    view.trigger_stop(confirm=False)
            if (
                any(view.is_busy() for view in self._views)
                or self.untargeted_view.has_active_report()
            ):
                self._show_status("正在停止工作，停止完成後再關閉視窗", 5000)
                event.ignore()
                return
        try:
            self.untargeted_view.persist_config()
        except OSError:
            pass  # never let a config-save failure block closing
        self._save_window_state()
        event.accept()

    def _show_status(self, text: str, timeout: int) -> None:
        self._status_bar.showMessage(text, timeout)
