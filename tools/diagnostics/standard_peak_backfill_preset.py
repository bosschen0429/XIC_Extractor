"""Run the standard-peak backfill preset after alignment output is available."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from tools.diagnostics import family_ms1_alignment_experiment
from tools.diagnostics.standard_peak_backfill_machine_pipeline import (
    render_overlay_batch_summary_from_review_queue,
    run_machine_pipeline,
    write_overlay_batch_summary_slice,
)
from xic_extractor.diagnostics.backfill_reconciliation_gallery import (
    run_reconciliation_gallery,
)
from xic_extractor.diagnostics.diagnostic_io import read_tsv_required, text_value
from xic_extractor.diagnostics.retained_backfill_evidence_gate import (
    run_retained_backfill_evidence_gate,
)
from xic_extractor.diagnostics.standard_peak_backfill_chunk_consolidation import (
    StandardPeakChunkConsolidationOutputs,
    run_standard_peak_backfill_chunk_consolidation,
)
from xic_extractor.diagnostics.timing import TimingRecord, TimingRecorder

PRESET_NAME = "standard-peak-backfill"
DEFAULT_CHUNK_SIZE = 120
PUBLICATION_MODES = frozenset({"matrix-only", "review-gallery", "deep-audit"})


@dataclass(frozen=True)
class StandardPeakBackfillPresetOutputs:
    summary_json: Path
    retained_gate_tsv: Path | None = None
    review_queue_tsv: Path | None = None
    consolidated_output_dir: Path | None = None
    consolidation_summary_json: Path | None = None
    published_alignment_manifest_json: Path | None = None
    gallery_html: Path | None = None


@dataclass(frozen=True)
class _ChunkExecutionResult:
    chunk_index: int
    summary_path: Path
    timing_records: tuple[TimingRecord, ...] = ()


MachinePipelineRunner = Callable[..., Path]
ConsolidationRunner = Callable[..., StandardPeakChunkConsolidationOutputs]
ReconciliationGroupsRunner = Callable[..., Path]


def run_standard_peak_backfill_preset(
    *,
    alignment_dir: Path,
    raw_dir: Path,
    dll_dir: Path,
    output_dir: Path | None = None,
    source_run_id: str = "",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    reuse_existing: bool = False,
    write_gallery: bool = True,
    publication_mode: str | None = None,
    ppm: float = 20.0,
    max_highlight_rescued: int = 8,
    min_shape_r: float = 0.95,
    render_workers: int = 1,
    chunk_workers: int = 1,
    render_dpi: int = 140,
    evidence_cache_dir: Path | None = None,
    timing_recorder: TimingRecorder | None = None,
    machine_pipeline_runner: MachinePipelineRunner = run_machine_pipeline,
    consolidation_runner: ConsolidationRunner = (
        run_standard_peak_backfill_chunk_consolidation
    ),
    reconciliation_groups_runner: ReconciliationGroupsRunner | None = None,
) -> StandardPeakBackfillPresetOutputs:
    """Run retained-gate, chunked machine pipeline, and formal consolidation."""

    if chunk_size < 1:
        raise ValueError("standard-peak backfill preset chunk_size must be >= 1")
    if render_workers < 1:
        raise ValueError("standard-peak backfill preset render_workers must be >= 1")
    if chunk_workers < 1:
        raise ValueError("standard-peak backfill preset chunk_workers must be >= 1")
    recorder = timing_recorder or TimingRecorder.disabled("standard_peak")
    publication_mode = _resolve_publication_mode(
        publication_mode,
        legacy_write_gallery=write_gallery,
    )
    evidence_only = publication_mode in {"matrix-only", "review-gallery"}
    write_consolidation_gallery = publication_mode in {"review-gallery", "deep-audit"}

    alignment_dir = alignment_dir.resolve()
    output_dir = (
        output_dir or alignment_dir / "standard_peak_backfill_preset"
    ).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = _resolve_alignment_paths(alignment_dir)
    source_run_id = source_run_id or PRESET_NAME
    retained_dir = output_dir / "retained_backfill_evidence_gate"
    with recorder.stage(
        "standard_peak.retained_gate",
        metrics={"publication_mode": publication_mode},
    ):
        retained_outputs = run_retained_backfill_evidence_gate(
            alignment_review_tsv=paths["review"],
            alignment_cells_tsv=paths["cells"],
            alignment_matrix_tsv=paths["activation_matrix"],
            output_dir=retained_dir,
            backfill_seed_audit_tsv=paths["seed_audit"],
            source_run_id=source_run_id,
        )
    with recorder.stage("standard_peak.review_queue_read") as scope:
        review_queue_rows = read_tsv_required(
            retained_outputs.review_overlay_queue_tsv,
            ("feature_family_id",),
        )
        scope.metrics["review_queue_row_count"] = len(review_queue_rows)
    if not review_queue_rows:
        summary_json = output_dir / "standard_peak_backfill_preset_summary.json"
        summary = _summary(
            status="pass",
            source_run_id=source_run_id,
            alignment_dir=alignment_dir,
            output_dir=output_dir,
            retained_gate_tsv=retained_outputs.tsv,
            review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
            queue_row_count=0,
            chunk_size=chunk_size,
            chunk_summary_jsons=(),
            consolidation_outputs=None,
            status_reasons=("no_standard_peak_backfill_review_rows",),
            publication_mode=publication_mode,
            render_workers=render_workers,
            chunk_workers=chunk_workers,
            render_dpi=render_dpi,
        )
        with recorder.stage("standard_peak.summary_write"):
            summary_json.write_text(
                json.dumps(summary, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        return StandardPeakBackfillPresetOutputs(
            summary_json=summary_json,
            retained_gate_tsv=retained_outputs.tsv,
            review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
        )

    chunk_summary_jsons: list[Path] = []
    queue_row_count = len(review_queue_rows)
    chunks_dir = output_dir / "chunks"
    chunk_specs: list[tuple[int, int, Path, Path, bool]] = []
    for start_rank, limit in _chunk_plan(queue_row_count, chunk_size):
        end_rank = start_rank + limit - 1
        chunk_dir = chunks_dir / f"r{start_rank}_{end_rank}"
        existing_summary_path = (
            chunk_dir / "standard_peak_backfill_machine_pipeline_summary.json"
        )
        can_reuse_chunk = (
            reuse_existing
            and existing_summary_path.is_file()
            and _can_reuse_chunk_summary(
                existing_summary_path,
                publication_mode=publication_mode,
                start_rank=start_rank,
                limit=limit,
                source_run_id=f"{source_run_id}-r{start_rank}-{end_rank}",
                min_shape_r=min_shape_r,
                render_dpi=render_dpi,
            )
        )
        chunk_specs.append(
            (start_rank, limit, chunk_dir, existing_summary_path, can_reuse_chunk),
        )

    pending_chunk_specs = [spec for spec in chunk_specs if not spec[4]]
    source_family_by_family_sample: Mapping[str, Mapping[str, str]] = {}
    global_overlay_batch_summary_tsv: Path | None = None
    reconciliation_groups_tsv: Path | None = None
    if pending_chunk_specs:
        queue_family_ids = _review_queue_family_ids(review_queue_rows)
        with recorder.stage(
            "standard_peak.source_family_map_read",
            metrics={"family_count": len(queue_family_ids)},
        ) as scope:
            source_family_by_family_sample = (
                family_ms1_alignment_experiment.load_source_family_by_family_sample(
                    paths["cells"],
                    family_ids=queue_family_ids,
                )
                if queue_family_ids
                else {}
            )
            scope.metrics["mapped_family_count"] = len(source_family_by_family_sample)

        reconciliation_groups_runner = (
            reconciliation_groups_runner or _build_global_reconciliation_groups
        )
        with recorder.stage(
            "standard_peak.global_reconciliation_groups",
            metrics={"family_count": len(queue_family_ids)},
        ) as scope:
            reconciliation_groups_tsv = reconciliation_groups_runner(
                alignment_review_tsv=paths["review"],
                alignment_cells_tsv=paths["cells"],
                alignment_matrix_tsv=paths["activation_matrix"],
                backfill_seed_audit_tsv=paths["seed_audit"],
                retained_gate_tsv=retained_outputs.tsv,
                output_dir=output_dir / "global_reconciliation_group_index",
                source_run_id=source_run_id,
            )
            scope.metrics["reconciliation_groups_tsv"] = str(reconciliation_groups_tsv)

        with recorder.stage(
            "standard_peak.global_overlay_batch",
            metrics={
                "queue_row_count": queue_row_count,
                "publication_mode": publication_mode,
                "evidence_only": evidence_only,
            },
        ) as scope:
            global_overlay_source = render_overlay_batch_summary_from_review_queue(
                review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
                alignment_cells_tsv=paths["cells"],
                raw_dir=raw_dir,
                dll_dir=dll_dir,
                output_dir=output_dir / "global_family_ms1_overlay_batch",
                start_rank=1,
                limit=queue_row_count,
                reuse_existing=reuse_existing,
                ppm=ppm,
                max_highlight_rescued=max_highlight_rescued,
                write_overlay_pdf=False,
                evidence_only=evidence_only,
                workers=render_workers,
                dpi=render_dpi,
                evidence_cache_dir=evidence_cache_dir,
            )
            global_overlay_batch_summary_tsv = global_overlay_source.summary_tsv
            scope.metrics["overlay_batch_summary_tsv"] = str(
                global_overlay_batch_summary_tsv,
            )
            if global_overlay_source.metrics:
                scope.metrics.update(global_overlay_source.metrics)

    chunk_summary_by_index: dict[int, Path] = {}
    chunk_timing_by_index: dict[int, tuple[TimingRecord, ...]] = {}
    pending_indexed_specs: list[tuple[int, tuple[int, int, Path, Path, bool]]] = []
    for chunk_index, spec in enumerate(chunk_specs):
        existing_summary_path = spec[3]
        can_reuse_chunk = spec[4]
        if can_reuse_chunk:
            chunk_summary_by_index[chunk_index] = existing_summary_path
        else:
            pending_indexed_specs.append((chunk_index, spec))

    active_chunk_workers = min(chunk_workers, len(pending_indexed_specs)) or 1
    recorder.record(
        "standard_peak.chunk_dispatch",
        elapsed_sec=0.0,
        metrics={
            "chunk_workers": active_chunk_workers,
            "pending_chunk_count": len(pending_indexed_specs),
            "reused_chunk_count": len(chunk_specs) - len(pending_indexed_specs),
        },
    )
    if active_chunk_workers == 1:
        for chunk_index, spec in pending_indexed_specs:
            result = _run_chunk(
                chunk_index=chunk_index,
                spec=spec,
                recorder=recorder,
                reconciliation_groups_tsv=reconciliation_groups_tsv,
                global_overlay_batch_summary_tsv=global_overlay_batch_summary_tsv,
                publication_mode=publication_mode,
                source_run_id=source_run_id,
                machine_pipeline_runner=machine_pipeline_runner,
                paths=paths,
                retained_gate_tsv=retained_outputs.tsv,
                reuse_existing=reuse_existing,
                min_shape_r=min_shape_r,
                render_workers=render_workers,
                render_dpi=render_dpi,
                source_family_by_family_sample=source_family_by_family_sample,
            )
            chunk_summary_by_index[chunk_index] = result.summary_path
    elif pending_indexed_specs:
        with ThreadPoolExecutor(max_workers=active_chunk_workers) as executor:
            future_to_index = {
                executor.submit(
                    _run_chunk_with_private_timing,
                    chunk_index=chunk_index,
                    spec=spec,
                    parent_recorder=recorder,
                    reconciliation_groups_tsv=reconciliation_groups_tsv,
                    global_overlay_batch_summary_tsv=global_overlay_batch_summary_tsv,
                    publication_mode=publication_mode,
                    source_run_id=source_run_id,
                    machine_pipeline_runner=machine_pipeline_runner,
                    paths=paths,
                    retained_gate_tsv=retained_outputs.tsv,
                    reuse_existing=reuse_existing,
                    min_shape_r=min_shape_r,
                    render_workers=render_workers,
                    render_dpi=render_dpi,
                    source_family_by_family_sample=source_family_by_family_sample,
                ): chunk_index
                for chunk_index, spec in pending_indexed_specs
            }
            for future in as_completed(future_to_index):
                result = future.result()
                chunk_summary_by_index[result.chunk_index] = result.summary_path
                chunk_timing_by_index[result.chunk_index] = result.timing_records
        for chunk_index, _spec in pending_indexed_specs:
            for record in chunk_timing_by_index.get(chunk_index, ()):
                recorder.record(
                    record.stage,
                    elapsed_sec=record.elapsed_sec,
                    sample_stem=record.sample_stem,
                    metrics=record.metrics,
                )

    chunk_summary_jsons = [
        chunk_summary_by_index[index] for index in range(len(chunk_specs))
    ]

    consolidated_dir = output_dir / "consolidated"
    gallery_dir = (
        consolidated_dir / "standard_peak_productization" / "reconciliation_gallery"
        if write_consolidation_gallery
        else None
    )
    with recorder.stage(
        "standard_peak.consolidation",
        metrics={
            "chunk_count": len(chunk_summary_jsons),
            "write_gallery": write_consolidation_gallery,
            "publication_mode": publication_mode,
        },
    ) as scope:
        consolidation_outputs = consolidation_runner(
            machine_pipeline_summary_jsons=tuple(chunk_summary_jsons),
            alignment_matrix_tsv=paths["activation_matrix"],
            alignment_matrix_identity_tsv=paths["activation_identity"],
            alignment_review_tsv=paths["review"],
            output_dir=consolidated_dir,
            source_run_id=source_run_id,
            review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
            write_gallery=write_consolidation_gallery,
            alignment_cells_tsv=paths["cells"],
            backfill_seed_audit_tsv=paths["seed_audit"],
            retained_backfill_gate_tsv=retained_outputs.tsv,
            gallery_output_dir=gallery_dir,
            emit_formal_product_output=True,
            publish_to_source_alignment_output=False,
        )
        scope.metrics["status"] = consolidation_outputs.status
    summary_json = output_dir / "standard_peak_backfill_preset_summary.json"
    summary = _summary(
        status=consolidation_outputs.status,
        source_run_id=source_run_id,
        alignment_dir=alignment_dir,
        output_dir=output_dir,
        retained_gate_tsv=retained_outputs.tsv,
        review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
        queue_row_count=queue_row_count,
        chunk_size=chunk_size,
        chunk_summary_jsons=tuple(chunk_summary_jsons),
        consolidation_outputs=consolidation_outputs,
        status_reasons=(),
        publication_mode=publication_mode,
        render_workers=render_workers,
        chunk_workers=chunk_workers,
        render_dpi=render_dpi,
    )
    with recorder.stage("standard_peak.summary_write"):
        summary_json.write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return StandardPeakBackfillPresetOutputs(
        summary_json=summary_json,
        retained_gate_tsv=retained_outputs.tsv,
        review_queue_tsv=retained_outputs.review_overlay_queue_tsv,
        consolidated_output_dir=consolidated_dir,
        consolidation_summary_json=consolidation_outputs.summary_json,
        published_alignment_manifest_json=(
            consolidation_outputs.published_alignment_manifest_json
        ),
        gallery_html=consolidation_outputs.productization.reconciliation_gallery_html,
    )


def _resolve_alignment_paths(alignment_dir: Path) -> dict[str, Path]:
    published_matrix = alignment_dir / "alignment_matrix.tsv"
    published_identity = alignment_dir / "alignment_matrix_identity.tsv"
    activation_matrix = _pre_standard_path_or_default(published_matrix)
    activation_identity = _pre_standard_path_or_default(published_identity)
    cells = _cell_evidence_path(alignment_dir)
    paths = {
        "review": alignment_dir / "alignment_review.tsv",
        "cells": cells,
        "seed_audit": alignment_dir / "alignment_owner_backfill_seed_audit.tsv",
        "activation_matrix": activation_matrix,
        "activation_identity": activation_identity,
        "published_matrix": published_matrix,
        "published_identity": published_identity,
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        details = ", ".join(f"{name}={paths[name]}" for name in missing)
        raise ValueError(
            "standard-peak backfill preset requires alignment artifacts: "
            f"{details}",
        )
    return paths


def _pre_standard_path_or_default(path: Path) -> Path:
    backup = _backup_path(path, "pre_standard_peak_backfill")
    return backup if backup.is_file() else path


def _backup_path(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}.{suffix}{path.suffix}")


def _cell_evidence_path(alignment_dir: Path) -> Path:
    light = alignment_dir / "alignment_backfill_cell_evidence.tsv"
    if light.is_file():
        return light
    return alignment_dir / "alignment_cells.tsv"


def _chunk_plan(queue_row_count: int, chunk_size: int) -> tuple[tuple[int, int], ...]:
    plan: list[tuple[int, int]] = []
    start_rank = 1
    while start_rank <= queue_row_count:
        limit = min(chunk_size, queue_row_count - start_rank + 1)
        plan.append((start_rank, limit))
        start_rank += limit
    return tuple(plan)


def _review_queue_family_ids(
    rows: Sequence[Mapping[str, str]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                family
                for row in rows
                if (family := text_value(row.get("feature_family_id")))
            },
        ),
    )


def _build_global_reconciliation_groups(
    *,
    alignment_review_tsv: Path,
    alignment_cells_tsv: Path,
    alignment_matrix_tsv: Path,
    backfill_seed_audit_tsv: Path,
    retained_gate_tsv: Path,
    output_dir: Path,
    source_run_id: str,
) -> Path:
    outputs = run_reconciliation_gallery(
        alignment_review_tsv=alignment_review_tsv,
        alignment_cells_tsv=alignment_cells_tsv,
        output_dir=output_dir,
        alignment_matrix_tsv=alignment_matrix_tsv,
        backfill_seed_audit_tsv=backfill_seed_audit_tsv,
        retained_backfill_gate_tsv=retained_gate_tsv,
        source_run_id=source_run_id,
    )
    return outputs.groups_tsv


def _run_chunk_with_private_timing(
    *,
    chunk_index: int,
    spec: tuple[int, int, Path, Path, bool],
    parent_recorder: TimingRecorder,
    reconciliation_groups_tsv: Path | None,
    global_overlay_batch_summary_tsv: Path | None,
    publication_mode: str,
    source_run_id: str,
    machine_pipeline_runner: MachinePipelineRunner,
    paths: Mapping[str, Path],
    retained_gate_tsv: Path,
    reuse_existing: bool,
    min_shape_r: float,
    render_workers: int,
    render_dpi: int,
    source_family_by_family_sample: Mapping[str, Mapping[str, str]],
) -> _ChunkExecutionResult:
    chunk_recorder = TimingRecorder(
        parent_recorder.pipeline,
        run_id=parent_recorder.run_id,
        enabled=parent_recorder.enabled,
    )
    result = _run_chunk(
        chunk_index=chunk_index,
        spec=spec,
        recorder=chunk_recorder,
        reconciliation_groups_tsv=reconciliation_groups_tsv,
        global_overlay_batch_summary_tsv=global_overlay_batch_summary_tsv,
        publication_mode=publication_mode,
        source_run_id=source_run_id,
        machine_pipeline_runner=machine_pipeline_runner,
        paths=paths,
        retained_gate_tsv=retained_gate_tsv,
        reuse_existing=reuse_existing,
        min_shape_r=min_shape_r,
        render_workers=render_workers,
        render_dpi=render_dpi,
        source_family_by_family_sample=source_family_by_family_sample,
    )
    return _ChunkExecutionResult(
        chunk_index=chunk_index,
        summary_path=result.summary_path,
        timing_records=chunk_recorder.records,
    )


def _run_chunk(
    *,
    chunk_index: int,
    spec: tuple[int, int, Path, Path, bool],
    recorder: TimingRecorder,
    reconciliation_groups_tsv: Path | None,
    global_overlay_batch_summary_tsv: Path | None,
    publication_mode: str,
    source_run_id: str,
    machine_pipeline_runner: MachinePipelineRunner,
    paths: Mapping[str, Path],
    retained_gate_tsv: Path,
    reuse_existing: bool,
    min_shape_r: float,
    render_workers: int,
    render_dpi: int,
    source_family_by_family_sample: Mapping[str, Mapping[str, str]],
) -> _ChunkExecutionResult:
    start_rank, limit, chunk_dir, _existing_summary_path, _can_reuse_chunk = spec
    end_rank = start_rank + limit - 1
    if reconciliation_groups_tsv is None:
        raise ValueError("reconciliation groups were not built for pending chunk")
    if global_overlay_batch_summary_tsv is None:
        raise ValueError("global overlay summary was not built for pending chunk")
    with recorder.stage(
        "standard_peak.chunk",
        metrics={
            "start_rank": start_rank,
            "end_rank": end_rank,
            "limit": limit,
            "publication_mode": publication_mode,
        },
    ) as scope:
        chunk_overlay_batch_summary_tsv = write_overlay_batch_summary_slice(
            source_overlay_batch_summary_tsv=global_overlay_batch_summary_tsv,
            output_dir=chunk_dir / "family_ms1_overlay_batch",
            start_rank=start_rank,
            limit=limit,
        )
        summary_path = machine_pipeline_runner(
            overlay_batch_summary_tsv=chunk_overlay_batch_summary_tsv,
            alignment_review_tsv=paths["review"],
            alignment_cells_tsv=paths["cells"],
            alignment_matrix_tsv=paths["activation_matrix"],
            alignment_matrix_identity_tsv=paths["activation_identity"],
            retained_gate_tsv=retained_gate_tsv,
            reconciliation_groups_tsv=reconciliation_groups_tsv,
            output_dir=chunk_dir,
            source_run_id=f"{source_run_id}-r{start_rank}-{end_rank}",
            backfill_seed_audit_tsv=paths["seed_audit"],
            start_rank=start_rank,
            limit=limit,
            reuse_existing=reuse_existing,
            min_shape_r=min_shape_r,
            publication_mode=publication_mode,
            defer_projection=True,
            render_workers=render_workers,
            render_dpi=render_dpi,
            timing_recorder=recorder,
            source_family_by_family_sample=source_family_by_family_sample,
        )
        scope.metrics["summary_json"] = str(summary_path)
    return _ChunkExecutionResult(chunk_index=chunk_index, summary_path=summary_path)


def _summary(
    *,
    status: str,
    source_run_id: str,
    alignment_dir: Path,
    output_dir: Path,
    retained_gate_tsv: Path,
    review_queue_tsv: Path,
    queue_row_count: int,
    chunk_size: int,
    chunk_summary_jsons: Sequence[Path],
    consolidation_outputs: StandardPeakChunkConsolidationOutputs | None,
    status_reasons: Sequence[str],
    publication_mode: str,
    render_workers: int,
    chunk_workers: int,
    render_dpi: int,
) -> dict[str, object]:
    consolidation_summary = (
        _load_json_mapping(consolidation_outputs.summary_json)
        if consolidation_outputs is not None
        else {}
    )
    return {
        "schema_version": "standard_peak_backfill_preset_v0",
        "preset_name": PRESET_NAME,
        "publication_mode": publication_mode,
        "render_workers": str(render_workers),
        "chunk_workers": str(chunk_workers),
        "render_dpi": str(render_dpi),
        "status": status,
        "source_run_id": source_run_id,
        "alignment_dir": str(alignment_dir),
        "output_dir": str(output_dir),
        "retained_gate_tsv": str(retained_gate_tsv),
        "review_queue_tsv": str(review_queue_tsv),
        "review_queue_row_count": str(queue_row_count),
        "chunk_size": str(chunk_size),
        "chunk_count": str(len(chunk_summary_jsons)),
        "chunk_summary_jsons": [str(path) for path in chunk_summary_jsons],
        "consolidation_summary_json": (
            str(consolidation_outputs.summary_json)
            if consolidation_outputs is not None
            else ""
        ),
        "formal_product_output_dir": (
            str(consolidation_outputs.formal_product_output_dir)
            if consolidation_outputs is not None
            and consolidation_outputs.formal_product_output_dir is not None
            else ""
        ),
        "published_alignment_manifest_json": (
            str(consolidation_outputs.published_alignment_manifest_json)
            if consolidation_outputs is not None
            and consolidation_outputs.published_alignment_manifest_json is not None
            else ""
        ),
        "gallery_html": (
            str(consolidation_outputs.productization.reconciliation_gallery_html)
            if consolidation_outputs is not None
            and consolidation_outputs.productization.reconciliation_gallery_html
            is not None
            else ""
        ),
        "matrix_cells_written": text_value(
            consolidation_summary.get("matrix_cells_written"),
        ),
        "coverage_status": text_value(consolidation_summary.get("coverage_status")),
        "status_reasons": ";".join(status_reasons),
    }


def _load_json_mapping(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_publication_mode(
    publication_mode: str | None,
    *,
    legacy_write_gallery: bool,
) -> str:
    if publication_mode is None:
        return "deep-audit" if legacy_write_gallery else "matrix-only"
    if publication_mode not in PUBLICATION_MODES:
        modes = ", ".join(sorted(PUBLICATION_MODES))
        raise ValueError(f"publication_mode must be one of: {modes}")
    return publication_mode


def _can_reuse_chunk_summary(
    path: Path,
    *,
    publication_mode: str,
    start_rank: int,
    limit: int,
    source_run_id: str,
    min_shape_r: float,
    render_dpi: int,
) -> bool:
    summary = _load_json_mapping(path)
    if text_value(summary.get("status")) != "pass":
        return False
    existing_mode = text_value(summary.get("publication_mode"))
    if existing_mode != publication_mode:
        return False
    if _int_value(summary.get("start_rank")) != start_rank:
        return False
    if _int_value(summary.get("effective_overlay_limit")) != limit:
        return False
    if text_value(summary.get("source_run_id")) != source_run_id:
        return False
    existing_render_dpi = _int_value(summary.get("render_dpi"))
    if existing_render_dpi is None and publication_mode != "matrix-only":
        return False
    if existing_render_dpi is not None and existing_render_dpi != render_dpi:
        return False
    existing_min_shape_r = _float_value(summary.get("min_shape_r"))
    return (
        existing_min_shape_r is not None
        and abs(existing_min_shape_r - min_shape_r) <= 1e-12
    )


def _int_value(value: object) -> int | None:
    try:
        return int(text_value(value))
    except ValueError:
        return None


def _float_value(value: object) -> float | None:
    try:
        return float(text_value(value))
    except ValueError:
        return None
