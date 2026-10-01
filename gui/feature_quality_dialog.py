"""Export an analysis set through the same finalized-matrix owner as the CLI."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.ui import DropLineEdit
from xic_extractor.feature_quality_io import export_feature_quality


class FeatureQualityDialog(QDialog):
    def __init__(
        self, matrix: Path, identity: Path, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("篩選並匯出分析矩陣")
        self.resize(620, 340)
        self._matrix = matrix
        self._identity = identity
        self._worker: _ExportWorker | None = None
        self.manifest_path: Path | None = None

        layout = QVBoxLayout(self)
        description = QLabel(
            "依研究樣本的有效數值比例篩選特徵。0 算有效值；空白不算。\n"
            "QC、blank 與排除樣本不計入比例；原始矩陣會保留。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        source = QLabel(f"來源：{matrix}")
        source.setWordWrap(True)
        layout.addWidget(source)

        self._inputs = QWidget()
        form = QFormLayout(self._inputs)
        form.setContentsMargins(0, 12, 0, 12)
        self.metadata = DropLineEdit()
        self.metadata.setPlaceholderText("在樣品表匯出 TSV，或選擇已有的樣品資料 TSV")
        metadata_row = QHBoxLayout()
        metadata_row.addWidget(self.metadata)
        browse_metadata = QPushButton("選擇…")
        browse_metadata.clicked.connect(self._browse_metadata)
        metadata_row.addWidget(browse_metadata)
        form.addRow("樣品資料 TSV", metadata_row)
        self.threshold = QLineEdit()
        self.threshold.setPlaceholderText("請指定 0 至 1；例如 0.8 表示至少 80%")
        form.addRow("最低有效值比例", self.threshold)
        self.output = DropLineEdit()
        self.output.setText(str(matrix.parent / "feature_quality"))
        output_row = QHBoxLayout()
        output_row.addWidget(self.output)
        browse_output = QPushButton("選擇位置…")
        browse_output.clicked.connect(self._browse_output)
        output_row.addWidget(browse_output)
        form.addRow("新輸出資料夾", output_row)
        layout.addWidget(self._inputs)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._cancel = QPushButton("取消")
        self._cancel.setAutoDefault(False)
        self._cancel.clicked.connect(self.reject)
        self.export_button = QPushButton("匯出並開啟資料夾")
        self.export_button.setDefault(True)
        self.export_button.clicked.connect(self._export)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)

    def _browse_metadata(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "選擇樣品資料 TSV", self.metadata.text(), "TSV (*.tsv)"
        )
        if path:
            self.metadata.setText(path)

    def _browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "選擇輸出位置", str(Path(self.output.text()).parent)
        )
        if directory:
            self.output.setText(str(Path(directory) / "feature_quality"))

    def _export(self) -> None:
        if self._worker is not None:
            return
        try:
            threshold = float(self.threshold.text())
            if not 0 <= threshold <= 1:
                raise ValueError
        except ValueError:
            self.error.setText("請指定 0 至 1 的最低有效值比例。")
            return
        if not self.metadata.text().strip() or not self.output.text().strip():
            self.error.setText("請選擇樣品資料 TSV 與新輸出資料夾。")
            return
        self.error.setText("正在匯出分析矩陣…")
        self._inputs.setEnabled(False)
        self.export_button.setEnabled(False)
        self._cancel.setEnabled(False)
        self._worker = _ExportWorker(
            self._matrix, self._identity, Path(self.metadata.text().strip()),
            Path(self.output.text().strip()), threshold, self,
        )
        self._worker.finished.connect(self._finished)
        self._worker.start()

    def _finished(self) -> None:
        worker = self._worker
        assert worker is not None
        self._worker = None
        self._inputs.setEnabled(True)
        self.export_button.setEnabled(True)
        self._cancel.setEnabled(True)
        self.manifest_path = worker.manifest_path
        self.error.setText(worker.error)
        worker.deleteLater()
        if self.manifest_path is not None:
            self.accept()

    def reject(self) -> None:
        if self._worker is None:
            super().reject()

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if self._worker is not None and event is not None:
            event.ignore()
        else:
            super().closeEvent(event)


class _ExportWorker(QThread):
    def __init__(
        self, matrix: Path, identity: Path, metadata: Path, output: Path,
        threshold: float, parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._matrix, self._identity = matrix, identity
        self._metadata, self._output = metadata, output
        self._threshold = threshold
        self.manifest_path: Path | None = None
        self.error = ""

    def run(self) -> None:
        try:
            self.manifest_path = export_feature_quality(
                matrix_path=self._matrix,
                identity_path=self._identity,
                sample_metadata_path=self._metadata,
                output_dir=self._output,
                min_finite_fraction=self._threshold,
            )
        except Exception as exc:
            self.error = f"匯出失敗：{exc}"
