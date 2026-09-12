from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.config_io import targets_example_path
from gui.sections.settings_widgets import CollapsibleSection
from gui.ui import DropLineEdit, field_label, section_card
from xic_extractor.configuration.models import ConfigError
from xic_extractor.configuration.targets import read_istd_targets

_MODE_LABELS = (
    ("full", "完整分析：候選偵測與跨樣本對齊"),
    ("discovery_only", "僅候選偵測：單一 RAW 檔"),
    ("align_only", "僅跨樣本對齊：既有偵測結果"),
)

# Which path fields each mode actually needs (others are hidden, not greyed).
_MODE_FIELDS = {
    "full": {"raw_dir", "targets_csv", "dll_dir", "output_dir"},
    "discovery_only": {"raw_file", "dll_dir", "output_dir"},
    "align_only": {"raw_dir", "targets_csv", "batch_index", "dll_dir", "output_dir"},
}

_FIELD_TOOLTIPS = {
    "raw_dir": "包含多個 .raw 檔的資料夾，整批跑 discovery 與對齊。",
    "raw_file": "單一 .raw 檔，只跑 discovery 不做跨樣本對齊。",
    "batch_index": "既有的 discovery_batch_index.csv，跳過 discovery 直接對齊。",
    "targets_csv": (
        "與 Targeted 共用 targets.csv；僅 is_istd=TRUE 列用於內標對齊，"
        "其餘列略過，不限制 Discovery 搜尋。"
    ),
    "dll_dir": "Thermo RAW 讀取所需的 DLL 目錄。",
    "output_dir": "候選清單與對齊矩陣等結果的儲存位置。",
}


class _PathField:
    """A label / line-edit / browse triplet laid out on a shared grid row so
    every field's input column aligns."""

    def __init__(
        self,
        grid: QGridLayout,
        row: int,
        label: str,
        *,
        directory: bool,
        tooltip: str,
        on_change: object,
    ) -> None:
        self._directory = directory
        self._label = field_label(label)
        self._label.setToolTip(tooltip)
        self._edit = DropLineEdit()
        self._edit.setToolTip(tooltip)
        self._edit.textChanged.connect(on_change)  # type: ignore[arg-type]
        self._browse = QPushButton("瀏覽…")
        self._browse.setFixedWidth(70)
        self._browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse.clicked.connect(self._on_browse)
        grid.addWidget(self._label, row, 0)
        grid.addWidget(self._edit, row, 1)
        grid.addWidget(self._browse, row, 2)

    def value(self) -> str:
        return self._edit.text().strip()

    def set_value(self, value: str) -> None:
        self._edit.setText(value)

    def set_visible(self, visible: bool) -> None:
        for widget in (self._label, self._edit, self._browse):
            widget.setVisible(visible)

    def set_invalid(self, invalid: bool) -> None:
        self._edit.setProperty("invalid", invalid)
        style = self._edit.style()
        if style is not None:
            style.unpolish(self._edit)
            style.polish(self._edit)

    def _on_browse(self) -> None:
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(
                self._edit, "選擇資料夾", self.value()
            )
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                self._edit, "選擇檔案", self.value()
            )
        if chosen:
            self._edit.setText(chosen)


