import csv
from pathlib import Path

from PyQt6.QtCore import QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import config_io
from gui.ui import DropTableWidget, titled_card
from xic_extractor.configuration.csv_io import read_target_draft_rows

_TABLE_HEADERS = [
    "名稱",
    "ISTD",
    "ISTD 配對",
    "m/z",
    "RT 下限",
    "RT 上限",
    "ppm",
    "NL (Da)",
    "",
]
(
    _COL_LABEL,
    _COL_ISTD,
    _COL_PAIR,
    _COL_MZ,
    _COL_RT_MIN,
    _COL_RT_MAX,
    _COL_PPM,
    _COL_NL,
    _COL_DEL,
) = range(9)

_NL_PRESETS: list[tuple[str, str]] = [
    ("", "—"),
    ("116.0474", "dR · 116.0474"),
    ("132.0423", "R · 132.0423"),
    ("146.0579", "MeR · 146.0579"),
]
_VALUE_TO_DISPLAY = {v: d for v, d in _NL_PRESETS}
_DISPLAY_TO_VALUE = {d: v for v, d in _NL_PRESETS}

_DEFAULT_PPM = "20"
_DEFAULT_NL_PPM_WARN = "20"
_DEFAULT_NL_PPM_MAX = "50"
_VISIBLE_TARGET_FIELDS = {
    "label",
    "mz",
    "rt_min",
    "rt_max",
    "ppm_tol",
    "neutral_loss_da",
    "nl_ppm_warn",
    "nl_ppm_max",
    "is_istd",
    "istd_pair",
}


class _NoScrollComboBox(QComboBox):
    """Only change the selection when the combo box itself has focus."""

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        if event is None:
            return
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


