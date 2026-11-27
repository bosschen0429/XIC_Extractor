"""Where an Alignment output keeps what curation reads; no curation computation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME
from xic_extractor.output.alignment.matrix_output_contract import (
    MATRIX_NAME,
    RUN_METADATA_NAME,
    locate_matrix_identity,
)


@dataclass(frozen=True)
class CurationSource:
    source_dir: Path
    matrix_path: Path
    identity_path: Path
    raw_dir: Path | None = None
    explanation_path: Path | None = None
    target_snapshot: str | None = None


def locate_curation_source(source_dir: Path) -> CurationSource:
    """Find the published Matrix, its row identities and the run's RAW folder."""
    if not source_dir.is_dir():
        raise ValueError(f"找不到 Untargeted 結果資料夾：{source_dir}")
    matrix = source_dir / MATRIX_NAME
    if not matrix.is_file():
        raise ValueError(f"資料夾中沒有 {MATRIX_NAME}：{source_dir}")
    run: dict[str, Any] = {}
    try:
        loaded = json.loads((source_dir / RUN_METADATA_NAME).read_text("utf-8"))
        run = loaded if isinstance(loaded, dict) else {}
    except OSError, ValueError:
        pass
    value = run.get("raw_dir")
    raw_dir = Path(value) if isinstance(value, str) and value.strip() else None
    explanation = source_dir / EXPLANATION_ARTIFACT_NAME
    snapshot = run.get("known_istd_reference_target_snapshot")
    return CurationSource(
        source_dir=source_dir,
        matrix_path=matrix,
        identity_path=locate_matrix_identity(source_dir, run),
        raw_dir=raw_dir,
        explanation_path=explanation if explanation.is_file() else None,
        target_snapshot=snapshot if isinstance(snapshot, str) else None,
    )
