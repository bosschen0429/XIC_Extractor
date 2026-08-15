from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from PyQt6.QtCore import QThread, pyqtSignal

from gui.workers.backfill_gallery import (
    BackfillGalleryInputs,
    build_backfill_review_gallery,
)
from xic_extractor.alignment.pipeline import run_alignment
from xic_extractor.alignment.pipeline_outputs import AlignmentRunOutputs
from xic_extractor.alignment.process_backend import AlignmentProcessExecutionError
from xic_extractor.config import ExtractionConfig
from xic_extractor.discovery.models import DiscoverySettings
from xic_extractor.discovery.physical_fact_record import (
    DiscoveryCandidatePhysicalBinding,
)
from xic_extractor.discovery.pipeline import run_discovery, run_discovery_batch
from xic_extractor.discovery.processing_run_spec_adapter import (
    compile_discovery_processing_run_spec_from_config as _compile_processing_run_spec,
)
from xic_extractor.presets import (
    BoundCidNlRecipe,
    PresetError,
    bind_cid_nl_recipe,
    load_preset,
)
from xic_extractor.raw_reader import RawReaderError
from xic_extractor.settings_schema import CANONICAL_SETTINGS_DEFAULTS

DiscoveryMode = Literal["full", "discovery_only", "align_only"]

# Alignment flags needed for the backfill review gallery. ``machine`` is a
# superset of ``production`` (it adds alignment_review.tsv); the emit flags add
# cells + seed audit the gallery reads. ``audit_evidence_mode="none"`` keeps the
# cheap path: the gallery's required cell columns are all core attributes, so we
# skip the heavy per-cell region audit that "auto" would switch on once cells
# are emitted.
_GALLERY_ALIGNMENT_KWARGS: dict[str, Any] = {
    "output_level": "machine",
    "emit_alignment_cells": True,
    "emit_alignment_backfill_seed_audit": True,
    "audit_evidence_mode": "none",
}

