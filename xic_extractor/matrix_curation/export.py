"""Read a published Alignment folder, run the enabled steps and write a new folder."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.matrix_curation.finite_fraction import FINITE_FRACTION_STEP
from xic_extractor.matrix_curation.steps import (
    CurationMatrix,
    CurationStep,
    SampleBinding,
)
from xic_extractor.output.alignment.matrix_output_contract import matrix_review_rows
from xic_extractor.sample_metadata import (
    SAMPLE_METADATA_COLUMNS,
    SAMPLE_METADATA_SCHEMA_VERSION,
    SampleMetadata,
    is_sample_metadata_source,
    load_sample_metadata,
)
from xic_extractor.tabular_io import (
    escape_excel_formula,
    read_tsv_with_header,
    read_tsv_with_header_snapshot,
    write_tsv,
)

# A later step joins by adding itself here; the page and outputs follow.
STEPS: tuple[CurationStep, ...] = (FINITE_FRACTION_STEP,)
MATRIX_NAME = "alignment_matrix.tsv"
REVIEW_NAME = "alignment_review.tsv"
SUMMARY_NAME = "matrix_curation_summary.json"
DECISION_KEY_COLUMNS = ("source_matrix_row_index", "feature_id", "Mz", "RT")


@dataclass(frozen=True)
class CurationSource:
    source_dir: Path
    matrix_path: Path
    review_path: Path


def locate_curation_source(source_dir: Path) -> CurationSource:
    """Find the published Matrix and its bound Review; nothing is rerun."""
    if not source_dir.is_dir():
        raise ValueError(f"找不到 Alignment 輸出資料夾：{source_dir}")
    matrix, review = source_dir / MATRIX_NAME, source_dir / REVIEW_NAME
    if not matrix.is_file():
        raise ValueError(f"資料夾中沒有 {MATRIX_NAME}：{source_dir}")
    if not review.is_file():
        raise ValueError(
            f"資料夾中沒有 {REVIEW_NAME}；請以會輸出 Review 的層級重跑 Alignment。"
        )
    return CurationSource(source_dir, matrix, review)


def default_output_dir(source: CurationSource) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return source.source_dir / f"matrix_curation_{stamp}"


def read_sample_metadata_file(path: Path) -> tuple[SampleMetadata, ...]:
    if path.suffix.lower() not in {".tsv", ".csv"} or not is_sample_metadata_source(
        path
    ):
        raise ValueError(
            f"樣本資料需為含 schema_version 與 sample_name 欄的 TSV/CSV：{path}"
        )
    return load_sample_metadata(path)


def bind_samples(
    sample_columns: Sequence[str],
    metadata: Sequence[SampleMetadata] | None,
    *,
    missing: str,
) -> SampleBinding:
    """Match each Matrix column to exactly one sample by name or RAW stem."""
    columns = tuple(sample_columns)
    if metadata is None:
        return SampleBinding(columns, None, missing)
    # The metadata owner already rejects duplicate and ambiguous aliases.
    aliases: dict[str, SampleMetadata] = {}
    for row in metadata:
        for alias in {row.sample_name, row.raw_stem}:
            column = escape_excel_formula(alias)
            if column in aliases and aliases[column] != row:
                return SampleBinding(columns, None, f"樣本名稱對應不明確：{column!r}")
            aliases[column] = row
    absent = [column for column in columns if column not in aliases]
    if absent:
        return SampleBinding(
            columns, None, f"樣本資料缺少 Matrix 欄位對應的樣本：{', '.join(absent)}"
        )
    matched = tuple(aliases[column] for column in columns)
    if len({row.sample_name for row in matched}) != len(columns):
        return SampleBinding(columns, None, "多個 Matrix 欄位對應到同一個樣本。")
    return SampleBinding(columns, matched)


def describe_steps(
    source: CurationSource,
    metadata: Sequence[SampleMetadata] | None,
    *,
    missing_samples: str,
) -> list[dict[str, Any]]:
    """Each step with its parameters and, when it cannot run, the reason."""
    header, _ = read_tsv_with_header(
        source.matrix_path, required_columns=("Mz", "RT"), encoding="utf-8-sig"
    )
    binding = bind_samples(header[2:], metadata, missing=missing_samples)
    return [
        {
            "key": step.key,
            "label": step.label,
            "description": step.description,
            "parameters": [asdict(parameter) for parameter in step.parameters],
            "unavailable_reason": step.unavailable_reason(binding),
        }
        for step in STEPS
    ]


def _step_parameters(
    step: CurationStep, values: Mapping[str, object]
) -> dict[str, float]:
    unknown = sorted(set(values) - {parameter.key for parameter in step.parameters})
    if unknown:
        raise ValueError(f"{step.label}不支援參數：{unknown[0]}")
    parsed = {}
    for parameter in step.parameters:
        raw = values.get(parameter.key)
        try:
            number = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            number = math.nan
        if not math.isfinite(number) or not (
            parameter.minimum <= number <= parameter.maximum
        ):
            raise ValueError(
                f"{step.label}：{parameter.label}必須介於 "
                f"{parameter.minimum:g} 與 {parameter.maximum:g}。"
            )
        parsed[parameter.key] = number
    return parsed


def export_matrix_curation(
    *,
    source: CurationSource,
    sample_metadata: Sequence[SampleMetadata] | None,
    sample_source: str,
    steps: Mapping[str, Mapping[str, object]],
    output_dir: Path,
) -> Path:
    """Write the curated Matrix and per-feature decisions into a new folder.

    Each enabled step judges the original Matrix on its own; a feature is kept
    only when every enabled step keeps it. Sources are never modified.
    """
    by_key = {step.key: step for step in STEPS}
    unknown = sorted(set(steps) - set(by_key))
    if unknown:
        raise ValueError(f"不支援的整理步驟：{unknown[0]}")
    enabled = [step for step in STEPS if step.key in steps]
    if not enabled:
        raise ValueError("請至少啟用一個整理步驟。")
    parameters = {step.key: _step_parameters(step, steps[step.key]) for step in enabled}

    # The snapshot reader also rejects rows whose cell count disagrees with
    # the header; its digest is not needed here.
    header, rows, _ = read_tsv_with_header_snapshot(
        source.matrix_path, required_columns=("Mz", "RT"), encoding="utf-8-sig"
    )
    review_header, review_rows, _ = read_tsv_with_header_snapshot(
        source.review_path, encoding="utf-8-sig"
    )
    identities = matrix_review_rows(
        matrix_header=header,
        matrix_rows=rows,
        review_columns=review_header,
        review_rows=review_rows,
    )
    binding = bind_samples(
        header[2:], sample_metadata, missing="找不到樣本資料；請選擇樣本資料 TSV。"
    )
    for step in enabled:
        reason = step.unavailable_reason(binding)
        if reason:
            raise ValueError(f"{step.label}無法執行：{reason}")
    samples = binding.columns
    values = np.empty((len(rows), len(samples)), dtype=float)
    for index, row in enumerate(rows):
        for column_index, column in enumerate(samples):
            token = row[column]
            try:
                values[index, column_index] = float(token) if token.strip() else np.nan
            except ValueError as exc:
                raise ValueError(
                    f"matrix row {index + 1}, sample {column!r}: "
                    f"invalid numeric value {token!r}"
                ) from exc
    matrix = CurationMatrix(sample_binding=binding, values=values)
    results = {
        step.key: step.evaluate(matrix, parameters[step.key]) for step in enabled
    }

    decision_columns = [*DECISION_KEY_COLUMNS]
    for step in enabled:
        decision_columns.extend(results[step.key].columns)
    decision_columns.append("decision")
    decision_rows = []
    kept_rows = []
    for index, (row, identity) in enumerate(zip(rows, identities, strict=True)):
        keep = all(results[step.key].keep[index] for step in enabled)
        decision: dict[str, object] = {
            "source_matrix_row_index": index + 1,
            "feature_id": identity["feature_id"],
            "Mz": row["Mz"],
            "RT": row["RT"],
        }
        for step in enabled:
            decision.update(results[step.key].rows[index])
        decision["decision"] = "keep" if keep else "drop"
        decision_rows.append(decision)
        if keep:
            kept_rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=False)
    write_tsv(output_dir / "analysis_matrix.tsv", kept_rows, header)
    write_tsv(output_dir / "feature_decisions.tsv", decision_rows, decision_columns)
    if binding.samples is not None:
        write_tsv(
            output_dir / "sample_metadata.tsv",
            [
                {
                    "matrix_column": column,
                    "schema_version": SAMPLE_METADATA_SCHEMA_VERSION,
                    **asdict(row),
                    "excluded": "TRUE" if row.excluded else "FALSE",
                }
                for column, row in zip(samples, binding.samples, strict=True)
            ],
            ("matrix_column", *SAMPLE_METADATA_COLUMNS),
        )
    summary = {
        "steps": [
            {
                "key": step.key,
                "parameters": parameters[step.key],
                "kept_feature_count": sum(results[step.key].keep),
                **results[step.key].summary,
            }
            for step in enabled
        ],
        "inputs": {
            "source_dir": str(source.source_dir.resolve()),
            "matrix": str(source.matrix_path.resolve()),
            "review": str(source.review_path.resolve()),
            "sample_metadata": sample_source,
        },
        "feature_count": len(rows),
        "kept_feature_count": len(kept_rows),
        "sample_count": len(samples),
    }
    # Written last: a folder without the summary is an incomplete export.
    summary_path = output_dir / SUMMARY_NAME
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary_path
