import statistics
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from PyQt6.QtCore import QThread, pyqtSignal

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.configuration import loader as config_loader
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extractor import ExtractionResult, RunOutput
from xic_extractor.output.method_manifest import MethodManifestContext
from xic_extractor.raw_reader import RawReaderError


class _TargetSummary(TypedDict):
    label: str
    detected: int
    total: int
    nl_ok: int
    nl_warn: int
    nl_fail: int
    nl_no_ms2: int
    median_area: float | None


class PipelineWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, config_dir: Path) -> None:
        super().__init__()
        self._config_dir = config_dir
        self.stage = "讀取 Targeted 設定"
        self.output_dir = config_dir.parent / "output"
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Targeted 執行已停止。"

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            config, targets = config_loader.load_config(self._config_dir)
            self.output_dir = config.output_csv.parent
            excel_path = _excel_output_path(config)
            self.stage = "Targeted 分析與結果發布"
            execution = build_canonical_targeted_application_service(
                extractor.acquire
            ).execute_application(
                build_targeted_application_request(
                    config,
                    targets,
                    progress_callback=self.progress.emit,
                    should_stop=self.isInterruptionRequested,
                    method_manifest_context=MethodManifestContext(
                        entrypoint="xic-extractor-gui",
                        config_dir=self._config_dir,
                        output_mode="excel",
                    ),
                    excel_output_path=excel_path,
                )
            )
            self.available_outputs = tuple(
                ("已發布 Targeted 結果", Path(path))
                for path in execution.published_paths
                if Path(path).is_file()
            )
            output = execution.active_output
            if self.isInterruptionRequested() and not execution.published_paths:
                self.cancelled.emit()
                return
            self.stage = "Targeted 結果摘要"
            summary = build_summary(config, targets, output, excel_path)
            self.finished.emit(summary)
        except ConfigError as exc:
            self.error.emit(f"設定檔錯誤：{exc}")
        except RawReaderError as exc:
            self.error.emit(f"Raw file 讀取失敗：{exc}")
        except InterruptedError as exc:
            self.cancellation_reason = str(exc) or self.cancellation_reason
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc) or type(exc).__name__)


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
) -> _TargetSummary:
    detected = 0
    nl_ok = 0
    nl_warn = 0
    nl_fail = 0
    nl_no_ms2 = 0
    accepted_areas: list[float] = []

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
        if result.reported_peak_area is not None:
            accepted_areas.append(result.reported_peak_area)

    median_area = statistics.median(accepted_areas) if accepted_areas else None
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
    projection = result.targeted_product_projection
    if projection is not None:
        return projection.counted_detection
    if result.peak_result.status != "OK" or result.peak_result.peak is None:
        return False
    if target.neutral_loss_da is None:
        return True
    if result.nl is None:
        return False
    if result.nl.status in {"OK", "WARN"}:
        return True
    return config.count_no_ms2_as_detected and result.nl.status == "NO_MS2"