_ALIGNMENT_OUTPUT_ATTRS = (
    "workbook",
    "review_html",
    "gallery_html",
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


class DiscoveryWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, request: DiscoveryRequest) -> None:
        super().__init__()
        self._request = request
        self._recipe_binding: tuple[BoundCidNlRecipe, str] | None = None

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
            if summary is None or self.isInterruptionRequested():
                self.cancelled.emit()
                return
            self.finished.emit(summary)
        except PresetError as exc:
            self.error.emit(f"Preset 錯誤：{exc}")
        except RawReaderError as exc:
            self.error.emit(f"Raw file 讀取失敗：{exc}")
        except (AlignmentProcessExecutionError, ValueError, OSError, KeyError) as exc:
            self.error.emit(str(exc))

    def _run_full(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        raw_dir = request.raw_dir
        if raw_dir is None:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        raw_paths = _raw_paths_from_dir(raw_dir)
        if not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")

        self.progress.emit(0, 3, "Discovery...")
        settings = self._build_settings(request)
        peak_config = self._discovery_peak_config(request, settings)
        run_spec = _compile_processing_run_spec(raw_paths, settings, peak_config)
        batch_outputs = run_discovery_batch(
            raw_paths,
            output_dir=request.output_dir,
            settings=settings,
            peak_config=peak_config,
            run_spec=run_spec,
        )
        index_path = _batch_index_path(batch_outputs, request.output_dir)
        if self.isInterruptionRequested():
            return None

        self.progress.emit(1, 3, "Alignment...")
        outputs, standard_peak_outputs = self._run_alignment(
            request,
            index_path=index_path,
            raw_dir=raw_dir,
            physical_bindings_by_sample={
                raw_path.stem: sample_outputs.physical_bindings
                for raw_path, sample_outputs in zip(
                    raw_paths,
                    batch_outputs.per_sample,
                    strict=True,
                )
            },
        )
        if self.isInterruptionRequested():
            return None

        self.progress.emit(2, 3, "Backfill gallery...")
        gallery = self._build_gallery(request, outputs, raw_dir=raw_dir)
        self.progress.emit(3, 3, "完成")

        summary = self._discovery_summary(request, index_path=index_path)
        summary.update(_alignment_summary(outputs, standard_peak_outputs))
        summary.update(gallery)
        return summary

    def _run_discovery_only(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        if request.raw_file is not None:
            self.progress.emit(0, 1, "Discovery...")
            settings = self._build_settings(request)
            peak_config = self._discovery_peak_config(request, settings)
            run_spec = _compile_processing_run_spec(
                (request.raw_file,), settings, peak_config
            )
            outputs = run_discovery(
                request.raw_file,
                output_dir=request.output_dir,
                settings=settings,
                peak_config=peak_config,
                run_spec=run_spec,
            )
            if self.isInterruptionRequested():
                return None
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

        raw_paths = _raw_paths_from_dir(request.raw_dir)
        if not raw_paths:
            raise ValueError(f"RAW 資料夾中找不到 .raw 檔案：{request.raw_dir}")
        self.progress.emit(0, 1, "Discovery...")
        settings = self._build_settings(request)
        peak_config = self._discovery_peak_config(request, settings)
        run_spec = _compile_processing_run_spec(raw_paths, settings, peak_config)
        batch_outputs = run_discovery_batch(
            raw_paths,
            output_dir=request.output_dir,
            settings=settings,
            peak_config=peak_config,
            run_spec=run_spec,
        )
        if self.isInterruptionRequested():
            return None
        self.progress.emit(1, 1, "完成")
        return self._discovery_summary(
            request,
            index_path=_batch_index_path(batch_outputs, request.output_dir),
        )

    def _run_align_only(self, request: DiscoveryRequest) -> dict[str, Any] | None:
        index_path = request.discovery_batch_index
        if index_path is None:
            raise ValueError("align-only 需要既有的 discovery_batch_index.csv")
        if request.raw_dir is None:
            raise ValueError("align-only 需要 raw_dir 來定位 RAW 檔案")
        candidate_counts, sample_count = _read_batch_counts(index_path)

        self.progress.emit(0, 2, "Alignment...")
        outputs, standard_peak_outputs = self._run_alignment(
            request,
            index_path=index_path,
            raw_dir=request.raw_dir,
        )
        if self.isInterruptionRequested():
            return None

        self.progress.emit(1, 2, "Backfill gallery...")
        gallery = self._build_gallery(request, outputs, raw_dir=request.raw_dir)
        self.progress.emit(2, 2, "完成")

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
        summary.update(_alignment_summary(outputs, standard_peak_outputs))
        summary.update(gallery)
        return summary

    def _run_alignment(
        self,
        request: DiscoveryRequest,
        *,
        index_path: Path,
        raw_dir: Path,
        physical_bindings_by_sample: (
            dict[str, tuple[DiscoveryCandidatePhysicalBinding, ...]] | None
        ) = None,
    ) -> tuple[AlignmentRunOutputs, Any | None]:
        recipe, source_run_id = self._alignment_runtime(request)
        outputs = run_alignment(
            discovery_batch_index=index_path,
            raw_dir=raw_dir,
            dll_dir=request.dll_dir,
            output_dir=request.output_dir,
            alignment_config=recipe.alignment_config,
            peak_config=self._alignment_peak_config(request),
            owner_build_xic_backend=recipe.owner_build_xic_backend,
            discovery_physical_bindings_by_sample=physical_bindings_by_sample,
            **_GALLERY_ALIGNMENT_KWARGS,
        )
        if self.isInterruptionRequested():
            return outputs, None
        standard_peak_outputs = self._run_standard_peak_backfill_if_enabled(
            request,
            raw_dir=raw_dir,
            recipe=recipe,
            source_run_id=source_run_id,
        )
        return outputs, standard_peak_outputs

    def _alignment_runtime(
        self,
        request: DiscoveryRequest,
    ) -> tuple[BoundCidNlRecipe, str]:
        binding = self._recipe_binding
        if binding is None:
            preset = load_preset(request.preset)
            recipe = bind_cid_nl_recipe(preset, request.mode)
            _validate_gui_alignment_recipe(recipe)
            binding = (recipe, str(preset.source))
            self._recipe_binding = binding
        recipe, preset_source = binding
        return recipe, _standard_peak_source_run_id(preset_source)

    def _run_standard_peak_backfill_if_enabled(
        self,
        request: DiscoveryRequest,
        *,
        raw_dir: Path,
        recipe: BoundCidNlRecipe,
        source_run_id: str,
    ) -> Any | None:
        if not recipe.standard_peak_backfill:
            return None
        return run_standard_peak_backfill_preset(
            alignment_dir=request.output_dir,
            raw_dir=raw_dir,
            dll_dir=request.dll_dir,
            source_run_id=source_run_id,
            chunk_size=recipe.standard_peak_backfill_chunk_size,
            reuse_existing=recipe.standard_peak_backfill_reuse_existing,
            write_gallery=recipe.standard_peak_backfill_write_gallery,
            publication_mode=recipe.standard_peak_backfill_publication_mode,
            min_shape_r=recipe.standard_peak_backfill_min_shape_r,
        )

    def _build_gallery(
        self,
        request: DiscoveryRequest,
        outputs: AlignmentRunOutputs,
        *,
        raw_dir: Path | None,
    ) -> dict[str, Any]:
        """Build the backfill review gallery; never fatal to the run.

        The matrix/review TSVs are the primary deliverable, so a gallery failure
        degrades to a disabled button plus a recorded reason rather than failing
        the whole run (and losing the already-written matrix from the UI).
        """
        review = outputs.review_tsv
        cells = outputs.cells_tsv
        matrix = outputs.matrix_tsv
        if raw_dir is None or review is None or cells is None or matrix is None:
            missing = [
                name
                for name, value in (
                    ("raw_dir", raw_dir),
                    ("alignment_review.tsv", review),
                    ("alignment_cells.tsv", cells),
                    ("alignment_matrix.tsv", matrix),
                )
                if value is None
            ]
            return {
                "gallery_html": None,
                "gallery_error": f"缺少 gallery 輸入：{', '.join(missing)}",
            }
        try:
            gallery_html = build_backfill_review_gallery(
                BackfillGalleryInputs(
                    alignment_review_tsv=review,
                    alignment_cells_tsv=cells,
                    alignment_matrix_tsv=matrix,
                    raw_dir=raw_dir,
                    dll_dir=request.dll_dir,
                    backfill_seed_audit_tsv=outputs.backfill_seed_audit_tsv,
                ),
                output_dir=request.output_dir / "backfill_gallery",
                dpi=140,
                workers=_render_workers(),
            )
        except (OSError, ValueError, KeyError, RawReaderError) as exc:
            return {"gallery_html": None, "gallery_error": str(exc)}
        return {"gallery_html": str(gallery_html), "gallery_error": None}

    def _build_settings(self, request: DiscoveryRequest) -> DiscoverySettings:
        preset = load_preset(request.preset)
        recipe = bind_cid_nl_recipe(
            preset,
            request.mode,
            recipe_overrides=request.tuning_overrides,
        )
        if request.mode != "discovery_only":
            _validate_gui_alignment_recipe(recipe)
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

    def _alignment_peak_config(self, request: DiscoveryRequest) -> ExtractionConfig:
        defaults = CANONICAL_SETTINGS_DEFAULTS
        return self._peak_config(
            request.raw_dir or request.output_dir,
            request,
            nl_min_intensity_ratio=float(defaults["nl_min_intensity_ratio"]),
            resolver_mode="local_minimum",
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
            smooth_window=int(defaults["smooth_window"]),
            smooth_polyorder=int(defaults["smooth_polyorder"]),
            ms1_morphology_smoothing_window_points=int(
                defaults["ms1_morphology_smoothing_window_points"]
            ),
            peak_rel_height=float(defaults["peak_rel_height"]),
            peak_min_prominence_ratio=float(defaults["peak_min_prominence_ratio"]),
            ms2_precursor_tol_da=float(defaults["ms2_precursor_tol_da"]),
            nl_min_intensity_ratio=nl_min_intensity_ratio,
            resolver_mode=resolver_mode,
            resolver_chrom_threshold=float(defaults["resolver_chrom_threshold"]),
            resolver_min_search_range_min=float(
                defaults["resolver_min_search_range_min"]
            ),
            resolver_min_relative_height=float(
                defaults["resolver_min_relative_height"]
            ),
            resolver_min_absolute_height=float(
                defaults["resolver_min_absolute_height"]
            ),
            resolver_min_ratio_top_edge=float(defaults["resolver_min_ratio_top_edge"]),
            resolver_peak_duration_min=float(defaults["resolver_peak_duration_min"]),
            resolver_peak_duration_max=float(defaults["resolver_peak_duration_max"]),
            resolver_min_scans=int(defaults["resolver_min_scans"]),
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


def _render_workers() -> int:
    """Parallel workers for overlay render: leave 2 cores for the UI / OS."""
    return max(1, (os.cpu_count() or 2) - 2)


def _batch_index_path(batch_outputs: Any, output_dir: Path) -> Path:
    return Path(
        getattr(
            batch_outputs,
            "batch_index_csv",
            output_dir / "discovery_batch_index.csv",
        )
    )


def run_standard_peak_backfill_preset(**kwargs: Any) -> Any:
    from tools.diagnostics.standard_peak_backfill_preset import (
        run_standard_peak_backfill_preset as runner,
    )

    return runner(**kwargs)


def _standard_peak_source_run_id(preset_source: str) -> str:
    safe_source = preset_source.replace("\\", "/")
    return f"alignment-preset:{safe_source}:standard-peak-backfill"


def _validate_gui_alignment_recipe(recipe: BoundCidNlRecipe) -> None:
    if recipe.backfill_expansion_productization != "off":
        raise ValueError(
            "GUI untargeted presets do not support "
            "backfill_expansion_productization yet; use scripts.run_alignment "
            "for this preset",
        )


def _alignment_summary(
    outputs: Any,
    standard_peak_outputs: Any | None = None,
) -> dict[str, Any]:
    alignment_outputs: dict[str, str] = {}
    for attr in _ALIGNMENT_OUTPUT_ATTRS:
        value = getattr(outputs, attr, None)
        if value is not None:
            alignment_outputs[attr] = str(value)
    alignment_outputs.update(_standard_peak_output_paths(standard_peak_outputs))

    matrix = getattr(outputs, "matrix_tsv", None)
    gallery = getattr(outputs, "gallery_html", None)
    run_metadata = getattr(outputs, "run_metadata_json", None)
    return {
        "alignment_outputs": alignment_outputs,
        "matrix_tsv": str(matrix) if matrix is not None else None,
        "gallery_html": str(gallery) if gallery is not None else None,
        "run_metadata_json": str(run_metadata) if run_metadata is not None else None,
    }


def _standard_peak_output_paths(outputs: Any | None) -> dict[str, str]:
    if outputs is None:
        return {}
    paths: dict[str, str] = {}
    for attr in (
        "summary_json",
        "retained_gate_tsv",
        "review_queue_tsv",
        "consolidated_output_dir",
        "consolidation_summary_json",
        "published_alignment_manifest_json",
        "gallery_html",
    ):
        value = getattr(outputs, attr, None)
        if value is not None:
            paths[f"standard_peak_{attr}"] = str(value)
    return paths


def _raw_paths_from_dir(raw_dir: Path | None) -> tuple[Path, ...]:
    if raw_dir is None or not raw_dir.is_dir():
        return ()
    return tuple(
        sorted(
            (
                path
                for path in raw_dir.iterdir()
                if path.is_file() and path.suffix.lower() == ".raw"
            ),
            key=lambda path: path.name.lower(),
        )
    )


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