class TargetsSection(QWidget):
    targets_saved = pyqtSignal()
    details_requested = pyqtSignal()
    changed = pyqtSignal()

    _COL_NL = _COL_NL
    _COL_ISTD = _COL_ISTD

    def __init__(self) -> None:
        super().__init__()
        self._dirty = False
        self._is_loading = False
        self._detail_mode = False
        self._row_meta: list[dict[str, str]] = []

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        card, header_layout, card_layout = titled_card("分析目標")
        root_layout.addWidget(card, 1)

        self._summary_label = QLabel()
        self._summary_label.setObjectName("targets_summary")
        self._dirty_label = QLabel()
        self._dirty_label.setObjectName("targets_dirty_status")
        self._inspect_button = QPushButton("檢視與編輯")
        self._inspect_button.setObjectName("inspect_targets")
        header_layout.addStretch()
        header_layout.addWidget(self._summary_label)
        header_layout.addWidget(self._dirty_label)
        header_layout.addWidget(self._inspect_button)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 16, 16, 16)
        self._import_button = QPushButton("匯入 CSV")
        self._import_xlsx_button = QPushButton("從 Excel 匯入")
        self._add_button = QPushButton("新增目標")
        self._add_button.setObjectName("btn_add")
        self._save_button = QPushButton("儲存目標")
        self._save_button.setObjectName("btn_save")
        self._save_button.setVisible(False)
        toolbar.addWidget(self._import_button)
        toolbar.addWidget(self._import_xlsx_button)
        toolbar.addStretch()
        toolbar.addWidget(self._add_button)
        toolbar.addWidget(self._save_button)
        card_layout.addLayout(toolbar)

        self._table = DropTableWidget(
            0,
            len(_TABLE_HEADERS),
            allowed_extensions=(".csv", ".tsv", ".xlsx", ".xls"),
        )
        self._table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        self._table.setAlternatingRowColors(True)
        v_header = self._table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked
            | QTableWidget.EditTrigger.EditKeyPressed
        )
        h_header = self._table.horizontalHeader()
        if h_header is not None:
            h_header.setStretchLastSection(False)
            h_header.setSectionResizeMode(_COL_LABEL, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(_COL_PAIR, QHeaderView.ResizeMode.Stretch)
            for column in (
                _COL_ISTD,
                _COL_MZ,
                _COL_RT_MIN,
                _COL_RT_MAX,
                _COL_PPM,
                _COL_NL,
                _COL_DEL,
            ):
                h_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_ISTD, 58)
        self._table.setColumnWidth(_COL_MZ, 104)
        self._table.setColumnWidth(_COL_RT_MIN, 88)
        self._table.setColumnWidth(_COL_RT_MAX, 88)
        self._table.setColumnWidth(_COL_PPM, 68)
        self._table.setColumnWidth(_COL_NL, 148)
        self._table.setColumnWidth(_COL_DEL, 64)
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._align_table_headers()
        self._table.file_dropped.connect(self._on_file_dropped)
        card_layout.addWidget(self._table, 1)
        self._table.setVisible(False)

        self._table.itemChanged.connect(self._on_item_changed)
        self._add_button.clicked.connect(self._on_add_row)
        self._save_button.clicked.connect(self._save)
        self._import_button.clicked.connect(self._on_import)
        self._import_xlsx_button.clicked.connect(self._on_import_xlsx)
        self._inspect_button.clicked.connect(lambda: self.details_requested.emit())
        self._update_summary()

    def load(self, targets: list[dict[str, str]]) -> None:
        self._is_loading = True
        self._row_meta = []
        self._table.setRowCount(0)
        try:
            for target in targets:
                self._append_row(target)
        finally:
            self._is_loading = False
        self._set_dirty(False)

    def get_targets(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for row in range(self._table.rowCount()):
            meta = self._row_meta[row]
            nl_combo = self._table.cellWidget(row, _COL_NL)
            assert isinstance(nl_combo, QComboBox)
            nl_value = _DISPLAY_TO_VALUE.get(
                nl_combo.currentText(), nl_combo.currentText()
            )
            if nl_value == "—":
                nl_value = ""

            istd_container = self._table.cellWidget(row, _COL_ISTD)
            assert istd_container is not None
            istd_cb = istd_container.findChild(QCheckBox)
            assert istd_cb is not None
            is_istd = "true" if istd_cb.isChecked() else "false"

            visible_values = {
                "label": self._item_text(row, _COL_LABEL),
                "mz": self._item_text(row, _COL_MZ),
                "rt_min": self._item_text(row, _COL_RT_MIN),
                "rt_max": self._item_text(row, _COL_RT_MAX),
                "ppm_tol": self._item_text(row, _COL_PPM) or _DEFAULT_PPM,
                "neutral_loss_da": nl_value,
                "nl_ppm_warn": meta.get("nl_ppm_warn", _DEFAULT_NL_PPM_WARN),
                "nl_ppm_max": meta.get("nl_ppm_max", _DEFAULT_NL_PPM_MAX),
                "is_istd": is_istd,
                "istd_pair": self._item_text(row, _COL_PAIR),
            }
            hidden_values = {
                key: value for key, value in meta.items() if key not in visible_values
            }
            result.append({**hidden_values, **visible_values})
        return result

    def _item_text(self, row: int, column: int) -> str:
        item = self._table.item(row, column)
        assert item is not None
        return item.text().strip()

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def set_detail_mode(self, enabled: bool) -> None:
        """Show the editor in the shared detail workspace without rebuilding it."""
        self._detail_mode = enabled
        self._table.setVisible(enabled)
        self._inspect_button.setVisible(not enabled)

    def _append_row(self, target: dict[str, str] | None = None) -> None:
        target = target or {}
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._row_meta.append(
            {
                key: str(value)
                for key, value in target.items()
                if key not in _VISIBLE_TARGET_FIELDS
            }
            | {
                "nl_ppm_warn": target.get("nl_ppm_warn", _DEFAULT_NL_PPM_WARN),
                "nl_ppm_max": target.get("nl_ppm_max", _DEFAULT_NL_PPM_MAX),
            }
        )

        with QSignalBlocker(self._table):
            self._table.setItem(
                row, _COL_LABEL, self._text_item(target.get("label", ""))
            )
            self._table.setItem(
                row, _COL_PAIR, self._text_item(target.get("istd_pair", ""))
            )
            self._table.setItem(row, _COL_MZ, self._numeric_item(target.get("mz", "")))
            self._table.setItem(
                row, _COL_RT_MIN, self._numeric_item(target.get("rt_min", ""))
            )
            self._table.setItem(
                row, _COL_RT_MAX, self._numeric_item(target.get("rt_max", ""))
            )
            self._table.setItem(
                row,
                _COL_PPM,
                self._numeric_item(target.get("ppm_tol", _DEFAULT_PPM)),
            )

        istd_cb = QCheckBox()
        istd_cb.setChecked(target.get("is_istd", "false").lower() == "true")
        istd_cb.stateChanged.connect(lambda _: self._set_dirty(True))
        istd_container = QWidget()
        istd_layout = QHBoxLayout(istd_container)
        istd_layout.setContentsMargins(0, 0, 0, 0)
        istd_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        istd_layout.addWidget(istd_cb)
        self._table.setCellWidget(row, _COL_ISTD, istd_container)

        nl_combo = self._make_nl_combo(target.get("neutral_loss_da", ""))
        self._table.setCellWidget(row, _COL_NL, nl_combo)

        delete_button = QPushButton("刪除")
        delete_button.setObjectName("btn_delete")
        delete_button.clicked.connect(self._on_delete_clicked)
        self._table.setCellWidget(row, _COL_DEL, delete_button)

    def _make_nl_combo(self, value: str) -> _NoScrollComboBox:
        combo = _NoScrollComboBox()
        combo.setEditable(True)
        for _val, display in _NL_PRESETS:
            combo.addItem(display)
        display = _VALUE_TO_DISPLAY.get(value, value or "—")
        idx = combo.findText(display)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setEditText(value)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
        combo.currentTextChanged.connect(lambda _: self._set_dirty(True))
        return combo

    @staticmethod
    def _text_item(value: str) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        return item

    @staticmethod
    def _numeric_item(value: str) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return item

    def _align_table_headers(self) -> None:
        for column in (_COL_LABEL, _COL_PAIR):
            item = self._table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
        for column in (_COL_MZ, _COL_RT_MIN, _COL_RT_MAX, _COL_PPM, _COL_NL):
            item = self._table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
        for column in (_COL_ISTD, _COL_DEL):
            item = self._table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._is_loading:
            self._set_dirty(True)

    def _on_add_row(self) -> None:
        self.details_requested.emit()
        self._append_row()
        row = self._table.rowCount() - 1
        self._table.setCurrentCell(row, _COL_LABEL)
        self._table.scrollToItem(self._table.item(row, _COL_LABEL))
        self._set_dirty(True)

    def _on_delete_clicked(self) -> None:
        button = self.sender()
        if not isinstance(button, QPushButton):
            return
        row = self._find_widget_row(button, _COL_DEL)
        if row >= 0:
            self._table.removeRow(row)
            del self._row_meta[row]
            self._set_dirty(True)

    def _on_file_dropped(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            self._import_xlsx_file(path)
        elif suffix in (".csv", ".tsv"):
            self._import_csv_file(path)

    def _on_import(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "匯入目標 CSV", "", "CSV Files (*.csv)"
        )
        if not filename:
            return
        self._import_csv_file(Path(filename))

    def _import_csv_file(self, path: Path) -> None:
        try:
            targets = read_target_draft_rows(path)
            self.details_requested.emit()
            self.load(targets)
            self._set_dirty(True)
        except Exception as exc:
            QMessageBox.warning(self, "匯入失敗", f"無法讀取目標檔案：{exc}")

    def _on_import_xlsx(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "選擇 RT 標準品 Excel", "", "Excel Files (*.xlsx *.xls)"
        )
        if not filename:
            return
        self._import_xlsx_file(Path(filename))

    def _import_xlsx_file(self, xlsx_path: Path) -> None:
        """從 RT 標準品 Excel 自動產生目標清單並載入表格。"""
        import openpyxl

        from scripts.xlsx_to_targets import (
            DEFAULT_RT_WINDOW,
            _assign_istd_pairs,
            parse_dna_sheet,
            parse_rna_sheet,
        )

        # 2. 選擇分頁
        _SHEET_OPTIONS = [
            "DNA（NL = dR · 116.0474 Da）",
            "RNA（NL = R / MeR 自動偵測）",
            "DNA + RNA（合併）",
        ]
        _SHEET_KEYS = ["DNA", "RNA", "both"]
        choice, ok = QInputDialog.getItem(
            self,
            "選擇分頁",
            "要匯入哪個分頁的標準品？",
            _SHEET_OPTIONS,
            0,
            False,
        )
        if not ok:
            return
        sheet_key = _SHEET_KEYS[_SHEET_OPTIONS.index(choice)]

        # 3. 解析 Excel
        try:
            wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False)
            targets = []
            if sheet_key in ("DNA", "both"):
                if "DNA" in wb.sheetnames:
                    targets.extend(
                        parse_dna_sheet(wb["DNA"], rt_window=DEFAULT_RT_WINDOW)
                    )
            if sheet_key in ("RNA", "both"):
                if "RNA" in wb.sheetnames:
                    targets.extend(
                        parse_rna_sheet(wb["RNA"], rt_window=DEFAULT_RT_WINDOW)
                    )
            _assign_istd_pairs(targets)
        except Exception as exc:
            QMessageBox.critical(self, "匯入失敗", f"無法解析 Excel 檔案：\n{exc}")
            return

        if not targets:
            QMessageBox.warning(self, "無資料", "在選定的分頁中找不到可匯入的目標。")
            return

        # 4. 存一份到 config/ 作為記錄（方便日後追溯使用了哪個 Excel）
        out_name = f"{xlsx_path.stem}_targets_{sheet_key}.csv"
        out_path = config_io.CONFIG_DIR / out_name
        try:
            config_io.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _FIELDS = [
                "label",
                "mz",
                "rt_min",
                "rt_max",
                "ppm_tol",
                "neutral_loss_da",
                "nl_ppm_warn",
                "nl_ppm_max",
                "is_istd",
                "istd_pair",
            ]
            with out_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_FIELDS)
                writer.writeheader()
                writer.writerows(targets)
        except Exception:
            pass  # 存檔失敗不影響主要流程

        # 5. 載入表格
        self.details_requested.emit()
        self.load([{k: str(v) for k, v in t.items()} for t in targets])
        self._set_dirty(True)

    def _find_widget_row(self, widget: QWidget, column: int) -> int:
        for row in range(self._table.rowCount()):
            if self._table.cellWidget(row, column) is widget:
                return row
        return -1

    def _save(self) -> None:
        config_io.write_targets(self.get_targets())
        self._set_dirty(False)
        self.targets_saved.emit()

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._save_button.setVisible(dirty)
        self._update_summary()
        if dirty:
            self.changed.emit()

    def _update_summary(self) -> None:
        istd_count = 0
        for row in range(self._table.rowCount()):
            container = self._table.cellWidget(row, _COL_ISTD)
            checkbox = container.findChild(QCheckBox) if container is not None else None
            if checkbox is not None and checkbox.isChecked():
                istd_count += 1
        self._summary_label.setText(
            f"{self._table.rowCount()} 個目標 · {istd_count} 個 ISTD"
        )
        self._dirty_label.setText("有未儲存變更" if self._dirty else "無未儲存變更")
