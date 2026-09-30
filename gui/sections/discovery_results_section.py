from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui import styles
from gui.inspector_export_dialog import InspectorExportDialog
from gui.ui import section_card
from gui.workers.inspector_report_worker import (
    InspectorJob,
    InspectorReportWorker,
    InspectorSummaryWorker,
)
from xic_inspector.api import EvidenceLocator, ReportResult, SelectionRequest

_PER_SAMPLE_HEADERS = ("樣本", "候選", "高", "中", "低")
_INSPECTOR_FILENAME = "xic_inspector.html"

_GALLERY_TOOLTIP = (
    "開啟 backfill + MS1 overlay 審閱 gallery（解釋 why-backfill 並比對 MS1 峰形）。"
)

# Confidence tiers resolve to live palette tokens (success / warning / neutral)
# at render time, so the dashboard's segments and legend follow the active
# light/dark theme instead of freezing to a light-palette hex.
_TIER_TOKENS = {
    "HIGH": "success",
    "MEDIUM": "warning",
    "LOW": "text_muted",
}
_TIER_LABELS = (("HIGH", "高信心"), ("MEDIUM", "中信心"), ("LOW", "低信心"))


class DiscoveryResultsSection(QWidget):
    status_message = pyqtSignal(str, int)

    def __init__(self) -> None:
        super().__init__()
        self._output_dir = ""
        self._gallery = ""
        self._matrix = ""
        self._alignment_outputs: dict[str, str] = {}
        self._summary_text = "尚未執行"
        self._evidence: EvidenceLocator | None = None
        self._generation = 0
        self._report_worker: InspectorReportWorker | None = None
        self._summary_worker: InspectorSummaryWorker | None = None
        self._report_target: Path | None = None
        self.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        card, body = section_card(
            "分析結果",
            "",
        )
        self._title = card.findChild(QLabel, "section_title")
        assert self._title is not None

        self._empty = QLabel("尚未執行，完成後這裡會顯示候選統計與輸出。")
        self._empty.setObjectName("section_subtitle")
        self._empty.setWordWrap(True)
        body.addWidget(self._empty)

        self._dashboard = self._build_dashboard()
        self._dashboard.setVisible(False)
        body.addWidget(self._dashboard)

        root.addWidget(card)

    # ── Construction helpers ────────────────────────────────────────────────

    def _build_dashboard(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self._stat_samples = _StatCard("樣本數")
        self._stat_total = _StatCard("總候選")
        self._stat_mode = _StatCard("模式")
        for card in (self._stat_samples, self._stat_total, self._stat_mode):
            stats_row.addWidget(card)
        layout.addLayout(stats_row)

        dist_label = QLabel("候選信心分佈")
        dist_label.setObjectName("field_label")
        layout.addWidget(dist_label)
        self._distribution = _DistributionBar()
        layout.addWidget(self._distribution)

        self._sample_heading = QLabel("每樣本明細")
        self._sample_heading.setObjectName("field_label")
        self._sample_heading.setVisible(False)
        layout.addWidget(self._sample_heading)
        self._sample_table = _build_sample_table()
        self._sample_table.setVisible(False)
        layout.addWidget(self._sample_table)

        outputs_label = QLabel("輸出")
        outputs_label.setObjectName("field_label")
        layout.addWidget(outputs_label)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._open_output = QPushButton("開啟輸出資料夾")
        self._open_output.clicked.connect(lambda: _open_path(self._output_dir))
        self._open_gallery = QPushButton("開啟審閱圖集")
        self._open_gallery.setToolTip(_GALLERY_TOOLTIP)
        self._open_gallery.clicked.connect(lambda: _open_path(self._gallery))
        self._open_matrix = QPushButton("開啟定量矩陣")
        self._open_matrix.clicked.connect(lambda: _open_path(self._matrix))
        self._buttons = (self._open_output, self._open_gallery, self._open_matrix)
        for button in self._buttons:
            button.setEnabled(False)
            button_row.addWidget(button)
        self._open_inspector = QPushButton("開啟 Inspector")
        self._open_inspector.setObjectName("open_inspector")
        self._open_inspector.setToolTip("在瀏覽器開啟本次結果的曲線檢閱頁")
        self._open_inspector.clicked.connect(
            lambda: _open_path(str(self._report_target or ""))
        )
        button_row.addWidget(self._open_inspector)
        button_row.addStretch()
        layout.addLayout(button_row)

        inspector_row = QHBoxLayout()
        self._inspector_status = QLabel("尚無 Inspector 來源")
        self._inspector_status.setObjectName("inspector_status")
        self._inspector_status.setWordWrap(True)
        inspector_row.addWidget(self._inspector_status, 1)
        self._cancel_inspector = QPushButton("取消")
        self._cancel_inspector.setObjectName("cancel_inspector")
        self._cancel_inspector.clicked.connect(self.cancel_report)
        self._export_inspector = QPushButton("自訂匯出…")
        self._export_inspector.setObjectName("export_inspector")
        self._export_inspector.setToolTip(
            "選擇抽樣方式、數量或指定樣本後另存 Inspector"
        )
        self._export_inspector.clicked.connect(self._export)
        inspector_row.addWidget(self._cancel_inspector)
        inspector_row.addWidget(self._export_inspector)
        layout.addLayout(inspector_row)
        self._refresh_inspector_buttons()

        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        return container

    # ── Public API ──────────────────────────────────────────────────────────

    def update_results(self, summary: dict[str, Any]) -> None:
        self._generation += 1
        self._report_target = None
        self._evidence, reason = _locator_from_summary(summary.get("evidence"))
        self._inspector_status.setText(
            "Inspector 尚未產生" if self._evidence else reason
        )
        self._inspector_status.setToolTip("")
        self._refresh_inspector_buttons()
        self.show()
        self._title.setText("本次分析結果")
        self._output_dir = str(summary.get("output_dir") or "")
        self._alignment_outputs = {
            str(key): str(path)
            for key, path in (summary.get("alignment_outputs") or {}).items()
            if path
        }
        # Only open a real review gallery; do NOT fall back to review_report.html
        # (the bare stats page) — it is not the backfill+MS1-overlay gallery the
        # user reviews with, and surfacing it as "gallery" is misleading.
        self._gallery = str(
            summary.get("gallery_html")
            or self._alignment_outputs.get("gallery_html")
            or ""
        )
        self._matrix = str(
            summary.get("matrix_tsv") or self._alignment_outputs.get("matrix_tsv") or ""
        )
        gallery_error = str(summary.get("gallery_error") or "")

        self._empty.setVisible(False)
        self._dashboard.setVisible(True)
        self._error.setVisible(False)

        sample_count = summary.get("sample_count")
        counts = summary.get("candidate_counts") or {}
        self._stat_samples.set_value("—" if sample_count is None else str(sample_count))
        self._stat_total.set_value(str(counts.get("total", 0)) if counts else "—")
        self._stat_mode.set_value(_MODE_LABELS.get(str(summary.get("mode")), "—"))
        self._distribution.set_counts(counts)
        self._populate_sample_table(summary.get("discovery_batch_index"))

        self._open_output.setEnabled(bool(self._output_dir))
        self._open_gallery.setEnabled(bool(self._gallery))
        if self._gallery:
            self._open_gallery.setToolTip(_GALLERY_TOOLTIP)
        elif gallery_error:
            self._open_gallery.setToolTip(f"gallery 未產出：{gallery_error}")
        else:
            self._open_gallery.setToolTip(_GALLERY_TOOLTIP)
        self._open_matrix.setEnabled(bool(self._matrix))
        self._summary_text = self._build_text(summary)

    def show_error(self, message: str) -> None:
        self._invalidate_inspector("執行未完成，無 Inspector 來源")
        self.show()
        self._title.setText("本次執行未完成")
        self._output_dir = ""
        self._gallery = ""
        self._matrix = ""
        self._alignment_outputs = {}
        self._empty.setVisible(False)
        self._dashboard.setVisible(True)
        for button in self._buttons:
            button.setEnabled(False)
        self._stat_samples.set_value("—")
        self._stat_total.set_value("—")
        self._stat_mode.set_value("—")
        self._distribution.set_counts({})
        self._sample_heading.setVisible(False)
        self._sample_table.setVisible(False)
        self._error.setText(f"執行失敗：{message}")
        self._error.setStyleSheet(f"color: {styles.ACTIVE['error']};")
        self._error.setVisible(True)
        self._summary_text = f"執行失敗：{message}"

    def mark_previous(self) -> None:
        if self._summary_text != "尚未執行":
            self._title.setText("上次執行結果 · 設定已變更")

    def clear_results(self) -> None:
        self._invalidate_inspector("新執行尚未完成，無 Inspector 來源")
        self._output_dir = ""
        self._gallery = ""
        self._matrix = ""
        self._alignment_outputs = {}
        self._summary_text = "尚未執行"
        self._sample_table.setRowCount(0)
        for button in self._buttons:
            button.setEnabled(False)
        self._error.clear()
        self._error.hide()
        self._dashboard.hide()
        self._empty.show()
        self.hide()

    def summary_text(self) -> str:
        return self._summary_text

    def refresh_theme(self) -> None:
        self._distribution.refresh()
        if self._error.isVisible():
            self._error.setStyleSheet(f"color: {styles.ACTIVE['error']};")

    def is_report_busy(self) -> bool:
        return self._report_worker is not None or self._summary_worker is not None

    def generate_default_report(self) -> None:
        """Write the default review page beside the evidence of the current result."""
        source = self._evidence
        if source is None or self.is_report_busy():
            return
        self._start_report(
            InspectorJob(
                source,
                SelectionRequest(),
                source.report_path.parent / _INSPECTOR_FILENAME,
                False,
            )
        )

    def cancel_report(self) -> None:
        if self._report_worker is not None:
            self._report_worker.stop()
            self._inspector_status.setText("正在取消 Inspector 匯出…")

    def stop_report_for_close(self) -> None:
        self.cancel_report()

    def _invalidate_inspector(self, reason: str) -> None:
        self._generation += 1
        self._evidence = None
        self._report_target = None
        self._inspector_status.setText(reason)
        self._refresh_inspector_buttons()

    def _refresh_inspector_buttons(self) -> None:
        self._export_inspector.setEnabled(
            self._evidence is not None and not self.is_report_busy()
        )
        self._open_inspector.setEnabled(self._report_target is not None)
        self._cancel_inspector.setVisible(self._report_worker is not None)

    def _export(self) -> None:
        source = self._evidence
        if source is None or self.is_report_busy():
            return
        dialog = InspectorExportDialog(
            source.report_path.parent / _INSPECTOR_FILENAME, self
        )
        worker = InspectorSummaryWorker(source)
        self._summary_worker = worker
        worker.loaded.connect(
            lambda summary: self._on_summary_loaded(worker, source, dialog, summary)
        )
        worker.failed.connect(
            lambda message: self._on_summary_failed(worker, source, dialog, message)
        )
        worker.finished.connect(lambda: self._on_summary_finished(worker))
        worker.start()
        self._refresh_inspector_buttons()
        accepted = dialog.exec()
        if not accepted or dialog.selection is None or dialog.output_path is None:
            return
        # The dialog may have stayed open while a new run replaced its source.
        if self._evidence != source or self._report_worker is not None:
            self._inspector_status.setText("來源已切換，請重新開啟匯出設定")
            return
        job = InspectorJob(
            source, dialog.selection, dialog.output_path, dialog.open_after_export
        )
        self._start_report(job)

    def _on_summary_loaded(
        self, worker: InspectorSummaryWorker, source: EvidenceLocator,
        dialog: InspectorExportDialog, summary: dict,
    ) -> None:
        if (
            self._summary_worker is worker
            and self._evidence == source
            and summary.get("processing_request_id") == source.processing_request_id
            and summary.get("processing_attempt_id") == source.processing_attempt_id
        ):
            dialog.set_samples(summary)
        else:
            dialog.set_source_error("來源身份不符或結果已切換")

    def _on_summary_failed(
        self, worker: InspectorSummaryWorker, source: EvidenceLocator,
        dialog: InspectorExportDialog, message: str,
    ) -> None:
        if self._summary_worker is worker:
            dialog.set_source_error(message)

    def _on_summary_finished(self, worker: InspectorSummaryWorker) -> None:
        if self._summary_worker is worker:
            self._summary_worker = None
            self._refresh_inspector_buttons()

    def _start_report(self, job: InspectorJob) -> None:
        if self._report_worker is not None:
            return
        generation = self._generation
        worker = InspectorReportWorker(job)
        self._report_worker = worker
        label = _run_label(job.source)
        self._inspector_status.setText("正在產生 Inspector…")
        self._inspector_status.setToolTip(label)
        worker.progress.connect(
            lambda progress: self._on_report_progress(worker, label, progress)
        )
        worker.succeeded.connect(
            lambda result: self._on_report_success(worker, generation, label, result)
        )
        worker.cancelled.connect(
            lambda: self._on_report_terminal(worker, label, "已取消")
        )
        worker.failed.connect(
            lambda code, reason: self._on_report_terminal(
                worker, label, f"Inspector 產生失敗 (code {code})：{reason}"
            )
        )
        worker.finished.connect(lambda: self._on_report_finished(worker))
        worker.start()
        self._refresh_inspector_buttons()

    def _on_report_progress(self, worker, label: str, progress) -> None:
        if self._report_worker is worker:
            self._inspector_status.setText(
                f"正在產生 Inspector：{progress.stage} {progress.completed}"
                + (f"/{progress.total}" if progress.total is not None else "")
            )

    def _on_report_success(
        self, worker: InspectorReportWorker, generation: int, label: str,
        result: ReportResult,
    ) -> None:
        if self._report_worker is not worker:
            return
        if result.source != worker.job.source:
            self._on_report_terminal(worker, label, "失敗：報告來源與本次 job 不符")
            return
        capability = (
            "limited / metadata only" if result.evidence_capability == "metadata_only"
            else result.evidence_capability
        )
        detail = (
            f"{label}：已匯出 {result.output_path}；{capability}；"
            f"run {result.run_total} / eligible {result.eligible_count} / "
            f"matched {result.matched_count} / selected {result.selected_count} / "
            f"missing RT {result.missing_rt_count}"
        )
        current = generation == self._generation and self._evidence == worker.job.source
        if current:
            self._report_target = result.output_path
        # A report finishing after a newer result keeps its run identity visible.
        limited = result.evidence_capability == "metadata_only"
        message = (
            f"Inspector 已就緒：抽出 {result.selected_count} / {result.run_total} 項"
            + ("；僅有結果紀錄、沒有曲線" if limited else "")
            if current
            else detail
        )
        self._on_report_terminal(worker, label, message)
        if self._report_worker is worker:
            self._inspector_status.setToolTip(detail)
        if worker.job.open_after_export:
            _open_path(str(result.output_path))

    def _on_report_terminal(self, worker, label: str, message: str) -> None:
        if self._report_worker is not worker:
            return
        self._inspector_status.setText(message)
        self.status_message.emit(message, 10000)
        self._refresh_inspector_buttons()

    def _on_report_finished(self, worker: InspectorReportWorker) -> None:
        if self._report_worker is worker:
            self._report_worker = None
            self._refresh_inspector_buttons()

    # ── Private ─────────────────────────────────────────────────────────────

    def _populate_sample_table(self, index_path: object) -> None:
        rows = _read_per_sample_rows(index_path)
        self._sample_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, value in enumerate(row):
                item = QTableWidgetItem(value)
                if col_index != 0:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._sample_table.setItem(row_index, col_index, item)
        has_rows = bool(rows)
        self._sample_heading.setVisible(has_rows)
        self._sample_table.setVisible(has_rows)
        if has_rows:
            visible = min(len(rows), 8)
            header = self._sample_table.horizontalHeader()
            header_height = header.height() if header is not None else 28
            self._sample_table.setFixedHeight(header_height + visible * 30 + 4)

    def _build_text(self, summary: dict[str, Any]) -> str:
        parts = [f"模式：{summary.get('mode', '')}"]
        sample_count = summary.get("sample_count")
        if sample_count is not None:
            parts.append(f"樣本數：{sample_count}")
        counts = summary.get("candidate_counts")
        if counts:
            parts.append(
                "候選：共 "
                f"{counts.get('total', 0)}（高 {counts.get('HIGH', 0)} / "
                f"中 {counts.get('MEDIUM', 0)} / 低 {counts.get('LOW', 0)}）"
            )
        for key, path in sorted(self._alignment_outputs.items()):
            parts.append(f"{key}：{path}")
        return " | ".join(parts)


def _locator_from_summary(value: object) -> tuple[EvidenceLocator | None, str]:
    if value is None:
        return None, "本次完成結果沒有 Inspector evidence locator"
    if not isinstance(value, EvidenceLocator):
        return None, "Inspector evidence locator 格式無效"
    return value, ""


def _run_label(source: EvidenceLocator) -> str:
    return (
        f"原執行 request {source.processing_request_id} / "
        f"attempt {source.processing_attempt_id}"
    )


_MODE_LABELS = {
    "full": "Discovery + Alignment",
    "discovery_only": "Discovery only",
    "align_only": "Align only",
}


class _StatCard(QFrame):
    """A compact metric tile: a large value over a muted caption."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setObjectName("section_card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(2)
        self._value = QLabel("—")
        self._value.setStyleSheet("font-size: 20pt; font-weight: 700;")
        caption_label = QLabel(caption)
        caption_label.setObjectName("section_subtitle")
        layout.addWidget(self._value)
        layout.addWidget(caption_label)

    def set_value(self, value: str) -> None:
        self._value.setText(value)


class _DistributionBar(QWidget):
    """Segmented confidence bar (HIGH/MEDIUM/LOW) with a counted legend."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._track = QFrame()
        self._track.setObjectName("dist_track")
        self._track.setFixedHeight(16)
        self._track_layout = QHBoxLayout(self._track)
        self._track_layout.setContentsMargins(0, 0, 0, 0)
        self._track_layout.setSpacing(0)
        self._segments: dict[str, QFrame] = {}
        for tier in _TIER_TOKENS:
            segment = QFrame()
            segment.setStyleSheet("background: transparent; border-radius: 8px;")
            self._track_layout.addWidget(segment, 0)
            self._segments[tier] = segment
        layout.addWidget(self._track)

        legend = QHBoxLayout()
        legend.setSpacing(16)
        self._legend: dict[str, QLabel] = {}
        for tier, label in _TIER_LABELS:
            item = QLabel()
            item.setTextFormat(Qt.TextFormat.RichText)
            self._legend[tier] = item
            legend.addWidget(item)
        legend.addStretch()
        layout.addLayout(legend)
        self._counts: dict[str, Any] = {}
        self.set_counts({})

    def set_counts(self, counts: dict[str, Any]) -> None:
        self._counts = dict(counts)
        values = {tier: int(counts.get(tier, 0) or 0) for tier in _TIER_TOKENS}
        total = sum(values.values())
        for tier, segment in self._segments.items():
            stretch = values[tier] if total else 0
            self._track_layout.setStretchFactor(segment, stretch)
            color = styles.ACTIVE[_TIER_TOKENS[tier]] if values[tier] else "transparent"
            segment.setStyleSheet(f"background: {color}; border-radius: 8px;")
        muted = styles.ACTIVE["text_muted"]
        for tier, label in _TIER_LABELS:
            tier_color = styles.ACTIVE[_TIER_TOKENS[tier]]
            dot = f'<span style="color:{tier_color}; font-size:13pt;">●</span>'
            label_html = (
                f'{dot} <span style="color:{muted};">{label}</span> '
                f"<b>{values[tier]}</b>"
            )
            self._legend[tier].setText(label_html)

    def refresh(self) -> None:
        self.set_counts(self._counts)


def _build_sample_table() -> QTableWidget:
    table = QTableWidget(0, len(_PER_SAMPLE_HEADERS))
    table.setHorizontalHeaderLabels(list(_PER_SAMPLE_HEADERS))
    for column in range(len(_PER_SAMPLE_HEADERS)):
        item = table.horizontalHeaderItem(column)
        if item is not None:
            item.setTextAlignment(
                (
                    Qt.AlignmentFlag.AlignLeft
                    if column == 0
                    else Qt.AlignmentFlag.AlignRight
                )
                | Qt.AlignmentFlag.AlignVCenter
            )
    vertical_header = table.verticalHeader()
    if vertical_header is not None:
        vertical_header.setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    header = table.horizontalHeader()
    if header is not None:
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(_PER_SAMPLE_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    return table


def _read_per_sample_rows(index_path: object) -> list[tuple[str, str, str, str, str]]:
    text = str(index_path or "").strip()
    if not text:
        return []
    path = Path(text)
    if not path.exists():
        return []
    rows: list[tuple[str, str, str, str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for record in csv.DictReader(handle):
            rows.append(
                (
                    str(record.get("sample_stem", "") or ""),
                    str(record.get("candidate_count", "0") or "0"),
                    str(record.get("high_count", "0") or "0"),
                    str(record.get("medium_count", "0") or "0"),
                    str(record.get("low_count", "0") or "0"),
                )
            )
    return rows


def _open_path(path: str) -> None:
    if not path:
        return
    target = Path(path)
    if target.exists():
        os.startfile(str(target))  # noqa: S606
