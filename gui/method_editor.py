"""Edit Discovery intent and save it through the shared preset owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from xic_extractor.presets import Preset, PresetError, PresetTag, apply_to_discovery
from xic_extractor.presets.models import CombineMode
from xic_extractor.presets.serialization import save_preset
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec


class MethodEditorDialog(QDialog):
    """Keep edits local until the operator successfully saves a method file."""

    def __init__(
        self,
        preset: Preset,
        *,
        discovery_overrides: Mapping[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Discovery 方法")
        self.resize(620, 460)
        self._original = preset
        self._overrides = dict(discovery_overrides or {})
        self.saved_path: Path | None = None

        layout = QVBoxLayout(self)
        heading = QLabel("設定要尋找的 tag 與組合方式")
        layout.addWidget(heading)
        form = QFormLayout()
        self._name = QLineEdit(preset.name)
        self._name.setObjectName("method_name")
        self._description = QLineEdit(preset.description)
        self._description.setObjectName("method_description")
        form.addRow("方法名稱", self._name)
        form.addRow("說明", self._description)
        layout.addLayout(form)

        self._tags = QTableWidget(0, 2)
        self._tags.setObjectName("method_tags")
        self._tags.setHorizontalHeaderLabels(["Tag 名稱", "Neutral loss (Da)"])
        header = self._tags.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tags.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tags.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._tags, 1)
        for tag in preset.tags:
            self.add_tag(name=tag.name, value=tag.value)

        tag_buttons = QHBoxLayout()
        add = QPushButton("新增 tag")
        add.setObjectName("add_method_tag")
        add.clicked.connect(lambda: self.add_tag())
        remove = QPushButton("移除所選 tag")
        remove.setObjectName("remove_method_tag")
        remove.clicked.connect(self._remove_selected_tag)
        tag_buttons.addWidget(add)
        tag_buttons.addWidget(remove)
        tag_buttons.addStretch()
        layout.addLayout(tag_buttons)

        self._combine = QComboBox()
        self._combine.setObjectName("method_combine")
        self._combine.addItem("單一 tag", "single")
        self._combine.addItem("OR — 同一個峰符合任一 tag", "union")
        self._combine.addItem("AND — 同一個峰符合全部 tag", "intersection")
        self._combine.setCurrentIndex(self._combine.findData(preset.combine_mode))
        layout.addWidget(self._combine)
        help_text = QLabel(
            "方法檔可直接交給 CLI 使用。進階 Discovery 參數會一起保存；"
            "跨樣本 Alignment 與 Backfill 由平台處理。"
        )
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self._error = QLabel()
        self._error.setObjectName("method_validation_error")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert cancel is not None
        cancel.setText("取消")
        save = buttons.addButton("另存方法…", QDialogButtonBox.ButtonRole.AcceptRole)
        assert save is not None
        save.setObjectName("save_method")
        save.clicked.connect(self._choose_destination)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_tag(self, *, name: str = "", value: float | None = None) -> None:
        row = self._tags.rowCount()
        self._tags.insertRow(row)
        self._tags.setItem(row, 0, QTableWidgetItem(name))
        # Text preserves imported precision until an operator explicitly edits it.
        self._tags.setItem(
            row, 1, QTableWidgetItem("" if value is None else repr(value))
        )
        self._tags.selectRow(row)

    def _remove_selected_tag(self) -> None:
        row = self._tags.currentRow()
        if row >= 0:
            self._tags.removeRow(row)

    def _edited_preset(self) -> Preset:
        tags: list[PresetTag] = []
        for row in range(self._tags.rowCount()):
            name_item = self._tags.item(row, 0)
            value_item = self._tags.item(row, 1)
            name = name_item.text() if name_item is not None else ""
            value_text = value_item.text().strip() if value_item is not None else ""
            try:
                value = float(value_text)
            except ValueError as exc:
                raise PresetError(
                    f"第 {row + 1} 個 tag 需要有效的 neutral loss 數值"
                ) from exc
            tags.append(PresetTag(strategy="neutral_loss", name=name, value=value))
        edited = replace(
            self._original,
            name=self._name.text(),
            description=self._description.text(),
            tags=tuple(tags),
            combine_mode=cast(CombineMode, self._combine.currentData()),
        )
        # Use the same method-identity validation as real Discovery before saving.
        CidNlDiscoveryMethodSpec(
            settings=apply_to_discovery(
                edited, explicit_tuning_overrides=self._overrides
            )
        )
        return edited

    def validation_message(self) -> str:
        return self._error.text()

    def save_to(self, path: Path) -> bool:
        try:
            preset = self._edited_preset()
            saved_path = path.resolve()
            save_preset(preset, saved_path, discovery_overrides=self._overrides)
        except (PresetError, ValueError, TypeError, OSError) as exc:
            self._error.setText(f"方法未保存：{exc}")
            return False
        self.saved_path = saved_path
        self._error.clear()
        self.accept()
        return True

    def _choose_destination(self) -> None:
        try:
            self._edited_preset()
        except (PresetError, ValueError, TypeError) as exc:
            self._error.setText(f"請先修正方法：{exc}")
            return
        chosen, _ = QFileDialog.getSaveFileName(
            self, "另存 Discovery 方法", "method.toml", "TOML 方法 (*.toml)"
        )
        if chosen:
            path = Path(chosen)
            if not path.suffix:
                path = path.with_suffix(".toml")
            self.save_to(path)
