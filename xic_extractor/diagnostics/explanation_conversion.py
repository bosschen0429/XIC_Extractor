"""Reduce a completed full report without reacquisition or scientific reconstruction."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any

from xic_extractor.alignment_explanation.reader import read_review_cells
from xic_extractor.alignment_explanation.review import (
    REVIEW_SCHEMA_VERSION,
    review_record,
)
from xic_extractor.alignment_explanation.storage import (
    CANDIDATE_SELECT,
    ExplanationError,
    decode_candidate,
    decode_object,
    open_readonly,
    read_completed_summary,
)
from xic_extractor.alignment_explanation.writer import ExplanationReportWriter


def compact_explanation(
    source: Path, destination: Path, *, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Keep the same scalar cells and review facts; omit full-only receipts/curves."""
    started = perf_counter()
    if source.resolve() == destination.resolve():
        raise ExplanationError("Compact output must differ from the source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open_readonly(source) as db:
        summary = read_completed_summary(db)
        if summary["schema_version"] == REVIEW_SCHEMA_VERSION:
            raise ExplanationError("Source is already a compact review")
        samples = {s["sample_key"]: i for i, s in enumerate(summary["samples"])}
        matrix = Path(summary["matrix_path"])
        if not matrix.is_file():
            matrix = source.parent / matrix.name
        if not matrix.is_file():
            raise ExplanationError("The bound Matrix must be present beside the source")
        with matrix.open("rb") as handle:
            if (
                hashlib.file_digest(handle, "sha256").hexdigest()
                != summary["matrix_sha256"]
            ):
                raise ExplanationError("The bound Matrix hash differs from the report")
        with ExplanationReportWriter(destination, review_only=True) as writer:
            if progress:
                progress("Copying candidate and group review facts")
            for row in db.execute(CANDIDATE_SELECT):
                _, candidate = decode_candidate(row)
                writer.add_candidate(
                    candidate["candidate_id"], candidate["sample_key"], candidate
                )
            links = iter(
                db.execute(
                    "SELECT kind,record_id,candidate_id,sample_id FROM links "
                    "WHERE kind!='capture_evidence' ORDER BY kind,record_id"
                )
            )
            linked = next(links, None)
            for kind, rid, payload in db.execute(
                "SELECT kind,record_id,payload FROM records "
                "WHERE kind!='capture_evidence' ORDER BY kind,record_id"
            ):
                related = []
                while linked is not None and linked[:2] < (kind, rid):
                    linked = next(links, None)
                while linked is not None and linked[:2] == (kind, rid):
                    related.append((linked[2], linked[3]))
                    linked = next(links, None)
                writer.add_record(
                    kind, rid, review_record(kind, decode_object(payload)), related
                )
            if progress:
                progress("Copying cell values and review evidence")
            for (row_id,) in db.execute(
                "SELECT record_id FROM records WHERE kind='rows'"
            ):
                cells = list(read_review_cells(db, summary, (row_id,)).values())
                for cell in cells:
                    cell.pop("observation", None)
                writer.add_row_cells(row_id, cells, samples)
            summary.update(
                matrix_path=str(matrix.resolve()),
                retention="review",
                curve_retention="none",
                curve_mz_ranges=[],
                record_counts=dict(writer.record_counts),
            )
            writer.finalize(summary)
        return {
            "source_bytes": source.stat().st_size,
            "review_bytes": destination.stat().st_size,
            "candidate_count": summary["candidate_count"],
            "cell_count": writer.record_counts.get("cell_decisions", 0),
            "elapsed_seconds": round(perf_counter() - started, 3),
            "output": str(destination.resolve()),
        }
