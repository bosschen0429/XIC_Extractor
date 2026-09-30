from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from gui.config_io import read_settings, read_targets, write_settings, write_targets
from gui.dialogs import confirm_stop
from gui.sections.results_section import ResultsSection
from gui.sections.run_section import RunSection, format_run_problem
from gui.sections.settings_section import SettingsSection
from gui.sections.targets_section import TargetsSection
from gui.ui import AnalysisWorkspace
from gui.workers.pipeline_worker import PipelineWorker


class TargetedView(QWidget):
    status_message = pyqtSignal(str, int)
    state_changed = pyqtSignal(str)

    def __init__(self, config_dir: Path) -> None:
        super().__init__()
        self._config_dir = config_dir
        self._worker: PipelineWorker | None = None
        self._terminal_state: str | None = None
        self._stop_requested = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._settings = SettingsSection()
        self._targets = TargetsSection()
        self._run = RunSection()
        self._results = ResultsSection()

        self._workspace = AnalysisWorkspace(self._run)
        for section in (self._settings, self._targets, self._results):
            self._workspace.add_section(section)
        layout.addWidget(self._workspace)

        self._settings.settings_saved.connect(self._save_settings)
        self._settings.settings_saved.connect(
            lambda: self.status_message.emit("設定已儲存", 3000)
        )
        self._targets.targets_saved.connect(self._save_targets)
        self._targets.targets_saved.connect(
            lambda: self.status_message.emit(
                f"目標已儲存，共 {self._targets._table.rowCount()} 筆", 3000
            )
        )
        self._run.run_clicked.connect(self._on_run)
        self._run.stop_clicked.connect(self._on_stop)
        self._run.problems_clicked.connect(self._show_problems)
        self._settings.changed.connect(self._on_config_changed)
        self._targets.changed.connect(self._on_config_changed)
        self._settings.new_batch_requested.connect(self._on_next_batch)
        self._targets.details_requested.connect(self._show_targets)
        self._workspace.detail_closed.connect(
            lambda _section: self._targets.set_detail_mode(False)
        )

    def _show_targets(self) -> None:
        self._targets.set_detail_mode(True)
        self._workspace.open_details(self._targets)

    def _show_problems(self) -> None:
        self._settings.reveal_invalid()
        self._workspace.reveal(self._settings)

    def _on_next_batch(self) -> None:
        if self.is_busy():
            return
        self._workspace.show_overview()
        self._settings.prepare_next_batch()
        self._terminal_state = None
        self._run.reset()
        self._results.clear_results()
        self._update_ready()
        self.status_message.emit("已沿用方法、目標與環境；請選擇本批 RAW 資料夾", 5000)

    def _on_config_changed(self) -> None:
        if not self.is_busy() and self._terminal_state is not None:
            self._terminal_state = None
            self._run.reset()
            self._results.mark_previous()
        self._update_ready()

    def _update_ready(self) -> None:
        valid = self._settings.is_valid()
        self._run.set_ready(valid, [] if valid else ["資料來源或分析參數"])
        if self._worker is not None:
            self.state_changed.emit("停止中…" if self._stop_requested else "執行中…")
        else:
            self.state_changed.emit(
                self._terminal_state or ("就緒" if valid else "需修正設定")
            )

    def load_config(self) -> None:
        self._load_config()

    def _load_config(self) -> None:
        settings: dict[str, str] = {}
        targets: list[dict[str, str]] = []
        try:
            settings = read_settings()
        except FileNotFoundError:
            settings = {}

        try:
            targets = read_targets()
        except FileNotFoundError:
            targets = []

        migrated = self._settings.load(settings)
        if migrated and self._settings.is_valid():
            write_settings(self._settings.get_values())
        self._targets.load(targets)
        self._update_ready()
        self.status_message.emit(f"已載入 {len(targets)} 個目標", 0)

    def _save_settings(self) -> None:
        write_settings(self._settings.get_values())

    def _save_targets(self) -> None:
        write_targets(self._targets.get_targets())

    def _on_run(self) -> None:
        if self.is_busy():
            return
        if not self._settings.is_valid():
            self.status_message.emit("設定值無效，請修正後再執行", 5000)
            return
        self._save_settings()
        self._save_targets()
        self._terminal_state = None
        self._stop_requested = False
        self._results.clear_results()
        self._settings.set_enabled(False)
        self._targets.set_enabled(False)
        self._run.set_running(True)
        self.status_message.emit("開始執行 pipeline...", 0)
        self.state_changed.emit("執行中…")

        worker = PipelineWorker(self._config_dir)
        self._worker = worker
        worker.progress.connect(
            lambda current, total, filename: self._on_progress(
                worker, current, total, filename
            )
        )
        worker.finished.connect(lambda summary: self._on_finished(worker, summary))
        worker.error.connect(lambda message: self._on_error(worker, message))
        worker.cancelled.connect(lambda: self._on_cancelled(worker))
        worker.start()

    def is_busy(self) -> bool:
        return self._worker is not None

    def emit_state(self) -> None:
        self._update_ready()

    def refresh_theme(self) -> None:
        """Replay the results cards so their baked-in colours follow the theme."""
        self._results.refresh_theme()

    def trigger_run(self) -> None:
        if self._worker is None:
            self._on_run()

    def trigger_stop(self, *, confirm: bool = True) -> None:
        if self._worker is not None:
            self._on_stop(confirm=confirm)

    def _on_stop(self, *, confirm: bool = True) -> None:
        if self._worker is not None:
            if self._stop_requested:
                return
            if confirm and not confirm_stop(self):
                return
            self._stop_requested = True
            self._run.set_stopping()
            self.state_changed.emit("停止中…")
            self._worker.stop()
            if not self._worker.wait(1000):
                self.status_message.emit("正在停止，等待目前階段結束...", 5000)
                return
            # The queued worker signal owns failure, cancellation and success.

    def _on_progress(
        self, worker: PipelineWorker, current: int, total: int, filename: str
    ) -> None:
        if self._worker is worker:
            self._run.set_progress(current, total, filename)

    def _on_finished(self, worker: PipelineWorker, summary: dict) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "完成"
        self._run.set_complete(summary["total_files"])
        self._run.set_running(False)
        self._settings.set_enabled(True)
        self._targets.set_enabled(True)
        self._results.update_results(summary)
        self._workspace.reveal(self._results)
        self.status_message.emit("Pipeline 執行完成", 5000)
        self._update_ready()

    def _on_error(self, worker: PipelineWorker, message: str) -> None:
        if self._worker is not worker:
            return
        details = format_run_problem(
            message,
            stage=worker.stage,
            output_dir=worker.output_dir,
            available_outputs=worker.available_outputs,
        )
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "執行失敗"
        self._run.set_running(False)
        self._run.set_error(details)
        self._settings.set_enabled(True)
        self._targets.set_enabled(True)
        self._results.show_error(details)
        self._workspace.reveal(self._results)
        self.status_message.emit(f"執行失敗: {message}", 5000)
        self._update_ready()

    def _on_cancelled(self, worker: PipelineWorker) -> None:
        if self._worker is not worker:
            return
        details = format_run_problem(
            worker.cancellation_reason,
            stage=worker.stage,
            output_dir=worker.output_dir,
            available_outputs=worker.available_outputs,
            cancelled=True,
        )
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "已停止"
        self._run.set_stopped(details)
        self._settings.set_enabled(True)
        self._targets.set_enabled(True)
        self._results.show_error(details, cancelled=True)
        self._workspace.reveal(self._results)
        self.status_message.emit("已停止執行", 3000)
        self._update_ready()
