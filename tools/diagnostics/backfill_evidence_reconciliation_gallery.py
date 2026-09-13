"""Build a diagnostic-only backfill evidence reconciliation gallery."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_inspector.reconciliation import (
    run_reconciliation_gallery,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        outputs = run_reconciliation_gallery(
            alignment_review_tsv=args.alignment_review_tsv,
            alignment_cells_tsv=args.alignment_cells_tsv,
            output_dir=args.output_dir,
            alignment_matrix_tsv=args.alignment_matrix_tsv,
            backfill_seed_audit_tsv=args.backfill_seed_audit_tsv,
            overlay_batch_summary_tsvs=tuple(args.overlay_batch_summary_tsv or ()),
            shift_aware_same_pattern_tsvs=tuple(
                args.shift_aware_same_pattern_tsv or (),
            ),
            shift_aware_standard_peak_gate_tsvs=tuple(
                args.shift_aware_standard_peak_gate_tsv or (),
            ),
            seed_aware_family_tsv=args.seed_aware_family_tsv,
            seed_aware_summary_tsv=args.seed_aware_summary_tsv,
            candidate_gate_tsv=args.candidate_gate_tsv,
            retained_backfill_gate_tsv=args.retained_backfill_gate_tsv,
            tier2_trace_evidence_tsv=args.tier2_trace_evidence_tsv,
            shadow_policy_cells_tsv=args.shadow_policy_cells_tsv,
            shadow_projection_cells_tsv=args.shadow_projection_cells_tsv,
            activation_application_summary_tsv=(
                args.activation_application_summary_tsv
            ),
            activation_value_delta_tsv=args.activation_value_delta_tsv,
            targeted_istd_benchmark_summary_tsv=(
                args.targeted_istd_benchmark_summary_tsv
            ),
            source_run_id=args.source_run_id or "",
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"backfill evidence reconciliation groups TSV: {outputs.groups_tsv}")
    print(
        "backfill evidence reconciliation representative cells TSV: "
        f"{outputs.representative_cells_tsv}",
    )
    print(f"backfill evidence reconciliation summary JSON: {outputs.summary_json}")
    print(f"backfill evidence reconciliation gallery HTML: {outputs.gallery_html}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--alignment-review-tsv",
        required=True,
        type=Path,
        help="Source alignment_review.tsv.",
    )
    parser.add_argument(
        "--alignment-cells-tsv",
        "--alignment-cell-evidence-tsv",
        dest="alignment_cells_tsv",
        required=True,
        type=Path,
        help=(
            "Source cell evidence TSV: compact "
            "alignment_backfill_cell_evidence.tsv or legacy alignment_cells.tsv."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory for TSV, JSON, and HTML review outputs.",
    )
    parser.add_argument(
        "--alignment-matrix-tsv",
        type=Path,
        help="Optional alignment_matrix.tsv for matrix inclusion context only.",
    )
    parser.add_argument(
        "--backfill-seed-audit-tsv",
        type=Path,
        help="Optional alignment_owner_backfill_seed_audit.tsv.",
    )
    parser.add_argument(
        "--overlay-batch-summary-tsv",
        action="append",
        type=Path,
        help="Optional family/seed overlay batch summary TSV; repeatable.",
    )
    parser.add_argument(
        "--shift-aware-same-pattern-tsv",
        action="append",
        type=Path,
        help=(
            "Optional source_family_best_shift_summary.tsv with review-only "
            "source-family median-shape correlation evidence; repeatable."
        ),
    )
    parser.add_argument(
        "--shift-aware-standard-peak-gate-tsv",
        action="append",
        type=Path,
        help=(
            "Optional shift_aware_standard_peak_gate_calibration.tsv with "
            "shadow-only standard-peak gate evidence; repeatable."
        ),
    )
    parser.add_argument(
        "--seed-aware-family-tsv",
        type=Path,
        help="Optional seed_aware_backfill_review_families.tsv.",
    )
    parser.add_argument(
        "--seed-aware-summary-tsv",
        type=Path,
        help="Optional seed_aware_backfill_review_summary.tsv.",
    )
    parser.add_argument(
        "--candidate-gate-tsv",
        type=Path,
        help="Optional alignment_production_candidate_gate.tsv.",
    )
    parser.add_argument(
        "--retained-backfill-evidence-gate-tsv",
        dest="retained_backfill_gate_tsv",
        type=Path,
        help=(
            "Optional alignment_retained_backfill_evidence_gate.tsv for "
            "overlay review routing and no-overlay machine-support display."
        ),
    )
    parser.add_argument(
        "--tier2-trace-evidence-tsv",
        type=Path,
        help="Optional alignment_tier2_trace_evidence.tsv for provenance display.",
    )
    parser.add_argument(
        "--shadow-policy-cells-tsv",
        type=Path,
        help="Optional backfill_shadow_policy_cells.tsv for HTML provenance display.",
    )
    parser.add_argument(
        "--shadow-production-projection-cells-tsv",
        dest="shadow_projection_cells_tsv",
        type=Path,
        help=(
            "Optional shadow_production_projection_cells.tsv for current vs "
            "projected decision display."
        ),
    )
    parser.add_argument(
        "--activation-application-summary-tsv",
        type=Path,
        help=(
            "Optional activation_application_summary.tsv for activated matrix "
            "view provenance."
        ),
    )
    parser.add_argument(
        "--activation-value-delta-tsv",
        type=Path,
        help=(
            "Optional activation_value_delta.tsv. Written rows are joined "
            "row-level to projection accepts before the gallery displays "
            "activated product state."
        ),
    )
    parser.add_argument(
        "--targeted-istd-benchmark-summary-tsv",
        type=Path,
        help=(
            "Optional targeted_istd_benchmark_summary.tsv for validation-only "
            "target match context in the HTML gallery."
        ),
    )
    parser.add_argument(
        "--source-run-id",
        help="Optional run label displayed as provenance only.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
