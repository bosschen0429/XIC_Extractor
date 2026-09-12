from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
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
from gui.ui import section_card

_PER_SAMPLE_HEADERS = ("樣本", "候選", "高", "中", "低")

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
    def __init__(self) -> None:
        super().__init__()
        self._output_dir = ""
        self._gallery = ""
        self._matrix = ""
        self._alignment_outputs: dict[str, str] = {}
        self._summary_text = "尚未執行"
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
        button_row.addStretch()
        layout.addLayout(button_row)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        return container

    # ── Public API ──────────────────────────────────────────────────────────

    def update_results(self, summary: dict[str, Any]) -> None:
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
