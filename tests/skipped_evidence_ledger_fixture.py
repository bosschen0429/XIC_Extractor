from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path

from xic_extractor.alignment.backfill.evidence_contract import (
    SKIPPED_EVIDENCE_LEDGER_COLUMNS,
)
from xic_extractor.alignment.backfill.scope import SkippedEvidenceRecord
from xic_extractor.tabular_io import write_tsv


def write_skipped_evidence_ledger_fixture(
    path: Path,
    rows: Iterable[SkippedEvidenceRecord],
) -> Path:
    """Write a historical skipped-evidence ledger for reader tests."""

    write_tsv(
        path,
        (asdict(row) for row in rows),
        SKIPPED_EVIDENCE_LEDGER_COLUMNS,
        extrasaction="raise",
        formatter=lambda value: "" if value is None else str(value),
        lineterminator="\n",
    )
    return path
