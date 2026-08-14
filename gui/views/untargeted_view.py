from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from gui.dialogs import confirm_stop
from gui.discovery_config_io import read_discovery_config, write_discovery_config
from gui.sections.discovery_method_section import DiscoveryMethodSection
from gui.sections.discovery_results_section import DiscoveryResultsSection
from gui.sections.discovery_source_section import DiscoverySourceSection
from gui.sections.run_section import RunSection
from gui.workers.discovery_worker import (
    DiscoveryMode,
    DiscoveryRequest,
    DiscoveryWorker,
)
from xic_extractor.presets import filter_cid_nl_recipe_presets, list_presets


class UntargetedView(QWidget):
    status_message = pyqtSignal(str, int)
    state_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._worker: DiscoveryWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._source = DiscoverySourceSection()
        self._method = DiscoveryMethodSection(
            presets=filter_cid_nl_recipe_presets(list_presets())
        )
        self._run = RunSection()
        self._results = DiscoveryResultsSection()

        layout.addWidget(self._source)
        layout.addWidget(self._method)
        layout.addWidget(self._run)
        layout.addWidget(self._results)
        layout.addStretch()

        state = read_discovery_config()
        self._source.load(state)
        self._method.load(state)

        self._run.run_clicked.connect(self._on_run)
        self._run.stop_clicked.connect(self._on_stop)
        self._source.changed.connect(self._update_ready)
        self._method.changed.connect(self._update_ready)
        self._update_ready()

    def is_busy(self) -> bool:
        return self._worker is not None

    def persist_config(self) -> None:
        """Save the current source/method config so paths (RAW/DLL/output)
        survive a restart even without a run — set-once stickiness."""
        write_discovery_config(self._current_config())

    def emit_state(self) -> None:
        self._update_ready()

    def refresh_theme(self) -> None:
        self._method.refresh_theme()
        self._results.refresh_theme()

    def trigger_run(self) -> None:
        if self._worker is None:
            self._on_run()

    def trigger_stop(self, *, confirm: bool = True) -> None:
        if self._worker is not None:
            self._on_stop(confirm=confirm)

    def _update_ready(self) -> None:
        missing = self._source.missing_fields()
        if not self._method.is_valid():
            missing.append(self._method.validation_reason())
        self._run.set_ready(not missing, missing)
        if missing:
            self.state_changed.emit(f"缺 {len(missing)} 項待補")
        else:
            preset = str(self._method.get_values().get("preset", "")).strip()
            self.state_changed.emit(f"就緒 · {preset}" if preset else "就緒")

    def _current_config(self) -> dict[str, Any]:
        return {**self._source.get_values(), **self._method.get_values()}

    def _on_run(self) -> None:
        if self._worker is not None:
            self.status_message.emit("已有 Untargeted 執行正在進行", 3000)
            return
        if not self._source.is_valid() or not self._method.is_valid():
            self.status_message.emit("設定值無效，請修正後再執行", 5000)
            return

        config = self._current_config()
        write_discovery_config(config)
        request = self._build_request(config)

        self._source.set_enabled(False)
        self._method.set_enabled(False)
        self._run.set_running(True)
        self.status_message.emit("開始執行 Untargeted...", 0)
        self.state_changed.emit("執行中…")

        worker = DiscoveryWorker(request)
        self._worker = worker
        worker.progress.connect(
            lambda current, total, filename: self._on_progress(
                worker,
                current,
                total,
                filename,
            )
        )
        worker.finished.connect(lambda summary: self._on_finished(worker, summary))
        worker.error.connect(lambda message: self._on_error(worker, message))
        worker.cancelled.connect(lambda: self._on_cancelled(worker))
        worker.start()

    def _build_request(self, config: dict[str, Any]) -> DiscoveryRequest:
        if not self._method.is_valid():
            raise ValueError(self._method.validation_reason())
        return DiscoveryRequest(
            mode=cast(DiscoveryMode, str(config["mode"])),
            preset=str(config["preset"]),
            tuning_overrides=dict(config.get("overrides") or {}),
            raw_dir=_optional_path(config.get("raw_dir")),
            raw_file=_optional_path(config.get("raw_file")),
            dll_dir=Path(str(config["dll_dir"])),
            output_dir=Path(str(config["output_dir"])),
            discovery_batch_index=_optional_path(config.get("discovery_batch_index")),
        )

    def _on_stop(self, *, confirm: bool = True) -> None:
        worker = self._worker
        if worker is not None:
            if confirm and not confirm_stop(self):
                return
            worker.stop()
            if not worker.wait(1000):
                self.status_message.emit("正在停止，等待目前階段結束...", 5000)
                return
            self._worker = None
        self._run.set_running(False)
        self._restore_controls()
        self.status_message.emit("已停止執行", 3000)

    def _on_progress(
        self,
        worker: DiscoveryWorker,
        current: int,
        total: int,
        filename: str,
    ) -> None:
        if self._worker is not worker:
            return
        self._run.set_progress(current, total, filename)

    def _on_finished(self, worker: DiscoveryWorker, summary: dict[str, Any]) -> None:
        if self._worker is not worker:
            return
        self._run.set_complete(_complete_total(summary))
        self._run.set_running(False)
        self._restore_controls()
        self._results.update_results(summary)
        self.status_message.emit("Untargeted 執行完成", 5000)
        self._worker = None

    def _on_error(self, worker: DiscoveryWorker, message: str) -> None:
        if self._worker is not worker:
            return
        self._run.set_running(False)
        self._run.set_error(message)
        self._restore_controls()
        self._results.show_error(message)
        self.status_message.emit(f"執行失敗: {message}", 5000)
        self._worker = None

    def _on_cancelled(self, worker: DiscoveryWorker) -> None:
        if self._worker is not worker:
            return
        self._run.set_running(False)
        self._restore_controls()
        self.status_message.emit("已停止執行", 3000)
        self._worker = None

    def _restore_controls(self) -> None:
        self._source.set_enabled(True)
        self._method.set_enabled(True)
        self._update_ready()


def _optional_path(value: object) -> Path | None:
    text = str(value or "").strip()
    return Path(text) if text else None


def _complete_total(summary: dict[str, Any]) -> int:
    value = summary.get("sample_count")
    if value is None:
        return 0
    return int(value)
