"""Where an Alignment output keeps what curation reads; no curation computation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME

MATRIX_NAME = "alignment_matrix.tsv"
REVIEW_NAME = "alignment_review.tsv"
RUN_METADATA_NAME = "alignment_run_metadata.json"


@dataclass(frozen=True)
class CurationSource:
    source_dir: Path
    matrix_path: Path
    review_path: Path
    # Where the run read its RAW, from the Alignment run metadata when present.
    raw_dir: Path | None = None
    dll_dir: Path | None = None
    # The run's per-cell explanation, when the folder has one.
    explanation_path: Path | None = None
    # The run's declared target list, from the Alignment run metadata.
    target_snapshot: str | None = None


def locate_curation_source(source_dir: Path) -> CurationSource:
    """Find the published Matrix, its bound Review and the run's RAW folder."""
    if not source_dir.is_dir():
        raise ValueError(f"找不到 Alignment 輸出資料夾：{source_dir}")
    matrix, review = source_dir / MATRIX_NAME, source_dir / REVIEW_NAME
    if not matrix.is_file():
        raise ValueError(f"資料夾中沒有 {MATRIX_NAME}：{source_dir}")
    if not review.is_file():
        raise ValueError(
            f"資料夾中沒有 {REVIEW_NAME}；請以會輸出 Review 的層級重跑 Alignment。"
        )
    run: dict[str, Any] = {}
    try:
        loaded = json.loads((source_dir / RUN_METADATA_NAME).read_text("utf-8"))
        run = loaded if isinstance(loaded, dict) else {}
    except OSError, ValueError:
        pass
    raw_dir, dll_dir = (
        Path(value) if isinstance(value, str) and value.strip() else None
        for value in (run.get("raw_dir"), run.get("dll_dir"))
    )
    explanation = source_dir / EXPLANATION_ARTIFACT_NAME
    snapshot = run.get("known_istd_reference_target_snapshot")
    return CurationSource(
        source_dir,
        matrix,
        review,
        raw_dir,
        dll_dir,
        explanation if explanation.is_file() else None,
        snapshot if isinstance(snapshot, str) else None,
    )
