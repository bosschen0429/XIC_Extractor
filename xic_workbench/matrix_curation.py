"""Workbench entry to Matrix curation of a published Alignment folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xic_extractor.matrix_curation.export import (
    default_output_dir,
    describe_steps,
    export_matrix_curation,
    prepare_matrix_curation,
    read_run_istd,
    read_run_samples,
    read_sample_metadata_file,
)
from xic_extractor.matrix_curation.istd import IstdWindow, read_istd_file
from xic_extractor.matrix_curation.same_compound import RELATIONS_NAME, REVIEW_NAME
from xic_extractor.matrix_curation.source import (
    RUN_METADATA_NAME,
    CurationSource,
    locate_curation_source,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.samples import from_payload, sample_annotations_path

MISSING_SAMPLES = (
    "找不到本次分析的樣本資訊。請在「改用其他來源」補入樣本資料；"
    "新分析會自動沿用並保存前面確認的角色、組別與排除設定。"
)


def resolve_samples(
    config_dir: Path,
    source: CurationSource,
    *,
    sample_metadata_path: str = "",
    samples: object = None,
) -> tuple[tuple[SampleMetadata, ...] | None, dict[str, str] | None]:
    """Pick sample data and say where it came from.

    Order: a chosen file, the saved run snapshot, the run's in-memory rows,
    confirmed folder rows, then RAW headers for older outputs.
    """
    if sample_metadata_path.strip():
        path = Path(sample_metadata_path.strip())
        return read_sample_metadata_file(path), {"kind": "file", "label": str(path)}
    saved = source.source_dir / "sample_metadata.tsv"
    if saved.is_file():
        return read_sample_metadata_file(saved), {
            "kind": "run",
            "label": "已沿用這次分析保存的樣本資訊",
        }
    rows = from_payload(samples)
    if rows is not None:
        return rows, {"kind": "run", "label": "本次 Untargeted 執行的樣本資料"}
    if source.raw_dir is not None:
        confirmed = sample_annotations_path(config_dir, source.raw_dir)
        if confirmed.is_file():
            return read_sample_metadata_file(confirmed), {
                "kind": "confirmed",
                "label": str(confirmed),
            }
    header_rows = read_run_samples(source)
    if header_rows is not None:
        return header_rows, {"kind": "raw", "label": str(source.raw_dir)}
    return None, None


def resolve_istd(
    source: CurationSource, *, istd_path: str = ""
) -> tuple[tuple[IstdWindow, ...] | None, dict[str, str] | None]:
    """Pick the internal standard list: a chosen file, else the run's own list."""
    if istd_path.strip():
        path = Path(istd_path.strip())
        return read_istd_file(path), {"kind": "file", "label": str(path)}
    windows = read_run_istd(source)
    if windows:
        return windows, {"kind": "run", "label": "本次分析宣告的內標清單"}
    return None, None


def describe_source(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    """What the page needs before a run: inputs found and which steps can run."""
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    metadata, sample_source = resolve_samples(
        config_dir,
        source,
        sample_metadata_path=str(values.get("sample_metadata_path") or ""),
        samples=values.get("samples"),
    )
    istd, istd_source = resolve_istd(
        source, istd_path=str(values.get("istd_path") or "")
    )
    return {
        "source_dir": str(source.source_dir),
        "matrix_path": str(source.matrix_path),
        "review_path": str(source.review_path),
        "sample_source": sample_source,
        "sample_count": len(metadata or ()),
        "study_sample_count": sum(
            row.sample_role == "sample" and not row.excluded for row in metadata or ()
        ),
        "istd_source": istd_source,
        "istd_count": len(istd or ()),
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
    istd, istd_source = resolve_istd(
        source, istd_path=str(values.get("istd_path") or "")
    )
    summary = export_matrix_curation(
        source=source,
        sample_metadata=metadata,
        sample_source=_summary_label(sample_source),
        steps=steps,
        output_dir=output_dir,
        istd_windows=istd,
        istd_source=_istd_label(source, istd_source),
    )
    counts = json.loads(summary.read_text(encoding="utf-8"))
    return {
        "output_dir": str(output_dir),
        "source_dir": str(source.source_dir),
        "summary_path": str(summary),
        "matrix_tsv": str(output_dir / "analysis_matrix.tsv"),
        "feature_count": counts["feature_count"],
        "kept_feature_count": counts["kept_feature_count"],
        "published_paths": [
            str(path)
            for path in (
                output_dir / "analysis_matrix.tsv",
                output_dir / "feature_decisions.tsv",
                output_dir / RELATIONS_NAME,
                output_dir / REVIEW_NAME,
                output_dir / "sample_metadata.tsv",
                summary,
            )
            if path.is_file()
        ],
    }


def preview_curation(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    metadata, sample_source = resolve_samples(
        config_dir,
        source,
        sample_metadata_path=str(values.get("sample_metadata_path") or ""),
        samples=values.get("samples"),
    )
    istd, istd_source = resolve_istd(
        source, istd_path=str(values.get("istd_path") or "")
    )
    steps = values.get("steps")
    if not isinstance(steps, dict):
        raise ValueError("請至少啟用一個整理步驟。")
    return prepare_matrix_curation(
        source=source,
        sample_metadata=metadata,
        sample_source=_summary_label(sample_source),
        steps=steps,
        istd_windows=istd,
        istd_source=_istd_label(source, istd_source),
    ).preview


def _summary_label(sample_source: dict[str, str] | None) -> str:
    if sample_source is None:
        return ""
    if sample_source["kind"] == "raw":
        return f"RAW headers: {sample_source['label']}"
    return sample_source["label"]


def _istd_label(source: CurationSource, istd_source: dict[str, str] | None) -> str:
    if istd_source is None:
        return ""
    if istd_source["kind"] == "run":
        return f"run metadata: {source.source_dir / RUN_METADATA_NAME}"
    return istd_source["label"]
