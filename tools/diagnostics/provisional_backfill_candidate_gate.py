"""Write a diagnostic-only provisional backfill candidate gate sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from tools.diagnostics.diagnostic_io import file_sha256, read_tsv_required, write_tsv
from xic_extractor.alignment.production_candidate_gate import (
    PRODUCTION_CANDIDATE_GATE_COLUMNS,
    evaluate_production_candidate_gate,
    is_candidate_gate_scope,
    load_tier2_trace_evidence,
    production_candidate_gate_as_row,
    source_context_for_artifacts,
    summarize_gate_decisions,
    tier2_candidate_subset_signature,
)

REVIEW_REQUIRED_COLUMNS = (
    "feature_family_id",
    "neutral_loss_tag",
    "include_in_primary_matrix",
    "identity_decision",
    "identity_confidence",
    "identity_reason",
    "primary_evidence",
    "quantifiable_detected_count",
    "quantifiable_rescue_count",
    "review_rescue_count",
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
MATRIX_REQUIRED_COLUMNS: tuple[str, ...] = ()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        output_dir = args.output_dir or args.alignment_dir
        outputs = run_gate(
            alignment_dir=args.alignment_dir,
            output_dir=output_dir,
            tier2_trace_evidence_tsv=args.tier2_trace_evidence_tsv,
            tier2_raw_manifest_tsv=args.tier2_raw_manifest_tsv,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"production candidate gate TSV: {outputs['tsv']}")
    print(f"production candidate gate JSON: {outputs['json']}")
    return 0


def run_gate(
    *,
    alignment_dir: Path,
    output_dir: Path,
    tier2_trace_evidence_tsv: Path | None = None,
    tier2_raw_manifest_tsv: Path | None = None,
) -> dict[str, Path]:
    review_path = alignment_dir / "alignment_review.tsv"
    cell_path = alignment_dir / "alignment_cells.tsv"
    matrix_path = alignment_dir / "alignment_matrix.tsv"
    review_rows = _read_required_tsv(review_path, REVIEW_REQUIRED_COLUMNS)
    cell_rows = _read_required_tsv(cell_path, CELL_REQUIRED_COLUMNS)
    _read_required_tsv(matrix_path, MATRIX_REQUIRED_COLUMNS)
    source_context = source_context_for_artifacts(
        review_path=review_path,
        cell_path=cell_path,
        matrix_path=matrix_path,
    )
    cells_by_family = _cells_by_family(cell_rows)
    candidate_rows = [row for row in review_rows if is_candidate_gate_scope(row)]
    if bool(tier2_trace_evidence_tsv) != bool(tier2_raw_manifest_tsv):
        raise ValueError(
            "tier2_trace_evidence_tsv and tier2_raw_manifest_tsv "
            "must be supplied together"
        )
    tier2_evidence_by_family = (
        load_tier2_trace_evidence(
            sidecar_path=tier2_trace_evidence_tsv,
            raw_manifest_path=tier2_raw_manifest_tsv,
            candidate_rows=candidate_rows,
            source_context=source_context,
        )
        if tier2_trace_evidence_tsv is not None
        and tier2_raw_manifest_tsv is not None
        else {}
    )
    decisions = [
        evaluate_production_candidate_gate(
            review_row,
            cells_by_family.get(review_row["feature_family_id"], ()),
            source_context=source_context,
            tier2_evidence=tier2_evidence_by_family.get(
                review_row["feature_family_id"]
            ),
        )
        for review_row in candidate_rows
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    tsv_path = output_dir / "alignment_production_candidate_gate.tsv"
    json_path = output_dir / "alignment_production_candidate_gate.json"
    write_tsv(
        tsv_path,
        [production_candidate_gate_as_row(decision) for decision in decisions],
        PRODUCTION_CANDIDATE_GATE_COLUMNS,
        lineterminator="\n",
    )
    summary = summarize_gate_decisions(decisions)
    summary.update(
        {
            "alignment_dir": str(alignment_dir),
            "source_review_artifact": str(source_context.review_path),
            "source_review_sha256": source_context.review_sha256,
            "source_cell_artifact": str(source_context.cell_path),
            "source_cell_sha256": source_context.cell_sha256,
            "source_matrix_artifact": str(source_context.matrix_path),
            "source_matrix_sha256": source_context.matrix_sha256,
        },
    )
    if tier2_trace_evidence_tsv is not None and tier2_raw_manifest_tsv is not None:
        candidate_subset = tier2_candidate_subset_signature(candidate_rows)
        summary.update(
            {
                "tier2_trace_evidence_artifact": str(tier2_trace_evidence_tsv),
                "tier2_trace_evidence_sha256": file_sha256(
                    tier2_trace_evidence_tsv
                ),
                "tier2_raw_manifest_artifact": str(tier2_raw_manifest_tsv),
                "tier2_raw_manifest_sha256": file_sha256(tier2_raw_manifest_tsv),
                "tier2_candidate_subset_sha256": candidate_subset.sha256,
                "tier2_candidate_subset_count": candidate_subset.count,
            },
        )
    json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {"tsv": tsv_path, "json": json_path}


def _read_required_tsv(
    path: Path,
    required_columns: tuple[str, ...],
) -> tuple[dict[str, str], ...]:
    try:
        return read_tsv_required(path, required_columns)
    except FileNotFoundError as exc:
        raise ValueError(f"Required TSV not found: {path}") from exc


def _cells_by_family(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, tuple[Mapping[str, str], ...]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["feature_family_id"], []).append(row)
    return {family_id: tuple(items) for family_id, items in grouped.items()}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alignment-dir",
        type=Path,
        required=True,
        help=(
            "Alignment output directory containing alignment_review.tsv, "
            "alignment_cells.tsv, and alignment_matrix.tsv."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Output directory for alignment_production_candidate_gate.tsv and "
            "alignment_production_candidate_gate.json. Defaults to --alignment-dir."
        ),
    )
    parser.add_argument(
        "--tier2-trace-evidence-tsv",
        type=Path,
        help="Optional Tier 2 trace evidence sidecar TSV.",
    )
    parser.add_argument(
        "--tier2-raw-manifest-tsv",
        type=Path,
        help="Required with --tier2-trace-evidence-tsv; RAW manifest TSV.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
