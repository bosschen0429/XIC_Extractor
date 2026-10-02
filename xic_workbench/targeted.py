"""Targeted execution and form metadata for the local HTML workbench."""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, TypedDict

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.configuration import loader as config_loader
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.settings import migrate_settings_dict
from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
from xic_extractor.output.method_manifest import MethodManifestContext
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

_VISIBLE_SETTINGS: tuple[dict[str, Any], ...] = (
    {
        "key": "data_dir",
        "label": "資料目錄",
        "kind": "folder",
        "group": "資料與環境",
    },
    {
        "key": "dll_dir",
        "label": "DLL 目錄",
        "kind": "folder",
        "group": "資料與環境",
    },
    {
        "key": "signal_processing_window_points",
        "label": "Gaussian 視窗點數",
        "kind": "number",
        "group": "共用物理峰",
        "min": 3,
        "step": 2,
    },
    {
        "key": "ms2_precursor_tol_da",
        "label": "Precursor tol",
        "kind": "number",
        "group": "峰偵測進階參數",
        "min": 0.01,
        "step": 0.1,
    },
    {
        "key": "nl_min_intensity_ratio",
        "label": "Min intensity",
        "kind": "number",
        "group": "峰偵測進階參數",
        "min": 0.01,
        "step": 0.01,
    },
    {
        "key": "keep_intermediate_csv",
        "label": "保留中間 CSV 檔",
        "kind": "checkbox",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "emit_score_breakdown",
        "label": "輸出 Score Breakdown sheet",
        "kind": "checkbox",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "emit_review_report",
        "label": "輸出 Review Report HTML",
        "kind": "checkbox",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "dirty_matrix_mode",
        "label": "啟用 dirty matrix mode",
        "kind": "checkbox",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "count_no_ms2_as_detected",
        "label": "將 NO_MS2 視為偵測到",
        "kind": "checkbox",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "rolling_window_size",
        "label": "Rolling window size",
        "kind": "number",
        "group": "除錯與方法開發設定",
        "min": 1,
        "step": 1,
    },
    {
        "key": "parallel_mode",
        "label": "Mode",
        "kind": "select",
        "group": "除錯與方法開發設定",
        "options": ["serial", "process"],
    },
    {
        "key": "parallel_workers",
        "label": "Workers",
        "kind": "number",
        "group": "除錯與方法開發設定",
        "min": 1,
        "step": 1,
    },
    {
        "key": "rt_prior_library_path",
        "label": "RT prior library (developer/debug)",
        "kind": "file",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "target_pair_rt_calibration_path",
        "label": "Target pair RT calibration (RT priors)",
        "kind": "file",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "injection_order_source",
        "label": "Injection order source",
        "kind": "file",
        "group": "除錯與方法開發設定",
    },
    {
        "key": "nl_rt_anchor_search_margin_min",
        "label": "NL search",
        "kind": "number",
        "group": "除錯與方法開發設定",
        "min": 0.0,
    },
    {
        "key": "nl_rt_anchor_half_window_min",
        "label": "NL anchor window",
        "kind": "number",
        "group": "除錯與方法開發設定",
        "min": 0.0,
    },
    {
        "key": "nl_fallback_half_window_min",
        "label": "NL fallback",
        "kind": "number",
        "group": "除錯與方法開發設定",
        "min": 0.0,
    },
)

_PASSTHROUGH_SETTINGS = (
    "signal_processing_method",
    "model_selection_expected_diff_approval_registry",
    "targeted_ms1_shape_identity_support_tsv",
    "targeted_ms1_shape_identity_activation_policy",
)


def targeted_form() -> dict[str, Any]:
    """Describe editable Targeted settings and their canonical defaults."""

    keys = tuple(field["key"] for field in _VISIBLE_SETTINGS) + _PASSTHROUGH_SETTINGS
    return {
        "settings": [
            {
                key: list(value) if isinstance(value, list) else value
                for key, value in field.items()
            }
            for field in _VISIBLE_SETTINGS
        ],
        "defaults": {key: str(CANONICAL_SETTINGS_DEFAULTS[key]) for key in keys},
        "passthrough_settings": list(_PASSTHROUGH_SETTINGS),
    }


def method_summary(settings: dict[str, str]) -> dict[str, Any]:
    """Describe whether persisted method settings differ from canonical defaults."""

    migrated, _warnings = migrate_settings_dict(settings)
    excluded = {"data_dir", "dll_dir", "ms1_morphology_smoothing_window_points"}
    custom_keys = [
        key
        for key, default in CANONICAL_SETTINGS_DEFAULTS.items()
        if key not in excluded
        and not _setting_values_equal(migrated.get(key, ""), default)
    ]
    label = f"自訂方法 · {len(custom_keys)} 項調整" if custom_keys else "標準方法"
    return {"label": label, "custom_keys": custom_keys}


def _setting_values_equal(actual: str, expected: str) -> bool:
    actual_text = str(actual).strip()
    expected_text = str(expected).strip()
    if expected_text.lower() in {"true", "false"}:
        return actual_text.lower() == expected_text.lower()
    try:
        return Decimal(actual_text) == Decimal(expected_text)
    except InvalidOperation:
        return actual_text == expected_text


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
        progress: Callable[[int, int, str], None],
        should_cancel: Callable[[], bool],
    ) -> None:
        self._config_dir = Path(config_dir)
        self._progress = progress
        self._should_cancel = should_cancel
        self.stage = "讀取 Targeted 設定"
        self.output_dir = self._config_dir.parent / "output"
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Targeted 執行已停止。"

    def execute(self) -> dict[str, Any]:
        config, targets = config_loader.load_config(self._config_dir)
        self.output_dir = config.output_csv.parent
        excel_path = _excel_output_path(config)
        self.stage = "Targeted 分析與結果發布"
        try:
            execution = build_canonical_targeted_application_service(
                extractor.acquire
            ).execute_application(
                build_targeted_application_request(
                    config,
                    targets,
                    progress_callback=self._progress,
                    should_stop=self._should_cancel,
                    method_manifest_context=MethodManifestContext(
                        entrypoint="xic-extractor-html",
                        config_dir=self._config_dir,
                        output_mode="excel",
                    ),
                    excel_output_path=excel_path,
                )
            )
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
