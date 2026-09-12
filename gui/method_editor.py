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

from gui import styles
from xic_extractor.presets import Preset, PresetError, PresetTag, apply_to_discovery
from xic_extractor.presets.models import CombineMode
from xic_extractor.presets.serialization import save_preset, serialize_preset
from xic_extractor.processing_run_spec import CidNlDiscoveryMethodSpec

CID_NL_TAGS = (
    ("DNA_dR", 116.0474, "dR"),
    ("RNA_ribose", 132.0423, "R"),
    ("RNA_MeR", 146.0579, "MeR"),
)


def validate_gui_method(
    preset: Preset, overrides: Mapping[str, object] | None = None
) -> None:
    """Reject unsupported projections before the GUI can replace method identity."""
    if any(
        tag.strategy != "neutral_loss" or tag.pattern_id is not None
        for tag in preset.tags
    ):
        raise PresetError("GUI 僅支援純 CID-NL 方法；原方法未變更")
    settings = apply_to_discovery(preset, explicit_tuning_overrides=overrides)
    CidNlDiscoveryMethodSpec(settings=settings)
    if not 0 <= settings.rt_min <= settings.rt_max <= 60:
        raise PresetError("GUI 的 RT 範圍為 0–60 min；原方法未變更")
    serialize_preset(preset, discovery_overrides=overrides)


