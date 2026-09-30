from pathlib import Path

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class RunSection(QWidget):
    run_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    problems_clicked = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("execution_bar")
        self._running = False
        self._stopping = False
        self._terminal = False
        self._ready = True
        self._missing: list[str] = []
        self._elapsed_seconds = 0

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 12, 20, 12)
        root_layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(12)
        status = QVBoxLayout()
        status.setSpacing(3)
        row.addLayout(status, 1)

        self._ready_hint = QLabel("")
        self._ready_hint.setObjectName("ready_hint_ok")
        self._ready_hint.setWordWrap(True)
        status.addWidget(self._ready_hint)

        self._status_label = QLabel("尚未開始")
        self._status_label.setWordWrap(True)
        self._status_label.setMaximumHeight(52)
        self._status_label.hide()
        status.addWidget(self._status_label)

        self._problems_button = QPushButton("查看問題")
        self._problems_button.setObjectName("show_run_problems")
        self._problems_button.clicked.connect(self.problems_clicked)
        self._problems_button.hide()
        row.addWidget(self._problems_button)

        self._button = QPushButton("開始執行")
        self._button.setObjectName("btn_run")
        self._button.setMinimumWidth(128)
        self._button.clicked.connect(self._on_button_clicked)
        row.addWidget(self._button)
        root_layout.addLayout(row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        root_layout.addWidget(self._progress)

        self._elapsed_label = QLabel("")
        self._elapsed_label.setObjectName("elapsed_label")
        self._elapsed_label.setVisible(False)
        status.addWidget(self._elapsed_label)

        # Elapsed timer (fires every second in the main thread — safe for Qt)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_ready(self, ready: bool, missing: list[str]) -> None:
        """Reflect whether the current config can run, and what is missing."""
        self._ready = ready
        self._missing = list(missing)
        prefix = "再次執行：" if self._terminal else ""
        if ready:
            self._ready_hint.setObjectName("ready_hint_ok")
            self._ready_hint.setText(prefix + "設定完整，可以執行")
        else:
            self._ready_hint.setObjectName("ready_hint_block")
            self._ready_hint.setText(prefix + "需修正：" + "、".join(missing))
        self._ready_hint.setVisible(not self._running)
        self._problems_button.setVisible(not ready and not self._running)
        self._repolish(self._ready_hint)
        if not self._running:
            self._button.setEnabled(ready)

    def set_progress(self, current: int, total: int, filename: str) -> None:
        self._progress.show()
        self._progress.setRange(0, max(total, 0))
        if total > 0:
            self._progress.setValue(current)
        if not self._stopping:
            count = f"{current}/{total}" if total > 0 else f"已處理 {current} 個檔案"
            self._status_label.setText(f"處理中 {count}：{filename}")
            self._status_label.setToolTip(filename)

    def set_running(self, running: bool) -> None:
        self._running = running
        if running:
            self._stopping = False
            self._terminal = False
            self._elapsed_seconds = 0
            self._elapsed_label.setText("已執行 00:00")
            self._elapsed_label.setVisible(True)
            self._timer.start()
            self._status_label.setText("準備中...")
            self._status_label.show()
            self._progress.setRange(0, 0)
            self._progress.show()
            self._progress.setValue(0)
            self._ready_hint.setVisible(False)
            self._problems_button.hide()
            self._button.setEnabled(True)
        else:
            self._stopping = False
            self._timer.stop()
            self.set_ready(self._ready, self._missing)
            self._button.setEnabled(self._ready)

        self._button.setText("停止執行" if running else "開始執行")
        self._button.setObjectName("btn_stop" if running else "btn_run")
        self._repolish(self._button)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        if style is not None:
            style.unpolish(widget)
            style.polish(widget)
        widget.update()

    def set_complete(self, total: int) -> None:
        self._terminal = True
        self._timer.stop()
        self._elapsed_label.setVisible(False)
        progress_max = max(total, 1)
        self._progress.setRange(0, progress_max)
        self._progress.setValue(total)
        self._status_label.setText(f"完成，共 {total} 個檔案")
        self._status_label.show()
        self._progress.show()

    def set_error(self, message: str) -> None:
        """Show an error state: reset progress bar and display the message."""
        self._terminal = True
        self._timer.stop()
        self._elapsed_label.setVisible(False)
        self._progress.setValue(0)
        self._progress.hide()
        first_line = (message.splitlines() or ["未提供錯誤原因"])[0]
        self._status_label.setText(f"執行失敗：{first_line[:180]}")
        self._status_label.setToolTip(message)
        self._status_label.show()
        self.set_ready(self._ready, self._missing)

    def set_stopping(self) -> None:
        self._stopping = True
        self._button.setEnabled(False)
        self._button.setText("停止中…")
        self._status_label.setText("停止中，等待目前階段結束")

    def set_stopped(self, message: str = "") -> None:
        self._terminal = True
        self.set_running(False)
        first_line = message.splitlines()[0] if message else "已停止執行"
        self._status_label.setText(
            f"已停止：{first_line[:180]}" if message else first_line
        )
        self._status_label.setToolTip(message)
        self._status_label.show()
        self._progress.hide()

    def reset(self) -> None:
        if self._running:
            return
        self._terminal = False
        self._status_label.setText("尚未開始")
        self._status_label.hide()
        self._status_label.setToolTip("")
        self._progress.hide()
        self._elapsed_label.hide()
        self.set_ready(self._ready, self._missing)

    # ── Private ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self._elapsed_seconds += 1
        mins, secs = divmod(self._elapsed_seconds, 60)
        self._elapsed_label.setText(f"已執行 {mins:02d}:{secs:02d}")

    def _on_button_clicked(self) -> None:
        if self._running:
            self.stop_clicked.emit()
            return
        self.run_clicked.emit()


def format_run_problem(
    reason: str,
    *,
    stage: str,
    output_dir: Path,
    available_outputs: tuple[tuple[str, Path], ...] = (),
    cancelled: bool = False,
) -> str:
    """Render GUI-owned run facts without searching an output directory."""
    parts = [f"{stage}：{reason}", f"要求的輸出位置：{output_dir}"]
    if available_outputs:
        parts.append("本次可用產物：")
        parts.extend(f"{label}：{path}" for label, path in available_outputs)
    else:
        parts.append("本次可用產物：無；尚未取得本次成功發布的產物。")
    parts.append("本次 attempt：GUI 尚未取得可供識別的 attempt ID。")
    if cancelled:
        parts.append("下一步：可檢查停止原因、調整設定後重新執行。")
    elif any(word in reason.lower() for word in ("denied", "permission", "errno 13")):
        parts.append("下一步：關閉占用的輸出檔，確認上述輸出資料夾可寫入，再重新執行。")
    else:
        parts.append("下一步：依照上述原因修正該階段的設定、輸入或輸出位置，再重新執行。")
    return "\n".join(parts)
