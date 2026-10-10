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
from xic_extractor.extraction.result_models import ExtractionResult, RunOutput
from xic_extractor.output.method_manifest import MethodManifestContext


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
