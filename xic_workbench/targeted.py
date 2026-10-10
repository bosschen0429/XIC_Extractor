"""Targeted execution for the local HTML workbench."""

from __future__ import annotations

import statistics
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from xic_extractor import extractor
from xic_extractor.applications.targeted.application import (
    build_canonical_targeted_application_service,
    build_targeted_application_request,
)
from xic_extractor.configuration import loader as config_loader
from xic_extractor.configuration.csv_io import _config_input_path
from xic_extractor.configuration.models import ExtractionConfig, Target
from xic_extractor.configuration.settings import method_settings_differences
from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
from xic_extractor.output.method_manifest import MethodManifestContext
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
    METHOD_KEYS,
    RESOURCE_KEYS,
)
from xic_extractor.targeted_ms1_shape_identity_policy import (
    TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES,
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
        progress: Callable[[int, int, str], None],
        should_cancel: Callable[[], bool],
    ) -> None:
        config_dir = Path(config_dir)
        # These are immutable execution receipts, not another active configuration.
        # The manifest and computation must consume the same bytes after dispatch.
        self._config_dir = (
            config_dir.parent / "output" / ".run_inputs" / uuid.uuid4().hex / "config"
        )
        self._config_dir.mkdir(parents=True)
        for name in ("settings", "targets"):
            (self._config_dir / f"{name}.csv").write_bytes(
                _config_input_path(config_dir, name).read_bytes()
            )
        self._config, self._targets = config_loader.load_config(
            self._config_dir, output_dir=config_dir.parent / "output"
        )
        self._progress = progress
        self._should_cancel = should_cancel
        self.stage = "讀取 Targeted 設定"
        self.output_dir = self._config.output_csv.parent
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Targeted 執行已停止。"

    def execute(self) -> dict[str, Any]:
        config, targets = self._config, self._targets
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


# Labels and controls are presentation only; METHOD_KEYS owns method membership.
_SETTING_FIELDS: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("data_dir", "資料目錄", "folder", {}),
    ("dll_dir", "DLL 目錄", "folder", {}),
    ("signal_processing_method", "平滑方法", "readonly", {"options": ["gaussian"]}),
    (
        "signal_processing_window_points",
        "Gaussian 平滑視窗（點）",
        "number",
        {"min": 3, "step": 2, "help": "奇數 3–999。"},
    ),
    (
        "ms2_precursor_tol_da",
        "MS2 precursor 容差（Da）",
        "number",
        {
            "min": 0.01,
            "step": 0.1,
            "help": "掃描 precursor 與推算 precursor 的最大差距。",
        },
    ),
    (
        "nl_min_intensity_ratio",
        "NL product 最低強度比",
        "number",
        {"min": 0.01, "step": 0.01, "help": "相對於該 scan 的 base peak。"},
    ),
    (
        "count_no_ms2_as_detected",
        "將 NO_MS2 視為偵測到",
        "readonly",
        {
            "help": "相容設定；正式 Targeted 結果不將 NO_MS2 算為偵測到。",
            "compatibility_only": True,
        },
    ),
    (
        "injection_order_source",
        "注射順序來源",
        "file",
        {
            "help": "CSV／XLSX 注射順序；留空時使用 RAW 修改時間。"
        },
    ),
    (
        "rolling_window_size",
        "ISTD RT prior 滾動視窗（± 注射數）",
        "number",
        {"min": 1, "step": 1},
    ),
    (
        "dirty_matrix_mode",
        "複雜基質模式",
        "checkbox",
        {"help": "放寬 S/N、收緊峰形；用於尿液等複雜基質。"},
    ),
    (
        "rt_prior_library_path",
        "RT prior 參考資料庫",
        "file",
        {"help": "一般使用請留空。"},
    ),
    (
        "target_pair_rt_calibration_path",
        "Target pair RT 校正",
        "file",
        {"help": "RT prior 使用的 TSV；留空停用。"},
    ),
    (
        "model_selection_expected_diff_approval_registry",
        "預期差異核准紀錄",
        "file",
        {"help": "已核准的 expected-diff registry TSV；沒有對應核准紀錄時留空。"},
    ),
    (
        "targeted_ms1_shape_identity_support_tsv",
        "MS1 峰形身分支持資料",
        "file",
        {"help": "已審核的 support TSV；留空時由目前啟用政策處理，不隨方法參數匯出。"},
    ),
    (
        "targeted_ms1_shape_identity_activation_policy",
        "MS1 shape identity 啟用政策",
        "select",
        {"options": list(TARGETED_MS1_SHAPE_IDENTITY_ACTIVATION_POLICIES)},
    ),
    ("emit_score_breakdown", "輸出 Score Breakdown 工作表", "checkbox", {}),
    ("emit_review_report", "輸出 Review Report HTML", "checkbox", {}),
    ("keep_intermediate_csv", "保留中間 CSV 檔", "checkbox", {"help": "除錯用。"}),
    (
        "nl_rt_anchor_search_margin_min",
        "NL 錨點搜尋半徑（min）",
        "number",
        {"min": 0.0, "help": "在目標 RT 中心 ± 此值內尋找 NL 確認的 MS2。"},
    ),
    (
        "nl_rt_anchor_half_window_min",
        "錨定後 XIC 半寬（min）",
        "number",
        {"min": 0.0, "help": "找到錨點時，XIC 視窗為錨點 RT ± 此值。"},
    ),
    (
        "nl_fallback_half_window_min",
        "未錨定時 XIC 半寬（min）",
        "number",
        {"min": 0.0, "help": "找不到錨點時，XIC 視窗為 RT 中心 ± 此值。"},
    ),
    (
        "parallel_mode",
        "執行方式",
        "select",
        {
            "options": ["serial", "process"],
            "help": "process 平行處理；serial 逐一處理，供除錯使用。",
        },
    ),
    (
        "parallel_workers",
        "平行 worker 數",
        "number",
        {"min": 1, "step": 1, "help": "process 模式使用的行程數。"},
    ),
)


def setting_fields() -> list[dict[str, Any]]:
    fields = []
    for key, label, kind, options in _SETTING_FIELDS:
        scope = (
            "method"
            if key in METHOD_KEYS
            else "resource"
            if key in RESOURCE_KEYS
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
    differences = method_settings_differences(settings)
    return {
        "label": f"自訂參數 · {len(differences)} 項調整" if differences else "標準參數",
        "custom_keys": [row["key"] for row in differences],
        "differences": differences,
        "resource_keys": [
            key for key in RESOURCE_KEYS if settings.get(key, "").strip()
        ],
    }
