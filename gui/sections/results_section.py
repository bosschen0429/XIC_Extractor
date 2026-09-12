import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui import styles
from gui.ui import titled_card


class _Card(QFrame):
    def __init__(self, label: str, value: str, detail: str, color: str) -> None:
        super().__init__()
        self.setObjectName("section_card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(6)

        muted = styles.ACTIVE["text_muted"]
        label_widget = QLabel(label.upper())
        label_widget.setStyleSheet(f"color: {muted}; font-size: 9pt; font-weight: 600;")
        value_widget = QLabel(value)
        value_widget.setStyleSheet(
            f"color: {color}; font-size: 20pt; font-weight: 700;"
        )
        detail_widget = QLabel(detail)
        detail_widget.setWordWrap(True)
        detail_widget.setStyleSheet(f"color: {muted}; font-size: 9pt;")

        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        layout.addWidget(detail_widget)
        layout.addStretch()


class ResultsSection(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._excel_path = ""
        # Cache the last render so a dark/light toggle can rebuild the dynamic
        # cards from the now-current ACTIVE palette (the cards hard-bake their
        # colours at build time, so they can't follow the theme on their own).
        self._last_summary: dict | None = None
        self._last_error: str | None = None
        self._previous = False
        self.setVisible(False)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        card, header_layout, card_layout = titled_card("分析結果", "")
        self._title = card.findChild(QLabel, "section_title")
        assert self._title is not None
        root_layout.addWidget(card)
        header_layout.addStretch()
        self._folder_button = QPushButton("開啟資料夾")
        self._folder_button.clicked.connect(self._open_folder)
        self._folder_button.setVisible(False)
        header_layout.addWidget(self._folder_button)

        self._open_button = QPushButton("開啟 Excel")
        self._open_button.clicked.connect(self._open_excel)
        self._open_button.setVisible(False)
        header_layout.addWidget(self._open_button)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(16, 16, 16, 16)
        self._body_layout.setSpacing(12)
        card_layout.addWidget(self._body)

        self._error_label = QLabel()
        self._error_label.setObjectName("results_error")
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._error_label.setVisible(False)
        self._body_layout.addWidget(self._error_label)

        self._istd_warn_label = QLabel()
        self._istd_warn_label.setObjectName("istd_warning")
        self._istd_warn_label.setWordWrap(True)
        self._istd_warn_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self._istd_warn_label.setVisible(False)
        self._body_layout.addWidget(self._istd_warn_label)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._body_layout.addLayout(self._grid)

    def update_results(self, summary: dict) -> None:
        self._previous = False
        self._title.setText("本次分析結果")
        self._last_summary = summary
        self._last_error = None
        self._excel_path = summary.get("excel_path", "")
        has_path = bool(self._excel_path)
        self._open_button.setVisible(has_path)
        self._folder_button.setVisible(has_path)
        self._error_label.clear()
        self._error_label.setVisible(False)
        istd_warnings = summary.get("istd_warnings", [])
        if istd_warnings:
            parts = [
                f"{warning['label']} ({warning['detected']}/{warning['total']})"
                for warning in istd_warnings
            ]
            self._istd_warn_label.setText("⚠ ISTD 未全偵測：" + "、".join(parts))
            self._istd_warn_label.setVisible(True)
        else:
            self._istd_warn_label.setVisible(False)
        self._clear_grid()

        cards: list[_Card] = []
        for target in summary.get("targets", []):
            cards.append(
                _Card(
                    label=str(target["label"]),
                    value=f"{target['detected']}/{target['total']}",
                    detail=_target_detail(target),
                    color=styles.ACTIVE["success"]
                    if target.get("detected")
                    else styles.ACTIVE["text_muted"],
                )
            )

        cards.append(
            _Card(
                label="DIAGNOSTICS",
                value=str(summary.get("diagnostics_count", 0)),
                detail="Issue rows",
                color=styles.ACTIVE["warning"]
                if summary.get("diagnostics_count", 0)
                else styles.ACTIVE["success"],
            )
        )
        cards.append(
            _Card(
                label="TOTAL FILES",
                value=str(summary.get("total_files", 0)),
                detail="Processed sample files",
                color=styles.ACTIVE["primary"],
            )
        )

        for index, card in enumerate(cards):
            row = index // 2
            column = index % 2
            self._grid.addWidget(card, row, column)

        self.setVisible(True)

    def show_error(self, message: str) -> None:
        self._previous = False
        self._title.setText("本次執行未完成")
        self._last_error = message
        self._last_summary = None
        self._excel_path = ""
        self._open_button.setVisible(False)
        self._folder_button.setVisible(False)
        self._istd_warn_label.setVisible(False)
        self._clear_grid()
        self._error_label.setText(message)
        self._error_label.setVisible(True)
        self.setVisible(True)

    def mark_previous(self) -> None:
        if self._last_summary is not None or self._last_error is not None:
            self._previous = True
            self._title.setText("上次執行結果 · 設定已變更")

    def clear_results(self) -> None:
        self._last_summary = None
        self._last_error = None
        self._previous = False
        self._excel_path = ""
        self._clear_grid()
        self._open_button.hide()
        self._folder_button.hide()
        self._error_label.clear()
        self._error_label.hide()
        self._istd_warn_label.hide()
        self.hide()

    def refresh_theme(self) -> None:
        """Rebuild the dynamic cards from the live palette on a theme switch.

        Static labels (error / ISTD warning) follow the theme automatically via
        their objectName QSS rules; only the colour-baked cards need replaying.
        """
        previous = self._previous
        if self._last_error is not None:
            self.show_error(self._last_error)
        elif self._last_summary is not None:
            self.update_results(self._last_summary)
        if previous:
            self.mark_previous()

    def _open_excel(self) -> None:
        if self._excel_path:
            os.startfile(self._excel_path)

    def _open_folder(self) -> None:
        if self._excel_path:
            from pathlib import Path

            os.startfile(str(Path(self._excel_path).parent))

    def _clear_grid(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _target_detail(target: dict) -> str:
    detail = (
        f"✓{target.get('nl_ok', 0)} "
        f"⚠{target.get('nl_warn', 0)} "
        f"✗{target.get('nl_fail', 0)} "
        f"—{target.get('nl_no_ms2', 0)}"
    )
    median_area = target.get("median_area")
    if median_area is not None:
        detail += f"\nMedian Area: {float(median_area):,.2f}"
    return detail
