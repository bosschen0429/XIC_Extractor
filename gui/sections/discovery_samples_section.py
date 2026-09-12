from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QPoint, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.ui import DropTableWidget, section_card
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadata,
    SampleRole,
    infer_sample_role,
    load_and_match_sample_metadata,
    load_sample_metadata,
)

_TABLE_HEADERS = [
    "檔案名稱",
    "批次",
    "進樣順序",
    "樣品名稱",
    "分析角色",
]
(
    _COL_STEM,
    _COL_BATCH,
    _COL_ORDER,
    _COL_NAME,
    _COL_ROLE,
) = range(5)

ROLE_CHOICES: tuple[tuple[SampleRole, str], ...] = (
    ("study_sample", "分析樣本"),
    ("qc", "品管"),
    ("pooled_qc", "混合品管"),
    ("blank", "空白"),
    ("calibrator", "校正／標準品"),
    ("solvent", "溶劑"),
    ("system_suitability", "系統適用性"),
    ("unknown", "未指定"),
)


class DiscoverySamplesSection(QWidget):
    changed = pyqtSignal()
    details_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._raw_dir: Path | None = None
        self._custom_roles: dict[str, str] = {}
        self._sequence_source = ""

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(0, 0, 0, 0)

        self._card, body = section_card("樣本與上機資訊")
        self._root_layout.addWidget(self._card)

        summary_toolbar = QHBoxLayout()
        summary_toolbar.setContentsMargins(0, 0, 0, 0)
        self._import_button = QPushButton("匯入 Word 上機資訊")
        self._import_button.setToolTip(
            "以 Word 上機資訊設定進樣順序；亦相容 Excel / CSV / TSV"
        )
        self._import_button.clicked.connect(self._on_import_sequence)

        self._inspect_button = QPushButton("檢視樣本明細")
        self._inspect_button.setObjectName("inspect_samples")
        self._inspect_button.clicked.connect(self.details_requested.emit)

        summary_toolbar.addWidget(self._import_button)
        summary_toolbar.addWidget(self._inspect_button)

        self._source_label = QLabel("Word 來源：尚未匯入")
        self._source_label.setStyleSheet("color: #7a828e;")
        body.addLayout(summary_toolbar)

        self._status_label = QLabel("尚未選取 RAW 資料夾")
        self._status_label.setStyleSheet("color: #7a828e;")
        self._status_label.setWordWrap(True)
        body.addWidget(self._status_label)

        self._export_button = QPushButton("匯出 CSV")
        self._export_button.setToolTip("將當前樣品列表匯出為 sample_metadata.csv")
        self._export_button.clicked.connect(self._on_export_csv)
        summary_toolbar.addWidget(self._export_button)
        summary_toolbar.addStretch()
        summary_toolbar.addWidget(self._source_label)

        self._filter_bar = QWidget()
        filter_bar = QHBoxLayout(self._filter_bar)
        filter_bar.setContentsMargins(0, 4, 0, 4)
        filter_bar.setSpacing(8)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("搜尋檔案、樣品或角色…")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setMaximumWidth(280)
        self._filter_edit.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self._filter_edit)

        self._filter_status_label = QLabel("")
        self._filter_status_label.setStyleSheet("color: #7a828e; font-size: 11px;")
        filter_bar.addWidget(self._filter_status_label)
        filter_bar.addStretch()

        batch_label = QLabel("批次設為:")
        batch_label.setStyleSheet("color: #7a828e; font-weight: 600;")
        filter_bar.addWidget(batch_label)

        self._batch_role_combo = QComboBox()
        for key, display in ROLE_CHOICES:
            self._batch_role_combo.addItem(display, key)
        filter_bar.addWidget(self._batch_role_combo)

        self._apply_batch_button = QPushButton("套用至選取項目")
        self._apply_batch_button.setToolTip("將選取的樣品批次設定為指定角色")
        self._apply_batch_button.clicked.connect(self._on_apply_batch_role)
        filter_bar.addWidget(self._apply_batch_button)
        body.addWidget(self._filter_bar)

        self._table = DropTableWidget(0, len(_TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        self._table.setAlternatingRowColors(True)
        v_header = self._table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.file_dropped.connect(self._import_from_path)
        self._table.setMinimumHeight(120)
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        h_header = self._table.horizontalHeader()
        if h_header is not None:
            h_header.setStretchLastSection(False)
            h_header.setSectionResizeMode(_COL_STEM, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(
                _COL_BATCH, QHeaderView.ResizeMode.Interactive
            )
            h_header.setSectionResizeMode(_COL_ORDER, QHeaderView.ResizeMode.Fixed)
            h_header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
            h_header.setSectionResizeMode(_COL_ROLE, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_BATCH, 130)
        self._table.setColumnWidth(_COL_ORDER, 96)
        self._table.setColumnWidth(_COL_ROLE, 170)
        for column in range(len(_TABLE_HEADERS)):
            header_item = self._table.horizontalHeaderItem(column)
            if header_item is not None:
                alignment = (
                    Qt.AlignmentFlag.AlignCenter
                    if column == _COL_ORDER
                    else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                header_item.setTextAlignment(alignment)
        self._table.itemChanged.connect(self._on_item_changed)
        body.addWidget(self._table, 1)
        self.set_detail_mode(False)

    def set_sources(
        self,
        raw_dir: Path | None,
        dll_dir: Path | None,
        custom_roles: Mapping[str, str] | None = None,
    ) -> None:
        del dll_dir  # Kept in the public signature for the view integration.
        raw_dir_resolved = raw_dir.resolve() if raw_dir and raw_dir.is_dir() else None

        if raw_dir_resolved != self._raw_dir:
            self._custom_roles.clear()
            self._sequence_source = ""
            self._show_sequence_source(None)

        if custom_roles is not None:
            self._custom_roles.update(custom_roles)

        if raw_dir_resolved == self._raw_dir:
            if custom_roles is not None:
                self.load_custom_roles(custom_roles)
            return

        self._raw_dir = raw_dir_resolved

        if self._raw_dir is None:
            self._table.setRowCount(0)
            self._status_label.setText("尚未選取有效的 RAW 資料夾")
            return

        raw_files = sorted(self._raw_dir.glob("*.raw"))

        if not raw_files:
            self._table.setRowCount(0)
            self._status_label.setText(f"資料夾中無 .raw 檔案：{self._raw_dir.name}")
            return

        self._populate_fast_entries(raw_files)
        self._status_label.setText(
            f"已列出 {len(raw_files)} 個 RAW；請匯入 Word 上機資訊設定進樣順序"
        )
        self.changed.emit()

    def clear_all(self) -> None:
        self._custom_roles.clear()
        self._raw_dir = None
        self._sequence_source = ""
        self._show_sequence_source(None)
        self._table.setRowCount(0)
        self._status_label.setText("尚未選取有效的 RAW 資料夾")
        self._filter_edit.clear()
        self.changed.emit()

    def get_sample_metadata(self) -> tuple[SampleMetadata, ...] | None:
        row_count = self._table.rowCount()
        if row_count == 0:
            return None

        results: list[SampleMetadata] = []
        for row in range(row_count):
            stem_item = self._table.item(row, _COL_STEM)
            assert stem_item is not None
            batch_item = self._table.item(row, _COL_BATCH)
            order_item = self._table.item(row, _COL_ORDER)
            name_item = self._table.item(row, _COL_NAME)
            role_combo = self._table.cellWidget(row, _COL_ROLE)

            stem = stem_item.text().strip()
            batch = batch_item.text().strip() if batch_item else ""
            order_text = order_item.text().strip() if order_item else ""
            order = int(order_text) if order_text.isdigit() else None
            name = name_item.text().strip() if name_item else stem
            role: SampleRole = "unknown"
            if isinstance(role_combo, QComboBox):
                data = role_combo.currentData()
                if data:
                    role = cast(SampleRole, str(data))

            results.append(
                replace(
                    cast(SampleMetadata, stem_item.data(Qt.ItemDataRole.UserRole)),
                    sample_name=name or stem,
                    raw_stem=stem,
                    injection_order=order,
                    sample_role=role,
                    batch_id=batch,
                )
            )
        return tuple(results)

    def get_custom_roles(self) -> dict[str, str]:
        return dict(self._custom_roles)

    def get_sequence_source(self) -> str:
        """Return the persisted sequence locator, including a failed restore."""
        return self._sequence_source

    def load_sequence_source(self, path: Path | str) -> bool:
        """Restore a persisted sequence source without opening a modal dialog."""
        sequence_path = Path(path)
        self._sequence_source = str(sequence_path)
        self._show_sequence_source(sequence_path)
        return self._load_sequence_path(
            sequence_path,
            show_error_dialog=False,
            retain_source_on_failure=True,
        )

    def load_custom_roles(self, roles: Mapping[str, str]) -> None:
        self._custom_roles.update(roles)
        for row in range(self._table.rowCount()):
            stem_item = self._table.item(row, _COL_STEM)
            if stem_item and stem_item.text() in self._custom_roles:
                role = self._custom_roles[stem_item.text()]
                self._set_row_role(row, role)

    def set_enabled(self, enabled: bool) -> None:
        self.setEnabled(enabled)

    def set_detail_mode(self, enabled: bool) -> None:
        """Show or hide the in-place editor without rebuilding its state."""
        self._detail_mode = enabled
        self._export_button.setVisible(enabled)
        self._filter_bar.setVisible(enabled)
        self._table.setVisible(enabled)
        self._inspect_button.setVisible(not enabled)

        vertical_policy = (
            QSizePolicy.Policy.Expanding if enabled else QSizePolicy.Policy.Preferred
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, vertical_policy)
        self._card.setSizePolicy(QSizePolicy.Policy.Expanding, vertical_policy)
        self._root_layout.setStretch(0, 1 if enabled else 0)

    # ── Private Helpers ──────────────────────────────────────────────────────

    def _populate_fast_entries(self, raw_files: Sequence[Path]) -> None:
        batch_id = self._raw_dir.name if self._raw_dir else "batch_0"
        with QSignalBlocker(self._table):
            self._table.setRowCount(0)
            for path in raw_files:
                row = self._table.rowCount()
                self._table.insertRow(row)
                stem = path.stem

                stem_item = QTableWidgetItem(stem)
                stem_item.setData(
                    Qt.ItemDataRole.UserRole,
                    SampleMetadata(sample_name=stem, raw_stem=stem),
                )
                stem_item.setFlags(stem_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, _COL_STEM, stem_item)

                batch_item = QTableWidgetItem(batch_id)
                self._table.setItem(row, _COL_BATCH, batch_item)

                order_item = self._order_item("")
                self._table.setItem(row, _COL_ORDER, order_item)

                name_item = QTableWidgetItem(stem)
                self._table.setItem(row, _COL_NAME, name_item)

                role = self._custom_roles.get(stem) or infer_sample_role(
                    "Unknown", sample_name=stem
                )
                role_combo = self._create_role_combo(role, stem)
                self._table.setCellWidget(row, _COL_ROLE, role_combo)

        if self._filter_edit.text():
            self._on_filter_changed(self._filter_edit.text())
        else:
            self._filter_status_label.setText("")

    def _create_role_combo(self, selected_role: str, stem: str) -> QComboBox:
        combo = QComboBox()
        for key, display in ROLE_CHOICES:
            combo.addItem(display, key)
        index = combo.findData(selected_role)
        if index >= 0:
            combo.setCurrentIndex(index)
        else:
            combo.setCurrentIndex(0)

        combo.currentIndexChanged.connect(
            lambda idx, s=stem, c=combo: self._on_role_changed(s, c)
        )
        return combo

    @staticmethod
    def _order_item(value: str) -> QTableWidgetItem:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    def _on_role_changed(self, stem: str, combo: QComboBox) -> None:
        role = str(combo.currentData() or "unknown")
        self._custom_roles[stem] = role
        self.changed.emit()

    def _set_row_role(self, row: int, role: str) -> None:
        combo = self._table.cellWidget(row, _COL_ROLE)
        if isinstance(combo, QComboBox):
            with QSignalBlocker(combo):
                idx = combo.findData(role)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

    def _get_selected_rows(self) -> list[int]:
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return []
        selected_indexes = selection_model.selectedRows()
        if not selected_indexes:
            rows = {idx.row() for idx in selection_model.selectedIndexes()}
        else:
            rows = {idx.row() for idx in selected_indexes}
        return sorted(rows)

    def _on_selection_changed(self) -> None:
        count = len(self._get_selected_rows())
        if count > 0:
            self._apply_batch_button.setText(f"套用至選取項目 ({count} 筆)")
        else:
            self._apply_batch_button.setText("套用至選取項目")

    def _on_apply_batch_role(self) -> None:
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            QMessageBox.information(
                self, "提示", "請先在表格中選取要變更角色的樣品行。"
            )
            return
        role_data = self._batch_role_combo.currentData()
        if not role_data:
            return
        self._set_selected_rows_role(selected_rows, str(role_data))

    def _set_selected_rows_role(self, rows: Sequence[int], role: str) -> None:
        count = 0
        for row in rows:
            stem_item = self._table.item(row, _COL_STEM)
            if stem_item:
                stem = stem_item.text()
                self._custom_roles[stem] = role
                self._set_row_role(row, role)
                count += 1
        self._status_label.setText(f"已將 {count} 筆樣品角色設為 {role}")
        if self._filter_edit.text():
            self._on_filter_changed(self._filter_edit.text())
        self.changed.emit()

    def _on_context_menu(self, pos: QPoint) -> None:
        selected_rows = self._get_selected_rows()
        if not selected_rows:
            item = self._table.itemAt(pos)
            if item is not None:
                row = item.row()
                self._table.selectRow(row)
                selected_rows = [row]
            else:
                return

        menu = QMenu(self)
        title = f"批次設定選取的 {len(selected_rows)} 筆樣品為："
        menu.addSection(title)
        for key, display in ROLE_CHOICES:
            action = menu.addAction(display)
            if action is not None:
                action.triggered.connect(
                    lambda _checked=False, r=key, r_rows=selected_rows: (
                        self._set_selected_rows_role(r_rows, r)
                    )
                )
        viewport = self._table.viewport()
        if viewport is not None:
            menu.exec(viewport.mapToGlobal(pos))

    def _on_filter_changed(self, text: str) -> None:
        query = text.strip().lower()
        total = self._table.rowCount()
        visible = 0
        for row in range(total):
            if not query:
                self._table.setRowHidden(row, False)
                visible += 1
                continue
            stem_item = self._table.item(row, _COL_STEM)
            batch_item = self._table.item(row, _COL_BATCH)
            order_item = self._table.item(row, _COL_ORDER)
            name_item = self._table.item(row, _COL_NAME)
            role_combo = self._table.cellWidget(row, _COL_ROLE)
            role_text = ""
            if isinstance(role_combo, QComboBox):
                role_text = role_combo.currentText()
                data_val = role_combo.currentData()
                if data_val:
                    role_text += f" {data_val}"

            haystack = " ".join(
                [
                    stem_item.text() if stem_item else "",
                    batch_item.text() if batch_item else "",
                    order_item.text() if order_item else "",
                    name_item.text() if name_item else "",
                    role_text,
                ]
            ).lower()

            matched = query in haystack
            self._table.setRowHidden(row, not matched)
            if matched:
                visible += 1

        if query:
            self._filter_status_label.setText(f"顯示 {visible} / {total} 筆")
        else:
            self._filter_status_label.setText("")

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        self.changed.emit()

    def _on_import_sequence(self) -> None:
        filters = (
            "Word 上機資訊 (*.docx);;"
            "相容序列表 (*.xlsx *.csv *.tsv);;"
            "所有支援格式 (*.docx *.xlsx *.csv *.tsv);;"
            "所有檔案 (*.*)"
        )
        path_str, _ = QFileDialog.getOpenFileName(
            self, "匯入 Word 上機資訊", "", filters
        )
        if not path_str:
            return
        self._import_from_path(Path(path_str))

    def _import_from_path(self, path: Path) -> None:
        self._load_sequence_path(
            path,
            show_error_dialog=True,
            retain_source_on_failure=False,
        )
        self.details_requested.emit()

    def _load_sequence_path(
        self,
        path: Path,
        *,
        show_error_dialog: bool,
        retain_source_on_failure: bool,
    ) -> bool:
        current_stems = [
            item.text()
            for row in range(self._table.rowCount())
            if (item := self._table.item(row, _COL_STEM)) is not None and item.text()
        ]

        try:
            if current_stems:
                report = load_and_match_sample_metadata(path, current_stems)
                metadata = report.rows
            else:
                report = None
                metadata = load_sample_metadata(path)
        except Exception as exc:
            if retain_source_on_failure:
                self._sequence_source = str(path)
                self._clear_injection_orders()
            raw_count = self._table.rowCount()
            count_suffix = f"（{raw_count} 個 RAW）" if raw_count else ""
            message = f"無法載入上機資訊《{path.name}》{count_suffix}"
            self._status_label.setText(message)
            self._status_label.setToolTip(f"來源：{path}\n錯誤：{exc}")
            if show_error_dialog:
                QMessageBox.warning(self, "匯入失敗", f"{message}：{exc}")
            if retain_source_on_failure:
                self.changed.emit()
            return False

        self._sequence_source = str(path)
        self._show_sequence_source(path)
        if current_stems and report is not None:
            self._apply_matched_metadata(metadata)
            self._status_label.setText(
                f"已匯入：匹配 {report.matched_count}/{len(current_stems)}、"
                f"未匹配 RAW {len(report.unmatched_raw_stems)}、"
                f"歧義 {len(report.ambiguous_sequence_names)}"
            )
            details = [
                f"來源：{path}",
                f"匹配 RAW：{report.matched_count}",
                f"未匹配 RAW：{len(report.unmatched_raw_stems)}",
                f"歧義序列：{len(report.ambiguous_sequence_names)}",
                f"序列未對應 RAW：{len(report.unmatched_sequence_names)}",
            ]
            if report.unmatched_raw_stems:
                details.append(
                    "未匹配 RAW：" + self._preview_names(report.unmatched_raw_stems)
                )
            if report.ambiguous_sequence_names:
                details.append(
                    "歧義序列：" + self._preview_names(report.ambiguous_sequence_names)
                )
            if report.unmatched_sequence_names:
                details.append(
                    "序列未對應 RAW："
                    + self._preview_names(report.unmatched_sequence_names)
                )
            self._status_label.setToolTip("\n".join(details))
        else:
            self._replace_table_metadata(metadata)
            self._status_label.setText(
                f"已載入 {len(metadata)} 筆上機資訊（尚無 RAW 可匹配）"
            )
            self._status_label.setToolTip(f"來源：{path}")

        if self._filter_edit.text():
            self._on_filter_changed(self._filter_edit.text())
        self.changed.emit()
        return True

    def _show_sequence_source(self, path: Path | None) -> None:
        if path is None:
            self._source_label.setText("Word 來源：尚未匯入")
            self._source_label.setToolTip("")
            return
        self._source_label.setText(f"Word 來源：{path.name}")
        self._source_label.setToolTip(str(path))

    def _apply_matched_metadata(
        self,
        metadata: Sequence[SampleMetadata],
    ) -> None:
        matched_by_stem = {row.raw_stem: row for row in metadata}
        with QSignalBlocker(self._table):
            for row in range(self._table.rowCount()):
                stem_item = self._table.item(row, _COL_STEM)
                if stem_item is None:
                    continue
                stem = stem_item.text()
                matched = matched_by_stem.get(stem)
                if matched is None:
                    continue

                stem_item.setData(Qt.ItemDataRole.UserRole, matched)
                order = (
                    ""
                    if matched.injection_order is None
                    else str(matched.injection_order)
                )
                self._table.setItem(row, _COL_ORDER, self._order_item(order))
                self._table.setItem(
                    row,
                    _COL_NAME,
                    QTableWidgetItem(matched.sample_name or stem),
                )
                if matched.batch_id:
                    self._table.setItem(
                        row,
                        _COL_BATCH,
                        QTableWidgetItem(matched.batch_id),
                    )
                role = self._custom_roles.get(stem, matched.sample_role)
                self._set_row_role(row, role)

    def _replace_table_metadata(
        self,
        metadata: Sequence[SampleMetadata],
    ) -> None:
        with QSignalBlocker(self._table):
            self._table.setRowCount(0)
            for row, sample in enumerate(metadata):
                self._table.insertRow(row)
                stem_item = QTableWidgetItem(sample.raw_stem)
                stem_item.setData(Qt.ItemDataRole.UserRole, sample)
                stem_item.setFlags(stem_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, _COL_STEM, stem_item)
                self._table.setItem(
                    row,
                    _COL_BATCH,
                    QTableWidgetItem(sample.batch_id),
                )
                order = (
                    ""
                    if sample.injection_order is None
                    else str(sample.injection_order)
                )
                self._table.setItem(row, _COL_ORDER, self._order_item(order))
                self._table.setItem(
                    row,
                    _COL_NAME,
                    QTableWidgetItem(sample.sample_name),
                )
                role = self._custom_roles.get(sample.raw_stem, sample.sample_role)
                self._table.setCellWidget(
                    row,
                    _COL_ROLE,
                    self._create_role_combo(role, sample.raw_stem),
                )

    def _clear_injection_orders(self) -> None:
        with QSignalBlocker(self._table):
            for row in range(self._table.rowCount()):
                self._table.setItem(row, _COL_ORDER, self._order_item(""))

    @staticmethod
    def _preview_names(names: Sequence[str]) -> str:
        preview = ", ".join(names[:8])
        if len(names) > 8:
            return f"{preview} …（另 {len(names) - 8} 筆）"
        return preview

    def _on_export_csv(self) -> None:
        metadata = self.get_sample_metadata()
        if not metadata:
            QMessageBox.information(self, "提示", "表格內無樣品資訊可供匯出。")
            return

        path_str, _ = QFileDialog.getSaveFileName(
            self, "匯出樣品元數據 CSV", "sample_metadata.csv", "CSV Files (*.csv)"
        )
        if not path_str:
            return
        path = Path(path_str)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(SAMPLE_METADATA_COLUMNS)
                for row in metadata:
                    writer.writerow(
                        [
                            SAMPLE_METADATA_SCHEMA_VERSION,
                            row.sample_name,
                            row.raw_stem,
                            ""
                            if row.injection_order is None
                            else str(row.injection_order),
                            row.sample_role,
                            row.batch_id,
                            row.prep_batch_id,
                            row.matrix_type,
                            row.group,
                            "true" if row.excluded else "false",
                            row.exclusion_reason,
                        ]
                    )
            self._status_label.setText(f"已成功匯出至 {path.name}")
        except Exception as exc:
            QMessageBox.warning(self, "匯出失敗", f"無法寫入檔案：{exc}")
