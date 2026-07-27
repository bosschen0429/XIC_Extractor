import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal

from xic_extractor import config as config_module
from xic_extractor import extractor
from xic_extractor.config import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.targeted_diagnostic_application import (
    build_canonical_targeted_application_service,
)
from xic_extractor.extraction.targeted_service import (
    build_targeted_application_request,
)
from xic_extractor.extractor import ExtractionResult, RunOutput
from xic_extractor.output.excel_pipeline import write_excel_from_run_output
from xic_extractor.raw_reader import RawReaderError


class PipelineWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, config_dir: Path) -> None:
        super().__init__()
        self._config_dir = config_dir

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            config, targets = config_module.load_config(self._config_dir)
            output = build_canonical_targeted_application_service(
                extractor.run
            ).execute_application(
                build_targeted_application_request(
                    config,
                    targets,
                    progress_callback=self.progress.emit,
                    should_stop=self.isInterruptionRequested,
                )
            ).active_output
            if self.isInterruptionRequested():
                self.cancelled.emit()
                return
            excel_path = write_excel_from_run_output(
                config,
                targets,
                output,
                output_path=_excel_output_path(config),
            )
            summary = build_summary(config, targets, output, excel_path)
            self.finished.emit(summary)
        except ConfigError as exc:
            self.error.emit(f"設定檔錯誤：{exc}")
        except RawReaderError as exc:
            self.error.emit(f"Raw file 讀取失敗：{exc}")
        except Exception as exc:
            self.error.emit(str(exc))


def _excel_output_path(config: ExtractionConfig) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return config.output_csv.parent / f"xic_results_{timestamp}.xlsx"


def build_summary(
    config: ExtractionConfig,
    targets: list[Target],
    output: RunOutput,
    excel_path: Path,
) -> dict[str, Any]:
    target_summaries = [_target_summary(config, target, output) for target in targets]
    return {
        "total_files": len(output.file_results),
        "excel_path": str(excel_path),
        "targets": target_summaries,
        "istd_warnings": [
            {
                "label": summary["label"],
                "detected": summary["detected"],
                "total": summary["total"],
            }
            for target, summary in zip(targets, target_summaries)
            if target.is_istd and summary["detected"] < summary["total"]
        ],
        "diagnostics_count": len(output.diagnostics),
    }


def _target_summary(
    config: ExtractionConfig,
    target: Target,
    output: RunOutput,
) -> dict[str, int | float | str | None]:
    detected = 0
    nl_ok = 0
    nl_warn = 0
    nl_fail = 0
    nl_no_ms2 = 0
    detected_areas: list[float] = []

    for file_result in output.file_results:
        result = file_result.results.get(target.label)
        if result is None:
            continue
        if result.nl is not None:
            if result.nl.status == "OK":
                nl_ok += 1
            elif result.nl.status == "WARN":
                nl_warn += 1
            elif result.nl.status == "NL_FAIL":
                nl_fail += 1
            elif result.nl.status == "NO_MS2":
                nl_no_ms2 += 1
        if _is_detected(config, target, result):
            detected += 1
            if result.peak_result.peak is not None:
                detected_areas.append(result.peak_result.peak.area)

    median_area = statistics.median(detected_areas) if detected_areas else None
    return {
        "label": target.label,
        "detected": detected,
        "total": len(output.file_results),
        "nl_ok": nl_ok,
        "nl_warn": nl_warn,
        "nl_fail": nl_fail,
        "nl_no_ms2": nl_no_ms2,
        "median_area": median_area,
    }


def _is_detected(
    config: ExtractionConfig,
    target: Target,
    result: ExtractionResult,
) -> bool:
    if result.peak_result.status != "OK" or result.peak_result.peak is None:
        return False
    if target.neutral_loss_da is None:
        return True
    if result.nl is None:
        return False
    if result.nl.status in {"OK", "WARN"}:
        return True
    return config.count_no_ms2_as_detected and result.nl.status == "NO_MS2"
