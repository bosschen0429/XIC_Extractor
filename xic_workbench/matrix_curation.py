"""Workbench entry to Matrix curation of a published Alignment folder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xic_extractor.matrix_curation.export import (
    CurationEvaluation,
    default_output_dir,
    describe_steps,
    prepare_matrix_curation,
    read_run_istd,
    read_sample_metadata_file,
)
from xic_extractor.matrix_curation.istd import read_istd_file
from xic_extractor.matrix_curation.mass_rt_windows import MzRtWindow
from xic_extractor.matrix_curation.published import CURATED_MATRIX_NAME
from xic_extractor.matrix_curation.source import (
    RUN_METADATA_NAME,
    CurationSource,
    locate_curation_source,
)
from xic_extractor.sample_metadata import SampleMetadata
from xic_workbench.samples import from_payload, sample_annotations_path

MISSING_SAMPLES = "找不到本次分析的樣本資訊；請在「改用其他來源」補入樣本資料。"


def resolve_samples(
    config_dir: Path,
    source: CurationSource,
    *,
    sample_metadata_path: str = "",
    samples: object = None,
) -> tuple[tuple[SampleMetadata, ...] | None, dict[str, str] | None]:
    """Pick sample data and say where it came from.

    Order: a chosen file, the saved run snapshot, the run's in-memory rows,
    then confirmed folder rows. Imported results never initiate RAW or DLL reads.
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
    return None, None


def resolve_istd(
    source: CurationSource, *, istd_path: str = ""
) -> tuple[tuple[MzRtWindow, ...] | None, dict[str, str] | None]:
    """Pick the internal standard list: a chosen file, else the run's own list."""
    if istd_path.strip():
        path = Path(istd_path.strip())
        return read_istd_file(path), {"kind": "file", "label": str(path)}
    windows = read_run_istd(source)
    if windows:
        return windows, {"kind": "run", "label": "本次分析宣告的 ISTD 清單"}
    return None, None


def _nucleoside_list(values: dict[str, Any]) -> Path | None:
    path = str(values.get("nucleoside_path") or "").strip()
    return Path(path) if path else None


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
        "steps": describe_steps(
            source,
            metadata,
            missing_samples=MISSING_SAMPLES,
            nucleoside_list=_nucleoside_list(values),
        ),
        "output_dir": str(default_output_dir(source)),
    }


def run_curation(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    output_text = str(values.get("output_dir") or "").strip()
    output_dir = Path(output_text) if output_text else default_output_dir(source)
    evaluation = _evaluate(config_dir, source, values)
    summary = evaluation.write_to(output_dir)
    return {
        "output_dir": str(output_dir),
        "source_dir": str(source.source_dir),
        "summary_path": str(summary),
        "matrix_tsv": str(output_dir / CURATED_MATRIX_NAME),
        "feature_count": evaluation.summary["feature_count"],
        "kept_feature_count": evaluation.summary["kept_feature_count"],
        "steps": evaluation.preview["steps"],
        "published_paths": [
            *(str(output_dir / name) for name in evaluation.tables),
            str(summary),
        ],
    }


def preview_curation(config_dir: Path, values: dict[str, Any]) -> dict[str, Any]:
    source = locate_curation_source(Path(str(values.get("source_dir") or "")))
    return _evaluate(config_dir, source, values).preview


def _evaluate(
    config_dir: Path, source: CurationSource, values: dict[str, Any]
) -> CurationEvaluation:
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
        nucleoside_list=_nucleoside_list(values),
        istd_windows=istd,
        istd_source=_istd_label(source, istd_source),
    )


def _summary_label(sample_source: dict[str, str] | None) -> str:
    return sample_source["label"] if sample_source is not None else ""


def _istd_label(source: CurationSource, istd_source: dict[str, str] | None) -> str:
    if istd_source is None:
        return ""
    if istd_source["kind"] == "run":
        return f"run metadata: {source.source_dir / RUN_METADATA_NAME}"
    return istd_source["label"]
