"""One-shot Inspector selection and output settings."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from xic_inspector.api import InspectorError, SelectionRequest


class InspectorExportDialog(QDialog):
    def __init__(self, default_output: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("匯出 XIC Inspector")
        self.resize(620, 660)
        self.open_after_export = False
        self.selection: SelectionRequest | None = None
        self.output_path: Path | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.strategy = QComboBox()
        for key, label in (
            ("sample", "Sample"), ("attention", "Attention"), ("query", "Query")
        ):
            self.strategy.addItem(label, key)
        self.strategy.currentIndexChanged.connect(self._update_strategy)
        form.addRow("策略", self.strategy)

        self.limit = QSpinBox()
        self.limit.setRange(1, 50)
        self.limit.setValue(30)
        form.addRow("上限 (1–50)", self.limit)
        self.seed = QLineEdit("0")
        form.addRow("Seed", self.seed)

        self.samples = QListWidget()
        self.samples.setMinimumHeight(100)
        self.samples.setEnabled(False)
        self.sample_status = QLabel("正在讀取來源樣本…")
        form.addRow("樣本 (stem / canonical ID)", self.samples)
        form.addRow("", self.sample_status)

        self.candidates = QLineEdit()
        self.groups = QLineEdit()
        self.mz_min = QLineEdit()
        self.mz_max = QLineEdit()
        self.rt_min = QLineEdit()
        self.rt_max = QLineEdit()
        self.offset = QLineEdit("0")
        for label, widget in (
            ("Candidate IDs (逗號分隔)", self.candidates),
            ("Group IDs (逗號分隔)", self.groups),
            ("m/z 下界", self.mz_min),
            ("m/z 上界", self.mz_max),
            ("RT 下界 (min)", self.rt_min),
            ("RT 上界 (min)", self.rt_max),
            ("Query offset", self.offset),
        ):
            form.addRow(label, widget)
        self._query_fields = (
            self.candidates, self.groups, self.mz_min, self.mz_max,
            self.rt_min, self.rt_max, self.offset,
        )

        output_row = QHBoxLayout()
        self.output = QLineEdit(str(default_output))
        browse = QPushButton("瀏覽…")
        browse.clicked.connect(self._browse)
        output_row.addWidget(self.output)
        output_row.addWidget(browse)
        form.addRow("HTML 路徑", output_row)
        layout.addLayout(form)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        export = QPushButton("Export")
        export.setEnabled(False)
        export.clicked.connect(lambda: self._submit(False))
        export_open = QPushButton("Export and Open")
        export_open.setEnabled(False)
        export_open.clicked.connect(lambda: self._submit(True))
        buttons.addStretch()
        for button in (cancel, export, export_open):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._update_strategy()

    def set_samples(self, summary: dict) -> None:
        self.samples.clear()
        for sample in summary.get("samples", ()):
            stem = sample.get("sample_stem")
            ident = sample.get("canonical_sample_identity_id")
            if not isinstance(stem, str) or not isinstance(ident, str) or not ident:
                continue
            item = QListWidgetItem(f"{stem} · {ident}")
            item.setData(Qt.ItemDataRole.UserRole, ident)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.samples.addItem(item)
        self.samples.setEnabled(True)
        self.sample_status.setText(
            f"已載入 {self.samples.count()} 個來源樣本；不選為全體"
        )
        for button in self.findChildren(QPushButton):
            if button.text() in ("Export", "Export and Open"):
                button.setEnabled(True)

    def set_source_error(self, message: str) -> None:
        self.samples.clear()
        self.samples.setEnabled(False)
        self.sample_status.setText(f"來源不可用：{message}")
        self.error.setText("來源不可用，無法匯出。")
        for button in self.findChildren(QPushButton):
            if button.text() in ("Export", "Export and Open"):
                button.setEnabled(False)

    def _update_strategy(self) -> None:
        query = self.strategy.currentData() == "query"
        for widget in self._query_fields:
            widget.setEnabled(query)

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, "儲存 XIC Inspector HTML", self.output.text(), "HTML (*.html)"
        )
        if chosen:
            self.output.setText(chosen)

    def _submit(self, open_after_export: bool) -> None:
        try:
            path_text = self.output.text().strip()
            if not path_text:
                raise ValueError("請選擇 HTML 輸出路徑")
            strategy = self.strategy.currentData()
            query = strategy == "query"
            selection = SelectionRequest(
                strategy=strategy,
                limit=self.limit.value(),
                seed=_integer(self.seed.text(), "Seed"),
                sample_ids=tuple(
                    self.samples.item(index).data(Qt.ItemDataRole.UserRole)
                    for index in range(self.samples.count())
                    if self.samples.item(index).checkState() == Qt.CheckState.Checked
                ),
                candidate_ids=_ids(self.candidates.text()) if query else (),
                group_ids=_ids(self.groups.text()) if query else (),
                mz_min=_number(self.mz_min.text()) if query else None,
                mz_max=_number(self.mz_max.text()) if query else None,
                rt_min=_number(self.rt_min.text()) if query else None,
                rt_max=_number(self.rt_max.text()) if query else None,
                offset=_integer(self.offset.text(), "Query offset") if query else 0,
            )
            selection.validate()
        except (ValueError, InspectorError) as exc:
            self.error.setText(str(exc))
            return
        self.selection = selection
        self.output_path = Path(path_text)
        self.open_after_export = open_after_export
        self.accept()


def _ids(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    parts = tuple(text.split(","))
    if any(not part.strip() for part in parts):
        raise ValueError("ID 清單包含空值，請輸入完整 canonical ID")
    return parts


def _number(text: str) -> float | None:
    return float(text.strip()) if text.strip() else None


def _integer(text: str, label: str) -> int:
    try:
        return int(text.strip())
    except ValueError as exc:
        raise ValueError(f"{label} 必須是整數") from exc
