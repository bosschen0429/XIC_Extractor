"""Shared layout primitives so every section follows one spacing system.

Centralising the card/header/label construction here keeps the visual rhythm
consistent (header padding, body padding, inter-field spacing) instead of each
section hard-coding its own margins.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)


class DropLineEdit(QLineEdit):
    """QLineEdit that accepts file/folder drag-and-drop and fills local path."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        mime = event.mimeData() if event is not None else None
        if mime is not None and mime.hasUrls():
            assert event is not None
            event.acceptProposedAction()
        elif event is not None:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:
        mime = event.mimeData() if event is not None else None
        if mime is not None and mime.hasUrls():
            assert event is not None
            event.acceptProposedAction()
        elif event is not None:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            urls = mime.urls()
            if urls:
                local_path = urls[0].toLocalFile()
                if local_path:
                    self.setText(local_path)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)


class DropTableWidget(QTableWidget):
    """QTableWidget that accepts drag-and-drop for supported files."""

    file_dropped = pyqtSignal(Path)

    def __init__(
        self,
        rows: int,
        cols: int,
        parent: QWidget | None = None,
        *,
        allowed_extensions: tuple[str, ...] = (
            ".csv",
            ".tsv",
            ".xlsx",
            ".docx",
        ),
    ) -> None:
        super().__init__(rows, cols, parent)
        self.setAcceptDrops(True)
        self._allowed_extensions = tuple(ext.lower() for ext in allowed_extensions)

    def _matches(self, event: QDragEnterEvent | QDragMoveEvent | None) -> bool:
        if event is None:
            return False
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in self._allowed_extensions:
                    return True
        return False

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if self._matches(event):
            assert event is not None
            event.acceptProposedAction()
        elif event is not None:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent | None) -> None:
        if self._matches(event):
            assert event is not None
            event.acceptProposedAction()
        elif event is not None:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in self._allowed_extensions and path.is_file():
                    self.file_dropped.emit(path)
                    event.acceptProposedAction()
                    return
        super().dropEvent(event)


# Spacing scale (px). Use these instead of ad-hoc numbers.
SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 20


class AnalysisWorkspace(QWidget):
    """Keep batch controls available while a table uses the main content area.

    Overview and detail share the section instance, so returning from an editor
    does not reconstruct its model, selection or unsaved values.
    """

    detail_closed = pyqtSignal(QWidget)

    def __init__(self, execution: QWidget) -> None:
        super().__init__()
        self._sections: list[QWidget] = []
        self._active_detail: QWidget | None = None
        self._overview_position = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        root.addWidget(execution)

        self._overview = QScrollArea()
        self._overview.setObjectName("content_scroll")
        self._overview.setWidgetResizable(True)
        self._overview.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("scroll_content")
        self._overview_layout = QVBoxLayout(container)
        self._overview_layout.setContentsMargins(SPACE_XL, SPACE_LG, SPACE_XL, SPACE_XL)
        self._overview_layout.setSpacing(SPACE_MD)
        self._overview_layout.addStretch()
        self._overview.setWidget(container)
        self._stack.addWidget(self._overview)

        self._detail_page = QWidget()
        self._detail_page.setObjectName("detail_workspace")
        self._detail_layout = QVBoxLayout(self._detail_page)
        self._detail_layout.setContentsMargins(SPACE_XL, SPACE_MD, SPACE_XL, SPACE_LG)
        self._detail_layout.setSpacing(SPACE_MD)
        back_row = QHBoxLayout()
        self._back = QPushButton("返回批次設定")
        self._back.setObjectName("back_to_batch")
        self._back.setShortcut("Alt+Left")
        self._back.setToolTip("返回摘要，保留目前修改與選取 · Alt+←")
        self._back.clicked.connect(self.show_overview)
        back_row.addWidget(self._back)
        back_row.addStretch()
        self._detail_layout.addLayout(back_row)
        self._stack.addWidget(self._detail_page)

    def add_section(self, section: QWidget) -> None:
        self._sections.append(section)
        self._overview_layout.insertWidget(len(self._sections) - 1, section)

    def open_details(self, section: QWidget) -> None:
        if self._active_detail is section:
            return
        if self._active_detail is not None:
            self.show_overview()
        scrollbar = self._overview.verticalScrollBar()
        assert scrollbar is not None
        self._overview_position = scrollbar.value()
        self._overview_layout.removeWidget(section)
        self._detail_layout.addWidget(section, 1)
        self._active_detail = section
        self._stack.setCurrentWidget(self._detail_page)
        section.show()
        self._back.setFocus(Qt.FocusReason.OtherFocusReason)

    def show_overview(self) -> None:
        section = self._active_detail
        if section is None:
            return
        self._detail_layout.removeWidget(section)
        self._overview_layout.insertWidget(self._sections.index(section), section)
        self._active_detail = None
        self.detail_closed.emit(section)
        self._stack.setCurrentWidget(self._overview)
        section.show()
        QTimer.singleShot(0, self._restore_overview_position)

    def reveal(self, section: QWidget) -> None:
        self.show_overview()
        QTimer.singleShot(0, lambda: self._overview.ensureWidgetVisible(section))

    def _restore_overview_position(self) -> None:
        if self._active_detail is None:
            scrollbar = self._overview.verticalScrollBar()
            assert scrollbar is not None
            scrollbar.setValue(self._overview_position)


def titled_card(
    title: str, subtitle: str = ""
) -> tuple[QFrame, QHBoxLayout, QVBoxLayout]:
    """A section card with a styled header. Returns (card, header_layout,
    card_layout). Callers add header actions to header_layout and their own
    body (grid, toolbar, table, …) to card_layout. Use section_card() instead
    when a simple padded vertical body is all you need.

    An optional subtitle renders a muted descriptor under the title so the
    numbered step cards read as a hierarchy rather than equal-weight boxes.
    """
    card = QFrame()
    card.setObjectName("section_card")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(0, 0, 0, 0)
    card_layout.setSpacing(0)

    header = QFrame()
    header.setObjectName("section_header")
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)

    title_col = QVBoxLayout()
    title_col.setContentsMargins(0, 0, 0, 0)
    title_col.setSpacing(1)
    label = QLabel(title)
    label.setObjectName("section_title")
    title_col.addWidget(label)
    if subtitle:
        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("section_subtitle")
        title_col.addWidget(subtitle_label)
    header_layout.addLayout(title_col)
    card_layout.addWidget(header)
    return card, header_layout, card_layout


def section_card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    """A titled section card with a padded vertical body.

    Body content is laid out with SPACE_LG padding and SPACE_MD between items.
    """
    card, _header_layout, card_layout = titled_card(title, subtitle)
    body = QVBoxLayout()
    body.setContentsMargins(SPACE_LG, SPACE_LG, SPACE_LG, SPACE_LG)
    body.setSpacing(SPACE_MD)
    card_layout.addLayout(body)
    return card, body


def field_label(text: str) -> QLabel:
    """A muted, consistent form-field label."""
    label = QLabel(text)
    label.setObjectName("field_label")
    return label
