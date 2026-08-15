"""Consolidate chunked standard-peak machine pipeline outputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from xic_extractor.diagnostics.standard_peak_backfill_chunk_consolidation import (
    SOURCE_MATRIX_REPUBLISHER_RETIRED_MESSAGE,
    run_standard_peak_backfill_chunk_consolidation,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if (args.publish_alignment_matrix_tsv is None) != (
        args.publish_alignment_matrix_identity_tsv is None
    ):
        print(
            "--publish-alignment-matrix-tsv and "
            "--publish-alignment-matrix-identity-tsv must be supplied together",
            file=sys.stderr,
        )
        return 2
    if args.publish_alignment_matrix_tsv is not None:
        print(SOURCE_MATRIX_REPUBLISHER_RETIRED_MESSAGE, file=sys.stderr)
        return 2
    summaries = _resolve_summary_paths(
        machine_pipeline_summary_jsons=tuple(
            args.machine_pipeline_summary_json or (),
        ),
        chunk_dirs=tuple(args.chunk_dir or ()),
    )
    try:
        outputs = run_standard_peak_backfill_chunk_consolidation(
            machine_pipeline_summary_jsons=summaries,
            alignment_matrix_tsv=args.alignment_matrix_tsv,
            alignment_matrix_identity_tsv=args.alignment_matrix_identity_tsv,
            alignment_review_tsv=args.alignment_review_tsv,
            output_dir=args.output_dir,
            source_run_id=args.source_run_id,
            review_queue_tsv=args.review_queue_tsv,
            write_gallery=args.write_gallery,
            alignment_cells_tsv=args.alignment_cells_tsv,
            backfill_seed_audit_tsv=args.backfill_seed_audit_tsv,
            retained_backfill_gate_tsv=args.retained_backfill_evidence_gate_tsv,
            gallery_output_dir=args.gallery_output_dir,
            emit_formal_product_output=args.emit_formal_product_output,
            publish_to_source_alignment_output=False,
            formal_product_output_dir=args.formal_product_output_dir,
            publish_alignment_matrix_tsv=args.publish_alignment_matrix_tsv,
            publish_alignment_matrix_identity_tsv=(
                args.publish_alignment_matrix_identity_tsv
            ),
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Consolidation summary TSV: {outputs.summary_tsv}")
    print(f"Consolidation summary JSON: {outputs.summary_json}")
    print(
        "Consolidated shadow projection cells TSV: "
        f"{outputs.merged_shadow_projection_cells_tsv}",
    )
    if outputs.productization.activated_matrix_tsv is not None:
        print(f"Activated matrix TSV: {outputs.productization.activated_matrix_tsv}")
    if outputs.productization.activation_value_delta_tsv is not None:
        print(
            "Activation value delta TSV: "
            f"{outputs.productization.activation_value_delta_tsv}",
        )
    if outputs.productization.reconciliation_gallery_html is not None:
        print(
            "Activation-synced reconciliation gallery HTML: "
            f"{outputs.productization.reconciliation_gallery_html}",
        )
    if outputs.formal_product_output_dir is not None:
        print(f"Formal product output dir: {outputs.formal_product_output_dir}")
    if outputs.formal_product_manifest_json is not None:
        print(
            "Formal product manifest JSON: "
            f"{outputs.formal_product_manifest_json}",
        )
    if outputs.published_alignment_output_dir is not None:
        print(
            "Published alignment output dir: "
            f"{outputs.published_alignment_output_dir}",
        )
    if outputs.published_alignment_manifest_json is not None:
        print(
            "Published alignment manifest JSON: "
            f"{outputs.published_alignment_manifest_json}",
        )
    return 0 if outputs.status == "pass" else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--machine-pipeline-summary-json",
        action="append",
        type=Path,
        help=(
            "standard_peak_backfill_machine_pipeline_summary.json. Repeat for "
            "each chunk."
        ),
    )
    parser.add_argument(
        "--chunk-dir",
        action="append",
        type=Path,
        help=(
            "Chunk output directory containing "
            "standard_peak_backfill_machine_pipeline_summary.json. Repeatable."
        ),
    )
    parser.add_argument(
        "--review-queue-tsv",
        type=Path,
        help=(
            "Optional full review queue TSV. When provided, consolidation "
            "requires complete non-overlapping rank coverage."
        ),
    )
    parser.add_argument("--alignment-matrix-tsv", type=Path, required=True)
    parser.add_argument(
        "--alignment-matrix-identity-tsv",
        type=Path,
        required=True,
    )
    parser.add_argument("--alignment-review-tsv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-run-id", default="")
    parser.add_argument(
        "--write-gallery",
        action="store_true",
        help="Also render one activation-synced gallery from the consolidated run.",
    )
    parser.add_argument(
        "--alignment-cells-tsv",
        type=Path,
        help="Cell evidence TSV for optional gallery rendering.",
    )
    parser.add_argument(
        "--backfill-seed-audit-tsv",
        type=Path,
        help="Optional alignment_owner_backfill_seed_audit.tsv for gallery.",
    )
    parser.add_argument(
        "--retained-backfill-evidence-gate-tsv",
        type=Path,
        help="Optional retained gate TSV for gallery.",
    )
    parser.add_argument(
        "--gallery-output-dir",
        type=Path,
        help="Optional output directory for the synced gallery.",
    )
    parser.add_argument(
        "--emit-formal-product-output",
        action="store_true",
        help=(
            "After the consolidated activation passes, publish a formal "
            "downstream product output directory containing alignment_matrix.tsv, "
            "identity sidecars, activation delta audit, and a manifest."
        ),
    )
    parser.add_argument(
        "--formal-product-output-dir",
        type=Path,
        help=(
            "Optional output directory for --emit-formal-product-output. Defaults "
            "to <output-dir>/formal_product_output."
        ),
    )
    parser.add_argument(
        "--no-publish-to-source-alignment-output",
        action="store_true",
        help=(
            "Compatibility flag. Source-Alignment republication is retired, so "
            "formal product output is always kept as a diagnostic sidecar."
        ),
    )
    parser.add_argument(
        "--publish-alignment-matrix-tsv",
        type=Path,
        help=(
            "Optional target alignment_matrix.tsv to publish into after a passing "
            "formal product output. Defaults to --alignment-matrix-tsv. Use this "
            "with a pre-standard backup input when rerunning after publication."
        ),
    )
    parser.add_argument(
        "--publish-alignment-matrix-identity-tsv",
        type=Path,
        help=(
            "Optional target alignment_matrix_identity.tsv to publish into. "
            "Defaults to --alignment-matrix-identity-tsv and should be supplied "
            "together with --publish-alignment-matrix-tsv."
        ),
    )
    return parser.parse_args(argv)


def _resolve_summary_paths(
    *,
    machine_pipeline_summary_jsons: Sequence[Path],
    chunk_dirs: Sequence[Path],
) -> tuple[Path, ...]:
    paths = list(machine_pipeline_summary_jsons)
    paths.extend(
        chunk_dir / "standard_peak_backfill_machine_pipeline_summary.json"
        for chunk_dir in chunk_dirs
    )
    if not paths:
        raise ValueError(
            "provide at least one --machine-pipeline-summary-json or --chunk-dir",
        )
    return tuple(paths)


if __name__ == "__main__":
    raise SystemExit(main())
