"""Background Inspector metadata and report calls for the Untargeted GUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PyQt6.QtCore import QThread, pyqtSignal

from xic_inspector.api import (
    EvidenceLocator,
    InspectorCancelled,
    InspectorError,
    ReportProgress,
    ReportResult,
    SelectionRequest,
    generate_report,
    read_evidence_summary,
)


@dataclass(frozen=True, slots=True)
class InspectorJob:
    source: EvidenceLocator
    request: SelectionRequest
    output_path: Path
    open_after_export: bool


class InspectorSummaryWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, source: EvidenceLocator) -> None:
        super().__init__()
        self.source = source

    def run(self) -> None:
        try:
            summary = read_evidence_summary(self.source.report_path)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.loaded.emit(summary)


class InspectorReportWorker(QThread):
    progress = pyqtSignal(object)
    succeeded = pyqtSignal(object)
    cancelled = pyqtSignal()
    failed = pyqtSignal(int, str)

    def __init__(self, job: InspectorJob) -> None:
        super().__init__()
        self.job = job
        self._cancel = Event()

    def stop(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result: ReportResult = generate_report(
                self.job.source.report_path,
                self.job.request,
                self.job.output_path,
                expected_source=self.job.source,
                on_progress=self._emit_progress,
                is_cancelled=self._cancel.is_set,
            )
        except InspectorCancelled:
            self.cancelled.emit()
        except InspectorError as exc:
            self.failed.emit(exc.code, exc.message)
        except Exception as exc:
            self.failed.emit(6, str(exc))
        else:
            self.succeeded.emit(result)

    def _emit_progress(self, snapshot: ReportProgress) -> None:
        self.progress.emit(snapshot)
