from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PyQt6.QtCore import QThread, pyqtSignal

from xic_extractor.applications.untargeted.application import run_untargeted_application
from xic_extractor.config import ExtractionConfig
from xic_extractor.configuration.execution_profile import (
    DEFAULT_ALIGNMENT_WORKER_BACKEND,
    ExecutionProfile,
)
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.pipeline import (
    run_discovery,
    run_discovery_batch,
)
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_from_config as _compile_processing_run_spec,
)
from xic_extractor.output.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.presets import (
    BoundCidNlRecipe,
    Preset,
    PresetError,
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.raw_reader import RawReaderError, read_analysis_set
from xic_extractor.sample_metadata import SampleMetadata
from xic_extractor.settings_schema import (
    CANONICAL_SETTINGS_DEFAULTS,
)

DiscoveryMode = Literal["full", "discovery_only", "align_only"]

# Keep the default GUI Alignment route on the primary Matrix artifact. Other
# renderers remain available through explicit Alignment output levels/flags.
_GUI_ALIGNMENT_KWARGS: dict[str, Any] = {
    "output_level": "production",
    "emit_alignment_cells": False,
    "emit_alignment_backfill_seed_audit": False,
    "audit_evidence_mode": "none",
}

_GALLERY_PARK_REASON = (
    "Backfill gallery / MS1 overlay 已退出一般 GUI 流程；保留為診斷工具。"
)

_ALIGNMENT_OUTPUT_ATTRS = (
    "review_tsv",
    "matrix_tsv",
    "cells_tsv",
    "run_metadata_json",
)


@dataclass
class DiscoveryRequest:
    mode: DiscoveryMode
    preset: str
    tuning_overrides: dict[str, Any]
    raw_dir: Path | None
    raw_file: Path | None
    dll_dir: Path
    output_dir: Path
    discovery_batch_index: Path | None
    targets_csv: Path | None = None
    sample_metadata: tuple[SampleMetadata, ...] | None = None
    preset_snapshot: Preset | None = None
    execution_profile: ExecutionProfile = field(default_factory=ExecutionProfile)


class DiscoveryWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, request: DiscoveryRequest) -> None:
        super().__init__()
        self._request = request
        self._recipe_binding: tuple[BoundCidNlRecipe, str] | None = None
        self.stage = "準備 Untargeted 執行"
        self.output_dir = request.output_dir
        self.available_outputs: tuple[tuple[str, Path], ...] = ()
        self.cancellation_reason = "使用者要求停止，本次 Untargeted 執行已停止。"

    def stop(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        request = self._request
        try:
            if request.mode == "full":
                summary = self._run_full(request)
            elif request.mode == "discovery_only":
                summary = self._run_discovery_only(request)
            elif request.mode == "align_only":
                summary = self._run_align_only(request)
            else:
                raise ValueError(f"unknown discovery mode: {request.mode}")
            if summary is None:
                self.cancelled.emit()
                return
            self.finished.emit(summary)
        except InterruptedError as exc:
            self.cancellation_reason = str(exc) or self.cancellation_reason
            self.cancelled.emit()
        except PresetError as exc:
            self.error.emit(f"Preset 錯誤：{exc}")
        except RawReaderError as exc:
            self.error.emit(f"Raw file 讀取失敗：{exc}")
        except Exception as exc:
            self.error.emit(str(exc) or type(exc).__name__)

    def _run_full(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        self.stage = "讀取 RAW 資料來源"
        raw_dir = request.raw_dir
        if raw_dir is None:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        raw_paths = _raw_paths_from_dir(
            raw_dir, request.dll_dir, request.sample_metadata
        )
        if not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")

        total_steps = len(raw_paths) + 1
        self.progress.emit(0, total_steps, "Discovery...")
        self.stage = "Discovery 設定"
        settings = self._build_settings(request)
        peak_config = self._discovery_peak_config(request, settings)
        run_spec = _compile_processing_run_spec(raw_paths, settings, peak_config)
        self.stage = "Discovery 擷取與結果發布"
        batch_outputs = run_discovery_batch(
            raw_paths,
            output_dir=request.output_dir,
            settings=settings,
            peak_config=peak_config,
            run_spec=run_spec,
            max_workers=_batch_discovery_workers(raw_paths, request.execution_profile),
            should_cancel=self.isInterruptionRequested,
            progress_callback=lambda done, total, sample: self.progress.emit(
                done, total + 1, f"Discovery: {sample}"
            ),
        )
        index_path = _batch_index_path(batch_outputs, request.output_dir)
        self._retain_discovery_index(batch_outputs)
        if self.isInterruptionRequested():
            return None

        self.progress.emit(len(raw_paths), total_steps, "Alignment...")
        outputs = self._run_alignment(
            request,
            index_path=index_path,
            raw_dir=raw_dir,
        )

        self.progress.emit(total_steps, total_steps, "完成")

        summary = self._discovery_summary(request, index_path=index_path)
        summary.update(_alignment_summary(outputs))
        summary.update(_parked_gallery_summary())
        return summary

    def _run_discovery_only(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        if request.raw_file is not None:
            self.progress.emit(0, 1, "Discovery...")
            self.stage = "Discovery 設定"
            settings = self._build_settings(request)
            peak_config = self._discovery_peak_config(request, settings)
            run_spec = _compile_processing_run_spec(
                (request.raw_file,), settings, peak_config
            )
            self.stage = "Discovery 擷取與結果發布"
            outputs = run_discovery(
                request.raw_file,
                output_dir=request.output_dir,
                settings=settings,
                peak_config=peak_config,
                run_spec=run_spec,
                should_cancel=self.isInterruptionRequested,
            )
            self.available_outputs = tuple(
                (label, Path(path))
                for label, path in (
                    ("Discovery 候選（中間產物）", outputs.candidates_csv),
                    ("Discovery review（中間產物）", outputs.review_csv),
                )
                if Path(path).is_file()
            )
            self.stage = "Discovery 結果摘要"
            self.progress.emit(1, 1, "完成")
            return {
                "mode": "discovery_only",
                "output_dir": str(request.output_dir),
                "sample_count": 1,
                "candidate_counts": _read_review_counts(outputs.review_csv),
                "discovery_candidates_csv": str(outputs.candidates_csv),
                "discovery_review_csv": str(outputs.review_csv),
                "discovery_batch_index": None,
                "alignment_outputs": {},
                "matrix_tsv": None,
                "gallery_html": None,
            }

        self.stage = "讀取 RAW 資料來源"
        raw_paths = _raw_paths_from_dir(
            request.raw_dir, request.dll_dir, request.sample_metadata
        )
        if not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        self.progress.emit(0, len(raw_paths), "Discovery...")
        self.stage = "Discovery 設定"
        settings = self._build_settings(request)
        peak_config = self._discovery_peak_config(request, settings)
        run_spec = _compile_processing_run_spec(raw_paths, settings, peak_config)
        self.stage = "Discovery 擷取與結果發布"
        batch_outputs = run_discovery_batch(
            raw_paths,
            output_dir=request.output_dir,
            settings=settings,
            peak_config=peak_config,
            run_spec=run_spec,
            max_workers=_batch_discovery_workers(raw_paths, request.execution_profile),
            should_cancel=self.isInterruptionRequested,
            progress_callback=self.progress.emit,
        )
        self._retain_discovery_index(batch_outputs)
        self.stage = "Discovery 結果摘要"
        self.progress.emit(len(raw_paths), len(raw_paths), "完成")
        return self._discovery_summary(
            request,
            index_path=_batch_index_path(batch_outputs, request.output_dir),
        )

    def _run_align_only(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        self.stage = "讀取 Discovery batch index"
        index_path = request.discovery_batch_index
        if index_path is None:
            raise ValueError("align-only 需要既有的 discovery_batch_index.csv")
        if request.raw_dir is None:
            raise ValueError("align-only 需要 raw_dir 來定位 RAW 檔案")
        candidate_counts, sample_count = _read_batch_counts(index_path)

        self.progress.emit(0, 1, "Alignment...")
        outputs = self._run_alignment(
            request,
            index_path=index_path,
            raw_dir=request.raw_dir,
        )

        self.progress.emit(1, 1, "完成")

        summary: dict[str, Any] = {
            "mode": "align_only",
            "output_dir": str(request.output_dir),
            "sample_count": sample_count,
            "candidate_counts": candidate_counts,
            "discovery_candidates_csv": None,
            "discovery_review_csv": None,
            "discovery_batch_index": str(index_path),
            "alignment_outputs": {},
            "matrix_tsv": None,
            "gallery_html": None,
        }
        summary.update(_alignment_summary(outputs))
        summary.update(_parked_gallery_summary())
        return summary

    def _run_alignment(
        self,
        request: DiscoveryRequest,
        *,
        index_path: Path,
        raw_dir: Path,
    ) -> AlignmentRunOutputs:
        self.stage = "Alignment 設定"
        recipe = self._alignment_runtime(request)
        self.stage = "Alignment 分析與 Matrix 發布"
        outputs = run_untargeted_application(
            discovery_batch_index=index_path,
            raw_dir=raw_dir,
            dll_dir=request.dll_dir,
            output_dir=request.output_dir,
            alignment_config=recipe.alignment_config,
            peak_config=self._alignment_peak_config(
                request,
                recipe.discovery_settings,
            ),
            discovery_settings=recipe.discovery_settings,
            owner_build_xic_backend=recipe.owner_build_xic_backend,
            sample_metadata=request.sample_metadata,
            targets_csv=request.targets_csv,
            raw_workers=request.execution_profile.alignment_workers,
            raw_worker_backend=DEFAULT_ALIGNMENT_WORKER_BACKEND,
            should_cancel=self.isInterruptionRequested,
            **_GUI_ALIGNMENT_KWARGS,
        )
        self.available_outputs += tuple(
            (f"Alignment {attr}", Path(path))
            for attr in _ALIGNMENT_OUTPUT_ATTRS
            if (path := getattr(outputs, attr, None)) is not None
            and Path(path).is_file()
        )
        self.stage = "Alignment 結果摘要"
        return outputs

    def _retain_discovery_index(self, outputs: Any) -> None:
        index_path = getattr(outputs, "batch_index_csv", None)
        if index_path is not None and Path(index_path).is_file():
            self.available_outputs = (
                ("Discovery batch index（中間產物）", Path(index_path)),
            )

    def _alignment_runtime(
        self,
        request: DiscoveryRequest,
    ) -> BoundCidNlRecipe:
        binding = self._recipe_binding
        if binding is None:
            preset = request.preset_snapshot or load_preset(request.preset)
            recipe = bind_cid_nl_recipe(
                preset,
                request.mode,
                recipe_overrides=request.tuning_overrides,
            )
            binding = (recipe, str(preset.source))
            self._recipe_binding = binding
        recipe, _preset_source = binding
        return recipe

    def _build_settings(self, request: DiscoveryRequest) -> DiscoverySettings:
        preset = request.preset_snapshot or load_preset(request.preset)
        recipe = bind_cid_nl_recipe(
            preset,
            request.mode,
            recipe_overrides=request.tuning_overrides,
        )
        self._recipe_binding = (recipe, str(preset.source))
        return recipe.discovery_settings

    def _discovery_peak_config(
        self,
        request: DiscoveryRequest,
        settings: DiscoverySettings,
    ) -> ExtractionConfig:
        data_dir = request.raw_dir
        if data_dir is None and request.raw_file is not None:
            data_dir = request.raw_file.parent
        if data_dir is None:
            data_dir = request.output_dir
        return self._peak_config(
            data_dir,
            request,
            nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
            resolver_mode=settings.resolver_mode,
        )

    def _alignment_peak_config(
        self,
        request: DiscoveryRequest,
        settings: DiscoverySettings,
    ) -> ExtractionConfig:
        return self._peak_config(
            request.raw_dir or request.output_dir,
            request,
            nl_min_intensity_ratio=settings.nl_min_intensity_ratio,
            resolver_mode=settings.resolver_mode,
        )

    def _peak_config(
        self,
        data_dir: Path,
        request: DiscoveryRequest,
        *,
        nl_min_intensity_ratio: float,
        resolver_mode: str,
    ) -> ExtractionConfig:
        defaults = CANONICAL_SETTINGS_DEFAULTS
        return ExtractionConfig(
            data_dir=data_dir,
            dll_dir=request.dll_dir,
            output_csv=request.output_dir / "xic_results.csv",
            diagnostics_csv=request.output_dir / "xic_diagnostics.csv",
            smooth_window=15,
            smooth_polyorder=3,
            ms1_morphology_smoothing_window_points=int(
                defaults["ms1_morphology_smoothing_window_points"]
            ),
            peak_rel_height=0.95,
            peak_min_prominence_ratio=0.10,
            ms2_precursor_tol_da=float(defaults["ms2_precursor_tol_da"]),
            nl_min_intensity_ratio=nl_min_intensity_ratio,
            resolver_mode=resolver_mode,
        )

    def _discovery_summary(
        self,
        request: DiscoveryRequest,
        *,
        index_path: Path,
    ) -> dict[str, Any]:
        counts, sample_count = _read_batch_counts(index_path)
        return {
            "mode": request.mode,
            "output_dir": str(request.output_dir),
            "sample_count": sample_count,
            "candidate_counts": counts,
            "discovery_candidates_csv": None,
            "discovery_review_csv": None,
            "discovery_batch_index": str(index_path),
            "alignment_outputs": {},
            "matrix_tsv": None,
            "gallery_html": None,
        }


def _batch_index_path(batch_outputs: Any, output_dir: Path) -> Path:
    return Path(
        getattr(
            batch_outputs,
            "batch_index_csv",
            output_dir / "discovery_batch_index.csv",
        )
    )


def _alignment_summary(
    outputs: Any,
) -> dict[str, Any]:
    alignment_outputs: dict[str, str] = {}
    for attr in _ALIGNMENT_OUTPUT_ATTRS:
        value = getattr(outputs, attr, None)
        if value is not None:
            alignment_outputs[attr] = str(value)
    matrix = getattr(outputs, "matrix_tsv", None)
    run_metadata = getattr(outputs, "run_metadata_json", None)
    evidence = getattr(outputs, "evidence", None)
    return {
        "alignment_outputs": alignment_outputs,
        "matrix_tsv": str(matrix) if matrix is not None else None,
        "run_metadata_json": str(run_metadata) if run_metadata is not None else None,
        "evidence": evidence,
        "owner_build_route": (
            outputs.owner_build_route_receipt.as_dict()
            if outputs.owner_build_route_receipt is not None
            else None
        ),
    }


def _parked_gallery_summary() -> dict[str, Any]:
    return {
        "gallery_html": None,
        "gallery_error": _GALLERY_PARK_REASON,
    }


def _raw_paths_from_dir(
    raw_dir: Path | None,
    dll_dir: Path,
    sample_metadata: tuple[SampleMetadata, ...] | None,
) -> tuple[Path, ...]:
    if raw_dir is None or not raw_dir.is_dir():
        return ()
    analysis_set = read_analysis_set(raw_dir, dll_dir, sample_metadata)
    return tuple(sorted(analysis_set.raw_paths, key=lambda path: path.name.lower()))


def _batch_discovery_workers(
    raw_paths: tuple[Path, ...], profile: ExecutionProfile
) -> int:
    if not raw_paths:
        raise ValueError("batch Discovery requires at least one RAW path")
    return min(len(raw_paths), profile.parallel_workers)


def _read_batch_counts(index_path: Path) -> tuple[dict[str, int] | None, int]:
    if not index_path.exists():
        return None, 0

    totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
    sample_count = 0
    with index_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            sample_count += 1
            totals["HIGH"] += _int_csv_value(row, "high_count")
            totals["MEDIUM"] += _int_csv_value(row, "medium_count")
            totals["LOW"] += _int_csv_value(row, "low_count")
            totals["total"] += _int_csv_value(row, "candidate_count")
    return totals, sample_count


def _read_review_counts(review_path: Path) -> dict[str, int] | None:
    if not review_path.exists():
        return None

    totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "total": 0}
    with review_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            priority = (row.get("review_priority") or "").upper()
            if priority in {"HIGH", "MEDIUM", "LOW"}:
                totals[priority] += 1
            totals["total"] += 1
    return totals


def _int_csv_value(row: dict[str, str | None], key: str) -> int:
    value = row.get(key)
    if value is None or value == "":
        return 0
    return int(value)
