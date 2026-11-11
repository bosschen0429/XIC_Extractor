"""Reduce a completed full report without reacquisition or scientific reconstruction."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from time import perf_counter
from typing import Any

from xic_extractor.alignment_explanation.errors import ExplanationError
from xic_extractor.alignment_explanation.storage import (
    encode_object,
    open_readonly,
    read_completed_summary,
)


def compact_explanation(
    source: Path, destination: Path, *, progress: Callable[[str], None] | None = None
) -> dict[str, Any]:
    """Keep the same scalar cells and review facts; omit full-only receipts/curves."""
    started = perf_counter()
    if source.resolve() == destination.resolve():
        raise ExplanationError("Compact output must differ from the source")
    if destination.exists():
        raise FileExistsError(f"explanation artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with open_readonly(source) as db:
        summary = read_completed_summary(db)
        if summary["retention"] == "review":
            raise ExplanationError("Source is already a compact review")
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
        if progress:
            progress("Copying candidate and group review facts")
        temporary.unlink(missing_ok=True)
        db.execute("VACUUM INTO ?", (str(temporary),))
    try:
        with closing(sqlite3.connect(temporary)) as out:
            out.execute("DROP TABLE IF EXISTS curves")
            out.execute(
                "UPDATE method_assessments "
                "SET acquisition_source_id=NULL, evaluation_receipt=NULL"
            )
            if progress:
                progress("Copying cell values and review evidence")
            out.execute("UPDATE cells SET observation=NULL")
            stored = json.loads(out.execute("SELECT payload FROM run").fetchone()[0])
            stored.update(
                matrix_path=str(matrix.resolve()),
                retention="review",
                curve_retention="none",
                curve_mz_ranges=[],
                plot_evidence_capability="metadata_only",
            )
            out.execute("UPDATE run SET payload=?", (encode_object(stored),))
            out.commit()
            out.execute("VACUUM")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    with open_readonly(destination) as db:
        compact = read_completed_summary(db)
        cell_count = db.execute("SELECT COUNT(*) FROM cells").fetchone()[0]
    return {
        "source_bytes": source.stat().st_size,
        "review_bytes": destination.stat().st_size,
        "candidate_count": compact["candidate_count"],
        "cell_count": cell_count,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "output": str(destination.resolve()),
    }
