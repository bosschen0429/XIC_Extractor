"""Input loading helpers for the alignment decision diagnostic report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.tabular_io import read_tsv_with_header


@dataclass(frozen=True)
class TsvTable:
    fieldnames: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


def read_tsv(path: Path, *, required_columns: tuple[str, ...]) -> TsvTable:
    try:
        fieldnames, rows = read_tsv_with_header(
            path,
            required_columns=required_columns,
            encoding="utf-8",
        )
        return TsvTable(
            fieldnames=fieldnames,
            rows=tuple(dict(row) for row in rows),
        )
    except OSError as exc:
        raise ValueError(f"{path}: could not read TSV: {exc}") from exc


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return payload
