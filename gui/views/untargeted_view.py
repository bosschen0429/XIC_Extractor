from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from gui.config_io import read_settings
from gui.dialogs import confirm_stop
from gui.discovery_config_io import read_discovery_config, write_discovery_config
from gui.sections.discovery_method_section import DiscoveryMethodSection
from gui.sections.discovery_results_section import DiscoveryResultsSection
from gui.sections.discovery_samples_section import DiscoverySamplesSection
from gui.sections.discovery_source_section import DiscoverySourceSection
from gui.sections.run_section import RunSection
from gui.ui import AnalysisWorkspace
from gui.workers.discovery_worker import (
    DiscoveryMode,
    DiscoveryRequest,
    DiscoveryWorker,
)
from xic_extractor.configuration.execution_profile import resolve_execution_profile
from xic_extractor.presets import filter_cid_nl_recipe_presets, list_presets


class UntargetedView(QWidget):
    status_message = pyqtSignal(str, int)
    state_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._worker: DiscoveryWorker | None = None
        self._terminal_state: str | None = None
        self._stop_requested = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._source = DiscoverySourceSection()
        self._samples = DiscoverySamplesSection()
        self._method = DiscoveryMethodSection(
            presets=filter_cid_nl_recipe_presets(list_presets())
        )
        self._run = RunSection()
        self._results = DiscoveryResultsSection()
        self._results.status_message.connect(self.status_message)
        self._workspace = AnalysisWorkspace(self._run)
        for section in (self._source, self._method, self._samples, self._results):
            self._workspace.add_section(section)
        layout.addWidget(self._workspace)

        state = read_discovery_config()
        self._source.load(state)
        self._method.load(state)
        self._sync_samples_source()
        sequence_source = str(state.get("sequence_source") or "").strip()
        if sequence_source:
            self._samples.load_sequence_source(sequence_source)
        self._samples.load_custom_roles(state.get("custom_roles") or {})

        self._run.run_clicked.connect(self._on_run)
        self._run.stop_clicked.connect(self._on_stop)
        self._run.problems_clicked.connect(self._show_problems)
        self._source.changed.connect(self._on_source_changed)
        self._source.reset_requested.connect(self._on_reset_batch)
        self._source.new_batch_requested.connect(self._on_next_batch)
        self._samples.changed.connect(self._on_config_changed)
        self._method.changed.connect(self._on_config_changed)
        self._samples.details_requested.connect(self._show_samples)
        self._workspace.detail_closed.connect(
            lambda _section: self._samples.set_detail_mode(False)
        )
        self._update_ready()

    def _on_reset_batch(self) -> None:
        self._workspace.show_overview()
        self._samples.clear_all()
        self._method.load({"preset": "dna_dr", "overrides": {}})
        self._clear_run()
        self._update_ready()
        self.status_message.emit("已清空批次設定，可開始配置新批次", 3000)

    def _on_next_batch(self) -> None:
        if self.is_busy():
            return
        self._workspace.show_overview()
        self._source.prepare_next_batch()
        self._samples.clear_all()
        self._clear_run()
        self._update_ready()
        self.status_message.emit(
            "已沿用方法、內標與環境；請選擇本批資料及輸出位置", 5000
        )

    def _clear_run(self) -> None:
        self._terminal_state = None
        self._run.reset()
        self._results.clear_results()

    def _show_samples(self) -> None:
        self._samples.set_detail_mode(True)
        self._workspace.open_details(self._samples)

    def _show_problems(self) -> None:
        section = self._source if self._source.missing_fields() else self._method
        if section is self._source:
            self._source.reveal_invalid()
        self._workspace.reveal(section)

    def _on_source_changed(self) -> None:
        self._sync_samples_source()
        self._on_config_changed()

    def _on_config_changed(self) -> None:
        if not self.is_busy() and self._terminal_state is not None:
            self._terminal_state = None
            self._run.reset()
            self._results.mark_previous()
        self._update_ready()

    def _sync_samples_source(self, custom_roles: dict[str, str] | None = None) -> None:
        values = self._source.get_values()
        raw_dir = _optional_path(values.get("raw_dir"))
        dll_dir = _optional_path(values.get("dll_dir"))
        mode = values.get("mode", "full")
        self._samples.setVisible(mode in ("full", "align_only"))
        self._samples.set_sources(raw_dir, dll_dir, custom_roles=custom_roles)

    def is_busy(self) -> bool:
        return self._worker is not None

    def has_active_report(self) -> bool:
        return self._results.is_report_busy()

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
        if self._results.is_report_busy():
            self._results.stop_report_for_close()

    def _update_ready(self) -> None:
        missing = self._source.missing_fields()
        if not self._method.is_valid():
            missing.append(self._method.validation_reason())
        self._run.set_ready(not missing, missing)
        if self._worker is not None:
            self.state_changed.emit("停止中…" if self._stop_requested else "執行中…")
        elif self._terminal_state is not None:
            self.state_changed.emit(self._terminal_state)
        elif missing:
            self.state_changed.emit(f"缺 {len(missing)} 項待補")
        else:
            preset = str(self._method.get_values().get("preset", "")).strip()
            self.state_changed.emit(f"就緒 · {preset}" if preset else "就緒")

    def _current_config(self) -> dict[str, Any]:
        return {
            **self._source.get_values(),
            **self._method.get_values(),
            "sequence_source": self._samples.get_sequence_source(),
            "custom_roles": self._samples.get_custom_roles(),
        }

    def _on_run(self) -> None:
        if self._worker is not None:
            self.status_message.emit("已有 Untargeted 執行正在進行", 3000)
            return
        if not self._source.is_valid() or not self._method.is_valid():
            self.status_message.emit("設定值無效，請修正後再執行", 5000)
            return

        config = self._current_config()
        try:
            request = self._build_request(config)
        except (OSError, ValueError) as exc:
            self.status_message.emit(f"執行設定無效：{exc}", 5000)
            return
        write_discovery_config(config)
        self._terminal_state = None
        self._stop_requested = False
        self._results.clear_results()

        self._source.set_enabled(False)
        self._samples.set_enabled(False)
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
            targets_csv=_optional_path(config.get("targets_csv")),
            sample_metadata=self._samples.get_sample_metadata(),
            preset_snapshot=self._method.selected_method(),
            execution_profile=resolve_execution_profile(read_settings()),
        )

    def _on_stop(self, *, confirm: bool = True) -> None:
        worker = self._worker
        if worker is not None:
            if self._stop_requested:
                return
            if confirm and not confirm_stop(self):
                return
            self._stop_requested = True
            self._run.set_stopping()
            self.state_changed.emit("停止中…")
            worker.stop()
            if not worker.wait(1000):
                self.status_message.emit("正在停止，等待目前階段結束...", 5000)
                return
            # The queued worker signal owns the terminal outcome, even when
            # shutdown completes here: a concurrent failure must stay a failure.

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
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "完成"
        self._run.set_complete(_complete_total(summary))
        self._run.set_running(False)
        self._restore_controls()
        self._results.update_results(summary)
        self._workspace.reveal(self._results)
        self.status_message.emit("Untargeted 執行完成", 5000)

    def _on_error(self, worker: DiscoveryWorker, message: str) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "執行失敗"
        self._run.set_running(False)
        self._run.set_error(message)
        self._restore_controls()
        self._results.show_error(message)
        self._workspace.reveal(self._results)
        self.status_message.emit(f"執行失敗: {message}", 5000)

    def _on_cancelled(self, worker: DiscoveryWorker) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        self._stop_requested = False
        self._terminal_state = "已停止"
        self._run.set_stopped()
        self._restore_controls()
        self.status_message.emit("已停止執行", 3000)

    def _restore_controls(self) -> None:
        self._source.set_enabled(True)
        self._samples.set_enabled(True)
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