class MethodEditorDialog(QDialog):
    """Keep edits local until Apply; saving a reusable file is independent."""

    def __init__(
        self,
        preset: Preset,
        *,
        discovery_overrides: Mapping[str, object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CID-NL · 方法編輯器")
        self.resize(960, 720)
        self.setMinimumSize(760, 580)
        self.setSizeGripEnabled(True)
        self._original = preset
        self._overrides = dict(discovery_overrides or {})
        if preset.source.startswith("builtin:") or preset.source == "gui:new":
            self._overrides.setdefault(
                "rt_max", preset.discovery_overrides.get("rt_max", 60.0)
            )
        self.saved_path: Path | None = None
        self.applied_preset: Preset | None = None
        self._baseline = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        heading = QLabel("CID-NL 方法")
        heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(heading)
        layout.addWidget(QLabel("選擇中性丟失標籤，調整質量與組合方式。"))
        root = layout
        columns = QHBoxLayout()
        columns.setSpacing(24)
        root.addLayout(columns, 1)
        layout = QVBoxLayout()
        layout.setSpacing(16)
        columns.addLayout(layout, 3)
        summary_panel = QWidget()
        summary_panel.setMinimumWidth(245)
        summary_panel.setMaximumWidth(320)
        summary_layout = QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.addWidget(QLabel("目前設定"))
        columns.addWidget(summary_panel, 2)
        form = QFormLayout()
        self._name = QLineEdit(preset.name)
        self._name.setObjectName("method_name")
        self._description = QLineEdit(preset.description)
        self._description.setObjectName("method_description")
        form.addRow("方法名稱", self._name)
        form.addRow("說明", self._description)
        layout.addLayout(form)

        layout.addWidget(QLabel("中性丟失標籤 · 勾選啟用，雙擊編輯"))
        self._tags = QTableWidget(0, 2)
        self._tags.setObjectName("method_tags")
        self._tags.setHorizontalHeaderLabels(["啟用 / 標籤", "中性丟失 NL (Da)"])
        header = self._tags.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._tags.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tags.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        vertical_header = self._tags.verticalHeader()
        assert vertical_header is not None
        vertical_header.hide()
        vertical_header.setDefaultSectionSize(42)
        self._tags.setMinimumHeight(160)
        layout.addWidget(self._tags, 1)
        for tag in preset.tags:
            self.add_tag(name=tag.name, value=tag.value)
        original_names = {tag.name for tag in preset.tags}
        for name, value, _label in CID_NL_TAGS:
            if name not in original_names:
                self.add_tag(name=name, value=value, selected=False)
        self._tags.clearSelection()
        self._tags.setCurrentCell(-1, -1)

        tag_buttons = QHBoxLayout()
        add = QPushButton("＋ 自訂 NL")
        add.setObjectName("add_method_tag")
        add.clicked.connect(lambda: self.add_tag())
        remove = QPushButton("移除所選")
        remove.setObjectName("remove_method_tag")
        remove.clicked.connect(self._remove_selected_tag)
        tag_buttons.addWidget(add)
        tag_buttons.addWidget(remove)
        tag_buttons.addStretch()
        layout.addLayout(tag_buttons)

        self._combine = QComboBox()
        self._combine.setObjectName("method_combine")
        self._combine.addItem("單一標籤", "single")
        self._combine.addItem("任一符合（OR）· 同一個峰符合任一標籤", "union")
        self._combine.addItem("全部符合（AND）· 同一個峰符合全部標籤", "intersection")
        self._combine.setCurrentIndex(self._combine.findData(preset.combine_mode))
        layout.addWidget(self._combine)
        self._summary = QLabel()
        self._summary.setObjectName("method_live_summary")
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        self._summary.setStyleSheet(
            f"background: {styles.ACTIVE['primary_soft']}; "
            f"color: {styles.ACTIVE['text']}; padding: 16px; border-radius: 8px;"
        )
        self._summary.setAlignment(Qt.AlignmentFlag.AlignTop)
        summary_layout.addWidget(self._summary, 1)
        self._state = QLabel("已套用 · 尚無變更")
        self._state.setObjectName("method_edit_state")
        self._state.setWordWrap(True)
        summary_layout.addWidget(self._state)
        self._error = QLabel()
        self._error.setObjectName("method_validation_error")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        summary_layout.addWidget(self._error)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert cancel is not None
        cancel.setText("取消")
        save = buttons.addButton(
            "另存為 preset…", QDialogButtonBox.ButtonRole.ActionRole
        )
        assert save is not None
        save.setObjectName("save_method")
        save.clicked.connect(self._choose_destination)
        apply = buttons.addButton(
            "套用至本次執行", QDialogButtonBox.ButtonRole.AcceptRole
        )
        assert apply is not None
        apply.setObjectName("apply_method")
        apply.setDefault(True)
        apply.clicked.connect(self.apply_current)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._baseline = self._fingerprint()
        self._name.textChanged.connect(self._refresh_summary)
        self._description.textChanged.connect(self._refresh_summary)
        self._tags.itemChanged.connect(self._tag_changed)
        self._combine.currentIndexChanged.connect(self._refresh_summary)
        self._refresh_summary()

    def _select_tag(self, name: str, value: float) -> None:
        for row in range(self._tags.rowCount()):
            item = self._tags.item(row, 0)
            if item is not None and item.text() == name:
                self._tags.selectRow(row)
                item.setCheckState(Qt.CheckState.Checked)
                return
        self.add_tag(name=name, value=value)
        if self._tags.rowCount() > 1 and self._combine.currentData() == "single":
            self._combine.setCurrentIndex(self._combine.findData("union"))

    def _tag_changed(self, item: QTableWidgetItem) -> None:
        selected = sum(
            tag_item.checkState() == Qt.CheckState.Checked
            for row in range(self._tags.rowCount())
            if (tag_item := self._tags.item(row, 0)) is not None
        )
        if (
            item.column() == 0
            and selected > 1
            and self._combine.currentData() == "single"
        ):
            self._combine.setCurrentIndex(self._combine.findData("union"))
        self._refresh_summary()

    def _fingerprint(self) -> tuple:
        return (
            self._name.text(),
            self._description.text(),
            self._combine.currentData(),
            tuple(
                tuple(
                    item.text() if (item := self._tags.item(r, c)) is not None else ""
                    for c in range(2)
                )
                for r in range(self._tags.rowCount())
            ),
            tuple(
                item.checkState().value
                if (item := self._tags.item(r, 0)) is not None
                else 0
                for r in range(self._tags.rowCount())
            ),
        )

    def _refresh_summary(self, *_args: object) -> None:
        dirty = self._fingerprint() != self._baseline
        self._state.setText("有未套用的變更" if dirty else "目前方法 · 尚無變更")
        try:
            preset = self._edited_preset()
            settings = apply_to_discovery(
                preset, explicit_tuning_overrides=self._overrides
            )
            tags = "\n".join(f"{t.name}  ·  {t.value} Da" for t in preset.tags)
            mode = {
                "single": "單一標籤",
                "union": "任一符合 OR",
                "intersection": "全部符合 AND",
            }[preset.combine_mode]
            self._summary.setText(
                f"CID-NL\n\n{mode}\n\n{tags}\n\n"
                f"RT {settings.rt_min:g}–{settings.rt_max:g} min\n"
                f"NL 容差 ±{settings.nl_tolerance_ppm:g} ppm\n"
                f"前驅離子容差 ±{settings.precursor_mz_tolerance_ppm:g} ppm"
            )
            self._error.clear()
        except (ValueError, TypeError) as exc:
            self._summary.setText(f"尚未就緒：{exc}")

    def apply_current(self) -> bool:
        try:
            preset = self._edited_preset()
        except (ValueError, TypeError) as exc:
            self._error.setText(f"方法未套用：{exc}")
            return False
        self.applied_preset = replace(
            preset,
            discovery_overrides={**preset.discovery_overrides, **self._overrides},
        )
        self._state.setText("已套用至本次執行")
        self.accept()
        return True

    def add_tag(
        self, *, name: str = "", value: float | None = None, selected: bool = True
    ) -> None:
        row = self._tags.rowCount()
        self._tags.insertRow(row)
        item = QTableWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked
        )
        self._tags.setItem(row, 0, item)
        # Text preserves imported precision until an operator explicitly edits it.
        self._tags.setItem(
            row, 1, QTableWidgetItem("" if value is None else repr(value))
        )
        self._tags.selectRow(row)
        if self._baseline is not None:
            self._refresh_summary()

    def _remove_selected_tag(self) -> None:
        row = self._tags.currentRow()
        if row >= 0:
            self._tags.removeRow(row)
            self._refresh_summary()

    def _edited_preset(self) -> Preset:
        if any(
            tag.strategy != "neutral_loss" or tag.pattern_id is not None
            for tag in self._original.tags
        ):
            raise PresetError("GUI 僅支援純 CID-NL 方法；原方法未變更")
        tags: list[PresetTag] = []
        for row in range(self._tags.rowCount()):
            name_item = self._tags.item(row, 0)
            if name_item is None or name_item.checkState() != Qt.CheckState.Checked:
                continue
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
        validate_gui_method(edited, self._overrides)
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
        self._state.setText("Preset 已另存；套用後才會更新本次執行")
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
