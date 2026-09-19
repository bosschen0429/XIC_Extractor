"""Batch render peak-group MS1 alignment experiments from legacy family overlays."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.diagnostics import family_ms1_alignment_experiment
from xic_extractor.diagnostics.diagnostic_io import (
    read_tsv_required,
    text_value,
    write_tsv,
)
from xic_extractor.diagnostics.timing import TimingRecorder

SUMMARY_COLUMNS = (
    "rank",
    "feature_family_id",
    "seed_group_id",
    "overlay_status",
    "alignment_status",
    "output_prefix",
    "trace_data_json",
    "source_best_shift_summary_tsv",
    "source_best_shift_png",
    "failure_reason",
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        rows, summary = run_alignment_experiment_batch(
            overlay_batch_summary_tsv=args.overlay_batch_summary_tsv,
            cell_evidence_tsv=args.cell_evidence_tsv,
            output_dir=args.output_dir,
            start_rank=args.start_rank,
            limit=args.limit,
            reuse_existing=args.reuse_existing,
            render_images=not args.no_images,
            write_auxiliary_summaries=not args.best_shift_only,
            write_incremental=True,
            workers=args.workers,
            dpi=args.dpi,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic CLI reports failure.
        print(f"error: {exc}")
        return 2
    output_tsv = args.output_dir / "family_ms1_alignment_experiment_batch_summary.tsv"
    output_json = args.output_dir / "family_ms1_alignment_experiment_batch_summary.json"
    write_tsv(output_tsv, rows, SUMMARY_COLUMNS)
    output_json.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"peak-group MS1 alignment experiment batch TSV: {output_tsv}")
    print(f"peak-group MS1 alignment experiment batch JSON: {output_json}")
    return 0


def run_alignment_experiment_batch(
    *,
    overlay_batch_summary_tsv: Path,
    cell_evidence_tsv: Path,
    output_dir: Path,
    start_rank: int = 1,
    limit: int | None = None,
    reuse_existing: bool = False,
    render_images: bool = True,
    write_auxiliary_summaries: bool = True,
    write_incremental: bool = False,
    timing_recorder: TimingRecorder | None = None,
    source_family_by_family_sample: Mapping[str, Mapping[str, str]] | None = None,
    workers: int = 1,
    dpi: int = 140,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    if start_rank < 1:
        raise ValueError("--start-rank must be >= 1")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be >= 1 when supplied")
    if workers < 1:
        raise ValueError("--workers must be >= 1")
    recorder = timing_recorder or TimingRecorder.disabled("standard_peak")
    with recorder.stage("standard_peak.shift_aware_batch.read_overlay"):
        overlay_rows = list(
            read_tsv_required(
                overlay_batch_summary_tsv,
                ("rank", "feature_family_id", "output_prefix", "status"),
            ),
        )
    with recorder.stage(
        "standard_peak.shift_aware_batch.select_rows",
        metrics={"start_rank": start_rank, "limit": limit or ""},
    ) as scope:
        selected_rows = _select_rows(overlay_rows, start_rank=start_rank, limit=limit)
        scope.metrics["selected_row_count"] = len(selected_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_family_ids = tuple(
        sorted(
            {
                text_value(row.get("feature_family_id"))
                for row in selected_rows
                if text_value(row.get("feature_family_id"))
            },
        ),
    )
    with recorder.stage(
        "standard_peak.shift_aware_batch.read_cell_evidence",
        metrics={"family_count": len(selected_family_ids)},
    ) as scope:
        if source_family_by_family_sample is not None:
            source_family_by_family = {
                family: dict(source_family_by_family_sample.get(family, {}))
                for family in selected_family_ids
            }
            source_family_map_source = "preloaded"
        else:
            source_family_by_family = (
                family_ms1_alignment_experiment.load_source_family_by_family_sample(
                    cell_evidence_tsv,
                    family_ids=selected_family_ids,
                )
                if selected_family_ids
                else {}
            )
            source_family_map_source = "cell_evidence_tsv"
        scope.metrics["source_family_map_source"] = source_family_map_source
        scope.metrics["mapped_family_count"] = len(source_family_by_family)
    rows: list[dict[str, str]] = []
    if workers > 1 and selected_rows:
        payloads = [
            (
                dict(row),
                {
                    "cell_evidence_tsv": cell_evidence_tsv,
                    "output_dir": output_dir,
                    "reuse_existing": reuse_existing,
                    "render_images": render_images,
                    "write_auxiliary_summaries": write_auxiliary_summaries,
                    "source_family_by_sample": source_family_by_family.get(
                        text_value(row.get("feature_family_id")),
                        {},
                    ),
                    "dpi": dpi,
                },
            )
            for row in selected_rows
        ]
        with recorder.stage(
            "standard_peak.shift_aware_batch.rows_parallel",
            metrics={
                "row_count": len(selected_rows),
                "workers": workers,
                "render_images": render_images,
                "dpi": dpi,
            },
        ):
            with ProcessPoolExecutor(max_workers=workers) as executor:
                for result in executor.map(_render_or_reuse_row_job, payloads):
                    rows.append(result)
                    if write_incremental:
                        with recorder.stage(
                            "standard_peak.shift_aware_batch.write_incremental_summary",
                            metrics={"row_count": len(rows)},
                        ):
                            write_tsv(
                                output_dir
                                / "family_ms1_alignment_experiment_batch_summary.tsv",
                                rows,
                                SUMMARY_COLUMNS,
                            )
    else:
        for row in selected_rows:
            family = text_value(row.get("feature_family_id"))
            with recorder.stage(
                "standard_peak.shift_aware_batch.row",
                metrics={
                    "rank": text_value(row.get("rank")),
                    "feature_family_id": family,
                    "render_images": render_images,
                },
            ) as scope:
                result = _render_or_reuse_row(
                    row,
                    cell_evidence_tsv=cell_evidence_tsv,
                    output_dir=output_dir,
                    reuse_existing=reuse_existing,
                    render_images=render_images,
                    write_auxiliary_summaries=write_auxiliary_summaries,
                    source_family_by_sample=source_family_by_family.get(family, {}),
                    dpi=dpi,
                )
                scope.metrics["alignment_status"] = result["alignment_status"]
                rows.append(result)
            if write_incremental:
                with recorder.stage(
                    "standard_peak.shift_aware_batch.write_incremental_summary",
                    metrics={"row_count": len(rows)},
                ):
                    write_tsv(
                        output_dir
                        / "family_ms1_alignment_experiment_batch_summary.tsv",
                        rows,
                        SUMMARY_COLUMNS,
                    )
    status_counts = Counter(row["alignment_status"] for row in rows)
    summary: dict[str, Any] = {
        "schema_version": "family_ms1_alignment_experiment_batch",
        "source_overlay_batch_summary_tsv": str(overlay_batch_summary_tsv),
        "source_cell_evidence_tsv": str(cell_evidence_tsv),
        "start_rank": start_rank,
        "limit": "" if limit is None else limit,
        "selected_row_count": len(rows),
        "render_images": render_images,
        "render_workers": workers,
        "render_dpi": dpi,
        "write_incremental": write_incremental,
        "source_family_map_source": source_family_map_source,
        "status_counts": dict(sorted(status_counts.items())),
        "successful_shift_aware_row_count": sum(
            1
            for row in rows
            if row["alignment_status"] in {"rendered", "reused"}
        ),
    }
    return rows, summary


def _render_or_reuse_row(
    row: Mapping[str, str],
    *,
    cell_evidence_tsv: Path,
    output_dir: Path,
    reuse_existing: bool,
    render_images: bool,
    write_auxiliary_summaries: bool,
    source_family_by_sample: Mapping[str, str],
    dpi: int = 140,
) -> dict[str, str]:
    rank = text_value(row.get("rank"))
    family = text_value(row.get("feature_family_id"))
    seed_group = text_value(row.get("seed_group_id"))
    overlay_status = text_value(row.get("status"))
    trace_json = text_value(row.get("trace_data_json"))
    output_prefix = _default_output_prefix(row)
    best_summary = output_dir / f"{output_prefix}_source_family_best_shift_summary.tsv"
    best_png = output_dir / f"{output_prefix}_source_family_best_shift_alignment.png"
    base = {
        "rank": rank,
        "feature_family_id": family,
        "seed_group_id": seed_group,
        "overlay_status": overlay_status,
        "output_prefix": output_prefix,
        "trace_data_json": trace_json,
        "source_best_shift_summary_tsv": str(best_summary),
        "source_best_shift_png": str(best_png) if render_images else "",
    }
    if overlay_status != "success":
        return {
            **base,
            "alignment_status": "skipped",
            "failure_reason": f"overlay_status_not_success:{overlay_status}",
        }
    if not trace_json:
        return {
            **base,
            "alignment_status": "failed",
            "failure_reason": "missing_trace_data_json",
        }
    trace_path = Path(trace_json)
    if not trace_path.exists():
        return {
            **base,
            "alignment_status": "failed",
            "failure_reason": f"trace_data_json_not_found:{trace_json}",
        }
    if reuse_existing and best_summary.exists() and (
        best_png.exists() if render_images else True
    ):
        return {**base, "alignment_status": "reused", "failure_reason": ""}
    try:
        family_ms1_alignment_experiment.run_alignment_experiment(
            trace_data_json=trace_path,
            output_dir=output_dir,
            output_prefix=output_prefix,
            cell_evidence_tsv=cell_evidence_tsv,
            source_family_by_sample=source_family_by_sample,
            render_images=render_images,
            write_auxiliary_summaries=write_auxiliary_summaries,
            dpi=dpi,
        )
    except Exception:  # noqa: BLE001 - preserve batch failure semantics.
        return {
            **base,
            "alignment_status": "failed",
            "failure_reason": "alignment_experiment_exit_code:2",
        }
    if not best_summary.exists():
        return {
            **base,
            "alignment_status": "failed",
            "failure_reason": "missing_source_family_best_shift_summary",
        }
    return {**base, "alignment_status": "rendered", "failure_reason": ""}


def _render_or_reuse_row_job(
    payload: tuple[Mapping[str, str], Mapping[str, Any]],
) -> dict[str, str]:
    row, kwargs = payload
    return _render_or_reuse_row(row, **dict(kwargs))


def _select_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    start_rank: int,
    limit: int | None,
) -> list[Mapping[str, str]]:
    selected = [
        row
        for row in rows
        if _positive_int(text_value(row.get("rank"))) >= start_rank
    ]
    return selected if limit is None else selected[:limit]


def _default_output_prefix(row: Mapping[str, str]) -> str:
    rank = _positive_int(text_value(row.get("rank")))
    family = text_value(row.get("feature_family_id")).lower()
    token = _family_file_token(family) if family else "unknown"
    return f"{rank:03d}_{token}_shift_aware"


def _family_file_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return token or "unknown"


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return 0
    return parsed if parsed > 0 else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay-batch-summary-tsv", type=Path, required=True)
    parser.add_argument("--cell-evidence-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help=(
            "Reuse rows whose source-family best-shift summary already exists "
            "in output-dir. Image mode also requires the best-shift PNG."
        ),
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Write shift-aware summary TSVs without rendering PNG review images.",
    )
    parser.add_argument(
        "--best-shift-only",
        action="store_true",
        help=(
            "Skip per-row auxiliary summaries and write only the best-shift "
            "summary TSVs consumed by the calibration pack."
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel render worker processes (1 = serial).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=140,
        help="Render DPI for shift-aware PNGs (default 140).",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
