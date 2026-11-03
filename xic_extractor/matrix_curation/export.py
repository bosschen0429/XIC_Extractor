"""Read a published Alignment folder, run the enabled steps and write a new folder."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from xic_extractor.alignment_explanation.storage import EXPLANATION_ARTIFACT_NAME
from xic_extractor.matrix_curation.finite_fraction import FINITE_FRACTION_STEP
from xic_extractor.matrix_curation.istd import (
    IstdWindow,
    match_istd,
    windows_from_explanation,
)
from xic_extractor.matrix_curation.same_compound import (
    REVIEW_NAME as SAME_COMPOUND_REVIEW_NAME,
)
from xic_extractor.matrix_curation.same_compound import (
    SAME_COMPOUND_STEP,
    group_outcome_review,
)
from xic_extractor.matrix_curation.steps import (
    CellEvidence,
    CurationInputs,
    CurationMatrix,
    CurationStep,
    FeatureIdentity,
    SampleBinding,
    StepTable,
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
STEPS: tuple[CurationStep, ...] = (FINITE_FRACTION_STEP, SAME_COMPOUND_STEP)
MATRIX_NAME = "alignment_matrix.tsv"
REVIEW_NAME = "alignment_review.tsv"
RUN_METADATA_NAME = "alignment_run_metadata.json"
SUMMARY_NAME = "matrix_curation_summary.json"
DECISION_KEY_COLUMNS = (
    "source_matrix_row_index",
    "feature_id",
    "Mz",
    "RT",
    "istd_label",
)


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
    except (OSError, ValueError):
        pass
    raw_dir, dll_dir = (
        Path(value) if isinstance(value, str) and value.strip() else None
        for value in (run.get("raw_dir"), run.get("dll_dir"))
    )
    explanation = source_dir / EXPLANATION_ARTIFACT_NAME
    return CurationSource(
        source_dir,
        matrix,
        review,
        raw_dir,
        dll_dir,
        explanation if explanation.is_file() else None,
    )


def cell_evidence_problem(source: CurationSource) -> str:
    """Why per-cell detection evidence cannot be read, or an empty string."""
    from xic_extractor.alignment_explanation.reader import (
        read_explanation_summary,
    )
    from xic_extractor.alignment_explanation.review import REVIEW_SCHEMA_VERSION
    from xic_extractor.alignment_explanation.storage import (
        ExplanationError,
    )

    if source.explanation_path is None:
        return (
            f"資料夾中沒有 {EXPLANATION_ARTIFACT_NAME}，無法取得每格的直接偵測證據；"
            "請重新執行 Alignment 產生新輸出。"
        )
    try:
        summary = read_explanation_summary(source.explanation_path)
    except ExplanationError as exc:
        return f"無法讀取 {EXPLANATION_ARTIFACT_NAME}：{exc}"
    if summary["schema_version"] != REVIEW_SCHEMA_VERSION:
        return (
            f"{EXPLANATION_ARTIFACT_NAME} 是舊格式；請重新執行 Alignment 產生新輸出。"
        )
    return ""


def read_run_istd(source: CurationSource) -> tuple[IstdWindow, ...] | None:
    """The internal standards the run declared, or None when it cannot be read."""
    if source.explanation_path is None:
        return None
    from xic_extractor.alignment_explanation.reader import (
        read_explanation_summary,
    )
    from xic_extractor.alignment_explanation.storage import (
        ExplanationError,
    )

    try:
        return windows_from_explanation(
            read_explanation_summary(source.explanation_path)
        )
    except (ExplanationError, ValueError):
        return None


def load_cell_evidence(
    source: CurationSource, features: Sequence[FeatureIdentity]
) -> tuple[dict[str, CellEvidence], ...]:
    """Published cells of each Matrix row, keyed by explanation sample."""
    from xic_extractor.alignment_explanation.published_cells import (
        read_published_cells,
    )

    problem = cell_evidence_problem(source)
    if problem:
        raise ValueError(problem)
    by_feature: dict[str, dict[str, CellEvidence]] = {
        feature.feature_id: {} for feature in features
    }
    for cell in read_published_cells(source.source_dir):
        row = by_feature.get(cell.feature_id)
        if row is None or cell.value is None:
            continue
        row[cell.sample] = CellEvidence(
            direct=cell.direct, value=cell.value, apex_rt_min=cell.apex_rt_min
        )
    return tuple(by_feature[feature.feature_id] for feature in features)


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


def read_run_samples(source: CurationSource) -> tuple[SampleMetadata, ...] | None:
    """Sample roles from the run's RAW headers, as Alignment reads them.

    Used when no sample data was supplied; None when the RAW folder or the
    reader is not available on this computer.
    """
    if source.raw_dir is None or source.dll_dir is None:
        return None
    if not source.raw_dir.is_dir():
        return None
    from xic_extractor.raw_reader import RawReaderError, read_analysis_set

    try:
        return read_analysis_set(source.raw_dir, source.dll_dir).sample_metadata
    except (OSError, RawReaderError, ValueError):
        return None


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
    inputs = CurationInputs(
        bind_samples(header[2:], metadata, missing=missing_samples),
        cell_evidence_problem(source),
    )
    return [
        {
            "key": step.key,
            "label": step.label,
            "description": step.description,
            "parameters": [asdict(parameter) for parameter in step.parameters],
            "unavailable_reason": step.unavailable_reason(inputs),
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
    istd_windows: Sequence[IstdWindow] | None = None,
    istd_source: str = "",
) -> Path:
    """Write the curated Matrix and per-feature decisions into a new folder.

    Each enabled step judges the original Matrix on its own. A feature is kept
    when it is an internal standard or every enabled step keeps it. Sources are
    never modified.
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
    features = tuple(
        FeatureIdentity(
            feature_id=str(identity["feature_id"]),
            matrix_mz=float(str(identity["matrix_mz"])),
            matrix_rt=float(str(identity["matrix_rt"])),
            center_mz_basis=str(identity.get("center_mz_basis", "")),
        )
        for identity in identities
    )
    binding = bind_samples(
        header[2:], sample_metadata, missing="找不到樣本資料；請選擇樣本資料 TSV。"
    )
    needs_cells = any(step.needs_cell_evidence for step in enabled)
    inputs = CurationInputs(
        binding, cell_evidence_problem(source) if needs_cells else ""
    )
    for step in enabled:
        reason = step.unavailable_reason(inputs)
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
    matrix = CurationMatrix(
        sample_binding=binding,
        values=values,
        features=features,
        cells=load_cell_evidence(source, features) if needs_cells else None,
    )
    results = {
        step.key: step.evaluate(matrix, parameters[step.key]) for step in enabled
    }
    windows = tuple(istd_windows or ())
    istd_labels = [
        match_istd(feature.matrix_mz, feature.matrix_rt, windows)
        for feature in features
    ]
    final_keep = [
        bool(label) or all(results[step.key].keep[index] for step in enabled)
        for index, label in enumerate(istd_labels)
    ]

    tables: dict[str, StepTable] = {}
    for step in enabled:
        tables.update(results[step.key].tables)
    step_extras: dict[str, dict[str, Any]] = {}
    same_compound = results.get(SAME_COMPOUND_STEP.key)
    if same_compound is not None:
        others = [step for step in enabled if step is not SAME_COMPOUND_STEP]
        group_review, empty_groups = group_outcome_review(
            same_compound,
            [feature.feature_id for feature in features],
            [
                all(results[step.key].keep[index] for step in others)
                for index in range(len(features))
            ],
            final_keep,
        )
        review = tables[SAME_COMPOUND_REVIEW_NAME]
        review_rows_all = (*review.rows, *group_review)
        tables[SAME_COMPOUND_REVIEW_NAME] = StepTable(review.columns, review_rows_all)
        step_extras[SAME_COMPOUND_STEP.key] = {
            "empty_group_count": empty_groups,
            "review_count": len(review_rows_all),
            "review_by_kind": dict(
                sorted(
                    Counter(str(row["review_kind"]) for row in review_rows_all).items()
                )
            ),
        }

    decision_columns = [*DECISION_KEY_COLUMNS]
    for step in enabled:
        decision_columns.extend(results[step.key].columns)
    decision_columns.extend(("decision", "decision_reason"))
    decision_rows = []
    kept_rows = []
    for index, (row, feature) in enumerate(zip(rows, features, strict=True)):
        decision: dict[str, object] = {
            "source_matrix_row_index": index + 1,
            "feature_id": feature.feature_id,
            "Mz": row["Mz"],
            "RT": row["RT"],
            "istd_label": istd_labels[index],
        }
        for step in enabled:
            decision.update(results[step.key].rows[index])
        dropped_by = [step.key for step in enabled if not results[step.key].keep[index]]
        decision["decision"] = "keep" if final_keep[index] else "drop"
        if istd_labels[index]:
            decision["decision_reason"] = "istd_protected"
        elif dropped_by:
            decision["decision_reason"] = "dropped_by:" + ",".join(dropped_by)
        else:
            decision["decision_reason"] = "kept_by_all_steps"
        decision_rows.append(decision)
        if final_keep[index]:
            kept_rows.append(row)

    output_dir.mkdir(parents=True, exist_ok=False)
    write_tsv(output_dir / "analysis_matrix.tsv", kept_rows, header)
    write_tsv(output_dir / "feature_decisions.tsv", decision_rows, decision_columns)
    for name, table in tables.items():
        write_tsv(output_dir / name, table.rows, table.columns)
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
                **step_extras.get(step.key, {}),
            }
            for step in enabled
        ],
        "istd": {
            "source": istd_source,
            "standard_count": len(windows),
            "protected_feature_count": sum(bool(label) for label in istd_labels),
        },
        "inputs": {
            "source_dir": str(source.source_dir.resolve()),
            "matrix": str(source.matrix_path.resolve()),
            "review": str(source.review_path.resolve()),
            "explanation": (
                str(source.explanation_path.resolve())
                if needs_cells and source.explanation_path is not None
                else ""
            ),
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
