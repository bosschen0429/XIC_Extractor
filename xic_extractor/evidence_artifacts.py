"""Locator for a completed Alignment evidence report."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EvidenceLocator:
    """Immutable provenance locator pointing to a completed evidence DB."""

    report_path: Path
    matrix_path: Path
    matrix_sha256: str
    processing_attempt_id: str
    schema_version: str
