"""Compose the backfill + MS1 overlay reconciliation gallery from alignment output.

``run_alignment`` only emits the tabular contract (matrix / review / cells). The
review gallery the user actually inspects — "explain why backfill" plus the MS1
overlay — is built by three further steps:

1. retained backfill evidence gate -> the overlay *review queue* (which families
   still need an overlay rendered) and the retained-gate TSV the gallery reads.
2. family MS1 overlay batch -> per-family overlay PNGs + a summary TSV. This is
   the only render-heavy step; it is parametrised on ``dpi`` / ``workers`` so the
   GUI can keep it inside the render budget.
3. reconciliation gallery -> the standalone HTML the GUI opens.

Keeping this composition in one testable place lets the worker call it after
alignment without embedding multi-step orchestration in a ``QThread``.

This review gallery deliberately does *not* run the shift-aware experiment batch.
The former Standard Peak shadow/productization/matrix-activation pipeline was
retired in T5D; retained overlay and shift-aware tools are diagnostic-only and
cannot change the Matrix.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from tools.diagnostics import family_ms1_overlay_batch
from xic_extractor.diagnostics.backfill_reconciliation_gallery import (
    run_reconciliation_gallery,
)
from xic_extractor.diagnostics.retained_backfill_evidence_gate import (
    run_retained_backfill_evidence_gate,
)


@dataclass(frozen=True)
class BackfillGalleryInputs:
    """Alignment artifacts the gallery is built from.

    ``cells`` and ``review`` are only emitted at ``machine``+ output levels (with
    ``emit_alignment_cells``); ``seed_audit`` is optional but improves the gate's
    seed-provenance evidence.
    """

    alignment_review_tsv: Path
    alignment_cells_tsv: Path
    alignment_matrix_tsv: Path
    raw_dir: Path
    dll_dir: Path
    backfill_seed_audit_tsv: Path | None = None


def build_backfill_review_gallery(
    inputs: BackfillGalleryInputs,
    *,
    output_dir: Path,
    dpi: int = 140,
    workers: int = 1,
    ppm: float = 20.0,
    source_run_id: str = "",
) -> Path:
    """Build the reconciliation gallery and return the standalone HTML path.

    The overlay render step is skipped when no family is queued for an overlay
    (e.g. a run with no retained backfills); the gallery is still produced from
    the gate evidence so the button has something honest to open.
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    gate = run_retained_backfill_evidence_gate(
        alignment_review_tsv=inputs.alignment_review_tsv,
        alignment_cells_tsv=inputs.alignment_cells_tsv,
        alignment_matrix_tsv=inputs.alignment_matrix_tsv,
        output_dir=output_dir / "retained_backfill_gate",
        backfill_seed_audit_tsv=inputs.backfill_seed_audit_tsv,
        source_run_id=source_run_id,
    )

    overlay_summary_tsvs: tuple[Path, ...] = ()
    queue_count = _row_count(gate.review_overlay_queue_tsv)
    if queue_count > 0:
        overlay_dir = output_dir / "family_ms1_overlay_batch"
        metrics: dict[str, object] = {}
        overlay_rows = family_ms1_overlay_batch.run_overlay_batch(
            review_queue_tsv=gate.review_overlay_queue_tsv,
            alignment_cells=inputs.alignment_cells_tsv,
            raw_dir=inputs.raw_dir,
            dll_dir=inputs.dll_dir,
            output_dir=overlay_dir,
            limit=queue_count,
            ppm=ppm,
            write_pdf=False,
            workers=workers,
            dpi=dpi,
            metrics=metrics,
        )
        family_ms1_overlay_batch._write_outputs(
            overlay_dir,
            overlay_rows,
            metrics=metrics,
        )
        overlay_summary_tsvs = (
            overlay_dir / "family_ms1_overlay_batch_summary.tsv",
        )

    gallery = run_reconciliation_gallery(
        alignment_review_tsv=inputs.alignment_review_tsv,
        alignment_cells_tsv=inputs.alignment_cells_tsv,
        output_dir=output_dir / "reconciliation_gallery",
        alignment_matrix_tsv=inputs.alignment_matrix_tsv,
        backfill_seed_audit_tsv=inputs.backfill_seed_audit_tsv,
        overlay_batch_summary_tsvs=overlay_summary_tsvs,
        retained_backfill_gate_tsv=gate.tsv,
        source_run_id=source_run_id,
    )
    return gallery.gallery_html


def _row_count(tsv_path: Path) -> int:
    if not tsv_path.exists():
        return 0
    with tsv_path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle, delimiter="\t"))
