"""Targeted execution for the local HTML workbench."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from xic_extractor import extractor
from xic_extractor.analysis_set import discover_raw_files
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.configuration import loader as config_loader
from xic_extractor.configuration.models import ConfigError, ExtractionConfig, Target
from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    METHOD_KEYS,
)


class _TargetSummary(TypedDict):
    label: str
    detected: int
    total: int
    nl_ok: int
    nl_warn: int
    nl_fail: int
    nl_no_ms2: int
    median_area: float | None


class TargetedRun:
    """Run one Targeted application request without a UI-framework dependency."""

    def __init__(
        self,
        config_dir: Path,
        *,
        output_dir: Path | None = None,
        progress: Callable[[int, int, str], None],
        should_cancel: Callable[[], bool],
    ) -> None:
        config_dir = Path(config_dir)
        prepared = config_loader.prepare_targeted_config(
            config_dir, output_dir=output_dir
        )
        self._prepared = prepared
        self._config, self._targets = prepared.config, list(prepared.targets)
        # Admission fixes the discovered file set, not analysis roles or RAW bytes.
        self._raw_paths = discover_raw_files(self._config.data_dir)
        self._progress = progress
        self._should_cancel = should_cancel
        self.stage = "讀取 Targeted 設定"
        self.output_dir = self._config.output_csv.parent
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Targeted 執行已停止。"

    def execute(self) -> dict[str, Any]:
        try:
            return self._execute()
        finally:
            self._prepared.cleanup()

    def _execute(self) -> dict[str, Any]:
        if (
            not self._config.data_dir.is_dir()
            or discover_raw_files(self._config.data_dir) != self._raw_paths
        ):
            raise ConfigError("排隊後 RAW 檔案集合已變更；請確認本批次資料並重新提交。")
        config, targets = self._config, self._targets
        self.output_dir = config.output_csv.parent
        excel_path = _excel_output_path(config)
        self.stage = "Targeted 分析與結果發布"
        try:
            application = build_canonical_targeted_application_service(
                extractor.acquire
            )
            request = build_targeted_application_request(
                config,
                targets,
                progress_callback=self._progress,
                should_stop=self._should_cancel,
                excel_output_path=excel_path,
            )
            execution = application.execute_application(request)
        except InterruptedError as exc:
            if str(exc):
                self.cancellation_reason = str(exc)
            raise
        published_paths = tuple(Path(path) for path in execution.published_paths)
        self.available_outputs = tuple(
            ("已發布 Targeted 結果", path) for path in published_paths if path.is_file()
        )
        if self._should_cancel() and not published_paths:
            raise InterruptedError(self.cancellation_reason)

        self.stage = "Targeted 結果摘要"
        summary = build_summary(config, targets, execution.active_output, excel_path)
        summary["published_paths"] = [str(path) for path in published_paths]
        return summary


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
            for target, summary in zip(targets, target_summaries, strict=True)
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


# Labels and controls are presentation only; METHOD_KEYS owns method membership.
_SETTING_FIELDS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("data_dir", "RAW 資料夾", "folder", {}),
    (
        "injection_order_source",
        "上機資訊",
        "file",
        {
            # The same 上機資訊 files as Untargeted.
            "browse": "metadata",
            "help": "Word、Excel、CSV 或 TSV。補充樣本角色、組別與排除標記；"
            "注射順序與樣本名稱以 RAW 為準。",
        },
    ),
    ("dll_dir", "RawFileReader DLL 資料夾", "folder", {}),
    (
        "parallel_workers",
        "平行處理數",
        "number",
        {
            "min": 1,
            "step": 1,
            "help": "同時處理的 RAW 數，Untargeted 也使用此值。"
            "留空時為 CPU 核心數減一（至少 1）。",
        },
    ),
)


def setting_fields() -> list[dict[str, Any]]:
    fields = []
    for key, label, kind, options in _SETTING_FIELDS:
        scope = (
            "method"
            if key in METHOD_KEYS
            else "batch"
            if key in {"data_dir", "injection_order_source"}
            else "environment"
            if key == "dll_dir"
            else "runtime"
            if key.startswith("parallel_")
            else "output"
        )
        fields.append(
            dict(
                key=key,
                label=label,
                kind=kind,
                scope=scope,
                default=CANONICAL_SETTINGS_DEFAULTS[key],
                **options,
            )
        )
    return fields


def method_summary(settings: dict[str, str]) -> dict[str, Any]:
    return {"label": "共用 CID-NL 策略", "custom_keys": [], "differences": []}
