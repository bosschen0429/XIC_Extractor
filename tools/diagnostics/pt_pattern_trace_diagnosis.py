"""Chromatography-aware Pt pattern trace diagnosis.

This diagnostic probes whether existing Pt pattern candidates look like a
co-eluting chromatographic peak family. It reads ``pt_pattern_candidates.csv``,
extracts vendor XIC traces for base / +1 / +2 / +4 around the candidate base
RT, and writes trace-level metrics. It does not create candidates, change
Discovery results, or write Matrix/Backfill outputs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from xic_extractor.discovery.pt_pattern_trace_batch import (
    ANCHOR_LOCAL_METRIC_COLUMNS,
    ANCHOR_LOCAL_SCHEMA_VERSION,
    ANCHOR_LOCAL_SUMMARY_COLUMNS,
    ANCHOR_LOCAL_TRACE_POINT_COLUMNS,
    diagnose_pt_pattern_trace_batch,
)
from xic_extractor.discovery.pt_pattern_trace_evidence import (
    DEFAULT_ACTIVE_FRACTION,
    DEFAULT_LIMIT,
    DEFAULT_MAX_APEX_DELTA_SEC,
    DEFAULT_MIN_PEARSON_R,
    DEFAULT_PPM,
    DEFAULT_RT_HALF_WINDOW_SEC,
    EVIDENCE_OWNER,
    METRIC_COLUMNS,
    READINESS_TIER,
    REQUIRED_CANDIDATE_COLUMNS,
    SCHEMA_VERSION,
    SUMMARY_COLUMNS,
    TRACE_DECISION_BASIS,
    TRACE_POINT_COLUMNS,
    TRACE_SMOOTH_METHOD,
    TRACE_SMOOTH_WINDOW_POINTS,
    build_trace_requests_for_candidate,
    combined_pt_pattern_status,
    diagnose_candidate_traces,
    load_candidate_rows,
)
from xic_extractor.raw_reader import RawReaderError, open_raw
from xic_extractor.tabular_io import write_delimited_rows
from xic_extractor.xic_models import XICTrace

__all__ = (
    "REQUIRED_CANDIDATE_COLUMNS",
    "build_trace_requests_for_candidate",
    "combined_pt_pattern_status",
    "diagnose_candidate_traces",
    "load_candidate_rows",
    "main",
    "run_trace_diagnosis",
)


def run_trace_diagnosis(
    *,
    candidates_csv: Path,
    raw: Path,
    dll_dir: Path,
    output_dir: Path,
    limit: int,
    offset: int,
    ppm: float,
    rt_half_window_sec: float,
    mz_source: Literal["expected", "observed"],
    active_fraction: float,
    min_pearson_r: float,
    max_apex_delta_sec: float,
    write_trace_points: bool,
) -> dict[str, Path]:
    candidate_rows = load_candidate_rows(candidates_csv, limit=limit, offset=offset)
    request_items = tuple(
        item
        for row in candidate_rows
        for item in build_trace_requests_for_candidate(
            row,
            ppm=ppm,
            rt_half_window_sec=rt_half_window_sec,
            mz_source=mz_source,
        )
    )

    trace_by_key: dict[tuple[str, str], XICTrace] = {}
    if request_items:
        with open_raw(raw, dll_dir) as raw_handle:
            traces = raw_handle.extract_xic_many(
                tuple(item.request for item in request_items)
            )
        trace_by_key = {
            (item.candidate_id, item.position_label): trace
            for item, trace in zip(request_items, traces, strict=True)
        }

    diagnosis = diagnose_pt_pattern_trace_batch(
        candidate_rows,
        trace_by_key,
        ppm=ppm,
        rt_half_window_sec=rt_half_window_sec,
        mz_source=mz_source,
        active_fraction=active_fraction,
        min_pearson_r=min_pearson_r,
        max_apex_delta_sec=max_apex_delta_sec,
        include_trace_points=write_trace_points,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_csv = output_dir / "pt_pattern_trace_summary.csv"
    metrics_csv = output_dir / "pt_pattern_trace_metrics.csv"
    trace_points_csv = output_dir / "pt_pattern_trace_points.csv"
    sidecar_json = output_dir / "pt_pattern_trace_diagnosis.json"
    anchor_summary_csv = output_dir / "pt_pattern_anchor_local_trace_summary.csv"
    anchor_metrics_csv = output_dir / "pt_pattern_anchor_local_trace_metrics.csv"
    anchor_trace_points_csv = (
        output_dir / "pt_pattern_anchor_local_trace_points.csv"
    )
    anchor_sidecar_json = (
        output_dir / "pt_pattern_anchor_local_trace_diagnosis.json"
    )

    write_delimited_rows(summary_csv, diagnosis.legacy_summary_rows, SUMMARY_COLUMNS)
    write_delimited_rows(metrics_csv, diagnosis.legacy_metric_rows, METRIC_COLUMNS)
    write_delimited_rows(
        anchor_summary_csv,
        diagnosis.anchor_local_summary_rows,
        ANCHOR_LOCAL_SUMMARY_COLUMNS,
    )
    write_delimited_rows(
        anchor_metrics_csv,
        diagnosis.anchor_local_metric_rows,
        ANCHOR_LOCAL_METRIC_COLUMNS,
    )
    if write_trace_points:
        write_delimited_rows(
            trace_points_csv,
            diagnosis.legacy_trace_point_rows,
            TRACE_POINT_COLUMNS,
        )
        write_delimited_rows(
            anchor_trace_points_csv,
            diagnosis.anchor_local_trace_point_rows,
            ANCHOR_LOCAL_TRACE_POINT_COLUMNS,
        )

    status_counts = Counter(
        str(row.get("trace_support_status", ""))
        for row in diagnosis.legacy_summary_rows
    )
    combined_status_counts = Counter(
        str(row.get("combined_pt_pattern_status", ""))
        for row in diagnosis.legacy_summary_rows
    )
    sidecar = {
        "schema_version": SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "candidates_csv": str(candidates_csv),
        "raw": str(raw),
        "raw_file_name": raw.name,
        "output_dir": str(output_dir),
        "limit": limit,
        "offset": offset,
        "candidate_rows_loaded": len(candidate_rows),
        "trace_request_count": len(request_items),
        "ppm": ppm,
        "rt_half_window_sec": rt_half_window_sec,
        "mz_source": mz_source,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "trace_smoothing_method": TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": TRACE_SMOOTH_WINDOW_POINTS,
        "raw_trace_points_written": write_trace_points,
        "active_fraction": active_fraction,
        "min_pearson_r": min_pearson_r,
        "max_apex_delta_sec": max_apex_delta_sec,
        "trace_points_written": write_trace_points,
        "trace_support_status_counts": dict(sorted(status_counts.items())),
        "combined_pt_pattern_status_counts": dict(
            sorted(combined_status_counts.items())
        ),
        "outputs": {
            "summary_csv": str(summary_csv),
            "metrics_csv": str(metrics_csv),
            "trace_points_csv": str(trace_points_csv) if write_trace_points else None,
        },
        "diagnostic_only": True,
    }
    sidecar_json.write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    anchor_status_counts = Counter(
        str(row.get("trace_support_status", ""))
        for row in diagnosis.anchor_local_summary_rows
    )
    anchor_combined_status_counts = Counter(
        str(row.get("combined_pt_pattern_status", ""))
        for row in diagnosis.anchor_local_summary_rows
    )
    anchor_sidecar = {
        "schema_version": ANCHOR_LOCAL_SCHEMA_VERSION,
        "readiness_tier": READINESS_TIER,
        "evidence_owner": EVIDENCE_OWNER,
        "source_trace_schema_version": SCHEMA_VERSION,
        "anchor_segmentation_selector": (
            "anchor_containing_chrom_peak_segment.v1"
        ),
        "anchor_interval_semantics": "coelution_comparison_not_integration",
        "candidates_csv": str(candidates_csv),
        "raw": str(raw),
        "raw_file_name": raw.name,
        "output_dir": str(output_dir),
        "limit": limit,
        "offset": offset,
        "candidate_rows_loaded": len(candidate_rows),
        "trace_request_count": len(request_items),
        "ppm": ppm,
        "rt_half_window_sec": rt_half_window_sec,
        "mz_source": mz_source,
        "trace_decision_basis": TRACE_DECISION_BASIS,
        "trace_smoothing_method": TRACE_SMOOTH_METHOD,
        "trace_smoothing_window_points": TRACE_SMOOTH_WINDOW_POINTS,
        "active_fraction": active_fraction,
        "min_pearson_r": min_pearson_r,
        "max_apex_delta_sec": max_apex_delta_sec,
        "trace_points_written": write_trace_points,
        "anchor_segmentation_status_counts": dict(
            sorted(Counter(diagnosis.anchor_segmentation_statuses).items())
        ),
        "anchor_boundary_review_status_counts": dict(
            sorted(Counter(diagnosis.anchor_boundary_review_statuses).items())
        ),
        "trace_support_status_counts": dict(sorted(anchor_status_counts.items())),
        "combined_pt_pattern_status_counts": dict(
            sorted(anchor_combined_status_counts.items())
        ),
        "outputs": {
            "summary_csv": str(anchor_summary_csv),
            "metrics_csv": str(anchor_metrics_csv),
            "trace_points_csv": (
                str(anchor_trace_points_csv) if write_trace_points else None
            ),
            "full_context_trace_points_csv": (
                str(trace_points_csv) if write_trace_points else None
            ),
        },
        "diagnostic_only": True,
    }
    anchor_sidecar_json.write_text(
        json.dumps(anchor_sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "summary_csv": summary_csv,
        "metrics_csv": metrics_csv,
        "trace_points_csv": trace_points_csv,
        "sidecar_json": sidecar_json,
        "anchor_summary_csv": anchor_summary_csv,
        "anchor_metrics_csv": anchor_metrics_csv,
        "anchor_trace_points_csv": anchor_trace_points_csv,
        "anchor_sidecar_json": anchor_sidecar_json,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract base/+1/+2/+4 XIC traces for Pt pattern candidates and "
            "write chromatography-aware diagnostic metrics."
        )
    )
    parser.add_argument("--candidates-csv", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--dll-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output") / "pt_pattern_trace_diagnosis",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--ppm", type=float, default=DEFAULT_PPM)
    parser.add_argument(
        "--rt-half-window-sec",
        type=float,
        default=DEFAULT_RT_HALF_WINDOW_SEC,
    )
    parser.add_argument(
        "--mz-source",
        choices=("expected", "observed"),
        default="expected",
    )
    parser.add_argument(
        "--active-fraction",
        type=float,
        default=DEFAULT_ACTIVE_FRACTION,
    )
    parser.add_argument("--min-pearson-r", type=float, default=DEFAULT_MIN_PEARSON_R)
    parser.add_argument(
        "--max-apex-delta-sec",
        type=float,
        default=DEFAULT_MAX_APEX_DELTA_SEC,
    )
    parser.add_argument(
        "--write-trace-points",
        action="store_true",
        help="Also write every extracted trace point. Use with small --limit values.",
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.candidates_csv.is_file():
        parser.error(f"{args.candidates_csv}: candidates CSV does not exist")
    if not args.raw.is_file():
        parser.error(f"{args.raw}: RAW file does not exist")
    if not args.dll_dir.is_dir():
        parser.error(f"{args.dll_dir}: DLL directory does not exist")
    if args.limit <= 0:
        parser.error("--limit must be > 0")
    if args.offset < 0:
        parser.error("--offset must be >= 0")
    if args.ppm <= 0:
        parser.error("--ppm must be > 0")
    if args.rt_half_window_sec <= 0:
        parser.error("--rt-half-window-sec must be > 0")
    if not 0.0 < args.active_fraction <= 1.0:
        parser.error("--active-fraction must be in (0, 1]")
    if not -1.0 <= args.min_pearson_r <= 1.0:
        parser.error("--min-pearson-r must be in [-1, 1]")
    if args.max_apex_delta_sec < 0:
        parser.error("--max-apex-delta-sec must be >= 0")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)
    try:
        outputs = run_trace_diagnosis(
            candidates_csv=args.candidates_csv.resolve(),
            raw=args.raw.resolve(),
            dll_dir=args.dll_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            limit=args.limit,
            offset=args.offset,
            ppm=args.ppm,
            rt_half_window_sec=args.rt_half_window_sec,
            mz_source=args.mz_source,
            active_fraction=args.active_fraction,
            min_pearson_r=args.min_pearson_r,
            max_apex_delta_sec=args.max_apex_delta_sec,
            write_trace_points=args.write_trace_points,
        )
    except (OSError, RawReaderError, ValueError) as exc:
        print(f"pt_pattern_trace_diagnosis failed: {exc}", file=sys.stderr)
        return 2

    print(f"Pt pattern trace summary CSV: {outputs['summary_csv']}")
    print(f"Pt pattern trace metrics CSV: {outputs['metrics_csv']}")
    if args.write_trace_points:
        print(
            "Pt pattern full-context trace points CSV: "
            f"{outputs['trace_points_csv']}"
        )
    print(f"Pt pattern trace diagnosis JSON: {outputs['sidecar_json']}")
    print(f"Pt pattern anchor-local summary CSV: {outputs['anchor_summary_csv']}")
    print(f"Pt pattern anchor-local metrics CSV: {outputs['anchor_metrics_csv']}")
    if args.write_trace_points:
        print(
            "Pt pattern anchor-local trace points CSV: "
            f"{outputs['anchor_trace_points_csv']}"
        )
    print(
        "Pt pattern anchor-local diagnosis JSON: "
        f"{outputs['anchor_sidecar_json']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
