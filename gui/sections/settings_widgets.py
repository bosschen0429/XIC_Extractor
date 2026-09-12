from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class _LabeledSpin(QWidget):
    """SpinBox with a sub-label and explicit − / + buttons.

    Solves the Qt arrow-button hit-target problem: removes the built-in
    arrows and replaces them with 32×32 px buttons that never overlap the
    text field.
    """

    def __init__(self, label: str, spin: QAbstractSpinBox) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        lbl = QLabel(label)
        lbl.setObjectName("hint")
        lay.addWidget(lbl)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        _btn_style = "font-size: 14pt; font-weight: 600; padding: 0;"

        btn_minus = QPushButton("−")
        btn_minus.setFixedSize(32, 32)
        btn_minus.setStyleSheet(_btn_style)
        btn_minus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_minus.clicked.connect(lambda: spin.stepBy(-1))

        btn_plus = QPushButton("+")
        btn_plus.setFixedSize(32, 32)
        btn_plus.setStyleSheet(_btn_style)
        btn_plus.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_plus.clicked.connect(lambda: spin.stepBy(1))

        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setMinimumWidth(60)

        row.addWidget(btn_minus)
        row.addWidget(spin, 1)
        row.addWidget(btn_plus)
        lay.addLayout(row)


class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toggle = QToolButton(self)
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.toggled.connect(self._on_toggled)

        self._content = QFrame(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(12, 8, 0, 0)
        self._content_layout.setSpacing(8)
        self._content.setVisible(False)

        outer = QVBoxLayout(self)
        outer.addWidget(self._toggle)
        outer.addWidget(self._content)
        outer.setContentsMargins(0, 0, 0, 0)

    def add_row(self, widget: QWidget) -> None:
        self._content_layout.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self._toggle.isChecked()

    def set_title(self, title: str) -> None:
        self._toggle.setText(title)

    def _on_toggled(self, checked: bool) -> None:
        self._content.setVisible(checked)
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )


def _set_float_range(
    spin: QDoubleSpinBox,
    minimum: float,
    maximum: float,
    decimals: int,
) -> None:
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(0.01)
