"""Workbench and batch entry to Matrix curation of a published Alignment folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xic_extractor.matrix_curation.export import (
    CurationSource,
    default_output_dir,
    describe_steps,
    export_matrix_curation,
    locate_curation_source,
    read_sample_metadata_file,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.samples import from_payload, sample_annotations_path

RUN_METADATA_NAME = "alignment_run_metadata.json"
MISSING_SAMPLES = (
    "找不到樣本資料：這次執行沒有帶入樣本資料，RAW 資料夾也沒有已確認的樣本資訊。"
    "請選擇樣本資料 TSV。"
)


def resolve_samples(
    config_dir: Path,
    source: CurationSource,
    *,
    sample_metadata_path: str = "",
    samples: object = None,
) -> tuple[tuple[SampleMetadata, ...] | None, str]:
    """Pick sample data: a chosen file, the run's own rows, then confirmed rows."""
    if sample_metadata_path.strip():
        path = Path(sample_metadata_path.strip())
        return read_sample_metadata_file(path), str(path)
    rows = from_payload(samples)
    if rows is not None:
        return rows, "本次 Untargeted 執行的樣本資料"
    raw_dir = _run_raw_dir(source.source_dir)
    if raw_dir is not None:
        confirmed = sample_annotations_path(config_dir, raw_dir)
        if confirmed.is_file():
            return read_sample_metadata_file(confirmed), str(confirmed)
    return None, ""


def _run_raw_dir(source_dir: Path) -> Path | None:
    path = source_dir / RUN_METADATA_NAME
    if not path.is_file():
        return None
    try:
        raw_dir = json.loads(path.read_text(encoding="utf-8")).get("raw_dir")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(raw_dir) if isinstance(raw_dir, str) and raw_dir.strip() else None


def describe_source(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    """What the page needs before a run: inputs found and which steps can run."""
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    metadata, sample_source = resolve_samples(
        config_dir,
        source,
        sample_metadata_path=str(values.get("sample_metadata_path") or ""),
        samples=values.get("samples"),
    )
    return {
        "source_dir": str(source.source_dir),
        "matrix_path": str(source.matrix_path),
        "review_path": str(source.review_path),
        "sample_source": sample_source,
        "steps": describe_steps(source, metadata, missing_samples=MISSING_SAMPLES),
        "output_dir": str(default_output_dir(source)),
    }


def run_curation(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    metadata, sample_source = resolve_samples(
        config_dir,
        source,
        sample_metadata_path=str(values.get("sample_metadata_path") or ""),
        samples=values.get("samples"),
    )
    steps = values.get("steps")
    if not isinstance(steps, dict):
        raise ValueError("請至少啟用一個整理步驟。")
    output_text = str(values.get("output_dir") or "").strip()
    output_dir = Path(output_text) if output_text else default_output_dir(source)
    summary = export_matrix_curation(
        source=source,
        sample_metadata=metadata,
        sample_source=sample_source,
        steps=steps,
        output_dir=output_dir,
    )
    counts = json.loads(summary.read_text(encoding="utf-8"))
    return {
        "output_dir": str(output_dir),
        "source_dir": str(source.source_dir),
        "summary_path": str(summary),
        "feature_count": counts["feature_count"],
        "kept_feature_count": counts["kept_feature_count"],
        "published_paths": [
            str(path)
            for path in (
                output_dir / "analysis_matrix.tsv",
                output_dir / "feature_decisions.tsv",
                output_dir / "sample_metadata.tsv",
                summary,
            )
            if path.is_file()
        ],
    }