class DiscoverySourceSection(QWidget):
    changed = pyqtSignal()
    new_batch_requested = pyqtSignal()
    reset_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card, body = section_card(
            "資料來源與輸出",
            "",
        )
        root.addWidget(card)

        mode_row = QHBoxLayout()
        mode_row.addWidget(field_label("模式"))
        self._mode = QComboBox()
        for key, label in _MODE_LABELS:
            self._mode.addItem(label, key)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        mode_row.addWidget(self._mode, 1)

        self._new_batch_btn = QPushButton("開始新批次")
        self._new_batch_btn.setToolTip(
            "保留目前模式、DLL 與內標設定，清空本批來源和輸出位置"
        )
        self._new_batch_btn.clicked.connect(lambda: self.new_batch_requested.emit())
        mode_row.addWidget(self._new_batch_btn)

        self._reset_btn = QPushButton("清空來源")
        self._reset_btn.setObjectName("btn_reset_batch")
        self._reset_btn.setToolTip("清空目前來源與樣品設定，從 0 開始配置新批次")
        self._reset_btn.setFlat(True)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        mode_row.addWidget(self._reset_btn)
        body.addLayout(mode_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)

        emit = self._emit_changed
        self._fields = {
            "raw_dir": _PathField(
                grid,
                0,
                "RAW 資料夾",
                directory=True,
                tooltip=_FIELD_TOOLTIPS["raw_dir"],
                on_change=self._on_raw_dir_changed,
            ),
            "raw_file": _PathField(
                grid,
                1,
                "單一 RAW 檔",
                directory=False,
                tooltip=_FIELD_TOOLTIPS["raw_file"],
                on_change=emit,
            ),
            "batch_index": _PathField(
                grid,
                2,
                "discovery_batch_index",
                directory=False,
                tooltip=_FIELD_TOOLTIPS["batch_index"],
                on_change=emit,
            ),
            "output_dir": _PathField(
                grid,
                3,
                "輸出目錄",
                directory=True,
                tooltip=_FIELD_TOOLTIPS["output_dir"],
                on_change=emit,
            ),
        }

        self._dll_section = CollapsibleSection("Thermo DLL：需要設定（展開）")
        dll_panel = QWidget()
        dll_grid = QGridLayout(dll_panel)
        dll_grid.setContentsMargins(0, 0, 0, 0)
        dll_grid.setColumnStretch(1, 1)
        self._fields["dll_dir"] = _PathField(
            dll_grid,
            0,
            "DLL 目錄",
            directory=True,
            tooltip=_FIELD_TOOLTIPS["dll_dir"],
            on_change=self._on_dll_changed,
        )
        self._dll_section.add_row(dll_panel)
        body.addWidget(self._dll_section)

        self._targets_section = CollapsibleSection("內標對齊（展開設定）")
        targets_panel = QWidget()
        targets_grid = QGridLayout(targets_panel)
        targets_grid.setContentsMargins(0, 0, 0, 0)
        targets_grid.setColumnStretch(1, 1)
        self._fields["targets_csv"] = _PathField(
            targets_grid,
            0,
            "內標清單 CSV",
            directory=False,
            tooltip=_FIELD_TOOLTIPS["targets_csv"],
            on_change=self._on_targets_changed,
        )
        self._targets_section.add_row(targets_panel)
        self._targets_error = ""
        self._targets_tools = QWidget()
        tools = QHBoxLayout(self._targets_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        self._targets_summary = QLabel()
        self._targets_summary.setWordWrap(True)
        tools.addWidget(self._targets_summary, 1)
        for text, callback in (
            ("另存可編輯副本…", self._save_targets_copy),
            ("開啟清單", self._open_targets),
            ("重新載入", self._on_targets_changed),
        ):
            button = QPushButton(text)
            button.clicked.connect(callback)
            tools.addWidget(button)
        self._targets_section.add_row(self._targets_tools)
        body.addWidget(self._targets_section)
        self._fields["targets_csv"].set_value(str(targets_example_path()))
        self._apply_mode_visibility()
        self._apply_initial_disclosure()

    def _on_targets_changed(self) -> None:
        if not hasattr(self, "_targets_summary"):
            return
        self._targets_error = ""
        value = self._fields["targets_csv"].value()
        if not value:
            self._targets_summary.setText("未載入內標清單；不使用內標對齊。")
            self._targets_section.set_title("內標對齊：未使用（展開設定）")
        else:
            try:
                selection = read_istd_targets(Path(value))
                self._targets_summary.setText(
                    f"已載入 {len(selection.targets)} 筆內標；略過 "
                    f"{selection.skipped_count} 筆一般目標物，不限制 Discovery 搜尋。"
                )
                self._targets_section.set_title(
                    f"內標對齊：{len(selection.targets)} 筆（展開編輯）"
                )
            except (OSError, ValueError, ConfigError) as exc:
                self._targets_error = str(exc)
                self._targets_summary.setText(
                    f"內標清單無法使用：{exc}\n請修正 CSV 並重新載入，或清空清單。"
                )
                self._targets_section.set_title("內標清單需要修正（展開處理）")
                self._targets_section.set_expanded(True)
        self._emit_changed()

    def _on_dll_changed(self) -> None:
        value = self._fields["dll_dir"].value()
        if _is_dir(value):
            title = "Thermo DLL：已設定（展開編輯）"
        elif value:
            title = "Thermo DLL：路徑無效（展開修正）"
        else:
            title = "Thermo DLL：需要設定（展開）"
        self._dll_section.set_title(title)
        if not _is_dir(value):
            self._dll_section.set_expanded(True)
        self._emit_changed()

    def _save_targets_copy(self) -> None:
        source = Path(self._fields["targets_csv"].value())
        if not source.is_file():
            source = targets_example_path()
        chosen, _ = QFileDialog.getSaveFileName(
            self, "另存可編輯內標清單", "targets.csv", "CSV (*.csv)"
        )
        if not chosen:
            return
        destination = Path(chosen)
        if destination.resolve() == targets_example_path().resolve():
            QMessageBox.warning(self, "請另存副本", "請選擇其他檔名，保留隨附範本。")
            return
        try:
            if source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
            self._fields["targets_csv"].set_value(str(destination))
        except OSError as exc:
            QMessageBox.warning(self, "無法儲存清單", str(exc))

    def _open_targets(self) -> None:
        path = Path(self._fields["targets_csv"].value())
        if path.resolve() == targets_example_path().resolve():
            self._save_targets_copy()
            path = Path(self._fields["targets_csv"].value())
            if path.resolve() == targets_example_path().resolve():
                return
        if not path.is_file() or not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(path.resolve()))
        ):
            QMessageBox.warning(
                self, "無法開啟清單", "請選擇 CSV，並設定試算表應用程式的檔案關聯。"
            )

    def _on_raw_dir_changed(self) -> None:
        if not self._loading:
            raw_dir_val = self._fields["raw_dir"].value().strip()
            if raw_dir_val:
                raw_name = Path(raw_dir_val).name
                if raw_name:
                    cur_out = self._fields["output_dir"].value().strip()
                    if not cur_out:
                        self._fields["output_dir"].set_value(f"output/{raw_name}")
                    else:
                        try:
                            p = Path(cur_out)
                            if p.name == "output":
                                self._fields["output_dir"].set_value(
                                    str(p / raw_name).replace("\\", "/")
                                )
                            elif p.parent.name == "output":
                                self._fields["output_dir"].set_value(
                                    str(p.parent / raw_name).replace("\\", "/")
                                )
                        except Exception:
                            pass
        self._emit_changed()

    def _on_reset_clicked(self) -> None:
        self.reset_paths()
        self.reset_requested.emit()

    def reset_paths(self) -> None:
        self._clear_fields(
            "raw_dir", "raw_file", "batch_index", "targets_csv", "output_dir"
        )

    def prepare_next_batch(self) -> None:
        """Clear batch-specific paths while preserving the reusable method context."""
        self._clear_fields("raw_dir", "raw_file", "batch_index", "output_dir")

    def load(self, values: Mapping[str, object]) -> None:
        self._loading = True
        try:
            mode = _string_value(values, "mode", default="full")
            index = self._mode.findData(mode)
            self._mode.setCurrentIndex(index if index >= 0 else 0)
            self._fields["raw_dir"].set_value(_string_value(values, "raw_dir"))
            self._fields["raw_file"].set_value(_string_value(values, "raw_file"))
            self._fields["batch_index"].set_value(
                _string_value(values, "discovery_batch_index")
            )
            self._fields["targets_csv"].set_value(_string_value(values, "targets_csv"))
            self._fields["dll_dir"].set_value(_string_value(values, "dll_dir"))
            self._fields["output_dir"].set_value(_string_value(values, "output_dir"))
            self._apply_mode_visibility()
        finally:
            self._loading = False
        self._apply_initial_disclosure()
        self.changed.emit()

    def get_values(self) -> dict[str, str]:
        return {
            "mode": self._mode_key(),
            "raw_dir": self._fields["raw_dir"].value(),
            "raw_file": self._fields["raw_file"].value(),
            "discovery_batch_index": self._fields["batch_index"].value(),
            "targets_csv": self._fields["targets_csv"].value(),
            "dll_dir": self._fields["dll_dir"].value(),
            "output_dir": self._fields["output_dir"].value(),
        }

    def is_valid(self) -> bool:
        return not self.missing_fields()

    def missing_fields(self) -> list[str]:
        """Human-readable names of required fields that are empty or invalid."""
        values = self.get_values()
        mode = values["mode"]
        problems: list[tuple[str, bool]] = [
            ("輸出目錄", not values["output_dir"]),
            ("DLL 目錄", not _is_dir(values["dll_dir"])),
        ]
        if mode != "discovery_only" and self._targets_error:
            problems.append(("內標清單：請修正 CSV 並重新載入，或清空清單", True))
        if mode == "full":
            problems.append(("RAW 資料夾", not _is_dir(values["raw_dir"])))
        elif mode == "discovery_only":
            problems.append(("單一 RAW 檔", not _is_file(values["raw_file"])))
        elif mode == "align_only":
            problems.append(("RAW 資料夾", not _is_dir(values["raw_dir"])))
            problems.append(
                ("discovery_batch_index", not _is_file(values["discovery_batch_index"]))
            )
        self._refresh_invalid(mode, values)
        return [name for name, bad in problems if bad]

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def reveal_invalid(self) -> None:
        """Expose invalid low-frequency fields without changing their values."""
        values = self.get_values()
        if not _is_dir(values["dll_dir"]):
            self._dll_section.set_expanded(True)
        if self._targets_error and self._mode_key() != "discovery_only":
            self._targets_section.set_expanded(True)

    # ── Private ─────────────────────────────────────────────────────────────

    def _refresh_invalid(self, mode: str, values: Mapping[str, str]) -> None:
        checks = {
            "raw_dir": not _is_dir(values["raw_dir"]),
            "raw_file": not _is_file(values["raw_file"]),
            "batch_index": not _is_file(values["discovery_batch_index"]),
            "targets_csv": (
                bool(values["targets_csv"]) and not _is_file(values["targets_csv"])
            ),
            "dll_dir": not _is_dir(values["dll_dir"]),
            "output_dir": not values["output_dir"],
        }
        needed = _MODE_FIELDS[mode]
        for key, field in self._fields.items():
            field.set_invalid(key in needed and checks[key])

    def _mode_key(self) -> str:
        mode = self._mode.currentData()
        return str(mode if mode is not None else "full")

    def _on_mode_changed(self, _index: int) -> None:
        self._apply_mode_visibility()
        self._emit_changed()

    def _apply_mode_visibility(self) -> None:
        needed = _MODE_FIELDS[self._mode_key()]
        self._targets_section.setVisible("targets_csv" in needed)
        for key, field in self._fields.items():
            if key not in {"dll_dir", "targets_csv"}:
                field.set_visible(key in needed)

    def _apply_initial_disclosure(self) -> None:
        self._dll_section.set_expanded(not _is_dir(self._fields["dll_dir"].value()))
        self._targets_section.set_expanded(bool(self._targets_error))

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()

    def _clear_fields(self, *keys: str) -> None:
        was_loading = self._loading
        self._loading = True
        try:
            for key in keys:
                self._fields[key].set_value("")
        finally:
            self._loading = was_loading
        if not was_loading:
            self.changed.emit()


def _is_dir(value: str) -> bool:
    return bool(value) and Path(value).is_dir()


def _is_file(value: str) -> bool:
    return bool(value) and Path(value).is_file()


def _string_value(
    values: Mapping[str, object],
    key: str,
    *,
    default: str = "",
) -> str:
    value = values.get(key, default)
    return value if isinstance(value, str) else default
