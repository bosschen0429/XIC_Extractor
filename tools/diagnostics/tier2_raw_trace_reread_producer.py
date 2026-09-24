"""Produce diagnostic-only Tier 2 RAW trace re-read sidecars."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from tools.diagnostics.diagnostic_io import file_sha256, read_tsv_required, write_tsv
from xic_extractor.alignment.production_candidate_gate import (
    TIER2_RAW_MANIFEST_REQUIRED_COLUMNS,
    TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS,
    is_candidate_gate_scope,
    source_context_for_artifacts,
    tier2_candidate_subset_signature,
)
from xic_extractor.alignment.tier2_trace_producer import (
    CRITERIA_VERSION,
    PRODUCER_VERSION,
    Tier2TraceProducerConfig,
    TraceLoader,
    build_tier2_trace_evidence_rows,
)

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "family_center_mz",
    "family_center_rt",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_confidence",
    "identity_reason",
    "primary_evidence",
    "quantifiable_detected_count",
    "quantifiable_backfill_count",
    "duplicate_assigned_count",
    "ambiguous_ms1_owner_count",
    "row_flags",
)
CELL_REQUIRED_COLUMNS = (
    "feature_family_id",
    "sample_stem",
    "status",
    "area",
    "apex_rt",
    "height",
    "peak_start_rt",
    "peak_end_rt",
    "rt_delta_sec",
    "trace_quality",
    "scan_support_score",
    "reason",
)
MATRIX_REQUIRED_COLUMNS = ("feature_family_id",)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_producer(
            alignment_dir=args.alignment_dir,
            output_dir=args.output_dir,
            raw_dir=args.raw_dir,
            dll_dir=args.dll_dir,
            expected_sample_count=args.expected_sample_count,
            config=Tier2TraceProducerConfig(
                ppm_tolerance=args.ppm_tol,
                rt_padding_min=args.rt_padding_min,
                max_backfilled_cells_per_family=args.max_rescued_cells_per_family,
            ),
            producer_command=" ".join(sys.argv),
            python_executable=sys.executable,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Tier 2 trace evidence TSV: {outputs['trace_evidence']}")
    print(f"Tier 2 RAW manifest TSV: {outputs['raw_manifest']}")
    print(f"Tier 2 summary JSON: {outputs['summary']}")
    return 0


def run_producer(
    *,
    alignment_dir: Path,
    output_dir: Path,
    raw_dir: Path,
    dll_dir: Path,
    expected_sample_count: int | None = None,
    trace_loader: TraceLoader | None = None,
    config: Tier2TraceProducerConfig | None = None,
    producer_command: str | None = None,
    generated_at_utc: str | None = None,
    python_executable: str | None = None,
) -> dict[str, Path]:
    review_path = alignment_dir / "alignment_review.tsv"
    cell_path = alignment_dir / "alignment_cells.tsv"
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    review_rows = read_tsv_required(review_path, REVIEW_REQUIRED_COLUMNS)
    cell_rows = read_tsv_required(cell_path, CELL_REQUIRED_COLUMNS)
    read_tsv_required(matrix_path, MATRIX_REQUIRED_COLUMNS)
    candidate_rows = tuple(row for row in review_rows if is_candidate_gate_scope(row))
    cells_by_family = _cells_by_family(cell_rows)
    sample_stems = tuple(sorted({row["sample_stem"] for row in cell_rows}))
    if expected_sample_count is not None and len(sample_stems) != expected_sample_count:
        raise ValueError(
            "expected_sample_count mismatch: "
            f"expected {expected_sample_count}, observed {len(sample_stems)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_manifest_path = output_dir / "alignment_tier2_raw_manifest.tsv"
    trace_evidence_path = output_dir / "alignment_tier2_trace_evidence.tsv"
    summary_path = output_dir / "alignment_tier2_trace_evidence_summary.json"

    effective_python = python_executable or sys.executable
    manifest_rows = _raw_manifest_rows(
        sample_stems=sample_stems,
        raw_dir=raw_dir,
        dll_dir=dll_dir,
        python_executable=effective_python,
    )
    write_tsv(
        raw_manifest_path,
        manifest_rows,
        TIER2_RAW_MANIFEST_REQUIRED_COLUMNS,
        lineterminator="\n",
    )
    source_context = source_context_for_artifacts(
        review_path=review_path,
        cell_path=cell_path,
        matrix_path=matrix_path,
    )
    effective_config = config or Tier2TraceProducerConfig()
    loader = trace_loader or _raw_trace_loader(raw_dir=raw_dir, dll_dir=dll_dir)
    generated = generated_at_utc or _utc_now()
    evidence_rows = build_tier2_trace_evidence_rows(
        candidate_rows=candidate_rows,
        cells_by_family=cells_by_family,
        source_context=source_context,
        raw_manifest_sha256=file_sha256(raw_manifest_path),
        source_expected_sample_count=len(sample_stems),
        trace_loader=loader,
        config=effective_config,
        producer_command=producer_command or "tier2_raw_trace_reread_producer",
        generated_at_utc=generated,
        python_executable=effective_python,
        dll_dir=str(dll_dir),
    )
    write_tsv(
        trace_evidence_path,
        evidence_rows,
        TIER2_TRACE_EVIDENCE_REQUIRED_COLUMNS,
        lineterminator="\n",
    )
    candidate_subset = tier2_candidate_subset_signature(candidate_rows)
    status_counts = Counter(row.get("evidence_status", "") for row in evidence_rows)
    positive_support_count = sum(
        1
        for row in evidence_rows
        if row.get("support_component") == "validated_tier2_trace_evidence"
    )
    summary = {
        "readiness_label": "diagnostic_only",
        "alignment_dir": str(alignment_dir),
        "source_review_artifact": str(source_context.review_path),
        "source_review_sha256": source_context.review_sha256,
        "source_cell_artifact": str(source_context.cell_path),
        "source_cell_sha256": source_context.cell_sha256,
        "source_matrix_artifact": str(source_context.matrix_path),
        "source_matrix_sha256": source_context.matrix_sha256,
        "raw_dir": str(raw_dir),
        "dll_dir": str(dll_dir),
        "candidate_count": len(candidate_rows),
        "candidate_subset_sha256": candidate_subset.sha256,
        "candidate_subset_count": candidate_subset.count,
        "source_expected_sample_count": len(sample_stems),
        "producer_version": PRODUCER_VERSION,
        "criteria_version": CRITERIA_VERSION,
        "rows_evaluated": len(evidence_rows),
        "evidence_status_counts": dict(sorted(status_counts.items())),
        "positive_support_count": positive_support_count,
        "raw_manifest_artifact": str(raw_manifest_path),
        "raw_manifest_sha256": file_sha256(raw_manifest_path),
        "trace_evidence_artifact": str(trace_evidence_path),
        "trace_evidence_sha256": file_sha256(trace_evidence_path),
        "validated_count": sum(
            1 for row in evidence_rows if row.get("evidence_status") == "validated"
        ),
        "blocked_count": sum(
            1 for row in evidence_rows if row.get("evidence_status") == "blocked"
        ),
        "not_supported_count": sum(
            1 for row in evidence_rows if row.get("evidence_status") == "not_supported"
        ),
        "inconclusive_count": sum(
            1 for row in evidence_rows if row.get("evidence_status") == "inconclusive"
        ),
        "production_ready": False,
        "matrix_contract_changed": False,
    }
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {
        "trace_evidence": trace_evidence_path,
        "raw_manifest": raw_manifest_path,
        "summary": summary_path,
    }


def _raw_manifest_rows(
    *,
    sample_stems: Sequence[str],
    raw_dir: Path,
    dll_dir: Path,
    python_executable: str,
) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for sample_stem in sample_stems:
        raw_path = raw_dir / f"{sample_stem}.raw"
        stat = raw_path.stat() if raw_path.is_file() else None
        rows.append(
            {
                "sample_stem": sample_stem,
                "raw_file_path": str(raw_path),
                "raw_file_size_bytes": "" if stat is None else str(stat.st_size),
                "raw_file_mtime_utc": (
                    "" if stat is None else _format_utc_timestamp(stat.st_mtime)
                ),
                "raw_reader_runtime": "pythonnet",
                "python_executable": python_executable,
                "dll_dir": str(dll_dir),
            }
        )
    return tuple(rows)


def _raw_trace_loader(
    *,
    raw_dir: Path,
    dll_dir: Path,
) -> TraceLoader:
    def load(
        sample_stem: str,
        mz: float,
        rt_min: float,
        rt_max: float,
        ppm_tolerance: float,
    ) -> tuple[object, object]:
        from xic_extractor.raw_reader import open_raw

        raw_path = raw_dir / f"{sample_stem}.raw"
        with open_raw(raw_path, dll_dir) as raw:
            return raw.extract_xic(mz, rt_min, rt_max, ppm_tolerance)

    return load


def _cells_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["feature_family_id"], []).append(row)
    return {family_id: tuple(items) for family_id, items in grouped.items()}


def _format_utc_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> str:
    return (
        datetime.now(tz=UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alignment-dir",
        type=Path,
        required=True,
        help="Alignment output directory containing review/cell/matrix TSVs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for Tier 2 trace evidence sidecars.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        required=True,
        help="Directory containing <sample_stem>.raw files.",
    )
    parser.add_argument(
        "--dll-dir",
        type=Path,
        required=True,
        help="Thermo RawFileReader DLL directory.",
    )
    parser.add_argument(
        "--expected-sample-count",
        type=int,
        help="Optional guard for the number of sample stems in alignment_cells.tsv.",
    )
    parser.add_argument(
        "--ppm-tol",
        type=float,
        default=20.0,
        help="MS1 XIC extraction tolerance in ppm.",
    )
    parser.add_argument(
        "--rt-padding-min",
        type=float,
        default=0.05,
        help="Minutes added to each side of the alignment cell boundary.",
    )
    parser.add_argument(
        "--max-rescued-cells-per-family",
        type=int,
        default=8,
        help="Maximum rescued cells reread per candidate family.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
